"""Unit tests for dispatch/plan helpers and error paths (no onejudge process)."""

from __future__ import annotations

import os

import pytest
from onejudge_sdk import RunResult

from orchestrator import BASE_CONFIG, REPO_ROOT
from orchestrator.dispatch import (
    AGENT_ONEHARNESS_BIN,
    DispatchError,
    Report,
    _agent_run_context,
    _build_report,
    run_onejudge,
)
from orchestrator.dispatch import main as dispatch_main
from orchestrator.labels import parse_labels
from orchestrator.plan import PlanNode, PlanResult, TaskResult, _render
from orchestrator.plan import main as plan_main


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
        "#!/bin/sh\n"
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


@pytest.mark.parametrize("bad_timeout", ["", "abc", "12.5", "0", "-5"])
def test_run_onejudge_rejects_invalid_timeout(monkeypatch, bad_timeout: str) -> None:
    # ONEHARNESS_TIMEOUT crosses in from the environment; a non-positive-integer value
    # must fail loudly at the boundary rather than reach oneharness.
    monkeypatch.setenv("ONEHARNESS_TIMEOUT", bad_timeout)
    with pytest.raises(DispatchError, match="ONEHARNESS_TIMEOUT must be a positive integer"):
        run_onejudge({}, "task")


def _label_echoing_onejudge(tmp_path) -> str:
    """A stand-in onejudge that reports the labels it was actually handed."""
    onejudge = tmp_path / "onejudge"
    onejudge.write_text(
        "#!/bin/sh\n"
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
