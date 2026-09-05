"""A dispatched agent asks its manager over the real planner channel, and can trust the answer.

`scripts/ask-manager.sh` is the one verb a planner has for stopping at a decision
fork instead of guessing, so what it may never do is hand back something that is not
the manager's answer. The channel gives it three ways to do exactly that, and all
three are real rather than hypothetical:

* it **answers its own timeouts**, at exit 0, with a ruling that reads like a
  decision (`no planner reply within the timeout; continue`);
* a reply is **claimed by whichever reader reaches it next**, so a live graph edit
  the manager addressed to the engine arrives at the asking call instead;
* and a frame it will never accept — a `node` the run does not have — is refused in
  a way a retry loop would bury.

So every journey here launches a real run through `just orchestrate`, drives the real
`scripts/ask-manager.sh` as a subprocess against that run's real channel, and answers
it through the real `just channel-next` and `just channel-reply`, the way a manager
does. Only the paid model is doubled, at the `oneharness` seam, exactly as
`tests/e2e/test_orchestrate_launch_e2e.py` doubles it — and these runs reach it only
in principle: the plan's frontier is a human gate, so nothing is ever dispatched.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, NamedTuple, NewType, Protocol, TypedDict, cast

import pytest
from nx_workspace import SHARED_TOOLCHAIN_GROUP
from planner_channel import (
    MANAGER_PATIENCE_SECONDS,
    TOKEN,
    Manager,
    PersistentManager,
    Surface,
    next_surface,
    next_surface_record,
    reply,
    reply_unguarded,
    ruling,
)
from project_fixtures import helper, project_from_plan
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The wrapper under test, run as an agent runs it, and the file beside it that
#: declares what a usable ruling is. A checkout holding one without the other is not a
#: checkout the wrapper can run in — it refuses, naming the missing helper — so the
#: journeys that stand a wrapper up somewhere else stand up both.
ASK_MANAGER = REPO_ROOT / "scripts" / "ask-manager.sh"
ASK_MANAGER_FILES = (ASK_MANAGER, REPO_ROOT / "scripts" / "ask-manager-contract.sh")

#: The stand-in for the paid model, and the provider binary beneath it. Neither is
#: reached by these runs — a human gate dispatches nothing — and both are named for
#: the same reason a seatbelt is worn on a short drive: a plan that came to dispatch
#: would otherwise spend real provider quota from a suite.
FAKE_BACKEND = helper("fake_backend.py")
FAKE_CODEX = helper("fake_codex.py")

#: A launching session these journeys state rather than inherit: this suite runs
#: inside a dispatch whose own harness session would otherwise own the runs.
LAUNCHING_SESSION = "e2e-ask-manager"

#: Every name a launcher identity reaches `scripts/onepipeline.sh` through, plus the
#: run the enclosing dispatch belongs to. That last one is what the wrapper reads, so
#: a journey that did not clear it would be asking the *outer* run's channel.
INHERITED_ENVIRONMENT = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "ONEPIPELINE_RUN_ID",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)

#: `onepipeline channel serve`'s own reply window, measured at 29.8 seconds. The
#: wrapper overrides it, and the journey below proves the override survives by
#: watching past this.
PUBLISHED_WINDOW_SECONDS = 30

#: How far past that window a call must still be waiting to have proven the override.
OBSERVED_WAITING_AFTER_SECONDS = 35

#: The reply window these journeys pin, where waiting is not the subject. Short, so a
#: journey whose manager never answers fails in seconds rather than at the default.
SHORT_WINDOW_SECONDS = 5

#: The reply window the wrapper is given while a manager looks for the question — through
#: `planner_channel.Manager`, or through `_waited_for_question` directly.
#: Deliberately the LONGER of the two, and that ordering is the point: with it
#: the other way round the wrapper gave up first, so a journey whose manager was merely
#: slow reported the timeout refusal — a real behavior, just not the one it was about —
#: and a journey whose manager genuinely failed reported nothing about why.
ANSWERED_WINDOW_SECONDS = int(e2e_timeout(MANAGER_PATIENCE_SECONDS * 2))

#: The reply window the misrouted-edit journey pins, and deliberately neither of the
#: two above. There the wrapper must outlast the manager because it is waiting for an
#: answer; here it is waiting for one that never comes — a live edit no longer answers
#: a question — so the window is what the journey *spends*, and the whole of it. It
#: only has to outlast a manager's send rather than a manager's search, since the send
#: is what has to land while the question is still pending, and a manager who loses
#: that race is refused by the channel and re-raised as itself rather than passing.
MISROUTED_EDIT_WINDOW_SECONDS = int(e2e_timeout(60))

#: A run's own name on the ledger. Every planner-facing verb takes one and the
#: wrapper reads one out of the environment, so it is distinguished from the prose it
#: is built out of: what makes a string a run id is where it came from.
RunId = NewType("RunId", str)

#: How many times `scripts/ask-manager.sh` puts one question to the channel before it
#: gives up. Restated from the wrapper rather than imported, because it is shell. The
#: journey that drives it to exhaustion reads the number back out of the refusal the
#: wrapper writes, so the two disagreeing is that assertion failing on the wrapper's own
#: sentence rather than a manager left holding an answer nobody came for.
MAX_ATTEMPTS = 4

#: The release the decoy `onetaskgraph` reports. Any release this checkout does not
#: adopt; the journey below holds it to differing from the pin.
DECOY_PIN = "0.0.1"

#: Read rather than restated, so the decoy is held to the real pin.
ADOPTED_ONETASKGRAPH = (
    (REPO_ROOT / "config" / "onetaskgraph.version").read_text(encoding="utf-8").strip()
)

#: The agent node every plan below carries. It is never dispatched — it depends on the
#: human gate, which nobody attests — and it exists so a `context` live edit has a
#: node to be addressed to, which is how the misrouted-edit journey gets a real one.
WORK_NODE = "work"


class Asked(NamedTuple):
    """A live run to ask questions on, and the environment every verb needs to see it."""

    environment: dict[str, str]
    run: RunId


def _sandboxed_home(tmp_path: Path) -> dict[str, str]:
    """A `HOME` of this journey's own, carrying a decoy at the once-shared tool path.

    Two reasons, and the second is what this returns rather than merely sets. Host
    state under the real `HOME` — an operator's `~/.config/onetaskgraph/secrets.env`,
    a Claude or codex config directory — reaches a launch these journeys make, so a
    sandbox is what makes them answer about this checkout. And the standalone
    `onetaskgraph` CLI was installed into `$HOME/.local/bin` until provisioning moved
    it into each checkout's own `.venv/bin`: one path the whole host shared, which the
    canonical checkout reverted below this one's pin roughly every 80 seconds, and
    four cases here failed every time it did.

    So the sandbox plants a *differently pinned* copy there and puts that directory
    first on `PATH`. Resolution then has to be positive rather than accidental: a
    launch reaches `onetaskgraph` through `uv run`, which prepends this checkout's own
    environment, so the decoy is passed over even from the front of the search path.
    Every journey in this module runs against it, and the one below asserts it.
    """
    home = tmp_path / "home"
    shared_bin = home / ".local" / "bin"
    shared_bin.mkdir(parents=True, exist_ok=True)
    decoy = shared_bin / "onetaskgraph"
    decoy.write_text(
        f"#!/usr/bin/env bash\nprintf '%s\\n' 'onetaskgraph {DECOY_PIN}'\n", encoding="utf-8"
    )
    decoy.chmod(0o755)
    return {
        "HOME": str(home),
        "PATH": os.pathsep.join((str(shared_bin), os.environ["PATH"])),
        # uv resolves this project's environment on every `uv run` and caches that
        # under the real `HOME`. Re-resolving it per journey costs minutes and a
        # network, neither of which any journey here is about.
        "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR") or str(Path.home() / ".cache" / "uv"),
    }


def _environment(tmp_path: Path) -> dict[str, str]:
    """The environment one launched run and the questions asked on it share."""
    environment = dict(os.environ)
    for name in INHERITED_ENVIRONMENT:
        environment.pop(name, None)
    environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    environment["XDG_STATE_HOME"] = str(tmp_path / "state")
    environment.update(_sandboxed_home(tmp_path))
    return environment


def _just(
    *args: str, environment: dict[str, str], seconds: float = 120
) -> subprocess.CompletedProcess[str]:
    """Run one real recipe from this checkout."""
    return subprocess.run(
        ["just", *args],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
    )


#: The two argv words a `channel serve` is recognised by, matched consecutively and
#: always followed by the run id. Whole words rather than a substring search, and never
#: without the run: this host runs several dispatches at once, so the only processes a
#: journey may look at — let alone signal — are the ones it started itself. A
#: `pgrep -f "channel serve"` matches a sibling's server, and matches the polling shell
#: that typed the pattern.
SERVE_ARGV = ("channel", "serve")

#: How long a reaped server is given to actually be gone before it is called a leak.
#: A signalled process is not gone the instant `communicate` returns for its parent, and
#: the failure this guards is a server that outlives the *suite* by minutes.
SURVIVOR_GRACE_SECONDS = 15

#: What makes this suite run's run ids its own, and it is load-bearing rather than
#: cosmetic. A journey's run is named after the journey, so two suites running this
#: module mint the same name — and the run id is the whole of how `_serving` decides
#: which servers a journey started. Without this, one suite's `_no_survivors` SIGKILLs
#: another's `channel serve` and each reads the other's work as its own leak.
#:
#: The victim reads that kill as the channel refusing its question — an engine that
#: would not answer — rather than as a stranger's, so the collision is invisible at the
#: point it is reported.
#:
#: Keyed on the checkout and this worker's process, because both collide: two checkouts
#: of this repository run at once here, and so do two suites of one checkout. Under
#: xdist each worker imports this module itself, so this is that worker's own.
SUITE = hashlib.sha256(f"{REPO_ROOT}\0{os.getpid()}".encode()).hexdigest()[:8]


def _serving(run: RunId) -> list[int]:
    """Every live `onepipeline channel serve` for exactly this run, by pid.

    Read out of `/proc` rather than by shelling out to `pgrep`, for the same reason the
    match is `channel serve <this run>` and not a substring: `pgrep -f "channel serve"`
    would match a sibling dispatch's server, and this suite has no business knowing one
    is there — still less signalling it.
    """
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            words = (entry / "cmdline").read_bytes().decode("utf-8", "replace").split("\0")
        except OSError:
            # It exited between the listing and the read, which is the one thing this
            # is looking for anyway.
            continue
        for at in range(len(words) - 2):
            if tuple(words[at : at + 2]) == SERVE_ARGV and words[at + 2] == run:
                found.append(int(entry.name))
                break
    return found


def _no_survivors(run: RunId) -> None:
    """Kill any `channel serve` this run left behind, then fail because it was there.

    Asserted per run rather than trusted to `_reaped`, because a test that exits while
    its child lives still passes: without this, a journey that grows a new way to end an
    ask reports the leak on somebody's process table instead of here. Killing before
    failing keeps the report from being the only thing the guard achieves.
    """
    limit = deadline(SURVIVOR_GRACE_SECONDS)
    while (survivors := _serving(run)) and time.monotonic() < limit:
        time.sleep(0.2)
    for pid in survivors:
        # Gone between the scan and here is the outcome this wants, not a problem.
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
    assert not survivors, (
        f"run {run}'s journey exited while {len(survivors)} 'onepipeline channel serve' "
        f"process(es) it started were still running (pid(s) {survivors}, now killed). An "
        f"ask must be ended with `_reaped`, which signals the whole process group; "
        f"signalling the wrapper alone orphans its server to init and pins the directory "
        f"it was started in."
    )


@pytest.fixture
def asked(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[Asked]:
    """A launched run whose frontier is a human gate, so its channel outlives the launch.

    `--dag-graph off` deliberately: with this host's observer graph attached, the
    monitor and the pacemaker raise surfaces of their own and answer on the same
    channel, and a question's answer would be racing theirs. That race is real and is
    what the correlation token exists for — it is driven below with an envelope this
    journey sends itself, rather than by hoping a monitor produces one.

    The launch returns as soon as the gate is reached (`awaiting-planner`), which is
    what makes this cheap: the ledger, the surfaces, and the reply rendezvous are all
    live afterwards, and nothing is running to spend anything.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    # Named after the journey, so a run left behind says which one left it — and
    # sanitized to what `onepipeline` mints a run id from unchanged, because a
    # parametrized id carries brackets and spaces that a run id is not. `SUITE` is what
    # keeps that name this suite run's alone; see its declaration for what shares a host.
    named = re.sub(r"[^A-Za-z0-9]+", "-", request.node.name)[-40:].strip("-")
    run = RunId(f"ask-manager-{SUITE}-{named}")
    environment = _environment(tmp_path)
    plan = tmp_path / "asked.plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "goal": {"text": "hold a channel open for an agent's question"},
                "name": run,
                "tasks": [
                    {
                        "id": "gate",
                        "kind": "human",
                        "task": "## What\nApprove.\n\n## Why\nHold the run.\n\n"
                        "## Acceptance criteria\n- Approved.",
                    },
                    {
                        "id": WORK_NODE,
                        "persona": "engineer",
                        "deps": ["gate"],
                        "task": "## What\nReport.\n\n## Why\nNever reached.\n\n"
                        "## Acceptance criteria\n- Reported.",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    launch = _just(
        "orchestrate",
        project_from_plan(plan),
        "--dag-graph",
        "off",
        environment=environment,
    )
    assert launch.returncode == 0, f"the launch failed:\n{launch.stdout}\n{launch.stderr}"
    try:
        yield Asked(environment, run)
    finally:
        _just("stop", run, environment=environment, seconds=60)
        # After the stop, deliberately: ending the run is what releases a server still
        # blocked on its reply window, so asking before it would report a leak that was
        # about to clear itself.
        _no_survivors(run)


def _ask(
    asked: Asked,
    *arguments: str,
    window: int = SHORT_WINDOW_SECONDS,
    overrides: dict[str, str] | None = None,
    dropping: tuple[str, ...] = (),
    stdin: str | None = None,
    cwd: Path = REPO_ROOT,
) -> subprocess.Popen[str]:
    """Start the real wrapper the way a dispatched agent runs it.

    Always in a session of its own, so that the wrapper and the `channel serve` it
    starts are one process group `_reaped` can end in one signal.

    `cwd` and `dropping` are what let a journey ask from somewhere other than this
    checkout, as a lifecycle dispatch does. Removing a name is a separate seam from
    overriding one on purpose: what a dispatch's environment says about the runs root is
    *nothing at all*, and an override can only ever say something.
    """
    environment = dict(asked.environment)
    environment["ONEPIPELINE_RUN_ID"] = asked.run
    environment["ORCHESTRATOR_ASK_MANAGER_TIMEOUT_SECONDS"] = str(window)
    environment.update(overrides or {})
    for name in dropping:
        environment.pop(name, None)
    asking = subprocess.Popen(  # noqa: S603 - the real wrapper, as an agent runs it
        [str(ASK_MANAGER), *arguments],
        cwd=cwd,
        env=environment,
        text=True,
        stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    if stdin is not None and asking.stdin is not None:
        asking.stdin.write(stdin)
        # Closed here rather than by the caller: the wrapper reads its question to EOF,
        # so the write side has to finish before there is a question to wait for. The
        # handle goes with it, because `communicate` flushes whatever `stdin` still
        # names before it drains the other two pipes and raises on a closed one — which
        # turned every reap of a piped ask into a failure after the journey had already
        # proved its point.
        asking.stdin.close()
        asking.stdin = None
    return asking


#: How long `_reaped` goes on re-signalling a group that still has a member in it, and
#: how long it waits between passes. Bounded rather than open-ended: a member that will
#: not go is a leak to report through `_no_survivors`, not a reason to hang the suite.
REAP_GRACE_SECONDS = 10
REAP_INTERVAL_SECONDS = 0.1


def _grouped(leader: int) -> list[int]:
    """Every live process in `leader`'s process group except the leader itself.

    Read out of `/proc` rather than asked with `killpg(pgid, 0)`, for two reasons that
    both make the kernel's answer the wrong one. The leader stays in its own group as a
    zombie until `communicate` reaps it, so `killpg` reports a member for the whole
    window whatever else is going on; and the question here is precisely about
    *everybody else*, since the leader is the one member already accounted for.
    """
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == leader:
            continue
        try:
            stat = (entry / "stat").read_bytes()
        except OSError:
            # It exited between the listing and the read, which is the outcome this
            # is looking for anyway.
            continue
        # `comm` is parenthesized and may itself hold spaces and parentheses, so the
        # fields are taken from the last `)` rather than by splitting the whole line:
        # after it come state, ppid, and pgrp, in that order.
        after = stat.rpartition(b")")[2].split()
        if len(after) < 3 or after[0] == b"Z":
            continue
        if int(after[2]) == leader:
            found.append(int(entry.name))
    return found


def _reaped(
    asking: subprocess.Popen[str],
    *,
    sending: int = signal.SIGKILL,
    seconds: float = 60,
) -> tuple[str, str]:
    """End one ask and everything it started, and do not return until they are gone.

    The group rather than the process, always: `channel serve` is a child of the
    wrapper, so signalling the wrapper alone leaves the server running, reparented to
    init and pinning its working directory until its reply window elapses.

    The group is named by `asking.pid` directly rather than through `os.getpgid`, which
    `start_new_session` in `_ask` is what makes valid. The lookup would race a wrapper
    that has just exited, and its answer would then be this test runner's own group —
    the one group that must never be signalled here.

    Signalled until the group is empty rather than once, because one pass is not the
    same as one group. `scripts/ask-manager.sh` asks up to `MAX_ATTEMPTS` times and
    forks a `channel serve` per attempt, and `killpg` signals the members the kernel
    finds as it walks the list — so a server forked while that walk is in progress is
    in the group and past the point the walk had reached, and lives on. That survivor
    is what `_no_survivors` then fails the whole journey's teardown over, which is a
    refused publication for a race in the reaping rather than anything about the seam.
    """
    limit = deadline(REAP_GRACE_SECONDS)
    while True:
        # A group already gone is the state this is for; the pipes are still drained
        # below, because draining them is also what reaps the wrapper.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(asking.pid, sending)
        if not _grouped(asking.pid) or time.monotonic() >= limit:
            break
        time.sleep(REAP_INTERVAL_SECONDS)
    return asking.communicate(timeout=e2e_timeout(seconds))


class Watched(Protocol):
    """Whoever is playing the manager beside an ask, narrowed to what `_finish` reads.

    Both `planner_channel.Manager` and `planner_channel.PersistentManager` are one, and
    they are watched through the same one method because what matters here is the same
    for either: whether they have already given up.
    """

    def failure(self) -> BaseException | None: ...


#: How often `_finish` looks up from the wrapper to see whether the manager is still
#: there. Short and unscaled: it is a polling interval rather than a hang guard, and the
#: cost of one look is a `select` that has already timed out.
WATCH_INTERVAL_SECONDS = 0.5


def _finish(
    asking: subprocess.Popen[str],
    *,
    seconds: float = 180,
    manager: Watched | None = None,
) -> tuple[int, str, str]:
    """Wait for one wrapper invocation and hand back what it reported.

    The manager is watched alongside it wherever there is one, for the reason
    `_await_answer` in `tests/ask_seam/test_launch_ask_seam_e2e.py` gives: the two are one
    round trip, and only one of the two failures is visible from this side. A manager
    who gave up leaves the wrapper blocking for its whole reply window — longer than the
    guard here — so waiting it out reports a killed wrapper with nothing on either pipe.
    That is what a real gate run reported, in place of the sentence saying why nobody
    answered.
    """
    limit = deadline(seconds)
    while True:
        try:
            out, err = asking.communicate(timeout=WATCH_INTERVAL_SECONDS)
        except subprocess.TimeoutExpired:
            stopped = None if manager is None else manager.failure()
            if stopped is not None:
                out, err = _reaped(asking)
                raise AssertionError(
                    f"the manager stopped before the wrapper had its answer: {stopped}\n"
                    f"the wrapper was still waiting, and reported:\n{out}\n{err}"
                ) from None
            if time.monotonic() >= limit:
                out, err = _reaped(asking)
                raise AssertionError(f"the wrapper never returned:\n{out}\n{err}") from None
            continue
        return asking.returncode, out, err


#: Every journey in this module is pinned to one worker. Not because the channel is
#: shared — each launches its own run under its own runs root — but because each one is
#: a wrapper process and a manager thread both waiting on `just` recipes, and four of
#: those racing the rest of a full suite is what turned a several-second round trip into
#: one that outlived the window it was given.
#:
#: `tests/e2e/nx_workspace.py`'s group, because every one of those `just` recipes
#: reaches its tool through `uv run`, which waits on the exclusive lock a journey
#: re-provisioning this checkout holds. A group of this module's own would co-locate
#: these journeys with each other and leave that writer free to run beside them on
#: another worker, which is not a constraint at all: `--dist loadgroup` serialises one
#: group name, never two.
CHANNEL_GROUP = SHARED_TOOLCHAIN_GROUP

#: Applied to the module rather than per journey, because what has to be serialised is
#: launching a run and `asked` is what launches one — function-scoped, so the refusal
#: journeys spend a real `just orchestrate` and `just stop` exactly as the round trips
#: do, however inert their bodies read. One `pytestmark` rather than a decorator per
#: test, so a journey added here cannot miss it; `tests/test_nx_cache_scope.py` holds
#: the rule and records what an ungrouped one cost.
pytestmark = pytest.mark.xdist_group(CHANNEL_GROUP)


def _waited_for_question(
    asked: Asked,
    asking: subprocess.Popen[str],
    *,
    seconds: float = MANAGER_PATIENCE_SECONDS,
    named: str = "the question",
) -> str:
    """Read surfaces until the question is there, and hand back what a manager would see.

    The asking process is watched alongside the queue, because the two ways this can
    fail need different repairs and only one of them is visible from the queue: a
    wrapper that refused its own invocation raises no surface ever, and waiting out the
    deadline for it reports an empty queue while throwing away the sentence that says
    why it is empty.

    `named` is what a caller that waits more than once calls this one, because the two
    failures below name the run and the run alone — and a journey that asks the same
    question two ways under the same run id then reports a deadline nothing can attribute
    to the way of asking that hung.
    """
    limit = deadline(seconds)
    while True:
        message = next_surface(asked.run, asked.environment)
        if message is not None:
            return message
        if asking.poll() is not None:
            out, err = asking.communicate()
            raise AssertionError(
                f"the asking side exited {asking.returncode} without {named} ever "
                f"reaching run {asked.run}'s channel:\n{err}{out}"
            )
        assert time.monotonic() < limit, f"{named} never reached run {asked.run}'s channel"
        time.sleep(0.2)


#: The answer a manager gives, so what arrives on stdout can be compared to it whole.
ANSWER = "Key it on the whole workspace; the narrower key would replay a stale verdict."

#: The line the wrapper puts between a surface's protocol and its body. What is above it
#: is how to answer; what is below it is what was asked.
PROTOCOL_SEPARATOR = "\n--\n"


def test_the_wrapper_answers_with_the_managers_message_and_nothing_else(asked: Asked) -> None:
    """The happy round trip: an agent asks, a manager answers, the agent reads the answer.

    Everything between the two is real — the wrapper, `onepipeline channel serve`, the
    run's ledger, `just channel-next`, and `just channel-reply`. What is asserted is
    the whole contract a caller depends on: exit 0, the manager's `message` on stdout,
    and nothing else there. A wrapper that printed the wire object instead would leave
    every caller parsing JSON out of what was supposed to be an answer.
    """
    asking = _ask(asked, "Should the test key cover docs?", window=ANSWERED_WINDOW_SECONDS)
    manager = Manager(asked.run, asked.environment, [lambda token: ruling(f"{ANSWER} {token}")])

    status, out, err = _finish(asking, manager=manager)
    manager.checked(asker_said=err)

    assert status == 0, f"the wrapper did not accept the manager's answer:\n{err}"
    assert TOKEN.sub("", out).strip() == ANSWER, out
    assert err == "", f"a successful ask reported something on stderr:\n{err}"


def test_the_round_trip_survives_a_differently_pinned_tool_at_the_once_shared_path(
    asked: Asked,
) -> None:
    """The same round trip, with a wrong-pinned `onetaskgraph` first on the search path.

    Provisioning installed that CLI into `$HOME/.local/bin` until it moved into each
    checkout's own `.venv/bin`, and while the path was shared a sibling checkout
    reinstalled its own release over this one — measured at roughly one reversion every
    80 seconds — which is what failed four cases here at a time. `_sandboxed_home`
    plants exactly that: a copy reporting a release this checkout does not adopt, at
    that path, ahead of everything else on `PATH`.

    Both halves are asserted, because either alone passes for the wrong reason. That
    the decoy is really there and really disagrees is what says the environment was
    arranged; that the launch in the fixture and the ask below both succeed anyway is
    what says resolution goes through this checkout's own environment rather than
    through whichever copy the search path reaches first.
    """
    decoy = Path(asked.environment["HOME"]) / ".local" / "bin" / "onetaskgraph"
    reported = subprocess.run([str(decoy)], text=True, capture_output=True, check=False)
    assert reported.stdout.strip() == f"onetaskgraph {DECOY_PIN}", (
        f"the decoy at {decoy} reports {reported.stdout.strip()!r}; this journey is "
        "about a wrong-pinned copy being passed over, so there has to be one"
    )
    assert asked.environment["PATH"].split(os.pathsep)[0] == str(decoy.parent), (
        "the decoy is not first on PATH, so passing it over proves nothing about how "
        f"the tool is resolved: {asked.environment['PATH']}"
    )
    assert ADOPTED_ONETASKGRAPH != DECOY_PIN, (
        f"config/onetaskgraph.version now adopts {ADOPTED_ONETASKGRAPH}, which is the "
        "decoy's own release; pick another for DECOY_PIN"
    )

    asking = _ask(asked, "Should the test key cover docs?", window=ANSWERED_WINDOW_SECONDS)
    manager = Manager(asked.run, asked.environment, [lambda token: ruling(f"{ANSWER} {token}")])

    status, out, err = _finish(asking, manager=manager)
    manager.checked(asker_said=err)

    assert status == 0, (
        "the round trip failed with a wrong-pinned onetaskgraph first on PATH, which "
        f"is the state a sibling checkout used to leave this host in:\n{err}"
    )
    assert TOKEN.sub("", out).strip() == ANSWER, out


def test_the_channels_own_timeout_ruling_is_refused_rather_than_returned(asked: Asked) -> None:
    """A synthesized verdict is worse than no verdict, because it is actionable.

    Nobody answers here, so `channel serve` answers itself: exit 0, valid JSON, and
    `{"completion": false, "message": "no planner reply within the timeout; continue"}`
    — a ruling an agent would act on. It happened on this plan's own dispatch. So the
    wrapper must exit non-zero, name the timeout as the cause, and put nothing on
    stdout for a caller to mistake for a decision.

    This is also the drift gate on the `reason` string the refusal keys off: were a
    future `onepipeline` to reword it, the synthesized ruling would stop matching, be
    re-asked as somebody else's answer, and fail with the *other* message — so this
    assertion is what says which of the two happened.
    """
    asking = _ask(asked, "Nobody is watching this run.", window=SHORT_WINDOW_SECONDS)

    status, out, err = _finish(asking)

    assert status != 0, f"the wrapper accepted the channel's own timeout as an answer:\n{out}"
    assert out == "", f"a refused question still printed a ruling:\n{out}"
    assert "synthesized its own ruling" in err and "no manager answered" in err, err


def test_a_manager_live_edit_is_not_handed_to_the_asking_call_as_its_answer(
    asked: Asked,
) -> None:
    """A graph mutation sent while this question is pending does not answer it.

    This journey was written for a delivery that no longer happens. Through onepipeline
    0.8.x the channel was a durable queue whose replies were claimed by whichever reader
    reached one next, so a manager's live edit —
    `{"version":1,"author":"monitor","commands":[{"op":"context",...}]}`, measured on a
    real re-ask — arrived at the asking call by arrival order alone, and the wrapper had
    to refuse it naming a cause of its own.

    **The adopted release routes a reply by the halves it carries**, so a commands-only
    envelope belongs to the command path and the verdict rendezvous this call waits on
    never holds it. Asserting the old refusal would now be asserting about a release
    nothing runs — the wrapper would simply wait, which is what it did, for its whole
    window.

    So the routing itself is what is measured, and by the verb that performed it rather
    than by inference: `just channel-reply` accepts the envelope, answers
    `{"reply":0,"state":"applied"}` — the edit landed on the graph, and it answered
    **zero** surfaces though a question was pending on this very run — and the asking
    call, handed none of it, falls through to the timeout refusal an unanswered question
    gets. The wrapper's own non-ruling check is deliberately left standing and is proved
    by `test_a_ruling_carrying_the_token_but_no_decision_is_refused`: it is what stands
    between an agent and a graph edit returned as prose if a release ever regresses.
    """
    asking = _ask(
        asked, "Which cursor shape should the route take?", window=MISROUTED_EDIT_WINDOW_SECONDS
    )
    edit = json.dumps(
        {
            "version": 1,
            "author": "monitor",
            "commands": [{"op": "context", "id": WORK_NODE, "note": "the base moved under you"}],
        }
    )
    manager = Manager(asked.run, asked.environment, [lambda _token: edit])

    status, out, err = _finish(asking, seconds=MISROUTED_EDIT_WINDOW_SECONDS + 120, manager=manager)
    manager.checked(asker_said=err)

    applied = [
        json.loads(answered.stdout)
        for answered in manager.answers
        if answered.stdout.strip().startswith("{")
    ]
    assert applied, (
        f"the reply verb answered nothing this journey can read the routing out of, so "
        f"neither half below is evidence about it: {[a.stdout for a in manager.answers]}"
    )
    assert all(answer.get("state") == "applied" for answer in applied), (
        f"a commands-only envelope was no longer applied to the graph, so this is a "
        f"lost edit rather than a routed one, and `docs/orchestration.md`'s quoted "
        f"answer wants re-measuring in this change: {applied}"
    )
    assert all(answer.get("reply") == 0 for answer in applied), (
        f"the live edit answered a pending surface, so the release has gone back to "
        f"routing by arrival order and this call's question can be consumed by a graph "
        f"edit again — the wrapper's non-ruling check is now load-bearing: {applied}"
    )

    assert status != 0, f"the wrapper returned a live graph edit as an answer:\n{out}"
    assert out == "", f"a refused question still printed a ruling:\n{out}"
    assert "synthesized its own ruling" in err and "no manager answered" in err, (
        f"the question was answered by something, though the only reply sent was a "
        f"graph edit the verb says answered no surface:\n{err}"
    )


def test_a_ruling_carrying_the_token_but_no_decision_is_refused(asked: Asked) -> None:
    """Echoing the token does not make an envelope a ruling.

    The token proves an answer was addressed to *this* question; `completion` proves it
    is an answer at all. Both are needed, and this is the case that separates them: a
    reply carrying the token and no decision reaches the wrapper — measured, the channel
    hands the envelope back verbatim — and must be refused rather than returned as the
    manager's prose.

    The manager here sends through the engine rather than through `just channel-reply`,
    because that recipe now refuses this exact envelope before it is sent —
    `test_an_envelope_the_pending_question_cannot_use_is_refused_where_it_is_sent` is
    what holds that. The two are complementary rather than redundant: the recipe stops a
    manager sending one, and this classifier is what still stands between an agent and
    an envelope that reached the channel by some other route.
    """
    asking = _ask(asked, "Is the cursor opaque?", window=ANSWERED_WINDOW_SECONDS)
    undecided = [lambda token: json.dumps({"version": 1, "message": f"maybe {token}"})]
    manager = Manager(asked.run, asked.environment, undecided, send=reply_unguarded)

    status, out, err = _finish(asking, manager=manager)
    manager.checked(asker_said=err)

    assert status != 0, f"the wrapper returned a decision-less envelope as an answer:\n{out}"
    assert out == "", f"a refused question still printed a ruling:\n{out}"
    assert "is not a ruling" in err, err


def test_a_ruling_addressed_to_another_reader_is_re_asked_rather_than_returned(
    asked: Asked,
) -> None:
    """The correlation token is the general remedy for a reply bound to the wrong reader.

    A ruling is a well-formed decision and still not this question's answer, and
    nothing in its shape says so — which is why the wrapper mints a token, puts it in
    the question, and requires it back. Here a perfectly valid ruling that never saw
    the token arrives first; the wrapper must ask again rather than hand it over, and
    the second ruling — the one that echoes the token — is the answer.

    That the question is re-asked and not merely rejected is what makes this usable: a
    manager who answered the wrong surface gets another chance at the right one,
    without the agent having to be restarted.

    The manager sends through the engine rather than through `just channel-reply`,
    because that recipe now refuses this exact envelope before it is sent —
    `test_a_ruling_that_does_not_echo_the_pending_questions_token_is_refused_where_it_is_sent`
    is what holds that. The two are complementary rather than redundant: the recipe stops
    a manager sending one, and this classifier is what still stands between an agent and
    a tokenless ruling that reached the channel by some other route.
    """
    misdirected = ruling("yes, that other node can be dropped")
    asking = _ask(asked, "Should the listing be paginated?", window=ANSWERED_WINDOW_SECONDS)
    manager = Manager(
        asked.run,
        asked.environment,
        [lambda _token: misdirected, lambda token: ruling(f"{ANSWER} {token}")],
        send=reply_unguarded,
    )

    status, out, err = _finish(asking, manager=manager)
    manager.checked(asker_said=err)

    assert status == 0, f"the wrapper did not survive a ruling meant for another reader:\n{err}"
    assert "that other node can be dropped" not in out, (
        f"the wrapper handed back a ruling that never saw its token:\n{out}"
    )
    assert TOKEN.sub("", out).strip() == ANSWER, out


#: An envelope a manager would plausibly write and the channel would plausibly accept,
#: and which the asking wrapper then discards: it decides nothing, so it is not a
#: ruling. Three of exactly this shape were each reported `delivered` on this host and
#: each read by nobody, leaving the planner that asked blocked for about thirty-five
#: minutes while it re-asked twice.
UNUSABLE_REPLY = json.dumps({"version": 1, "message": "yes, key it on the whole workspace"})

#: Every other way an envelope can fail to be a ruling, each named by what a manager
#: would have done to produce it. All four are one rule — the asking wrapper's own — and
#: all four are discarded there in silence, so the refusal has to reach all four.
UNUSABLE_REPLIES = (
    ("no completion field", UNUSABLE_REPLY),
    ("not JSON at all", "yes, key it on the whole workspace"),
    ("a JSON list", json.dumps([{"completion": True}])),
    ("a completion that is a string", json.dumps({"version": 1, "completion": "true"})),
)

#: A live edit, which carries no `completion` by design and which the adopted release
#: routes to the command path rather than to the waiting reader. It must still be sent
#: while a question is pending: that is precisely when a manager most needs to steer,
#: and a guard that refused it would take that away for the whole time the question is
#: unanswered.
LIVE_EDIT = json.dumps(
    {
        "version": 1,
        "author": "planner",
        "commands": [{"op": "context", "id": WORK_NODE, "note": "the base moved under you"}],
    }
)


def _waited_for_a_queued_question(
    asked: Asked, carrying: str, *, seconds: float = MANAGER_PATIENCE_SECONDS
) -> str:
    """The text of a blocking question carrying `carrying`, while it is still unread.

    Read off the queue rather than through `just channel-next`, because handing the
    surface out is the very thing these journeys must not do: the refusal they are about
    fires for a question whose rendezvous is not open, and reading one opens it.
    """
    limit = deadline(seconds)
    while True:
        # llmlint: ignore[tests_mirror_real_usage] Reading a surface through the verb hands it out.
        queue = (
            Path(asked.environment["ONEPIPELINE_RUNS_DIR"]) / asked.run / "channel" / "queue.json"
        )
        if queue.is_file():
            # `onepipeline` owns this file's schema; `waiting` is the list of surfaces
            # nobody has read, and each entry is checked by what is asserted of it.
            held = cast(dict[str, Any], json.loads(queue.read_text(encoding="utf-8")))
            waiting = held.get("waiting")
            for surface in waiting if isinstance(waiting, list) else []:
                if not isinstance(surface, dict) or surface.get("blocking") is not True:
                    # Checked rather than assumed. What these journeys are about is a
                    # rendezvous that is not open, and only a blocking surface has one:
                    # a non-blocking surface carrying the same text would satisfy the
                    # search while proving nothing about the state under test.
                    continue
                if carrying in (surface.get("message") or ""):
                    return cast(str, surface["message"])
        assert time.monotonic() < limit, (
            f"no blocking question carrying {carrying!r} was ever queued on run {asked.run}"
        )
        time.sleep(0.2)


def _pending(asked: Asked) -> dict[str, object] | None:
    """The surface a reply would bind to, read out of the channel's own queue.

    Read from the queue rather than through `just channel-next`, deliberately: reading a
    surface is what a manager does *to* the queue, and this asks what is still waiting
    without touching it. The file is `onepipeline`'s, and `pending` is the object a
    reply binds to.
    """
    # llmlint: ignore[tests_mirror_real_usage] Reading a surface consumes it.
    queue = Path(asked.environment["ONEPIPELINE_RUNS_DIR"]) / asked.run / "channel" / "queue.json"
    if not queue.is_file():
        return None
    # `cast` rather than a validating read: `onepipeline` owns this file's schema, and
    # what this journey reads off it is one object under one key.
    return cast(dict[str, object] | None, json.loads(queue.read_text(encoding="utf-8"))["pending"])


def test_an_envelope_the_pending_question_cannot_use_reaches_the_channel_unrefused(
    asked: Asked,
) -> None:
    """The refusal this used to hold is withdrawn, and the wrapper is what closes the silence.

    `just channel-reply` refused these while a question was pending: none carries a
    boolean `completion`, so the waiting wrapper discards them without a word. The
    withdrawal is a measurement rather than a preference — the monitor's own score is a
    `completion` carrying no correlation token, so from the queue it and a manager's
    token-less answer are the same bytes. Keyed on what the queue holds, the guard
    refused both, which takes the supervisory tier's answer path away for as long as any
    agent is waiting.

    So the recipe refuses only what an envelope says about *itself* — that it names a
    blocking question the run has not handed out — and everything here goes through, to
    be discarded by `scripts/ask-manager.sh` exactly as it always was.
    `test_a_ruling_carrying_the_token_but_no_decision_is_refused` is that discard, and it
    is what now stands between an agent and an envelope it cannot act on.

    The round trip the journey this replaces ended on is gone with the refusal: these
    envelopes now reach the channel, and the engine answers the pending surface with the
    first of them that it reads as a verdict. That a refusal leaves the question
    answerable is a property only a refusal can have, and the journeys in
    `tests/ask_seam/test_channel_reply_e2e.py` are where the surviving one is held to it.
    """
    asking = _ask(asked, "Should the test key cover docs?", window=ANSWERED_WINDOW_SECONDS)
    # Handing the blocking surface out is what opens the reply rendezvous at all, so a
    # reply sent before it is refused for a reason that has nothing to do with this.
    question = _waited_for_question(asked, asking)
    token = TOKEN.search(question)
    assert token is not None, f"the question carried no correlation token:\n{question}"

    for what, envelope in UNUSABLE_REPLIES:
        sent = reply(asked.run, asked.environment, envelope)

        # The recipe's own refusals are the only thing this journey owns. Two of these
        # shapes are malformed as far as `onepipeline reply` is concerned and it refuses
        # them itself, which is its surface to own rather than a guard here.
        assert "channel-reply:" not in sent.stderr, (
            f"an envelope with {what} was refused by this recipe. It carries no "
            f"correlation token, and neither does a monitor score — so a guard refusing "
            f"this refuses every score raised while an agent waits:\n{sent.stderr}"
        )
        assert sent.returncode == 0 or "onepipeline: refused" in sent.stderr, (
            f"an envelope with {what} ended non-zero without the published verb saying "
            f"why, so something between the two refused it silently:"
            f"\n{sent.stdout}{sent.stderr}"
        )

    _reaped(asking)


#: What `just channel-reply` exits with when it refuses an envelope. Asserted exactly
#: rather than as "not zero", because a refusal and a crash are both non-zero and only
#: one of them leaves the question answerable — a guard that died on its own would
#: satisfy a loose check while telling the manager nothing about the token.
REPLY_REFUSED = 2


class UnechoingRuling(NamedTuple):
    """One ruling that cannot answer the pending question, and what a manager did to it.

    Named rather than positional because the two carry different weight in a failure: the
    description is what a refused assertion reports, and the envelope is what was sent.
    """

    what: str
    envelope: str


def _unechoing_rulings(token: str) -> tuple[UnechoingRuling, ...]:
    """Every ruling that cannot be *this* question's answer, by what a manager would do.

    All of them pass the shape check — each carries a boolean `completion`, so nothing
    about them says they answer nothing — and each reaches a waiting wrapper that
    discards it as another reader's without a word on the channel.

    Taken as a function of the token because the last one needs it: a ruling that carries
    the token *somewhere else* is the case where the two ends could most easily disagree,
    since `answer_echoes` reads the one field the wrapper takes an answer's text from and
    a guard matching the whole envelope would accept it. That reply is delivered and
    discarded exactly as one naming no token at all.
    """
    return (
        UnechoingRuling("a decision naming no token", ruling("yes, key it on the whole workspace")),
        UnechoingRuling(
            "a decision with no message at all",
            json.dumps({"version": 1, "completion": True}),
        ),
        UnechoingRuling(
            "a message that is not text",
            json.dumps({"version": 1, "completion": True, "message": 7}),
        ),
        UnechoingRuling(
            "a decision carrying the token outside the message the wrapper reads",
            json.dumps({"version": 1, "completion": True, "message": ANSWER, "reason": token}),
        ),
    )


#: The two published ways of handing this recipe an envelope. Both are judged, and both
#: are driven, because they are not one path: an envelope on stdin is staged as it is
#: read, and one in a file is staged from that file — so a guard wired to only one of
#: them would pass the other straight through.
REPLY_ROUTES = ("on stdin", "from a file")


def test_a_ruling_that_echoes_no_token_reaches_the_channel_unrefused(
    asked: Asked, tmp_path: Path
) -> None:
    """A well-formed decision naming no question is the shape a monitor score also has.

    It carries a boolean `completion` and no correlation token, which is exactly what
    `scripts/channel-serve.py` asks a manager for when it scores a monitor turn. Nothing
    on the queue tells the two apart, so refusing it refused every score raised while an
    agent's question sat pending — and this holds the withdrawal of that refusal, by both
    published ways of handing an envelope over.

    One send per route rather than a table of shapes, because each of these *is* a valid
    verdict once it is accepted: the channel claims it and the rendezvous closes, so the
    second route waits for the wrapper's own re-ask to open another.

    What the agent still owes is unchanged: the wrapper discards a ruling it cannot match
    to its own question, which is what
    `test_a_ruling_addressed_to_another_reader_is_re_asked_rather_than_returned` holds.
    The refusal this recipe keeps is for a ruling that names a question the run has NOT
    handed out — a fact about the envelope rather than about the queue — and
    `tests/ask_seam/test_channel_reply_e2e.py` is where that one is driven.
    """
    asking = _ask(asked, "Should the test key cover docs?", window=ANSWERED_WINDOW_SECONDS)
    written = tmp_path / "unechoing.json"
    written.write_text(ruling(ANSWER), encoding="utf-8")

    for route in REPLY_ROUTES:
        _waited_for_question(asked, asking, named=f"the question answered {route}")
        sent = (
            reply(asked.run, asked.environment, ruling(ANSWER))
            if route == "on stdin"
            else _just("channel-reply", asked.run, str(written), environment=asked.environment)
        )

        assert "channel-reply:" not in sent.stderr, (
            f"a decision naming no token, sent {route}, was refused by this recipe — and "
            f"a monitor score is the same envelope from the queue's point of view, so "
            f"this refuses every score raised while an agent waits:\n{sent.stderr}"
        )
        assert sent.returncode == 0, (
            f"a decision naming no token, sent {route}, did not reach the channel:"
            f"\n{sent.stdout}{sent.stderr}"
        )

    _reaped(asking)


def test_a_live_edit_still_reaches_the_graph_while_a_question_is_pending(
    asked: Asked,
) -> None:
    """The guard refuses an answer that cannot answer, not everything without a verdict.

    A `commands` envelope carries no `completion` and is not trying to answer anything:
    the adopted release routes it to the command path, where it lands on the graph and
    answers zero surfaces. Refusing it would take a manager's steering away for exactly
    as long as a question went unanswered — so it goes through with a question pending,
    and the verb's own answer is what says it landed.
    """
    # The answered window rather than the short one, for the reason that constant states:
    # a manager has to find the question before the edit can be sent while it is pending,
    # and a wrapper that gave up first would report the channel's own timeout refusal
    # instead of this journey's subject. None of it is spent — the ask is reaped below as
    # soon as the edit has landed.
    asking = _ask(
        asked, "Which cursor shape should the route take?", window=ANSWERED_WINDOW_SECONDS
    )
    _waited_for_question(asked, asking)

    edited = reply(asked.run, asked.environment, LIVE_EDIT)

    assert edited.returncode == 0, (
        f"a live graph edit was refused while a question was pending, which is when a "
        f"manager most needs to steer:\n{edited.stdout}{edited.stderr}"
    )
    assert json.loads(edited.stdout)["state"] == "applied", (
        f"the edit reached the verb but did not land on the graph:\n{edited.stdout}"
    )

    _reaped(asking)


#: A token-shaped string an agent plausibly writes into its own question: the one it was
#: asked under last time, quoted while recalling what that decision was. Nobody is
#: waiting on it, and it sits in the surface below the wrapper's own protocol — so what
#: the guard does with it is the whole subject of the journey below.
QUOTED_TOKEN = f"ask-manager-token:{'b' * 24}"


def test_the_token_a_reply_must_echo_is_the_one_at_the_head_of_the_surface(
    asked: Asked,
) -> None:
    """A question body quoting an older token does not get to say which question it is.

    The rule reads a surface's token off the first line carrying the prefix, and the
    wrapper states its protocol at the head so that line is the protocol's. Reading any
    other one would let the agent's own prose decide which question a reply names: the
    question here quotes the token it was asked under last time, which nobody is waiting
    on.

    Driven while the question is still queued, because that is where the surviving
    refusal lives. A ruling echoing the *minted* token is refused — which it could only
    be if the head line is the one that was read, since a guard reading the quoted line
    would find nothing in this envelope to match. Handing the question out then makes the
    same ruling its answer.
    """
    asking = _ask(
        asked,
        "Should the cursor be opaque? Nobody is waiting on the last one, which was\n"
        f"{QUOTED_TOKEN}",
        window=ANSWERED_WINDOW_SECONDS,
    )
    queued_message = _waited_for_a_queued_question(asked, QUOTED_TOKEN)
    assert _pending(asked) is None, (
        f"the question was handed out, so it is not the queued case this is about: "
        f"{_pending(asked)}"
    )

    # The wrapper's own token leads the surface and the quoted one sits below the
    # separator, so the first match is the head line's — which is the line the rule reads.
    found = TOKEN.search(queued_message)
    assert found is not None, f"the queued question carried no minted token:\n{queued_message}"
    minted = found.group(0)
    assert minted != QUOTED_TOKEN, (
        f"the wrapper minted the token its own question quotes, so nothing here says "
        f"which line the rule read: {minted}"
    )

    refused = reply(asked.run, asked.environment, ruling(f"{ANSWER} {minted}"))

    assert refused.returncode == REPLY_REFUSED, (
        f"a ruling naming the question at the head of this surface was accepted while "
        f"that question was still queued, so it was reported delivered and the agent "
        f"went on waiting:\n{refused.stdout}{refused.stderr}"
    )
    assert minted in refused.stderr and QUOTED_TOKEN not in refused.stderr, (
        f"the refusal names a token other than the one at the head of the surface, so "
        f"the rule read the agent's own prose instead:\n{refused.stderr}"
    )

    _waited_for_question(asked, asking)
    answered = reply(asked.run, asked.environment, ruling(f"{ANSWER} {minted}"))
    assert answered.returncode == 0, (
        f"the same ruling was refused once its question was pending, so this is a wall "
        f"rather than a guard:\n{answered.stdout}{answered.stderr}"
    )

    status, out, err = _finish(asking)
    assert status == 0, f"the wrapper never received the ruling that was accepted:\n{err}"
    assert TOKEN.sub("", out).strip() == ANSWER, out


#: A correlation token in a shape this checkout could not have minted: its own mint is
#: 24 lowercase hex digits, and the journey below `UPPERCASE_OD` is what holds it to
#: refusing anything else *before* it asks. So this shape
#: reaches a channel only from an asker that is not this wrapper — an older or newer one
#: minting differently, or another tool sharing the prefix.
#:
#: The guard must protect it anyway, and that is the whole of why the rule takes whatever
#: follows the prefix through the end of the line rather than a shape it recognises. The
#: prefix is what says a reader is waiting; the value after it is the asker's to choose.
#: A guard holding it to today's shape would read this surface as carrying no token at
#: all — and pass every reply to it through unjudged, reported delivered, discarded by
#: the reader waiting — which is the exact silence the guard exists to close, restored
#: for the one asker it cannot recognise.
UNMINTABLE_TOKEN = "ask-manager-token:v2-v4Q7v/rota-1"


def _asked_by_another_minter(asked: Asked, question: str) -> subprocess.Popen[str]:
    """Put one blocking question on the channel as an asker this checkout is not.

    Through the engine's own server directly, for the reason `reply_unguarded` exists at
    the other end of the same channel: the surface under test is one this checkout's
    wrapper refuses to raise, so being the asker that would is the only honest way to
    have one. What is sent is the published frame the wrapper itself builds — one line,
    `blocking`, `kind` of `planner-question` — differing only in the token it carries.

    A session of its own, so `_reaped` can end the server in one signal exactly as it
    ends a wrapper and the `channel serve` beneath it.
    """
    frame = json.dumps(
        {"kind": "planner-question", "message": question, "blocking": True},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    environment = dict(asked.environment)
    environment["ONEPIPELINE_REPLY_TIMEOUT_SECONDS"] = str(ANSWERED_WINDOW_SECONDS)
    serving = subprocess.Popen(
        [str(REPO_ROOT / "scripts" / "onepipeline.sh"), "channel", "serve", asked.run],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    assert serving.stdin is not None
    serving.stdin.write(frame + "\n")
    # Closed here for the reason `_ask` closes its own: the server reads exactly one
    # line and the write side has to finish before there is a question to wait for.
    serving.stdin.close()
    serving.stdin = None
    return serving


def test_a_question_whose_token_this_checkout_could_not_mint_is_still_guarded(
    asked: Asked,
) -> None:
    """The prefix says a reader is waiting; the shape after it is the asker's to choose.

    The rule reads a surface's token as whatever follows the prefix, and this is the
    surface that makes the difference: a question raised by an asker minting a shape this
    checkout does not. Held to today's shape, the guard would find no token here and let a
    manager address a question nobody has handed out — reported delivered, read by the
    reader that is waiting, and discarded.

    Driven while the question is still queued, which is where the surviving refusal lives:
    a ruling naming it is refused, the question is still queued afterwards, and once it is
    handed out the same ruling is accepted and reaches the reader waiting for it.
    """
    serving = _asked_by_another_minter(
        asked, f"Which cache key should this tier use?\n{UNMINTABLE_TOKEN}"
    )
    reached = _waited_for_a_queued_question(asked, UNMINTABLE_TOKEN)
    assert TOKEN.search(reached) is None, (
        f"this token matches the shape this checkout mints after all, so nothing here "
        f"says the rule read one it does not recognise:\n{reached}"
    )

    refused = reply(asked.run, asked.environment, ruling(f"{ANSWER} {UNMINTABLE_TOKEN}"))

    assert refused.returncode == REPLY_REFUSED, (
        f"a ruling naming a queued question was accepted because the token it names is "
        f"a shape this checkout does not mint, so it was reported delivered and the "
        f"reader waiting on that question went on waiting:\n{refused.stdout}{refused.stderr}"
    )
    assert UNMINTABLE_TOKEN in refused.stderr, (
        f"the refusal does not name the question it is about, so a manager is told their "
        f"reply was wrong without being told which one to read:\n{refused.stderr}"
    )
    assert UNMINTABLE_TOKEN in _waited_for_a_queued_question(asked, UNMINTABLE_TOKEN), (
        "refusing that reply consumed the question, so nothing is left to hand out"
    )

    _waited_for_question(asked, serving)
    answered = reply(asked.run, asked.environment, ruling(f"{ANSWER} {UNMINTABLE_TOKEN}"))
    assert answered.returncode == 0, (
        f"the same ruling was refused once its question was pending, so this is a wall "
        f"rather than a guard:\n{answered.stdout}{answered.stderr}"
    )

    status, out, err = _finish(serving)
    assert status == 0, f"the reader never received the ruling that was accepted:\n{err}"
    assert ANSWER in out and UNMINTABLE_TOKEN in out, (
        f"the accepted ruling did not reach the reader waiting on the question:\n{out}"
    )


def _compare_both_routes(asked: Asked, state: str) -> None:
    """The recipe and the verb beneath it answer one envelope the same way.

    Proof by comparison rather than by asserting one message: a refusal reworded
    upstream moves both together, where an assertion about wording would go stale
    claiming a guard was silent when it had merely stopped mattering.
    """
    through_the_recipe = reply(asked.run, asked.environment, UNUSABLE_REPLY)
    through_the_engine = reply_unguarded(asked.run, asked.environment, UNUSABLE_REPLY)

    assert through_the_recipe.returncode == through_the_engine.returncode, (
        f"with {state}, the recipe answered {through_the_recipe.returncode} where the "
        f"verb beneath it answered {through_the_engine.returncode}:"
        f"\n{through_the_recipe.stderr}"
    )
    assert through_the_recipe.stdout == through_the_engine.stdout, (
        f"with {state}, the recipe reported something the verb did not:"
        f"\n{through_the_recipe.stdout}"
    )
    assert "channel-reply:" not in through_the_recipe.stderr, (
        f"with {state}, the recipe refused a reply it has nothing to refuse against:"
        f"\n{through_the_recipe.stderr}"
    )


def test_a_reply_with_no_blocking_question_pending_behaves_as_it_did_before(
    asked: Asked,
) -> None:
    """With no question waiting, the recipe is the passthrough it always was.

    Twice, because there are two ways for a run to have no question waiting and only one
    of them is the empty case. A run this host watches always has *something* pending —
    the monitor and the pacemaker raise a surface on every turn — and a guard that fired
    on those would refuse a manager's replies for the whole life of every watched run,
    which is a far larger regression than the defect it was added for.
    """
    assert _pending(asked) is None, "this run already has a surface pending"
    _compare_both_routes(asked, "nothing pending")

    # And with a surface pending that is not a question anybody is blocked on. This
    # host's monitor and its pacemaker raise one on every run, so it is the ordinary
    # state rather than a corner: a guard that fired on it would refuse a manager's
    # replies for the whole life of every watched run.
    raised = _just(
        "channel-surface", asked.run, "the frontier is idle", environment=asked.environment
    )
    assert raised.returncode == 0, f"the run refused a status update:\n{raised.stderr}"
    handed = next_surface_record(asked.run, asked.environment)
    assert handed is not None and handed["blocking"] is False, (
        f"this journey needs a non-blocking surface pending and got {handed}"
    )
    _compare_both_routes(asked, "a non-blocking surface pending")


def _drained(asked: Asked) -> list[Surface]:
    """Every surface still queued, read the way a manager reads one, until none is left."""
    rest: list[Surface] = []
    while (surface := next_surface_record(asked.run, asked.environment)) is not None:
        rest.append(surface)
    return rest


def _stranded_answer(asked: Asked, question: str) -> str:
    """Leave one manager's answer on the channel with nobody to claim it, and name its token.

    The shape run `issue-28` produced: an ask is killed while it waits — as an agent's
    two-minute tool deadline killed one there, against a fifty-minute reply window — and
    the manager answers afterwards, into a rendezvous nobody is at. The whole process
    group goes, because the wrapper's `channel serve` child is the thing waiting and an
    orphan of it would claim that answer and throw it away, which is a different state.
    """
    # The long window deliberately: the seed has to be killed while waiting rather than
    # time out on its own, since a wrapper that gave up leaves the channel differently.
    seed = _ask(asked, question, window=ANSWERED_WINDOW_SECONDS)
    found = TOKEN.search(_waited_for_question(asked, seed, named=f"the seed question {question!r}"))
    assert found is not None, f"the seed question {question!r} reached the manager with no token"
    assert seed.poll() is None, (
        f"the seed ask {question!r} was over before this journey could end it, so what "
        f"follows is not the state the defect needs"
    )
    # `SIGTERM` rather than the default kill, because this one is imitating a specific
    # death: the tool deadline that ended the ask on run `issue-28`. What it must not
    # leave behind is an orphaned `channel serve`, which would claim the stale answer
    # below and throw it away — a different state from the one being seeded.
    _reaped(seed, sending=signal.SIGTERM)
    stale = reply(asked.run, asked.environment, ruling(f"answered too late {found.group(0)}"))
    assert stale.returncode == 0, (
        f"the channel refused the answer to a question whose reader had gone, so no stale "
        f"ruling was left on it and nothing after this is about the defect:\n"
        f"{stale.stderr}{stale.stdout}"
    )
    return found.group(0)


def test_one_ask_puts_one_blocking_question_to_a_manager_however_often_it_re_arms(
    asked: Asked,
) -> None:
    """A foreign-token ruling re-arms a listener, so one ask blocks a manager once.

    The seed is ordinary and is what run `issue-28` did: an ask outlives its agent's tool
    deadline and is killed while waiting, so its manager's answer — echoing that ask's
    token — is left with no reader. The next ask draws it milliseconds after asking.

    A wrapper that answered that by asking again queued a second *blocking* question, and
    that duplicate is what made the defect self-sustaining: the manager answers both
    copies, one listener is left to claim an answer, and the orphan poisons the ask after
    it. Asserted here is that the ask still returns the manager's answer, that it really
    did re-arm rather than skipping this path, and that exactly one surface blocks.

    The manager answers persistently because the re-arm window has no reader in it, and
    the seed's own completion has already settled the run — so a reply sent there is
    refused rather than queued. Every send is the same answer carrying the same token,
    and what this counts is surfaces, which a re-send raises none of.
    """
    seeded = _stranded_answer(asked, "Which base does this branch merge to?")

    asking = _ask(asked, "Should the listing be paginated?", window=ANSWERED_WINDOW_SECONDS)
    manager = PersistentManager(
        asked.run, asked.environment, lambda token: ruling(f"{ANSWER} {token}")
    )

    status, out, err = _finish(asking, manager=manager)
    manager.stop()
    manager.checked(asker_said=err)

    assert status == 0, f"the wrapper did not survive a stale ruling on the channel:\n{err}"
    assert TOKEN.sub("", out).strip() == ANSWER, out

    # Every surface, each seen through `just channel-next`: the ones the manager read on
    # their way to answering, plus whatever was still queued once the ask was over.
    raised = [
        (found.group(0), surface)
        for surface in [*manager.surfaces, *_drained(asked)]
        if (found := TOKEN.search(surface["message"])) is not None and found.group(0) != seeded
    ]
    assert len(raised) > 1, (
        f"the ask drew the stale ruling and never re-armed, so it never reached the path "
        f"this journey is about and its single surface proves nothing about it: {raised}"
    )
    assert len({token for token, _ in raised}) == 1, (
        f"these surfaces did not all come from one ask, so counting them says nothing "
        f"about what one ask does: {raised}"
    )
    assert len([surface for _, surface in raised if surface["blocking"]]) == 1, (
        f"one ask put more than one blocking question in front of the manager, which is "
        f"the duplication itself: every copy has to be answered, and the answer nobody is "
        f"left to claim is what poisons the ask after it: {raised}"
    )

    # Every surface of one ask, not only its question: a re-arm note is answerable too,
    # and a manager who answers it still has to echo the token. Below the body it is the
    # first thing a reader that truncates loses.
    buried = [
        surface["message"]
        for token, surface in raised
        if PROTOCOL_SEPARATOR not in surface["message"]
        or surface["message"].index(token) > surface["message"].index(PROTOCOL_SEPARATOR)
    ]
    assert not buried, (
        f"these surfaces state their token below the body rather than above it, where a "
        f"reader that truncates loses the one thing that binds an answer back: {buried}"
    )


def test_a_frame_the_channel_refuses_is_fatal_rather_than_retried(asked: Asked) -> None:
    """A refused submission is a cause to report, not a condition to wait out.

    `channel serve` refuses a frame naming a node the run does not have, by name and
    at once. Retrying it would spend the whole reply window per attempt and then report
    a timeout, burying the one sentence that says what is wrong — so the wrapper stops
    on the refusal and relays it.
    """
    asking = _ask(
        asked,
        "Which way?",
        window=ANSWERED_WINDOW_SECONDS,
        overrides={"ORCHESTRATOR_ASK_MANAGER_NODE": "no-such-node"},
    )

    started = time.monotonic()
    status, out, err = _finish(asking)

    assert status != 0, out
    assert out == "", f"a refused question still printed a ruling:\n{out}"
    assert "no-such-node" in err and "does not have" in err, err
    assert time.monotonic() - started < PUBLISHED_WINDOW_SECONDS, (
        f"the wrapper waited out a reply window before reporting a refusal it was told "
        f"about immediately:\n{err}"
    )


def test_the_wrapper_sets_a_reply_window_longer_than_the_published_default(asked: Asked) -> None:
    """The window is the wrapper's own, and not a thing a caller has to remember.

    `channel serve`'s own default is ~30 seconds (29.8s measured), which would end a
    manager's question before they had finished reading it. `ONEPIPELINE_REPLY_TIMEOUT_
    SECONDS` governs the wait exactly and appears in no `--help` output, so it is a
    surface that can drift silently — a wrapper that stopped setting it would still
    work, just for half a minute. Watching one call past that default is what says it
    is still set.
    """
    asking = _ask(asked, "Take your time.", window=OBSERVED_WAITING_AFTER_SECONDS * 3)
    try:
        started = time.monotonic()
        while time.monotonic() - started < OBSERVED_WAITING_AFTER_SECONDS:
            assert asking.poll() is None, (
                f"the wrapper stopped waiting {time.monotonic() - started:.0f}s in, so its "
                f"window is the published {PUBLISHED_WINDOW_SECONDS}s default rather than "
                f"its own:\n{asking.communicate()[1]}"
            )
            time.sleep(0.5)
    finally:
        _reaped(asking)


def test_the_question_reaches_the_manager_as_the_surface_they_read(asked: Asked) -> None:
    """What the manager reads is how to answer, and then the agent's question.

    The surface is the whole interface between the two: an agent's words, the run they
    are blocked on, and the token that binds the answer back. A surface missing any of
    those makes the manager guess, which is the failure the ask channel exists to end.

    The order is asserted as well as the contents, because it is what survives a reader
    that truncates. Below the body the token was the first thing such a reader lost, and
    losing it loses the one thing deciding whether the answer can be matched at all —
    leaving a manager to write a reply nothing can claim and the agent to keep waiting.
    """
    body = "Should the cursor be an opaque token or a node id?"
    asking = _ask(asked, body, window=ANSWERED_WINDOW_SECONDS)
    try:
        message = _waited_for_question(asked, asking)
        assert body in message, message
        assert asked.run in message, message
        found = TOKEN.search(message)
        assert found is not None, message
        assert "just channel-reply" in message, message
        assert PROTOCOL_SEPARATOR in message, (
            f"the surface no longer separates how to answer from what was asked, so "
            f"nothing here can say which came first:\n{message}"
        )
        assert found.start() < message.index(PROTOCOL_SEPARATOR) < message.index(body), (
            f"the token sits below the question body, where a reader that truncates "
            f"loses it and the manager is left unable to bind their answer:\n{message}"
        )
    finally:
        _reaped(asking)


def test_a_question_of_several_words_reaches_the_manager_whole(asked: Asked) -> None:
    """An unquoted question is joined rather than truncated at its first word.

    A decision fork stated in one shell argument needs quoting, and an agent composing
    a command will sometimes not quote it. Taking only the first word would send the
    manager a question that reads like one — `Should` — so the words are joined, and
    this is the only place that says so.
    """
    asking = _ask(asked, "Should", "the", "cursor", "be", "opaque?", window=ANSWERED_WINDOW_SECONDS)
    try:
        assert "Should the cursor be opaque?" in _waited_for_question(asked, asking)
    finally:
        _reaped(asking)


def test_an_explicitly_named_onepipeline_is_the_one_that_reaches_the_manager(
    asked: Asked,
) -> None:
    """`ONEPIPELINE_BIN` is a seam a journey can drive, not only one that can be broken.

    The wrapper resolves the pinned binary from its own location and takes an override,
    exactly as `scripts/channel-serve.py` does — so a journey can point either of them
    at a stand-in channel. That is only true if the override *works*, and an override
    that merely fails loudly on a bad value would satisfy the refusal journey above
    while making the seam useless. Here it names the real pinned binary and the question
    still reaches the run's channel.
    """
    pinned = REPO_ROOT / ".venv" / "bin" / "onepipeline"
    assert pinned.is_file(), f"this checkout has no pinned onepipeline at {pinned}"
    asking = _ask(
        asked,
        "Which release answered this?",
        window=ANSWERED_WINDOW_SECONDS,
        overrides={"ONEPIPELINE_BIN": str(pinned)},
    )
    try:
        assert "Which release answered this?" in _waited_for_question(asked, asking)
    finally:
        _reaped(asking)


def test_the_question_can_be_piped_in_or_read_from_a_file(asked: Asked, tmp_path: Path) -> None:
    """All three input forms reach the same surface, so a long question needs no quoting.

    A decision fork worth blocking on is usually more than one line, and an agent that
    had to fit it into one shell argument would send a worse question. Both other forms
    are proven here against the real channel rather than by reading the script.
    """
    question = "Which of the two schemas should the route answer in?"
    written = tmp_path / "question.txt"
    written.write_text(f"{question}\n", encoding="utf-8")
    # One at a time: two wrappers asking at once would raise two surfaces, and which
    # one a read handed back would be the thing under test rather than the input form.
    # Named, because both forms ask under the same run id and a bare deadline here says
    # only that the run's channel stayed empty — never which way of asking left it that way.
    for named, form in (
        (
            "a question read from --file",
            lambda: _ask(asked, "--file", str(written), window=ANSWERED_WINDOW_SECONDS),
        ),
        (
            "a question piped in on stdin",
            lambda: _ask(asked, window=ANSWERED_WINDOW_SECONDS, stdin=f"{question}\n"),
        ),
    ):
        asking = form()
        try:
            assert question in _waited_for_question(asked, asking, named=named), question
        finally:
            _reaped(asking)


#: The answer a manager gives a question asked from a worktree, so what comes back can
#: be compared to it whole exactly as the round trip from this checkout is.
WORKTREE_ANSWER = "Amend the node's task; a context note reaches no judge."

#: The question file the worktree journey names by a RELATIVE path, from a directory
#: that is not this checkout. Distinctive rather than `question.txt` so that a wrapper
#: resolving it against the checkout root cannot find one there by accident, and so a
#: reader of a failure knows which file was meant.
WORKTREE_QUESTION_FILE = "ask-from-a-worktree.question.txt"


class Dispatched(NamedTuple):
    """Where a lifecycle dispatch asks from, and what its environment says about the run."""

    #: The directory the wrapper is run in — a session worktree, never this checkout.
    worktree: Path
    #: What the dispatch's environment adds, over the launching session's.
    overrides: dict[str, str]
    #: What it does not carry at all, which is the half an override cannot express.
    unset: tuple[str, ...]


def _as_a_dispatch(asked: Asked, tmp_path: Path) -> Dispatched:
    """The environment and working directory a lifecycle dispatch really asks from.

    Measured on this host, inside a dispatch of a lifecycle node, rather than assumed:
    `ONEPIPELINE_RUNS_DIR` is **not** set, `ONEPIPELINE_NODE_SCRATCH_DIR` is set to an
    absolute `<runs-root>/<run>/scratch/<pid>-<n>`, and the working directory is the
    session worktree — which is somewhere else entirely and has no `runs` directory of
    its own. That combination is the whole defect: `onepipeline` resolves a run under a
    relative `runs` when nothing names one, so the question was refused `no such run
    '<run>' under runs` and no surface was ever raised for a manager to notice.

    The scratch directory is created here because these runs reach no dispatch — the
    frontier is a human gate — so the engine never composes one. That shape is the
    engine's to change, so it is not asserted here:
    `tests/ask_seam/test_launch_ask_seam_e2e.py` holds it against a real dispatch of every
    launch shape, and this stands up what that measures.
    """
    runs_root = Path(asked.environment["ONEPIPELINE_RUNS_DIR"])
    scratch = runs_root / asked.run / "scratch" / "e2e-0"
    scratch.mkdir(parents=True)
    assert (runs_root / asked.run / "launch.json").is_file(), (
        f"run {asked.run} has no launch record under {runs_root}; the wrapper corroborates "
        "a runs root against that file, so a journey without one proves nothing"
    )
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    assert not (worktree / "runs").exists(), (
        f"{worktree} holds a `runs` directory, so an ask made from it would resolve the "
        "run through the caller's own directory and this journey would pass either way"
    )
    return Dispatched(
        worktree=worktree,
        overrides={"ONEPIPELINE_NODE_SCRATCH_DIR": str(scratch)},
        unset=("ONEPIPELINE_RUNS_DIR",),
    )


def test_a_question_asked_from_a_lifecycle_worktree_reaches_its_own_runs_channel(
    asked: Asked, tmp_path: Path
) -> None:
    """The round trip an agent actually makes: asked from the worktree it was dispatched into.

    Every journey above asks with `ONEPIPELINE_RUNS_DIR` naming the run store, which is
    why the defect survived them: with it set the working directory does not matter, and
    it is exactly what a real dispatch does not carry.
    """
    dispatch = _as_a_dispatch(asked, tmp_path)
    asking = _ask(
        asked,
        "Should the amendment name the property or the mechanism?",
        window=ANSWERED_WINDOW_SECONDS,
        overrides=dispatch.overrides,
        dropping=dispatch.unset,
        cwd=dispatch.worktree,
    )
    manager = Manager(
        asked.run, asked.environment, [lambda token: ruling(f"{WORKTREE_ANSWER} {token}")]
    )

    status, out, err = _finish(asking, manager=manager)
    manager.checked(asker_said=err)

    assert status == 0, f"a question asked from a worktree did not reach its run:\n{err}"
    assert TOKEN.sub("", out).strip() == WORKTREE_ANSWER, out


#: Every shape of `ONEPIPELINE_NODE_SCRATCH_DIR` that says nothing about where this run
#: is: none at all, one that is not absolute, and an absolute one under no run of this
#: store. Each is a state the wrapper must leave the ask in rather than resolve it out
#: of, because the alternative is a plausible path nobody is reading.
#:
#: The relative one is not a variation on the other two. The wrapper walks a path by
#: stripping its last component, and that strip is a fixpoint once no `/` is left — so a
#: relative scratch directory walks to its first component and loops there. Measured
#: against a copy of the wrapper without its absolute-path guard, the ask never returns;
#: here it is refused in seconds like the rest.
UNINFORMATIVE_SCRATCH: dict[str, Callable[[Path], str | None]] = {
    "no scratch directory at all": lambda _: None,
    "a relative scratch directory": lambda _: "scratch/e2e-0",
    "a scratch directory under no run": lambda under: str(under / "elsewhere" / "scratch"),
}


@pytest.mark.parametrize("shape", sorted(UNINFORMATIVE_SCRATCH))
def test_a_dispatch_whose_environment_names_no_run_store_is_refused_rather_than_guessed_at(
    asked: Asked, tmp_path: Path, shape: str
) -> None:
    """With no evidence to resolve, the ask stays where it was and says so.

    A wrapper that fell back to a plausible-looking directory would put the question
    somewhere no manager reads and report success — the failure this seam exists to
    remove, arriving through the repair for it.
    """
    dispatch = _as_a_dispatch(asked, tmp_path)
    named = UNINFORMATIVE_SCRATCH[shape](tmp_path)
    overrides = dict(dispatch.overrides)
    if named is None:
        overrides.pop("ONEPIPELINE_NODE_SCRATCH_DIR")
    else:
        overrides["ONEPIPELINE_NODE_SCRATCH_DIR"] = named

    asking = _ask(
        asked,
        "Which store is this question asked on?",
        overrides=overrides,
        dropping=dispatch.unset,
        cwd=dispatch.worktree,
    )
    status, out, err = _finish(asking)

    assert status == 2, f"{shape} was resolved into a runs root rather than refused:\n{err}{out}"
    assert f"no such run '{asked.run}' under runs" in err, (
        f"the refusal for {shape} does not name the run and the directory it was looked "
        f"for under, which is what an agent has to read to repair it:\n{err}"
    )
    assert out == "", (
        f"a refused ask put something on stdout, which a caller reads as an answer:\n{out}"
    )


def test_a_runs_root_that_does_not_hold_this_run_is_passed_over_for_the_dispatchs_own(
    asked: Asked, tmp_path: Path
) -> None:
    """A stated runs root is checked against this run's records, not taken on being set.

    The two states an inherited `ONEPIPELINE_RUNS_DIR` can be in look identical from the
    variable alone: the launch's own store, which must be passed through untouched, or a
    value that reached this process some other way — a manager's read, inherited — naming
    a store this run is not in. So the one here exists and holds another run's records,
    which is what a bare existence check would wave through.
    """
    dispatch = _as_a_dispatch(asked, tmp_path)
    elsewhere = tmp_path / "another-hosts-runs"
    (elsewhere / "some-other-run").mkdir(parents=True)
    (elsewhere / "some-other-run" / "launch.json").write_text("{}", encoding="utf-8")
    asking = _ask(
        asked,
        "Which store is this question asked on?",
        window=ANSWERED_WINDOW_SECONDS,
        overrides={**dispatch.overrides, "ONEPIPELINE_RUNS_DIR": str(elsewhere)},
        cwd=dispatch.worktree,
    )
    manager = Manager(
        asked.run, asked.environment, [lambda token: ruling(f"{WORKTREE_ANSWER} {token}")]
    )

    status, out, err = _finish(asking, manager=manager)
    manager.checked(asker_said=err)

    assert status == 0, (
        f"a runs root holding another run was taken for this one's, so the question "
        f"never reached run {asked.run}:\n{err}"
    )
    assert TOKEN.sub("", out).strip() == WORKTREE_ANSWER, out


def test_every_way_of_asking_from_a_worktree_still_reads_the_callers_own_files(
    asked: Asked, tmp_path: Path
) -> None:
    """Finding the run moved the runs root and nothing else the wrapper reads.

    A `--file` path is the caller's, relative to where the agent ran the wrapper, so a
    fix that reached the run by changing directory would re-root it silently — one quiet
    failure traded for another. Hence a RELATIVE path, readable only from where the ask
    was made.
    """
    dispatch = _as_a_dispatch(asked, tmp_path)
    question = "Which of the two schemas should the route answer in?"
    (dispatch.worktree / WORKTREE_QUESTION_FILE).write_text(f"{question}\n", encoding="utf-8")
    # One at a time, and named, for the reason
    # `test_the_question_can_be_piped_in_or_read_from_a_file` gives: three asks at once
    # would raise three surfaces under one run id, and which one a read handed back
    # would become the subject instead of the way of asking.
    for named, form in (
        (
            "a question passed as an argument",
            lambda: _ask(
                asked,
                question,
                window=ANSWERED_WINDOW_SECONDS,
                overrides=dispatch.overrides,
                dropping=dispatch.unset,
                cwd=dispatch.worktree,
            ),
        ),
        (
            "a question read from a --file path relative to the asking directory",
            lambda: _ask(
                asked,
                "--file",
                WORKTREE_QUESTION_FILE,
                window=ANSWERED_WINDOW_SECONDS,
                overrides=dispatch.overrides,
                dropping=dispatch.unset,
                cwd=dispatch.worktree,
            ),
        ),
        (
            "a question piped in on stdin",
            lambda: _ask(
                asked,
                window=ANSWERED_WINDOW_SECONDS,
                overrides=dispatch.overrides,
                dropping=dispatch.unset,
                cwd=dispatch.worktree,
                stdin=f"{question}\n",
            ),
        ),
    ):
        asking = form()
        try:
            assert question in _waited_for_question(asked, asking, named=named), question
        finally:
            _reaped(asking)


class Refusal(NamedTuple):
    """One way of asking that cannot reach a manager, and what the refusal must say."""

    #: What the caller got wrong, for the test id.
    what: str
    #: The environment it reaches the wrapper with, over the working one.
    overrides: dict[str, str]
    #: The arguments it is called with.
    arguments: tuple[str, ...]
    #: A fragment the reported cause must carry.
    names: str


#: Every way of asking that is refused before the channel is ever reached. Each one is
#: a state a dispatched agent can genuinely be in — no run exported, a question that
#: came out empty, a toolchain that is not there — and each has to say which, because
#: the agent reading the refusal is the one that has to repair it.
REFUSALS = (
    Refusal("no run", {"ONEPIPELINE_RUN_ID": ""}, ("Which way?",), "blank"),
    Refusal("blank run", {"ONEPIPELINE_RUN_ID": "   "}, ("Which way?",), "blank"),
    Refusal("empty question", {}, ("   ",), "question is empty"),
    Refusal(
        "unusable binary",
        {"ONEPIPELINE_BIN": "/nonexistent/onepipeline"},
        ("Which way?",),
        "not an executable",
    ),
    Refusal("unreadable file", {}, ("--file", "/nonexistent/question.txt"), "is not readable"),
    Refusal("two files", {}, ("--file", "one.txt", "two.txt"), "exactly one path"),
    Refusal("unknown option", {}, ("--why",), "not an option"),
    # Both of these arrive from the environment a dispatch was started with, which is
    # somebody else's to write: one becomes an argv word `channel serve` resolves as a
    # directory, the other a field the engine resolves against the run's graph.
    Refusal("an unusable run", {"ONEPIPELINE_RUN_ID": "../elsewhere"}, ("Which way?",), "as a run"),
    Refusal(
        "an unusable node",
        {"ORCHESTRATOR_ASK_MANAGER_NODE": "../elsewhere"},
        ("Which way?",),
        "as a node",
    ),
    Refusal(
        "a window that is not seconds",
        {"ORCHESTRATOR_ASK_MANAGER_TIMEOUT_SECONDS": "soon"},
        ("Which way?",),
        "not a number of seconds",
    ),
)


@pytest.mark.parametrize("refusal", REFUSALS, ids=lambda row: row.what)
def test_a_question_that_cannot_reach_a_manager_names_its_cause_and_a_remedy(
    asked: Asked, refusal: Refusal
) -> None:
    """Every refusal is non-zero, silent on stdout, and says what to do about it.

    An agent asking a question is already stuck; a refusal it cannot act on leaves it
    stuck with one more thing to work out. So each of these names what is wrong *and*
    the repair, and none of them leaves anything on stdout — a caller reads stdout as
    the answer, so a diagnostic printed there would be read as one.
    """
    status, out, err = _finish(_ask(asked, *refusal.arguments, overrides=refusal.overrides))

    assert status != 0, f"asking with {refusal.what} was not refused:\n{out}"
    assert out == "", (
        f"a refused question printed something a caller would read as an answer:\n{out}"
    )
    assert refusal.names in err, err
    # A cause and a remedy on one line, in `scripts/channel-serve.py`'s shape. Found
    # among the lines rather than at the start of stderr: a malformed invocation is
    # also shown the usage line first, which is for the caller and not the repair.
    reported = [line for line in err.splitlines() if line.startswith("ask-manager: ")]
    assert reported and all("; " in line for line in reported), (
        f"the refusal states no remedy beside its cause:\n{err}"
    )


def test_an_unset_run_is_refused_by_name_rather_than_guessed_at(asked: Asked) -> None:
    """A process with no run exported is refused, because guessing one asks the wrong run.

    Separate from the blank case above because the two are different mistakes with
    different repairs: a blank value is a caller that computed one badly, and an unset
    one is a process that was never dispatched under a run at all. The wrapper is run
    with the variable removed entirely, which is the state that matters — anything a
    wrapper inferred here would send an agent's question to somebody else's channel.
    """
    environment = dict(asked.environment)
    environment.pop("ONEPIPELINE_RUN_ID", None)
    environment["ORCHESTRATOR_ASK_MANAGER_TIMEOUT_SECONDS"] = str(SHORT_WINDOW_SECONDS)
    asking = subprocess.Popen(  # noqa: S603 - the real wrapper, with no run exported
        [str(ASK_MANAGER), "Which way?"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    status, out, err = _finish(asking)

    assert status != 0, out
    assert out == "", out
    assert "ONEPIPELINE_RUN_ID is not set" in err, err


#: The `graphs/node-scope.yaml` member a dispatched plan node runs as, and the
#: `graphs/dag-scope.yaml` member that watches the run. `oneagentgraph` gives every
#: member a scratch directory named after it and pins that member's harness configs
#: inside it, so the recorded `--config` is what says which member a turn belongs to.
WORKER_MEMBER = "worker"
MONITOR_MEMBER = "monitor"
MEMBER_OF_CONFIG = re.compile(r"/members/([^/]+)/")

#: Which environment variables the fake backend is asked to record per turn.
ENVIRONMENT_KEYS_ENV = "FAKE_BACKEND_ENVIRONMENT_KEYS"
PROMPT_LOG_ENV = "FAKE_BACKEND_PROMPT_LOG"

#: The variable this whole wrapper rests on, and the run the journey below launches.
RUN_ID_ENV = "ONEPIPELINE_RUN_ID"
DISPATCHED_RUN = RunId("ask-manager-dispatch-environment")


class TurnRecord(TypedDict):
    """One recorded harness turn, in the terms this journey reads it.

    `tests/e2e/fake_backend.py` writes this file, so the two fields consumed here are
    stated rather than validated: which member the turn served, and what its
    environment carried.
    """

    config: str | None
    environment: dict[str, str | None]


@pytest.fixture(scope="module")
def dispatched_turns(
    tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str
) -> list[TurnRecord]:
    """Launch one node for real and hand back every harness turn the run reached.

    A whole launch, with this host's observer graph attached, because the claim is
    about what a *dispatch* is given and the only way to see that is to make one: a
    variable reaches an agent by inheritance through `onepipeline`, `oneagentgraph`,
    and `oneharness`, and none of them reports what it passed on.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp_path = tmp_path_factory.mktemp("ask-manager-dispatch-environment")
    environment = _environment(tmp_path)
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    turns = tmp_path / "turns.jsonl"
    environment[PROMPT_LOG_ENV] = str(turns)
    environment[ENVIRONMENT_KEYS_ENV] = RUN_ID_ENV
    plan = tmp_path / "dispatch.plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "goal": {"text": "record what a dispatched agent's environment carries"},
                "name": DISPATCHED_RUN,
                "tasks": [
                    {
                        "id": "only",
                        "persona": "engineer",
                        "task": "## What\nReport, changing nothing.\n\n## Why\nThe environment "
                        "the dispatch was given is the subject, not the work.\n\n"
                        "## Acceptance criteria\n- Reported.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    launch = _just(
        "orchestrate",
        project_from_plan(plan),
        environment=environment,
        seconds=300,
    )
    try:
        assert launch.returncode == 0, f"the launch did not settle:\n{launch.stdout}{launch.stderr}"
        assert turns.is_file(), f"no harness turn was recorded at {turns}"
        # `cast` rather than a validating read: `tests/e2e/fake_backend.py` writes these
        # lines and `TurnRecord` states the two fields read off them, so a validator here
        # would restate that stand-in's own shape and fail on a turn it grew a field for.
        return [
            cast(TurnRecord, json.loads(line))
            for line in turns.read_text(encoding="utf-8").splitlines()
        ]
    finally:
        _just("stop", DISPATCHED_RUN, environment=environment, seconds=60)


def _turns_of(turns: list[TurnRecord], member: str) -> list[TurnRecord]:
    """Every recorded turn that served one graph member."""
    found = []
    for turn in turns:
        named = MEMBER_OF_CONFIG.search(turn["config"] or "")
        if named is not None and named.group(1) == member:
            found.append(turn)
    return found


def test_a_dispatched_agent_is_given_the_run_it_belongs_to(
    dispatched_turns: list[TurnRecord],
) -> None:
    """`ONEPIPELINE_RUN_ID` reaches a dispatched worker holding that run's own id.

    This is the environment fact the whole ask channel rests on. `scripts/ask-manager.sh`
    reads the run from here and refuses rather than guessing when it is absent, so an
    agent can only ask at all because a dispatch is given it — and it can only ask the
    *right* run because the value is that run's own id and not an enclosing one's.

    Measured rather than assumed, and measured here rather than in the wrapper's own
    journeys, which state the run themselves: nothing in a launch reports which
    variables it passed down, so a release that stopped exporting this would leave every
    dispatched question refused with no clue why.
    """
    dispatched = _turns_of(dispatched_turns, WORKER_MEMBER)
    assert dispatched, "the run dispatched no worker, so nothing here is about a dispatch"
    carried = {turn["environment"].get(RUN_ID_ENV) for turn in dispatched}
    assert carried == {DISPATCHED_RUN}, (
        f"a dispatched worker was given {sorted(str(value) for value in carried)} as "
        f"{RUN_ID_ENV}; a question asked from one of those would reach the wrong run"
    )


def test_an_observer_member_cannot_tell_its_run_from_an_enclosing_one_by_that_variable(
    dispatched_turns: list[TurnRecord],
) -> None:
    """The observer graph carries the same variable, so it is no proof of a dispatch.

    Worth pinning because the obvious reading of the fact above is the wrong one. The
    driver exports `ONEPIPELINE_RUN_ID` and the observer graph inherits it, measured
    here on a real launch and again on the judge side of an observer member by
    `tests/e2e/test_orchestrate_launch_e2e.py`.

    What that costs is precision, not correctness: reading the variable tells a process
    which run it is *under*, never whether it is the dispatch of a node. So a wrapper
    may trust the value it finds and must not infer anything from merely finding one —
    and if a release goes back to withholding it, this fails and says to re-read.
    """
    watching = _turns_of(dispatched_turns, MONITOR_MEMBER)
    assert watching, "the run started no monitor, so there is no observer turn to read"
    carried = {turn["environment"].get(RUN_ID_ENV) for turn in watching}
    assert carried == {DISPATCHED_RUN}, (
        f"the observer member was given {sorted(str(value) for value in carried)} as "
        f"{RUN_ID_ENV}; the adopted release exports the run's own id to it, and a change "
        f"here changes what reading that variable proves"
    )


def test_a_checkout_with_no_pinned_onepipeline_is_refused_rather_than_falling_back(
    asked: Asked, tmp_path: Path
) -> None:
    """A missing toolchain is said out loud, never replaced by whatever is on PATH.

    The wrapper resolves `onepipeline` from its own location, because a dispatch
    inherits the launching session's PATH and a bare lookup would let the ambient
    environment decide which release the manager is reached through. So a copy of the
    wrapper with no pinned binary beside it must refuse and name the repair, rather
    than quietly asking through a different release. Driven by running the real script
    from a directory that has no `.venv`, which is exactly the state a half-restored
    checkout is in.
    """
    copied = _wrapper_in(tmp_path / "checkout" / "scripts")
    environment = dict(asked.environment)
    environment["ONEPIPELINE_RUN_ID"] = asked.run
    environment.pop("ONEPIPELINE_BIN", None)

    refused = subprocess.run(  # noqa: S603 - the real wrapper, from a checkout without one
        [str(copied), "Which way?"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )

    assert refused.returncode != 0, refused.stdout
    assert refused.stdout == "", refused.stdout
    assert "has no onepipeline at" in refused.stderr and "just bootstrap" in refused.stderr, (
        refused.stderr
    )


def test_a_checkout_without_the_shared_contract_is_refused_rather_than_judging_alone(
    asked: Asked, tmp_path: Path
) -> None:
    """The wrapper will not classify an answer with a rule it could not read.

    What makes an answer a ruling is stated once, beside this wrapper, and read by the
    recipe that sends one too. A copy of the wrapper standing without it has no rule at
    all — and the shell it is written in fails open, so an unset condition would classify
    every envelope the channel handed back as this question's answer. That is the one
    failure worse than not asking, so it is named and the ask stops.
    """
    scripts = tmp_path / "checkout" / "scripts"
    copied = _wrapper_in(scripts)
    (scripts / "ask-manager-contract.sh").unlink()
    environment = dict(asked.environment)
    environment["ONEPIPELINE_RUN_ID"] = asked.run

    refused = subprocess.run(  # noqa: S603 - the real wrapper, from a checkout missing a piece
        [str(copied), "Which way?"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )

    assert refused.returncode != 0, refused.stdout
    assert refused.stdout == "", (
        f"a refused ask still printed something to act on:\n{refused.stdout}"
    )
    assert "ask-manager-contract.sh" in refused.stderr, refused.stderr
    assert "just bootstrap" in refused.stderr, refused.stderr


def test_a_run_whose_channel_keeps_answering_other_readers_is_given_up_on(asked: Asked) -> None:
    """Re-asking is bounded, so a misrouted channel ends in a refusal rather than forever.

    A ruling meant for another reader is re-asked, which is what makes the correlation
    token usable rather than merely safe. But a channel that keeps handing this call
    somebody else's answers is a state the agent cannot ask its way out of — a monitor
    replying in a loop, or a manager who never echoes the token — so the wrapper stops
    and says which of the two it saw, rather than blocking on a question that will
    never be answered.

    Every ruling here is well-formed and none carries the token, which is exactly the
    shape that would otherwise loop.

    The manager answers persistently, for the reason `planner_channel.answer_persistently`
    records: one send is one throw of a race, and losing it wedges the wrapper instead of
    producing the refusal this is about. Re-sending is sound where every send is the same
    envelope, which holds here — each ruling is the same answer to somebody else's
    question — and the bound is read off the wrapper's own sentence rather than counted
    from the sends, because that sentence is what `MAX_ATTEMPTS` restates.

    Through the engine rather than through `just channel-reply`, because that recipe now
    refuses a tokenless ruling before it is sent. Sending it there would send nothing at
    all, which reads from the asking side as a manager who never answered rather than as
    the misrouted channel this is about — and the state it is about is one the recipe
    cannot prevent, since a monitor's reply and an older tool both reach the channel
    without passing through it.
    """
    asking = _ask(asked, "Which cursor shape?", window=ANSWERED_WINDOW_SECONDS)
    manager = PersistentManager(
        asked.run,
        asked.environment,
        lambda _token: ruling("this answers a different question"),
        send=reply_unguarded,
    )

    status, out, err = _finish(asking, manager=manager)
    manager.stop()
    manager.checked(asker_said=err)

    assert status != 0, f"the wrapper never stopped re-asking:\n{out}"
    assert out == "", f"a refused question still printed a ruling:\n{out}"
    assert f"the last {MAX_ATTEMPTS} rulings" in err, (
        f"the wrapper gave up at a bound this journey does not know about, so what it "
        f"drives is not what {MAX_ATTEMPTS} says it is:\n{err}"
    )
    assert "answers to other readers" in err and "include the token" in err, err


# llmlint: ignore-block[e2e_not_mocked] The layer under test is the wrapper, and it is the
# real one; what is substituted is the *input* — a channel that answers nothing, which the
# real `onepipeline channel serve` produces only when the run settles while a question is
# waiting, and a journey cannot end the run out from under its own question. Supplying it
# is exactly what the published `ONEPIPELINE_BIN` seam is for, and every site that reads
# this constant already carries the same directive; this one covers the constant itself,
# which is where the rule attributed the finding.
#: A `onepipeline` that answers the frame with nothing at all, at exit 0. The shape a
#: channel takes when the run settled while the question was waiting, and the one an
#: exit status alone cannot tell from an answer.
#:
#: It reads the frame before exiting, as the real verb does. One that exits without
#: reading leaves the wrapper's `printf` writing down a pipe with no reader, and under
#: `set -o pipefail` that is `exit 141` — a refusal rather than the silence this is about.
SILENT_CHANNEL = "#!/usr/bin/env bash\ncat >/dev/null\nexit 0\n"
# llmlint: ignore-end[e2e_not_mocked]

#: A `python3` that serves the frame and then refuses to judge the answer. Two
#: programs reach it and they are told apart by the one that mentions the timeout
#: reason, which only the classifier is given.
BROKEN_JUDGE = """#!/usr/bin/env bash
for argument in "$@"; do
  case "$argument" in
    *timeout_reason*) exit 3 ;;
  esac
done
exec {python} "$@"
"""


#: The real interpreter each broken stand-in above hands the one call it must still
#: serve. Named once so every stand-in below fits the line its own ignore sits above.
REAL_PYTHON = REPO_ROOT / ".venv" / "bin" / "python3"


def _stand_in(directory: Path, name: str, script: str) -> Path:
    """Write one executable stand-in for a tool the wrapper resolves."""
    directory.mkdir(parents=True, exist_ok=True)
    written = directory / name
    written.write_text(script, encoding="utf-8")
    written.chmod(0o755)
    return written


def _wrapper_in(scripts: Path) -> Path:
    """Stand the real wrapper up in a scripts directory of its own, and hand it back.

    The whole point of these journeys is a checkout that is missing something, so what
    is present has to be exactly what a runnable one has: the wrapper and the rule file
    it sources. Copying the wrapper alone would refuse on the helper, which is a real
    refusal and not the one under test.
    """
    return [
        _stand_in(scripts, source.name, source.read_text(encoding="utf-8"))
        for source in ASK_MANAGER_FILES
    ][0]


def test_a_channel_that_answers_with_nothing_is_reported_rather_than_read_as_an_answer(
    asked: Asked, tmp_path: Path
) -> None:
    """Exit 0 and no answer is a state, and it is not agreement.

    The channel closes without answering when the run settles while a question is
    waiting — nobody is left to read it. An exit status alone cannot tell that from an
    answer, so a wrapper that branched on the status would return an empty string as
    the manager's ruling and the agent would act on silence.

    Driven through `ONEPIPELINE_BIN`, which is what that seam is for.
    """
    silent = _stand_in(tmp_path / "bin", "onepipeline", SILENT_CHANNEL)

    # llmlint: ignore[e2e_not_mocked] The wrapper is real; a silent channel is the input.
    asking = _ask(asked, "Which way?", overrides={"ONEPIPELINE_BIN": str(silent)})
    status, out, err = _finish(asking)

    assert status != 0, out
    assert out == "", f"a channel that answered nothing produced something to act on:\n{out}"
    assert "closed without answering" in err, err


def test_a_toolchain_that_cannot_judge_the_answer_says_so_rather_than_falling_through(
    asked: Asked, tmp_path: Path
) -> None:
    """A broken judging helper is named, not left to `set -e` with somebody else's output.

    The wrapper decides what the channel handed back with a helper, and a helper that
    cannot run is the one failure that could leave an agent with no account of an answer
    it never got. Driven by putting a `python3` on PATH that serves the frame and then
    refuses to judge, in a copy of the wrapper with no pinned interpreter beside it —
    which is what a half-restored checkout does.
    """
    checkout = tmp_path / "checkout"
    copied = _wrapper_in(checkout / "scripts")
    (checkout / ".venv" / "bin").mkdir(parents=True)
    (checkout / ".venv" / "bin" / "onepipeline").symlink_to(REPO_ROOT / ".venv/bin/onepipeline")
    # llmlint: ignore[e2e_not_mocked] The wrapper is real; a broken judge is the input.
    _stand_in(tmp_path / "bin", "python3", BROKEN_JUDGE.format(python=REAL_PYTHON))
    environment = dict(asked.environment)
    environment["ONEPIPELINE_RUN_ID"] = asked.run
    environment["ORCHESTRATOR_ASK_MANAGER_TIMEOUT_SECONDS"] = str(SHORT_WINDOW_SECONDS)
    environment["PATH"] = f"{tmp_path / 'bin'}{os.pathsep}{environment['PATH']}"

    asking = subprocess.Popen(  # noqa: S603 - the real wrapper, with a broken interpreter
        [str(copied), "Which way?"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    status, out, err = _finish(asking)

    assert status != 0, out
    assert out == "", f"an unjudged answer still reached the caller:\n{out}"
    assert "could not be judged" in err and "just bootstrap" in err, err


#: A `python3` that refuses to encode the question as a channel frame. Told apart from
#: the classifier by the program that mentions the timeout reason, which only the
#: classifier is given — so this one fails first and nothing is ever asked.
BROKEN_ENCODER = """#!/usr/bin/env bash
for argument in "$@"; do
  case "$argument" in
    *timeout_reason*) exec {python} "$@" ;;
  esac
done
exit 4
"""


def test_a_question_that_cannot_be_encoded_is_refused_before_the_channel_is_reached(
    asked: Asked, tmp_path: Path
) -> None:
    """A frame that could not be built is named, not sent as an empty line.

    The frame is one compact line of JSON built by a helper, and `channel serve` reads
    exactly one line — so a helper that failed silently would submit nothing and be
    refused for a parse error, naming the symptom instead of the cause. Driven with a
    `python3` on PATH that refuses to encode, in a copy of the wrapper with no pinned
    interpreter beside it.
    """
    checkout = tmp_path / "checkout"
    copied = _wrapper_in(checkout / "scripts")
    (checkout / ".venv" / "bin").mkdir(parents=True)
    (checkout / ".venv" / "bin" / "onepipeline").symlink_to(REPO_ROOT / ".venv/bin/onepipeline")
    # llmlint: ignore[e2e_not_mocked] The wrapper is real; a broken encoder is the input.
    _stand_in(tmp_path / "bin", "python3", BROKEN_ENCODER.format(python=REAL_PYTHON))
    environment = dict(asked.environment)
    environment["ONEPIPELINE_RUN_ID"] = asked.run
    environment["ORCHESTRATOR_ASK_MANAGER_TIMEOUT_SECONDS"] = str(SHORT_WINDOW_SECONDS)
    environment["PATH"] = f"{tmp_path / 'bin'}{os.pathsep}{environment['PATH']}"

    asking = subprocess.Popen(  # noqa: S603 - the real wrapper, with an encoder that fails
        [str(copied), "Which way?"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    status, out, err = _finish(asking)

    assert status != 0, out
    assert out == "", out
    assert "could not be encoded" in err and "just bootstrap" in err, err


#: An `od` that answers in uppercase. The wrapper matches a reply against `[0-9a-f]`,
#: so a token minted in this shape is one its own classifier could never recognise —
#: which is the failure worth driving rather than a garbled read, because every ruling
#: would then read as carrying no token and put the question back.
UPPERCASE_OD = """#!/usr/bin/env bash
echo " AA BB CC DD EE FF AA BB CC DD EE FF"
"""


def test_a_token_the_classifier_could_not_match_is_refused_before_anything_is_asked(
    asked: Asked, tmp_path: Path
) -> None:
    """The minted token is checked against the shape replies are matched against.

    Non-emptiness is not enough. `TOKEN_PATTERN` is what decides whether a drawn ruling
    is this question's, so a token outside that shape is invisible to the wrapper's own
    classifier: every answer would read as carrying no token, and the question would go
    back as a second blocking surface on every attempt. That is the duplication this
    wrapper exists to prevent, arriving silently.

    Driven by putting an `od` on PATH that answers in uppercase, which is a real
    variation between coreutils and the busybox and locale-affected builds a container
    can carry. The refusal must land *before* the channel is reached, so the manager
    never sees a question no reply of theirs could answer.
    """
    # llmlint: ignore[e2e_not_mocked] The wrapper is real; a malformed mint is the input.
    _stand_in(tmp_path / "bin", "od", UPPERCASE_OD)

    asking = _ask(
        asked,
        "Which way?",
        overrides={"PATH": f"{tmp_path / 'bin'}{os.pathsep}{asked.environment['PATH']}"},
    )
    status, out, err = _finish(asking)

    assert status != 0, out
    assert out == "", f"a question carrying an unmatchable token still printed:\n{out}"
    assert "lowercase hex digits" in err, err
    assert next_surface(asked.run, asked.environment) is None, (
        "the question was put to the manager carrying a token no reply of theirs could "
        "echo, so answering it could only have re-asked it"
    )
