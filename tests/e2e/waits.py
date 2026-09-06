"""Load-tolerant wall-clock guards for end-to-end tests.

These timeouts only guard against a hung test. Deadlines whose expiry is the
behavior under test must remain explicit at their call site.

`timeout` and `deadline` scale one; `until` is the wait built out of them, for the
polling shape that otherwise has no bound at all — see its own docstring for why a
wait of that shape must name what it was waiting for and what state that reached.

`install_default_bounds` is the same policy for the blocking calls a suite makes by
the hundred: a `subprocess.run`, a `Popen.wait`, a `Popen.communicate` that states no
timeout of its own. Stating one at every call site would be the policy written out two
hundred times, and the two hundred and first would be the one somebody forgot.
"""

from __future__ import annotations

import math
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import FrameType
from typing import Any

_ENVIRONMENT_VARIABLE = "ORCHESTRATOR_E2E_TIMEOUT_SCALE"
_DEFAULT_SCALE = 4.0


def timeout(seconds: float) -> float:
    """Scale a hang guard without changing timeout behavior under test."""
    raw_scale = os.environ.get(_ENVIRONMENT_VARIABLE, str(_DEFAULT_SCALE))
    try:
        scale = float(raw_scale)
    except ValueError as exc:
        raise ValueError(f"{_ENVIRONMENT_VARIABLE} must be a number, got {raw_scale!r}") from exc
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError(f"{_ENVIRONMENT_VARIABLE} must be finite and positive, got {raw_scale!r}")
    return seconds * scale


def deadline(seconds: float) -> float:
    """Return a monotonic deadline for a load-scaled hang guard."""
    return time.monotonic() + timeout(seconds)


# `clock` and `pause` below are nullary functions, and a `Callable` states such a seam
# whole: there is no object with several members to describe, so a Protocol would name
# the same contract in more words.
# llmlint: ignore-block[protocol_based_seams] nullary functions Callable states whole
def until(
    what: str,
    ready: Callable[[], bool],
    *,
    seconds: float,
    state: Callable[[], str],
    interval: float = 0.5,
    clock: Callable[[], float] = time.monotonic,
    pause: Callable[[float], None] = time.sleep,
) -> None:
    """Poll for `ready`, or fail naming what was awaited and the state it got to.

    The class of wait this is for is a poll on **another party's progress** — a launched
    run, a dispatched member, a file some other process writes — where the thing waited
    on can stop making progress without ending. A loop of that shape whose only exit is
    the good one never fails: it stops silently, for as long as whoever started it will
    wait, and from outside a hung tier is indistinguishable from slow work.

    `state` is required rather than defaulted, because a wait that expires saying only
    that something never happened leaves its reader the hang's own question — what was
    the other party doing? — to answer out of a world that has usually gone by then. It
    is read at expiry rather than when the wait began, since what a reader needs is the
    state it gave up at.

    `clock` and `pause` are the seam a test drives this to expiry through. Expiry is the
    behaviour this exists for, so it has to be provable, and against the wall clock that
    means either a test that really waits or a bound so short it expires on a loaded
    host. Injecting both makes the proof arithmetic.
    """
    limit = clock() + timeout(seconds)
    while True:
        if ready():
            return
        if clock() >= limit:
            raise expired(what, state(), after=timeout(seconds))
        pause(interval)


# llmlint: ignore-end[protocol_based_seams]


#: How long a blocking call that states no bound of its own may take. Scaled like every
#: other guard here, so the default is forty minutes on an unloaded host.
#:
#: A ceiling rather than a deadline, and chosen from what the slowest legitimate call in
#: this suite costs: provisioning a checkout, a real Nx run over the whole workspace, a
#: judged lint tier, an install from PyPI. Those are minutes, and several times that on a
#: loaded host, so this sits far above all of them — a call that reaches it has stopped
#: rather than slowed. It is deliberately not tuned per call: a site that needs a tighter
#: bound states its own, and stating one is what turns this off for that call.
BLOCKING_SECONDS = 600

#: This suite's own root. A frame under it is what makes a blocking call one of ours, so
#: that the bound reaches the calls this repository writes and not the ones pytest,
#: xdist, or execnet make on their own account — whose waits are theirs to bound.
SUITE_ROOT = str(Path(__file__).resolve().parent.parent)

#: This file, which is under that root and must not answer the question for itself. The
#: wrapper below is always on the stack when the question is asked, so counting its own
#: frame makes every call in the interpreter this suite's — including `platform.platform()`
#: on the xdist worker's first breath, which is where that mistake was caught.
_THIS_FILE = str(Path(__file__).resolve())


def expired(what: str, state: str, *, after: float) -> AssertionError:
    """The one sentence every bound here fails with: what was awaited, and where it got to."""
    return AssertionError(f"{what} did not happen within {after:.0f}s; {state}")


def made_by_this_suite() -> bool:
    """Whether any frame below this one belongs to this suite's own code.

    Asked per blocking call rather than decided once, because the same interpreter runs
    this suite and the machinery that hosts it: pytest's own subprocess calls, and
    execnet's waits on the xdist workers, are not this repository's to put a deadline on
    and would be answered with a diagnosis about a test that was not making them.
    """
    frame: FrameType | None = sys._getframe(1)
    while frame is not None:
        named = frame.f_globals.get("__file__")
        if isinstance(named, str) and named != _THIS_FILE and named.startswith(SUITE_ROOT):
            return True
        frame = frame.f_back
    return False


def _partial(stream: object) -> str:
    """Whatever a killed call had produced by the time it was killed, as text.

    Three shapes, because a caller decides which two of them it gets: `text=True` makes
    the captured stream a `str`, its absence makes it `bytes`, and a call that captured
    nothing leaves it `None`.
    """
    match stream:
        case bytes():
            return stream.decode("utf-8", "replace")
        case str():
            return stream
        case _:
            return ""


def _ran_state(ended: subprocess.TimeoutExpired) -> str:
    """What a timed-out `subprocess.run` had to show for itself."""
    out, err = _partial(ended.stdout), _partial(ended.stderr)
    shown = f"{out}{err}".strip()
    return (
        f"it had produced {shown[-400:]!r} and was killed"
        if shown
        else "it had produced nothing on either pipe and was killed"
    )


def _process_state(process: subprocess.Popen[Any]) -> str:
    # `Any` because a bound is indifferent to the stream types a caller opened its
    # process with: this reads the pid and the exit status, which every `Popen` has.
    """What a process this suite is still waiting on had got to."""
    return f"pid {process.pid} is still running and its exit status is {process.returncode!r}"


def install_default_bounds(seconds: float = BLOCKING_SECONDS) -> Callable[[], None]:
    """Give every unbounded blocking call this suite makes a finite one, and a diagnosis.

    Three calls, because between them they are how this suite blocks on another process:
    `subprocess.run`, `Popen.wait`, and `Popen.communicate`. A call that states its own
    `timeout` is left exactly as it was — including the `TimeoutExpired` it raises, which
    several journeys here catch on purpose — so this only ever reaches a call that had no
    bound at all, and the bound it adds is the caller's to override by stating one.

    Installed once for the whole suite rather than written at each of the two hundred
    call sites: one policy, one place to read it, and no site to forget it at. Hands back
    what undoes it, so a test can install a bound of its own and put this one back.
    """
    original_run = subprocess.run
    original_wait = subprocess.Popen.wait
    original_communicate = subprocess.Popen.communicate
    limit = timeout(seconds)

    # `Any` throughout the three wrappers below: each stands in for a call whose own
    # signature is overloaded on what the caller asked for — text or bytes, captured or
    # not — and narrowing it here would refuse callers the real one accepts.
    def bounded_run(*arguments: Any, **keywords: Any) -> Any:
        if keywords.get("timeout") is not None or not made_by_this_suite():
            return original_run(*arguments, **keywords)
        try:
            # Merged rather than passed beside `**keywords`: `subprocess.check_output`
            # and friends forward an explicit `timeout=None`, and adding a second one
            # is a `TypeError` from inside the wrapper rather than a bound.
            return original_run(*arguments, **{**keywords, "timeout": limit})
        except subprocess.TimeoutExpired as ended:
            raise expired(
                f"the command {ended.cmd!r} this suite ran", _ran_state(ended), after=limit
            ) from None

    def bounded_wait(self: subprocess.Popen[Any], timeout: float | None = None) -> int:
        if timeout is not None or not made_by_this_suite():
            return original_wait(self, timeout)
        try:
            return original_wait(self, limit)
        except subprocess.TimeoutExpired:
            raise expired(
                f"the process {self.args!r} this suite started to exit",
                _process_state(self),
                after=limit,
            ) from None

    def bounded_communicate(
        self: subprocess.Popen[Any],
        input: Any = None,  # noqa: A002 - the parameter `Popen.communicate` names
        timeout: float | None = None,
    ) -> tuple[Any, Any]:
        if timeout is not None or not made_by_this_suite():
            return original_communicate(self, input, timeout)
        try:
            return original_communicate(self, input, limit)
        except subprocess.TimeoutExpired:
            raise expired(
                f"the process {self.args!r} this suite started to close its pipes",
                _process_state(self),
                after=limit,
            ) from None

    # Rebinding the module attribute and the two methods is the whole mechanism; the
    # ignores below say that is deliberate, not that the types are unknown.
    subprocess.run = bounded_run  # type: ignore[assignment]
    subprocess.Popen.wait = bounded_wait  # type: ignore[method-assign,assignment]
    subprocess.Popen.communicate = bounded_communicate  # type: ignore[method-assign,assignment]

    def unwind() -> None:
        # Putting back exactly what was found, with the same ignores and for the same
        # reason as the rebinding above.
        subprocess.run = original_run  # type: ignore[assignment]
        subprocess.Popen.wait = original_wait  # type: ignore[method-assign]
        subprocess.Popen.communicate = original_communicate  # type: ignore[method-assign]

    return unwind
