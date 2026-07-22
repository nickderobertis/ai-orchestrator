from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.config import ConfigError
from orchestrator.goals import (
    finish_run,
    graph_identities,
    main,
    register_run,
    sweep_and_list_active_runs,
    update_run_owner,
)
from orchestrator.registry import Registry


def test_index_lifecycle_sweeps_dead_owner_and_transfers_live_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(state))
    dead_dir = tmp_path / "dead"
    live_dir = tmp_path / "live"
    state.mkdir()
    (state / "runs-index.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "runs": {
                    "dead": {
                        "run_id": "dead",
                        "run_dir": str(dead_dir),
                        "goal": None,
                        "identities": ["shared"],
                        "pid": 999_999_999,
                        "host": socket.gethostname(),
                        "started": "earlier",
                        "status": "active",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    assert sweep_and_list_active_runs() == []
    assert (
        register_run(
            run_id="live",
            run_dir=live_dir,
            goal={"id": "ship", "text": "Ship safely"},
            identities=["shared"],
            pid=os.getpid(),
            acknowledge_concurrent=False,
        )
        == []
    )
    update_run_owner("live", live_dir, os.getpid())
    assert sweep_and_list_active_runs()[0]["pid"] == os.getpid()
    finish_run("live", live_dir)
    assert sweep_and_list_active_runs() == []


def test_live_overlap_requires_ack_and_reader_lists_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(tmp_path / "state"))
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    register_run(
        run_id="first",
        run_dir=first_dir,
        goal={"id": "one", "text": "First goal"},
        identities=["identity"],
        pid=os.getpid(),
        acknowledge_concurrent=False,
    )
    with pytest.raises(ConfigError, match="first.*First goal.*identity"):
        register_run(
            run_id="second",
            run_dir=second_dir,
            goal=None,
            identities=["identity"],
            pid=os.getpid(),
            acknowledge_concurrent=False,
        )
    acknowledgements = register_run(
        run_id="second",
        run_dir=second_dir,
        goal=None,
        identities=["identity"],
        pid=os.getpid(),
        acknowledge_concurrent=True,
    )
    assert acknowledgements[0]["runs"] == ["first"]
    assert main([]) == 0
    output = capsys.readouterr().out
    assert "First goal" in output
    assert "(no goal)" in output
    assert str(second_dir.resolve()) in output


def test_report_sweep_and_invalid_or_missing_index_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(state))
    assert main([]) == 0
    assert "No active DAG goals" in capsys.readouterr().out

    run_dir = tmp_path / "reported"
    register_run(
        run_id="reported",
        run_dir=run_dir,
        goal=None,
        identities=[],
        pid=os.getpid(),
        acknowledge_concurrent=False,
    )
    report = run_dir / "orchestrator" / "report.json"
    report.parent.mkdir(parents=True)
    report.write_text("{}\n", encoding="utf-8")
    assert sweep_and_list_active_runs() == []

    (state / "runs-index.json").write_text('{"schema_version":99,"runs":{}}', encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid runs index"):
        sweep_and_list_active_runs()
    (state / "runs-index.json").write_text(
        '{"schema_version":1,"runs":{"broken":{"run_id":"broken"}}}', encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="invalid runs index entry.*broken"):
        sweep_and_list_active_runs()
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", " ")
    with pytest.raises(ValueError, match="must not be empty"):
        sweep_and_list_active_runs()


def test_owner_transfer_rejects_missing_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(tmp_path / "state"))
    with pytest.raises(ConfigError, match="reservation disappeared"):
        update_run_owner("missing", tmp_path / "missing", os.getpid())


def test_identity_enumeration_uses_only_lifecycle_nodes(tmp_path: Path) -> None:
    graph = SimpleNamespace(
        tasks=[
            SimpleNamespace(lifecycle=object(), repo="Example/Widget"),
            SimpleNamespace(lifecycle=object(), repo="https://github.com/example/widget.git"),
            SimpleNamespace(lifecycle=None, repo=None),
        ]
    )
    registry = Registry(tmp_path / "repos.json")

    assert graph_identities(graph, registry) == ["https://github.com/example/widget"]


def test_same_run_registration_preserves_owner_and_ack_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(tmp_path / "state"))
    run_dir = tmp_path / "run"
    register_run(
        run_id="run",
        run_dir=run_dir,
        goal=None,
        identities=["one"],
        pid=os.getpid(),
        acknowledge_concurrent=False,
    )
    repeated = register_run(
        run_id="run",
        run_dir=run_dir,
        goal=None,
        identities=["one"],
        pid=999_999_999,
        acknowledge_concurrent=False,
    )
    assert repeated == []
    assert sweep_and_list_active_runs()[0]["pid"] == os.getpid()
    finish_run("run", tmp_path / "different")
    assert len(sweep_and_list_active_runs()) == 1

    other = register_run(
        run_id="other",
        run_dir=tmp_path / "other",
        goal=None,
        identities=["unshared"],
        pid=os.getpid(),
        acknowledge_concurrent=False,
    )
    assert other == []
