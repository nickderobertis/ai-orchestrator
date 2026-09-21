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

**What is deliberately absent is a rendered page.** That the bundle draws what the
reader serves is `onepipeline-ui`'s own tier to hold, and a journey here that drove a
browser put one on this repository's merge path, where nothing provisions it. So assert
what the reader answers, which is the layer a rendered row could only ever be as true
as; `tests/dag_ui/AGENTS.md` records what that cost, and
`test_no_browser_needed_e2e.py` beside this file is what holds it.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import http.client
import importlib.metadata
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import NamedTuple, NewType

import pytest
from nx_workspace import WORKSPACE_INSTALL_MARKS
from published_tools import PUBLISHED_TOOLS
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

pytestmark = [*WORKSPACE_INSTALL_MARKS]

#: The one source for the address the read API answers on, which the recipe reads
#: too — restating it here would let this journey pass while `just dag-ui` and
#: `just telemetry-server` stopped finding each other.
READ_API_ADDRESS = REPO_ROOT / "config" / "read-api.address"

#: The adopted `onepipeline-ui` release, taken from the same table every other pin
#: check reads rather than from a literal here. Both halves of that release — the
#: wheel behind `just telemetry-server` and the npm bundle behind `just dag-ui` —
#: are pinned to it, which is what makes one value enough to judge both.
ADOPTED_UI = next(tool for tool in PUBLISHED_TOOLS if tool.npm_package == "onepipeline-ui")
#: Where the npm half of that release installs, and what `scripts/dag-ui-server.js`
#: serves by default.
INSTALLED_BUNDLE = REPO_ROOT / "node_modules" / "onepipeline-ui"

#: A runs root holding real recorded runs, so the timeline `docs/telemetry.md`
#: documents can be asserted against what the adopted reader actually answers rather
#: than against the empty root the connectivity journeys use. They are checked in because
#: this host's own runs root is not reproducible: runs are added and reclaimed
#: continuously, so a journey reading it would assert against a different tree on every
#: invocation.
#: A recorded run's own id, and a dispatched conversation's. Both are ordinary strings
#: on the wire, and both are read by routes that take one or the other — so naming them
#: apart is what stops a conversation id being handed to a route that wants a run.
RunId = NewType("RunId", str)
ConversationId = NewType("ConversationId", str)
#: A launching session, which is what the API acts as and what a run's ownership is
#: compared against. Named apart from the two above for the same reason they are named
#: apart from each other: every ownership assertion here turns on not confusing the
#: session a server acts as with the session a run recorded, and both are bare strings
#: on the wire.
LauncherSession = NewType("LauncherSession", str)
TIMELINE_RUNS = REPO_ROOT / "tests" / "fixtures" / "timeline-runs"
#: The graph runs those recorded runs' graphs made, each with the graph document it
#: launched. The reader derives a session's `agent_role` from the members a run's recorded
#: graph declarations name and from nothing built in, so a recorded run served without
#: them has no lanes at all — and the runs above predate this host keeping those records
#: beside them. So the journey writes each record `oneagentgraph` would have kept, its
#: members read out of the document named here rather than restated: an observer graph
#: under the id its launch record names as `graph_run`, a node graph under the stream its
#: records were relayed on, and the derived monitor slice's observer under both, since the
#: slice took its one turn from a run whose launch it did not keep.
GRAPH_RUNS = {
    "dag-scope-1787250914995-537481": "graphs/dag-scope.yaml",
    "dag-scope-1787326722074-517521": "graphs/dag-scope.yaml",
    "dag-scope-1787308673405-2003245": "graphs/dag-scope.yaml",
    "node-scope-1787250915010-537481": "graphs/node-scope.yaml",
    "node-scope-1787250915010-537481-interrupt-537481": "graphs/node-scope.yaml",
    "node-scope-1786920912010-3261528": "graphs/node-scope.yaml",
    "node-scope-1787237768855-569067": "graphs/node-scope.yaml",
    "node-scope-1787318326076-2751798": "graphs/node-scope.yaml",
    "node-scope-1787318326076-2751798-interrupt-2751798": "graphs/node-scope.yaml",
    "node-scope-1787317190418-2313512": "graphs/node-scope.yaml",
    "pr-author-1786923240299-3261528": "graphs/pr-author.yaml",
    "pr-author-1787320393941-2751798": "graphs/pr-author.yaml",
}
#: The schema `oneagentgraph` writes a graph run's `record.json` under.
GRAPH_RECORD_SCHEMA = 3
GRAPH_STATE_ENV = "ONEAGENTGRAPH_STATE_DIR"
#: The smallest recorded run still exhibiting the whole per-node tier — a node, its
#: worker dispatch, and its gate run — which is what makes a 60KB fixture enough.
RECORDED_RUN = RunId("orchestrator-gate-fix-findings-2")
#: A second run, for the half the first one has no occasion to show: it published and
#: it queued behind a lock, so it carries the `publication`, `lock-wait` and
#: `pr-author` spans, and it settled, so it carries a `finished` phase and drops out
#: of the listing. Several runs rather than one because none exhibits every kind.
SETTLED_RUN = RunId("relink-race")
#: A third, for the supervisory tier: its pacemaker completed a turn and settled, which
#: is what makes a `dispatch` span exist at all. Neither run above has one — a run whose
#: observer never finished a turn records the members and no conversation.
#:
#: * Rewritten: one field, the `payload.detail` of its seventh event, whose recorded
#:   `Bash` call read a plan file the move to onetaskgraph plan storage has since
#:   deleted. It now names the shipped example project record that replaced it. The
#:   rewrite is declared here rather than made silently, for the same reason the slice
#:   below declares its own: a recording nobody can tell apart from an edited one is
#:   evidence of nothing. Nothing read from this fixture touches that payload — the
#:   journeys below read span shape — and its 623 events are otherwise untouched.
SUPERVISED_RUN = RunId("dag-ui-conversation")
#: A fourth, for the publication that reached its base: it is the one recorded here
#: whose publication span reads `merged`, and it reached that state without ever
#: carrying a change-request reference.
MERGED_RUN = RunId("gate-parity-2")
#: A fifth, for the `orchestrator` half of `agent_role`, and the one fixture here that
#: is **derived rather than recorded** — so its provenance is bounded on purpose:
#:
#: * Source: the recorded run `dag-ui-truth`, whose *monitor* member is the only one on
#:   this host ever to have completed a turn. That turn is what makes an `orchestrator`
#:   label exist, so no other run can stand in.
#: * Kept, verbatim: the six events whose `labels.run_id` is that run's dag-scope graph
#:   `dag-scope-1787308673405-2003245` — its `graph-started`, both members'
#:   `member-started`, the monitor's `turn-completed` and `member-settled`, and
#:   `graph-settled`.
#: * Dropped: that graph's `member-heartbeat` and `cron-reset` noise, and every event
#:   belonging to any other graph or node. The whole run is 8.9MB and its worker
#:   transcripts quote an `llmlint: ignore` directive that the judged tier then reads
#:   as a real directive and refuses, so checking the run in is not open to us.
#: * Rewritten: one field, `labels["onepipeline.run_id"]`, to this fixture's own id, so
#:   a reader cannot mistake six events for the run they came from.
SUPERVISING_RUN = RunId("dag-ui-truth-monitor-slice")
#: A sixth, and the only one here checked in **with its `reports/` directory**, which is
#: what makes it the one that can hold the conversation view to anything. Every other
#: fixture carries `events.jsonl` alone, and a run's events record that a turn happened;
#: they do not carry what the agent said, what its tools observed, or what the turn cost.
#: All three live in the dispatch's report, so a fixture without one serves a transcript
#: of empty turns at every release and can no more show this defect than show it fixed.
#:
#: Recorded verbatim — no field is rewritten and nothing is dropped but the run's
#: `driver.log`, its empty `dispatches/`, and its `channel/` supervisory queue, none of
#: which any route read here opens. It was chosen out of every run on this host for two
#: properties nothing else combined: its report quotes no `llmlint: ignore` directive, so
#: the judged tier cannot read the fixture's own transcript as a real suppression (the
#: hazard that keeps `dag-ui-truth` out of this directory), and its second turn is one
#: its supervisor asked for and the agent answered, so the release's whole delta is
#: visible in a two-turn conversation.
REPORTED_RUN = RunId("triage-by-root-cause-2")
#: That run's worker dispatch, whose two report turns the conversation route serves.
REPORTED_CONVERSATION = ConversationId("node-scope-1787317190418-2313512.worker")
#: The five figures a turn accounts for itself with. Named rather than inlined because
#: the assertion below is that a turn carries *all* of them, and a list that drifted
#: shorter would weaken that into whichever ones still happened to be served.
TURN_USAGE_FIGURES = (
    "inputTokens",
    "outputTokens",
    "cacheReadTokens",
    "cacheWriteTokens",
    "costUsd",
)
#: The timeline schema `docs/telemetry.md` documents for the adopted release. Restated
#: here rather than read from the response, because reading it from the response is what
#: an assertion about a schema version cannot do: the paragraph and the reader have to be
#: moved together, and a bump that moved neither would pass.
TIMELINE_SCHEMA_VERSION = 10
#: The envelope's telemetry schema, restated for the same reason and moved with the same
#: paragraph.
TELEMETRY_SCHEMA_VERSION = 19

#: The variable `scripts/launcher-session.sh` resolves the acting session into, and
#: `scripts/telemetry-server.sh` renders as the API's `--session`.
LAUNCHER_SESSION_ENV = "ONEPIPELINE_LAUNCHER_SESSION"
#: The session the ownership journeys act as. Not any run's recorded launcher, which
#: is the whole point: what is asserted is the refusal a *stranger* meets.
STRANGER_SESSION = LauncherSession("dag-ui-serving-e2e-stranger")
#: The qualified project ids the grouping fixture stamps onto copies of the recorded
#: runs. Two, because one group and one ungrouped remainder cannot show an ordering.
FIRST_PROJECT = "plans:dag-ui-grouping-first"
SECOND_PROJECT = "plans:dag-ui-grouping-second"

#: How many of the read API's keepalive comments an idle stream is held for: the first
#: proves the connection outlived one of its idle intervals, the second that it was not
#: the last thing the connection carried.
KEEPALIVES_HELD = 2
#: How much shorter than the read API's own the proxied stream's idle stretch may be.
#: Both are opened at once and paced by the same timer, so they differ by scheduling.
IDLE_TOLERANCE = 0.9


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _serve(command: list[str], environment: dict[str, str] | None = None) -> subprocess.Popen[str]:
    """Start one server, in a session of its own so the whole tree can be stopped.

    A server started here is never the process this returns: `just` runs the recipe
    in a shell, the wrapper script `exec`s the published CLI, and `uv run` starts the
    server under itself. `just` also does not exit on `SIGTERM` — it keeps waiting on
    the recipe — so signalling this process alone leaves the server bound to its port
    and holding these pipes open, which is a teardown that hangs until its timeout
    and a port that is still answering when the next journey binds one. A session of
    its own makes the whole tree one process group, which `_stop` signals as a whole.
    """
    return subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


def _stop(*processes: subprocess.Popen[str]) -> None:
    """Stop each server's process group, and reap what it started."""

    def signalled(process: subprocess.Popen[str], number: int) -> None:
        # A group already gone is one that exited on its own, and is reaped below.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, number)

    for process in processes:
        signalled(process, signal.SIGTERM)
    for process in processes:
        try:
            process.communicate(timeout=e2e_timeout(30))
        except subprocess.TimeoutExpired:
            signalled(process, signal.SIGKILL)
            process.communicate()


class Answer(NamedTuple):
    """One HTTP answer, in the three fields every assertion here reads off it.

    A named triple rather than a bare tuple: these are read positionally in a dozen
    places, and `status, body, kind` in the wrong order is a passing test.
    """

    status: int
    body: bytes
    content_type: str


@dataclass
class Served:
    """The bundle server this recipe started, and the read API it was pointed at."""

    base: str
    api: str

    def get(self, path: str) -> Answer:
        request = urllib.request.Request(f"{self.base}{path}")
        return self._answer(request)

    def post(self, path: str, payload: dict[str, object]) -> Answer:
        """One mutation, over the proxy, as the session the API was started under.

        Over the proxy for the same reason every read here is: the proxied origin is
        the only one the bundle asks on, so a mutation asserted against the API
        directly would prove nothing about the arrangement an operator acts through.
        """
        request = urllib.request.Request(
            f"{self.base}{path}",
            data=json.dumps(payload).encode(),
            headers={"content-type": "application/json"},
            method="POST",
        )
        return self._answer(request)

    def _answer(self, request: urllib.request.Request) -> Answer:
        try:
            with urllib.request.urlopen(request, timeout=e2e_timeout(10)) as response:
                return Answer(response.status, response.read(), response.headers.get_content_type())
        except urllib.error.HTTPError as refused:
            return Answer(refused.code, refused.read(), refused.headers.get_content_type())


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


@contextlib.contextmanager
def _both_recipes(
    runs_root: Path,
    graph_records: Path | None = None,
    acting_session: LauncherSession | None = None,
) -> Iterator[Served]:
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
    # The acting session is given the way an operator's shell gives it — through the
    # harness variable `scripts/launcher-session.sh` reads — rather than by spelling
    # `--session` here, because what this journey is about is the recipe deriving it.
    # `ONEPIPELINE_LAUNCHER_SESSION` is dropped first: this suite runs inside a
    # dispatch that exports one, and an inherited value would make every ownership
    # assertion below a statement about whoever launched the suite.
    serving = {
        **os.environ,
        GRAPH_STATE_ENV: str(graph_records or runs_root.parent / "graph-records"),
    }
    serving.pop(LAUNCHER_SESSION_ENV, None)
    serving.pop("CLAUDE_CODE_SESSION_ID", None)
    serving.pop("CLAUDE_SESSION_ID", None)
    serving.pop("CODEX_THREAD_ID", None)
    serving.pop("CODEX_SESSION_ID", None)
    if acting_session is not None:
        serving["CLAUDE_CODE_SESSION_ID"] = acting_session
    api = _serve(
        ["just", "telemetry-server", "--runs-dir", str(runs_root), "--port", str(api_port)],
        serving,
    )
    recipe = _serve(
        ["just", "dag-ui"],
        {
            **os.environ,
            "DAG_UI_PORT": str(ui_port),
            "DAG_UI_API_URL": f"http://127.0.0.1:{api_port}",
        },
    )
    base = f"http://127.0.0.1:{ui_port}"
    try:
        _await_ready(f"http://127.0.0.1:{api_port}/healthz", api, "the read API")
        _await_ready(f"{base}/", recipe, "the bundle server")
        yield Served(base=base, api=f"http://127.0.0.1:{api_port}")
    finally:
        _stop(recipe, api)


@pytest.fixture
def served(tmp_path: Path) -> Iterator[Served]:
    """The recipes over an empty runs root, which is what connectivity needs."""
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    with _both_recipes(runs_root) as pair:
        yield pair


@pytest.fixture
def served_recorded(tmp_path: Path) -> Iterator[Served]:
    """The same recipes over a private copy of the checked-in recorded run.

    Copied rather than served in place so no journey can write to the fixture: the
    read API only reads, but the recipes are the real ones and a future flag that
    wrote would corrupt the tree rather than a temporary directory.
    """
    runs_root = tmp_path / "runs"
    shutil.copytree(TIMELINE_RUNS, runs_root)
    graph_records = _graph_records(tmp_path / "graph-records")
    with _both_recipes(runs_root, graph_records) as pair:
        yield pair


# llmlint: ignore-block[tests_mirror_real_usage] The checked-in recorded runs all
# predate this host launching from a plan-store project, so none of them carries
# `project` in its launch record, and no producer verb stamps one onto a run that
# already happened — a real one would cost a launch per group. What this writes is the
# one field the engine reads to group by, in the launch record's own shape, onto a
# *copy* under a run id of its own; everything else about each run is the recorded
# fixture. A grouping asserted over one `(no project)` group would be satisfied by a
# reader that had not grouped at all, which is the assertion this exists to avoid.
def _grouped_runs_root(runs_root: Path) -> dict[str, str]:
    """Stamp a project onto copies of the recorded runs, and say which went where.

    Two projects and an ungrouped remainder, so the answer has three groups: enough
    for the partition, the `(no project)` rule and the ordering to each mean something.
    """
    placed = {
        f"{RECORDED_RUN}-first": FIRST_PROJECT,
        f"{SUPERVISING_RUN}-first": FIRST_PROJECT,
        f"{REPORTED_RUN}-second": SECOND_PROJECT,
    }
    for copied, project in placed.items():
        source = runs_root / copied.rsplit("-", 1)[0]
        destination = runs_root / copied
        shutil.copytree(source, destination)
        launch = json.loads((destination / "launch.json").read_text(encoding="utf-8"))
        launch["run_id"] = copied
        launch["project"] = project
        (destination / "launch.json").write_text(json.dumps(launch), encoding="utf-8")
    return placed


# llmlint: ignore-end[tests_mirror_real_usage]


class Grouped(NamedTuple):
    """The grouped pair, and which run this fixture put in which project."""

    served: Served
    placed: dict[str, str]


@pytest.fixture
def served_grouped(tmp_path: Path) -> Iterator[Grouped]:
    """The recipes over recorded runs some of which name a project, as a stranger."""
    runs_root = tmp_path / "runs"
    shutil.copytree(TIMELINE_RUNS, runs_root)
    placed = _grouped_runs_root(runs_root)
    graph_records = _graph_records(tmp_path / "graph-records")
    with _both_recipes(runs_root, graph_records, acting_session=STRANGER_SESSION) as pair:
        yield Grouped(pair, placed)


def _recorded_launcher_session(run: RunId) -> LauncherSession:
    """The session a checked-in recorded run names as its own launcher.

    Read off the fixture rather than restated, so the pair of journeys below stays a
    statement about *this* run's owner: a literal would keep passing against a fixture
    that had been replaced, with the stranger and the owner both strangers.
    """
    launch = json.loads((TIMELINE_RUNS / run / "launch.json").read_text(encoding="utf-8"))
    session = launch.get("session")
    assert isinstance(session, str) and session, (
        f"{run}'s launch record names no session, so nothing here can act as its owner"
    )
    return LauncherSession(session)


@pytest.fixture
def served_unattributed(tmp_path: Path) -> Iterator[Served]:
    """The recipes over a private copy, where this host can name no acting session."""
    runs_root = tmp_path / "runs"
    shutil.copytree(TIMELINE_RUNS, runs_root)
    graph_records = _graph_records(tmp_path / "graph-records")
    with _both_recipes(runs_root, graph_records, acting_session=None) as pair:
        yield pair


@pytest.fixture
def served_as_owner(tmp_path: Path) -> Iterator[Served]:
    """The recipes over a private copy, acting as the recorded run's own launcher."""
    runs_root = tmp_path / "runs"
    shutil.copytree(TIMELINE_RUNS, runs_root)
    graph_records = _graph_records(tmp_path / "graph-records")
    with _both_recipes(
        runs_root,
        graph_records,
        acting_session=_recorded_launcher_session(SETTLED_RUN),
    ) as pair:
        yield pair


def _declared_members(graph: str) -> list[str]:
    """The members one graph document declares, in its own order.

    Read with an indentation walk rather than a YAML library, as this suite's other
    readers of these documents are: the workspace installs none, and `oneagentgraph
    validate` over `graphs/` is what holds them well-formed.
    """
    lines = (REPO_ROOT / graph).read_text(encoding="utf-8").splitlines()
    members: list[str] = []
    for line in lines[lines.index("members:") + 1 :]:
        if line.strip() and not line.startswith(" "):
            break
        if re.match(r"^  [a-z][a-z0-9_-]*:\s*$", line):
            members.append(line.strip().rstrip(":"))
    assert members, f"{graph} declares no members"
    return members


# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] The recorded runs predate
# this host keeping `oneagentgraph`'s run records beside them, and no producer verb writes a
# record under a graph run id it did not mint, so this writes the record `oneagentgraph`'s
# schema-3 `Record` type declares — the shape onepipeline-ui's own suite writes through that
# type. A record the reader cannot parse leaves every session without an `agent_role`, which
# fails each lane assertion below by name, so a drift in the shape fails here.
# llmlint: ignore-block[tests_mirror_real_usage] Same site, same reason: there is no producer
# interface that records a declaration for a graph run that already happened.
def _graph_records(root: Path) -> Path:
    """One `record.json` per graph run in `GRAPH_RUNS`, as `oneagentgraph` keeps them."""
    for graph_run, graph in GRAPH_RUNS.items():
        (root / graph_run).mkdir(parents=True)
        record = {
            "schema_version": GRAPH_RECORD_SCHEMA,
            "run_id": graph_run,
            "graph": graph,
            "name": graph_run.rsplit("-", 2)[0],
            "started_ms": 0,
            "members": {},
            "declared_members": _declared_members(graph),
            "refs": [],
            "events_path": str(root / graph_run / "events.jsonl"),
        }
        (root / graph_run / "record.json").write_text(json.dumps(record), encoding="utf-8")
    return root


# llmlint: ignore-end[tests_mirror_real_usage]
# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]


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


def test_the_timeline_this_repository_documents_is_what_the_reader_answers(
    served_recorded: Served,
) -> None:
    """`docs/telemetry.md`'s timeline paragraph, held to the adopted reader's own answer.

    Asserted over the proxy, because the proxied origin is the only one the bundle
    asks on. Every assertion below fails on 0.5.0, which is what the pin was moved for.
    """
    status, body, content_type = served_recorded.get(
        f"/api/v2/runs/{RECORDED_RUN}/timeline?scope=run"
    )

    assert status == 200, body
    assert content_type == "application/json"
    timeline = json.loads(body)
    assert timeline["timeline_schema_version"] == TIMELINE_SCHEMA_VERSION, (
        f"the reader answers timeline schema {timeline['timeline_schema_version']}, and "
        f"docs/telemetry.md documents {TIMELINE_SCHEMA_VERSION}; re-measure that "
        "paragraph against what this release serves and move both together"
    )
    assert timeline["telemetry_schema_version"] == TELEMETRY_SCHEMA_VERSION, (
        f"the reader answers telemetry schema {timeline['telemetry_schema_version']}, and "
        f"docs/telemetry.md documents {TELEMETRY_SCHEMA_VERSION}; re-measure that "
        "paragraph against what this release serves and move both together"
    )

    spans = {span["kind"]: span for span in timeline["spans"]}
    assert spans.keys() == {"run", "node", "rollup", "verification"}, sorted(spans)

    # The per-node dispatch tier, which is the half 0.5.0 did not serve at all.
    rollup = spans["rollup"]
    assert rollup["label"] == "dispatch"
    assert rollup["agent_role"] == "worker"
    assert rollup["transport_role"] == "agent"
    assert rollup["count"] == 1
    assert rollup["parent_id"] == spans["node"]["id"], (
        "a dispatch rollup hangs off its node, not off the run; the paragraph "
        "distinguishes it from a `dispatch` span on exactly that"
    )

    # The gate run, whose `detail` is what makes a failed gate readable from the view.
    verification = spans["verification"]
    assert verification["status"] == "ok"
    assert verification["detail"].keys() == {"ok", "output_tail", "artifact_id"}

    # Every span is bounded the same way, which is what lets a duration be read off one.
    for kind, span in spans.items():
        assert span["started_at"], kind
        assert "ended_at" in span, kind


def _at(timestamp: str) -> datetime:
    """One span timestamp, for the assertions that are about where a span starts and ends."""
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))


def test_the_publication_and_wait_tier_is_what_the_reader_answers(
    served_recorded: Served,
) -> None:
    """The rest of the paragraph's span vocabulary, on a run that has occasion to show it.

    The other fixture never published, never queued behind a lock and never drafted a
    body, so a journey reading only that one leaves `publication`, `lock-wait` and the
    `pr-author` rollup as prose no response was ever held to.
    """
    status, body, _ = served_recorded.get(f"/api/v2/runs/{SETTLED_RUN}/timeline?scope=run")

    assert status == 200, body
    timeline = json.loads(body)
    assert timeline["timeline_schema_version"] == TIMELINE_SCHEMA_VERSION
    spans = [span for span in timeline["spans"] if span["kind"] != "verification"]
    node = next(span for span in spans if span["kind"] == "node")

    # A publication span hangs off its node and says where the change went.
    publication = next(span for span in spans if span["kind"] == "publication")
    assert publication["status"] == "open"
    assert publication["reference"] == {
        "kind": "pr",
        "value": "https://github.com/nickderobertis/onepipeline/pull/98",
    }
    assert publication["parent_id"] == node["id"]

    # And it is bounded by the publication rather than by the node that started it,
    # which is the half a status assertion cannot see: 0.5.0 left superseded spans
    # open-ended and stretched the survivors across their node's whole window, so a
    # duration read off this view was the node's work, not the publication's.
    assert publication["ended_at"] is not None
    started, ended = _at(publication["started_at"]), _at(publication["ended_at"])
    node_started, node_ended = _at(node["started_at"]), _at(node["ended_at"])
    assert node_started < started and ended < node_ended
    assert ended - started < (node_ended - node_started) / 2

    # Both rollup shapes, which the paragraph distinguishes on exactly these fields:
    # a `dispatch` carries roles and a `count`, a `lock-wait` carries neither.
    rollups = {span["label"]: span for span in spans if span["kind"] == "rollup"}
    assert rollups.keys() == {"dispatch", "lock-wait"}, sorted(rollups)
    assert rollups["lock-wait"]["total_duration_ms"] is not None
    assert rollups["lock-wait"].get("agent_role") is None
    assert rollups["lock-wait"].get("transport_role") is None

    dispatches = [span for span in spans if span.get("label") == "dispatch"]
    assert {span["agent_role"] for span in dispatches} == {"worker", "pr-author"}
    assert all(span["transport_role"] == "agent" for span in dispatches), dispatches
    assert all(span["parent_id"] == node["id"] for span in dispatches), dispatches
    assert all(span["count"] >= 1 for span in dispatches), dispatches


def test_a_merged_publication_reads_merged_and_need_carry_no_reference(
    served_recorded: Served,
) -> None:
    """The other end of a publication's life, and the `reference` clause's escape.

    A publication that reached its base reads `merged` rather than staying `open`, and
    this one got there with no change request at all — which is what makes "once it has
    one" a real qualifier rather than a hedge, and what a view rendering an empty link
    for every merged publication would get wrong.
    """
    status, body, _ = served_recorded.get(f"/api/v2/runs/{MERGED_RUN}/timeline?scope=run")

    assert status == 200, body
    spans = json.loads(body)["spans"]
    publication = next(span for span in spans if span["kind"] == "publication")

    assert publication["status"] == "merged"
    assert publication.get("reference") is None
    assert publication["ended_at"] is not None


def test_the_supervisory_dispatch_span_is_what_the_reader_answers(
    served_recorded: Served,
) -> None:
    """The supervisory tier, which is the whole reason this section of the view exists.

    A `dispatch` span and a `dispatch`-labelled `rollup` are the pair the paragraph
    warns are easy to confuse, so both are read off one response and told apart on the
    two fields that actually differ: what they hang off, and what they reference.
    """
    status, body, _ = served_recorded.get(f"/api/v2/runs/{SUPERVISED_RUN}/timeline?scope=run")

    assert status == 200, body
    timeline = json.loads(body)
    assert timeline["timeline_schema_version"] == TIMELINE_SCHEMA_VERSION
    spans = timeline["spans"]
    run_span = next(span for span in spans if span["kind"] == "run")
    assert run_span["phase"] == "settled"

    dispatch = next(span for span in spans if span["kind"] == "dispatch")
    assert dispatch["agent_role"] == "check-in"
    assert dispatch["transport_role"] == "agent"
    assert dispatch["status"] == "done"
    assert dispatch["reference"]["kind"] == "conversation"
    assert dispatch["parent_id"] == run_span["id"], (
        "a supervisory dispatch hangs off the run; only the per-node rollup hangs off a node"
    )

    # The rollup of the same name, for contrast: a node's tier, carrying a count.
    rollup = next(span for span in spans if span["kind"] == "rollup")
    assert rollup["label"] == "dispatch"
    assert rollup["agent_role"] == "worker"
    assert rollup["count"] >= 1
    assert rollup["parent_id"] != run_span["id"]


def test_the_monitors_dispatch_is_labelled_by_its_own_member_name(served_recorded: Served) -> None:
    """The other supervisory `agent_role`, served as the member the graph declares.

    0.5.0 served this dispatch with a null `agent_role`, and 0.5.x through 0.8.x renamed it
    `orchestrator` out of a vocabulary the reader built in. From 0.9.0 the lane is the
    member's own name, read off the observer graph's declaration — so this host's
    `monitor` is served as `monitor`. The pacemaker above proves the field is populated;
    only this proves it is populated *per member*, which a reader distinguishing the
    monitor from the pacemaker depends on.
    """
    status, body, _ = served_recorded.get(f"/api/v2/runs/{SUPERVISING_RUN}/timeline?scope=run")

    assert status == 200, body
    timeline = json.loads(body)
    assert timeline["timeline_schema_version"] == TIMELINE_SCHEMA_VERSION
    spans = timeline["spans"]
    run_span = next(span for span in spans if span["kind"] == "run")

    dispatch = next(span for span in spans if span["kind"] == "dispatch")
    assert dispatch["agent_role"] == "monitor"
    assert dispatch["transport_role"] == "agent"
    assert dispatch["reference"] == {
        "kind": "conversation",
        "value": "dag-scope-1787308673405-2003245.monitor",
    }
    assert dispatch["parent_id"] == run_span["id"]

    # The monitor's own turn is what carries the label, so the span has to hold it.
    assert [event["kind"] for event in dispatch["events"]] == ["turn-completed"]


def test_a_dispatchs_report_turns_carry_their_own_text_tools_and_usage(
    served_recorded: Served,
) -> None:
    """The conversation view, which is the whole of what this release was adopted for.

    The operator's report was that the view showed them no assistant text, no tool
    output, and no token or cost figures — so what is asserted is each of those three,
    per turn, off the route the view reads them from.

    Assistant text, model and usage are asserted per turn and separately, because they
    fail separately: a reader can be served one turn's reply where there were two, or a
    turn carrying the whole dispatch's totals instead of its own. The surrounding
    assertions — the route answering, the transcript belonging to this node, the turn
    count — keep those three from passing vacuously.

    The cost identity is the one worth keeping longest: presence alone passes on totals
    attributed to the wrong turn, while per-turn costs summing to the dispatch's own
    recorded spend cannot.
    """
    status, body, _ = served_recorded.get(
        f"/api/v2/runs/{REPORTED_RUN}/conversations/{REPORTED_CONVERSATION}"
    )

    assert status == 200, body
    served = json.loads(body)
    # The dispatch this transcript belongs to, so a conversation cannot be read as
    # some other node's: the view titles the panel from it.
    assert served["attribution"]["runId"] == REPORTED_RUN
    assert served["attribution"]["agentRole"] == "worker"
    turns = served["conversation"]["turns"]
    assert len(turns) == 2, turns

    for position, turn in enumerate(turns):
        assert turn["assistant"], (
            f"turn {position} was served with no assistant text ({turn['assistant']!r}); "
            "this is the operator's own report — the view has only the tool calls to show"
        )
        assert turn["model"], f"turn {position} names no model: {turn['model']!r}"
        # Exactly these, not merely these: a subset check would let the reader grow a
        # sixth figure this list never learned about, which is the drift the list is
        # here to catch rather than a gap it may quietly tolerate.
        assert set(turn["usage"]) == set(TURN_USAGE_FIGURES), (
            f"turn {position} accounts for itself with {sorted(turn['usage'])}, "
            f"where this file's one list of the figures says {sorted(TURN_USAGE_FIGURES)}"
        )
        missing = [figure for figure in TURN_USAGE_FIGURES if turn["usage"].get(figure) is None]
        assert not missing, f"turn {position} accounts for itself without {missing}"

    # The tool observations, which are half of what a transcript is for: a call whose
    # output is null renders as a command nobody has the result of.
    observed = [tool for turn in turns for tool in turn["tools"] if tool.get("output")]
    assert observed, "no tool call was served with its output"

    # And the identity that makes the figures above trustworthy rather than merely
    # present. Rounded to the millionth because these are floats summed out of a JSON
    # document, which is far tighter than the doubling it exists to catch.
    per_turn = sum(turn["usage"]["costUsd"] for turn in turns)
    reported = json.loads(served_recorded.get(f"/api/v2/runs/{REPORTED_RUN}")[1])
    dispatch_total = reported["run"]["usage"]["total"]["cost_usd"]

    assert round(per_turn, 6) == round(dispatch_total, 6), (
        f"the turns account for ${per_turn} against the ${dispatch_total} this run's "
        "report records — a view adding these up tells the operator the wrong number"
    )


def test_a_settled_run_keeps_its_timeline_but_leaves_the_listing(
    served_recorded: Served,
) -> None:
    """The listing is the live runs, and a finished one is reachable only by id.

    An operator who cannot find a run they know finished has met this rather than a
    broken reader, and a browser following the listing alone would never open it. Both
    halves are asserted together because either on its own reads as the other's bug.
    """
    listed = json.loads(served_recorded.get("/api/v2/runs")[1])

    assert listed["api_version"] == 2
    listed_runs = {run["run_id"]: run["phase"] for run in listed["runs"]}

    # The split is on the phase rather than on which fixtures happen to be checked in,
    # so adding one to this root does not quietly change what this journey claims.
    assert set(listed_runs) == {RECORDED_RUN, SUPERVISING_RUN, REPORTED_RUN}, listed_runs
    assert not {"settled", "finished"} & set(listed_runs.values()), listed_runs
    for absent in (SETTLED_RUN, SUPERVISED_RUN, MERGED_RUN):
        assert absent not in listed_runs, listed_runs

    # `surfacing` was one of the phases `docs/telemetry.md` recorded having observed
    # and being unable to hold, for want of a run carrying one. The fixture checked in
    # for the conversation view carries it, so it is held here rather than left to the
    # by-hand re-measurement that paragraph asks a bump for.
    assert listed_runs[REPORTED_RUN] == "surfacing", listed_runs

    # Gone from the listing, still served in full by id.
    status, body, _ = served_recorded.get(f"/api/v2/runs/{SETTLED_RUN}/timeline?scope=run")
    assert status == 200, body
    run_span = next(span for span in json.loads(body)["spans"] if span["kind"] == "run")
    assert run_span["phase"] == "finished"
    assert run_span["ended_at"] is not None


def test_the_projects_route_groups_the_runs_it_serves(served_grouped: Grouped) -> None:
    """`GET /api/v2/projects` is the grouped listing, served over the operator's origin.

    This is the shape the manager's own views moved to and the one the browser lands
    on, so what is held is the grouping *contract* rather than a rendering of it: every
    run the flat listing carries appears in exactly one group, a group carries the runs
    whose launch record names its project, the runs naming none are in **one** ordinary
    group rather than scattered or hidden, and the groups are ordered by their own
    newest activity. A reader that had not grouped at all satisfies none of those —
    which is why the fixture stamps two projects on rather than relying on the recorded
    runs, every one of which predates a project-carrying launch.
    """
    status, body, content_type = served_grouped.served.get("/api/v2/projects")

    assert status == 200, body
    assert content_type == "application/json"
    answer = json.loads(body)
    assert answer["api_version"] == 2
    groups = answer["projects"]

    # Every run the flat listing carries, in exactly one group and nowhere twice.
    # Against `include_settled=true`, because the two routes answer different
    # questions and this is the one worth stating rather than tripping over: the
    # grouped listing is the **whole** store grouped, where the flat default is the
    # live runs alone. A settled run is reachable from the project view and not from
    # the run list, which is the opposite of what an operator would guess.
    flat = served_grouped.served.get("/api/v2/runs?include_settled=true")
    listed = {run["run_id"] for run in json.loads(flat[1])["runs"]}
    grouped = [run["run_id"] for group in groups for run in group["runs"]]
    assert sorted(grouped) == sorted(listed), (
        f"the grouped listing carries {sorted(grouped)} and the flat one {sorted(listed)}; "
        "a run in neither is one an operator landing on the project view cannot reach, "
        "and a run in both groups is one they would act on twice"
    )

    by_project = {group["project"]: {run["run_id"] for run in group["runs"]} for group in groups}
    assert len(by_project) == len(groups), f"two groups carry one project id: {groups}"

    # Each stamped run is under the project its launch record names, and only there.
    for run, project in served_grouped.placed.items():
        if run not in listed:
            continue
        assert run in by_project.get(project, set()), (
            f"{run} names project {project} in its launch record and the reader served "
            f"it under {[p for p, runs in by_project.items() if run in runs]}"
        )

    # The runs naming no project are one ordinary group, never several and never hidden.
    assert None in by_project, (
        f"no group carries the runs that name no project: {sorted(by_project)}. Those "
        "runs are most of this host's history and a listing that drops them reads as an "
        "empty host"
    )
    assert by_project[None] == listed - set(served_grouped.placed), by_project

    # Total order, newest activity first, so two readings of one root agree.
    activity = [group["last_write_at"] for group in groups]
    assert activity == sorted(activity, reverse=True), (
        f"the groups are ordered {activity}; the contract is the group's own newest "
        "activity first, and an unstable order is one an operator cannot navigate by"
    )


def test_a_stop_from_the_browser_is_refused_for_a_run_this_session_does_not_own(
    served_grouped: Grouped,
) -> None:
    """The ownership rule reaches the browser, and the refusal names who to ask.

    This is the half that makes the Observatory safe to act from at all. Several
    managers share this host; the API performs every mutation as **one** acting
    session, the `--session` `scripts/telemetry-server.sh` derives, and a run another
    session launched has to be refused there exactly as `just stop` refuses it in a
    terminal. Asserted as the engine's own refusal rather than as a status code alone,
    because what an operator needs from it is the owner: a `409` naming nobody is a
    dead end, and `not_owner` naming the launcher is the next thing to do.

    The fixture runs are another session's by construction — they are checked-in
    recordings of runs this suite did not launch — so the stranger is genuinely one.
    Nothing is stopped here: the refusal is the whole assertion, and a run this server
    did own would be a mutation of a fixture copy for no added evidence.
    """
    status, body, content_type = served_grouped.served.post(f"/api/v2/runs/{SETTLED_RUN}/stop", {})

    assert status == 409, (status, body)
    assert content_type == "application/json"
    refusal = json.loads(body)["error"]
    assert refusal["code"] == "not_owner", refusal
    assert SETTLED_RUN in refusal["message"], refusal
    owner = json.loads(served_grouped.served.get(f"/api/v2/runs/{SETTLED_RUN}")[1])["launch"]
    assert owner["launcher"] in refusal["message"], (
        f"the refusal is {refusal['message']!r} and the run's recorded launcher is "
        f"{owner['launcher']!r}; an operator refused a stop needs the refusal to name "
        "whose run it is, or they have nowhere to go with it"
    )
    assert STRANGER_SESSION not in refusal["message"], (
        f"the refusal names the acting session {STRANGER_SESSION!r} as the owner: "
        f"{refusal['message']!r}. That is the server reporting itself as the owner of a "
        "run it was just refused, which would read as the rule having been applied "
        "backwards"
    )


def test_an_unattributed_server_owns_nothing_and_says_so_through_the_proxy(
    served_unattributed: Served,
) -> None:
    """What `scripts/telemetry-server.sh`'s header promises about a server with no session.

    Two observable consequences, both the reason the recipe hands the API a session at
    all: a stop it does not force is refused — here even the stop of the run's own
    recorded owner would be, because nobody is acting — and `GET /api/v2/unwatched`
    reports no run. The second is the dangerous one, because from a browser it reads as
    a host with nothing to supervise rather than as a server that cannot say who it is.

    `_both_recipes` clears every variable the acting-session ladder reads, so this is a
    real unattributed start and not one this journey merely asked for.
    """
    status, body, _ = served_unattributed.post(f"/api/v2/runs/{SETTLED_RUN}/stop", {})

    assert status == 409, (status, body)
    assert json.loads(body)["error"]["code"] == "not_owner", body

    status, body, _ = served_unattributed.get("/api/v2/unwatched")

    assert status == 200, body
    unwatched = json.loads(body)
    assert unwatched["reported"] == [], (
        f"an unattributed server reported {unwatched['reported']} as unwatched; it owns no "
        "run, so it has none to report — and a non-empty answer here would be one it could "
        "not have compared against anything"
    )


def test_the_recipe_hands_the_api_the_acting_session_so_an_owner_is_recognised(
    served_as_owner: Served,
) -> None:
    """The other half of the refusal above, and the one that proves the handoff.

    The refusal alone cannot: an **unattributed** server owns nothing and is refused
    every stop it does not force, so a `scripts/telemetry-server.sh` that had dropped
    `--session` entirely would satisfy that journey exactly as well. What separates the
    two is a stop the acting session *does* own — reached only when the session the
    recipe derived arrived at the API and matched the run's recorded launcher.

    So this asserts what the answer is **not**: not `not_owner`. What it is instead is
    the engine's own account of a run it cannot establish the state of — these are
    checked-in recordings with no `dispatches` directory — and that is left unasserted
    beyond its not being the ownership refusal, because it is a fact about the fixture
    rather than about the identity this journey is measuring.
    """
    status, body, _ = served_as_owner.post(f"/api/v2/runs/{SETTLED_RUN}/stop", {})

    assert status != 404, (
        f"the reader does not know {SETTLED_RUN} at all, so this measured nothing about "
        f"ownership: {body!r}"
    )
    refusal = json.loads(body).get("error", {}) if status >= 400 else {}
    assert refusal.get("code") != "not_owner", (
        f"acting as {SETTLED_RUN}'s own recorded launcher, the API still refused the "
        f"stop as another session's: {refusal}. The session `scripts/telemetry-server.sh` "
        "derives is not reaching `onepipeline-api serve --session`, which leaves every "
        "mutation from the browser unattributed and every stop refused"
    )


def _linked_onepipeline_release() -> str:
    """The `onepipeline` release the adopted read-API wheel was built against.

    The reader answers `/healthz` with this value, and nothing this repository
    tracks declares it — `config/onepipeline.version` pins the *engine CLI*, which
    is a separate adoption and is deliberately allowed to differ. So the expectation
    comes from the adopted wheel's own bill of materials, which ships in its
    `dist-info` and moves with every release: a literal here would have to be
    hand-edited on each bump, which is exactly the drift this journey is about.
    """
    distribution = importlib.metadata.distribution(ADOPTED_UI.distribution)
    boms = [entry for entry in distribution.files or [] if "sboms/" in str(entry)]
    if len(boms) != 1:
        pytest.fail(
            f"{ADOPTED_UI.distribution} must ship exactly one bill of materials to read "
            f"its linked releases from; found {[str(entry) for entry in boms]}"
        )
    components = json.loads(Path(boms[0].locate()).read_text(encoding="utf-8"))["components"]
    linked = [item["version"] for item in components if item["name"] == "onepipeline"]
    if len(linked) != 1:
        pytest.fail(
            f"{ADOPTED_UI.distribution} {ADOPTED_UI.adopted_version} must record exactly one "
            f"linked onepipeline release; found {linked}"
        )
    return str(linked[0])


def test_the_served_bundle_is_the_adopted_release(served: Served) -> None:
    """What a browser is handed is the npm half of the release `config/` adopted.

    Moving the pin installs a release; it does not put one in front of anybody. A
    `node_modules` nobody reinstalled goes on serving the bundle it already has, and
    no pin check can see that — every one of them reads a declaration rather than
    the server. So the release is read off the package the bundle server is serving
    out of, and the bytes handed back are held to that same package's `index.html`.
    """
    installed = json.loads((INSTALLED_BUNDLE / "package.json").read_text(encoding="utf-8"))

    assert installed["version"] == ADOPTED_UI.adopted_version, (
        f"{INSTALLED_BUNDLE} holds {ADOPTED_UI.npm_package} {installed['version']}, not the "
        f"adopted {ADOPTED_UI.adopted_version} — run 'just bootstrap' and restart 'just dag-ui'"
    )
    assert served.get("/")[1] == (INSTALLED_BUNDLE / "dist" / "index.html").read_bytes()


# llmlint: ignore[changed_behavior_has_e2e] no run on this host can carry a release event
def test_no_run_served_here_carries_a_release(served_recorded: Served) -> None:
    """The release tier of the timeline, on data that has nothing to put in it.

    The adopted view renders which release carried a landed node and what a held node
    awaits, and `AGENTS.md` records observing no release surface at all when these runs
    are opened. This is the layer under that observation, and the half this repository
    owns: what the reader serves. A view drawing a release row is `onepipeline-ui`'s
    own tier to hold — but a row can only ever be as true as the span behind it, and a
    reader that invented a `release` on a run that never had one would be telling an
    operator something untrue about where their work is however faithfully the bundle
    drew it.

    **This is a statement about these runs, not about this host.** They are checked-in
    fixtures and can never grow a release event, so declaring a release target here
    would not move it. The gate that fires on *that* is
    `tests/e2e/test_release_adoption_in_force_e2e.py::test_which_registered_repositories_declare_a_release_target`,
    which asks every registered identity rather than reading a frozen tree.
    """
    for run in (RECORDED_RUN, SETTLED_RUN, MERGED_RUN, REPORTED_RUN):
        status, body, _ = served_recorded.get(f"/api/v2/runs/{run}/timeline?scope=run")
        assert status == 200, body
        carrying = [span for span in json.loads(body)["spans"] if span.get("release") is not None]
        assert not carrying, (
            f"{run} serves {len(carrying)} span(s) carrying a release, and no run this "
            "repository checks in has a release event for one to have come from"
        )


def test_the_served_reader_links_the_engine_the_adopted_wheel_links(served: Served) -> None:
    """The reader answering says which engine release it reads run stores through.

    This host pins the engine that *writes* a run store and the reader of it
    separately, and nothing but `/healthz` says whether the reader answering is the
    one the adopted wheel would have started: a reader left over from before a bump
    holds the same port and serves the same route table. So the field is read off
    the running server and held to what the adopted wheel records linking.

    It bounds the answering release rather than naming it — the surface publishes no
    release of its own, and two `onepipeline-api-cli` releases linking one engine are
    indistinguishable here. That the *installed* wheel is the adopted one is
    `tests/test_published_tools.py`'s; what this adds is that the process answering
    is not some older reader still holding the port, which no file can show.
    """
    health = json.loads(served.get("/healthz")[1])

    assert health.get("onepipeline_version") == _linked_onepipeline_release(), (
        f"the read API answering here reports {health!r}, which is not what "
        f"{ADOPTED_UI.distribution} {ADOPTED_UI.adopted_version} links — "
        "run 'just bootstrap' and restart 'just telemetry-server'"
    )


@dataclass
class HeldStream:
    """What one event stream carried while the client that opened it sent nothing."""

    snapshot: bool
    keepalives: int
    #: The longest the stream went carrying nothing — read off the stream rather than
    #: restated here, because the interval that produces it is the read API's.
    longest_idle: float
    #: How long it lasted before the far side ended it, or `None` if it was still open.
    closed_after: float | None


def _keepalives(stream: str) -> int:
    """Count the SSE comment lines that are all an idle stream ever carries."""
    return sum(1 for line in stream.splitlines() if line.startswith(":"))


def _hold_event_stream(origin: str, keepalives: int) -> HeldStream:
    """Read `/api/v2/events` from ``origin``, never sending on it, as an `EventSource` does.

    The stream's only traffic is the opening snapshot and the read API's own keepalive
    comment, so waiting for ``keepalives`` of them is both the hold and the
    measurement: the gap between two is an idle stretch the connection survived.

    The wait is not scaled by `waits`, because it is not a hang guard — it is a signal
    ticking in another process, and a multiple of it would only sit longer on a
    schedule that does not move. The socket deadline is the hang guard, and it is
    scaled.
    """
    address = urllib.parse.urlsplit(origin)
    connection = http.client.HTTPConnection(
        address.hostname or "", address.port or 80, timeout=e2e_timeout(60)
    )
    started = last = time.monotonic()
    received = ""
    longest_idle = 0.0
    closed_after = None
    try:
        connection.request("GET", "/api/v2/events", headers={"Accept": "text/event-stream"})
        response = connection.getresponse()
        assert response.status == 200, f"{origin} refused the event stream: {response.status}"
        while _keepalives(received) < keepalives:
            # `read1` rather than `read`, which would block for a full buffer an idle
            # stream never fills. Both ways this route can end are the far side hanging
            # up: an empty result, and — because an event stream is sent chunked and
            # never reaches a terminating chunk — a truncated body.
            try:
                chunk = response.read1(4096)
            except (http.client.IncompleteRead, ConnectionError):
                chunk = b""
            arrived = time.monotonic()
            if not chunk:
                closed_after = arrived - started
                break
            received += chunk.decode()
            longest_idle = max(longest_idle, arrived - last)
            last = arrived
    finally:
        connection.close()
    return HeldStream(
        snapshot="event: snapshot" in received,
        keepalives=_keepalives(received),
        longest_idle=longest_idle,
        closed_after=closed_after,
    )


def test_an_idle_event_stream_is_held_open_through_the_proxy(served: Served) -> None:
    """The view's live telemetry is one stream nobody writes to, and it must survive.

    `Bun.serve` closes a connection nothing has been sent on after ten seconds by
    default, which is shorter than the interval between the read API's keepalives — so
    a proxy taking that default can never carry this stream. The browser renders each
    close as a live-telemetry warning, reconnects, and loses it again, which is an
    indicator that fires every few seconds and therefore says nothing when telemetry
    is genuinely gone.

    Both streams are held at once and asserted on separately, because "the stream
    closed" has two suspects. A journey watching only the proxied one would blame this
    repository for the read API's bug, and neither the bound nor the keepalive
    interval is restated here — the proxied stream is held to what the read API's own
    stream did beside it, whatever that turns out to be.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        proxied = pool.submit(_hold_event_stream, served.base, KEEPALIVES_HELD)
        direct = pool.submit(_hold_event_stream, served.api, KEEPALIVES_HELD)
        through_proxy, from_the_api = proxied.result(), direct.result()

    assert from_the_api.closed_after is None, (
        f"the read API closed its own event stream after {from_the_api.closed_after:.1f}s, "
        "so nothing here can be concluded about the proxy in front of it"
    )
    assert through_proxy.closed_after is None, (
        f"the proxy closed the event stream after {through_proxy.closed_after:.1f}s while "
        "the read API held its own open; `just dag-ui` is imposing an idle bound"
    )
    for what, held in (("proxied", through_proxy), ("direct", from_the_api)):
        assert held.snapshot, f"the {what} stream carried no opening snapshot"
        assert held.keepalives >= KEEPALIVES_HELD, (
            f"the {what} stream carried {held.keepalives} keepalive(s), not {KEEPALIVES_HELD}"
        )
    assert through_proxy.longest_idle >= from_the_api.longest_idle * IDLE_TOLERANCE, (
        f"the proxied stream went idle for at most {through_proxy.longest_idle:.1f}s where the "
        f"read API's own went {from_the_api.longest_idle:.1f}s, so the proxy is pacing it"
    )


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
    recipe = _serve(
        ["just", "dag-ui"],
        {**os.environ, "DAG_UI_PORT": str(ui_port), "DAG_UI_API_URL": api},
    )
    base = f"http://127.0.0.1:{ui_port}"
    try:
        _await_ready(f"{base}/", recipe, "the bundle server")
        status, body, content_type = Served(base=base, api=api).get("/api/v2/runs")
    finally:
        _stop(recipe)

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


# llmlint: ignore-block[tests_mirror_real_usage] These four journeys drive the server's
# refusal paths, and each one needs a repository state `just dag-ui` cannot be asked to
# produce: an address file that is not an address, a fabricated one to prove the file is
# what the proxy reads, an absent published bundle, and a bundle directory holding
# nothing. The recipe reads this checkout's own config and its real installed bundle, so
# through it none of these four conditions can be reached at all — running the server
# under a fabricated root is the only way to observe a refusal that only occurs when the
# configuration is broken. The journeys above drive the recipe itself for real.
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

    api = _serve(
        ["just", "telemetry-server", "--runs-dir", str(runs_root), "--port", str(api_port)]
    )
    unnamed = {key: value for key, value in os.environ.items() if key != "DAG_UI_API_URL"}
    recipe = _serve(
        ["bun", str(server / "dag-ui-server.js")],
        {
            **unnamed,
            "DAG_UI_DIST": str(REPO_ROOT / "node_modules/onepipeline-ui/dist"),
            "DAG_UI_PORT": str(ui_port),
        },
    )
    base = f"http://127.0.0.1:{ui_port}"
    try:
        _await_ready(f"http://127.0.0.1:{api_port}/healthz", api, "the read API")
        _await_ready(f"{base}/", recipe, "the bundle server")
        status, body, content_type = Served(base=base, api=f"http://127.0.0.1:{api_port}").get(
            "/api/v2/runs"
        )
    finally:
        _stop(recipe, api)

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


# llmlint: ignore-end[tests_mirror_real_usage]
# llmlint: ignore-block[tests_mirror_real_usage] This whole test is a drift gate over the
# two recipes' own text, not a behavioural journey, and the property it holds has no
# behavioural signature: a script carrying a hardcoded literal equal to the configured
# address serves byte-identical answers, so every operator-facing route through it passes
# while the second copy sits there waiting to drift. Both halves are that same gate — the
# dry run reads what the recipe would run without starting a server, and the loop reads
# whether either script restates the address. Reading the source is the only thing that can
# see either, which is why `tests/test_watch_surface_drift.py` and
# `tests/test_engine_contracts.py` read theirs. The journeys either side of this one
# start both servers for real and drive them over HTTP.
def test_the_read_api_address_has_one_source_both_recipes_read() -> None:
    """`just dag-ui` finds `just telemetry-server` only while they agree on it."""
    address = READ_API_ADDRESS.read_text(encoding="utf-8").strip()
    host, _, port = address.partition(":")
    assert host and port.isdigit(), address

    dry_run = subprocess.run(
        ["just", "--dry-run", "telemetry-server", "--host", host],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
    )
    assert dry_run.returncode == 0, dry_run.stderr

    # Neither script may carry its own copy of the address: that is the drift this
    # file exists to prevent, and a literal here would be a third one.
    for script in ("scripts/telemetry-server.sh", "scripts/dag-ui-server.js"):
        text = (REPO_ROOT / script).read_text(encoding="utf-8")
        assert "read-api.address" in text, f"{script} must read the address from its one source"
        assert address not in text, f"{script} restates the read API address"
    # llmlint: ignore-end[tests_mirror_real_usage]
