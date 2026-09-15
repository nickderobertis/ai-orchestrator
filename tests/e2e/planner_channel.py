"""Playing the manager on a live run's planner channel, for the journeys that need one.

A blocking question is only half a round trip. Every journey that drives
`scripts/ask-manager.sh` — from a subprocess, or from inside a real dispatch — needs
somebody on the other end doing what a manager does: `just channel-next` until a
question is there, then `just channel-reply --correlation <c>` with a ruling. The
correlation is the one `onemessagebus ask` stamped on the question, read off the surface
`channel-next` hands out, so the reply binds to that question and no other. That loop is
here rather than in each module because it is the same loop, and two copies of it drift
into two different ideas of what answering means.

It is written as the recipes a manager types, deliberately: `channel-next` is what hands a
surface out, and `channel-reply` is the bus's `reply` over this host's configuration.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import NotRequired, TypedDict, cast

from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: How long a manager keeps looking for a question to answer. Load-scaled like every
#: other hang guard here, because the manager is played by a thread driving real `just`
#: recipes and a loaded suite slows every one of them down.
MANAGER_PATIENCE_SECONDS = 120

#: This host's bus configuration, which every channel verb here reads.
BUS_CONFIG = REPO_ROOT / "config" / "onemessagebus.yaml"


class Surface(TypedDict):
    """One planner surface, narrowed to the fields a manager acts on.

    `blocking` decides whether they must answer it, and `correlation` — present on a
    question the bus asked — is what their reply binds by.
    """

    message: str
    blocking: bool
    correlation: NotRequired[str]


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
    return json.dumps({"version": 3, "completion": True, "message": message})


#: The surface a run raises about *itself*: its settlement write-back could not reach
#: the plan it was launched from. Nothing a journey does causes one and nothing in this
#: suite measures one — the projection has its own journeys — but the engine raises it on
#: runs these journeys launch, because onepipeline 0.18.4 cannot read the `location` this
#: checkout's plan store reports for every entity:
#: https://github.com/nickderobertis/onepipeline/issues/179. Handed out as the manager's
#: next surface it displaces the question a journey is waiting for, and it falsifies the
#: premise of a journey asserting that a run raised none, so both read past it. An engine
#: that can read the store raises none, and then this matches nothing and every reader
#: behaves exactly as it did before.
RUNS_OWN_PROJECTION_COMPLAINT = re.compile(r"did not take this run's projection")


def queue_may_hand_something_out(run: str, environment: dict[str, str]) -> bool:
    """Whether the run's `surfaces` queue holds anything waiting or pending, per the bus.

    Asked through `onemessagebus status`, which reads the queue without claiming anything,
    so a manager spends its wait looking rather than handing out through `channel-next`
    over an empty queue. `True` where the runs root is not named: the look can only ask
    where it knows the channel is, and a caller that did not say is read through the verb.
    """
    runs = environment.get("ONEPIPELINE_RUNS_DIR")
    if runs is None:
        return True
    channel = Path(runs) / run / "channel"
    if not channel.is_dir():
        return False
    looked = subprocess.run(
        [
            "onemessagebus",
            "status",
            "surfaces",
            "--config",
            str(BUS_CONFIG),
            "--transport-dir",
            str(channel),
        ],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
        check=False,
    )
    assert looked.returncode == 0, f"`onemessagebus status` over {channel} failed:\n{looked.stderr}"
    # `cast` rather than a validating read: `status` is the bus's own answer, one object per
    # queue, and the two members read here are only tested for being set.
    queues = cast(list[dict[str, object]], json.loads(looked.stdout))
    return any(queue.get("waiting") or queue.get("pending") is not None for queue in queues)


#: How long a waiting manager pauses between looks at a queue that holds nothing.
LOOK_INTERVAL_SECONDS = 0.2


def next_surface_record(run: str, environment: dict[str, str]) -> Surface | None:
    """Read the run's next unread surface whole, exactly as a manager reads one.

    A read that FAILS is raised rather than reported as an empty queue: treating a
    refusal as "nothing there yet" turns it into a timeout at the far end of a wait.
    Surfaces the run raised about itself are consumed and passed over — see
    `RUNS_OWN_PROJECTION_COMPLAINT`.
    """
    while True:
        handed = just("channel-next", run, environment=environment, seconds=60)
        assert handed.returncode == 0, (
            f"`just channel-next {run}` failed while reading this run's surfaces:\n"
            f"{handed.stderr}{handed.stdout}"
        )
        if not handed.stdout.strip():
            return None
        # `cast` rather than a validating read: `onepipeline next` owns this schema and
        # `SurfaceRead` states the part a manager consumes.
        surface = cast(SurfaceRead, json.loads(handed.stdout))["surface"]
        if surface is None or not RUNS_OWN_PROJECTION_COMPLAINT.search(surface["message"]):
            return surface


def next_surface(run: str, environment: dict[str, str]) -> str | None:
    """The text of the run's next unread surface, for a caller that wants only that."""
    surface = next_surface_record(run, environment)
    return None if surface is None else surface["message"]


def reply(
    run: str, environment: dict[str, str], envelope: str, correlation: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Send one reply envelope over the live channel through `just channel-reply`."""
    bound = [] if correlation is None else ["--correlation", correlation]
    return subprocess.run(
        ["just", "channel-reply", run, *bound],
        cwd=REPO_ROOT,
        env=environment,
        input=envelope,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )


def _next_question(
    run: str, environment: dict[str, str], seen: list[Surface] | None
) -> Surface | None:
    """Hand out the next surface when the queue holds one, and keep it when it asks."""
    if not queue_may_hand_something_out(run, environment):
        return None
    surface = next_surface_record(run, environment)
    if surface is not None and seen is not None:
        seen.append(surface)
    if surface is None or not surface.get("correlation") or not surface["blocking"]:
        return None
    return surface


def answer_each(
    run: str,
    environment: dict[str, str],
    answers: list[Callable[[Surface], str]],
    *,
    seconds: float = MANAGER_PATIENCE_SECONDS,
    seen: list[Surface] | None = None,
) -> list[subprocess.CompletedProcess[str]]:
    """Read surfaces until a blocking question appears, then reply to it. Once per answer.

    A surface that is not a blocking question the bus asked is read and passed over: this
    host's monitor and pacemaker raise their own on the same channel. Hands back what
    `channel-reply` answered each send, which is the bus's own `{answered, correlation,
    sent}` line.
    """
    limit = deadline(seconds)
    answered: list[subprocess.CompletedProcess[str]] = []
    for compose in answers:
        question = None
        while question is None:
            if time.monotonic() >= limit:
                raise AssertionError(f"run {run} never surfaced a blocking question")
            question = _next_question(run, environment, seen)
            if question is None:
                time.sleep(LOOK_INTERVAL_SECONDS)
        sent = reply(run, environment, compose(question), question.get("correlation"))
        assert sent.returncode == 0, f"the channel refused this reply:\n{sent.stderr}{sent.stdout}"
        answered.append(sent)
    return answered


class PersistentManager:
    """A manager who answers one question and keeps reading until the journey stops them.

    The journey decides when they are done, because a dispatched agent may ask only after
    a launch has run for a while. Failures are held rather than raised on the thread.
    """

    def __init__(
        self,
        run: str,
        environment: dict[str, str],
        compose: Callable[[Surface], str],
        *,
        seconds: float = MANAGER_PATIENCE_SECONDS,
    ) -> None:
        self._failures: list[BaseException] = []
        self._surfaces: list[Surface] = []
        self._stopping = threading.Event()
        self._thread = threading.Thread(
            target=self._play, args=(run, environment, compose, seconds), daemon=True
        )
        self._thread.start()

    def _play(
        self,
        run: str,
        environment: dict[str, str],
        compose: Callable[[Surface], str],
        seconds: float,
    ) -> None:
        try:
            limit = deadline(seconds)
            answered = False
            while not self._stopping.is_set():
                question = _next_question(run, environment, self._surfaces)
                if question is not None:
                    sent = reply(run, environment, compose(question), question.get("correlation"))
                    assert sent.returncode == 0, (
                        f"the channel refused this reply:\n{sent.stderr}{sent.stdout}"
                    )
                    answered = True
                elif not answered and time.monotonic() >= limit:
                    raise AssertionError(f"run {run} never surfaced a blocking question")
                time.sleep(LOOK_INTERVAL_SECONDS)
        except BaseException as error:  # noqa: BLE001 - re-raised by `checked` below
            self._failures.append(error)

    @property
    def surfaces(self) -> list[Surface]:
        """Every surface this manager read."""
        return list(self._surfaces)

    def stop(self) -> None:
        """End the watch after the read in flight, if any."""
        self._stopping.set()

    def failure(self) -> BaseException | None:
        """Whatever the manager has already given up on, if it has given up at all."""
        return self._failures[0] if self._failures else None

    def checked(self, *, asker_said: str = "", seconds: float = 60) -> None:
        """Give the manager up to `seconds` to end, then re-raise whatever it hit."""
        self._thread.join(timeout=e2e_timeout(seconds))
        if self._failures:
            failure = self._failures[0]
            if asker_said:
                raise AssertionError(f"{failure}\nthe asking side reported:\n{asker_said}")
            raise failure


class Manager:
    """The manager, played beside the asking side because the asking side blocks on them.

    Its failures are held rather than raised on its own thread: an assertion that died in
    a thread nobody joined would leave the asking side's own timeout as the only thing the
    test reported, which says nothing about why the answer never came.
    """

    def __init__(
        self,
        run: str,
        environment: dict[str, str],
        answers: list[Callable[[Surface], str]],
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
        answers: list[Callable[[Surface], str]],
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
        """Every surface this manager read, read after `checked`."""
        return list(self._surfaces)

    @property
    def answers(self) -> list[subprocess.CompletedProcess[str]]:
        """What `channel-reply` answered each send, read after `checked`."""
        return list(self._answers)

    def failure(self) -> BaseException | None:
        """Whatever the manager has already given up on, if it has given up at all."""
        return self._failures[0] if self._failures else None

    def checked(self, *, asker_said: str = "", seconds: float = 60) -> None:
        """Give the manager up to `seconds` to end, then re-raise whatever it hit.

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
