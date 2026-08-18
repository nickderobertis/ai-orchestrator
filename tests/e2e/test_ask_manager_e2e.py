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

import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple, NewType, TypedDict, cast

import pytest
from planner_channel import MANAGER_PATIENCE_SECONDS, TOKEN, Manager, next_surface, ruling
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The wrapper under test, run as an agent runs it.
ASK_MANAGER = REPO_ROOT / "scripts" / "ask-manager.sh"

#: The stand-in for the paid model, and the provider binary beneath it. Neither is
#: reached by these runs — a human gate dispatches nothing — and both are named for
#: the same reason a seatbelt is worn on a short drive: a plan that came to dispatch
#: would otherwise spend real provider quota from a suite.
FAKE_BACKEND = Path(__file__).resolve().parent / "fake_backend.py"
FAKE_CODEX = Path(__file__).resolve().parent / "fake_codex.py"

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

#: The reply window the wrapper is given while `planner_channel.Manager` looks for the
#: question. Deliberately the LONGER of the two, and that ordering is the point: with it
#: the other way round the wrapper gave up first, so a journey whose manager was merely
#: slow reported the timeout refusal — a real behavior, just not the one it was about —
#: and a journey whose manager genuinely failed reported nothing about why.
ANSWERED_WINDOW_SECONDS = int(e2e_timeout(MANAGER_PATIENCE_SECONDS * 2))

#: A run's own name on the ledger. Every planner-facing verb takes one and the
#: wrapper reads one out of the environment, so it is distinguished from the prose it
#: is built out of: what makes a string a run id is where it came from.
RunId = NewType("RunId", str)

#: How many times `scripts/ask-manager.sh` puts one question to the channel before it
#: gives up. Restated from the wrapper rather than imported, because it is shell; the
#: journey that drives it fails loudly if the two disagree, since a bound higher than
#: this leaves the wrapper still waiting and a lower one refuses before the last answer.
MAX_ATTEMPTS = 4

#: The agent node every plan below carries. It is never dispatched — it depends on the
#: human gate, which nobody attests — and it exists so a `context` live edit has a
#: node to be addressed to, which is how the misrouted-edit journey gets a real one.
WORK_NODE = "work"


class Asked(NamedTuple):
    """A live run to ask questions on, and the environment every verb needs to see it."""

    environment: dict[str, str]
    run: RunId


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
    # parametrized id carries brackets and spaces that a run id is not.
    named = re.sub(r"[^A-Za-z0-9]+", "-", request.node.name)[-40:].strip("-")
    run = RunId(f"ask-manager-{named}")
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
    launch = _just("orchestrate", str(plan), "--dag-graph", "off", environment=environment)
    assert launch.returncode == 0, f"the launch failed:\n{launch.stdout}\n{launch.stderr}"
    try:
        yield Asked(environment, run)
    finally:
        _just("stop", run, environment=environment, seconds=60)


def _ask(
    asked: Asked,
    *arguments: str,
    window: int = SHORT_WINDOW_SECONDS,
    overrides: dict[str, str] | None = None,
    stdin: str | None = None,
) -> subprocess.Popen[str]:
    """Start the real wrapper the way a dispatched agent runs it."""
    environment = dict(asked.environment)
    environment["ONEPIPELINE_RUN_ID"] = asked.run
    environment["ORCHESTRATOR_ASK_MANAGER_TIMEOUT_SECONDS"] = str(window)
    environment.update(overrides or {})
    asking = subprocess.Popen(  # noqa: S603 - the real wrapper, as an agent runs it
        [str(ASK_MANAGER), *arguments],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
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


def _finish(asking: subprocess.Popen[str], *, seconds: float = 180) -> tuple[int, str, str]:
    """Wait for one wrapper invocation and hand back what it reported."""
    try:
        out, err = asking.communicate(timeout=e2e_timeout(seconds))
    except subprocess.TimeoutExpired:
        asking.kill()
        out, err = asking.communicate()
        raise AssertionError(f"the wrapper never returned:\n{out}\n{err}") from None
    return asking.returncode, out, err


#: Every journey that drives the live channel is pinned to one worker. Not because the
#: channel is shared — each launches its own run under its own runs root — but because
#: each one is a wrapper process and a manager thread both waiting on `just` recipes,
#: and four of those racing the rest of a full suite is what turned a several-second
#: round trip into one that outlived the window it was given.
CHANNEL_GROUP = "ask-manager-channel"


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


@pytest.mark.xdist_group(CHANNEL_GROUP)
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

    status, out, err = _finish(asking)
    manager.checked(asker_said=err)

    assert status == 0, f"the wrapper did not accept the manager's answer:\n{err}"
    assert TOKEN.sub("", out).strip() == ANSWER, out
    assert err == "", f"a successful ask reported something on stderr:\n{err}"


@pytest.mark.xdist_group(CHANNEL_GROUP)
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


@pytest.mark.xdist_group(CHANNEL_GROUP)
def test_a_live_edit_envelope_routed_to_this_reader_is_refused_with_its_own_cause(
    asked: Asked,
) -> None:
    """A graph mutation delivered to the asking call is not the manager's answer.

    This is measured, not defensive padding. A reply on this channel is claimed by
    whichever reader reaches it next, and a real re-ask returned
    `{"version":1,"author":"monitor","commands":[{"op":"context",...}]}` — the monitor's
    live edit, addressed to the engine and delivered here because this call happened to
    arrive first. It carries no `completion` and no `reason`, so a wrapper checking only
    for the timeout string would pass a graph mutation through as prose.

    The envelope below is the real one: `just channel-reply` accepts it, the engine
    applies it to the graph, and it reaches the wrapper — which must refuse it naming a
    cause of its own, distinct from the timeout's.
    """
    asking = _ask(
        asked, "Which cursor shape should the route take?", window=ANSWERED_WINDOW_SECONDS
    )
    edit = json.dumps(
        {
            "version": 1,
            "author": "monitor",
            "commands": [{"op": "context", "id": WORK_NODE, "note": "the base moved under you"}],
        }
    )
    manager = Manager(asked.run, asked.environment, [lambda _token: edit])

    status, out, err = _finish(asking)
    manager.checked(asker_said=err)

    assert status != 0, f"the wrapper returned a live graph edit as an answer:\n{out}"
    assert out == "", f"a refused question still printed a ruling:\n{out}"
    assert "is not a ruling" in err and "boolean 'completion'" in err, err
    assert "synthesized its own ruling" not in err, (
        f"a misrouted live edit was reported as a timeout, which sends a manager to the "
        f"wrong repair:\n{err}"
    )


@pytest.mark.xdist_group(CHANNEL_GROUP)
def test_a_ruling_carrying_the_token_but_no_decision_is_refused(asked: Asked) -> None:
    """Echoing the token does not make an envelope a ruling.

    The token proves an answer was addressed to *this* question; `completion` proves it
    is an answer at all. Both are needed, and this is the case that separates them: a
    reply carrying the token and no decision reaches the wrapper — measured, the channel
    hands the envelope back verbatim — and must be refused rather than returned as the
    manager's prose.
    """
    asking = _ask(asked, "Is the cursor opaque?", window=ANSWERED_WINDOW_SECONDS)
    undecided = [lambda token: json.dumps({"version": 1, "message": f"maybe {token}"})]
    manager = Manager(asked.run, asked.environment, undecided)

    status, out, err = _finish(asking)
    manager.checked(asker_said=err)

    assert status != 0, f"the wrapper returned a decision-less envelope as an answer:\n{out}"
    assert out == "", f"a refused question still printed a ruling:\n{out}"
    assert "is not a ruling" in err, err


@pytest.mark.xdist_group(CHANNEL_GROUP)
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
    """
    misdirected = ruling("yes, that other node can be dropped")
    asking = _ask(asked, "Should the listing be paginated?", window=ANSWERED_WINDOW_SECONDS)
    manager = Manager(
        asked.run,
        asked.environment,
        [lambda _token: misdirected, lambda token: ruling(f"{ANSWER} {token}")],
    )

    status, out, err = _finish(asking)
    manager.checked(asker_said=err)

    assert status == 0, f"the wrapper did not survive a ruling meant for another reader:\n{err}"
    assert "that other node can be dropped" not in out, (
        f"the wrapper handed back a ruling that never saw its token:\n{out}"
    )
    assert TOKEN.sub("", out).strip() == ANSWER, out


@pytest.mark.xdist_group(CHANNEL_GROUP)
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


@pytest.mark.xdist_group(CHANNEL_GROUP)
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
        asking.kill()
        asking.communicate()


@pytest.mark.xdist_group(CHANNEL_GROUP)
def test_the_question_reaches_the_manager_as_the_surface_they_read(asked: Asked) -> None:
    """What the manager reads is the agent's question, plus how to answer it.

    The surface is the whole interface between the two: an agent's words, the run they
    are blocked on, and the token that binds the answer back. A surface missing any of
    those makes the manager guess, which is the failure the ask channel exists to end.
    """
    asking = _ask(
        asked, "Should the cursor be an opaque token or a node id?", window=ANSWERED_WINDOW_SECONDS
    )
    try:
        message = _waited_for_question(asked, asking)
        assert "Should the cursor be an opaque token or a node id?" in message, message
        assert asked.run in message, message
        assert TOKEN.search(message) is not None, message
        assert "just channel-reply" in message, message
    finally:
        asking.kill()
        asking.communicate()


@pytest.mark.xdist_group(CHANNEL_GROUP)
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
        asking.kill()
        asking.communicate()


@pytest.mark.xdist_group(CHANNEL_GROUP)
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
        asking.kill()
        asking.communicate()


@pytest.mark.xdist_group(CHANNEL_GROUP)
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
            asking.kill()
            asking.communicate()


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
    launch = _just("orchestrate", str(plan), environment=environment, seconds=300)
    try:
        assert launch.returncode == 0, f"the launch did not settle:\n{launch.stdout}{launch.stderr}"
        assert turns.is_file(), f"no harness turn was recorded at {turns}"
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


@pytest.mark.xdist_group("ask-manager-dispatch-environment")
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


@pytest.mark.xdist_group("ask-manager-dispatch-environment")
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
    detached = tmp_path / "checkout" / "scripts"
    detached.mkdir(parents=True)
    copied = detached / ASK_MANAGER.name
    copied.write_bytes(ASK_MANAGER.read_bytes())
    copied.chmod(0o755)
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


@pytest.mark.xdist_group(CHANNEL_GROUP)
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
    """
    asking = _ask(asked, "Which cursor shape?", window=ANSWERED_WINDOW_SECONDS)
    misdirected = ruling("this answers a different question")
    # One more than the wrapper's own bound, so the last one is unread if it stopped
    # where it promised: a manager whose answer is never taken is what this looks like.
    manager = Manager(
        asked.run, asked.environment, [lambda _token: misdirected for _ in range(MAX_ATTEMPTS)]
    )

    status, out, err = _finish(asking)
    manager.checked(asker_said=err)

    assert status != 0, f"the wrapper never stopped re-asking:\n{out}"
    assert out == "", f"a refused question still printed a ruling:\n{out}"
    assert "answers to other readers" in err and "include the token" in err, err


#: A `onepipeline` that answers the frame with nothing at all, at exit 0. The shape a
#: channel takes when the run settled while the question was waiting, and the one an
#: exit status alone cannot tell from an answer.
SILENT_CHANNEL = "#!/usr/bin/env bash\nexit 0\n"

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


@pytest.mark.xdist_group(CHANNEL_GROUP)
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


@pytest.mark.xdist_group(CHANNEL_GROUP)
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
    copied = _stand_in(checkout / "scripts", ASK_MANAGER.name, ASK_MANAGER.read_text("utf-8"))
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


@pytest.mark.xdist_group(CHANNEL_GROUP)
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
    copied = _stand_in(checkout / "scripts", ASK_MANAGER.name, ASK_MANAGER.read_text("utf-8"))
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
