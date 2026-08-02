"""Real journeys for the two commands that attach to a run and return when it settles.

`just orchestrate` runs in the foreground here — a real launch, the real onejudge
split provider, the real channel — and the assertion is what a planner actually
gets: the launch record, then the same stream `just monitor` prints, then a return
at the moment the run stops advancing on its own. `--detach` is the same launch
with none of the waiting, and it is proven by what happens *after* it returns: the
run reaches its planner surface anyway.

The settlements themselves are `monitor.SETTLEMENTS`, and both commands reach them
through one function (`monitor.attach`), so each one is driven here through the
commands a planner would type and nothing else: `just orchestrate` launches, `just
channel-next` and `just channel-reply` answer, `just stop` ends. No run state here
is manufactured — every ledger, journal, and channel record these journeys read was
written by the run itself.

Parked is reached the way a run really parks, which this command can only do to the
run it just launched: its provider wedges the real orchestrator, so the launch keeps
its pid and its `running` claim while collecting nothing, spawning nothing, and
recording nothing. `just monitor --until-settled` is held to the same answer for the
same state in tests/e2e/test_liveness_e2e.py.
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
from typing import NamedTuple

import pytest
import yaml
from rendezvous import Rendezvous
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator import BASE_CONFIG, REPO_ROOT
from orchestrator.liveness import has_live_descendant
from orchestrator.monitor import HEADER, SETTLED_UNATTENDED, SETTLEMENTS

FAKE_BACKEND = REPO_ROOT / "tests" / "e2e" / "fake_backend.py"

#: What `SETTLED_UNATTENDED` exits with. Read from the mapping that defines it;
#: `tests/test_monitor.py` is where that mapping's values are pinned, so the status
#: a scripted launch reads has one source and one drift gate rather than a literal
#: restated here.
UNATTENDED_STATUS = SETTLEMENTS[SETTLED_UNATTENDED]


def _base(tmp_path: Path) -> Path:
    """A base config whose worker provider is the deterministic protocol backend."""
    value = yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8"))
    value["provider"] = {"kind": "command", "command": [sys.executable, str(FAKE_BACKEND)]}
    path = tmp_path / "base.yaml"
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    return path


def _plan(tmp_path: Path, name: str, *, task: str = "complete-now attach") -> Path:
    path = tmp_path / f"plan-{name}.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "name": name,
                "tasks": [{"id": "worker", "persona": "engineer", "task": task}],
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


class Attached(NamedTuple):
    """One foreground `just orchestrate` still attached, and the streams it writes.

    Its output goes to files rather than pipes for the reason a planner's terminal
    does: the stream is written for the whole life of the attachment, and a journey
    that acted on the run while draining a pipe would be racing its own reader.
    """

    process: subprocess.Popen[str]
    out: Path
    err: Path

    def settled(self, seconds: float) -> tuple[int, str, str]:
        """Wait for the attachment to hand the run back, with everything it printed."""
        status = self.process.wait(timeout=e2e_timeout(seconds))
        return status, self.out.read_text(encoding="utf-8"), self.err.read_text(encoding="utf-8")


@pytest.fixture
def attachments() -> list[subprocess.Popen[str]]:
    """Kill any attachment a journey left running, so no assertion failure hangs."""
    started: list[subprocess.Popen[str]] = []
    yield started
    for process in started:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=e2e_timeout(30))


def _orchestrate_attached(
    plan: Path,
    runs: Path,
    base: Path,
    onejudge_bin: str,
    *extra: str,
    logs: Path,
    attachments: list[subprocess.Popen[str]],
    skill: list[str] | None = None,
) -> Attached:
    """Start the same foreground launch `_orchestrate` runs, without waiting for it.

    The journeys that act on a run *while* it is attached need the command still
    running to act on, which `subprocess.run` cannot give them. Everything else is
    identical, so what these prove is the same command a planner types.
    """
    logs.mkdir(parents=True, exist_ok=True)
    out, err = logs / "stdout.txt", logs / "stderr.txt"
    with out.open("w", encoding="utf-8") as stdout, err.open("w", encoding="utf-8") as stderr:
        process = subprocess.Popen(
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
            stdout=stdout,
            stderr=stderr,
        )
    attachments.append(process)
    return Attached(process, out, err)


def _launched_run(runs: Path) -> Path:
    directories = [entry for entry in runs.iterdir() if entry.is_dir()]
    assert len(directories) == 1, directories
    return directories[0]


def _await_launched_run(runs: Path) -> Path:
    """The run directory the attached launch created, once it exists."""
    wait = deadline(120)
    while time.monotonic() < wait:
        if runs.is_dir() and any(entry.is_dir() for entry in runs.iterdir()):
            return _launched_run(runs)
        time.sleep(0.005)
    raise AssertionError(f"the attached launch never created a run under {runs}")


#: A provider that wedges the real orchestrator instead of answering it. The fork
#: is orphaned onto init the moment the provider exits, so it is not this run's
#: work and never becomes it — it simply never lets go of the protocol stream
#: onejudge is reading. Real onejudge then does what a wedged harness does: it
#: waits on a read that will never complete, collecting nothing, spawning nothing,
#: recording nothing, with its pid and its `running` claim intact.
WEDGED_PROVIDER = """\
import os
import time

if os.fork() == 0:
    time.sleep(3600)
os._exit(0)
"""


def _await_wedged(run_dir: Path, owner: int) -> None:
    """Wait until the launch has nothing running under it, the parked precondition."""
    wait = deadline(120)
    while time.monotonic() < wait:
        if not has_live_descendant(frozenset({owner})):
            return
        time.sleep(0.05)
    raise AssertionError(f"the launched orchestrator {owner} never went quiet: {run_dir}")


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


#: The journey's own parked threshold, deliberately unscaled: it is the deadline
#: under test rather than a hang guard, and the run it is applied to has been
#: frozen, so nothing it measures can be slowed by load.
PARKED_AFTER = 5.0


def test_the_foreground_launch_returns_when_its_own_orchestrator_parks(
    tmp_path: Path, onejudge_bin: str, reaped: list[Path], attachments: list[subprocess.Popen[str]]
) -> None:
    """A launch that goes quiet while staying alive is handed back, not waited on.

    This is the state the attach mode exists for and the one it could most easily
    get wrong: a parked orchestrator keeps its pid and its ``running`` record, so
    every check the follow makes says the run is fine and the planner waits on
    something that will never finish. `just monitor --until-settled` already
    refuses to (tests/e2e/test_liveness_e2e.py); this is the same refusal on the
    command a planner is actually sitting in front of, with the same status and the
    same line.

    Nothing here is staged around the run: `just orchestrate` launches it, real
    onejudge drives it, and it parks because its provider wedges it — which is what
    a parked launch is. The one process this journey writes is that provider, and
    it is the double this suite already has.
    """
    runs = tmp_path / "runs"
    wedged = tmp_path / "wedged-provider.py"
    wedged.write_text(WEDGED_PROVIDER, encoding="utf-8")
    attached = _orchestrate_attached(
        _plan(tmp_path, "parked"),
        runs,
        _base(tmp_path),
        onejudge_bin,
        "--parked-after",
        f"{PARKED_AFTER:g}",
        logs=tmp_path / "parked-log",
        attachments=attachments,
        skill=[sys.executable, str(wedged)],
    )
    run_dir = _await_launched_run(runs)
    reaped.append(run_dir)
    owner = _owner_pid(run_dir)

    # The two facts the settlement turns on, established before it is asserted:
    # nothing is running underneath the launch, and no round it could have been
    # abandoned mid-way through was ever claimed. Without both, "nothing is driving
    # this run" would be true for a reason that is not parked.
    _await_wedged(run_dir, owner)
    assert not (run_dir / "round-01").exists(), "the wedged launch claimed a round"

    status, out, err = attached.settled(180)
    assert status == UNATTENDED_STATUS, out + err
    _, _, followed = out.partition(f"{HEADER}\n")
    last = followed.splitlines()[-1]
    assert "settled, nothing is driving this run" in last, out
    assert "PARKED (alive with no child process" in last, out
    # It is still there, which is the whole difference from the launch that died:
    # the planner is being handed a run to intervene in, not told one is over.
    assert _alive(owner)


def test_the_foreground_launch_returns_when_the_run_it_is_watching_is_stopped(
    tmp_path: Path, onejudge_bin: str, reaped: list[Path], attachments: list[subprocess.Popen[str]]
) -> None:
    """A run that dies underneath a working attachment settles it too.

    The launch that never started is the easy half of "nothing is driving this
    run"; this is the other. The round is genuinely in flight — its worker is held
    at a rendezvous — so the attachment has been following real work for as long as
    it takes to stop it, and what it must notice is a transition rather than the
    state of its first poll. `just stop` is how a run really ends here, exactly as
    in the `monitor` journey that asserts the same settlement.
    """
    runs = tmp_path / "runs"
    held = Rendezvous.at(tmp_path, "attached-stop")
    attached = _orchestrate_attached(
        _plan(tmp_path, "stopped-attached", task=f"complete-now attach{held.sentinels()}"),
        runs,
        _base(tmp_path),
        onejudge_bin,
        logs=tmp_path / "stopped-log",
        attachments=attachments,
    )
    run_dir = _await_launched_run(runs)
    reaped.append(run_dir)
    held.wait(240)

    stopped = subprocess.run(
        ["just", "stop", run_dir.name, "--runs-dir", str(runs), "--force"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
    )
    assert stopped.returncode == 0, stopped.stdout + stopped.stderr

    status, out, err = attached.settled(240)
    assert status == UNATTENDED_STATUS, out + err
    _, _, followed = out.partition(f"{HEADER}\n")
    assert "settled, nothing is driving this run" in followed.splitlines()[-1], out


def _channel(recipe: str, run_id: str, runs: Path, payload: str | None = None) -> str:
    result = subprocess.run(
        ["just", recipe, run_id, "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        input=payload,
        timeout=e2e_timeout(120),
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _answer_until_the_run_is_over(run_id: str, runs: Path) -> None:
    """Reply to every surface the orchestrator raises until it writes its report.

    The planner's own loop, run through the planner's own commands: this is how a
    graph reaches `complete` here, and it is the only way to reach it without
    manufacturing a ledger the executor should have written.
    """
    report = runs / run_id / "orchestrator" / "report.json"
    wait = deadline(240)
    while time.monotonic() < wait:
        if report.is_file() and report.stat().st_size:
            return
        surface = json.loads(_channel("channel-next", run_id, runs) or "{}")
        if surface.get("surface") is None:
            continue
        _channel(
            "channel-reply",
            run_id,
            runs,
            json.dumps({"completion": True, "reason": "verified by the attach journey"}),
        )
    raise AssertionError(f"the orchestrator never finished {run_id}")


def test_monitor_until_settled_returns_on_a_graph_that_completed(
    tmp_path: Path, onejudge_bin: str, reaped: list[Path]
) -> None:
    """Attaching to a run that is already over returns at once, saying so.

    Every part of this run is the planner's own: `just orchestrate` launched it,
    `just channel-next` and `just channel-reply` answered it, and it completed. The
    attach is then asked about a run whose orchestrator has already written its
    report — the *already settled* case — and must recognise the completed graph
    rather than follow a stream nothing will add to, or call it abandoned.
    """
    runs = tmp_path / "runs"
    launched = _orchestrate(
        _plan(tmp_path, "completed"), runs, _base(tmp_path), onejudge_bin, "--detach"
    )
    assert launched.returncode == 0, launched.stderr
    run_dir = _launched_run(runs)
    reaped.append(run_dir)
    _answer_until_the_run_is_over(run_dir.name, runs)

    settled = _monitor(runs, run_dir.name, "--until-settled", "--format", "jsonl")
    assert settled.returncode == 0, settled.stdout + settled.stderr
    last = json.loads(settled.stdout.splitlines()[-1])
    assert (last["state"], last["detail"]) == ("complete", "graph complete")
    assert last["settlement"] == "complete"


def test_monitor_until_settled_returns_when_a_stopped_run_leaves_nobody_driving(
    tmp_path: Path, onejudge_bin: str, reaped: list[Path]
) -> None:
    """`just stop` is how a run really ends here, and this is what it leaves behind.

    The graph is unfinished — its worker is still working when the stop lands — so
    nothing about the ledger says the run is over. What says it is that nothing is
    driving it any more, which is the settlement a follow would otherwise wait
    through forever while reporting a round that is never going to finish.
    """
    runs = tmp_path / "runs"
    witness = tmp_path / "slow-witness"
    plan = tmp_path / "plan-stopped.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "name": "stopped",
                "tasks": [
                    {"id": "worker", "persona": "engineer", "task": f"slow-branch {witness}"}
                ],
            }
        ),
        encoding="utf-8",
    )
    launched = _orchestrate(plan, runs, _base(tmp_path), onejudge_bin, "--detach")
    assert launched.returncode == 0, launched.stderr
    run_dir = _launched_run(runs)
    reaped.append(run_dir)
    wait = deadline(240)
    while not (run_dir / "round-01" / "status.json").is_file():
        assert time.monotonic() < wait, "the launched run never claimed its first round"
        time.sleep(0.05)

    stopped = subprocess.run(
        ["just", "stop", run_dir.name, "--runs-dir", str(runs), "--force"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
    )
    assert stopped.returncode == 0, stopped.stdout + stopped.stderr

    settled = _monitor(runs, run_dir.name, "--until-settled", "--format", "jsonl")
    assert settled.returncode == UNATTENDED_STATUS, settled.stdout + settled.stderr
    last = json.loads(settled.stdout.splitlines()[-1])
    assert last["settlement"] == "unattended"
    assert "settled, nothing is driving this run" in last["detail"]
