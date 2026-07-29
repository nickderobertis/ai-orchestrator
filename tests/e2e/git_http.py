"""Serve a real bare repository as `https://github.com/<owner>/<name>.git`.

Some lifecycle guarantees only exist for a **GitHub identity**: required PR status
checks are merge-path coverage only when the identity's origin normalizes to
`https://github.com/...`, so a journey that proves them cannot use a
`file://` origin. Everything here keeps git real — `git http-backend` serves the
same bare repository the rest of the suite uses, and the checkout fetches and
pushes to it over TLS. The single redirection is the DNS answer:
`http.curloptResolve` points `github.com:443` at the loopback listener, exactly
as `curl --resolve` would.

Port 443 is not negotiable — a port in the URL would stop the origin normalizing
to a GitHub identity, which is the whole point — so `serve_github_origin` holds a
machine-wide lock for the life of the server rather than racing another suite.
"""

from __future__ import annotations

import fcntl
import os
import shutil
import ssl
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

#: Held for the life of the listener so concurrent suites on one host serialize
#: instead of colliding on the fixed port. The path is deliberately fixed and
#: outside any tmp_path: it has to be the same file for every suite on the machine,
#: which is exactly what S108 warns about and exactly what is needed here.
PORT_LOCK = Path("/tmp/ai-orchestrator-github-git-https.lock")  # noqa: S108


@dataclass(frozen=True)
class GitHubOrigin:
    """A bare repository reachable at a real GitHub clone URL."""

    url: str
    ca_certificate: Path

    def attach(self, checkout: Path) -> None:
        """Give ``checkout`` this server's clone URL as its real origin.

        Only the origin URL is per-checkout, because it is what the repository
        *identity* is derived from. Reaching the listener is process-wide config
        (see `serve_github_origin`): a lifecycle run pushes from a private clone it
        makes itself, which inherits nothing from this checkout's config file.
        """
        subprocess.run(
            ["git", "-C", str(checkout), "remote", "set-url", "origin", self.url],
            check=True,
            capture_output=True,
        )


def _certificate(directory: Path) -> tuple[Path, Path]:
    certificate = directory / "github.crt"
    key = directory / "github.key"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(certificate),
            "-days",
            "1",
            "-subj",
            "/CN=github.com",
            "-addext",
            "subjectAltName=DNS:github.com",
        ],
        check=True,
        capture_output=True,
    )
    return certificate, key


def _read_body(handler: BaseHTTPRequestHandler) -> bytes:
    declared = handler.headers.get("Content-Length")
    if declared is not None:
        return handler.rfile.read(int(declared))
    if handler.headers.get("Transfer-Encoding", "").lower() != "chunked":
        return b""
    chunks = bytearray()
    while True:
        size = int(handler.rfile.readline().split(b";")[0], 16)
        if size == 0:
            handler.rfile.readline()
            return bytes(chunks)
        chunks += handler.rfile.read(size)
        handler.rfile.readline()


class _GitBackendServer(ThreadingHTTPServer):
    """A listener that knows which tree `git http-backend` should serve."""

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler], root: Path):
        super().__init__(address, handler)
        self.project_root = root


class _GitBackendHandler(BaseHTTPRequestHandler):
    """Run the real `git http-backend` as a CGI program."""

    protocol_version = "HTTP/1.1"
    server: _GitBackendServer

    def do_GET(self) -> None:
        self._run("GET")

    def do_POST(self) -> None:
        self._run("POST")

    def log_message(self, *_args: object) -> None:
        """Keep the served requests out of the test session's output."""

    def _run(self, method: str) -> None:
        split = urlsplit(self.path)
        body = _read_body(self) if method == "POST" else b""
        environment = {
            **os.environ,
            "GIT_PROJECT_ROOT": str(self.server.project_root),
            "GIT_HTTP_EXPORT_ALL": "1",
            "REQUEST_METHOD": method,
            "PATH_INFO": unquote(split.path),
            "QUERY_STRING": split.query,
            "REMOTE_ADDR": "127.0.0.1",
            "REMOTE_USER": "tester",
            "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            "HTTP_CONTENT_ENCODING": self.headers.get("Content-Encoding", ""),
            "CONTENT_LENGTH": str(len(body)),
        }
        completed = subprocess.run(
            [shutil.which("git") or "git", "http-backend"],
            input=body,
            capture_output=True,
            env=environment,
        )
        head, _, payload = completed.stdout.partition(b"\r\n\r\n")
        status = 200
        headers: list[tuple[str, str]] = []
        for line in head.decode("latin-1").splitlines():
            name, _, value = line.partition(":")
            if name.strip().lower() == "status":
                status = int(value.strip().split()[0])
            elif name:
                headers.append((name.strip(), value.strip()))
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@contextmanager
def serve_github_origin(origin: Path, tmp_path: Path, *, slug: str) -> Iterator[GitHubOrigin]:
    """Serve ``origin`` at `https://github.com/<slug>.git` over real TLS."""
    subprocess.run(
        ["git", "-C", str(origin), "config", "--bool", "http.receivepack", "true"],
        check=True,
        capture_output=True,
    )
    certificate, key = _certificate(tmp_path)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, key)

    owner, _, name = slug.partition("/")
    # `git http-backend` resolves PATH_INFO under the project root, so the served
    # tree has to mirror the URL the identity is spelled with.
    root = tmp_path / "served"
    (root / owner).mkdir(parents=True, exist_ok=True)
    served = root / owner / f"{name}.git"
    if not served.exists():
        served.symlink_to(origin)

    PORT_LOCK.touch()
    with PORT_LOCK.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        server = _GitBackendServer(("127.0.0.1", 443), _GitBackendHandler, root)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        # Git's own environment-config mechanism, so every git process the lifecycle
        # starts is reached — including the private run clone, which is created after
        # this point and inherits no checkout's config file.
        transport = {
            "http.sslCAInfo": str(certificate),
            "http.curloptResolve": "github.com:443:127.0.0.1",
        }
        previous = {name: os.environ.get(name) for name in ("GIT_CONFIG_COUNT",)}
        offset = int(os.environ.get("GIT_CONFIG_COUNT") or 0)
        for index, (key, value) in enumerate(transport.items(), start=offset):
            previous[f"GIT_CONFIG_KEY_{index}"] = os.environ.get(f"GIT_CONFIG_KEY_{index}")
            previous[f"GIT_CONFIG_VALUE_{index}"] = os.environ.get(f"GIT_CONFIG_VALUE_{index}")
            os.environ[f"GIT_CONFIG_KEY_{index}"] = key
            os.environ[f"GIT_CONFIG_VALUE_{index}"] = value
        os.environ["GIT_CONFIG_COUNT"] = str(offset + len(transport))
        try:
            yield GitHubOrigin(f"https://github.com/{slug}.git", certificate)
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            server.shutdown()
            server.server_close()
            thread.join(timeout=10)
