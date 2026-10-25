"""PAM password verification and in-memory sessions for Llameros dashboard authentication.

PAM verification and short-lived, server-side sessions.
"""

import ctypes
import ctypes.util
import pwd
import secrets
import threading
import time
from collections import deque

COOKIE = "__Host-llameros"


def account_available(username, service="login"):
    """Fail closed on missing/login-disabled accounts or PAM account-policy failures.

    This checks account policy without retaining or replaying a password. Password-only
    locks are not portable account-disable signals: offboarding must also block Llameros.
    """
    try:
        account = pwd.getpwnam(username)
        if not account.pw_shell or account.pw_shell.rsplit("/", 1)[-1] in {"nologin", "false"}:
            return False
        if "\x00" in username + service:
            return False
        library = ctypes.util.find_library("pam")
        if not library:
            return False
        lib = ctypes.CDLL(library)
        callback_type = ctypes.CFUNCTYPE(
            ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
        )

        class Conversation(ctypes.Structure):
            _fields_ = [("conv", callback_type), ("data", ctypes.c_void_p)]

        callback = callback_type(lambda *args: 19)  # PAM_CONV_ERR: never answer prompts.
        conversation = Conversation(callback, None)
        handle = ctypes.c_void_p()
        lib.pam_start.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.POINTER(Conversation),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        lib.pam_acct_mgmt.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.pam_end.argtypes = [ctypes.c_void_p, ctypes.c_int]
        result = lib.pam_start(
            service.encode(), username.encode(), ctypes.byref(conversation), ctypes.byref(handle)
        )
        if result != 0:
            return False
        try:
            result = lib.pam_acct_mgmt(handle, 0)
            return result == 0
        finally:
            lib.pam_end(handle, result)
    except (KeyError, OSError, ValueError):
        return False


def verify_password(username: str, password: str, service: str) -> bool:
    """Check a login-shell account through PAM without storing the password.

    Reject missing/daemon accounts and NUL-containing credentials before invoking PAM; host
    account policy still applies.
    """
    if not username or not password or "\x00" in username + password:
        return False
    try:
        account = pwd.getpwnam(username)
    except KeyError:
        return False
    if not account.pw_shell or account.pw_shell.rsplit("/", 1)[-1] in {"nologin", "false"}:
        return False
    import pam

    verifier = pam.pam()
    return bool(verifier.authenticate(username, password, service=service, resetcreds=False))


class Sessions:
    """Thread-safe sessions with bounded source/account throttling and PAM concurrency."""

    def __init__(self, lifetime: int):
        """Initialize session storage and a monotonic rate-limit window; lifetime is in seconds."""
        self.lifetime = lifetime
        self.sessions: dict[str, dict] = {}
        self.attempts = {}
        self.pam_slots = threading.BoundedSemaphore(4)
        self.lock = threading.Lock()

    def allow_attempt(self, source="local", username="") -> bool:
        """Bound source and source/account attempts; never trust forwarded-IP headers."""
        with self.lock:
            now = time.monotonic()
            for key in list(self.attempts):
                queue = self.attempts[key]
                while queue and queue[0] <= now - 60:
                    queue.popleft()
                if not queue:
                    del self.attempts[key]
            limits = [(("source", source), 30), (("pair", source, username), 5)]
            for key, limit in limits:
                if len(self.attempts.get(key, ())) >= limit:
                    return False
                if key not in self.attempts and len(self.attempts) >= 4096:
                    return False
            for key, _ in limits:
                self.attempts.setdefault(key, deque()).append(now)
            return True

    def create(self, username: str) -> tuple[str, dict]:
        """Return a new opaque cookie token and CSRF-bearing session; prune expired sessions."""
        with self.lock:
            now = time.monotonic()
            self.sessions = {
                key: value for key, value in self.sessions.items() if value["expires"] > now
            }
            if len(self.sessions) >= 100:
                self.sessions.pop(next(iter(self.sessions)))
            token = secrets.token_urlsafe(32)
            session = {
                "username": username,
                "csrf": secrets.token_urlsafe(32),
                "expires": now + self.lifetime,
            }
            self.sessions[token] = session
            return token, session

    def get(self, token: str) -> dict | None:
        """Return a copy of an unexpired session or remove the stale token and return None."""
        with self.lock:
            session = self.sessions.get(token)
            if session and session["expires"] > time.monotonic():
                return session.copy()
            self.sessions.pop(token, None)
            return None

    def remove(self, token: str):
        """Invalidate one cookie token; unknown tokens are harmless."""
        with self.lock:
            self.sessions.pop(token, None)

    def remove_user(self, username: str):
        """Invalidate all sessions for an account when an administrator blocks it."""
        with self.lock:
            self.sessions = {
                token: session
                for token, session in self.sessions.items()
                if session["username"] != username
            }
