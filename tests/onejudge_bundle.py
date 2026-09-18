"""onejudge's published frame bundle, derived from the installed release, and served offline.

`config/onemessagebus.yaml` links the bundle onejudge publishes at its release tag, and
every verb that loads that file resolves the link before it does anything else — the
queue verbs, `serve`, the engine a launch hands the file to. So a suite that loaded the
real file against an empty cache would fetch from GitHub on its first verb, and fail on a
host with no network. Nothing here does.

The bundle is never a hand-maintained copy. The installed onejudge SDK ships the frame
schemas the release was generated from (`_generated/schemas.json`, keyed
`agent.onejudge-frame.<op>@<protocol>`), and `bundle()` composes Contract L's document
out of exactly those entries, declaring the protocol their ids name. What that proves is
held by `tests/test_onemessagebus_config.py`; this module only makes the bundle and
reaches the bus with it.

`seed_process_cache()` is how every process of the suite resolves the committed link
with no request leaving the host: a loopback CONNECT proxy, named by the `HTTPS_PROXY`
the bus's Contract L honours, terminates TLS for the link's own host with a certificate a
throwaway authority issued, and `SSL_CERT_FILE` — which the same contract says replaces
the platform's roots — trusts that authority alone for the one `schemas fetch` that warms
a cache private to this process. The cache then answers every later resolution, for
`serve` without revalidation and for every other verb inside a freshness window this sets
past the life of the suite. The bus's own interfaces store the entry, so this writes
nothing in the cache's private layout.
"""

from __future__ import annotations

import http.server
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from orchestrator.root import REPO_ROOT

#: This host's bus configuration, whose `schemas` links the suite has to resolve offline.
CONFIG = REPO_ROOT / "config" / "onemessagebus.yaml"

#: The adopted onejudge release, whose tag the committed link names.
ONEJUDGE_VERSION_FILE = REPO_ROOT / "config" / "onejudge.version"

#: How the SDK keys a frame schema: `agent.onejudge-frame.<op>@<protocol>`.
FRAME_ID = re.compile(r"^agent\.onejudge-frame\.(?P<op>[a-z]+)@(?P<protocol>\d+)$")

#: The variables Contract L reads the cache and its freshness from.
CACHE_DIR_ENV = "ONEMESSAGEBUS_SCHEMA_CACHE_DIR"
TTL_ENV = "ONEMESSAGEBUS_SCHEMA_TTL"

#: A freshness window longer than any suite runs, so no verb after the seed revalidates.
SUITE_TTL_SECONDS = str(10 * 365 * 24 * 3600)

#: How long the one warming fetch may take before it is called hung.
FETCH_SECONDS = 60


# `Any` because each value is a JSON Schema document onejudge generated: it is handed to
# the bus whole and never read here, so there is no narrower shape to hold it to.
@cache
def installed_frames() -> dict[str, dict[str, Any]]:
    """Every frame schema the installed onejudge SDK carries, keyed by schema id."""
    generated = resources.files("onejudge_sdk") / "_generated" / "schemas.json"
    frames = json.loads(generated.read_text(encoding="utf-8"))["frames"]
    assert frames and all(FRAME_ID.match(key) for key in frames), sorted(frames)
    return dict(frames)


def protocol() -> str:
    """The protocol version the installed onejudge speaks, read off its frame ids."""
    spoken = {
        found.group("protocol") for key in installed_frames() if (found := FRAME_ID.match(key))
    }
    assert len(spoken) == 1, f"the installed onejudge names several protocols: {sorted(spoken)}"
    return spoken.pop()


# `Any` for the same reason: the bundle is Contract L's JSON document, written out whole.
def bundle(*, version: str | None = None) -> dict[str, Any]:
    """Contract L's bundle document over the installed frames, declaring `version`.

    The declared version is the protocol unless a caller names another, which is how a
    journey makes a bundle this host's pin must refuse.
    """
    return {
        "version": version or protocol(),
        "description": "onejudge command-provider request frames",
        "schemas": [{"id": key, "schema": value} for key, value in installed_frames().items()],
    }


def write_bundle(path: Path, *, version: str | None = None) -> Path:
    """Write `bundle()` to `path`, and return it."""
    path.write_text(json.dumps(bundle(version=version)), encoding="utf-8")
    return path


def configured_links(config: Path = CONFIG) -> list[str]:
    """The `schemas` links a configuration names, read off its own lines."""
    lines = config.read_text(encoding="utf-8").splitlines()
    opened = lines.index("schemas:")
    links = []
    for line in lines[opened + 1 :]:
        if line.startswith("  - "):
            links.append(line[4:].strip().strip('"'))
        elif line.strip() and not line.lstrip().startswith("#"):
            break
    assert links, f"{config} names no schema link"
    return links


def relinked_copy(destination: Path, link: str, *, replacing: dict[str, str] | None = None) -> Path:
    """A copy of this host's configuration differing only in where its one link points.

    `replacing` changes further lines verbatim — the one use is shortening the reply
    window, which each caller names — so a copy never drifts from the committed file in
    anything it did not say.
    """
    committed = configured_links()
    assert len(committed) == 1, committed
    text = CONFIG.read_text(encoding="utf-8")
    text = text.replace(f'"{committed[0]}"', json.dumps(link))
    for old, new in (replacing or {}).items():
        assert text.count(old) == 1, f"{old!r} is not one line of {CONFIG.name}"
        text = text.replace(old, new)
    destination.write_text(text, encoding="utf-8")
    return destination


def _openssl(*arguments: str) -> None:
    subprocess.run(["openssl", *arguments], check=True, capture_output=True, timeout=60)


def _authority(directory: Path, hosts: set[str]) -> tuple[Path, ssl.SSLContext]:
    """A throwaway certificate authority, and a server context for `hosts` it issued.

    Each `openssl` argv below is left as written under `# fmt: skip`: the formatter would
    put every option and its value on a line of its own, and a command that reads as
    the command line it is beats one argument per line.
    """
    authority, key = directory / "authority.pem", directory / "authority.key"
    _openssl(
        "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
        "-keyout", str(key), "-out", str(authority), "-subj", "/CN=suite authority",
        "-addext", "basicConstraints=critical,CA:TRUE",
        "-addext", "keyUsage=critical,keyCertSign",
    )  # fmt: skip
    leaf, leaf_key, request = directory / "leaf.pem", directory / "leaf.key", directory / "leaf.csr"
    _openssl(
        "req", "-newkey", "rsa:2048", "-nodes", "-keyout", str(leaf_key),
        "-out", str(request), "-subj", f"/CN={sorted(hosts)[0]}",
    )  # fmt: skip
    extensions = directory / "leaf.ext"
    names = ",".join(f"DNS:{host}" for host in sorted(hosts))
    extensions.write_text(
        f"subjectAltName={names}\nbasicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\n",
        encoding="utf-8",
    )
    _openssl(
        "x509", "-req", "-in", str(request), "-CA", str(authority), "-CAkey", str(key),
        "-CAcreateserial", "-out", str(leaf), "-days", "2", "-extfile", str(extensions),
    )  # fmt: skip
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(leaf, leaf_key)
    return authority, context


def _serve_through_a_tunnel(context: ssl.SSLContext, body: bytes) -> tuple[socket.socket, str]:
    """A loopback proxy answering every CONNECT with TLS and every GET with `body`."""
    listener = socket.create_server(("127.0.0.1", 0))

    def read_head(connection: socket.socket | ssl.SSLSocket) -> bytes:
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = connection.recv(4096)
            if not chunk:
                break
            head += chunk
        return head

    def answer(connection: socket.socket) -> None:
        with connection:
            if not read_head(connection).startswith(b"CONNECT "):
                return
            connection.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            with context.wrap_socket(connection, server_side=True) as tunnel:
                read_head(tunnel)
                tunnel.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                    + body
                )

    def accept() -> None:
        while True:
            try:
                connection, _ = listener.accept()
            except OSError:
                return
            threading.Thread(target=answer, args=(connection,), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()
    return listener, f"http://127.0.0.1:{listener.getsockname()[1]}"


def seed_process_cache() -> Path:
    """Warm a schema cache private to this process with the committed links, offline.

    Exports the cache and its freshness window into this process's environment, so every
    verb and launch the suite spawns inherits them, and returns the cache directory.
    """
    links = configured_links()
    hosts = {urlsplit(link).hostname or "" for link in links if link.startswith("https://")}
    directory = Path(tempfile.mkdtemp(prefix="onemessagebus-schemas-"))
    cache_dir = directory / "cache"
    authority, context = _authority(directory, hosts)
    listener, proxy = _serve_through_a_tunnel(context, json.dumps(bundle()).encode())
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.lower() not in {"no_proxy", "http_proxy", "https_proxy", "ssl_cert_dir"}
    }
    environment.update({"HTTPS_PROXY": proxy, "SSL_CERT_FILE": str(authority)})
    environment[CACHE_DIR_ENV] = str(cache_dir)
    try:
        fetched = subprocess.run(
            [_bus(), "schemas", "fetch", "--config", str(CONFIG), "--format", "text"],
            env=environment,
            capture_output=True,
            text=True,
            timeout=FETCH_SECONDS,
            check=False,
        )
    finally:
        listener.close()
    assert fetched.returncode == 0, (
        f"the suite could not warm its schema cache from {CONFIG.name}'s links, offline, "
        f"with the bundle derived from the installed onejudge:\n{fetched.stdout}{fetched.stderr}"
    )
    os.environ[CACHE_DIR_ENV] = str(cache_dir)
    os.environ[TTL_ENV] = SUITE_TTL_SECONDS
    return cache_dir


def _bus() -> str:
    """The `onemessagebus` this checkout pins, never one the ambient PATH offers first."""
    pinned = REPO_ROOT / ".venv" / "bin" / "onemessagebus"
    found = str(pinned) if pinned.is_file() else shutil.which("onemessagebus")
    assert found is not None, "onemessagebus is not installed; run scripts/session-setup.sh"
    return found


class LoopbackOrigin:
    """A loopback origin serving one bundle, answering a conditional request with `304`."""

    ETAG = '"the-one-bundle"'

    def __init__(self, body: bytes) -> None:
        self.body = body
        self.requests: list[str | None] = []
        origin = self

        class Serve(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 — the name is BaseHTTPRequestHandler's
                condition = self.headers.get("If-None-Match")
                origin.requests.append(condition)
                if condition == origin.ETAG:
                    self.send_response(304)
                    self.send_header("ETag", origin.ETAG)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("ETag", origin.ETAG)
                self.send_header("Content-Length", str(len(origin.body)))
                self.end_headers()
                self.wfile.write(origin.body)

            def log_message(self, format: str, *args: object) -> None:
                """Nothing: the requests are recorded above, where the journey reads them."""

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Serve)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/judge-seat-frames.json"
