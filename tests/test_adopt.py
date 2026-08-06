"""Adopting an orphaned run: the record it replays, and the refusals that guard it.

The journey itself — kill a driver mid-round, adopt, drive the run to completion on
its original ledger — runs against the real CLI in
`tests/e2e/test_run_adoption_e2e.py`. What lives here is the trust boundary that
journey cannot produce on demand: a relaunch record hand-edited into every shape the
reader must refuse, and the two ownership/liveness states an adoption is refused for.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from orchestrator.adopt import (
    RELAUNCH_SCHEMA_VERSION,
    RelaunchRecord,
    read_relaunch_record,
    relaunch_path,
    write_relaunch_record,
)
from orchestrator.config import ConfigError
from orchestrator.dispatch import DispatchError, adopt_orchestrator, launch_orchestrator

SESSION = "session-adopting"


def _valid(tmp_path: Path) -> RelaunchRecord:
    return {
        "schema_version": RELAUNCH_SCHEMA_VERSION,
        "plan": str(tmp_path / "plan.json"),
        "base_path": str(tmp_path / "base.yaml"),
        "onejudge_bin": "onejudge",
        "cwd": str(tmp_path),
        "max_turns": 100,
        "turn_timeout": 86400,
        "heartbeat_interval": 1800.0,
        "acknowledge_concurrent": False,
        "oneharness_mode": "bypass",
        "adoptions": 0,
    }


def test_relaunch_record_round_trips_every_optional_field(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    record: RelaunchRecord = {
        **_valid(tmp_path),
        "round_budget": 90.0,
        "worker_harness": "codex",
        "judge_harness": "claude-code:alternate",
        "skill_provider": {"kind": "command", "command": ["fake-provider"]},
    }
    write_relaunch_record(run_dir, record)

    assert read_relaunch_record(run_dir) == record


def test_relaunch_record_is_absent_before_adoption_existed(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="no relaunch record"):
        read_relaunch_record(tmp_path / "run")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schema_version", 99, "schema version"),
        ("plan", "", "'plan'"),
        ("plan", 7, "'plan'"),
        ("cwd", "with\x00nul", "'cwd'"),
        ("max_turns", -1, "'max_turns'"),
        ("max_turns", True, "'max_turns'"),
        ("turn_timeout", "soon", "'turn_timeout'"),
        ("heartbeat_interval", 0, "'heartbeat_interval'"),
        ("heartbeat_interval", float("nan"), "'heartbeat_interval'"),
        ("oneharness_mode", None, "'oneharness_mode'"),
        ("adoptions", 1.5, "'adoptions'"),
        ("round_budget", -3, "'round_budget'"),
        ("worker_harness", 4, "'worker_harness'"),
        ("judge_harness", "", "'judge_harness'"),
        ("skill_provider", ["kind"], "'skill_provider'"),
    ],
)
def test_relaunch_record_refuses_a_field_it_cannot_replay(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    """A record an adoption cannot fully understand stops it, rather than defaulting.

    Every one of these decides how a second live orchestrator process is started
    against work the first one left behind, so a value that is missing, malformed, or
    from another schema must refuse rather than fall back to whatever this build's
    default happens to be.
    """
    run_dir = tmp_path / "run"
    raw: dict[str, Any] = {**_valid(tmp_path), field: value}
    relaunch_path(run_dir).parent.mkdir(parents=True)
    relaunch_path(run_dir).write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ConfigError, match=message):
        read_relaunch_record(run_dir)


def _launch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, session: str | None) -> Path:
    """Launch one run with a fake process and return its run directory."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("ONEHARNESS_MODE", raising=False)
    plan = tmp_path / "plan.json"
    plan.write_text(
        '{"schema_version":3,"tasks":[{"id":"approval","kind":"human","task":"approve"}]}',
        encoding="utf-8",
    )

    class Process:
        pid = 4321

    monkeypatch.setattr("orchestrator.dispatch.subprocess.Popen", lambda *_a, **_k: Process())
    monkeypatch.setattr(
        "orchestrator.dispatch._resolve_onejudge",
        lambda binary, _env: {"path": str(Path(binary).resolve()), "version": "0.3.4"},
    )
    runs = tmp_path / "runs"
    run_id = launch_orchestrator(
        plan,
        runs_dir=runs,
        run_id="demo",
        skill_provider={"kind": "command", "command": ["fake-provider"]},
        launcher="claude-code" if session else None,
        launcher_session_id=session,
    )
    return runs / run_id


def _as_session(monkeypatch: pytest.MonkeyPatch, session: str | None) -> None:
    """Make this process look like the named planner session, or like none."""
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    if session is None:
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
        monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
        return
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", session)


def _driver_is_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("orchestrator.runs.process_may_be_live", lambda _pid, _host: False)


def test_adoption_refuses_a_run_this_session_did_not_launch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_dir = _launch(monkeypatch, tmp_path, "session-other")
    _as_session(monkeypatch, SESSION)
    _driver_is_gone(monkeypatch)

    with pytest.raises(DispatchError, match="another planner"):
        adopt_orchestrator(run_dir.name, runs_dir=run_dir.parent)


def test_adoption_refuses_a_run_nobody_can_attribute(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unknown is never mine: an unattributable run belongs to another planner."""
    run_dir = _launch(monkeypatch, tmp_path, None)
    _as_session(monkeypatch, SESSION)
    _driver_is_gone(monkeypatch)

    with pytest.raises(DispatchError, match="no recorded launcher"):
        adopt_orchestrator(run_dir.name, runs_dir=run_dir.parent)


def test_adoption_refuses_a_run_whose_driver_is_still_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_dir = _launch(monkeypatch, tmp_path, SESSION)
    _as_session(monkeypatch, SESSION)
    monkeypatch.setattr("orchestrator.runs.process_may_be_live", lambda _pid, _host: True)

    with pytest.raises(DispatchError, match="orchestrator process is still running"):
        adopt_orchestrator(run_dir.name, runs_dir=run_dir.parent)


def test_adoption_refuses_a_run_whose_round_is_still_in_flight(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A dead launcher with a live round is still a run something is driving."""
    run_dir = _launch(monkeypatch, tmp_path, SESSION)
    _as_session(monkeypatch, SESSION)
    round_dir = run_dir / "round-01"
    round_dir.mkdir()
    (round_dir / "status.json").write_text(
        json.dumps({"status": "running", "pid": 4321, "host": "elsewhere"}), encoding="utf-8"
    )
    monkeypatch.setattr(
        "orchestrator.runs.process_may_be_live",
        lambda _pid, host: host == "elsewhere",
    )

    with pytest.raises(DispatchError, match="round-01 is still in flight"):
        adopt_orchestrator(run_dir.name, runs_dir=run_dir.parent)


def test_adoption_refuses_a_run_that_does_not_exist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _as_session(monkeypatch, SESSION)
    with pytest.raises(DispatchError, match="no recorded run"):
        adopt_orchestrator("missing", runs_dir=tmp_path / "runs")


def test_adoption_refuses_a_run_whose_plan_is_gone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_dir = _launch(monkeypatch, tmp_path, SESSION)
    _as_session(monkeypatch, SESSION)
    _driver_is_gone(monkeypatch)
    (tmp_path / "plan.json").unlink()

    with pytest.raises(DispatchError, match="recorded plan no longer exists"):
        adopt_orchestrator(run_dir.name, runs_dir=run_dir.parent)


def test_adoption_refuses_a_run_with_no_relaunch_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A run launched before adoption existed has nothing to replay."""
    run_dir = _launch(monkeypatch, tmp_path, SESSION)
    _as_session(monkeypatch, SESSION)
    _driver_is_gone(monkeypatch)
    relaunch_path(run_dir).unlink()

    with pytest.raises(DispatchError, match="cannot adopt demo: no relaunch record"):
        adopt_orchestrator(run_dir.name, runs_dir=run_dir.parent)


def test_adoption_starts_a_fresh_conversation_and_preserves_the_dead_drivers_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The second driver never resumes the first one's session, and never buries it.

    Resuming is the failure this whole path exists to end — a relaunch that asks the
    harness for a conversation it no longer has loops on "No conversation found" — so
    the adopted process takes a session of its own. Its predecessor's report and
    stderr move aside rather than being truncated, because they are the evidence of
    how it died and the first thing a planner reads afterwards.
    """
    run_dir = _launch(monkeypatch, tmp_path, SESSION)
    (run_dir / "orchestrator" / "report.json").write_text("{}\n", encoding="utf-8")
    (run_dir / "orchestrator" / "stderr.log").write_text("quota exhausted\n", encoding="utf-8")
    _as_session(monkeypatch, SESSION)
    _driver_is_gone(monkeypatch)

    captured: dict[str, Any] = {}

    class Process:
        pid = 9876

    def fake_popen(command: list[str], **kwargs: Any) -> Process:
        captured["command"] = list(command)
        return Process()

    monkeypatch.setattr("orchestrator.dispatch.subprocess.Popen", fake_popen)

    adopted = adopt_orchestrator(run_dir.name, runs_dir=run_dir.parent)

    assert adopted == run_dir.name
    effective = (run_dir / "orchestrator" / "effective.onejudge.yaml").read_text(encoding="utf-8")
    assert f"session: orchestrator-{run_dir.name}-adopt1" in effective
    task = captured["command"][captured["command"].index("--task") + 1]
    assert "--recover" in task
    assert "Do not mint a new run id." in task
    assert (run_dir / "orchestrator" / "report.pre-adopt-1.json").read_text(
        encoding="utf-8"
    ) == "{}\n"
    assert "quota exhausted" in (run_dir / "orchestrator" / "stderr.pre-adopt-1.log").read_text(
        encoding="utf-8"
    )
    # The generation is durable, so a second adoption cannot overwrite this one's
    # evidence or reuse its conversation.
    assert read_relaunch_record(run_dir)["adoptions"] == 1
    assert (
        json.loads((run_dir / "orchestrator" / "status.json").read_text(encoding="utf-8"))["pid"]
        == 9876
    )
