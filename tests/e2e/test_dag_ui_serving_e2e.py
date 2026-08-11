"""`just dag-ui` serves the published bundle and its read API on one origin.

The published packages split the view from its data: `onepipeline-ui` is a built
static bundle that asks for `/api/v2/...` relative to wherever it was served from,
and `onepipeline-api` serves that data and not the bundle. A browser accepts only
one arrangement of those two — same origin — because the read API sends no CORS
headers, so the proxy this recipe starts is load-bearing rather than convenience.

Everything here is real: the real recipe, the real server, the real published
bundle out of `node_modules`, and a real HTTP server standing in for the read API
at the address the recipe was pointed at. That stand-in is the boundary below the
seam under test — the API is proven in its own repository — and it is what lets
these journeys assert what came back rather than only that something did.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from nx_workspace import requires_workspace_install
from waits import timeout as e2e_timeout

from orchestrator import REPO_ROOT

pytestmark = [requires_workspace_install]

#: The one source for the address the read API answers on, which the recipe reads
#: too — restating it here would let this journey pass while `just dag-ui` and
#: `just telemetry-server` stopped finding each other.
READ_API_ADDRESS = REPO_ROOT / "config" / "read-api.address"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@dataclass
class Served:
    """The bundle server this recipe started, and the API it was pointed at."""

    base: str
    api_requests: Path

    def get(self, path: str) -> tuple[int, bytes, str]:
        request = urllib.request.Request(f"{self.base}{path}")
        try:
            with urllib.request.urlopen(request, timeout=e2e_timeout(10)) as response:
                return response.status, response.read(), response.headers.get_content_type()
        except urllib.error.HTTPError as refused:
            return refused.code, refused.read(), refused.headers.get_content_type()


def _await_ready(base: str, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + e2e_timeout(60)
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail(f"the recipe exited early: {process.communicate()[1]}")
        try:
            with urllib.request.urlopen(f"{base}/", timeout=2):
                return
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(0.1)
    pytest.fail("the bundle server never answered")


@pytest.fixture
def served(tmp_path: Path) -> Iterator[Served]:
    """`just dag-ui`, running against a stand-in read API on a port of its own."""
    api_port = _free_port()
    ui_port = _free_port()
    api_requests = tmp_path / "api-requests"
    api = subprocess.Popen(
        [
            "python3",
            "-c",
            "import http.server, json, sys\n"
            "class Handler(http.server.BaseHTTPRequestHandler):\n"
            "    def do_GET(self):\n"
            "        open(sys.argv[2], 'a').write(self.path + chr(10))\n"
            "        body = json.dumps({'served': self.path}).encode()\n"
            "        self.send_response(200)\n"
            "        self.send_header('content-type', 'application/json')\n"
            "        self.send_header('content-length', str(len(body)))\n"
            "        self.end_headers()\n"
            "        self.wfile.write(body)\n"
            "    def log_message(self, *args):\n"
            "        pass\n"
            "http.server.HTTPServer(('127.0.0.1', int(sys.argv[1])), Handler).serve_forever()\n",
            str(api_port),
            str(api_requests),
        ],
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    recipe = subprocess.Popen(
        ["just", "dag-ui"],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "DAG_UI_PORT": str(ui_port),
            "DAG_UI_API_URL": f"http://127.0.0.1:{api_port}",
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    base = f"http://127.0.0.1:{ui_port}"
    try:
        _await_ready(base, recipe)
        yield Served(base=base, api_requests=api_requests)
    finally:
        recipe.terminate()
        recipe.communicate(timeout=e2e_timeout(30))
        api.terminate()
        api.communicate(timeout=e2e_timeout(30))


def test_the_recipe_serves_the_published_bundle(served: Served) -> None:
    """The view an operator opens is the installed package, not a copy kept here."""
    status, body, _ = served.get("/")

    assert status == 200
    assert body == (REPO_ROOT / "node_modules/onepipeline-ui/dist/index.html").read_bytes()

    # And its assets, which is what makes the page more than markup: an index that
    # loads while its bundle 404s renders nothing and looks like a working server.
    asset = body.decode().split('src="', 1)[1].split('"', 1)[0]
    status, script, content_type = served.get(asset)
    assert status == 200, asset
    assert "javascript" in content_type, content_type
    assert len(script) > 1000


def test_the_read_api_answers_on_the_same_origin_as_the_view(served: Served) -> None:
    """The bundle asks for `/api/v2/...` where it was served from, and nowhere else."""
    status, body, _ = served.get("/api/v2/runs")
    assert status == 200
    assert json.loads(body) == {"served": "/api/v2/runs"}

    status, body, _ = served.get("/healthz")
    assert status == 200
    assert json.loads(body) == {"served": "/healthz"}

    # Reaching the API at all is the claim, so it is asserted where it arrived: the
    # stand-in records every path it was asked for.
    assert served.api_requests.read_text().split() == ["/api/v2/runs", "/healthz"]


def test_a_client_route_falls_back_to_the_view_and_a_climb_out_does_not(served: Served) -> None:
    """An unknown path is a route the app owns; a path leaving the bundle is not."""
    status, body, _ = served.get("/?run=some-run&view=overall")
    assert status == 200
    assert b"<!doctype html>" in body[:20].lower()

    status, body, _ = served.get("/..%2fpackage.json")
    assert status == 404, body
    assert b"ai-orchestrator" not in body


def test_a_read_api_that_is_not_up_is_reported_rather_than_rendered(tmp_path: Path) -> None:
    """Starting the two in two shells means one is often not up yet.

    A thrown proxy fetch renders a runtime error page into an XHR, which tells the
    operator nothing about which address refused. This answers in the error shape
    the view already reads.
    """
    ui_port = _free_port()
    recipe = subprocess.Popen(
        ["just", "dag-ui"],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "DAG_UI_PORT": str(ui_port),
            "DAG_UI_API_URL": f"http://127.0.0.1:{_free_port()}",
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    base = f"http://127.0.0.1:{ui_port}"
    try:
        _await_ready(base, recipe)
        status, body, content_type = Served(base=base, api_requests=tmp_path / "unused").get(
            "/api/v2/runs"
        )
    finally:
        recipe.terminate()
        recipe.communicate(timeout=e2e_timeout(30))

    assert status == 502
    assert content_type == "application/json"
    reported = json.loads(body)["error"]
    assert reported["code"] == "read_api_unreachable"
    assert "just telemetry-server" in reported["message"]


def test_the_recipe_refuses_a_port_that_is_not_one(tmp_path: Path) -> None:
    """A misspelled port is a request this host cannot honour, not one to guess at."""
    result = subprocess.run(
        ["just", "dag-ui"],
        cwd=REPO_ROOT,
        env={**os.environ, "DAG_UI_PORT": "not-a-port"},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
    )

    assert result.returncode != 0
    assert "DAG_UI_PORT must be a port number, not not-a-port" in result.stderr


def test_a_read_api_address_file_that_is_not_one_is_refused(tmp_path: Path) -> None:
    """A misshapen address would become a proxy target that fails later as a 502.

    Blaming the read API for a file this repository got wrong is the failure this
    check exists to prevent, so the server refuses at startup and names the file.
    """
    server = tmp_path / "scripts"
    server.mkdir()
    shutil.copy2(REPO_ROOT / "scripts/dag-ui-server.js", server / "dag-ui-server.js")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "read-api.address").write_text("not an address\n", encoding="utf-8")

    result = subprocess.run(
        ["bun", str(server / "dag-ui-server.js")],
        env={**os.environ, "DAG_UI_DIST": str(REPO_ROOT / "node_modules/onepipeline-ui/dist")},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
    )

    assert result.returncode == 2, result.stdout
    assert "read-api.address must hold one HOST:PORT, not not an address" in result.stderr


def test_a_missing_published_bundle_is_named_rather_than_served_empty(tmp_path: Path) -> None:
    """A worktree with no install is the ordinary case a fresh clone is in.

    Serving an empty directory would answer every request with a 404 that reads as
    a broken app rather than as an install nobody ran yet.
    """
    result = subprocess.run(
        ["bun", str(REPO_ROOT / "scripts/dag-ui-server.js")],
        env={**os.environ, "DAG_UI_DIST": str(tmp_path / "nothing-here")},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
    )

    assert result.returncode == 2, result.stdout
    assert "no published bundle at" in result.stderr
    assert "just bootstrap" in result.stderr


def test_the_read_api_address_has_one_source_both_recipes_read() -> None:
    """`just dag-ui` finds `just telemetry-server` only while they agree on it."""
    address = READ_API_ADDRESS.read_text(encoding="utf-8").strip()
    host, _, port = address.partition(":")
    assert host and port.isdigit(), address

    served = subprocess.run(
        ["just", "--dry-run", "telemetry-server", "--host", host],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
    )
    assert served.returncode == 0, served.stderr

    # Neither script may carry its own copy of the address: that is the drift this
    # file exists to prevent, and a literal here would be a third one.
    for script in ("scripts/telemetry-server.sh", "scripts/dag-ui-server.js"):
        text = (REPO_ROOT / script).read_text(encoding="utf-8")
        assert "read-api.address" in text, f"{script} must read the address from its one source"
        assert address not in text, f"{script} restates the read API address"
