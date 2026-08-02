"""Real journeys for the two commands that attach to a run and return when it settles.

`just orchestrate` runs in the foreground here — a real launch, the real onejudge
split provider, the real channel — and the assertion is what a planner actually
gets: the launch record, then the same stream `just monitor` prints, then a return
at the moment the run stops advancing on its own. `--detach` is the same launch
with none of the waiting, and it is proven by what happens *after* it returns: the
run reaches its planner surface anyway.

The settlements themselves are `monitor.SETTLEMENTS`, and both commands reach them
through one function (`monitor.attach`), so each one is driven here through the
command a planner would type. Two of them are cheap to build honestly — a graph
that completed, and a round whose owner this host can prove is gone — and the
blocking-surface one comes from a real orchestrator asking a real question.

Parked is the one settlement no journey here can reach through `just orchestrate`,
and not for want of trying: the run it attaches to is the one it just created, and
a live launch always has either a live descendant or a written report. It is
proven against a real parked launch through the same `--until-settled` code in
tests/e2e/test_liveness_e2e.py.
"""

# llmlint: ignore-file[e2e_not_mocked] Only the paid model is a double — the deterministic
# protocol backend this suite already dispatches through (tests/e2e/fake_backend.py). The
# recipes, onejudge, the channel, the ledger, the run journal, and process liveness are real.

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

import pytest
import yaml
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator import BASE_CONFIG, REPO_ROOT
from orchestrator.journal import open_journal
from orchestrator.monitor import HEADER
from orchestrator.runs import NodeId, RunId, prepare_round, write_result

FAKE_BACKEND = REPO_ROOT / "tests" / "e2e" / "fake_backend.py"

#: What `SETTLED_UNATTENDED` exits with. Spelled out rather than imported, because
#: the status is the contract a scripted launch reads and a test that imported the
#: number could not notice it changing.
UNATTENDED_STATUS = 3


def _base(tmp_path: Path) -> Path:
    """A base config whose worker provider is the deterministic protocol backend."""
    value = yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8"))
    value["provider"] = {"kind": "command", "command": [sys.executable, str(FAKE_BACKEND)]}
    path = tmp_path / "base.yaml"
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    return path


def _plan(tmp_path: Path, name: str) -> Path:
    path = tmp_path / f"plan-{name}.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "name": name,
                "tasks": [{"id": "worker", "persona": "engineer", "task": "complete-now attach"}],
            }
        ),
        encoding="utf-8",
    )
    return path


def _orchestrate(
    plan: Path,
    runs: Path,
    base: Path,
    onejudge_bin: str,
    *extra: str,
    skill: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "just",
            "orchestrate",
            str(plan),
            "--runs-dir",
            str(runs),
            "--base",
            str(base),
            "--onejudge-bin",
            onejudge_bin,
            *extra,
            "--skill-command",
            *(skill or [sys.executable, str(FAKE_BACKEND)]),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
    )


def _launched_run(runs: Path) -> Path:
    directories = [entry for entry in runs.iterdir() if entry.is_dir()]
    assert len(directories) == 1, directories
    return directories[0]


def _owner_pid(run_dir: Path) -> int:
    status = run_dir / "orchestrator" / "status.json"
    wait = deadline(30)
    while not status.is_file() and time.monotonic() < wait:
        time.sleep(0.02)
    assert status.is_file(), f"the launch never recorded its owner: {run_dir}"
    return int(json.loads(status.read_text(encoding="utf-8"))["pid"])


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@pytest.fixture
def reaped(tmp_path: Path) -> list[Path]:
    """Kill every launch this test started; each leads its own session by design."""
    started: list[Path] = []
    yield started
    for run_dir in started:
        status = run_dir / "orchestrator" / "status.json"
        if not status.is_file():
            continue
        pid = json.loads(status.read_text(encoding="utf-8"))["pid"]
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(pid, signal.SIGKILL)
        reap = deadline(30)
        while time.monotonic() < reap:
            if not _alive(pid):
                break
            time.sleep(0.02)


def _monitor(runs: Path, run_id: str, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["just", "monitor", run_id, "--runs-dir", str(runs), *extra],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
    )


# --- the foreground launch -----------------------------------------------------


def test_orchestrate_stays_attached_and_returns_when_the_run_needs_the_planner(
    tmp_path: Path, onejudge_bin: str, reaped: list[Path]
) -> None:
    """The default launch watches its own run and hands it back at the first question.

    This is the whole point of the inversion: no `&`, no `pgrep`, no second command
    to find the run again. What the planner gets is the launch record, the monitor
    stream, and a return the moment the orchestrator asks something only they can
    answer — with the run still alive behind it, which the pid check proves.
    """
    runs = tmp_path / "runs"
    attached = _orchestrate(_plan(tmp_path, "attached"), runs, _base(tmp_path), onejudge_bin)
    run_dir = _launched_run(runs)
    reaped.append(run_dir)

    assert attached.returncode == 0, attached.stderr
    # The record still comes first and still parses, ahead of the stream's own
    # header: `--detach`'s consumers and this one read the same launch record.
    record, header, followed = attached.stdout.partition(f"{HEADER}\n")
    assert header == f"{HEADER}\n", attached.stdout
    assert json.loads(record)["run_id"] == run_dir.name
    lines = followed.splitlines()
    assert f"attached to {run_dir.name}" in attached.stderr
    assert "Ctrl-C detaches" in attached.stderr
    assert "settled, awaiting the planner" in lines[-1], attached.stdout
    assert "ACK REQUIRED" in lines[-1]
    # Returning is not stopping: the orchestrator is still there holding the
    # question, which is what makes `just channel-reply` the next thing to run.
    assert _alive(_owner_pid(run_dir))
    assert (run_dir / "channel" / "planner-pending.json").is_file()


def test_detach_returns_at_the_launch_record_and_the_run_carries_on(
    tmp_path: Path, onejudge_bin: str, reaped: list[Path]
) -> None:
    """`--detach` is the previous behaviour, and the run proves it kept running."""
    runs = tmp_path / "runs"
    launched = _orchestrate(
        _plan(tmp_path, "detached"), runs, _base(tmp_path), onejudge_bin, "--detach"
    )
    run_dir = _launched_run(runs)
    reaped.append(run_dir)

    assert launched.returncode == 0, launched.stderr
    # The whole of stdout is the record: nothing streamed, so a caller that parses
    # it as JSON — as every launcher does — is unaffected by the new default.
    assert json.loads(launched.stdout)["run_id"] == run_dir.name
    assert HEADER not in launched.stdout
    assert "attached to" not in launched.stderr

    pending = run_dir / "channel" / "planner-pending.json"
    wait = deadline(180)
    while not pending.is_file() and time.monotonic() < wait:
        assert _alive(_owner_pid(run_dir)), "the detached launch died"
        time.sleep(0.05)
    assert pending.is_file(), "the detached run never reached its planner surface"


def test_the_foreground_launch_returns_when_nothing_is_left_driving_the_run(
    tmp_path: Path, onejudge_bin: str, reaped: list[Path]
) -> None:
    """A launch whose orchestrator dies at once returns non-zero instead of waiting.

    The skill provider here exits without speaking the protocol, so the launched
    process is gone before the first poll. That is the *already settled* case for
    this path — the attach must recognise a run that is over rather than follow a
    stream nothing will ever add to — and it is reported as the failure it is.
    """
    dead = tmp_path / "dead-provider.py"
    dead.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    runs = tmp_path / "runs"
    attached = _orchestrate(
        _plan(tmp_path, "unattended"),
        runs,
        _base(tmp_path),
        onejudge_bin,
        skill=[sys.executable, str(dead)],
    )
    run_dir = _launched_run(runs)
    reaped.append(run_dir)

    assert attached.returncode == UNATTENDED_STATUS, attached.stdout + attached.stderr
    _, _, followed = attached.stdout.partition(f"{HEADER}\n")
    assert "settled, nothing is driving this run" in followed.splitlines()[-1]
    assert not _alive(_owner_pid(run_dir))


# --- the same endings through `just monitor` -----------------------------------


def _recorded_run(runs: Path, run_id: RunId, *, ok: bool, state: str, status: str) -> Path:
    """One real recorded round, settled through the ledger's own writers."""
    run_dir = runs / run_id
    journal = open_journal(run_dir, run_id, 1)
    journal.append("node-started", node=NodeId("api"), detail={"persona": "engineer"})
    journal.append("node-settled", node=NodeId("api"), detail={"status": status})
    claimed = prepare_round(run_dir, {"tasks": [{"id": "api", "persona": "engineer", "task": "x"}]})
    write_result(
        claimed.directory,
        {"ok": ok, "state": state, "started_order": ["api"], "results": {"api": {"status": status}}},
    )
    return run_dir


def test_monitor_until_settled_returns_on_a_run_that_is_already_complete(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    _recorded_run(runs, RunId("already-done"), ok=True, state="complete", status="done")

    settled = _monitor(runs, "already-done", "--until-settled", "--format", "jsonl")
    assert settled.returncode == 0, settled.stderr
    last = json.loads(settled.stdout.splitlines()[-1])
    assert (last["state"], last["detail"]) == ("complete", "graph complete")
    assert last["settlement"] == "complete"


def test_monitor_until_settled_returns_when_the_rounds_owner_is_gone(
    tmp_path: Path,
) -> None:
    """A claimed round whose owner this host proved dead is settled, not in flight."""
    runs = tmp_path / "runs"
    run_dir = runs / "abandoned"
    open_journal(run_dir, RunId("abandoned"), 1).append("node-started", node=NodeId("api"))
    claimed = prepare_round(run_dir, {"tasks": [{"id": "api", "persona": "engineer", "task": "x"}]})
    # A pid this host can prove is gone: a real child, reaped before the read.
    corpse = subprocess.Popen([sys.executable, "-c", ""])
    corpse.wait(timeout=e2e_timeout(30))
    status = json.loads((claimed.directory / "status.json").read_text(encoding="utf-8"))
    (claimed.directory / "status.json").write_text(
        json.dumps({**status, "pid": corpse.pid}), encoding="utf-8"
    )

    settled = _monitor(runs, "abandoned", "--until-settled", "--format", "jsonl")
    assert settled.returncode == UNATTENDED_STATUS, settled.stderr
    last = json.loads(settled.stdout.splitlines()[-1])
    assert last["settlement"] == "unattended"
    assert "settled, nothing is driving this run" in last["detail"]


def test_monitor_until_settled_keeps_following_a_surface_the_run_does_not_wait_on(
    tmp_path: Path,
) -> None:
    """Only a *blocking* surface settles: the orchestrator continues past the others.

    A heartbeat update is persisted the same way and reads the same to every view,
    so an attach that returned on it would walk away from a run that is still
    working. It keeps following, and then settles the moment a surface the run is
    actually blocked on replaces it.
    """
    runs = tmp_path / "runs"
    run_dir = _recorded_run(runs, RunId("still-working"), ok=False, state="waiting", status="waiting")
    pending = run_dir / "channel" / "planner-pending.json"
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text(
        json.dumps({"kind": "heartbeat", "message": "round one still running", "blocking": False}),
        encoding="utf-8",
    )
    following = subprocess.Popen(
        [
            "just",
            "monitor",
            "still-working",
            "--runs-dir",
            str(runs),
            "--until-settled",
            "--format",
            "jsonl",
            "--heartbeat",
            "0.01",
            "--poll-interval",
            "0.05",
            "--max-poll-interval",
            "0.05",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert following.stdout is not None
        beats = []
        while len(beats) < 3:
            record = json.loads(following.stdout.readline())
            if record["type"] == "heartbeat":
                beats.append(record)
        assert all("settlement" not in beat for beat in beats), beats
        assert following.poll() is None
        # The same file, now blocking: the run is waiting on the planner and the
        # attach hands it back.
        pending.write_text(
            json.dumps({"kind": "blocker", "message": "decide", "blocking": True}),
            encoding="utf-8",
        )
        assert following.wait(timeout=e2e_timeout(60)) == 0
        last = json.loads(following.stdout.read().strip().splitlines()[-1])
        assert last["settlement"] == "awaiting-planner"
        assert "settled, awaiting the planner" in last["detail"]
    finally:
        if following.poll() is None:
            following.kill()
        following.wait(timeout=e2e_timeout(30))
