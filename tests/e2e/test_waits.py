"""What a bounded wait does when the thing it waits on never gets there.

`waits.until` is the one wait every poll on another party's progress in this suite is
built out of, and the behaviour worth proving is its *expiry*: the fixtures it replaced
could only ever end well, so a run that wedged took the whole tier with it silently.

Driven through the injected clock rather than against the wall clock, deliberately. A
journey that really waited would either spend its bound on every run or would have to
pick a bound so short that a loaded host expires the wait it was meant to survive —
which is the failure this helper exists to prevent, reintroduced in the test for it.
The clock here also counts the polls, so "it expired" is asserted rather than inferred
from the test having finished: a helper that never gave up would run the counter out.
"""

from __future__ import annotations

import inspect
import math
import re
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from waits import install_default_bounds, timeout, until

#: How many turns of the loop this clock will serve before it calls the wait wedged.
#: Far more than any case here needs and far fewer than a wait with no exit takes to
#: look like a hang, so a helper that stopped giving up fails as a refusal rather than
#: by running until somebody kills the tier.
POLL_BUDGET = 100


class Clock:
    """A clock that advances one step per reading, and a pause that refuses to loop forever.

    The budget is spent in `pause` rather than in `read`, and that placement is the
    whole of what makes "it expired" an assertion. A helper that never gives up need
    never read the clock again — the mutation that deletes its expiry check does
    exactly that — but it must pause between polls or it is a busy loop, so the pause
    is the one seam every turn of the loop passes through.
    """

    def __init__(self, step: float) -> None:
        self.step = step
        self.now = 0.0
        self.pauses: list[float] = []

    def read(self) -> float:
        now = self.now
        self.now += self.step
        return now

    def pause(self, seconds: float) -> None:
        """Stand in for the sleep between polls; time is the clock's to move, not this."""
        self.pauses.append(seconds)
        if len(self.pauses) > POLL_BUDGET:
            raise AssertionError(
                f"the wait went round {len(self.pauses)} times without deciding, which "
                f"is the unbounded loop this helper replaced"
            )


def test_a_bounded_wait_returns_as_soon_as_what_it_waits_for_happens() -> None:
    """The ordinary path: ready on a later poll, and no failure for the time it took."""
    clock = Clock(step=1.0)
    polls = 0

    def ready() -> bool:
        nonlocal polls
        polls += 1
        return polls >= 3

    until(
        "the run to dispatch a worker",
        ready,
        seconds=60,
        state=lambda: pytest.fail("the state was read on a wait that did not expire"),
        clock=clock.read,
        pause=clock.pause,
    )

    assert polls == 3, f"the wait polled {polls} time(s) rather than until it was ready"
    assert clock.pauses == [0.5, 0.5], (
        f"the wait paused {clock.pauses} between polls; one pause per poll that was not "
        f"ready is what keeps a poll off a busy loop"
    )


def test_a_bounded_wait_that_expires_names_what_it_waited_for_and_the_state_it_reached() -> None:
    """The whole point: it gives up, and it says enough to diagnose why.

    Both halves are asserted because either alone is the failure this replaced. A wait
    that expires with no state hands its reader the hang's own question; one that names
    a state but not what it was waiting for cannot be attributed to a wait at all.
    """
    clock = Clock(step=30.0)

    with pytest.raises(AssertionError) as expired:
        until(
            "the run to hand the monitor the filter's ruling",
            lambda: False,
            seconds=60,
            state=lambda: "the launch is still running; the monitor has taken 0 turn(s)",
            clock=clock.read,
            pause=clock.pause,
        )

    said = str(expired.value)
    assert "the run to hand the monitor the filter's ruling" in said, (
        f"the expiry does not name what was awaited, so it cannot be attributed to a "
        f"wait at all:\n{said}"
    )
    assert "the launch is still running; the monitor has taken 0 turn(s)" in said, (
        f"the expiry does not say what state the thing it waited on reached, which is "
        f"the question a hang leaves its reader with:\n{said}"
    )
    assert re.search(rf"\b{timeout(60):.0f}s\b", said), (
        f"the expiry does not say how long it waited, so a reader cannot tell a bound "
        f"that was too short from a party that never moved:\n{said}"
    )


def test_the_state_a_bounded_wait_reports_is_the_one_it_gave_up_at() -> None:
    """Read at expiry rather than sampled when the wait began.

    A state captured up front describes the moment before anything had happened, which
    on a wait about progress is the one moment guaranteed to be uninformative.
    """
    step = 30.0
    clock = Clock(step=step)
    turns = 0

    def ready() -> bool:
        nonlocal turns
        turns += 1
        return False

    # Derived rather than written down: the count is arithmetic on the scaled bound and
    # the step, so a changed default scale moves the expectation with it instead of
    # turning this into a test about `ORCHESTRATOR_E2E_TIMEOUT_SCALE`.
    gave_up_at = math.ceil(timeout(60) / step)

    with pytest.raises(AssertionError) as expired:
        until(
            "the dispatch to record a turn",
            ready,
            seconds=60,
            state=lambda: f"the dispatch has recorded {turns} turn(s)",
            clock=clock.read,
            pause=clock.pause,
        )

    assert f"recorded {gave_up_at} turn(s)" in str(expired.value), (
        f"the reported state is not the one the wait gave up at, after "
        f"{gave_up_at} poll(s):\n{expired.value}"
    )


#: A command that outlives any bound a case here installs, so expiry is arithmetic
#: rather than a race with the host: the bound is a twentieth of a second and this runs
#: for half a minute, so no load makes it finish first and none makes it expire late.
SLEEPS = [sys.executable, "-c", "import time; time.sleep(30)"]

#: What a command writes before it stops answering, so a case can assert on what a
#: killed call had produced. Computed by the child rather than written in its argv,
#: because the command line is in the diagnosis too: a literal would satisfy an
#: assertion about captured output without any output having been captured, which is a
#: mistake this case made before it was written this way.
SAID_BEFORE_SLEEPING = str(0xDEADBEEF)
SAYS_THEN_SLEEPS = [
    sys.executable,
    "-c",
    "import sys, time; sys.stdout.write(str(0xDEADBEEF)); sys.stdout.flush(); time.sleep(30)",
]

#: The bound each case below installs. Short because nothing here waits for work to
#: finish — every case is about what happens when it does not.
BOUND_SECONDS = 0.05


@pytest.fixture
def bounded() -> Iterator[float]:
    """This suite's own bound, reinstalled at a length these cases can reach.

    The suite already runs under `install_default_bounds()` from `tests/conftest.py`, so
    what a case here needs is not to switch the policy on but to shorten it. Unwinding
    puts back exactly what was found, which is that installed one — re-installing on top
    of it would leave this worker running every blocking call through two wrappers.
    """
    unwind = install_default_bounds(BOUND_SECONDS)
    try:
        yield timeout(BOUND_SECONDS)
    finally:
        unwind()


def test_a_command_this_suite_runs_with_no_bound_of_its_own_is_given_one(
    bounded: float,
) -> None:
    """The two hundred call sites that state no timeout, answered in one place."""
    started = time.monotonic()

    with pytest.raises(AssertionError) as expired:
        subprocess.run(SAYS_THEN_SLEEPS, capture_output=True, text=True, check=False)

    took = time.monotonic() - started
    said = str(expired.value)
    assert "this suite ran" in said, (
        f"this is not the bound `subprocess.run` was given — the wording belongs to "
        f"another of them, so the case is passing on a wrapper it is not about:\n{said}"
    )
    assert "time.sleep(30)" in said, (
        f"the expiry does not name the command that was run, so a reader cannot tell "
        f"which of a journey's calls stopped:\n{said}"
    )
    assert SAID_BEFORE_SLEEPING in said, (
        f"the expiry does not carry what the command had produced before it was killed, "
        f"which is the whole of its last observed state:\n{said}"
    )
    assert took < 30, (
        f"the call ran for {took:.1f}s against a {bounded}s bound, so it was waited out "
        f"rather than bounded — the command it ran sleeps for 30s"
    )


def test_a_caller_that_states_its_own_timeout_keeps_it(bounded: float) -> None:
    """Untouched, and that is what makes this safe to install for the whole suite.

    Several journeys here catch `TimeoutExpired` on purpose — a pipe drained after a
    `kill`, a probe that expects a command not to answer — so a bound that converted
    every expiry into an assertion would break the code that had already thought about
    it. What a caller states is what a caller gets.
    """
    with pytest.raises(subprocess.TimeoutExpired):
        subprocess.run(SLEEPS, timeout=BOUND_SECONDS, check=False)


def test_a_process_this_suite_waits_on_is_given_a_bound(bounded: float) -> None:
    """`Popen.wait()`, which blocks until the child exits and otherwise forever."""
    waited = subprocess.Popen(SLEEPS)  # noqa: S603 - this suite's own interpreter
    try:
        with pytest.raises(AssertionError) as expired:
            waited.wait()
        said = str(expired.value)
        assert f"pid {waited.pid}" in said and "still running" in said, (
            f"the expiry does not say which process was still running, which is the "
            f"state a reader needs in order to go and look at it:\n{said}"
        )
    finally:
        waited.kill()
        waited.wait(timeout=timeout(30))


def test_pipes_this_suite_drains_are_given_a_bound(bounded: float) -> None:
    """`Popen.communicate()`, which blocks until the pipes close.

    A killed process is not a process whose pipes are closed — anything it spawned
    inherited them — so this is the wait that outlives the one above it.
    """
    draining = subprocess.Popen(SLEEPS, stdout=subprocess.PIPE)  # noqa: S603 - as above
    try:
        with pytest.raises(AssertionError) as expired:
            draining.communicate()
        assert "close its pipes" in str(expired.value), (
            f"the expiry does not say what was being waited for:\n{expired.value}"
        )
    finally:
        draining.kill()
        draining.communicate(timeout=timeout(30))


#: An importable module that is not part of this suite, and what it answers from a
#: thread. Written to a temporary path rather than to `tests/`, because being outside
#: this suite's own root is the whole of what is under test.
OUTSIDER = """import subprocess
import sys

import waits

answers = []
ran = []


def asked():
    answers.append(waits.made_by_this_suite())


def runs_a_command_that_outlives_the_bound():
    try:
        subprocess.run(
            [sys.executable, "-c", "import time; time.sleep(1)"], check=False
        )
    except BaseException as refused:  # noqa: BLE001 - the point is that there is none
        ran.append(refused)
    else:
        ran.append(None)
"""


def test_a_blocking_call_this_suite_did_not_make_is_left_alone(tmp_path: Path) -> None:
    """The bound reaches this repository's calls and not its host's.

    pytest, xdist and execnet block on processes of their own, and putting this suite's
    deadline on those would answer a wedged worker with a diagnosis about a test that
    was not making the call.

    Driven from a thread whose target is defined outside this suite, because a thread's
    stack starts at its target: there is no frame of ours below it, which is exactly the
    shape an execnet call has. The target records the answer itself for the same reason
    — a callback written here would put a frame of this suite back underneath it, and
    the case would pass while measuring nothing.
    """
    written = tmp_path / "outsider.py"
    written.write_text(OUTSIDER, encoding="utf-8")
    outsider = ModuleType("outsider")
    outsider.__file__ = str(written)
    exec(  # noqa: S102 - the source is this module's own constant, compiled under a
        # path outside this suite, which is the only way to get a frame that is not ours
        compile(OUTSIDER, str(written), "exec"),
        outsider.__dict__,
    )

    asking = threading.Thread(target=outsider.asked)
    asking.start()
    asking.join(timeout=timeout(30))
    assert not asking.is_alive(), "the outsider never answered"

    assert outsider.answers == [False], (
        f"a call with no frame of this suite below it was claimed by this suite's "
        f"bound: {outsider.answers}"
    )
    # The other direction, so the answer above is the predicate working rather than one
    # that says no to everything: the same function, called from here, has this test's
    # own frame below it.
    outsider.asked()
    assert outsider.answers == [False, True], (
        f"the same call made from within this suite was not recognised as ours: {outsider.answers}"
    )


def test_a_foreign_blocking_call_is_left_alone_through_the_wrapper_itself(
    tmp_path: Path, bounded: float
) -> None:
    """The same guard asked the way the wrapper asks it, which is where it went wrong.

    The case above asks the predicate directly; the wrapper asks it with its own frame
    already on the stack, and `waits.py` is under this suite's root. Counting that frame
    made every blocking call in the interpreter this suite's — including the
    `platform.platform()` an xdist worker makes before it has collected anything, which
    is how it was found: every worker died at session start with an internal error.

    So the call is really made, from a thread whose target is outside this suite, under
    a bound far shorter than the command takes. Being allowed to finish is the assertion.
    """
    written = tmp_path / "outsider.py"
    written.write_text(OUTSIDER, encoding="utf-8")
    outsider = ModuleType("outsider")
    outsider.__file__ = str(written)
    exec(  # noqa: S102 - the source is this module's own constant, compiled under a
        # path outside this suite, which is the only way to get a frame that is not ours
        compile(OUTSIDER, str(written), "exec"),
        outsider.__dict__,
    )

    running = threading.Thread(target=outsider.runs_a_command_that_outlives_the_bound)
    running.start()
    running.join(timeout=timeout(60))
    assert not running.is_alive(), "the outsider's command never returned"

    assert outsider.ran == [None], (
        f"a command run from outside this suite was refused by this suite's {bounded}s "
        f"bound, which is not this suite's call to bound: {outsider.ran}"
    )


def test_a_caller_that_forwards_an_explicit_empty_timeout_is_still_answered() -> None:
    """`subprocess.check_output` states `timeout=None`, which is not a bound.

    It forwards one to `subprocess.run` whatever its caller passed, so a wrapper adding
    its own beside it raised `TypeError: got multiple values for keyword argument
    'timeout'` from inside itself — a call with a bound in name and neither a bound nor
    a result in fact. Driven through the real `check_output` rather than by passing the
    keyword by hand, because forwarding it is that function's own behaviour.
    """
    answered = subprocess.check_output([sys.executable, "-c", "print('ok')"], text=True)

    assert answered.strip() == "ok", f"the forwarded empty timeout broke the call: {answered!r}"


#: The calls the bound stands in for, and where each one lives. `subprocess.run` is not
#: among them: its wrapper forwards `*arguments, **keywords` whole and so restates
#: nothing that could drift.
STOOD_IN_FOR = {
    "subprocess.Popen.wait": subprocess.Popen.wait,
    "subprocess.Popen.communicate": subprocess.Popen.communicate,
}

#: Read from an interpreter that has not installed the bound, which is the only place
#: the unwrapped signature still exists once `tests/conftest.py` has run.
READS_A_SIGNATURE = (
    "import inspect, subprocess, sys; print(','.join(inspect.signature({}).parameters))"
)


@pytest.mark.parametrize("dotted", sorted(STOOD_IN_FOR))
def test_the_bound_takes_what_the_call_it_stands_in_for_takes(dotted: str) -> None:
    """Two of the three wrappers name their parameters, so those names can drift.

    They are the standard library's rather than this repository's, and a wrapper that
    kept naming `timeout` after CPython had added a parameter beside it would drop that
    parameter silently — a caller would state something and be answered as though it had
    not. So the names are read back from an interpreter that has not installed the bound,
    which is where the unwrapped signature still exists, and compared in order.

    `subprocess.run` is deliberately not here: its wrapper forwards everything it was
    given and merges only the bound, so it has nothing to drift from.
    """
    reading = subprocess.run(
        [sys.executable, "-c", READS_A_SIGNATURE.format(dotted)],
        capture_output=True,
        text=True,
        check=True,
    )
    pristine = reading.stdout.strip().split(",")

    standing_in = list(inspect.signature(STOOD_IN_FOR[dotted]).parameters)

    assert standing_in == pristine, (
        f"the bound installed over {dotted} takes {standing_in} where the call it stands "
        f"in for takes {pristine}; a parameter it does not name is one a caller can state "
        f"and never have forwarded"
    )
