from __future__ import annotations

import json
import os
import socket
import tomllib
from pathlib import Path
from typing import get_type_hints

import pytest

from orchestrator import REPO_ROOT
from orchestrator.config import ConfigError
from orchestrator.dispatch import DispatchError, launch_orchestrator, main_orchestrate
from orchestrator.runs import (
    LAUNCH_COMMAND_FIELDS,
    LAUNCH_STRING_FIELDS,
    LaunchCommands,
    LaunchRecord,
    launch_may_be_active,
    resolve_supervision_run,
)


def _launch_record(
    plan_name: str, pid: int | None = None, run_id: str = "run"
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "channel_id": run_id,
        "plan_name": plan_name,
        "pid": pid if pid is not None else os.getpid(),
        "host": socket.gethostname(),
        "started": "2026-01-01T00:00:00+00:00",
        "commands": {"channel_next": "just channel-next run", "monitor": "just monitor run"},
    }


def test_launch_record_validator_field_lists_match_typed_contract() -> None:
    assert set(get_type_hints(LaunchRecord)) == {
        "schema_version",
        "pid",
        "commands",
        *LAUNCH_STRING_FIELDS,
    }
    assert set(get_type_hints(LaunchCommands)) == set(LAUNCH_COMMAND_FIELDS)


def test_launch_rejects_missing_plan_and_split_skill(tmp_path: Path) -> None:
    with pytest.raises(DispatchError, match="plan does not exist"):
        launch_orchestrator(tmp_path / "missing.json", runs_dir=tmp_path / "runs")
    plan = tmp_path / "plan.json"
    plan.write_text(
        '{"schema_version":3,"tasks":[{"id":"approval","kind":"human","task":"approve"}]}',
        encoding="utf-8",
    )
    with pytest.raises(DispatchError, match="provider kind"):
        launch_orchestrator(
            plan,
            runs_dir=tmp_path / "runs",
            skill_provider={"kind": "split"},
        )


@pytest.mark.parametrize(
    "provider",
    [
        {"kind": "command"},
        {"kind": "command", "command": [""]},
        {"kind": "oneharness", "bin": ""},
    ],
)
def test_launch_validates_provider_payload(tmp_path: Path, provider: dict[str, object]) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(
        '{"schema_version":3,"tasks":[{"id":"approval","kind":"human","task":"approve"}]}',
        encoding="utf-8",
    )
    with pytest.raises(DispatchError, match="provider"):
        launch_orchestrator(plan, runs_dir=tmp_path / "runs", skill_provider=provider)


def test_launch_reaches_real_process_boundary_for_valid_oneharness_provider(tmp_path: Path) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(
        '{"schema_version":3,"tasks":[{"id":"approval","kind":"human","task":"approve"}]}',
        encoding="utf-8",
    )
    with pytest.raises(DispatchError, match="binary not found"):
        launch_orchestrator(
            plan,
            runs_dir=tmp_path / "runs",
            onejudge_bin="definitely-missing-onejudge",
            skill_provider={"kind": "oneharness", "bin": "oneharness"},
        )


def test_launch_rejects_nul_onejudge_binary(tmp_path: Path) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(
        '{"schema_version":3,"tasks":[{"id":"approval","kind":"human","task":"approve"}]}',
        encoding="utf-8",
    )
    with pytest.raises(DispatchError, match="non-NUL"):
        launch_orchestrator(plan, runs_dir=tmp_path / "runs", onejudge_bin="bad\0binary")


def test_orchestrate_cli_prints_run_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text("{}", encoding="utf-8")
    received: dict[str, object] = {}

    def fake_launch(path: Path, **kwargs: object) -> str:
        received.update({"path": path, **kwargs})
        run_dir = tmp_path / "runs" / "live-run"
        run_dir.mkdir(parents=True)
        (run_dir / "launch.json").write_text(
            '{"run_id":"live-run","channel_id":"live-run"}', encoding="utf-8"
        )
        return "live-run"

    monkeypatch.setattr("orchestrator.dispatch.launch_orchestrator", fake_launch)
    assert (
        main_orchestrate(
            [
                str(plan),
                "--runs-dir",
                str(tmp_path / "runs"),
                "--run-id",
                "chosen",
                "--skill-command",
                "fake-provider",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {
        "run_id": "live-run",
        "channel_id": "live-run",
    }
    assert received["skill_provider"] == {
        "kind": "command",
        "command": ["fake-provider"],
    }


def test_orchestrate_cli_reports_launch_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text("{}", encoding="utf-8")

    def fail(*args: object, **kwargs: object) -> str:
        raise DispatchError("launch failed")

    monkeypatch.setattr("orchestrator.dispatch.launch_orchestrator", fail)
    assert main_orchestrate([str(plan)]) == 2
    assert "orchestrate: launch failed" in capsys.readouterr().err


def test_bridge_recipes_reference_declared_console_scripts() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = project["project"]["scripts"]
    justfile = (REPO_ROOT / "justfile").read_text(encoding="utf-8")
    for command in (
        "orchestrator-orchestrate",
        "orchestrator-channel-next",
        "orchestrator-channel-reply",
        "orchestrator-channel-approve",
        "orchestrator-channel-reject",
        "orchestrator-channel-continue",
    ):
        assert command in scripts
        assert f"uv run {command}" in justfile


def test_plan_name_resolution_rejects_ambiguous_and_stale_ids(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    for run_id in ("active-a", "active-b"):
        run_dir = runs / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "launch.json").write_text(
            json.dumps(_launch_record("shared", run_id=run_id)),
            encoding="utf-8",
        )
    with pytest.raises(ConfigError, match="valid active run ids: active-a, active-b"):
        resolve_supervision_run(runs, "shared")
    assert resolve_supervision_run(runs, "active-a") == "active-a"
    with pytest.raises(ConfigError, match="valid run ids: active-a, active-b"):
        resolve_supervision_run(runs, "mistyped")

    (runs / "active-b" / "launch.json").write_text(
        json.dumps(_launch_record("shared", 999_999_999, "active-b")),
        encoding="utf-8",
    )
    assert resolve_supervision_run(runs, "shared") == "active-a"

    named = runs / "named"
    named.mkdir()
    named_launch = _launch_record("plan with spaces", run_id="named")
    (named / "launch.json").write_text(json.dumps(named_launch), encoding="utf-8")
    assert resolve_supervision_run(runs, "plan with spaces") == "named"


def test_launch_activity_handles_finished_remote_invalid_and_unreadable_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = tmp_path / "run"
    run.mkdir()
    local = _launch_record("local")
    (run / "launch.json").write_text(json.dumps(local), encoding="utf-8")
    assert launch_may_be_active(run)
    assert launch_may_be_active(run, {"pid": 123, "host": "remote-host"})
    assert not launch_may_be_active(run, {"pid": "bad", "host": socket.gethostname()})

    monkeypatch.setattr(
        "orchestrator.runs.os.kill",
        lambda pid, signal: (_ for _ in ()).throw(PermissionError()),
    )
    assert launch_may_be_active(run, {"pid": 123, "host": socket.gethostname()})

    report = run / "orchestrator" / "report.json"
    report.parent.mkdir()
    report.write_text('{"completed":true}', encoding="utf-8")
    assert not launch_may_be_active(run, local)
    report.write_text("invalid", encoding="utf-8")
    assert launch_may_be_active(run, local)
