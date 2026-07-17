"""Unit tests for dispatch/plan helpers and error paths (no onejudge process)."""

from __future__ import annotations

import os

import pytest

from orchestrator import BASE_CONFIG, REPO_ROOT
from orchestrator.dispatch import (
    AGENT_ONEHARNESS_BIN,
    DispatchError,
    Report,
    _agent_run_context,
    _build_report,
    _parse_report,
    run_onejudge,
)
from orchestrator.dispatch import main as dispatch_main
from orchestrator.plan import PlanNode, PlanResult, TaskResult, _render
from orchestrator.plan import main as plan_main


def test_parse_report_handles_junk() -> None:
    assert _parse_report("") is None
    assert _parse_report("not json") is None
    assert _parse_report("[1, 2]") is None  # valid JSON, wrong shape
    assert _parse_report('{"a": 1}') == {"a": 1}


def test_build_report_from_empty_stdout() -> None:
    report = _build_report("p", 1, "", "some stderr")
    assert report.completed is False
    assert report.assistant_turns == 0
    assert report.verdicts == []
    assert report.usage == {}


def test_build_report_counts_assistant_turns() -> None:
    stdout = (
        '{"transcript": {"messages": ['
        '{"role": "user", "content": "t"},'
        '{"role": "assistant", "content": "a"},'
        '{"role": "user", "content": "u"},'
        '{"role": "assistant", "content": "b"}'
        ']}, "verdicts": [], "usage": {"output_tokens": 3}}'
    )
    report = _build_report("p", 0, stdout, "")
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
    import json

    report = _build_report("p", 0, json.dumps({"assessment": value}), "")
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


def test_agent_run_context_project_dir_absolutizes_judge_config() -> None:
    cfg: dict = {"provider": {"kind": "oneharness", "judge_config": "oneharness.judge.toml"}}
    run_cwd, env = _agent_run_context(
        cfg, cwd="/repo", project_dir="/work/target", oneharness_mode="bypass"
    )
    assert run_cwd == "/work/target"
    assert cfg["provider"]["bin"] == str(AGENT_ONEHARNESS_BIN)
    assert env["ONEHARNESS_MODE"] == "bypass"
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
    cfg = {
        "provider": {"kind": "command", "command": ["true"]},
        "agent": {"name": "a", "dir": ".", "instructions": "x"},
        "user": {"persona": "p", "done_when": "d", "max_turns": 1},
    }
    with pytest.raises(DispatchError, match="not found"):
        run_onejudge(cfg, "t", onejudge_bin="onejudge-does-not-exist-xyz")


@pytest.mark.parametrize(
    ("configured_timeout", "expected_timeout"),
    [(None, "10800"), ("73", "73")],
)
def test_run_onejudge_sets_per_turn_timeout(
    tmp_path, monkeypatch, configured_timeout: str | None, expected_timeout: str
) -> None:
    onejudge = tmp_path / "onejudge"
    onejudge.write_text(
        '#!/bin/sh\nprintf \'{"usage": {"oneharness_timeout": "%s"}}\' "$ONEHARNESS_TIMEOUT"\n',
        encoding="utf-8",
    )
    onejudge.chmod(0o755)
    if configured_timeout is None:
        monkeypatch.delenv("ONEHARNESS_TIMEOUT", raising=False)
    else:
        monkeypatch.setenv("ONEHARNESS_TIMEOUT", configured_timeout)
    report = run_onejudge({}, "task", onejudge_bin=os.fspath(onejudge))

    assert report.raw is not None
    assert report.usage["oneharness_timeout"] == expected_timeout


@pytest.mark.parametrize("bad_timeout", ["", "abc", "12.5", "0", "-5"])
def test_run_onejudge_rejects_invalid_timeout(monkeypatch, bad_timeout: str) -> None:
    # ONEHARNESS_TIMEOUT crosses in from the environment; a non-positive-integer value
    # must fail loudly at the boundary rather than reach oneharness.
    monkeypatch.setenv("ONEHARNESS_TIMEOUT", bad_timeout)
    with pytest.raises(DispatchError, match="ONEHARNESS_TIMEOUT must be a positive integer"):
        run_onejudge({}, "task")
