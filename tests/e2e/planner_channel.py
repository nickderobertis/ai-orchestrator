"""Playing the manager on a live run's planner channel, for the journeys that need one.

A blocking question is only half a round trip. Every journey that drives
`scripts/ask-manager.sh` — from a subprocess, or from inside a real dispatch — needs
somebody on the other end doing what a manager does: `just channel-next` until a
question is there, then `just channel-reply` with a ruling that echoes the question's
correlation token. That loop is here rather than in each module because it is the same
loop, and two copies of it drift into two different ideas of what answering means.

It is written as the recipes a manager types, deliberately. Handing out a blocking
surface is what opens the reply rendezvous at all — a reply sent to a run whose surface
nobody has read is refused with `nothing will ever read a reply to it` — so a shortcut
past `channel-next` would not be a faster manager, it would be a different protocol.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from collections.abc import Callable
from typing import TypedDict, cast

from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The correlation token `scripts/ask-manager.sh` mints and asks the manager to echo.
#: A reply that does not carry it is somebody else's answer arriving at the asking
#: call, so echoing it is what makes an answer this question's.
TOKEN = re.compile(r"ask-manager-token:[0-9a-f]{24}(?![0-9a-f])")

#: How long a manager keeps looking for a question to answer. Load-scaled like every
#: other hang guard here, because the manager is played by a thread driving real `just`
#: recipes and a loaded suite slows every one of them down.
MANAGER_PATIENCE_SECONDS = 120


class Surface(TypedDict):
    """One planner surface, narrowed to the two fields a manager acts on.

    `blocking` is the one that decides what they do about it: a question they have to
    answer, or a note telling them a listener re-armed and needs nothing.
    """

    message: str
    blocking: bool


class SurfaceRead(TypedDict):
    """`onepipeline next`'s answer, narrowed to what a manager reads off it."""

    status: str
    surface: Surface | None


def just(
    *arguments: str, environment: dict[str, str], seconds: float = 120
) -> subprocess.CompletedProcess[str]:
    """Run one real recipe from this checkout."""
    return subprocess.run(
        ["just", *arguments],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
    )


def ruling(message: str) -> str:
    """One reply envelope carrying a decision, as a manager's answer is spelled."""
    return json.dumps({"version": 1, "completion": True, "message": message})


def next_surface_record(run: str, environment: dict[str, str]) -> Surface | None:
    """Read the run's next unread surface whole, exactly as a manager reads one.

    Whole rather than just its text, because `onepipeline next` reports `blocking` per
    surface and that is a thing a manager acts on: it is the difference between a
    question they must answer and a note telling them somebody is listening. A journey
    counting how many questions one ask put in front of them reads it here, through the
    verb a manager uses, rather than out of the run's journal.

    A read that FAILS is raised rather than reported as an empty queue. The two are
    opposite states and look identical from a polling loop: treating a refusal as
    "nothing there yet" turns every one of them into a timeout at the far end of a
    wait, with the sentence that said what was wrong thrown away on the way.
    """
    handed = just("channel-next", run, environment=environment, seconds=60)
    assert handed.returncode == 0, (
        f"`just channel-next {run}` failed while reading this run's surfaces:\n"
        f"{handed.stderr}{handed.stdout}"
    )
    if not handed.stdout.strip():
        return None
    # `cast` rather than a validating read: `onepipeline next` owns this schema and
    # `SurfaceRead` states the part a manager consumes.
    return cast(SurfaceRead, json.loads(handed.stdout))["surface"]


def next_surface(run: str, environment: dict[str, str]) -> str | None:
    """The text of the run's next unread surface, for a caller that wants only that."""
    surface = next_surface_record(run, environment)
    return None if surface is None else surface["message"]


def reply(run: str, environment: dict[str, str], envelope: str) -> subprocess.CompletedProcess[str]:
    """Send one reply envelope over the live channel, as `just channel-reply` does."""
    return subprocess.run(
        ["just", "channel-reply", run],
        cwd=REPO_ROOT,
        env=environment,
        input=envelope,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )


def answer_each(
    run: str,
    environment: dict[str, str],
    answers: list[Callable[[str], str]],
    *,
    seconds: float = MANAGER_PATIENCE_SECONDS,
    seen: list[Surface] | None = None,
) -> list[subprocess.CompletedProcess[str]]:
    """Read surfaces until a question appears, then reply. Once per answer.

    A surface carrying no token is discarded rather than answered: this host's monitor
    and its pacemaker raise their own on the same channel, and a run watched by them is
    the ordinary case rather than the exception.

    Hands back what the reply verb *answered*, one per send, rather than only asserting
    it was accepted. The answer is evidence in its own right: `onepipeline reply` reports
    `{"reply":N,"state":"applied"}`, and the `N` there is how many pending surfaces that
    send also answered — which is the one direct reading of how the adopted release
    routed an envelope, as opposed to inferring it from who blocked afterwards.
    """
    limit = deadline(seconds)
    answered: list[subprocess.CompletedProcess[str]] = []
    for compose in answers:
        token = None
        while token is None:
            if time.monotonic() >= limit:
                raise AssertionError(f"run {run} never surfaced a question carrying a token")
            surface = next_surface_record(run, environment)
            if surface is not None and seen is not None:
                seen.append(surface)
            message = None if surface is None else surface["message"]
            found = None if message is None else TOKEN.search(message)
            if found is not None:
                token = found.group(0)
                break
            time.sleep(0.2)
        sent = reply(run, environment, compose(token))
        assert sent.returncode == 0, f"the channel refused this reply:\n{sent.stderr}{sent.stdout}"
        answered.append(sent)
    return answered


def answer_persistently(
    run: str,
    environment: dict[str, str],
    compose: Callable[[str], str],
    stopping: threading.Event,
    *,
    seconds: float = MANAGER_PATIENCE_SECONDS,
) -> None:
    """Answer this run's blocking question, and keep answering until somebody stops.

    Sending the answer once is not enough on a watched run, and this is measured rather
    than defensive. A reply on this channel is claimed by whichever reader reaches it
    next, and the monitor reads the same channel: it took the answer to a dispatched
    agent's question, and because `scripts/ask-manager.sh` only asks again when it
    *receives* a ruling it cannot match to its own question, receiving nothing left it
    waiting out its whole reply window with nobody about to answer again.

    So the answer is re-sent while the watch lasts, carrying the same correlation token
    every time, until the journey says the asker has it. A send nobody is waiting for is
    refused and that refusal is ignored on purpose — between two readers there are
    moments with no reader at all, and that is not a failing manager.

    Surfaces keep being read alongside, because handing one out is what opens the reply
    rendezvous at all: a re-ask that nobody reads can be answered by nobody.
    """
    limit = deadline(seconds)
    token: str | None = None
    while not stopping.is_set():
        if time.monotonic() >= limit:
            if token is not None:
                return
            raise AssertionError(f"run {run} never surfaced a question carrying a token")
        message = next_surface(run, environment)
        found = None if message is None else TOKEN.search(message)
        if found is not None:
            token = found.group(0)
        if token is not None:
            reply(run, environment, compose(token))
        time.sleep(0.2)


class PersistentManager:
    """A manager who keeps answering one question until the asker has the answer.

    Same holding of failures as `Manager` below and for the same reason. What differs is
    who decides they are done: the journey does, once the asking side reports back,
    because how many times an answer has to be sent is a property of who else is reading
    this run's channel rather than of the question.
    """

    def __init__(
        self,
        run: str,
        environment: dict[str, str],
        compose: Callable[[str], str],
        *,
        seconds: float = MANAGER_PATIENCE_SECONDS,
    ) -> None:
        self._failures: list[BaseException] = []
        self._stopping = threading.Event()
        self._thread = threading.Thread(
            target=self._play, args=(run, environment, compose, seconds), daemon=True
        )
        self._thread.start()

    def _play(
        self,
        run: str,
        environment: dict[str, str],
        compose: Callable[[str], str],
        seconds: float,
    ) -> None:
        try:
            answer_persistently(run, environment, compose, self._stopping, seconds=seconds)
        except BaseException as error:  # noqa: BLE001 - re-raised by `checked` below
            self._failures.append(error)

    def stop(self) -> None:
        """End the watch after the send in flight, if any."""
        self._stopping.set()

    def failure(self) -> BaseException | None:
        """Whatever the manager has already given up on, if it has given up at all.

        Read while somebody else is still blocked on them: a wrapper waits out its whole
        reply window for a manager who died in the first second, and the wait reports the
        window rather than the death. Checking this instead ends that wait at the cause.
        """
        return self._failures[0] if self._failures else None

    def checked(self, *, asker_said: str = "", seconds: float = 60) -> None:
        """Give the manager up to `seconds` to end, then re-raise whatever it hit.

        Deliberately not a promise that the thread has ended. It runs real recipes as
        subprocesses, so a manager told to stop can still be inside one, and a caller
        that has what it came for has no reason to hold the whole journey open for that.
        What must not be lost is a failure the manager already hit, which is what this
        reads and re-raises.
        """
        self._thread.join(timeout=e2e_timeout(seconds))
        if self._failures:
            failure = self._failures[0]
            if asker_said:
                raise AssertionError(f"{failure}\nthe asking side reported:\n{asker_said}")
            raise failure


class Manager:
    """The manager, played beside the asking side because the asking side blocks on them.

    Its failures are held rather than raised on its own thread: an assertion that died
    in a thread nobody joined would leave the asking side's own timeout as the only
    thing the test reported, which says nothing about why the answer never came.
    """

    def __init__(
        self,
        run: str,
        environment: dict[str, str],
        answers: list[Callable[[str], str]],
        *,
        seconds: float = MANAGER_PATIENCE_SECONDS,
    ) -> None:
        self._failures: list[BaseException] = []
        self._answers: list[subprocess.CompletedProcess[str]] = []
        self._surfaces: list[Surface] = []
        self._thread = threading.Thread(
            target=self._play, args=(run, environment, answers, seconds), daemon=True
        )
        self._thread.start()

    def _play(
        self,
        run: str,
        environment: dict[str, str],
        answers: list[Callable[[str], str]],
        seconds: float,
    ) -> None:
        try:
            self._answers.extend(
                answer_each(run, environment, answers, seconds=seconds, seen=self._surfaces)
            )
        except BaseException as error:  # noqa: BLE001 - re-raised by `checked` below
            self._failures.append(error)

    @property
    def surfaces(self) -> list[Surface]:
        """Every surface this manager read, for a journey counting what one ask raised.

        Read after `checked`. A manager stops reading once they have answered, so a
        journey wanting all of them drains whatever is still queued afterwards and adds
        it to this — which together is every surface, each seen through `channel-next`.
        """
        return list(self._surfaces)

    @property
    def answers(self) -> list[subprocess.CompletedProcess[str]]:
        """What the reply verb answered each send, for a journey whose subject is routing.

        Read after `checked`, which is where a manager that never got to send at all is
        re-raised as its own cause rather than as an empty list somebody has to explain.
        """
        return list(self._answers)

    def failure(self) -> BaseException | None:
        """Whatever the manager has already given up on, if it has given up at all.

        Read while somebody else is still blocked on them: a wrapper waits out its whole
        reply window for a manager who died in the first second, and the wait reports the
        window rather than the death. Checking this instead ends that wait at the cause.
        """
        return self._failures[0] if self._failures else None

    def checked(self, *, asker_said: str = "", seconds: float = 60) -> None:
        """Give the manager up to `seconds` to end, then re-raise whatever it hit.

        Deliberately not a promise that the thread has ended: a journey may leave a
        manager holding an answer nobody took, which is exactly what one of them is
        about. What must not be lost is a failure the manager already hit.

        `asker_said` is the asking side's own diagnostics, folded into the failure: the
        manager gives up when no question ever arrives, and the reason a question never
        arrived is something only the asking side said.
        """
        self._thread.join(timeout=e2e_timeout(seconds))
        if self._failures:
            failure = self._failures[0]
            if asker_said:
                raise AssertionError(f"{failure}\nthe asking side reported:\n{asker_said}")
            raise failure
