"""The adopted releases keep a spike's branch, time each change, and discard what was kept.

Three capabilities the approved-budgets plan (issue #1508) builds on are released
upstream and in force here only through `config/onepipeline.version` and
`config/onevcs.version`: a lifecycle node declaring `publish: "preserve"` settles `done`
as `preserved` with its branch on its origin and nothing landed; `onepipeline telemetry
RUN --changes --json` reports each change's cycle time, gate runs and landing; and
`onevcs reclaim --discard` deletes a branch kept on purpose that never landed. So this
drives each the way a run on this host meets it: the real `just orchestrate`, `just
results`, `just telemetry`, `just unpublished` and `just reclaim-branch`, the real driver
and the `onevcs` it links, over scratch identities whose origins are real bare
repositories.

The second run lands through a scratch `local-direct` identity whose merge path is a real
`pre-push` hook. It records its own start, end and the commit it was asked to push, and
sleeps a known interval, so the gate run the per-change view reports is held to a run
this journey timed independently rather than to a number the engine merely printed.

What is doubled is the paid provider and nothing above it: `tests/e2e/fake_backend.py`
for both sides of a dispatch, `tests/e2e/fake_codex.py` for the single-sided members, and
the worker's one decision — committing a file — made by real git where the dispatch runs,
keyed by `FAKE_BACKEND_RUN_ON_MARKER`.

llmlint: ignore-file[shell_test_tiers_stay_split] A pytest launch journey over the real
`just` recipes, in the `tests/e2e` tree beside every other launch journey, as the task
adopting these releases requires; re-homing launch journeys into a new Nx project is
enforcement configuration this change may not move.

llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] `xdist_group` selects an
xdist worker under this suite's `--dist loadgroup`, keeping the module's two launches on
one worker; it selects no test tier.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] The two launches take
about twenty seconds between them, in the `tests/e2e` tree whose Nx project owns every
launch journey; which project owns that tree is a property of the tree, not of this change.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterator, Mapping
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

import pytest
import short_state
from conftest import git
from fake_backend import PROMPT_LOG_ENV, RUN_ON_MARKER_ENV
from harness_indirections import established_indirections
from project_fixtures import helper, project_from_plan
from scratch_identity import Identity, seeded
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

FAKE_BACKEND = helper("fake_backend.py")
FAKE_CODEX = helper("fake_codex.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")
INDIRECTION_CALLER = "tests/e2e/test_budget_capabilities_adopted_e2e.py"

EXECUTION_ALIAS = "execution"
BASE = "main"

PRESERVE_RUN = "budget-preserve"
PRESERVE_NODE = "spike"
LANDING_RUN = "budget-landing"
LANDING_NODE = "service"

#: The phrase that tells the stand-in worker this is the turn that commits. A lifecycle
#: node whose branch ends level with its base settles `empty-branch`, preserved or not.
COMMIT_MARKER = "Record the measurement in MEASURED.md and commit it."
COMMIT_COMMANDS = [
    [
        "sh",
        "-c",
        "[ -f MEASURED.md ] || { echo measured > MEASURED.md && git add MEASURED.md "
        "&& git -c user.email=test@example.com -c user.name=ai-orchestrator-test "
        "commit -qm 'feat: record the measurement'; }",
    ]
]

#: How long the scratch gate sleeps between stamping its start and its end. Long enough
#: that a gate run the engine failed to time would not reach it by rounding.
GATE_SLEEP_SECONDS = 2

#: The scratch identity's `pre-push` gate. It appends one line per run: the millisecond
#: it started, the one it ended, and every local commit git handed it to push.
GATE_HOOK = """#!/usr/bin/env bash
set -euo pipefail
started=$(date +%s%3N)
pushed=$(awk '{{print $2}}' | tr '\\n' ' ')
sleep {sleep}
ended=$(date +%s%3N)
printf '{{"started_ms": %s, "ended_ms": %s, "pushed": "%s"}}\\n' \\
    "$started" "$ended" "$pushed" >> {log}
"""

#: A branch this journey puts on the preserve identity's origin itself, which the
#: discard must leave in place.
UNRELATED_BRANCH = "unrelated/keep-me"

#: `just unpublished`'s exit status when a row is counted, as `scripts/unpublished.sh
#: --print-surface` names it, and `onevcs reclaim`'s when the class is not permitted.
UNPUBLISHED_COUNTED = 7
RECLAIM_REFUSED = 4

LAUNCHER_ENVIRONMENT = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)


def _object(value: object, what: str) -> Mapping[str, object]:
    assert isinstance(value, dict), f"{what} is not a JSON object: {value!r}"
    return value


def _text(record: Mapping[str, object], name: str) -> str:
    value = record.get(name)
    assert isinstance(value, str), f"`{name}` is not a string in {record}"
    return value


def _optional_text(record: Mapping[str, object], name: str) -> str | None:
    value = record.get(name)
    assert value is None or isinstance(value, str), f"`{name}` is not a string in {record}"
    return value


def _number(value: object, what: str) -> float:
    assert isinstance(value, int | float) and not isinstance(value, bool), (
        f"{what} is not a number: {value!r}"
    )
    return float(value)


def _list(record: Mapping[str, object], name: str) -> list[object]:
    value = record.get(name)
    assert isinstance(value, list), f"`{name}` is not a list in {record}"
    return value


class NodeResult(NamedTuple):
    """One node of the run's `result.json`, the fields this journey reads of it."""

    id: str
    status: str
    outcome: str | None
    branch: str | None
    head: str | None
    remote: str | None

    @classmethod
    def parse(cls, value: object) -> NodeResult:
        entry = _object(value, "a result node")
        return cls(
            id=_text(entry, "id"),
            status=_text(entry, "status"),
            outcome=_optional_text(entry, "outcome"),
            branch=_optional_text(entry, "branch"),
            head=_optional_text(entry, "head"),
            remote=_optional_text(entry, "remote"),
        )


class GateRun(NamedTuple):
    """One entry of a change's `gate_runs`."""

    gate: str
    verdict: str
    started_at: str
    ended_at: str
    seconds: float

    @classmethod
    def parse(cls, value: object) -> GateRun:
        entry = _object(value, "a gate run")
        return cls(
            gate=_text(entry, "gate"),
            verdict=_text(entry, "verdict"),
            started_at=_text(entry, "started_at"),
            ended_at=_text(entry, "ended_at"),
            seconds=_number(entry.get("seconds"), "a gate run's seconds"),
        )


class Change(NamedTuple):
    """One entry of the per-change document, the fields a budget is read against."""

    node: str
    outcome: str
    dispatched_at: str
    landed_at: str | None
    landing: str | None
    cycle_seconds: float | None
    gate_runs: tuple[GateRun, ...]
    gate_seconds: float
    segments: dict[str, float]
    not_measured: tuple[str, ...]

    @classmethod
    def parse(cls, value: object) -> Change:
        entry = _object(value, "a change")
        cycle = entry.get("cycle_seconds")
        segments = _object(entry.get("segments"), "a change's segments")
        return cls(
            node=_text(entry, "node"),
            outcome=_text(entry, "outcome"),
            dispatched_at=_text(entry, "dispatched_at"),
            landed_at=_optional_text(entry, "landed_at"),
            landing=_optional_text(entry, "landing"),
            cycle_seconds=None if cycle is None else _number(cycle, "cycle_seconds"),
            gate_runs=tuple(GateRun.parse(run) for run in _list(entry, "gate_runs")),
            gate_seconds=_number(entry.get("gate_seconds"), "gate_seconds"),
            segments={name: _number(seconds, name) for name, seconds in segments.items()},
            not_measured=tuple(str(name) for name in _list(entry, "not_measured")),
        )


class HookRun(NamedTuple):
    """One line the scratch `pre-push` gate wrote about itself."""

    started_ms: int
    ended_ms: int
    pushed: tuple[str, ...]

    @classmethod
    def parse(cls, line: str) -> HookRun:
        entry = _object(json.loads(line), "a gate log line")
        return cls(
            started_ms=int(_number(entry.get("started_ms"), "started_ms")),
            ended_ms=int(_number(entry.get("ended_ms"), "ended_ms")),
            pushed=tuple(_text(entry, "pushed").split()),
        )


class UnpublishedRow(NamedTuple):
    """One row of `just unpublished --json`, the fields this journey reads of it."""

    branch: str
    counted: bool

    @classmethod
    def parse(cls, value: object) -> UnpublishedRow:
        entry = _object(value, "an unpublished row")
        return cls(branch=_text(entry, "branch"), counted=entry.get("counted") is True)


def _environment(
    root: Path, identity: Identity, session: str, oneharness_bin: str
) -> dict[str, str]:
    """Every root this run writes under is the journey's own, and the provider is doubled."""
    environment = dict(os.environ)
    for name in LAUNCHER_ENVIRONMENT:
        environment.pop(name, None)
    environment["CLAUDE_CODE_SESSION_ID"] = session
    environment.update(identity.environment)
    environment["ONEVCS_HOME"] = str(identity.home)
    environment["ONEPIPELINE_RUNS_DIR"] = str(root / "runs")
    environment["XDG_STATE_HOME"] = str(short_state.state_home(root))
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment.update(established_indirections(INDIRECTION_CALLER))
    environment[PROMPT_LOG_ENV] = str(root / "turns.jsonl")
    keyed = root / "commands.json"
    keyed.write_text(json.dumps({COMMIT_MARKER: COMMIT_COMMANDS}), encoding="utf-8")
    environment[RUN_ON_MARKER_ENV] = str(keyed)
    return environment


def _plan(root: Path, name: str, repo: Path, node: Mapping[str, str]) -> Path:
    """A one-node schema-3 lifecycle plan against a scratch identity."""
    plan = root / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "goal": {"text": "Put the adopted budget capabilities in force on this host"},
                "name": name,
                "tasks": [
                    {
                        **node,
                        "repo": str(repo),
                        "execution_checkout": EXECUTION_ALIAS,
                        "persona": "engineer",
                        "task": (
                            f"## What\n\n{COMMIT_MARKER}\n\n"
                            "## Why\n\nWhat the closeout does with the branch is the "
                            "subject; the work itself is not.\n\n"
                            "## Acceptance criteria\n\n- MEASURED.md is committed.\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return plan


def _just(
    *arguments: str, environment: Mapping[str, str], seconds: int = 120
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["just", *arguments],
        cwd=REPO_ROOT,
        env=dict(environment),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
    )


def _launched(name: str, plan: Path, environment: Mapping[str, str]) -> None:
    launch = _just(
        "orchestrate", project_from_plan(plan, name), environment=environment, seconds=300
    )
    assert launch.returncode == 0, f"the launch did not settle:\n{launch.stdout}\n{launch.stderr}"


class Settled(NamedTuple):
    """One node as the run reports it: its `result.json` entry and `just results`' text."""

    node: NodeResult
    results: str


def _settled(run: str, node: str, environment: Mapping[str, str]) -> Settled:
    """The engine's result document for one node, beside the operator's view of the run."""
    shown = _just("results", run, environment=environment)
    assert shown.returncode == 0, f"`just results {run}` failed:\n{shown.stderr}"
    result = Path(environment["ONEPIPELINE_RUNS_DIR"]) / run / "result.json"
    document = _object(json.loads(result.read_text(encoding="utf-8")), str(result))
    nodes = {entry.id: entry for entry in map(NodeResult.parse, _list(document, "nodes"))}
    assert node in nodes, f"{result} names no node {node!r}: {sorted(nodes)}"
    return Settled(node=nodes[node], results=shown.stdout)


def _changes(run: str, environment: Mapping[str, str]) -> tuple[Change, ...]:
    """`just telemetry RUN --changes --json`: the adopted engine's per-change document."""
    read = _just("telemetry", run, "--changes", "--json", environment=environment)
    assert read.returncode == 0, f"the per-change view failed:\n{read.stdout}\n{read.stderr}"
    lines = read.stdout.strip().splitlines()
    assert len(lines) == 1, (
        f"the per-change view printed {len(lines)} lines, not one: {read.stdout}"
    )
    document = _object(json.loads(lines[0]), "the per-change document")
    assert document.get("run_id") == run, document
    return tuple(Change.parse(change) for change in _list(document, "changes"))


def _millis(stamp: str) -> int:
    """A `YYYY-MM-DDThh:mm:ss.sssZ` stamp as epoch milliseconds."""
    return round(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1000)


def _ms(seconds: float) -> int:
    """Seconds the document spells as a whole number of milliseconds, as that integer."""
    return round(seconds * 1000)


def _head(repository: Path, branch: str) -> str | None:
    """The commit ``branch`` stands at in ``repository``, or ``None`` where it has none."""
    shown = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repository,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    return shown.stdout.strip() or None


def _branches(checkout: Path) -> set[str]:
    listed = git("for-each-ref", "--format=%(refname:short)", "refs/heads", cwd=checkout)
    return set(listed.split())


class Preserved(NamedTuple):
    """The preserve run, and everything this journey read off it before and after discarding."""

    seed_commit: str
    settled: Settled
    origin_head: str | None
    changes: tuple[Change, ...]
    unpublished: subprocess.CompletedProcess[str]
    refused: subprocess.CompletedProcess[str]
    discarded: subprocess.CompletedProcess[str]
    origin_after_discard: set[str]
    publication_after_discard: set[str]
    execution_after_discard: set[str]
    origin_main_after: str


@pytest.fixture(scope="module")
def preserved(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Iterator[Preserved]:
    """Launch a `publish: "preserve"` node, read it, then discard the branch it kept.

    Every read is taken here, in this order, because the discard at the end removes what
    the earlier reads are about.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    root = tmp_path_factory.mktemp("budget-preserve")
    identity = seeded(root, execution=EXECUTION_ALIAS)
    origin = root / "origin.git"
    seed_commit = git("rev-parse", BASE, cwd=origin).strip()
    git("branch", UNRELATED_BRANCH, cwd=identity.publication)
    git("push", "-q", "origin", UNRELATED_BRANCH, cwd=identity.publication)
    environment = _environment(root, identity, "e2e-budget-preserve", oneharness_bin)

    node = {"id": PRESERVE_NODE, "title": "feat: keep the measurement", "publish": "preserve"}
    _launched(PRESERVE_RUN, _plan(root, PRESERVE_RUN, identity.publication, node), environment)
    settled = _settled(PRESERVE_RUN, PRESERVE_NODE, environment)
    changes = _changes(PRESERVE_RUN, environment)
    unpublished = _just("unpublished", "--json", "--no-disk", environment=environment)

    branch = str(settled.node.branch)
    origin_head = _head(origin, branch)
    publication = str(identity.publication)
    refused = _just("reclaim-branch", branch, "--repo", publication, environment=environment)
    discarded = _just(
        "reclaim-branch", branch, "--repo", publication, "--discard", environment=environment
    )
    yield Preserved(
        seed_commit=seed_commit,
        settled=settled,
        origin_head=origin_head,
        changes=changes,
        unpublished=unpublished,
        refused=refused,
        discarded=discarded,
        origin_after_discard=_branches(origin),
        publication_after_discard=_branches(identity.publication),
        execution_after_discard=_branches(identity.execution),
        origin_main_after=git("rev-parse", BASE, cwd=origin).strip(),
    )


class Landed(NamedTuple):
    """The landing run, the scratch gate's own record of its runs, and the base it moved."""

    settled: Settled
    changes: tuple[Change, ...]
    hook_runs: tuple[HookRun, ...]
    seed_commit: str
    origin_main: str


@pytest.fixture(scope="module")
def landed(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Iterator[Landed]:
    """Launch a node that lands through a real, timed `pre-push` gate, and read it."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    root = tmp_path_factory.mktemp("budget-landing")
    identity = seeded(root, execution=EXECUTION_ALIAS)
    origin = root / "origin.git"
    seed_commit = git("rev-parse", BASE, cwd=origin).strip()
    gate_log = root / "gate-runs.jsonl"
    hooks = root / "hooks"
    hooks.mkdir()
    hook = hooks / "pre-push"
    hook.write_text(GATE_HOOK.format(sleep=GATE_SLEEP_SECONDS, log=gate_log), encoding="utf-8")
    hook.chmod(0o755)
    # The execution checkout is where an operator's hooks live; `onevcs` carries them
    # into the clone a session publishes from.
    git("config", "core.hooksPath", str(hooks), cwd=identity.execution)
    environment = _environment(root, identity, "e2e-budget-landing", oneharness_bin)

    node = {"id": LANDING_NODE, "title": "feat: land the measured change"}
    _launched(LANDING_RUN, _plan(root, LANDING_RUN, identity.publication, node), environment)
    logged = gate_log.read_text(encoding="utf-8").splitlines() if gate_log.is_file() else []
    yield Landed(
        settled=_settled(LANDING_RUN, LANDING_NODE, environment),
        changes=_changes(LANDING_RUN, environment),
        hook_runs=tuple(HookRun.parse(line) for line in logged),
        seed_commit=seed_commit,
        origin_main=git("rev-parse", BASE, cwd=origin).strip(),
    )


@pytest.mark.xdist_group("budget-capabilities")
def test_a_preserve_node_settles_preserved_with_its_branch_on_the_origin(
    preserved: Preserved,
) -> None:
    """`done`/`preserved`, its branch on the origin at the head it names, the base unmoved."""
    node = preserved.settled.node
    assert (node.status, node.outcome, node.remote) == ("done", "preserved", "pushed"), node
    assert node.branch is not None and node.head is not None, node
    assert preserved.origin_head == node.head, (
        f"the origin holds {node.branch!r} at {preserved.origin_head!r}, "
        f"not at the settled {node.head}"
    )
    assert preserved.origin_main_after == preserved.seed_commit, (
        "the preserve run moved the base, which a kept branch must never do"
    )


@pytest.mark.xdist_group("budget-capabilities")
def test_the_results_view_reads_the_kept_branch(preserved: Preserved) -> None:
    """What an operator reads: the outcome word, and where the branch was kept."""
    node, results = preserved.settled.node, preserved.settled.results
    assert "done (preserved)" in results, results
    assert f"kept on {node.branch} at {node.head} (pushed)" in results, results


@pytest.mark.xdist_group("budget-capabilities")
def test_the_per_change_view_reports_the_kept_change_as_unlanded(preserved: Preserved) -> None:
    """One change naming the node, dispatched and never landed."""
    assert len(preserved.changes) == 1, preserved.changes
    (change,) = preserved.changes
    assert (change.node, change.outcome) == (PRESERVE_NODE, "preserved"), change
    assert _millis(change.dispatched_at) > 0, change
    assert (change.landed_at, change.landing, change.cycle_seconds) == (None, None, None), change


@pytest.mark.xdist_group("budget-capabilities")
def test_unpublished_counts_the_kept_branch(preserved: Preserved) -> None:
    """A kept branch is owed until it is acknowledged or discarded, so the view counts it."""
    listed = preserved.unpublished
    assert listed.returncode == UNPUBLISHED_COUNTED, (
        f"`just unpublished` exited {listed.returncode}:\n{listed.stdout}\n{listed.stderr}"
    )
    rows = [UnpublishedRow.parse(row) for row in json.loads(listed.stdout)]
    kept = [row for row in rows if row.branch == preserved.settled.node.branch]
    assert len(kept) == 1 and kept[0].counted, f"the kept branch is not counted: {rows}"


@pytest.mark.xdist_group("budget-capabilities")
def test_reclaim_refuses_a_kept_branch_without_discard(preserved: Preserved) -> None:
    """Nothing superseded the kept branch, so a plain reclaim refuses and deletes nothing."""
    assert preserved.refused.returncode == RECLAIM_REFUSED, (
        f"a plain reclaim exited {preserved.refused.returncode}:\n"
        f"{preserved.refused.stdout}\n{preserved.refused.stderr}"
    )


@pytest.mark.xdist_group("budget-capabilities")
def test_discard_removes_the_kept_branch_and_leaves_an_unrelated_one(preserved: Preserved) -> None:
    """Gone from the origin and every checkout holding it; the unrelated branch stays."""
    discarded = preserved.discarded
    assert discarded.returncode == 0, f"the discard failed:\n{discarded.stdout}\n{discarded.stderr}"
    branch = preserved.settled.node.branch
    for place, branches in (
        ("origin", preserved.origin_after_discard),
        ("publication checkout", preserved.publication_after_discard),
        ("execution checkout", preserved.execution_after_discard),
    ):
        assert branch not in branches, f"the {place} still holds {branch!r}: {branches}"
    assert UNRELATED_BRANCH in preserved.origin_after_discard, preserved.origin_after_discard
    assert UNRELATED_BRANCH in preserved.publication_after_discard


@pytest.mark.xdist_group("budget-capabilities")
def test_a_normal_node_lands_through_its_gate(landed: Landed) -> None:
    """`done`, the base moved to the landing, and the one gate run pushed exactly that."""
    node = landed.settled.node
    assert (node.status, node.outcome) == ("done", "merged"), node
    assert landed.origin_main != landed.seed_commit, "the base did not move"
    assert len(landed.hook_runs) == 1, f"the gate ran {len(landed.hook_runs)} times"
    assert landed.hook_runs[0].pushed == (landed.origin_main,), (
        f"the recorded gate run pushed {landed.hook_runs[0].pushed}, not the landing "
        f"{landed.origin_main}"
    )


@pytest.mark.xdist_group("budget-capabilities")
def test_the_per_change_view_times_the_gate_and_the_landing(landed: Landed) -> None:
    """The change's gate run brackets the hook's own, and its cycle adds up exactly."""
    assert len(landed.changes) == 1, landed.changes
    (change,) = landed.changes
    assert change.node == LANDING_NODE, change
    assert change.landed_at is not None, change
    assert change.landing == landed.origin_main, change

    assert [run.gate for run in change.gate_runs] == ["pre-push"], change.gate_runs
    (run,) = change.gate_runs
    (hook,) = landed.hook_runs
    assert run.verdict == "passed", run
    assert _millis(run.started_at) <= hook.started_ms, (run, hook)
    assert _millis(run.ended_at) >= hook.ended_ms, (run, hook)
    assert run.seconds >= GATE_SLEEP_SECONDS, run

    gate_ms = _ms(change.gate_seconds)
    assert gate_ms == sum(_ms(entry.seconds) for entry in change.gate_runs), change
    assert gate_ms == _ms(change.segments["gate"]), change
    assert change.cycle_seconds is not None, change
    cycle_ms = _ms(change.cycle_seconds)
    assert cycle_ms == _millis(change.landed_at) - _millis(change.dispatched_at), change
    assert "gate" not in change.not_measured, change
    assert sum(_ms(seconds) for seconds in change.segments.values()) == cycle_ms, change
