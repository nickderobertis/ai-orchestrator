"""Dispatch/plan tests, including small real subprocess liveness journeys."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from onejudge_sdk import RunResult
from process_tree import await_reaped, await_recorded_pid, is_running, write_orphaning_tree

from orchestrator import BASE_CONFIG, PERSONA_DIR, REPO_ROOT
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
    agent_failure_reason,
    incomplete_detail,
    run_onejudge,
)
from orchestrator.dispatch import main as dispatch_main
from orchestrator.graph import DEFAULT_ROUND_BUDGET
from orchestrator.labels import parse_labels
from orchestrator.plan import PlanNode, PlanResult, TaskResult, _render
from orchestrator.plan import main as plan_main
from orchestrator.watchdog import (
    OWN_PROCESS_GROUP_FLAG,
    ProcessId,
    _parse_stat,
    process_activity,
    terminate_process_group,
    terminate_processes,
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


def test_dispatch_main_unknown_persona_exit_2(capsys) -> None:
    rc = dispatch_main(["no-such-persona", "do it", "--base", str(BASE_CONFIG)])
    assert rc == 2
    assert "unknown persona" in capsys.readouterr().err


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
        'printf "%s\\n" "$$" >"$dir/agent.failed"\nwhile :; do :; done\n',
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
        'printf "provider error: 429 rate_limit_error quota exhausted\\n" >"$d/agent.stderr"\n'
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
        "agent harness exited 7: provider error: 429 rate_limit_error quota exhausted"
    )
    assert stopped.outcome_detail == "the agent harness stopped heartbeating for 0.2s"
    # The reported sentence is the observed condition wrapped in what the wrapper
    # recorded about the child, so both halves reach a reader of the node result.
    assert throttled.stderr.startswith("worker-died (watchdog pid ")
    assert throttled.stderr.endswith(f": {throttled.outcome_detail}")


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


def test_watchdog_terminates_live_process_tree() -> None:
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])

    terminate_tree(ProcessId(process.pid))
    process.wait(timeout=1)

    assert process.returncode is not None


def test_watchdog_process_group_cleanup_is_safe_for_absent_group() -> None:
    assert terminate_process_group(ProcessId(2**31 - 1)) is None


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
