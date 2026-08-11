"""`just dag-ui` serves the published bundle and its read API on one origin.

The published packages split the view from its data: `onepipeline-ui` is a built
static bundle that asks for `/api/v2/...` relative to wherever it was served from,
and `onepipeline-api` serves that data and not the bundle. A browser accepts only
one arrangement of those two — same origin — because the read API sends no CORS
headers, so the proxy this recipe starts is load-bearing rather than convenience.

Nothing here is doubled. Both recipes run for real — `just telemetry-server` starts
the published read API and `just dag-ui` serves the published bundle against it —
so what a proxied request returns is what the read API itself said, and the two
recipes finding each other is part of what these journeys prove rather than
something a stand-in arranged. Both are free to run: the read API serves a local
run store and starts no agents.
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

from orchestrator.root import REPO_ROOT

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
    """The bundle server this recipe started, and the read API it was pointed at."""

    base: str
    api: str

    def get(self, path: str) -> tuple[int, bytes, str]:
        request = urllib.request.Request(f"{self.base}{path}")
        try:
            with urllib.request.urlopen(request, timeout=e2e_timeout(10)) as response:
                return response.status, response.read(), response.headers.get_content_type()
        except urllib.error.HTTPError as refused:
            return refused.code, refused.read(), refused.headers.get_content_type()


def _await_ready(url: str, process: subprocess.Popen[str], what: str) -> None:
    """Wait on the fact that ``url`` answers, rather than on a duration.

    Both servers are waited for, not just the one under test: a proxied request that
    arrives before the API behind it has bound answers 502 for a reason that has
    nothing to do with the proxy.
    """
    deadline = time.monotonic() + e2e_timeout(60)
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail(f"{what} exited early: {process.communicate()[1]}")
        try:
            with urllib.request.urlopen(url, timeout=2):
                return
        except urllib.error.HTTPError:
            return
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(0.1)
    pytest.fail(f"{what} never answered")


@pytest.fixture
def served(tmp_path: Path) -> Iterator[Served]:
    """Both recipes, for real: `just telemetry-server` behind `just dag-ui`.

    This is the arrangement the documentation tells an operator to start in two
    shells, and the published read API is the thing the proxy exists to reach — so
    it is what runs here. Ports are chosen per test rather than taken from
    `config/read-api.address`, because a suite that bound the one documented address
    could not run twice at once; the two are still wired to each other through the
    recipes' own flags.
    """
    api_port = _free_port()
    ui_port = _free_port()
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    api = subprocess.Popen(
        ["just", "telemetry-server", "--runs-dir", str(runs_root), "--port", str(api_port)],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
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
        _await_ready(f"http://127.0.0.1:{api_port}/healthz", api, "the read API")
        _await_ready(f"{base}/", recipe, "the bundle server")
        yield Served(base=base, api=f"http://127.0.0.1:{api_port}")
    finally:
        for process in (recipe, api):
            process.terminate()
            process.communicate(timeout=e2e_timeout(30))


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
    """The bundle asks for `/api/v2/...` where it was served from, and nowhere else.

    Asserted against what the read API itself answers directly, so the claim is that
    the proxy carried its answer rather than that something answered: a body the
    proxy could have synthesized proves nothing about which process produced it.
    """
    for path in ("/healthz", "/api/v2/runs"):
        status, body, content_type = served.get(path)
        direct = urllib.request.urlopen(f"{served.api}{path}", timeout=e2e_timeout(10))

        assert status == 200, path
        assert content_type == "application/json", path
        assert json.loads(body).keys() == json.loads(direct.read()).keys(), path

    # And the read API's own contract, which nothing but the read API produces.
    listed = json.loads(served.get("/api/v2/runs")[1])
    assert listed["api_version"] == 2
    assert listed["runs"] == []


def test_an_api_path_the_read_api_refuses_is_passed_back_as_it_refused_it(
    served: Served,
) -> None:
    """A proxy that invented its own answers would hide exactly this.

    The view distinguishes a route the read API does not serve from one it served
    empty, so the status and the body have to be the API's rather than the proxy's.
    """
    status, body, content_type = served.get("/api/v2/no-such-route")

    assert status == 404
    assert content_type == "application/json"
    assert json.loads(body) != {}


def test_a_client_route_falls_back_to_the_view_and_a_climb_out_does_not(served: Served) -> None:
    """An unknown path is a route the app owns; a path leaving the bundle is not."""
    status, body, _ = served.get("/?run=some-run&view=overall")
    assert status == 200
    assert b"<!doctype html>" in body[:20].lower()

    status, body, _ = served.get("/..%2fpackage.json")
    assert status == 404, body
    assert b"ai-orchestrator" not in body


def test_a_read_api_that_is_not_up_is_reported_rather_than_rendered() -> None:
    """Starting the two in two shells means one is often not up yet.

    A thrown proxy fetch renders a runtime error page into an XHR, which tells the
    operator nothing about which address refused. This answers in the error shape
    the view already reads.
    """
    ui_port = _free_port()
    api = f"http://127.0.0.1:{_free_port()}"
    recipe = subprocess.Popen(
        ["just", "dag-ui"],
        cwd=REPO_ROOT,
        env={**os.environ, "DAG_UI_PORT": str(ui_port), "DAG_UI_API_URL": api},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    base = f"http://127.0.0.1:{ui_port}"
    try:
        _await_ready(f"{base}/", recipe, "the bundle server")
        status, body, content_type = Served(base=base, api=api).get("/api/v2/runs")
    finally:
        recipe.terminate()
        recipe.communicate(timeout=e2e_timeout(30))

    assert status == 502
    assert content_type == "application/json"
    reported = json.loads(body)["error"]
    assert reported["code"] == "read_api_unreachable"
    assert "just telemetry-server" in reported["message"]


@pytest.mark.parametrize(
    ("variable", "value", "reason"),
    [
        ("DAG_UI_PORT", "not-a-port", "DAG_UI_PORT must be a port number, not not-a-port"),
        ("DAG_UI_HOST", "127.0.0.1:8765", "DAG_UI_HOST must be a hostname"),
        ("DAG_UI_API_URL", "127.0.0.1:8765", "DAG_UI_API_URL must be an http(s) URL"),
        ("DAG_UI_API_URL", "", "DAG_UI_API_URL must be an http(s) URL, not nothing"),
    ],
    ids=("port", "host", "api-url", "empty-api-url"),
)
def test_the_recipe_refuses_an_environment_value_that_is_not_one(
    variable: str, value: str, reason: str
) -> None:
    """Each of these becomes something whose own failure would blame the wrong thing.

    A misspelled port binds an arbitrary one, a host Bun cannot resolve throws out
    of `serve` and reads as a crash, and an address that is not an absolute origin
    makes every proxied request throw and be reported as the read API refusing. So
    each is refused at startup, naming the variable rather than the symptom.
    """
    result = subprocess.run(
        ["just", "dag-ui"],
        cwd=REPO_ROOT,
        env={**os.environ, variable: value},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
    )

    assert result.returncode != 0
    assert reason in result.stderr


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


def test_the_address_file_is_what_an_unnamed_api_proxies_to(tmp_path: Path) -> None:
    """`just dag-ui` with nothing named is the documented invocation, and it reads a file.

    Every other journey here names `DAG_UI_API_URL`, so only the refusal arm of that
    read runs and a target hardcoded beside it would pass the whole suite while the
    two recipes stopped finding each other. Driven the way the failure arm is — a
    throwaway tree carrying its own `config/read-api.address` — because the checked-in
    address is the one port a suite must not bind if it is to run twice at once.
    """
    api_port = _free_port()
    ui_port = _free_port()
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    server = tmp_path / "scripts"
    server.mkdir()
    shutil.copy2(REPO_ROOT / "scripts/dag-ui-server.js", server / "dag-ui-server.js")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "read-api.address").write_text(
        f"127.0.0.1:{api_port}\n", encoding="utf-8"
    )

    api = subprocess.Popen(
        ["just", "telemetry-server", "--runs-dir", str(runs_root), "--port", str(api_port)],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    unnamed = {key: value for key, value in os.environ.items() if key != "DAG_UI_API_URL"}
    recipe = subprocess.Popen(
        ["bun", str(server / "dag-ui-server.js")],
        env={
            **unnamed,
            "DAG_UI_DIST": str(REPO_ROOT / "node_modules/onepipeline-ui/dist"),
            "DAG_UI_PORT": str(ui_port),
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    base = f"http://127.0.0.1:{ui_port}"
    try:
        _await_ready(f"http://127.0.0.1:{api_port}/healthz", api, "the read API")
        _await_ready(f"{base}/", recipe, "the bundle server")
        status, body, content_type = Served(base=base, api=f"http://127.0.0.1:{api_port}").get(
            "/api/v2/runs"
        )
    finally:
        for process in (recipe, api):
            process.terminate()
            process.communicate(timeout=e2e_timeout(30))

    # The read API's own contract, which nothing but the read API produces — so the
    # proxy reached the address the file named rather than answering for it.
    assert status == 200, body
    assert content_type == "application/json"
    assert json.loads(body)["api_version"] == 2


def test_a_missing_published_bundle_is_named_rather_than_served_empty(tmp_path: Path) -> None:
    """A worktree with no install is the ordinary case a fresh clone is in.

    Serving an empty directory would answer every request with a 404 that reads as
    a broken app rather than as an install nobody ran yet. Driven at the default
    location — the server copied into a tree that genuinely has no `node_modules` —
    because that is the state the advice it gives is advice for.
    """
    server = tmp_path / "scripts"
    server.mkdir()
    shutil.copy2(REPO_ROOT / "scripts/dag-ui-server.js", server / "dag-ui-server.js")

    environment = {key: value for key, value in os.environ.items() if key != "DAG_UI_DIST"}
    result = subprocess.run(
        ["bun", str(server / "dag-ui-server.js")],
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
    )

    assert result.returncode == 2, result.stdout
    assert "no published bundle at" in result.stderr
    assert "just bootstrap" in result.stderr


def test_a_named_bundle_directory_that_holds_none_names_the_variable(tmp_path: Path) -> None:
    """`DAG_UI_DIST` is how the screenshot tier points the server elsewhere.

    Pointed at a directory with no bundle in it, the advice to run `just bootstrap`
    would be wrong: the install is fine and the variable is not. So this path says
    which variable to fix instead.
    """
    result = subprocess.run(
        ["bun", str(REPO_ROOT / "scripts/dag-ui-server.js")],
        env={**os.environ, "DAG_UI_DIST": str(tmp_path / "nothing-here")},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
    )

    assert result.returncode == 2, result.stdout
    assert "DAG_UI_DIST must name a directory holding a published bundle" in result.stderr


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
