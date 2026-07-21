from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from orchestrator import REPO_ROOT
from orchestrator.dispatch import DispatchError, launch_orchestrator, main_orchestrate


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


@pytest.mark.parametrize("interval", [0, -1, float("inf"), float("nan")])
def test_launch_rejects_invalid_heartbeat_interval(tmp_path: Path, interval: float) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(
        '{"schema_version":3,"tasks":[{"id":"approval","kind":"human","task":"approve"}]}',
        encoding="utf-8",
    )
    with pytest.raises(DispatchError, match="positive, finite"):
        launch_orchestrator(
            plan,
            runs_dir=tmp_path / "runs",
            run_id=f"invalid-{str(interval).replace('.', '-')}",
            heartbeat_interval=interval,
        )


def test_orchestrate_cli_prints_run_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text("{}", encoding="utf-8")
    received: dict[str, object] = {}

    def fake_launch(path: Path, **kwargs: object) -> str:
        received.update({"path": path, **kwargs})
        run = tmp_path / "runs" / "live-run"
        run.mkdir(parents=True)
        (run / "launch.json").write_text(
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
