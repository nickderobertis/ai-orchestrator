"""Real-CLI journeys for the sequence a live run's ledger hands out and replays.

Both journeys are the same incident from its two ends. A run's journal came to hold
two events at ``seq`` 104 — one written by the planner's own ``channel-next`` from a
newer checkout, one by the executor that could not read it — and from that moment
every ``channel-reply`` was refused as non-contiguous, so a healthy run could no
longer be dropped, retried or attested and had to be killed with a node in flight.

The first journey proves the writer can no longer take a number that is already on
disk. The second proves a ledger that *already* holds such a pair still accepts a
live edit, because a planner must never lose control of a run over a journal defect.
"""

# llmlint: ignore-file[live_tier_compiles_and_requires_credential] the onejudge_bin fixture fails
# fast unless the real adopted onejudge CLI is on PATH, and these journeys drive it as a real
# subprocess; per the documented suite invariant only the paid model backend is faked via
# onejudge's own command provider (fake_backend.py), so no model credential is required.
# llmlint: ignore-file[tests_mirror_real_usage] The one thing written by hand here is the record
# a *second writer* put in the journal — in the incident, the planner's own `channel-next`
# running from a newer checkout than the orchestrator it supervised. No command of this build
# can produce it, because the whole premise is a record this build did not write and, in the
# first journey, cannot even read. Everything else is real: the executor, its round, every
# ledger write it makes afterwards, and the `channel-reply` that must still be applied.

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import yaml
from rendezvous import Rendezvous
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator import BASE_CONFIG, REPO_ROOT
from orchestrator.coordination import advisory_lock
from orchestrator.journal import SCHEMA_VERSION

FAKE_BACKEND = REPO_ROOT / "tests" / "e2e" / "fake_backend.py"
LIVE_PROCESS_TIMEOUT = 60


def _base(tmp_path: Path) -> Path:
    value = yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8"))
    value["provider"] = {"kind": "command", "command": [sys.executable, str(FAKE_BACKEND)]}
    path = tmp_path / "base.yaml"
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    return path


def _held(tmp_path: Path) -> Rendezvous:
    """The rendezvous the plan's `held` node parks its second turn at."""
    return Rendezvous.at(tmp_path, "held")


def _plan(tmp_path: Path) -> Path:
    """One node held inside its turn, one node queued behind it."""
    held = _held(tmp_path)
    path = tmp_path / "plan.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 5,
                "name": "journal-sequence",
                "concurrency": 4,
                "tasks": [
                    {
                        "id": "held",
                        "persona": "engineer",
                        "task": f"slow-branch {tmp_path / 'held.ticks'}{held.sentinels(1)}",
                    },
                    {
                        "id": "queued",
                        "task": "No diff",
                        "expects_no_diff": True,
                        "deps": ["held"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _wait_for(path: Path, predicate: Callable[[str], bool], timeout: float = 30) -> str:
    wait_deadline = deadline(timeout)
    while time.monotonic() < wait_deadline:
        if path.is_file() and predicate(text := path.read_text(encoding="utf-8")):
            return text
        time.sleep(0.02)
    raise AssertionError(f"condition did not appear in {path}")


def _records(events: Path) -> list[dict]:
    """Every whole line on disk, including ones this build would not interpret."""
    text = events.read_text(encoding="utf-8")
    lines = text.splitlines()
    if lines and not text.endswith("\n"):
        lines.pop()
    return [json.loads(line) for line in lines if line.strip()]


def _append_as_a_second_writer(events: Path, build: Callable[[int], dict]) -> dict:
    """Append one record the way another process holding this journal would.

    ``build`` receives the highest sequence currently stored and returns the record
    to write. The advisory lock is the one every appender takes, so the record lands
    at a known point in the stream instead of racing the executor for the tail; the
    run's own writer is untouched, and what is written is exactly what a second
    process legitimately writes.
    """
    with advisory_lock(f"journal:{events.resolve()}"):
        record = build(max(item["seq"] for item in _records(events)))
        with events.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
            handle.flush()
    return record


def _surfaced(run_id: str, runs: Path) -> dict:
    surfaced = subprocess.run(
        [
            "just",
            "channel-next",
            run_id,
            "--runs-dir",
            str(runs),
            "--timeout",
            str(e2e_timeout(10)),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(surfaced.stdout)


def _reply(run_id: str, runs: Path, payload: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["just", "channel-reply", run_id, "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
    )


def _close_out(run_id: str, runs: Path) -> None:
    """Answer surfaces until the planner's completion verdict ends the run."""
    for _ in range(8):
        payload = _surfaced(run_id, runs)
        if payload.get("status") == "finished":
            break
        surface = payload.get("surface")
        if surface is None:
            continue
        if surface["kind"] in {"milestone", "closeout"}:
            assert _reply(run_id, runs, {"completion": True, "reason": "verified"}).returncode == 0
            break
        assert (
            _reply(
                run_id,
                runs,
                {"completion": False, "message": "continue", "reason": "observed"},
            ).returncode
            == 0
        )
    _wait_for(
        runs / run_id / "orchestrator" / "report.json",
        lambda text: bool(text.strip()),
        LIVE_PROCESS_TIMEOUT,
    )


def _started_run(tmp_path: Path, onejudge_bin: str) -> tuple[str, Path, Path]:
    """Launch the plan and return once its held node is provably inside its turn."""
    runs = tmp_path / "runs"
    launched = subprocess.run(
        [
            "just",
            "orchestrate",
            "--detach",
            str(_plan(tmp_path)),
            "--runs-dir",
            str(runs),
            "--base",
            str(_base(tmp_path)),
            "--onejudge-bin",
            onejudge_bin,
            "--skill-command",
            sys.executable,
            str(FAKE_BACKEND),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    run_id = str(json.loads(launched.stdout)["run_id"])
    events = runs / run_id / "events.jsonl"
    # The ready file, not `node-started`: the backend writes it from inside the turn
    # it then holds, so from here the round provably cannot settle on its own.
    _held(tmp_path).wait(LIVE_PROCESS_TIMEOUT)
    return run_id, runs, events


def _release(tmp_path: Path, run_id: str, runs: Path) -> dict:
    _held(tmp_path).let_go()
    return json.loads(
        _wait_for(
            runs / run_id / "round-01" / "result.json",
            lambda text: bool(text.strip()),
            LIVE_PROCESS_TIMEOUT,
        )
    )


def test_a_record_this_build_cannot_read_still_holds_its_sequence(
    tmp_path: Path, command_base
) -> None:
    """The live executor may not reissue a number a newer build already wrote.

    A journal outlives the build that opened it: the planner runs ``channel-next``
    from whatever checkout is current while the orchestrator keeps the build it
    launched with, so a record from a newer ``SCHEMA_VERSION`` legitimately appears
    in a live run's ledger — that is exactly how the lost run's two events at
    ``seq`` 104 were made. The executor cannot *interpret* such a record, which is by
    design; it must still treat the sequence number it holds as taken.

    Strict replay is the other half, and it stays strict: a line this build cannot
    read might have been an authoritative graph mutation, so the round refuses to
    record a result folded without it — and says so, rather than crashing.
    """
    runs = tmp_path / "runs"
    held = Rendezvous.at(tmp_path, "hold")
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 5,
                "name": "journal-sequence",
                "tasks": [
                    {
                        "id": "held",
                        "persona": "engineer",
                        "task": f"complete-now{held.sentinels()}",
                    },
                    {
                        "id": "queued",
                        "task": "No diff",
                        "expects_no_diff": True,
                        "deps": ["held"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    executor = subprocess.Popen(
        [
            "just",
            "run-plan",
            str(plan),
            "--runs-dir",
            str(runs),
            "--run",
            "sequence",
            "--base",
            str(command_base()),
            "--provider",
            "command",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    events = runs / "sequence" / "events.jsonl"
    try:
        # The backend writes the ready file from inside the turn it then holds, so the
        # executor is provably mid-round — its Journal open and its sequence in hand.
        held.wait(LIVE_PROCESS_TIMEOUT)
        before = _records(events)
        foreign = _append_as_a_second_writer(
            events,
            lambda last: {
                "version": SCHEMA_VERSION + 1,
                "seq": last + 1,
                "at": time.time(),
                "kind": "planner-surfaced",
                "run_id": "sequence",
                "round": 1,
                "detail": {
                    "kind": "heartbeat",
                    "message": "from a newer build",
                    "blocking": False,
                },
            },
        )
    finally:
        held.let_go()
    stdout, stderr = executor.communicate(timeout=e2e_timeout(LIVE_PROCESS_TIMEOUT))

    after = _records(events)
    assert len(after) > len(before) + 1, "the executor journaled nothing after the foreign record"
    sequences = [record["seq"] for record in after]
    assert len(sequences) == len(set(sequences)), f"a sequence was issued twice: {sequences}"
    assert max(sequences) > foreign["seq"], "the run never advanced past the foreign record"
    assert any(record["kind"] == "round-finished" for record in after)
    # Fail-closed, and stated: a refusal to record, never an unexplained crash.
    assert executor.returncode == 2, stdout + stderr
    assert "could not record run" in stderr
    assert not (runs / "sequence" / "round-01" / "result.json").exists()


def test_a_duplicated_sequence_leaves_the_planner_in_control(
    tmp_path: Path, onejudge_bin: str
) -> None:
    """A ledger already holding two events at one number still accepts a live edit.

    This replays the shape of the run that was lost: a `planner-surfaced` and an
    executor event share a sequence number. Every later `channel-reply` was then
    refused as non-contiguous, so the planner could not drop, retry or attest
    anything. A collision loses no record, and supervision has to survive it.
    """
    run_id, runs, events = _started_run(tmp_path, onejudge_bin)
    _append_as_a_second_writer(
        events,
        lambda last: {
            "version": SCHEMA_VERSION,
            "seq": last,
            "at": time.time(),
            "kind": "planner-surfaced",
            "run_id": run_id,
            "round": 1,
            "detail": {"kind": "heartbeat", "message": "collided", "blocking": False},
        },
    )
    sequences = [record["seq"] for record in _records(events)]
    assert len(sequences) != len(set(sequences)), "the journey did not produce a collision"

    accepted = _reply(
        run_id,
        runs,
        {
            "version": 1,
            "commands": [
                {
                    "op": "add",
                    "node": {"id": "recovered", "task": "No diff", "expects_no_diff": True},
                }
            ],
        },
    )
    assert accepted.returncode == 0, accepted.stderr
    _wait_for(events, lambda text: '"kind": "edit-committed"' in text, LIVE_PROCESS_TIMEOUT)

    payload = _release(tmp_path, run_id, runs)
    # The edit did not merely validate: it reached the running graph and the node ran.
    assert payload["results"]["recovered"]["status"] == "done"
    _close_out(run_id, runs)
