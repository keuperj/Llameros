"""Private, process-owned Unix sockets relaying to container loopback via Docker exec."""

import os
import select
import selectors
import socketserver
import subprocess
import tempfile
import threading

_lock = threading.Lock()
_relays = {}

# Fixed program only: no user input is interpolated into shell code. The remote
# reader is reaped when stdin closes, including cancellation of a streaming call.
RELAY = (
    "exec 3<>/dev/tcp/127.0.0.1/11434 || exit 1; "
    "cat <&3 & reader=$!; "
    "trap 'kill $reader 2>/dev/null; wait $reader 2>/dev/null' EXIT; cat >&3"
)


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        if not self.server.slots.acquire(blocking=False):
            return
        process = None
        try:
            process = subprocess.Popen(
                ["docker", "exec", "-i", self.server.container, "bash", "-c", RELAY],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
            with self.server.guard:
                self.server.processes.add(process)
            os.set_blocking(process.stdin.fileno(), False)
            self.request.settimeout(30)
            with selectors.DefaultSelector() as selector:
                selector.register(self.request, selectors.EVENT_READ, process.stdin)
                selector.register(process.stdout, selectors.EVENT_READ, self.request)
                while not self.server.stopping.is_set():
                    for key, _ in selector.select(1):
                        data = os.read(key.fd, 65536)
                        if not data:
                            return
                        if key.data is self.request:
                            self.request.sendall(data)
                        else:
                            view = memoryview(data)
                            while view:
                                if not select.select([], [key.data], [], 30)[1]:
                                    return
                                try:
                                    written = os.write(key.data.fileno(), view)
                                    view = view[written:]
                                except BlockingIOError:
                                    continue
        except (OSError, subprocess.SubprocessError):
            pass
        finally:
            if process is not None:
                process.stdin.close()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                process.stdout.close()
                with self.server.guard:
                    self.server.processes.discard(process)
            self.server.slots.release()


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, container):
        self.directory = tempfile.TemporaryDirectory(prefix="llameros-runtime-")
        self.container = container
        self.slots = threading.BoundedSemaphore(16)
        self.guard = threading.Lock()
        self.processes = set()
        self.stopping = threading.Event()
        super().__init__(self.directory.name + "/api.sock", _Handler)
        os.chmod(self.server_address, 0o600)
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()


def private_socket(container):
    """Return a socket inside a fresh mode-0700 directory, never a host TCP listener."""
    with _lock:
        if container not in _relays:
            _relays[container] = _Server(container)
        return _relays[container].server_address


def close_relays():
    """Close listeners and terminate outstanding relay clients at application shutdown."""
    with _lock:
        servers = list(_relays.values())
        _relays.clear()
    for server in servers:
        server.stopping.set()
        server.shutdown()
        server.server_close()
        with server.guard:
            for process in server.processes:
                if process.poll() is None:
                    process.terminate()
        server.directory.cleanup()
