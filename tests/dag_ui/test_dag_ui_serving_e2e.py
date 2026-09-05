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

import concurrent.futures
import contextlib
import http.client
import importlib.metadata
import json
import os
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
TIMELINE_RUNS = REPO_ROOT / "tests" / "fixtures" / "timeline-runs"
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
TIMELINE_SCHEMA_VERSION = 8

#: The word every one of the adopted view's release surfaces is built out of — its
#: heading, all three wait states, and the row naming what a node adopted. Matched as
#: the bare word rather than as the four rendered labels on purpose: mirroring upstream
#: wording here would make this gate vacuous the day upstream reworded it, since the
#: assertion is an *absence* and a label nobody renders any more is absent for the wrong
#: reason. The word is broader than the labels and owned by nobody.
RELEASE_ON_THE_PAGE = "release"
#: One viewport, not the matrix `just dag-ui-screens` photographs at. The reflow
#: defects that matrix exists to catch are a different question from whether the
#: adopted bundle renders a runs root at all, and this is the second one — so it pays
#: for one desktop render rather than five.
RENDER_VIEWPORT = (1440, 900)
#: What the app is waited on before its rendered text is read, per view. Waiting on a
#: *rendered string* rather than on the network is the whole point: this bundle answers
#: `networkidle` while it still says "Loading execution history…", so a journey that
#: waited on the network would assert against a spinner and pass at any release.
RENDER_SETTLED = {"overall": "WALL TIME", "graph": "task-failed"}
#: The browser driver, kept in this module rather than in `scripts/` deliberately: it
#: is test machinery for reading a rendered page, not a command this repository offers
#: an operator, and a script under `scripts/` would owe a journey of its own.
#:
#: Playwright is the workspace's own devDependency and reports where it installed its
#: browser, so nothing here hardcodes a cache path. It is required by an absolute path
#: under the workspace root rather than by bare name, because `bun` resolves a bare
#: `require` from the *script's* own directory and this script is written into a pytest
#: temporary directory outside the workspace — so a bare name resolved to nothing, and
#: `bun` silently installed the newest playwright from the registry instead of the one
#: `bun.lock` pins. That stayed invisible while the two happened to agree and broke the
#: moment they did not: the fetched release wanted a browser build the workspace's own
#: install had never downloaded. A test that reaches the network for an unpinned
#: dependency is not testing the workspace, so the path is explicit.
RENDER_DRIVER = """
const [root, url, width, height, settled] = process.argv.slice(2);
const { chromium } = require(root + '/node_modules/playwright');
const browser = await chromium.launch({ args: ['--no-sandbox'] });
const page = await browser.newPage({
  viewport: { width: Number(width), height: Number(height) },
});
await page.goto(url, { timeout: 30000 });
await page.getByText(settled, { exact: false }).first().waitFor({ timeout: 30000 });
process.stdout.write(await page.evaluate(() => document.body.innerText));
await browser.close();
"""

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
def _both_recipes(runs_root: Path) -> Iterator[Served]:
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
    api = _serve(
        ["just", "telemetry-server", "--runs-dir", str(runs_root), "--port", str(api_port)]
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
    with _both_recipes(runs_root) as pair:
        yield pair


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


def test_the_monitors_dispatch_is_labelled_orchestrator(served_recorded: Served) -> None:
    """The other supervisory `agent_role`, and the one this release was adopted to fix.

    0.5.0 served this dispatch with a null `agent_role`, so the monitor was present in
    the timeline and unattributable in it — the defect the pin moved for. The pacemaker
    above proves the field is populated; only this proves it is populated *per role*,
    which a reader distinguishing the monitor from the pacemaker depends on.
    """
    status, body, _ = served_recorded.get(f"/api/v2/runs/{SUPERVISING_RUN}/timeline?scope=run")

    assert status == 200, body
    timeline = json.loads(body)
    assert timeline["timeline_schema_version"] == TIMELINE_SCHEMA_VERSION
    spans = timeline["spans"]
    run_span = next(span for span in spans if span["kind"] == "run")

    dispatch = next(span for span in spans if span["kind"] == "dispatch")
    assert dispatch["agent_role"] == "orchestrator"
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


def _rendered(served: Served, tmp_path: Path, run: RunId, view: str) -> str:
    """The text a browser shows for one view of `run`, from the bundle this pair serves.

    A real browser against the real proxy, because every cheaper stand-in answers a
    different question: the served bytes say what was shipped, and only a rendered page
    says what an operator is shown.
    """
    driver = tmp_path / "render.js"
    driver.write_text(RENDER_DRIVER, encoding="utf-8")
    width, height = RENDER_VIEWPORT
    rendered = subprocess.run(
        [
            "bun",
            str(driver),
            str(REPO_ROOT),
            f"{served.base}/?run={urllib.parse.quote(run)}&view={view}",
            str(width),
            str(height),
            RENDER_SETTLED[view],
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        check=False,
    )
    assert rendered.returncode == 0, rendered.stderr
    return rendered.stdout


def test_the_adopted_bundle_renders_a_real_runs_root_in_a_browser(
    served_recorded: Served, tmp_path: Path
) -> None:
    """`just dag-ui` and `just telemetry-server`, opened the way an operator opens them.

    Everything else here reads bytes off the wire, which cannot tell a bundle that
    renders from one that throws on its first frame and leaves an empty root — a
    failure that serves 200s the whole way down. So one desktop viewport is driven
    through a real browser against a real recorded run, and what is asserted is the
    text on the page: the run's own name and goal, the counters the Overall view is
    read for, the liveness verdict the API reports for it, and, in the Graph view, the
    node with the outcome that failed it.

    **The verdict is read from the API rather than written down here.** A literal spelled
    into this file is a claim about one bundle release, and it goes stale silently — it
    fails only once something reinstalls, which is later than the bump that broke it.
    Asking the API what it says and requiring the page to show *that* fails when the view
    stops rendering the verdict and does not fail when the verdict is spelled differently.
    """
    overall = _rendered(served_recorded, tmp_path, RECORDED_RUN, "overall")

    assert RECORDED_RUN in overall, overall
    assert "Clear the three judged findings" in overall, overall
    for counter in ("STATUS", "NODES", "WALL TIME", "TURNS"):
        assert counter in overall, f"the Overall view rendered no {counter}: {overall}"

    status, body, _ = served_recorded.get("/api/v2/runs")
    assert status == 200, (status, body)
    reported = {run["run_id"]: run["state"] for run in json.loads(body)["runs"]}
    assert RECORDED_RUN in reported, (
        f"the read API lists no {RECORDED_RUN}, so this journey has no verdict to hold "
        f"the rendered page to: {sorted(reported)}"
    )
    assert reported[RECORDED_RUN] in overall, (
        f"the read API reports {RECORDED_RUN} as {reported[RECORDED_RUN]!r} and the "
        f"Overall view renders no such verdict. An operator is being shown a run whose "
        f"liveness the page does not say: {overall}"
    )

    graph = _rendered(served_recorded, tmp_path, RECORDED_RUN, "graph")

    assert "gate-fix-findings-2" in graph, graph
    assert "task-failed" in graph, graph


# llmlint: ignore[changed_behavior_has_e2e] no run on this host can carry a release event
def test_a_run_carrying_no_release_event_renders_no_release_row(
    served_recorded: Served, tmp_path: Path
) -> None:
    """The adopted view's release surface, on data that has nothing to put in it.

    `onepipeline-ui` 0.6.3 renders which release carried a landed node and what a held
    node awaits. None of the recorded runs served here carries a release event, so the
    correct rendering is no release row at all — and a bundle that drew one anyway,
    from a field it had misread, would be showing an operator something untrue about
    where their work is.

    **This is a statement about these runs, not about this host.** They are checked-in
    fixtures and can never grow a release event, so declaring a release target here
    would not move it. The gate that fires on *that* is
    `tests/e2e/test_release_adoption_in_force_e2e.py::test_nothing_on_this_host_declares_a_release_target`,
    which asks every registered identity rather than reading a frozen tree.
    """
    for view in ("overall", "graph"):
        rendered = _rendered(served_recorded, tmp_path, RECORDED_RUN, view)
        assert RELEASE_ON_THE_PAGE not in rendered.lower(), (
            f"the {view} view of {RECORDED_RUN} rendered {RELEASE_ON_THE_PAGE!r}, but "
            "that run carries no release event, so there is nothing for a release "
            f"surface to be about: {rendered}"
        )

    # And the layer under it, which a rendered page cannot distinguish: a view showing
    # no release row because the data has none looks exactly like one that dropped it.
    for run in (RECORDED_RUN, SETTLED_RUN, MERGED_RUN, REPORTED_RUN):
        status, body, _ = served_recorded.get(f"/api/v2/runs/{run}/timeline?scope=run")
        assert status == 200, body
        carrying = [span for span in json.loads(body)["spans"] if span.get("release") is not None]
        assert not carrying, (
            f"{run} serves {len(carrying)} span(s) carrying a release, so the premise "
            "above is gone and the rendered assertion is measuring the wrong thing"
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
# `tests/test_lost_turn_wire_contract.py` read theirs. The journeys either side of this one
# start both servers for real and drive them over HTTP and a browser.
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
