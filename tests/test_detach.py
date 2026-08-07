"""Unit tests for leaving the launching turn before a round claims anything.

The real journey — a `just run-plan` round surviving the teardown of the turn that
launched it — is
`tests/e2e/test_round_ownership_e2e.py::test_a_round_survives_the_teardown_of_its_launching_turn`.
These cover what that journey cannot see from outside the process: that the round runs
in a session of its own, and that the relaying parent reports the round's own outcome
whether it returned an exit code, chose one by raising ``SystemExit``, crashed, or died
under a signal.

The fork is real here too. This test process is the one that forks, and the round it
forks off ends with ``os._exit``, so it never returns into the test — which is exactly
the property that makes calling ``run_detached`` from a test safe.
"""

from __future__ import annotations

import json
import os
import signal
import threading
import warnings
from pathlib import Path

import pytest

import orchestrator.detach as detach
from orchestrator.detach import (
    CRASHED,
    SUCCESSOR_ENV,
    reattribute_successor,
    run_detached,
)
from orchestrator.scratch import AGENT_STATUS_DIR_ENV


def _never_called(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("re-attribution took a path it had no business taking")


def test_detached_round_leads_its_own_session_and_relays_its_exit_code(tmp_path: Path) -> None:
    record = tmp_path / "round.json"

    def entry(argv: list[str] | None) -> int:
        record.write_text(
            json.dumps(
                {
                    "leads_session": os.getsid(0) == os.getpid(),
                    "pid": os.getpid(),
                    "argv": argv,
                }
            ),
            encoding="utf-8",
        )
        return 3

    assert run_detached(entry, ["--flag"], "run-plan") == 3
    observed = json.loads(record.read_text(encoding="utf-8"))
    assert observed["leads_session"] is True, "the round did not lead its own session"
    assert observed["pid"] != os.getpid(), "the round ran in the launching process"
    assert observed["argv"] == ["--flag"]


def test_signalled_round_is_relayed_as_its_own_exit_status() -> None:
    def entry(_argv: list[str] | None) -> int:
        # The round takes the signal a torn-down launcher would have sent it, under the
        # default disposition it inherits, so what the parent relays is a real 128+N.
        os.kill(os.getpid(), signal.SIGTERM)
        return 0

    assert run_detached(entry, None, "run-plan") == 128 + int(signal.SIGTERM)


def test_a_crashing_round_never_unwinds_back_into_the_launching_process() -> None:
    """An exception must end the round where it is, not climb back out of the fork.

    This test process *is* the launching process, so an escaping exception would land
    in the middle of pytest's own program — running the rest of this session a second
    time in the forked child. Reaching the assertion at all is half the proof; the
    other half is that the crash arrives as its own status rather than as the `1` an
    unfinished round returns.
    """

    def entry(_argv: list[str] | None) -> int:
        raise RuntimeError("the round's own failure")

    assert run_detached(entry, None, "run-plan") == CRASHED


def test_an_interrupted_round_is_relayed_as_the_signal_that_interrupted_it() -> None:
    """SIGINT that arrives as an exception must still report the 128+N a signal does.

    A recorded round installs its own SIGINT handler, so this is the window before that
    — argv parsing, loading the plan — where the interpreter raises instead. Reporting
    it as a crash would make a deliberate Ctrl-C look like an internal failure.
    """

    def entry(_argv: list[str] | None) -> int:
        raise KeyboardInterrupt

    assert run_detached(entry, None, "run-plan") == 128 + int(signal.SIGINT)


# The payloads `SystemExit` can carry, all three of which the interpreter reads
# differently; only the `int` one is reachable through a real command line, and reading
# any of them as a crash would rewrite a status the round deliberately chose.
@pytest.mark.parametrize(("chosen", "relayed"), [(None, 0), (2, 2), ("no plan given", 1)])
def test_an_exit_status_the_round_chose_is_relayed_unchanged(
    chosen: int | str | None, relayed: int
) -> None:
    def entry(_argv: list[str] | None) -> int:
        raise SystemExit(chosen)

    assert run_detached(entry, None, "run-plan") == relayed


def test_the_deliberate_fork_is_silent_while_every_other_fork_still_warns() -> None:
    """The interpreter's threaded-fork warning is suppressed here, and only here.

    A live thread is what makes the interpreter issue it at all, so this test starts
    one: without it the fork is single-threaded and the warning under test never
    happens, which would leave the assertion passing for the wrong reason. The second
    half is what keeps the suppression honest — the same fork, made straight from this
    test, still warns, so nothing global was installed to buy the first half.
    """
    running = threading.Event()
    thread = threading.Thread(target=running.wait, name="detach-warning-probe")
    thread.start()
    try:
        with warnings.catch_warnings(record=True) as at_the_site:
            warnings.simplefilter("always")
            assert run_detached(lambda _argv: 0, None, "run-plan") == 0
        assert [str(warned.message) for warned in at_the_site] == []

        with warnings.catch_warnings(record=True) as anywhere_else:
            warnings.simplefilter("always")
            child = os.fork()
            if child == 0:
                os._exit(0)
            os.waitpid(child, 0)
        assert [
            str(warned.message)
            for warned in anywhere_else
            if issubclass(warned.category, DeprecationWarning)
            and "multi-threaded" in str(warned.message)
        ] != []
    finally:
        running.set()
        thread.join()


# Re-attribution's own `exec` is proven by `tests/e2e/test_successor_survival_e2e.py`,
# because a process entry point is the only place it can be called: it replaces the
# process image, and calling it anywhere else restarts that program — here, pytest.
# What is left are the three ways it decides *not* to exec, each of which leaves a
# round running under an attribution somebody has to be able to reason about.


def test_a_process_no_dispatch_started_is_left_exactly_as_it_was(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`just run-plan` from a shell has no launcher to be misattributed to."""
    monkeypatch.delenv(AGENT_STATUS_DIR_ENV, raising=False)
    monkeypatch.delenv(SUCCESSOR_ENV, raising=False)
    monkeypatch.setattr(detach, "claim_successor_scratch_directory", _never_called)

    reattribute_successor("run-plan")

    assert AGENT_STATUS_DIR_ENV not in os.environ


def test_the_re_execed_side_claims_its_directory_and_stops_passing_the_sentinel_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A successor under a successor must attribute to itself, not inherit the claim."""
    claimed = tmp_path / "orchestrator-watchdog-mine" / "agent"
    monkeypatch.setenv(SUCCESSOR_ENV, "run-plan")
    monkeypatch.setenv(AGENT_STATUS_DIR_ENV, str(claimed))
    monkeypatch.setattr(detach, "claim_successor_scratch_directory", _never_called)
    monkeypatch.setattr(detach, "_claimed", None)

    reattribute_successor("run-plan")

    assert detach._claimed == claimed
    # Still stamped, so everything below inherits the attribution; no longer marked as
    # already-attributed, so a `repo-recover` this round starts takes one of its own.
    assert os.environ[AGENT_STATUS_DIR_ENV] == str(claimed)
    assert SUCCESSOR_ENV not in os.environ


def test_a_claim_that_could_not_be_made_runs_unattributed_rather_than_misattributed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Losing the sweep's reach beats keeping the launcher's stamp and being reaped.

    Nothing execs here, so the round goes on to run under the stamp it inherited — the
    one condition this whole mechanism cannot repair, and the reason it is reported
    rather than raised.
    """
    launcher = tmp_path / "orchestrator-watchdog-launcher" / "agent"
    monkeypatch.setenv(AGENT_STATUS_DIR_ENV, str(launcher))
    monkeypatch.delenv(SUCCESSOR_ENV, raising=False)
    monkeypatch.setattr(detach, "claim_successor_scratch_directory", lambda: None)
    monkeypatch.setattr(os, "execve", _never_called)

    reattribute_successor("repo-recover")

    assert os.environ[AGENT_STATUS_DIR_ENV] == str(launcher)
