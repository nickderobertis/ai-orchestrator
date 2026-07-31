from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.config import ConfigError
from orchestrator.goals import (
    concurrent_indicator,
    concurrent_runs,
    finish_run,
    graph_identities,
    main,
    register_run,
    sweep_and_list_active_runs,
    update_run_owner,
)
from orchestrator.liveness import PARKED_AFTER_SECONDS
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
    with pytest.raises(ConfigError, match="'first' is LIVE.*First goal.*identity"):
        register_run(
            run_id="second",
            run_dir=second_dir,
            goal=None,
            identities=["identity"],
            pid=os.getpid(),
            acknowledge_concurrent=False,
        )
    notices: list[str] = []
    acknowledgements = register_run(
        run_id="second",
        run_dir=second_dir,
        goal=None,
        identities=["identity"],
        pid=os.getpid(),
        acknowledge_concurrent=True,
        report=notices.append,
    )
    assert acknowledgements[0]["runs"] == ["first"]
    # Acknowledging gets past the guard; it never makes the live neighbour invisible.
    assert notices == [
        f"proceeding alongside a live concurrent run — {concurrent_runs(second_dir)[0].describe()}"
        "; inspect it with: just monitor first"
    ]
    assert "CONCURRENT: run 'first' is LIVE" in str(concurrent_indicator(second_dir))
    assert main([]) == 0
    output = capsys.readouterr().out
    assert "First goal" in output
    assert "(no goal)" in output
    assert str(second_dir.resolve()) in output


def test_a_parked_launch_is_not_reported_as_live_company(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Holding a pid is not working, and the guard's report has to say which it is.

    A launched orchestrator can keep its pid while doing nothing observable, and
    reporting that as a second run *at work* on the identity is the same misreading
    the parked indicator exists to prevent one layer down.
    """
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(tmp_path / "state"))
    # A real process that holds a pid and does nothing: no child of its own, and a
    # launch record whose every stamp predates the threshold. That is what a parked
    # orchestrator looks like from outside.
    owner = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    parked_dir = tmp_path / "parked"
    (parked_dir / "orchestrator").mkdir(parents=True)
    (parked_dir / "launch.json").write_text(json.dumps({"run_id": "parked"}), encoding="utf-8")
    (parked_dir / "orchestrator" / "status.json").write_text(
        json.dumps({"status": "running", "pid": owner.pid, "host": socket.gethostname()}),
        encoding="utf-8",
    )
    stale = time.time() - PARKED_AFTER_SECONDS * 4
    for path in (parked_dir / "launch.json", parked_dir / "orchestrator" / "status.json"):
        os.utime(path, (stale, stale))
    try:
        register_run(
            run_id="parked",
            run_dir=parked_dir,
            goal={"id": "parked", "text": "A launch that stopped working"},
            identities=["identity"],
            pid=owner.pid,
            acknowledge_concurrent=False,
        )

        notices: list[str] = []
        with pytest.raises(ConfigError, match=r"holds pid \d+ on .* but shows no progress"):
            register_run(
                run_id="next",
                run_dir=tmp_path / "next",
                goal=None,
                identities=["identity"],
                pid=os.getpid(),
                acknowledge_concurrent=False,
                report=notices.append,
            )
        register_run(
            run_id="next",
            run_dir=tmp_path / "next",
            goal=None,
            identities=["identity"],
            pid=os.getpid(),
            acknowledge_concurrent=True,
            report=notices.append,
        )

        # Acknowledged, and still no claim of a live neighbour: there is not one.
        assert notices == []
        assert concurrent_indicator(tmp_path / "next") is None
    finally:
        owner.kill()
        owner.wait(timeout=10)


def test_an_unobservable_registration_is_never_reported_as_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every registration this host cannot rule on reads as exactly that.

    Another host's entry and an owner whose pid the kernel says is gone are both
    residue rather than work. Saying so is what lets a planner tell the run it must
    not collide with from the one it may launch past.
    """
    state = tmp_path / "state"
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(state))
    state.mkdir()
    reader = tmp_path / "reader"
    owners = {
        "elsewhere": (4242, "a-host-that-is-not-this-one"),
        # On this host with a pid the kernel says is gone. `concurrent_runs` never
        # rewrites the index, so an entry no sweep has retired yet is classified here.
        "departed": (999_999_999, socket.gethostname()),
        "reader": (os.getpid(), socket.gethostname()),
    }
    (state / "runs-index.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "runs": {
                    name: {
                        "run_id": name,
                        "run_dir": str(reader if name == "reader" else tmp_path / name),
                        "goal": None,
                        "identities": ["shared"],
                        "pid": pid,
                        "host": host,
                        "started": "earlier",
                        "status": "active",
                    }
                    for name, (pid, host) in owners.items()
                },
            }
        ),
        encoding="utf-8",
    )

    found = {run.run_id: run for run in concurrent_runs(reader)}

    assert set(found) == {"elsewhere", "departed"}
    assert all(run.state == "unobservable" for run in found.values())
    assert "is registered but not observable here (recorded owner pid 4242 on " in (
        found["elsewhere"].describe()
    )
    assert "goal '(no goal)'; shared identities: shared" in found["elsewhere"].describe()
    # None of them is company a progress view should report as work in flight.
    assert concurrent_indicator(reader) is None

    # An owner this user may not signal is still an owner that exists: pid 1 belongs
    # to another user here, and "I may not ask" is not "it is gone". Resolving that
    # toward still-running is the same asymmetry every liveness probe here keeps.
    indexed = json.loads((state / "runs-index.json").read_text(encoding="utf-8"))
    indexed["runs"]["departed"]["pid"] = 1
    (state / "runs-index.json").write_text(json.dumps(indexed), encoding="utf-8")
    assert {run.run_id: run.state for run in concurrent_runs(reader)}["departed"] == "live"

    # An index this build cannot parse leaves the view silent rather than failing it:
    # the concurrency line is an extra, never a reason a planner loses `just status`.
    (state / "runs-index.json").write_text('{"schema_version":99,"runs":{}}', encoding="utf-8")
    assert concurrent_indicator(reader) is None


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
    report.write_text("not json\n", encoding="utf-8")
    assert len(sweep_and_list_active_runs()) == 1
    report.write_text("[]\n", encoding="utf-8")
    assert len(sweep_and_list_active_runs()) == 1
    report.write_text(
        json.dumps(
            {
                "schema_version": 5,
                "transcript": {"messages": []},
                "stopped_early": False,
            }
        ),
        encoding="utf-8",
    )
    assert sweep_and_list_active_runs() == []

    (state / "runs-index.json").write_text('{"schema_version":99,"runs":{}}', encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid runs index"):
        sweep_and_list_active_runs()
    (state / "runs-index.json").write_text(
        '{"schema_version":1,"runs":{"broken":{"run_id":"broken"}}}', encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="invalid runs index entry.*broken"):
        sweep_and_list_active_runs()
    (state / "runs-index.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "runs": {
                    "relative": {
                        "run_id": "relative",
                        "run_dir": "relative/path",
                        "goal": None,
                        "identities": [],
                        "pid": os.getpid(),
                        "host": socket.gethostname(),
                        "started": "earlier",
                        "status": "active",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="invalid runs index entry.*relative"):
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


def test_remote_owner_reservation_is_retained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(state))
    state.mkdir()
    run_dir = tmp_path / "remote"
    (state / "runs-index.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "runs": {
                    "remote": {
                        "run_id": "remote",
                        "run_dir": str(run_dir),
                        "goal": None,
                        "identities": ["shared"],
                        "pid": 999_999_999,
                        "host": "other-host",
                        "started": "earlier",
                        "status": "active",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    assert sweep_and_list_active_runs()[0]["run_id"] == "remote"


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
