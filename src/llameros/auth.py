"""PAM verification and short-lived, server-side sessions."""

import secrets
import threading
import time
from collections import deque

from .config import current_user

COOKIE = "__Host-llameros"


def verify_password(username: str, password: str, service: str) -> bool:
    if username != current_user() or not password or "\x00" in username + password:
        return False
    import pam

    verifier = pam.pam()
    return bool(verifier.authenticate(username, password, service=service, resetcreds=False))


class Sessions:
    def __init__(self, lifetime: int):
        self.lifetime = lifetime
        self.sessions: dict[str, dict] = {}
        self.attempts: deque[float] = deque()
        self.lock = threading.Lock()

    def allow_attempt(self) -> bool:
        # Global limit as well as single-user authorization: source IP spoofing cannot bypass it.
        with self.lock:
            now = time.monotonic()
            while self.attempts and self.attempts[0] < now - 60:
                self.attempts.popleft()
            if len(self.attempts) >= 5:
                return False
            self.attempts.append(now)
            return True

    def create(self, username: str) -> tuple[str, dict]:
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
        with self.lock:
            session = self.sessions.get(token)
            if session and session["expires"] > time.monotonic():
                return session.copy()
            self.sessions.pop(token, None)
            return None

    def remove(self, token: str):
        with self.lock:
            self.sessions.pop(token, None)
