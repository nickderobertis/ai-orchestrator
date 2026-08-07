"""Dispatch/plan tests, including small real subprocess liveness journeys."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple

import pytest
from onejudge_sdk import RunResult
from process_tree import await_reaped, await_recorded_pid, is_running, write_orphaning_tree

from orchestrator import BASE_CONFIG, PERSONA_DIR, REPO_ROOT, watchdog
from orchestrator import dispatch as dispatch_module
from orchestrator.dispatch import (
    AGENT_ONEHARNESS_BIN,
    DEFAULT_DISPATCH_STALL_TIMEOUT,
    DEFAULT_WORKER_HEARTBEAT_TIMEOUT,
    NO_AGENT_PROGRESS_OUTCOME,
    REPORTED_BLOCKER_OUTCOME,
    REPORTED_BLOCKER_PREFIX,
    DispatchError,
    Report,
    _agent_run_context,
    _build_report,
    _configured_turn_cap,
    _file_progress,
    _read_watchdog_pid,
    agent_exit_status,
    agent_failure_reason,
    classify_provider_failure,
    dispatch,
    group_holds_stamped_process,
    incomplete_detail,
    owned_tree,
    recordable_provider_failure,
    run_onejudge,
)
from orchestrator.graph import DEFAULT_ROUND_BUDGET
from orchestrator.graph import main as graph_main
from orchestrator.harnesses import JUDGE_HARNESS_ENV
from orchestrator.labels import parse_labels
from orchestrator.plan import PlanNode, PlanResult, TaskResult, _render
from orchestrator.plan import main as plan_main
from orchestrator.scratch import AGENT_STATUS_DIR_ENV
from orchestrator.watchdog import (
    OWN_PROCESS_GROUP_FLAG,
    TERMINATION_GRACE,
    ProcessId,
    ProcessIdentity,
    _parse_stat,
    identify,
    process_activity,
    process_group_of,
    terminate_identified_processes,
    terminate_process_group,
    terminate_processes,
    terminate_proven_process_group,
    terminate_tree,
)
from orchestrator.watchdog import main as watchdog_main


def test_build_report_maps_incomplete_sdk_result() -> None:
    result = RunResult(
        exit_code=1,
        stderr="some stderr",
        raw={"schema_version": 4, "transcript": {"messages": []}, "stopped_early": False},
    )
    report = _build_report("p", result)
    assert report.completed is False
    assert report.assistant_turns == 0
    assert report.verdicts == []
    assert report.usage == {}
    assert report.telemetry is None


def test_build_report_preserves_usage_assessment_and_real_telemetry_field(monkeypatch) -> None:
    monkeypatch.setattr(
        RunResult,
        "telemetry",
        property(lambda result: result.raw.get("telemetry")),
        raising=False,
    )
    result = RunResult(
        exit_code=0,
        stderr="",
        raw={
            "schema_version": 5,
            "transcript": {"messages": []},
            "usage": {"input_tokens": 4, "vendor": "kept"},
            "assessment": "ordinary follow-up",
            "telemetry": {"wall_ms": 7},
        },
    )
    report = _build_report("p", result)
    assert report.usage == {"input_tokens": 4, "vendor": "kept"}
    assert report.assessment == "ordinary follow-up"
    assert report.telemetry == {"wall_ms": 7}


def _sdk_report(
    *, completed: bool, contents: list[str], verdicts: list[dict[str, object]] | None = None
) -> RunResult:
    """An SDK-validated report whose assistant turns carry the given content."""
    return RunResult(
        exit_code=0 if completed else 1,
        stderr="",
        raw={
            "schema_version": 4,
            "transcript": {
                "messages": [
                    message
                    for content in contents
                    for message in (
                        {"role": "user", "content": "go"},
                        {"role": "assistant", "content": content},
                    )
                ]
            },
            "stopped_early": not completed,
            "verdicts": verdicts or [],
        },
    )


def test_a_budget_spent_without_agent_progress_is_not_the_agent_hitting_the_cap() -> None:
    """Content, not turn count: onejudge records an empty answer as a spent turn.

    The real journey is tests/e2e/test_tracked_graph_e2e.py, which drives a provider
    that answers nothing through `just run-plan`. This pins the boundary the report
    is read at, including the blank answer a turn count cannot distinguish.
    """
    silent = _build_report(
        "engineer", _sdk_report(completed=False, contents=["", "  ", ""]), max_turns=3
    )

    assert silent.outcome == NO_AGENT_PROGRESS_OUTCOME
    # It did spend its whole cap, and saying so is exactly the misreading: this stop
    # answers ahead of the turn accounting, because a bigger cap is no answer to it.
    assert silent.assistant_turns == silent.max_turns
    assert "without the agent producing anything" in incomplete_detail(silent)
    assert "turn cap" not in incomplete_detail(silent)

    worked = _build_report(
        "engineer", _sdk_report(completed=False, contents=["", "a real answer"]), max_turns=3
    )

    assert worked.outcome is None
    # A run that produced something falls through to the ordinary accounting, which
    # reports how far it got against the cap it was given.
    assert incomplete_detail(worked) == "did not complete after 2 turns, short of its 3-turn cap"

    finished = _build_report("engineer", _sdk_report(completed=True, contents=["done"]))

    assert finished.completed and finished.outcome is None


def test_a_supervisor_confirmed_terminal_blocker_is_a_distinct_failed_outcome() -> None:
    """The failed final verdict is the trust boundary, not repeated worker prose."""
    result = _sdk_report(
        completed=False,
        contents=["Terminal blocker: deployment approval is unavailable."],
        verdicts=[
            {"verdict": {"value": True, "reason": "an earlier criterion passed"}},
            {
                "verdict": {
                    "value": False,
                    "reason": "Terminal blocker reported: deployment approval is unavailable",
                }
            },
        ],
    )

    report = _build_report("engineer", result, max_turns=12)

    assert report.outcome == REPORTED_BLOCKER_OUTCOME
    assert report.outcome_detail == "deployment approval is unavailable"
    assert incomplete_detail(report) == (
        "stopped on a reported blocker: deployment approval is unavailable"
    )


def test_engineer_supervisor_blocker_prefix_cannot_drift_from_dispatch_parser() -> None:
    persona = (PERSONA_DIR / "engineer.yaml").read_text(encoding="utf-8").lower()

    assert f"`{REPORTED_BLOCKER_PREFIX}`" in persona


@pytest.mark.parametrize(
    "verdicts",
    [
        [{"verdict": {"value": False, "reason": "ordinary unmet criterion"}}],
        [{"verdict": {"value": False, "reason": 42}}],
        [{"verdict": "malformed"}],
        [{"verdict": {"value": True, "reason": "terminal blocker reported: not blocked"}}],
    ],
)
def test_repeated_work_is_not_a_reported_blocker_without_the_failed_verdict_contract(
    verdicts: list[dict[str, object]],
) -> None:
    report = _build_report(
        "engineer",
        _sdk_report(
            completed=False, contents=["same progress", "same progress"], verdicts=verdicts
        ),
    )

    assert report.outcome is None


def test_a_worker_that_died_keeps_its_own_name_over_the_no_progress_one() -> None:
    """`worker-died` is the more specific diagnosis and is reported ahead of it."""
    died = Report("engineer", 1, False, True, 0, [], {}, {}, "", outcome="worker-died")

    assert incomplete_detail(died) == "worker-died"


def test_build_report_counts_assistant_turns() -> None:
    result = RunResult(
        exit_code=0,
        stderr="",
        raw={
            "schema_version": 4,
            "transcript": {
                "messages": [
                    {"role": "user", "content": "t"},
                    {"role": "assistant", "content": "a"},
                    {"role": "user", "content": "u"},
                    {"role": "assistant", "content": "b"},
                ]
            },
            "stopped_early": False,
            "verdicts": [],
            "usage": {"output_tokens": 3},
        },
    )
    report = _build_report("p", result)
    assert report.completed is True
    assert report.assistant_turns == 2
    assert report.usage == {"output_tokens": 3}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("- Investigate the adjacent bug. ", "- Investigate the adjacent bug."),
        (None, None),
        ("  ", None),
    ],
)
def test_build_report_parses_optional_assessment(value, expected) -> None:
    result = RunResult(
        exit_code=0,
        stderr="",
        raw={
            "schema_version": 4,
            "transcript": {"messages": []},
            "stopped_early": False,
            "assessment": value,
        },
    )
    report = _build_report("p", result)
    assert report.assessment == expected


def test_report_summary_lists_verdicts() -> None:
    report = Report(
        persona="reviewer",
        exit_code=0,
        completed=True,
        stopped_early=False,
        assistant_turns=1,
        verdicts=[{"kind": "boolean", "criterion": "done", "verdict": {"value": True}}],
        usage={},
        raw={},
        stderr="",
    )
    text = report.summary()
    assert "reviewer: completed" in text
    assert "done: True" in text


def test_report_summary_lists_follow_ups() -> None:
    report = Report("p", 0, True, False, 1, [], {}, {}, "", "- Add a missing test.")
    assert "follow-ups: - Add a missing test." in report.summary()


def test_one_node_plan_reports_an_unknown_persona_against_its_node(tmp_path, capsys) -> None:
    """A single dispatch is a one-node plan, so its failure lands on that node.

    The removed `just dispatch` printed this to stderr and exited 2. The plan path
    keeps it addressable instead: the run fails, and the node that could not be
    prepared names the persona and how to create it.
    """
    plan = tmp_path / "unknown-persona.plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 6,
                "tasks": [{"id": "solo", "persona": "no-such-persona", "task": "do it"}],
            }
        ),
        encoding="utf-8",
    )

    rc = graph_main([str(plan), "--no-record", "--base", str(BASE_CONFIG), "--format", "json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["results"]["solo"]["status"] == "failed"
    assert "unknown persona 'no-such-persona'" in payload["results"]["solo"]["error"]
    assert "just new-persona no-such-persona" in payload["results"]["solo"]["error"]


def test_plan_main_bad_plan_exit_2(tmp_path, capsys) -> None:
    rc = plan_main([str(tmp_path / "missing.json")])
    assert rc == 2
    assert "run-plan:" in capsys.readouterr().err


def test_render_json_and_human() -> None:
    done = TaskResult(
        "a",
        "done",
        report=Report("p", 0, True, False, 2, [], {"output_tokens": 1}, {}, ""),
    )
    skipped = TaskResult("b", "skipped", report=None, error="a dependency did not complete")
    result = PlanResult(results={"a": done, "b": skipped}, started_order=["a"])

    human = _render(result, "human")
    assert "a: done" in human and "b: skipped" in human

    import json

    payload = json.loads(_render(result, "json"))
    assert payload["ok"] is False
    assert payload["results"]["a"]["completed"] is True
    assert payload["results"]["b"]["exit_code"] is None


def test_plan_node_defaults() -> None:
    node = PlanNode("a", "planner", "do it")
    assert node.deps == []
    assert node.session is None


def test_agent_run_context_defaults() -> None:
    cfg: dict = {"provider": {"kind": "oneharness"}}
    run_cwd, env = _agent_run_context(cfg, cwd="/repo", project_dir=None, oneharness_mode=None)
    assert run_cwd == "/repo"
    assert env == {}


def test_omitted_dispatch_sessions_are_unique_and_explicit_sessions_are_preserved(
    monkeypatch,
) -> None:
    observed: list[str] = []

    def record(config, task, **kwargs):
        observed.append(config["session"])
        return Report("engineer", 0, True, False, 1, [], {}, {}, "")

    monkeypatch.setattr("orchestrator.dispatch.run_onejudge", record)

    dispatch("engineer", "first")
    dispatch("engineer", "second")
    dispatch("engineer", "resume", session="operator-session")

    assert observed[0] != observed[1]
    assert all(name.startswith("dispatch-engineer-") for name in observed[:2])
    assert observed[2] == "operator-session"


def test_agent_run_context_forwards_mode() -> None:
    cfg: dict = {"provider": {}}
    _, env = _agent_run_context(cfg, cwd="/repo", project_dir=None, oneharness_mode="bypass")
    assert env["ONEHARNESS_MODE"] == "bypass"
    assert env["LLMLINT_ONEHARNESS_BIN"] == str(REPO_ROOT / "scripts/llmlint-oneharness.sh")


def test_agent_run_context_omits_llmlint_wrapper_for_foreign_repository() -> None:
    cfg: dict = {"provider": {}}
    _, env = _agent_run_context(
        cfg,
        cwd="/repo",
        project_dir="/work/foreign",
        oneharness_mode="bypass",
        use_llmlint_wrapper=False,
    )
    assert env == {"ONEHARNESS_MODE": "bypass"}


def test_agent_run_context_keeps_llmlint_sandbox_for_non_bypass_mode() -> None:
    cfg: dict = {"provider": {}}
    _, env = _agent_run_context(cfg, cwd="/repo", project_dir=None, oneharness_mode="auto")
    assert env == {"ONEHARNESS_MODE": "auto"}


def test_agent_run_context_project_dir_absolutizes_judge_config() -> None:
    cfg: dict = {"provider": {"kind": "oneharness", "judge_config": "oneharness.judge.toml"}}
    run_cwd, env = _agent_run_context(
        cfg, cwd="/repo", project_dir="/work/target", oneharness_mode="bypass"
    )
    assert run_cwd == "/work/target"
    assert cfg["provider"]["bin"] == str(AGENT_ONEHARNESS_BIN)
    assert env["ONEHARNESS_MODE"] == "bypass"
    assert env["LLMLINT_ONEHARNESS_BIN"] == str(REPO_ROOT / "scripts/llmlint-oneharness.sh")
    assert cfg["provider"]["judge_config"] == str((REPO_ROOT / "oneharness.judge.toml").resolve())


def test_agent_run_context_keeps_absolute_judge_config() -> None:
    cfg: dict = {"provider": {"judge_config": "/abs/oneharness.judge.toml"}}
    _agent_run_context(cfg, cwd="/repo", project_dir="/work", oneharness_mode=None)
    assert cfg["provider"]["judge_config"] == "/abs/oneharness.judge.toml"


def test_agent_run_context_pins_the_wrapper_for_a_side_selection_without_a_project_dir() -> None:
    """Only that wrapper resolves a side, so a selection has to pin it.

    Without a `--project-dir` the provider bin stays whatever the config named, and
    oneharness discovers the repo config by walking up from the run cwd — a path
    that never reads either per-side variable. A selection would be silently
    ignored, which is the one outcome this seam exists to prevent.
    """
    cfg: dict = {"provider": {"kind": "oneharness", "judge_config": "oneharness.judge.toml"}}
    run_cwd, env = _agent_run_context(
        cfg,
        cwd="/repo",
        project_dir=None,
        oneharness_mode=None,
        judge_harness="claude-code:primary",
    )
    assert run_cwd == "/repo"
    assert cfg["provider"]["bin"] == str(AGENT_ONEHARNESS_BIN)
    assert cfg["provider"]["judge_config"] == str((REPO_ROOT / "oneharness.judge.toml").resolve())
    assert env == {JUDGE_HARNESS_ENV: "claude-code:primary"}


def test_agent_run_context_pins_split_skill_harness() -> None:
    cfg: dict = {"provider": {"kind": "split", "skill": {"kind": "oneharness"}}}
    _agent_run_context(cfg, cwd="/repo", project_dir="/work", oneharness_mode=None)
    assert cfg["provider"]["skill"]["bin"] == str(AGENT_ONEHARNESS_BIN)


def test_run_onejudge_missing_binary_raises() -> None:
    with pytest.raises(DispatchError, match="not found"):
        run_onejudge({}, "t", onejudge_bin="onejudge-does-not-exist-xyz")


@pytest.mark.parametrize(
    ("configured_timeout", "expected_timeout"),
    [(None, "10800"), ("73", "73")],
)
def test_run_onejudge_sets_per_turn_timeout(
    tmp_path, monkeypatch, configured_timeout: str | None, expected_timeout: str
) -> None:
    onejudge = tmp_path / "onejudge"
    onejudge.write_text(
        '#!/bin/sh\nif [ "$1" = "--version" ]; then echo "onejudge 0.3.4"; exit 0; fi\n'
        "printf '"
        '{"schema_version":4,"transcript":{"messages":[]},"stopped_early":false,'
        '"usage":{"oneharness_timeout":"%s"}}'
        '\' "$ONEHARNESS_TIMEOUT"\n',
        encoding="utf-8",
    )
    onejudge.chmod(0o700)
    if configured_timeout is None:
        monkeypatch.delenv("ONEHARNESS_TIMEOUT", raising=False)
    else:
        monkeypatch.setenv("ONEHARNESS_TIMEOUT", configured_timeout)
    report = run_onejudge({}, "task", onejudge_bin=os.fspath(onejudge))

    assert report.raw is not None
    assert report.usage["oneharness_timeout"] == expected_timeout
    assert report.raw["provenance"] == {
        "provider_kind": "oneharness",
        "onejudge": {"path": str(onejudge), "version": "0.3.4"},
    }


def test_run_onejudge_rejects_shadowed_version_before_dispatch(tmp_path: Path) -> None:
    onejudge = tmp_path / "onejudge"
    onejudge.write_text(
        "#!/bin/sh\nprintf 'onejudge 0.3.0\\n'\n",
        encoding="utf-8",
    )
    onejudge.chmod(0o700)

    with pytest.raises(DispatchError, match=r"expected 'onejudge 0.3.4'.*onejudge"):
        run_onejudge({}, "task", onejudge_bin=os.fspath(onejudge))


@pytest.mark.parametrize("bad_timeout", ["", "abc", "12.5", "0", "-5"])
def test_run_onejudge_rejects_invalid_timeout(monkeypatch, bad_timeout: str) -> None:
    # ONEHARNESS_TIMEOUT crosses in from the environment; a non-positive-integer value
    # must fail loudly at the boundary rather than reach oneharness.
    monkeypatch.setenv("ONEHARNESS_TIMEOUT", bad_timeout)
    with pytest.raises(DispatchError, match="ONEHARNESS_TIMEOUT must be a positive integer"):
        run_onejudge({}, "task")


@pytest.mark.parametrize("bad_timeout", ["", "never", "nan", "inf", "0", "-1"])
def test_run_onejudge_rejects_invalid_stall_timeout(monkeypatch, bad_timeout: str) -> None:
    monkeypatch.setenv("ORCHESTRATOR_DISPATCH_STALL_TIMEOUT", bad_timeout)
    with pytest.raises(DispatchError, match="DISPATCH_STALL_TIMEOUT must be a positive number"):
        run_onejudge({}, "task")


def test_run_onejudge_surfaces_a_quiet_wedged_process_tree(tmp_path) -> None:
    onejudge = tmp_path / "onejudge"
    onejudge.write_text(
        '#!/bin/sh\n[ "$1" = "--version" ] && { echo "onejudge 0.3.4"; exit; }\nsleep 30\n',
        encoding="utf-8",
    )
    onejudge.chmod(0o700)

    with pytest.raises(DispatchError, match="dispatch stalled for 0.3s.*terminated"):
        run_onejudge(
            {},
            "task",
            onejudge_bin=os.fspath(onejudge),
            cwd=tmp_path,
            env={"ORCHESTRATOR_DISPATCH_STALL_TIMEOUT": "0.3"},
        )


def test_run_onejudge_allows_slow_dispatch_with_real_io_progress(tmp_path) -> None:
    onejudge = tmp_path / "onejudge"
    onejudge.write_text(
        '#!/bin/sh\nif [ "$1" = "--version" ]; then echo "onejudge 0.3.4"; exit 0; fi\n'
        "i=0\n"
        "while [ $i -lt 8 ]; do printf progress >&2; sleep 0.08; i=$((i + 1)); done\n"
        'printf \'%s\\n\' \'{"schema_version":4,"transcript":{"messages":[]},'
        '"stopped_early":false}\'\n',
        encoding="utf-8",
    )
    onejudge.chmod(0o700)

    report = run_onejudge(
        {},
        "task",
        onejudge_bin=os.fspath(onejudge),
        cwd=tmp_path,
        env={"ORCHESTRATOR_DISPATCH_STALL_TIMEOUT": "0.2"},
    )

    assert report.completed is True


#: A dispatch whose process tree churns the way a real one does — a run's `bunx nx`,
#: its xdist workers, its git children all appear and exit while the watcher samples —
#: and which then leaves one worker behind in a session of its own, so that when the
#: tree exits nothing above the survivor is left to walk down from. Both halves are
#: recorded by pid, because the whole question at teardown is which of them may be
#: signalled.
_CHURNING_ONEJUDGE = """\
import json
import os
import subprocess
import sys

if "--version" in sys.argv:
    print("onejudge 0.3.4")
    raise SystemExit(0)

churned = []
for _ in range(3):
    # Long enough to be sampled by a watcher polling four times a second, and waited
    # for here, so every one of these pids has been collected before the report below.
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.4)"])
    churned.append(child.pid)
    child.wait()
    with open(os.environ["CHURN_RECORD"], "w") as record:
        record.write("\\n".join(str(pid) for pid in churned))

orphan = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(60)"],
    start_new_session=True,
    # Holding this process's pipes would keep the report unread and turn a
    # completed dispatch into a worker death; a real leaked worker rarely does.
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
with open(os.environ["ORPHAN_RECORD"], "w") as record:
    record.write(str(orphan.pid))
with open(os.environ["STATUS_RECORD"], "w") as record:
    record.write(os.environ["ORCHESTRATOR_AGENT_STATUS_DIR"])

print(json.dumps({"schema_version": 4, "transcript": {"messages": []}, "stopped_early": False}))
"""


def _pids(identities: tuple[ProcessIdentity, ...]) -> set[ProcessId]:
    """The numbers behind a set of proven identities, for comparison in assertions."""
    return {identity.pid for identity in identities}


def _group_members(group_id: ProcessId) -> set[ProcessId]:
    """Every live process procfs currently places in ``group_id``.

    What a `killpg` on that number would reach, read independently of whatever
    partition the code under test computed for itself.
    """
    return {
        pid
        for entry in Path("/proc").iterdir()
        if entry.name.isdigit()
        for pid in (ProcessId(int(entry.name)),)
        if process_group_of(pid) == group_id
    }


class TeardownOperation(StrEnum):
    """The operations a teardown can reach for, named once.

    `str`-valued because two of them are also the attribute this trace patches on
    `orchestrator.dispatch`, and because the assertions below compare whole steps
    against plain tuples.
    """

    #: The one step that signals a set of proven identities rather than a number.
    EXACT_SET = "terminate_identified_processes"
    #: The one broad handle teardown still uses, and only while it is proven.
    PROCESS_GROUP = "terminate_proven_process_group"
    #: Neither of these belongs on this path any more; recording them is the point.
    TREE = "terminate_tree"
    UNPROVEN_GROUP = "terminate_process_group"


class TeardownStep(NamedTuple):
    """One recorded operation: what ran, on which handle, and what proved it."""

    operation: TeardownOperation
    handle: ProcessId | None
    evidence_was_live: bool


@dataclass
class TeardownTrace:
    """Everything one teardown did, in order, with the evidence behind each step."""

    operations: list[TeardownStep] = field(default_factory=list)
    #: Every pid teardown selected for the exact-pid phase. This is the partition
    #: decision itself, which is what has to be disjoint from the group's half.
    pid_phase_selection: set[ProcessId] = field(default_factory=set)
    #: Those of them that had already exited when that step reached them.
    already_exited: list[ProcessId] = field(default_factory=list)
    #: The identities that phase escalated to `SIGKILL` — its *second* signal, which
    #: it sends only to a process still answering to the identity it was proven under.
    escalated: set[ProcessId] = field(default_factory=set)
    #: Every pid a broad handle actually covered at the instant it was signalled —
    #: real process-group membership, not the parentage tree, because a descendant
    #: that left for a session of its own is under the root and *not* in its group.
    broad_targets: set[ProcessId] = field(default_factory=set)

    @property
    def names(self) -> list[TeardownOperation]:
        return [step.operation for step in self.operations]

    @property
    def broad(self) -> list[TeardownStep]:
        """Every step that signals through a number rather than a set of pids."""
        return [step for step in self.operations if step.operation != TeardownOperation.EXACT_SET]

    def assert_one_mechanism_per_process_in_the_right_order(self) -> None:
        """The whole teardown contract, asserted the same way wherever it runs.

        Three claims, and they are one claim from three sides. Every broad operation
        ran while a stamped process still held its number — that is the proof being
        live. None ran after the exact-pid step, which is what can destroy that proof
        by killing the members whose existence reserves the number. And no pid was
        signalled by both mechanisms: a process the group terminates is dead when that
        operation returns, so sending its number a second signal a grace period later
        is sending it to whatever holds that number by then.

        An implementation that signalled the proven processes first fails the second
        claim at once and the first as soon as the kill lands; one that passed the
        group's own members on to the pid phase fails the third.
        """
        for step in self.broad:
            assert step.evidence_was_live, (
                f"{step.operation} on {step.handle} ran with nothing stamped holding it"
            )
        if TeardownOperation.EXACT_SET in self.names:
            first_exact = self.names.index(TeardownOperation.EXACT_SET)
            assert all(name == TeardownOperation.EXACT_SET for name in self.names[first_exact:]), (
                self.names
            )
        overlap = sorted(self.pid_phase_selection & self.broad_targets)
        assert not overlap, (
            f"{overlap} were selected for the pid phase although the group handle "
            "already covers them, so their numbers are signalled again after that "
            "operation has released them"
        )
        assert self.already_exited == [], self.already_exited


def _trace_teardown(monkeypatch: pytest.MonkeyPatch, status_record: Path) -> TeardownTrace:
    """Watch every teardown operation, in order, without replacing any of them.

    Each wrapper calls straight through, so the real signals go out and the real
    processes die; what is added is a reading taken at the instant teardown reached
    that operation. Three defects live in those instants and none is observable from
    outside the process they happen in: a pid list assembled from history, a numeric
    root walked or grouped without proof, and — the ordering one — a broad handle
    dereferenced after an earlier step killed the process whose existence proved it.

    ``status_record`` is where the dispatch under test writes its own status
    directory, which `run_onejudge` creates and names only once it is running; each
    wrapper reads it back at the instant it is called.
    """
    trace = TeardownTrace()
    terminate = terminate_identified_processes
    proven_group = terminate_proven_process_group
    tree = terminate_tree
    plain_group = terminate_process_group

    def evidence_is_live(handle: ProcessId) -> bool:
        """Whether a stamped process still holds the number about to be signalled."""
        try:
            directory = Path(status_record.read_text(encoding="utf-8").strip())
        except OSError:  # pragma: no cover - the dispatch always records it first
            return False
        return group_holds_stamped_process(directory, handle)

    def observed_broad(operation: TeardownOperation, handle: ProcessId) -> None:
        trace.operations.append(TeardownStep(operation, handle, evidence_is_live(handle)))
        trace.broad_targets.update(_group_members(handle))

    def observing_terminate(
        identities: tuple[ProcessIdentity, ...],
        *,
        externally_waited: tuple[ProcessId, ...] = (),
    ) -> tuple[ProcessIdentity, ...]:
        trace.operations.append(TeardownStep(TeardownOperation.EXACT_SET, None, True))
        pids = {identity.pid for identity in identities}
        trace.pid_phase_selection.update(pids)
        trace.already_exited.extend(pid for pid in pids if not is_running(pid))
        escalated = terminate(identities, externally_waited=externally_waited)
        trace.escalated.update(identity.pid for identity in escalated)
        return escalated

    def observing_group(group_id: ProcessId, *, still_ours: Callable[[ProcessId], bool]) -> None:
        observed_broad(TeardownOperation.PROCESS_GROUP, group_id)
        proven_group(group_id, still_ours=still_ours)

    def observing_tree(root_pid: ProcessId) -> None:  # pragma: no cover - must never run
        observed_broad(TeardownOperation.TREE, root_pid)
        tree(root_pid)

    def observing_plain_group(  # pragma: no cover - must never run
        group_id: ProcessId, *, externally_waited: tuple[ProcessId, ...] = ()
    ) -> None:
        observed_broad(TeardownOperation.UNPROVEN_GROUP, group_id)
        plain_group(group_id, externally_waited=externally_waited)

    monkeypatch.setattr(dispatch_module, TeardownOperation.EXACT_SET, observing_terminate)
    monkeypatch.setattr(dispatch_module, TeardownOperation.PROCESS_GROUP, observing_group)
    # Neither is used by teardown any more, and re-introducing either is the
    # regression: both re-derive a pid list from a number after the caller's own
    # signals may have released it.
    monkeypatch.setattr(dispatch_module, TeardownOperation.TREE, observing_tree, raising=False)
    monkeypatch.setattr(
        dispatch_module, TeardownOperation.UNPROVEN_GROUP, observing_plain_group, raising=False
    )
    return trace


#: A process that leads a session of its own and spawns one child, exactly as a
#: dispatch root does — so a recorded number pointing at it looks, to parentage and to
#: `killpg`, like the real thing. Its child is started *without* the stamp, so a walk
#: from a proven root is the only thing that can reach it.
_SESSION_TREE = (
    "import os, subprocess, sys, time\n"
    "unstamped = {k: v for k, v in os.environ.items() if k != sys.argv[2]}\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],"
    " env=unstamped)\n"
    "open(sys.argv[1], 'w').write(str(child.pid))\n"
    "time.sleep(30)\n"
)


def _session_tree(marker: Path, *, stamp: Path | None) -> subprocess.Popen[bytes]:
    """Start one such tree, stamped for ``stamp`` or carrying no stamp at all."""
    environment = {key: value for key, value in os.environ.items() if key != AGENT_STATUS_DIR_ENV}
    if stamp is not None:
        environment[AGENT_STATUS_DIR_ENV] = os.fspath(stamp)
    return subprocess.Popen(
        [sys.executable, "-c", _SESSION_TREE, os.fspath(marker), AGENT_STATUS_DIR_ENV],
        env=environment,
        start_new_session=True,
    )


def test_a_recorded_root_that_is_no_longer_ours_is_never_walked_grouped_or_signalled(
    tmp_path,
) -> None:
    """The recycled root: the number is live, and none of it is this dispatch's.

    A pid is a slot. This host's counter completes a full cycle in under a day and a
    dispatch holds its recorded root for the length of a turn, so by teardown that
    number can name a stranger — one that leads a process group of its own and has
    children, which is exactly what the recorded number was for. Walking it, grouping
    it, or signalling it would then reach into somebody else's work; on this host that
    somebody is another planner's supervision.

    Asked directly rather than through a dispatch, because a dispatch cannot be made
    to recycle its own root on demand — and asked *before* anything is signalled, so a
    wrong answer is only ever recorded here. The stranger and its child are left
    running afterwards as the assertion that nothing acted on them.
    """
    status_dir = tmp_path / "agent"
    status_dir.mkdir()
    stranger_marker = tmp_path / "stranger-child"
    mine_marker = tmp_path / "my-child"
    stranger = _session_tree(stranger_marker, stamp=None)
    mine = _session_tree(mine_marker, stamp=status_dir)
    try:
        stranger_child = ProcessId(await_recorded_pid(stranger_marker))
        my_child = ProcessId(await_recorded_pid(mine_marker))

        unowned = owned_tree(status_dir, ProcessId(stranger.pid))
        owned = owned_tree(status_dir, ProcessId(mine.pid))

        # The stranger's number proves nothing, so neither handle built on it is
        # admitted and nothing under it is selected. What is left is the stamped half,
        # which belongs to this status directory however the root reads — and with no
        # group admitted, the pid phase is what has to signal it.
        assert unowned.root is None
        assert unowned.group is None
        assert unowned.grouped == ()
        assert _pids(unowned.ungrouped) == {ProcessId(mine.pid)}
        assert is_running(ProcessId(stranger.pid)) and is_running(stranger_child)
        # The same shape, one stamp different: every handle is admitted, the walk from
        # the proven root reaches a child that carries no stamp of its own, and both
        # land in the group's half — so the pid phase is handed nothing at all.
        assert owned.root == mine.pid
        assert owned.group == mine.pid
        assert set(owned.grouped) == {ProcessId(mine.pid), my_child}
        assert owned.ungrouped == ()
        # A dispatch whose root is gone keeps the stamped half and withholds the rest.
        rootless = owned_tree(status_dir, None)
        assert (rootless.grouped, rootless.root, rootless.group) == ((), None, None)
        assert _pids(rootless.ungrouped) == {ProcessId(mine.pid)}
    finally:
        for started in (stranger, mine):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(os.getpgid(started.pid), signal.SIGKILL)
            started.wait(timeout=10)


def test_teardown_signals_the_live_tree_and_never_a_pid_that_already_exited(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Teardown acts on processes that exist, not on a list of ones that did.

    Every pid the dispatch ever saw used to be unioned into one list and signalled at
    the end, most of it long gone by then — a traced run measured 28% of the signalled
    pids already exited. This host's pid counter completes a full cycle in under a day
    while a single turn holds its recorded pids for up to half an hour, so those
    signals are aimed at slots something unrelated may already hold.

    Both halves of the replacement are asserted here against a real tree: nothing that
    exited during the run is signalled, and the survivor that no walk from the root can
    reach — reparented to init, in a session of its own — still is, claimed by the
    environment stamp its dispatch fixed at ``exec``.

    `terminate_processes` is wrapped rather than replaced: the real function runs, real
    signals go out, and what the wrapper adds is a reading of each pid's liveness taken
    at the instant teardown decided on it. That decision is the defect, and it is not
    otherwise observable from outside the process it happens in.
    """
    churn_record = tmp_path / "churned"
    orphan_record = tmp_path / "orphan"
    onejudge = tmp_path / "onejudge"
    onejudge.write_text(f"#!/usr/bin/env python3\n{_CHURNING_ONEJUDGE}", encoding="utf-8")
    onejudge.chmod(0o700)

    status_record = tmp_path / "status-dir"
    trace = _trace_teardown(monkeypatch, status_record)

    report = run_onejudge(
        {},
        "task",
        onejudge_bin=os.fspath(onejudge),
        cwd=tmp_path,
        env={
            "CHURN_RECORD": os.fspath(churn_record),
            "ORPHAN_RECORD": os.fspath(orphan_record),
            "STATUS_RECORD": os.fspath(status_record),
        },
    )

    churned = [ProcessId(int(line)) for line in churn_record.read_text(encoding="utf-8").split()]
    orphan = ProcessId(int(orphan_record.read_text(encoding="utf-8")))

    assert report.completed is True
    assert len(churned) == 3
    assert [pid for pid in churned if is_running(pid)] == []
    assert trace.pid_phase_selection.isdisjoint(churned)
    assert orphan in trace.pid_phase_selection
    assert await_reaped(orphan)
    # The root exited with the report, and the orphan left its group, so no number
    # proves anything here: every broad handle is withheld and the only operation is
    # the exact set. The orphan was still reaped, by its stamp — which is the point.
    assert trace.names == [TeardownOperation.EXACT_SET]
    trace.assert_one_mechanism_per_process_in_the_right_order()


#: A wedged dispatch carrying every shape teardown has to tell apart: the root, a
#: child that stays in its process group, a descendant that leaves for a session of
#: its own, and an orphan whose parent exits at once so init adopts it. The last two
#: are exactly what `killpg` cannot reach and what the pid phase is for.
_WEDGED_ONEJUDGE = """\
import os
import subprocess
import sys
import time

if "--version" in sys.argv:
    print("onejudge 0.3.4")
    raise SystemExit(0)

QUIET = [sys.executable, "-c", "import time; time.sleep(30)"]
# Ignores SIGTERM, so terminating it takes the second signal — the one that must be
# sent only while the identity it was proven under still answers to the number.
DEAF = [sys.executable, "-c", "import signal, time; signal.signal(signal.SIGTERM, "
        "signal.SIG_IGN); time.sleep(30)"]


def record(name, pid):
    with open(os.environ[name], "w") as handle:
        handle.write(str(pid))


record("ROOT_RECORD", os.getpid())
record("STATUS_RECORD", os.environ["ORCHESTRATOR_AGENT_STATUS_DIR"])
record("GROUPED_RECORD", subprocess.Popen(QUIET).pid)
record("DETACHED_RECORD", subprocess.Popen(DEAF, start_new_session=True).pid)
# Its parent exits immediately, so this one is reparented to init: no walk from any
# root reaches it, and only the stamp it inherited says whose it is.
orphan = subprocess.Popen(
    [sys.executable, "-c", "import os, subprocess, sys, time; "
     "child = subprocess.Popen(sys.argv[1:], start_new_session=True); "
     "open(os.environ['ORPHAN_RECORD'], 'w').write(str(child.pid))", *QUIET]
)
orphan.wait()
time.sleep(30)
"""


def test_a_stalled_dispatch_still_walks_and_groups_the_root_it_can_prove(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other side of the gate, and the partition: one mechanism per process.

    A proof that never admits anything would close the stale-pid hazard by giving up
    the cleanup, so this pins the case the group handle exists for — a wedged tree
    whose root is alive and stamped — and, in the same tree, the two shapes that
    handle cannot reach:

    * a **descendant in a session of its own**, which parentage still finds and
      `killpg` does not — and which ignores `SIGTERM`, so terminating it needs the
      second signal; and
    * a **stamped orphan**, whose parent exits immediately so that init adopts it and
      no walk from any root can reach it either — and which exits on the first signal,
      so its number must never be signalled again.

    Every process here is therefore terminated, and each by exactly one mechanism: the
    group's own members through the group, these two by pid. The pid phase must not
    even be *handed* a group member — the group operation ends with that member dead,
    and its number is then the kernel's to give away — and within that phase, the
    escalation to `SIGKILL` must reach the process that is still itself and no other.
    """
    records = {name: tmp_path / name for name in ("root", "grouped", "detached", "orphan")}
    status_record = tmp_path / "status-dir"
    onejudge = tmp_path / "onejudge"
    onejudge.write_text(
        f"#!/usr/bin/env python3\n{_WEDGED_ONEJUDGE}",
        encoding="utf-8",
    )
    onejudge.chmod(0o700)

    trace = _trace_teardown(monkeypatch, status_record)

    with pytest.raises(DispatchError, match="dispatch stalled"):
        run_onejudge(
            {},
            "task",
            onejudge_bin=os.fspath(onejudge),
            cwd=tmp_path,
            env={
                "ORCHESTRATOR_DISPATCH_STALL_TIMEOUT": "0.3",
                "STATUS_RECORD": os.fspath(status_record),
                **{f"{name.upper()}_RECORD": os.fspath(path) for name, path in records.items()},
            },
        )

    pids = {
        name: ProcessId(int(path.read_text(encoding="utf-8"))) for name, path in records.items()
    }

    # One broad operation, on the proven group, and it is the *first* thing teardown
    # does — before any pid this dispatch owns has been signalled, so the members
    # whose existence reserves that number are all still alive to reserve it.
    assert trace.names == [TeardownOperation.PROCESS_GROUP, TeardownOperation.EXACT_SET]
    assert trace.broad == [TeardownStep(TeardownOperation.PROCESS_GROUP, pids["root"], True)]
    # The partition, both ways round: the group covers the root and the child that
    # stayed in it, and the pid phase is handed those two and *only* those two that
    # the group cannot reach.
    trace.assert_one_mechanism_per_process_in_the_right_order()
    assert {pids["root"], pids["grouped"]} <= trace.broad_targets
    assert trace.pid_phase_selection == {pids["detached"], pids["orphan"]}
    # And inside that phase, the second signal went only where the identity still
    # matched: the orphan exited on the `SIGTERM` and its number was left alone, while
    # the descendant that ignored it was still itself and was killed.
    assert trace.escalated == {pids["detached"]}
    for pid in pids.values():
        assert await_reaped(pid), pid


def test_run_onejudge_retries_transient_empty_watchdog_pid(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    onejudge = tmp_path / "onejudge"
    onejudge.write_text(
        '#!/bin/sh\nif [ "$1" = "--version" ]; then echo "onejudge 0.3.4"; exit 0; fi\n'
        "sleep 0.2\n"
        'printf \'%s\\n\' \'{"schema_version":4,"transcript":{"messages":[]},'
        '"stopped_early":false}\'\n',
        encoding="utf-8",
    )
    onejudge.chmod(0o700)
    original_read_text = Path.read_text
    injected = False

    def transient_empty(path: Path, *args: object, **kwargs: object) -> str:
        nonlocal injected
        if (
            not injected
            and path.name == "pid"
            and path.parent.name.startswith("orchestrator-watchdog-")
        ):
            injected = True
            return ""
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", transient_empty)

    report = run_onejudge({}, "task", onejudge_bin=os.fspath(onejudge))

    assert injected
    assert report.completed is True


def test_worker_heartbeat_deadline_ignores_busy_descendant(tmp_path) -> None:
    status = tmp_path / "agent-status"
    onejudge = tmp_path / "onejudge"
    onejudge.write_text(
        '#!/bin/sh\nif [ "$1" = "--version" ]; then echo "onejudge 0.3.4"; exit 0; fi\n'
        'printf "%s\\n" "$$" >"$ORCHESTRATOR_AGENT_STATUS_DIR/agent.pid"\n'
        'touch "$ORCHESTRATOR_AGENT_STATUS_DIR/agent.heartbeat"\n'
        "while :; do :; done &\n"
        "child=$!\n"
        'printf "%s\\n" "$child" >"$ORCHESTRATOR_AGENT_STATUS_DIR/agent.child.pid"\n'
        'trap \'kill "$child" 2>/dev/null || true; wait "$child" 2>/dev/null || true; '
        "exit 143' TERM\n"
        'wait "$child"\n',
        encoding="utf-8",
    )
    onejudge.chmod(0o700)

    report = run_onejudge(
        {},
        "task",
        onejudge_bin=os.fspath(onejudge),
        cwd=tmp_path,
        env={
            "ORCHESTRATOR_AGENT_STATUS_DIR": os.fspath(status),
            "ORCHESTRATOR_WORKER_HEARTBEAT_TIMEOUT": "0.2",
            "ORCHESTRATOR_DISPATCH_STALL_TIMEOUT": "5",
        },
    )

    assert report.outcome == "worker-died"
    assert report.completed is False


def test_worker_death_report_carries_the_recorded_exit_status_and_stderr(tmp_path) -> None:
    """A death before the first turn reports the wrapper's account of it.

    The wrapper parks after its child fails so the dispatcher can see the marker,
    then the whole tree is torn down — so the exit status and stderr it left in the
    status directory are the only evidence that outlives the failure.
    """
    status = tmp_path / "agent-status"
    onejudge = tmp_path / "onejudge"
    onejudge.write_text(
        '#!/bin/sh\nif [ "$1" = "--version" ]; then echo "onejudge 0.3.4"; exit 0; fi\n'
        'dir=$ORCHESTRATOR_AGENT_STATUS_DIR\nprintf "%s\\n" "$$" >"$dir/agent.pid"\n'
        'printf "7\\n" >"$dir/agent.exit_code"\n'
        # A verbose harness precedes its own failure with pages of startup chatter;
        # the tail is the part that names the failure, so that is what survives.
        'python3 -c "print(\'noise \' * 400)" >"$dir/agent.stderr"\n'
        'printf "claude: no conversation found with session id 0dd\\n" >>"$dir/agent.stderr"\n'
        # Parks exactly as the wrapper does, and idles exactly as the wrapper does:
        # a double that spun here would hold a core of this host for the whole test.
        'printf "%s\\n" "$$" >"$dir/agent.failed"\nwhile :; do sleep 0.05; done\n',
        encoding="utf-8",
    )
    onejudge.chmod(0o700)

    report = run_onejudge(
        {},
        "task",
        onejudge_bin=os.fspath(onejudge),
        env={"ORCHESTRATOR_AGENT_STATUS_DIR": os.fspath(status)},
    )

    assert report.outcome == "worker-died"
    assert report.stderr.startswith("worker-died")
    assert "agent exit status 7" in report.stderr
    assert report.stderr.endswith("claude: no conversation found with session id 0dd")
    assert "..." in report.stderr
    assert len(report.stderr) < 1600
    assert report.failure_attribution is not None
    assert report.failure_attribution["cause"] == "stale_session_resume"
    # The harness wrote a bare "claude", which names none of the three configured
    # claude-code identities. Reported unknown rather than guessed at: an invented
    # identity would send the operator to capacity that no view can corroborate.
    # The cause and the dropped session id are what this failure is diagnosed from.
    assert report.failure_attribution["identity"] == "unknown"
    assert report.failure_attribution["missing_session_id"] == "0dd"
    assert recordable_provider_failure(report.failure_attribution)


def test_a_provider_failure_reads_differently_from_a_worker_that_stopped(tmp_path) -> None:
    """`worker-died` alone shaped every wrong hypothesis; the reason is the fix.

    Both journeys below end as `worker-died`. Only the recorded reason says which
    one to retry and which one to escalate, so the two are compared side by side.
    """
    status = tmp_path / "throttled-status"
    onejudge = tmp_path / "throttled"
    onejudge.write_text(
        '#!/bin/sh\nif [ "$1" = "--version" ]; then echo "onejudge 0.3.4"; exit 0; fi\n'
        'd="$ORCHESTRATOR_AGENT_STATUS_DIR"\n'
        'printf "%s\\n" "$$" >"$d/agent.pid"\n'
        'touch "$d/agent.heartbeat"\n'
        'printf "provider error: codex 429 rate_limit_error quota exhausted; '
        'retry after 30 seconds\\n" >"$d/agent.stderr"\n'
        'printf "agent harness exited 7\\n" >"$d/agent.failure"\n'
        'printf "%s\\n" "$$" >"$d/agent.failed"\n'
        "while :; do sleep 0.05; done\n",
        encoding="utf-8",
    )
    onejudge.chmod(0o700)

    throttled = run_onejudge(
        {},
        "task",
        onejudge_bin=os.fspath(onejudge),
        env={"ORCHESTRATOR_AGENT_STATUS_DIR": os.fspath(status)},
    )

    quiet_status = tmp_path / "quiet-status"
    quiet = tmp_path / "quiet"
    quiet.write_text(
        '#!/bin/sh\nif [ "$1" = "--version" ]; then echo "onejudge 0.3.4"; exit 0; fi\n'
        'printf "%s\\n" "$$" >"$ORCHESTRATOR_AGENT_STATUS_DIR/agent.pid"\n'
        "while :; do :; done &\n"
        "child=$!\n"
        'printf "%s\\n" "$child" >"$ORCHESTRATOR_AGENT_STATUS_DIR/agent.child.pid"\n'
        'trap \'kill "$child" 2>/dev/null || true; wait "$child" 2>/dev/null || true; '
        "exit 143' TERM\n"
        'wait "$child"\n',
        encoding="utf-8",
    )
    quiet.chmod(0o700)

    stopped = run_onejudge(
        {},
        "task",
        onejudge_bin=os.fspath(quiet),
        env={
            "ORCHESTRATOR_AGENT_STATUS_DIR": os.fspath(quiet_status),
            "ORCHESTRATOR_WORKER_HEARTBEAT_TIMEOUT": "0.2",
        },
    )

    assert throttled.outcome == stopped.outcome == "worker-died"
    assert throttled.outcome_detail == (
        "agent harness exited 7: provider error: codex 429 rate_limit_error quota "
        "exhausted; retry after 30 seconds"
    )
    assert stopped.outcome_detail == "the agent harness stopped heartbeating for 0.2s"
    # The reported sentence is the observed condition wrapped in what the wrapper
    # recorded about the child, so both halves reach a reader of the node result.
    assert throttled.stderr.startswith("worker-died (watchdog pid ")
    assert throttled.stderr.endswith(f": {throttled.outcome_detail}")
    assert throttled.failure_attribution is not None
    assert throttled.failure_attribution["side"] == "agent"
    assert throttled.failure_attribution["cause"] == "rate_limit"
    assert throttled.failure_attribution["failure_kind"] == "rate_limit"
    assert throttled.failure_attribution["identity"] == "codex"
    assert throttled.failure_attribution["wait_seconds"] == 30


def test_a_recorded_provider_refusal_never_carries_a_credential_value() -> None:
    """`raw_tail` is the harness's own words, so it is redacted like every other line.

    The attribution is persisted to the journal and the recorded result and served
    over the read API — the durable, served surface the redaction path exists to keep
    a credential out of. The structured payload is the same text by another route, so
    the redaction happens before either is derived rather than on the tail alone.
    """
    token = "sk-ant-oat01-not-a-real-credential"
    os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = token
    try:
        classified = classify_provider_failure(
            f"provider error (respond): harness failed (quota) for codex using {token}; "
            f'resets Aug 8; {{"subtype":"auth_error","errors":["token {token} rejected"]}}'
        )
    finally:
        del os.environ["CLAUDE_CODE_OAUTH_TOKEN"]

    assert classified is not None
    assert token not in classified["raw_tail"]
    assert "<redacted:CLAUDE_CODE_OAUTH_TOKEN>" in classified["raw_tail"]
    structured = classified["structured_error"]
    assert structured["errors"] == ["token <redacted:CLAUDE_CODE_OAUTH_TOKEN> rejected"]
    assert structured["subtype"] == "auth_error"
    # Redacting did not cost the classification the evidence around it.
    assert classified["cause"] == "quota_mid_conversation"
    assert classified["identity"] == "codex"
    assert classified["reset_time"] == "Aug 8"


def test_a_recorded_agent_failure_never_carries_a_credential_value(tmp_path) -> None:
    """The harness stderr this reads back is durable evidence, so it is redacted."""
    status = tmp_path / "agent"
    status.mkdir()
    token = "sk-ant-oat01-not-a-real-credential"
    (status / "agent.failure").write_text("agent harness exited 1\n", encoding="utf-8")
    (status / "agent.stderr").write_text(
        f"harness failed (auth): token {token} rejected\n", encoding="utf-8"
    )

    os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = token
    try:
        reason = agent_failure_reason(status)
    finally:
        del os.environ["CLAUDE_CODE_OAUTH_TOKEN"]

    assert reason is not None
    assert token not in reason
    assert "<redacted:CLAUDE_CODE_OAUTH_TOKEN>" in reason
    assert reason.startswith("agent harness exited 1: harness failed (auth):")


def test_missing_agent_heartbeat_reaches_worker_death_deadline(tmp_path) -> None:
    status = tmp_path / "agent-status"
    onejudge = tmp_path / "onejudge"
    onejudge.write_text(
        '#!/bin/sh\nif [ "$1" = "--version" ]; then echo "onejudge 0.3.4"; exit 0; fi\n'
        'printf "%s\\n" "$$" >"$ORCHESTRATOR_AGENT_STATUS_DIR/agent.pid"\n'
        "while :; do :; done &\n"
        "child=$!\n"
        'printf "%s\\n" "$child" >"$ORCHESTRATOR_AGENT_STATUS_DIR/agent.child.pid"\n'
        'trap \'kill "$child" 2>/dev/null || true; wait "$child" 2>/dev/null || true; '
        "exit 143' TERM\n"
        'wait "$child"\n',
        encoding="utf-8",
    )
    onejudge.chmod(0o700)

    report = run_onejudge(
        {},
        "task",
        onejudge_bin=os.fspath(onejudge),
        env={
            "ORCHESTRATOR_AGENT_STATUS_DIR": os.fspath(status),
            "ORCHESTRATOR_WORKER_HEARTBEAT_TIMEOUT": "0.2",
        },
    )

    assert report.outcome == "worker-died"


def test_malformed_agent_identity_falls_back_to_stall_watchdog(tmp_path) -> None:
    status = tmp_path / "agent-status"
    onejudge = tmp_path / "onejudge"
    onejudge.write_text(
        '#!/bin/sh\n[ "$1" = "--version" ] && { echo "onejudge 0.3.4"; exit; }\n'
        'printf "bad\\n" >"$ORCHESTRATOR_AGENT_STATUS_DIR/agent.pid"\nsleep 60\n',
        encoding="utf-8",
    )
    onejudge.chmod(0o700)

    with pytest.raises(DispatchError, match="dispatch stalled"):
        run_onejudge(
            {},
            "task",
            onejudge_bin=os.fspath(onejudge),
            env={
                "ORCHESTRATOR_AGENT_STATUS_DIR": os.fspath(status),
                "ORCHESTRATOR_DISPATCH_STALL_TIMEOUT": "0.2",
            },
        )


def test_malformed_agent_child_pid_does_not_mask_heartbeat_deadline(tmp_path) -> None:
    status = tmp_path / "agent-status"
    onejudge = tmp_path / "onejudge"
    onejudge.write_text(
        '#!/bin/sh\nif [ "$1" = "--version" ]; then echo "onejudge 0.3.4"; exit 0; fi\n'
        'printf "%s\\n" "$$" >"$ORCHESTRATOR_AGENT_STATUS_DIR/agent.pid"\n'
        'printf "bad\\n" >"$ORCHESTRATOR_AGENT_STATUS_DIR/agent.child.pid"\n'
        'touch "$ORCHESTRATOR_AGENT_STATUS_DIR/agent.heartbeat"\n'
        "sleep 60\n",
        encoding="utf-8",
    )
    onejudge.chmod(0o700)

    report = run_onejudge(
        {},
        "task",
        onejudge_bin=os.fspath(onejudge),
        env={
            "ORCHESTRATOR_AGENT_STATUS_DIR": os.fspath(status),
            "ORCHESTRATOR_WORKER_HEARTBEAT_TIMEOUT": "0.2",
        },
    )

    assert report.outcome == "worker-died"


def test_agent_pid_outside_dispatch_tree_is_rejected(tmp_path) -> None:
    onejudge = tmp_path / "onejudge"
    onejudge.write_text(
        '#!/bin/sh\n[ "$1" = "--version" ] && { echo "onejudge 0.3.4"; exit; }\n'
        'printf "2147483647\\n" >"$ORCHESTRATOR_AGENT_STATUS_DIR/agent.pid"\nsleep 60\n',
        encoding="utf-8",
    )
    onejudge.chmod(0o700)

    report = run_onejudge({}, "task", onejudge_bin=os.fspath(onejudge))

    assert report.outcome == "worker-died"


def test_agent_turns_shorter_than_the_poll_interval_are_not_mistaken_for_death(tmp_path) -> None:
    """A healthy worker rotates agent turns faster than the supervisor samples.

    The process tree is sampled before the recorded agent pid is read, so a turn
    that starts or finishes inside that gap is absent from the sample while the
    worker is perfectly alive.

    The double keeps `scripts/oneharness-agent.sh`'s ordering — the pid a turn
    advertises is the turn's own process, and it records `agent.done` before
    exiting — because that is the whole reason the gap is survivable: a pid missing
    from a re-sampled tree has by construction already left its marker. The wrapper
    owns that contract and
    `test_oneharness_agent_wrapper.test_the_pid_a_turn_advertises_outlives_the_marker_that_closes_it`
    gates the two against drifting apart.
    """
    onejudge = tmp_path / "onejudge"
    onejudge.write_text(
        '#!/bin/sh\n[ "$1" = "--version" ] && { echo "onejudge 0.3.4"; exit; }\n'
        'd="$ORCHESTRATOR_AGENT_STATUS_DIR"\ni=0\n'
        "while [ $i -lt 40 ]; do\n"
        "  sh -c '\n"
        "    d=$1\n"
        '    printf "%s\\n" "$$" >"$d/agent.pid.tmp"; mv "$d/agent.pid.tmp" "$d/agent.pid"\n'
        '    rm -f "$d/agent.done"\n'
        '    printf "%s\\n" "$$" >"$d/agent.heartbeat.tmp";'
        ' mv "$d/agent.heartbeat.tmp" "$d/agent.heartbeat"\n'
        "    sleep 0.12\n"
        '    printf "%s\\n" "$$" >"$d/agent.done.tmp"; mv "$d/agent.done.tmp" "$d/agent.done"\n'
        '  \' turn "$d"\n'
        "  i=$((i + 1))\n"
        "done\n"
        'printf \'%s\\n\' \'{"schema_version":4,"transcript":{"messages":[]},'
        '"stopped_early":false}\'\n',
        encoding="utf-8",
    )
    onejudge.chmod(0o700)

    report = run_onejudge({}, "task", onejudge_bin=os.fspath(onejudge))

    assert report.outcome is None
    assert report.completed is True


@pytest.mark.parametrize("value", ["bad", "0", "-1", "nan", "inf"])
def test_worker_heartbeat_timeout_rejects_invalid_values(tmp_path, value: str) -> None:
    onejudge = tmp_path / "onejudge"
    onejudge.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    onejudge.chmod(0o700)

    with pytest.raises(DispatchError, match="ORCHESTRATOR_WORKER_HEARTBEAT_TIMEOUT"):
        run_onejudge(
            {},
            "task",
            onejudge_bin=os.fspath(onejudge),
            env={"ORCHESTRATOR_WORKER_HEARTBEAT_TIMEOUT": value},
        )


def test_dispatch_file_progress_records_files_and_skips_disappeared_entries(tmp_path) -> None:
    progress_file = tmp_path / "progress.log"
    progress_file.write_text("working", encoding="utf-8")
    (tmp_path / "disappeared").symlink_to(tmp_path / "absent")

    progress = _file_progress(tmp_path)

    assert len(progress) == 1
    assert progress[0].path == os.fspath(progress_file)
    assert progress[0].size == len("working")


@pytest.mark.parametrize("contents", ["", "not-a-pid", "0", "-1"])
def test_watchdog_pid_file_rejects_invalid_contents(tmp_path, contents: str) -> None:
    pid_file = tmp_path / "watchdog.pid"
    pid_file.write_text(contents, encoding="utf-8")

    with pytest.raises(DispatchError, match="watchdog pid file is invalid"):
        _read_watchdog_pid(pid_file)


@pytest.mark.reads_docs
def test_dispatch_stall_default_documentation_cannot_drift() -> None:
    documentation = (REPO_ROOT / "docs" / "onejudge-integration.md").read_text(encoding="utf-8")

    assert f"defaults to `{DEFAULT_DISPATCH_STALL_TIMEOUT}` seconds" in documentation
    assert f"defaults to `{DEFAULT_WORKER_HEARTBEAT_TIMEOUT}`" in documentation
    assert f"default `{DEFAULT_ROUND_BUDGET:g}`" in documentation


def test_watchdog_process_probe_identifies_current_process() -> None:
    activity = process_activity(ProcessId(os.getpid()))
    assert os.getpid() in activity.pids
    assert activity.cpu_ticks > 0
    assert activity.io_bytes >= 0


@pytest.mark.parametrize(
    ("raw_stat", "io_fields"),
    [
        ("missing delimiter", ["rchar: 1"]),
        ("1 (worker) S 2", ["rchar: 1"]),
        ("1 (worker) S 2 0 0 0 0 0 0 0 0 0 bad 5", ["rchar: 1"]),
        ("1 (worker) S 2 0 0 0 0 0 0 0 0 0 4 5", ["rchar: bad"]),
    ],
)
def test_watchdog_rejects_malformed_procfs_records(raw_stat: str, io_fields: list[str]) -> None:
    assert _parse_stat(raw_stat, io_fields) is None


def test_watchdog_cleanup_and_usage_are_safe_for_absent_process(capsys) -> None:
    terminate_tree(ProcessId(2**31 - 1))
    assert watchdog_main([]) == 2
    assert "usage: watchdog" in capsys.readouterr().err


def test_watchdog_reaps_previously_observed_process() -> None:
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])

    terminate_processes((ProcessId(process.pid),))
    process.wait(timeout=1)

    assert process.returncode is not None


#: A worker that survives the `SIGTERM` ahead of the `SIGKILL`, which is what puts a
#: process in the "killed but not yet reaped" state the reaping loop exists for. A
#: harness that installs its own shutdown handler is the real case; ignoring the signal
#: outright is the same thing without the shutdown work, and it records its pid only
#: once the handler is in place so the termination below cannot race the install.
_SIGTERM_DEAF_SLEEPER = """
import os, signal, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    handle.write(str(os.getpid()))
time.sleep(60)
"""


def test_watchdog_waits_for_a_killed_process_to_actually_be_reaped(tmp_path) -> None:
    """Termination returns once the kernel has collected the process, not once it signalled.

    ``kill`` returns as soon as the signal is queued, so a process that was still
    running when the ``SIGKILL`` arrived has not died yet when the first
    ``waitpid(WNOHANG)`` pass asks: there is nothing to collect, and the loop has to
    come back for it. Treating that pass as a successful reap would leave the process
    a zombie — still holding the pid a later ``/proc`` walk resolves through, and still
    counted by anything that asks the kernel what is left of a dispatch.
    """
    marker = tmp_path / "deaf-worker.pid"
    process = subprocess.Popen([sys.executable, "-c", _SIGTERM_DEAF_SLEEPER, os.fspath(marker)])
    assert await_recorded_pid(marker) == process.pid

    terminate_processes((ProcessId(process.pid),))

    # A zombie keeps its `/proc` entry until someone waits on it, so this says the
    # process was *reaped* rather than merely killed. Nothing else here can have
    # collected it: this session is the parent and has not waited on it yet.
    assert not Path(f"/proc/{process.pid}").exists()
    assert process.wait(timeout=1) is not None


#: A worker that shuts *itself* down on `SIGTERM`, which is the whole reason a grace
#: period sits between the signal and the `SIGKILL`. Its shutdown work comes before
#: the record of having done it, so the record exists only if the process was still
#: alive some way into that period — termination reaps the process itself, so the file
#: is the evidence and the exit status is not available to be one.
_GRACEFUL_SHUTDOWN_WORK = 0.02
_GRACEFUL_SLEEPER = f"""
import os, signal, sys, time

def shutdown(signum, frame):
    time.sleep({_GRACEFUL_SHUTDOWN_WORK})
    with open(sys.argv[2], "w", encoding="utf-8") as handle:
        handle.write("shut down on SIGTERM")
    os._exit(0)

signal.signal(signal.SIGTERM, shutdown)
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    handle.write(str(os.getpid()))
time.sleep(60)
"""


def test_watchdog_lets_a_signalled_worker_shut_itself_down(tmp_path) -> None:
    """The `SIGKILL` is still held back long enough for a harness to handle `SIGTERM`.

    Termination stops waiting as soon as nothing it signalled is running, which is what
    keeps a finished dispatch from paying for a grace period it is not owed. The period
    itself is not a formality: a worker that is still running gets the whole of it, and
    a worker killed before it finished shutting down leaves no record of having.
    """
    marker = tmp_path / "graceful-worker.pid"
    shutdown = tmp_path / "graceful-worker.shutdown"
    process = subprocess.Popen(
        [sys.executable, "-c", _GRACEFUL_SLEEPER, os.fspath(marker), os.fspath(shutdown)]
    )
    assert await_recorded_pid(marker) == process.pid

    terminate_processes((ProcessId(process.pid),))

    assert shutdown.read_text(encoding="utf-8") == "shut down on SIGTERM"
    assert not is_running(process.pid)


#: The grace period this teardown must not pay, set far enough from the cost it *must*
#: pay that no measurement can confuse the two. What it must pay is three walks of the
#: whole host's process table, two procfs reads per process — a cost that belongs to
#: the host rather than to the teardown: 9ms here at rest, and 180ms measured while
#: this host carried the concurrent-dispatch load it routinely runs under, which is
#: what used to fail this test against a fixed 150ms budget with nothing about the
#: teardown having changed. One grace at this multiple is twenty-five times that
#: worst measurement, so the bound below separates grace from walks by construction
#: instead of by a margin the next busier host closes again.
_UNMISTAKABLE_GRACE = 100 * TERMINATION_GRACE


def test_watchdog_teardown_of_a_finished_dispatch_pays_no_grace_period(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Tearing down a dispatch that already exited costs nothing but the `/proc` walks.

    Every completed dispatch runs this cleanup, and every one of them used to sleep out
    the full `SIGTERM`-to-`SIGKILL` grace once per call — four times over, at a point
    where the tree it is being graceful toward has already gone. Across a suite that
    dispatches hundreds of times that was the single largest cost in the lifecycle e2e.

    The grace is raised rather than the budget, because the two costs are not separable
    by a wall-clock bound at the real one: a walk of a loaded host's process table is
    the same order as `TERMINATION_GRACE` itself. Raised, every one of the four sleeps
    the unconditional form pays is on its own longer than the whole teardown, so this
    still fails for exactly the defect it was written for and no longer fails for the
    host being busy.
    """
    monkeypatch.setattr(watchdog, "TERMINATION_GRACE", _UNMISTAKABLE_GRACE)
    process = subprocess.Popen([sys.executable, "-c", ""], start_new_session=True)
    assert process.wait(timeout=5) == 0
    finished = ProcessId(process.pid)

    start = time.monotonic()
    terminate_processes((finished,))
    terminate_process_group(finished)
    terminate_tree(finished)
    elapsed = time.monotonic() - start

    # Five times the dearest teardown measured under load, and a fifth of a single one
    # of the four grace periods the unconditional form sleeps: too much room to fail on
    # the walks, too little to pass on even one grace.
    assert elapsed < _UNMISTAKABLE_GRACE / 5, elapsed


def test_watchdog_terminates_live_process_tree() -> None:
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])

    terminate_tree(ProcessId(process.pid))
    process.wait(timeout=1)

    assert process.returncode is not None


def test_watchdog_process_group_cleanup_is_safe_for_absent_group() -> None:
    assert terminate_process_group(ProcessId(2**31 - 1)) is None


def test_watchdog_group_shutdown_waits_out_a_member_that_ignores_sigterm(tmp_path) -> None:
    """A group's grace period is the group's, not a formality skipped once it looks empty.

    The group teardown asks `process_group_is_running` rather than holding a pid list,
    and that question has to keep being asked for the whole period: a member that
    ignores `SIGTERM` is still running when it is first asked, and is killed by the
    `SIGKILL` behind it rather than left for whoever looks next.
    """
    marker = tmp_path / "deaf-group-member.pid"
    process = subprocess.Popen(
        [sys.executable, "-c", _SIGTERM_DEAF_SLEEPER, os.fspath(marker)],
        start_new_session=True,
    )
    assert await_recorded_pid(marker) == process.pid

    terminate_process_group(ProcessId(process.pid))

    assert await_reaped(process.pid)


def test_a_recorded_identity_whose_number_moved_on_is_never_signalled(tmp_path) -> None:
    """The recycled pid, at the level where every teardown signal is actually sent.

    Between a caller proving a set and signalling it — and, worse, between its own
    `SIGTERM` and the `SIGKILL` behind it — a process can exit and its number can be
    handed to something unrelated. That successor is indistinguishable from the
    original by number alone, so what is recorded is the number *and* the kernel's
    start token for the process holding it.

    Reproduced with two real processes rather than by waiting for the counter to wrap:
    the identity carries one process's number and another's start token, which is
    exactly the pairing a caller ends up holding when the number changes hands. The
    process wearing that number must be left alone, and it is still running at the end
    to say so.
    """
    stranger = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    # The tokens are start times in clock ticks, so give the two processes different
    # ones rather than trusting the scheduler to; a shared tick would make the stale
    # identity below accidentally valid and the test meaningless.
    time.sleep(0.05)
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        current = identify(ProcessId(stranger.pid))
        borrowed = identify(ProcessId(other.pid))
        assert current is not None and borrowed is not None
        assert current.start != borrowed.start
        stale = ProcessIdentity(ProcessId(stranger.pid), borrowed.start)

        assert terminate_identified_processes((stale,)) == ()

        assert is_running(stranger.pid)
        assert stranger.poll() is None
    finally:
        for process in (stranger, other):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                process.kill()
            process.wait(timeout=10)


def test_only_a_process_that_is_still_itself_is_escalated_to_sigkill(tmp_path) -> None:
    """The second signal, gated by the same identity the first one was.

    Both halves in one call, because they are one decision: a worker that handles
    `SIGTERM` is gone before the grace period is out, and sending its number a
    `SIGKILL` afterwards is sending it to whoever holds that number by then — while a
    worker that ignores `SIGTERM` is still itself, and refusing to kill it would trade
    one defect for a leak. What is returned names the ones that had to be killed.
    """
    graceful_marker = tmp_path / "graceful.pid"
    shutdown = tmp_path / "graceful.shutdown"
    deaf_marker = tmp_path / "deaf.pid"
    graceful = subprocess.Popen(
        [sys.executable, "-c", _GRACEFUL_SLEEPER, os.fspath(graceful_marker), os.fspath(shutdown)]
    )
    deaf = subprocess.Popen([sys.executable, "-c", _SIGTERM_DEAF_SLEEPER, os.fspath(deaf_marker)])
    assert await_recorded_pid(graceful_marker) == graceful.pid
    assert await_recorded_pid(deaf_marker) == deaf.pid
    identities = tuple(
        identity
        for pid in (graceful.pid, deaf.pid)
        if (identity := identify(ProcessId(pid))) is not None
    )
    assert len(identities) == 2

    escalated = terminate_identified_processes(identities)

    assert _pids(escalated) == {ProcessId(deaf.pid)}
    # The graceful worker was never sent a second signal, and it got far enough into
    # its own shutdown to say so.
    assert shutdown.read_text(encoding="utf-8") == "shut down on SIGTERM"
    assert await_reaped(graceful.pid)
    assert await_reaped(deaf.pid)


def test_a_proven_group_kill_is_withheld_once_the_first_signal_released_the_number(
    tmp_path,
) -> None:
    """The second signal asks again, because the first one can release the number.

    A group id is its leader's pid, and the kernel holds that number only while some
    live process names the group — so the `SIGTERM` a caller sends is exactly what can
    free it, and a proof taken before that signal says nothing about the instant
    after. A caller that can no longer prove the group therefore keeps its hands off
    it: the deaf member below survives, as an unrelated process that had been handed
    the recycled number would have to. With the proof intact the same call kills it,
    so the gate withholds nothing a caller can still show is its own.
    """
    marker = tmp_path / "deaf-group-member.pid"
    process = subprocess.Popen(
        [sys.executable, "-c", _SIGTERM_DEAF_SLEEPER, os.fspath(marker)],
        start_new_session=True,
    )
    assert await_recorded_pid(marker) == process.pid
    try:
        terminate_proven_process_group(ProcessId(process.pid), still_ours=lambda _group: False)

        assert is_running(process.pid)

        terminate_proven_process_group(ProcessId(process.pid), still_ours=lambda _group: True)

        assert await_reaped(process.pid)
    finally:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        process.wait(timeout=10)


def test_watchdog_terminates_live_process_group() -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True,
    )

    terminate_process_group(ProcessId(process.pid))
    process.wait(timeout=1)

    assert process.returncode is not None


# A session leader, so the wrapper it starts is *not* already a group leader and the
# flag has real work to do. Without this the guard's own `start_new_session` would
# hand the wrapper a group it did not ask for and the journey would prove nothing.
_SESSION_LAUNCHER = """
import os, subprocess, sys, time
try:
    os.setsid()
except OSError:
    pass  # already led its own session, which is all this needs
subprocess.Popen(sys.argv[1:])
time.sleep(60)
"""


def test_watchdog_group_reaps_a_worker_that_reparented_away(tmp_path) -> None:
    """The wrapper's own process group still reaches what a tree walk has lost."""
    pid_file = tmp_path / "watchdog.pid"
    marker = tmp_path / "worker.pid"
    tree = write_orphaning_tree(tmp_path)
    launcher = subprocess.Popen(
        [
            sys.executable,
            "-c",
            _SESSION_LAUNCHER,
            sys.executable,
            "-m",
            "orchestrator.watchdog",
            OWN_PROCESS_GROUP_FLAG,
            os.fspath(pid_file),
            sys.executable,
            os.fspath(tree),
            os.fspath(marker),
            "--root-exits",
        ],
        cwd=REPO_ROOT,
    )
    try:
        wrapper = ProcessId(await_recorded_pid(pid_file))
        worker = await_recorded_pid(marker)

        assert os.getpgid(wrapper) == wrapper
        assert os.getpgid(worker) == wrapper
        assert os.getpgid(launcher.pid) != wrapper
        # The recorded root is gone, so the supervisor's tree walk reports an empty
        # dispatch while the worker is still running — the leak this group closes.
        assert process_activity(wrapper).pids == ()
        assert is_running(worker)

        terminate_process_group(wrapper)

        assert await_reaped(worker)
    finally:
        launcher.kill()
        launcher.wait(timeout=5)


def test_watchdog_records_pid_and_executes_command(tmp_path, monkeypatch) -> None:
    pid_file = tmp_path / "watchdog.pid"
    monkeypatch.setenv("ORCHESTRATOR_WATCHDOG_UNSET_LLMLINT", "1")
    monkeypatch.setenv("LLMLINT_ONEHARNESS_BIN", "oneharness")

    def execvpe(command: str, args: list[str], env: dict[str, str]) -> None:
        assert command == "worker"
        assert args == ["worker", "--flag"]
        assert "ORCHESTRATOR_WATCHDOG_UNSET_LLMLINT" not in env
        assert "LLMLINT_ONEHARNESS_BIN" not in env
        raise RuntimeError("exec boundary reached")

    monkeypatch.setattr(os, "execvpe", execvpe)
    with pytest.raises(RuntimeError, match="exec boundary reached"):
        watchdog_main([os.fspath(pid_file), "worker", "--flag"])

    assert pid_file.read_text(encoding="utf-8") == str(os.getpid())


def test_watchdog_asked_for_its_own_group_leads_one_before_recording_its_pid(
    tmp_path, monkeypatch
) -> None:
    """The flagged wrapper really regroups itself, and keeps the environment it was given.

    The caller here is the test session, which is what the flag exists to spare — so it
    rejoins the group it came from as soon as the postcondition is read. Rejoining an
    existing group of the same session is permitted for anything that is not a session
    leader, and the guard starts every test subprocess in a session of its own, so
    nothing spawned while this runs can inherit the group either.
    """
    pid_file = tmp_path / "watchdog.pid"
    monkeypatch.delenv("ORCHESTRATOR_WATCHDOG_UNSET_LLMLINT", raising=False)
    monkeypatch.setenv("LLMLINT_ONEHARNESS_BIN", "oneharness")
    original = os.getpgrp()

    def execvpe(command: str, args: list[str], env: dict[str, str]) -> None:
        # Untouched: only the dispatch that asks for it drops the llmlint wrapper, and
        # a worker that inherited one must still find it.
        assert env["LLMLINT_ONEHARNESS_BIN"] == "oneharness"
        raise RuntimeError("exec boundary reached")

    monkeypatch.setattr(os, "execvpe", execvpe)
    try:
        with pytest.raises(RuntimeError, match="exec boundary reached"):
            watchdog_main([OWN_PROCESS_GROUP_FLAG, os.fspath(pid_file), "worker", "--flag"])

        assert os.getpgrp() == os.getpid()
    finally:
        os.setpgid(0, original)

    assert os.getpgrp() == original
    assert pid_file.read_text(encoding="utf-8") == str(os.getpid())


def _label_echoing_onejudge(tmp_path) -> str:
    """A stand-in onejudge that reports the labels it was actually handed."""
    onejudge = tmp_path / "onejudge"
    onejudge.write_text(
        '#!/bin/sh\nif [ "$1" = "--version" ]; then echo "onejudge 0.3.4"; exit 0; fi\n'
        "printf '"
        '{"schema_version":4,"transcript":{"messages":[]},"stopped_early":false,'
        '"usage":{"labels":"%s"}}'
        '\' "$ONEHARNESS_HISTORY_LABELS"\n',
        encoding="utf-8",
    )
    onejudge.chmod(0o700)
    return os.fspath(onejudge)


def test_run_onejudge_propagates_history_labels_to_the_subprocess(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ONEHARNESS_HISTORY_LABELS", raising=False)
    report = run_onejudge(
        {},
        "task",
        onejudge_bin=_label_echoing_onejudge(tmp_path),
        labels={"run_id": "run-9", "round": "2", "node": "api"},
    )
    assert report.raw is not None
    assert report.usage["labels"] == "run_id=run-9,round=2,node=api"


def test_run_onejudge_preserves_inherited_labels_it_did_not_set(tmp_path, monkeypatch) -> None:
    # A nested dispatch must keep the outer run's labels, and win only on conflict.
    monkeypatch.setenv("ONEHARNESS_HISTORY_LABELS", "outer=keep,node=old")
    report = run_onejudge(
        {},
        "task",
        onejudge_bin=_label_echoing_onejudge(tmp_path),
        labels={"node": "api", "run_id": "run-9"},
    )
    assert parse_labels(report.usage["labels"]) == {
        "outer": "keep",
        "node": "api",
        "run_id": "run-9",
    }


def test_run_onejudge_without_labels_leaves_the_env_alone(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ONEHARNESS_HISTORY_LABELS", "outer=keep")
    report = run_onejudge({}, "task", onejudge_bin=_label_echoing_onejudge(tmp_path))
    assert report.usage["labels"] == "outer=keep"


def test_run_onejudge_validates_inherited_labels_without_adding_its_own(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("ONEHARNESS_HISTORY_LABELS", "bad key=dropped,outer=keep,novalue")

    report = run_onejudge({}, "task", onejudge_bin=_label_echoing_onejudge(tmp_path))

    assert report.usage["labels"] == "outer=keep"


def test_run_onejudge_rejects_an_off_contract_label(tmp_path) -> None:
    # A comma cannot round-trip through the list format; fail loudly rather than
    # hand oneharness a value that parses back as two different labels.
    with pytest.raises(DispatchError, match="invalid history label"):
        run_onejudge(
            {}, "task", onejudge_bin=_label_echoing_onejudge(tmp_path), labels={"node": "a,b"}
        )


def _incomplete(
    *,
    turns: int,
    max_turns: int | None,
    verdicts: list[dict[str, object]] | None = None,
    assessment: str | None = None,
    stderr: str = "",
) -> Report:
    return Report(
        "engineer",
        1,
        False,
        True,
        turns,
        verdicts or [],
        {},
        {},
        stderr,
        assessment=assessment,
        max_turns=max_turns,
    )


def test_only_a_run_that_reached_its_cap_is_reported_as_hitting_it() -> None:
    """onejudge exits 1 for both, so the turn count is the only thing that tells them apart."""
    assert incomplete_detail(_incomplete(turns=12, max_turns=12)) == (
        "hit the turn cap after 12 turns"
    )
    assert incomplete_detail(_incomplete(turns=1, max_turns=12)) == (
        "did not complete after 1 turn, short of its 12-turn cap"
    )
    # A dispatch whose config states no cap can still say how far it got.
    assert incomplete_detail(_incomplete(turns=3, max_turns=None)) == (
        "did not complete after 3 turns"
    )


def test_an_incomplete_stop_carries_the_most_specific_reason_it_has() -> None:
    """Verdict, then assessment, then the harness's own words — never nothing."""
    unmet = {
        "kind": "done_when",
        "criterion": "the gate is green",
        "verdict": {"value": False, "reason": "the gate was never run"},
    }
    met = {"kind": "check", "verdict": {"value": True, "reason": "ignored"}}
    detail = incomplete_detail(
        _incomplete(turns=2, max_turns=9, verdicts=[met, unmet], assessment="unused")
    )
    assert detail.endswith(": unmet done_when verdict: the gate was never run")

    # No unmet verdict carries a reason, so the worker's own assessment stands in.
    assert incomplete_detail(
        _incomplete(turns=2, max_turns=9, verdicts=[met], assessment="ran out of context")
    ).endswith(": ran out of context")

    # Neither exists: the harness stderr is the last thing that can say anything.
    assert incomplete_detail(
        _incomplete(turns=2, max_turns=9, stderr="  provider error: 503\n")
    ).endswith(": provider error: 503")

    # And when there is genuinely nothing, the sentence stops rather than trailing.
    assert incomplete_detail(_incomplete(turns=2, max_turns=9)) == (
        "did not complete after 2 turns, short of its 9-turn cap"
    )


def test_an_unusable_turn_cap_is_read_as_no_cap_at_all() -> None:
    """The cap crosses in from a merged config, so an unusable value must not be trusted."""
    assert _configured_turn_cap({"user": {"max_turns": 12}}) == 12
    assert _configured_turn_cap({}) is None
    assert _configured_turn_cap({"user": "not-a-mapping"}) is None
    assert _configured_turn_cap({"user": {}}) is None
    assert _configured_turn_cap({"user": {"max_turns": True}}) is None
    assert _configured_turn_cap({"user": {"max_turns": 0}}) is None
    assert _configured_turn_cap({"user": {"max_turns": "12"}}) is None


@pytest.mark.parametrize(
    ("recorded", "expected"),
    [
        ("1", 1),
        ("0", 0),
        ("128", 128),
        ("137", 137),
        ("143", 143),
        ("", None),
        ("   ", None),
        ("-1", None),
        ("1e3", None),
        ("256", None),
        ("9999", None),
        ("not-a-number", None),
    ],
    ids=[
        "ordinary-exit",
        "zero",
        "boundary",
        "sigkill",
        "sigterm",
        "empty",
        "blank",
        "negative",
        "scientific",
        "above-wait-status",
        "four-digits",
        "prose",
    ],
)
def test_agent_exit_status_repeats_back_only_a_plausible_wait_status(
    tmp_path, recorded: str, expected: int | None
) -> None:
    """The wrapper is the only writer, but this marker crosses a process boundary.

    Whatever cannot be read as a wait status answers ``None``, which the relaunch
    decision treats as "not a launch failure" — the relaunch is positively earned,
    so a marker nobody wrote can never buy one.
    """
    status = tmp_path / f"agent-status-{abs(hash(recorded))}"
    status.mkdir()
    (status / "agent.exit_code").write_text(recorded, encoding="utf-8")

    assert agent_exit_status(status) == expected


def test_agent_exit_status_is_absent_when_the_wrapper_recorded_nothing(tmp_path) -> None:
    """No marker at all is the same answer as an unreadable one, and for one reason."""
    status = tmp_path / "agent-status-unwritten"
    status.mkdir()

    assert agent_exit_status(status) is None
