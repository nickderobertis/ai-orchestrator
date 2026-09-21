"""How long a waiter queues for this host's merge-queue lock, measured end to end.

`config/onevcs.rules.yml` routes this repository through `local-direct`, whose
publication runs the complete pre-push gate inside a clone it holds under `onevcs`'s
merge-queue lock. `onevcs` bounds a queued wait at 900 seconds and names
`ONEVCS_LOCK_TIMEOUT_SECONDS` as the lever; nothing here exported it, so a gate that took
23 minutes held the lock while a sibling behind it gave up at fifteen — `aio-1108`
settled `publication-failed` behind `aio-1110` (ai-orchestrator#1164).

These journeys hold the first two links of the chain the fix is made of, against the
real helper:

* the gate's duration is **recorded** where the gate runs, with a stand-in gate of a
  known duration standing in for the real one, which no test here runs;
* the bound is **derived** from that recording, floor and factor and margin, and
  exported to a caller unless it named one of its own.

The last link — that the derived bound is what a real `onevcs` on a publication path is
started under — is read on those paths themselves: `tests/e2e/test_publish_branch_e2e.py`
reads it at the `pre-push` hook a real `just publish-branch` runs through `onevcs`, and
`tests/e2e/test_orchestrate_launch_e2e.py` reads it off a real dispatched turn under a
real launch. No journey waits out a real `onevcs` lock timeout, and none queues under a
bound of `onevcs`'s default size or longer: that is the manager's ruling, because the
derivation's floor is 900 seconds and a timeout message is printed only once the whole
bound has elapsed. The floor, the factor and the margin are not test-configurable and are
not lowered here; `tests/test_engine_contracts.py` holds the floor to the default the
adopted `onevcs` declares.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] What these spend is a
few `bash` runs of the helper and one two-second stand-in gate — no launch, no paid turn
— and what they read is `scripts/lock-timeout.sh`, which the root
project's `codeWorkspace` key already covers.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

HELPER = REPO_ROOT / "scripts" / "lock-timeout.sh"

#: The variable `onevcs` reads the bound out of, and its own default — `onevcs`'s
#: `lock::DEFAULT_TIMEOUT_SECONDS`. Stated here rather than read out of the helper: a
#: fixture that took its numbers from its subject would agree with whatever it said.
BOUND = "ONEVCS_LOCK_TIMEOUT_SECONDS"
ONEVCS_DEFAULT = 900

#: The derivation, stated the same way and for the same reason: how many of this
#: identity's publications a waiter may be queued behind, and what is added on top for
#: what a publication does around each gate.
QUEUE_DEPTH = 8
MARGIN = 300

#: A recorded gate duration big enough that the derivation, rather than its floor, is
#: what the bound comes out of: 8 * 120 + 300 = 1260, which is above 900.
A_LONG_GATE = 120
#: And one small enough that the floor is: 8 * 2 + 300 = 316, which is below it.
A_SHORT_GATE = 2

#: How long the stand-in gate really runs. Short, because what the recording has to be is
#: the elapsed time of whatever it was given, not a particular number of seconds.
STAND_IN_GATE_SECONDS = 2

#: Export the bound the way every caller does, then print what was exported.
EXPORT_AND_PRINT = 'export_lock_timeout probe\nprintf "%s\\n" "${ONEVCS_LOCK_TIMEOUT_SECONDS}"'


def _bash(script: str, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run one script against the tracked helper, from this checkout."""
    return subprocess.run(
        ["bash", "-c", f'. "{HELPER}"\n{script}'],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )


def _state(tmp_path: Path) -> dict[str, str]:
    """An environment whose state root, and so whose recording, is this journey's own."""
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    return {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path / "home"),
        "XDG_STATE_HOME": str(state),
    }


def _recording(tmp_path: Path) -> Path:
    return tmp_path / "state" / "ai-orchestrator" / "local-direct-gate-seconds"


# llmlint: ignore[e2e_not_mocked] The ticket rejects a second test that runs the full gate, and the recording path sees a gate only as the command `.githooks/pre-push` hands it; a command of known duration is that interface driven for real, with nothing below it substituted.  # noqa: E501
def test_the_gate_records_how_long_it_took_where_the_gate_runs(tmp_path: Path) -> None:
    """The real recording path, with a stand-in gate of a known duration.

    A stand-in rather than `just gate`, deliberately: the ticket rejects a second test
    that runs the whole bar, and what the recording has to be right about is elapsed
    time, which a command that sleeps a known number of seconds states exactly.
    """
    environment = _state(tmp_path)
    gate = tmp_path / "stand-in-gate"
    gate.write_text(
        f"#!/usr/bin/env bash\nsleep {STAND_IN_GATE_SECONDS}\nexit 0\n", encoding="utf-8"
    )
    gate.chmod(0o755)

    ran = _bash(f'record_local_direct_gate "{gate}"', environment)

    assert ran.returncode == 0, ran.stderr
    recorded = _recording(tmp_path)
    assert recorded.is_file(), f"the gate recorded nothing at {recorded}: {ran.stderr}"
    measured = int(recorded.read_text(encoding="utf-8").strip())
    assert measured >= STAND_IN_GATE_SECONDS, (
        f"the gate ran for {STAND_IN_GATE_SECONDS}s and {measured}s was recorded"
    )


def test_a_gate_that_failed_is_recorded_and_its_status_is_the_wrappers(
    tmp_path: Path,
) -> None:
    """The lock is held for the whole run whatever the verdict, so the duration counts.

    And what the hook admits is unchanged: the wrapper hands back the gate's own status,
    so a failing gate still fails the push.
    """
    environment = _state(tmp_path)

    ran = _bash(
        f"record_local_direct_gate bash -c 'sleep {STAND_IN_GATE_SECONDS}; exit 7'", environment
    )

    assert ran.returncode == 7, f"the wrapper did not return the gate's status: {ran.stderr}"
    assert _recording(tmp_path).is_file(), "a failed gate recorded no duration"


@pytest.mark.parametrize(
    ("recorded", "expected"),
    [
        (None, ONEVCS_DEFAULT),
        (A_SHORT_GATE, ONEVCS_DEFAULT),
        (A_LONG_GATE, A_LONG_GATE * QUEUE_DEPTH + MARGIN),
        ("not a number", ONEVCS_DEFAULT),
        ("9" * 30, ONEVCS_DEFAULT),
    ],
    ids=[
        "no-recording",
        "a-fast-gate",
        "a-slow-gate",
        "a-malformed-recording",
        "a-recording-no-gate-could-take",
    ],
)
def test_the_bound_is_derived_from_the_recording_and_never_below_the_default(
    tmp_path: Path, recorded: int | str | None, expected: int
) -> None:
    """Every answer the derivation has, including the two that are the floor.

    The slow-gate arm is the one that exercises the derivation rather than its floor: at
    120 recorded seconds a waiter behind a full queue of this identity's publications
    needs 1260, which is what `onevcs` is then told.
    """
    environment = _state(tmp_path)
    if recorded is not None:
        file = _recording(tmp_path)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(f"{recorded}\n", encoding="utf-8")

    ran = _bash(EXPORT_AND_PRINT, environment)

    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip() == str(expected), ran.stdout + ran.stderr


def test_a_gate_whose_duration_cannot_be_recorded_says_so_and_keeps_its_status(
    tmp_path: Path,
) -> None:
    """A state root that is not a directory costs the recording, never the push's verdict.

    Reported, with what to do about it, because a recording that silently stops is a
    bound derived from an older run forever.
    """
    environment = _state(tmp_path)
    blocked = tmp_path / "a-file-where-the-state-root-should-be"
    blocked.write_text("", encoding="utf-8")
    environment["XDG_STATE_HOME"] = str(blocked)

    ran = _bash("record_local_direct_gate bash -c 'exit 3'", environment)

    assert ran.returncode == 3, f"the wrapper did not return the gate's status: {ran.stderr}"
    assert "could not be recorded at" in ran.stderr, ran.stderr
    assert "make " in ran.stderr and "writable directory" in ran.stderr, ran.stderr


def test_a_relative_state_root_is_ignored_for_the_one_under_home(tmp_path: Path) -> None:
    """`XDG_STATE_HOME` counts only when absolute, which is that variable's own rule.

    A relative one would put the recording under whichever directory a hook ran in —
    a clone a publication then throws away.
    """
    environment = {**_state(tmp_path), "XDG_STATE_HOME": "relative/state"}

    ran = _bash("record_local_direct_gate true", environment)

    assert ran.returncode == 0, ran.stderr
    under_home = tmp_path / "home" / ".local/state/ai-orchestrator/local-direct-gate-seconds"
    assert under_home.is_file(), f"nothing was recorded at {under_home}: {ran.stderr}"
    assert not (REPO_ROOT / "relative").exists(), "the recording followed a relative root"


def test_a_host_with_no_absolute_state_root_records_nothing_and_says_so(
    tmp_path: Path,
) -> None:
    """With neither root absolute there is nowhere host-local to keep the recording.

    Reported, and the bound still derives — from the floor — rather than from a file
    under whichever directory the hook happened to run in.
    """
    environment = {**_state(tmp_path), "XDG_STATE_HOME": "relative", "HOME": "also-relative"}

    ran = _bash(
        "record_local_direct_gate bash -c 'exit 4' || echo \"gate status $?\" >&2\n"
        + EXPORT_AND_PRINT,
        environment,
    )

    assert "gate status 4" in ran.stderr, f"the gate's own status was lost: {ran.stderr}"
    assert "neither XDG_STATE_HOME nor HOME is an absolute path" in ran.stderr, ran.stderr
    assert ran.stdout.strip() == str(ONEVCS_DEFAULT), ran.stdout + ran.stderr
    assert not (REPO_ROOT / "also-relative").exists(), "the recording followed a relative HOME"


@pytest.mark.parametrize("named", ["abc", "0", "0.0", "-5", "1.2.3"])
def test_a_bound_the_caller_named_that_onevcs_would_refuse_is_refused_here(
    tmp_path: Path, named: str
) -> None:
    """Told where the bound came from, rather than by whichever `onevcs` verb reads it."""
    environment = {**_state(tmp_path), BOUND: named}

    ran = _bash(EXPORT_AND_PRINT, environment)

    assert ran.returncode == 2, ran.stdout + ran.stderr
    assert f"probe: {BOUND}={named} is not a positive number of seconds" in ran.stderr, ran.stderr


def test_a_bound_the_caller_already_named_is_kept(tmp_path: Path) -> None:
    """An operator raising it by hand for a wait they know is legitimate is not overruled."""
    environment = {**_state(tmp_path), BOUND: "4242"}
    file = _recording(tmp_path)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(f"{A_LONG_GATE}\n", encoding="utf-8")

    ran = _bash(EXPORT_AND_PRINT, environment)

    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip() == "4242", ran.stdout
