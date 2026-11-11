"""What this host's supervisory member is really told, read out of a real launch.

Defects in that prose each had a supervisor misinform the operator it exists to inform: a
message body the shell executed before the verb ever saw it, a monitor that surfaced its
own turn preamble as a planner update, and a reporter that could not tell a bounded wait
from a hang. Whether a model then writes a *good* finding or a *good* report is the paid
model's business and untestable here. Which prose the model is given is neither, and it is
the half that kept regressing.

The **monitor** is `graphs/dag-scope.yaml`'s two-party `kind: onejudge` member, so its
effective system prompt is `config/onejudge.base.yaml` merged with
`personas/orchestrator.yaml`, and it is readable off a launched run's own turns. It is also
the author of the run's plain-language progress report, which its scheduled note says
*when* to write and its persona says *what* to write — so the report's rules are read here
off the same prompt, and only the paid model is substituted.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple, cast

import pytest
import short_state
from example_records import isolated_examples
from fake_backend import AGENT_DELAY_ENV, JUDGE_CONFIG_NAME, PROMPT_LOG_ENV
from harness_indirections import established_indirections
from project_fixtures import helper
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The stand-in for the paid model at the `oneharness` seam a two-party member reaches
#: it through, and the stand-in one layer lower for the provider a single-sided member's
#: in-library turn spawns — the change-request drafter a launch also attaches.
FAKE_BACKEND = helper("fake_backend.py")
FAKE_CODEX = helper("fake_codex.py")

#: The directory put ahead of everything on `PATH`, holding a `claude` that refuses the
#: turn — the identities `ONEHARNESS_BIN_*` cannot reach, since that seam keys on a
#: harness id and reaches no variant.
PAID_PROVIDER_GUARD = helper("no-paid-provider")

#: The shipped example the monitor's launch runs, and the run id `onepipeline` mints
#: from its `name`. A one-node plan: what is under test is the observer graph every
#: launch attaches, not anything the plan's own node does.
SHIPPED_PROJECT = "examples:scheduler-research"
SHIPPED_RUN = "scheduler-research"

#: How long the stand-in holds the dispatched worker's turn. The monitor's own turns
#: answer before reaching that delay, so this buys a window in which the monitor is
#: certain to have taken one without slowing the watch.
WORKER_HELD_SECONDS = 20

#: The shipped observer graph, and the member of it this journey reads.
DAG_SCOPE_GRAPH = "graphs/dag-scope.yaml"
MONITOR_MEMBER = "monitor"

#: Who `harness_indirections` attributes an unresolvable alternate identity to.
INDIRECTION_CALLER = "tests/e2e/launch_monitor_observer/test_supervisory_prompt_discipline_e2e.py"

#: The monitor's role, as `personas/orchestrator.yaml` states it. The composed run task
#: says only what the run is, so this is what tells a monitor turn from any other.
WATCH_ROLE = "Actively monitor one executing tracked graph"

#: The byte-carrying form the monitor is told to raise a surface through: the verb
#: reading its text off stdin, with a single-quoted heredoc delimiter so the shell
#: expands nothing on the way there. Per surface, because a finding and the progress
#: report each name their own kind and their own delimiter, and neither is
#: interchangeable with a command-line word.
BYTE_CARRYING_FORM = {
    "finding": "onepipeline surface --kind finding \"$ONEPIPELINE_RUN_ID\" <<'FINDING'",
    "progress report": "onepipeline surface --kind check-in \"$ONEPIPELINE_RUN_ID\" <<'REPORT'",
}

#: The form the incident was measured on, which no member may be told to use again.
#: Held separately from the count below because a prompt could carry this while also
#: carrying the safe form, and that is exactly how the defect came back the first time.
SUBSTITUTING_FORM = '--message "$(cat'

#: Every mention of the inline option a member's prompt is allowed to make: the one in
#: the sentence forbidding it. The count equality below is what keeps a prompt from
#: offering it as an alternative alongside the safe form.
INLINE_OPTION = "--message"
INLINE_OPTION_PROHIBITION = "reach for the inline `--message`"

#: The reason the instruction has to carry, so a later editor cannot restore the
#: substituting form as a simplification without first contradicting it.
SUBSTITUTION_REASON = "command substitution"

#: What the monitor is told a dispatch *is*, which is the reading behind an echo claim.
#: Three phrases rather than one sentence, because the finding this answers was grounded
#: in exactly one of the three being unknown: a dispatch is two parties of one
#: conversation, the stream labels both with one member and alternates the role, and a
#: turn's instruction repeating the previous turn's output is that handoff.
TWO_PARTIES = "A dispatch is two parties of one conversation"


class OnBothSides(NamedTuple):
    """One rule as each of the monitor's two sides has to state it.

    The member's own prose tells it how to read; its reviewing bar is what refuses a
    turn that read otherwise. A rule stated only in the first is advice, and one stated
    only in the second is enforced against an agent nobody told — so each is a phrase of
    its own, taken from the side that has to carry it.
    """

    monitor: str
    review: str


SAME_MEMBER_ALTERNATING_ROLE = OnBothSides(
    monitor="the **same member label**, with the **role** alternating",
    review="one member label with the role alternating",
)
REPEATED_INSTRUCTION_IS_THE_HANDOFF = OnBothSides(
    monitor="repeats the previous turn's output word for word is the handoff",
    review="repeats the previous turn's output is the handoff between them",
)
READ_BOTH_LABELS = OnBothSides(
    monitor="Read the member label and the role together before calling",
    review="does not read the member label beside the role",
)

#: What that reading rule must not become. It is a rule about naming one finding
#: correctly, and a monitor that read it as a reason to raise less would cost more than
#: the wrong finding did — so both sides say so, and this is what holds them to it.
STILL_RAISE_THE_ANOMALY = OnBothSides(
    monitor="That is a reading rule and not a narrowing",
    review="an unexplained observation is still a finding",
)

#: The rule the echo reading above widens into: a turn of a dispatch is never a manager
#: ruling, whatever role it carries. The journey below has the incident.
A_TURN_IS_NEVER_THE_PLANNER = OnBothSides(
    monitor="A turn inside a dispatch is never the planner, whatever role it carries",
    review="grounded in a turn of a dispatch offered as the planner's instruction",
)

#: The op named on both sides rather than left inside "every edit", because it is the one
#: that destroys what it is wrong about.
NO_CANCEL_FROM_A_TURN = OnBothSides(
    monitor="No `cancel` may be grounded in one",
    review="A `cancel` is the one to refuse hardest",
)

#: What *does* ground such a claim: a manager's instruction reaches a node as a `note` on
#: the run's channel, which the engine appends to that run's journal as an
#: `edit-committed` event carrying the command.
THE_RECORD_THAT_GROUNDS_A_RULING = "`edit-committed` event carrying that command against the node"

#: The monitor's discipline on *how much* of the detailed stream a turn reads. Its turns
#: are paced five minutes apart by `graphs/dag-scope.yaml`, and the streams it watches
#: move at some 300 worker events an hour, so a turn that read a fixed tail either missed
#: most of what landed or re-judged what the previous turn had already read. The cursor
#: is the verb's own: every `onepipeline monitor` read ends in one resume line, and
#: `--cursor` resumes from it — named on both sides, because a bar that requires "the
#: stream since the resume line" of an agent never told what that line looks like holds
#: it to a line it cannot find.
RESUME_LINE = "-- cursor"
CURSOR_OPTION = "--cursor"
READ_FROM_A_CURSOR = OnBothSides(
    monitor="read the stream from a cursor, never from a tail",
    review="Require the stream **since the previous turn's resume line** as the evidence",
)
#: Where the cursor comes from: the resume line the previous turn's read ended with,
#: which the held conversation keeps. It replaced a cursor file in the member's working
#: directory — which is the launch directory, a publication checkout on this host, whose
#: next `local-direct` landing was refused over the two files the persona had it write.
CURSOR_CARRIED_FROM_THE_RESUME_LINE = OnBothSides(
    monitor="Carry the cursor from the resume line your previous turn's read ended with",
    review="since the resume line its previous turn's read ended with",
)
#: What the member may not do to keep it, on both sides for that incident's sake.
WRITES_NO_FILE = OnBothSides(
    monitor="write no file, there or anywhere else",
    review="Reject a turn that wrote a file",
)
#: The files that incident left, which neither side may name as somewhere to write again.
RETIRED_CURSOR_FILES = ("monitor.cursor", "monitor.batch")
#: What happens when there is no cursor to read from, or the verb refuses the one given:
#: a bounded tail, and the turn goes on. Both halves, because the failure this guards is
#: a monitor that treated a refusal as a reason to fail the turn, and a reviewer that
#: would then have rejected the fallback the agent was told to take.
CURSOR_FALLS_BACK_TO_A_TAIL = OnBothSides(
    monitor="the command then falls back to a bounded tail",
    review="reads a bounded tail and says so; accept that",
)

#: The monitor's discipline on what a turn is allowed to say. Three halves now, because
#: the reporting route and the liveness rule are separate claims and each has its own
#: failure: prose reaches nobody, so a monitor told only to stop narrating would still
#: think a written-out observation had been reported; and a turn producing nothing at all
#: is what a lost turn looks like, so a prompt telling it to fall silent kills the member
#: on its first correct turn.
#: `tests/e2e/launch_monitor_observer/test_monitor_quiet_turn_e2e.py` drives both.
NARRATION_BAN = "Never narrate what you are about to do"
PROSE_REACHES_NOBODY = "The prose of your reply reaches nobody"
ALWAYS_SOME_OUTPUT = "**Always produce some output, on every turn"

#: The structured route the monitor is told to report a finding through, and the two
#: properties that make it different from every other op it may issue. Read out of the
#: effective prompt, because a route stated only in `personas/orchestrator.yaml` is a
#: route that may never have reached the model.
FINDING_OP_ENVELOPE = '{"op":"finding","message":"issue: ..."}'
FINDING_MUTATES_NOTHING = "It mutates no graph"
FINDING_RAISES_ONE_SURFACE = "queues no second `monitor applied an edit` surface"

#: The environment variable a launch names its run to an observer member in, and the
#: placeholder it replaced. Both are read out of the monitor's prompt: naming the
#: variable is what tells the member which run is its own, and the placeholder is what
#: it had instead — a literal `<run-id>` in every command, which a monitor could only
#: fill in by inferring which of this host's concurrent runs it was watching. It
#: inferred wrong, and edited a node of one run citing an edit attached to another's.
RUN_ID_ENV = "ONEPIPELINE_RUN_ID"
RUN_ID_PLACEHOLDER = "<run-id>"

#: How every command in that prompt has to spell the run, so a read, a reply, and a
#: surface all reach the run the launch bound the member to.
BOUND_RUN_WORD = '"$ONEPIPELINE_RUN_ID"'

#: The verbs whose examples take it. Each one is a different way to be wrong about
#: which run is the subject: reading somebody else's stream, replying on their
#: channel, or surfacing a finding onto it.
RUN_SCOPED_COMMANDS = (
    "onepipeline monitor",
    "onepipeline status",
    "onepipeline results",
    "onepipeline surface",
)

#: How the monitor's reply examples send an envelope of edits: the bus's own verb, over
#: this host's configuration and the channel of the run the launch bound it to — the
#: route `just channel-reply` takes for an edits-only envelope. The engine verb it
#: replaced is named here so its return fails the journey below.
SENT_ON_THIS_RUNS_CHANNEL = (
    "onemessagebus send replies --config config/onemessagebus.yaml "
    '--transport-dir "${ONEPIPELINE_RUNS_DIR:-runs}/$ONEPIPELINE_RUN_ID/channel"'
)
RETIRED_SEND = "onepipeline reply"

#: The bound on what the member may act on, and the permission that bound deliberately
#: does not take away. Both, because a rule that only forbade would cost the finding a
#: cross-run conflict is: it is visible to nobody but a reader who read both runs.
EDIT_IS_ABOUT_THIS_RUN = {
    MONITOR_MEMBER: "Every edit you issue is about",
    "review": "reject an edit whose subject is any other run",
}
FINDING_NAMES_ITS_RUN = {
    MONITOR_MEMBER: "A finding about any other run names that run",
    "review": "finding about any other run to name that run by id",
}
READING_ANOTHER_RUN_IS_ALLOWED = {
    MONITOR_MEMBER: "Reading another run is allowed",
    "review": "Reading another run is not the violation",
}

#: The grounding guard this change had to leave standing: a claimed rule violation
#: quotes the file and the line its rule comes from. It is a separate protection —
#: about inventing the rule rather than about mistaking the run — and a rewrite of
#: this prose that dropped it would look like a tidy-up.
GROUNDING_GUARD = {
    MONITOR_MEMBER: "Quote the file and the line it comes from",
    "review": "Reject a claimed rule violation that does not quote the file and the line",
}

#: Where `oneagentgraph` writes each member's effective onejudge config, named by this
#: journey so it reads this launch's own and never a concurrent dispatch's.
GRAPH_STATE_ENV = "ONEAGENTGRAPH_STATE_DIR"

#: The top-level key that opens the supervisor half of a onejudge config. Split on
#: rather than parsed, because the workspace installs no YAML reader and the two halves
#: have to be told apart: a rule stated only to the agent is advice, and one stated only
#: to the reviewer is enforced against an agent nobody told.
REVIEW_SIDE = "\nuser:\n"

#: The monitor's edit allowlist, asserted from the effective prompt. The allowlist
#: moves with the adopted engine — `finding` joined it, and the weaker manager-note op
#: left it when the engine collapsed that op into `note` and kept `note` off the list —
#: so what is held here is that the five the engine really accepts are the five the model
#: is offered, and that what it refuses is still named as refusals.
MONITOR_ALLOWLISTED_OPS = (
    "`retry`",
    "`cancel`",
    "`requeue`",
    "`add`",
    "`finding`",
)
MONITOR_ALLOWLIST_CLAUSE = "You may issue exactly these five ops"

#: The progress report, as the persona defines it: written when the scheduled note
#: arrives, even when nothing is wrong, as a plain-language overview a manager can relay,
#: drawn from the stream and the conversation, and raised as exactly one `check-in`
#: surface. The note says *when*; this is the *what*, on both sides.
REPORT_WHEN_THE_NOTE_ARRIVES = OnBothSides(
    monitor="When it arrives, write the report now, on that turn, **even when nothing is wrong**",
    review="reject one that skipped it because nothing was wrong",
)
REPORT_CONTENT = "what is **done**, what is **in progress**, what comes **next**, and any **risk**"
REPORT_DRAWN_FROM = (
    "Draw it from the detailed stream you have been reading and from this conversation's "
    "own history"
)
REPORT_RAISED_ONCE = OnBothSides(
    monitor="Raise it with exactly one `check-in` surface",
    review="raise exactly one `check-in` surface",
)

#: The surfaces the engine already raises to the planner itself, which the monitor is
#: told not to restate, each named as the engine names it.
ENGINE_SURFACES_NOT_RESTATED = OnBothSides(
    monitor="**Do not restate what the engine already surfaces to the planner.**",
    review="Reject a finding that only restates a surface the engine already raises",
)
ENGINE_SURFACES = (
    ("quiet-worker alerts", "a quiet worker (`quiet-worker`)"),
    ("settlements", "for every node's settlement"),
    ("check failures", "a failing check on a published change (`check-failed`)"),
    ("release waits", "a node waiting on a release (`release-wait`)"),
)
ADDS_ONLY_WHAT_THE_SURFACE_LACKS = "Add to one only with what that surface does not carry"

#: The undriven-node rule the adopted engine calls for. Its source raises `quiet-worker`
#: only for a dispatch its own driver holds in flight, and its surface vocabulary has no
#: kind for a node its ledger calls running that nothing drives — so the alarm stays the
#: monitor's, behind a second reading, on both sides.
UNDRIVEN_ALARM_KEPT = OnBothSides(
    monitor=(
        "**A node the ledger calls running that nothing drives is yours to raise, after a "
        "second reading.**"
    ),
    review=(
        "Reject an alarm that a node the ledger calls running has nothing driving it "
        "unless it quotes two readings"
    ),
)
UNDRIVEN_BOTH_READINGS_AGREE = (
    "raise it only when both readings agree, quoting each command and its output"
)


class Recorded(NamedTuple):
    """One turn as a stand-in recorded it, narrowed to what this journey reads."""

    config: str
    system: str
    prompt: str


class Monitored(NamedTuple):
    """Both halves of what one launch really told its monitor.

    They arrive by different routes and only one of them is a prompt. The agent's role
    is composed into a system prompt a turn carries, so it is read off the turn; the
    reviewing bar is `user.persona`, which this member's judge side is a command rather
    than a model — so it never appears in any turn, and the merged config
    `oneagentgraph` wrote for the member is the only place a launch's own copy of it
    exists.
    """

    #: The monitor's effective system prompt, off its first agent turn.
    system: str
    #: The `user:` half of the effective onejudge config the launch composed.
    review_bar: str


def _environment(tmp_path: Path, oneharness_bin: str) -> dict[str, str]:
    """The environment both launches get, with the paid provider substituted twice."""
    environment = dict(os.environ)
    for name in ("ONEPIPELINE_LAUNCHER", "ONEPIPELINE_LAUNCHER_SESSION", "CODEX_THREAD_ID"):
        environment.pop(name, None)
    environment["CLAUDE_CODE_SESSION_ID"] = "supervisory-prompt-e2e"
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment.update(established_indirections(INDIRECTION_CALLER))
    # Keeps this journey's graph scratch and history out of the host's, so it never
    # reads or reclaims a live dispatch's.
    environment["XDG_STATE_HOME"] = str(short_state.state_home(tmp_path))
    # Where the merged base ⊕ persona config is written. Named rather than left to the
    # state home above, because this journey globs for one member's copy of it and a
    # path it chose is the only one it can be sure belongs to its own launch.
    environment[GRAPH_STATE_ENV] = str(tmp_path / "graph")
    return environment


def _recorded(log: Path) -> list[Recorded]:
    """Every turn a stand-in recorded, as JSONL it wrote itself.

    Test-owned on both ends — `fake_backend.py` and `fake_codex.py` are this suite's
    files — so this states their schema rather than validating somebody else's.
    """
    if not log.is_file():
        return []
    written = [
        cast(dict[str, str], json.loads(line))
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return [
        Recorded(
            config=turn.get("config") or "",
            system=turn.get("system") or "",
            prompt=turn.get("prompt") or "",
        )
        for turn in written
    ]


def _flat(prompt: str) -> str:
    """One line of the prompt, so an assertion is not a claim about where it wrapped."""
    return " ".join(prompt.split())


# llmlint: ignore[expensive_tests_stay_behind_their_own_edge] Existing shared launch; this change only isolates its plan source.  # noqa: E501
@pytest.fixture(scope="module")
def monitored(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Iterator[Monitored]:
    """What one run launched through the real recipe really told its monitor, both sides.

    Everything between `just orchestrate` and the model is real here: the driver, the
    observer graph, `oneagentgraph`, the onejudge conversation, and the base config the
    persona merges into. The worker's turn is held only so the run is certainly still
    alive when the monitor takes its first one.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp_path = tmp_path_factory.mktemp("monitor-prompt")
    environment = _environment(tmp_path, oneharness_bin)
    # The run writes its settlements back to the project it was launched from, so it is
    # launched from a copy, and the block's exit holds the tracked records unchanged.
    with isolated_examples(tmp_path) as examples:
        environment.update(examples.environment)
        prompt_log = tmp_path / "prompts.jsonl"
        environment[PROMPT_LOG_ENV] = str(prompt_log)
        environment[AGENT_DELAY_ENV] = str(WORKER_HELD_SECONDS)

        launch = subprocess.run(
            ["just", "orchestrate", SHIPPED_PROJECT],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(300),
            check=False,
        )
        try:
            watching = [
                turn
                for turn in _recorded(prompt_log)
                # An agent side, by the one property that separates the two: the judge side
                # is the turn pinned to the judge config.
                if WATCH_ROLE in turn.system and Path(turn.config).name != JUDGE_CONFIG_NAME
            ]
            assert watching, (
                "no turn of this run carried the monitoring role, so nothing here reads an "
                f"effective monitor prompt:\n{launch.stdout}\n{launch.stderr}"
            )
            assert f"/members/{MONITOR_MEMBER}/" in watching[0].config, (
                f"the monitoring role arrived on a member other than `{MONITOR_MEMBER}` of "
                f"{DAG_SCOPE_GRAPH}: {watching[0].config}"
            )
            # The merged config, which is where this launch's own copy of the reviewing bar
            # is: the monitor's judge side is this host's bus binding rather than a model, so
            # no turn of the run carries that prose and the two files it is merged from each
            # hold half of it.
            # llmlint: ignore[tests_mirror_real_usage] No operator view carries a merged config.
            composed = sorted(
                Path(environment[GRAPH_STATE_ENV]).glob(
                    f"dag-scope-*/members/{MONITOR_MEMBER}/onejudge.yaml"
                )
            )
            assert composed, (
                f"the launch wrote no effective config for the `{MONITOR_MEMBER}` member "
                f"under {environment[GRAPH_STATE_ENV]}, so what its reviewing side was "
                f"given cannot be read at all:\n{launch.stdout}\n{launch.stderr}"
            )
            effective = composed[0].read_text(encoding="utf-8")
            _, separator, review_bar = effective.partition(REVIEW_SIDE)
            assert separator, (
                f"the effective config for `{MONITOR_MEMBER}` carries no `user:` block, so "
                f"the launch composed no reviewing bar at all:\n{effective}"
            )
            yield Monitored(system=watching[0].system, review_bar=review_bar)
        finally:
            subprocess.run(
                ["just", "stop", SHIPPED_RUN],
                cwd=REPO_ROOT,
                env=environment,
                capture_output=True,
                timeout=e2e_timeout(60),
                check=False,
            )


@pytest.fixture(scope="module")
def monitor_prompt(monitored: Monitored) -> str:
    """The monitor's effective system prompt alone, for the journeys that read one."""
    return monitored.system


def _assert_surface_text_travels_as_bytes(prompt: str) -> None:
    """The monitor's prompt hands the engine every surface's text rather than a shell word.

    The same properties for a finding and for the progress report, because the incident
    and the protection are the same: the verb reads the text from a file it is named or
    from stdin when it is named none, so a single-quoted heredoc on that stdin is prose the
    shell never parsed. The inline option survives for text a person typed, which is why
    the prohibition has to be present rather than the option merely unused.
    """
    flat = _flat(prompt)

    for surface, form in BYTE_CARRYING_FORM.items():
        assert form in flat, (
            f"the monitor is no longer told to hand a {surface}'s text to the verb as "
            f"bytes on its stdin, so its prose is shell input again:\n{prompt}"
        )
    assert SUBSTITUTING_FORM not in flat, (
        "the monitor is told to build a surface message with command substitution "
        f"again, which is the exact form the incident was measured on:\n{prompt}"
    )
    # Every mention, not merely one: an instruction that also offers the inline form is
    # an instruction to use the inline form, and that is how this came back before.
    assert flat.count(INLINE_OPTION) == flat.count(INLINE_OPTION_PROHIBITION), (
        f"the monitor's prompt mentions `{INLINE_OPTION}` somewhere other than the "
        f"sentence forbidding it, so the unsafe form is on offer again:\n{prompt}"
    )
    assert SUBSTITUTION_REASON in flat, (
        "the instruction no longer says why, so a later editor can restore the "
        f"substituting form as a simplification:\n{prompt}"
    )


@pytest.mark.xdist_group("supervisory-prompts")
def test_the_monitor_is_told_to_hand_a_surface_message_over_as_bytes(
    monitor_prompt: str,
) -> None:
    """The monitor's own prose can no longer be executed by the shell that surfaces it.

    `onepipeline surface`'s inline `--message` takes its text as an argument, so an
    instruction to pass a finding that way is an instruction to hand agent-authored
    prose to bash: inside double quotes backticks and `$(...)` are command
    substitution. This host measured the consequence — a surface quoting a command name
    was read back with that command replaced by its own output, having spent
    twenty-five minutes of a shared host's CPU on work nobody requested, with the
    mutation as the only trace. The verb now reads its text from a file or from stdin,
    so the protection is the form rather than a quoting rule the model has to apply.
    """
    _assert_surface_text_travels_as_bytes(monitor_prompt)


def _assert_bound_to_the_run_it_observes(side: str, prose: str) -> None:
    """One side of the monitor is told which run is its own, and bounded to it.

    The same four properties on both sides, because the failure needs only one of them
    missing: an agent told nothing keeps guessing, and a reviewer told nothing accepts
    the guess. Which run a supervisory agent is watching is not a detail it can infer
    on this host — several managers supervise several runs at once, and their events,
    directories and status views interleave — and the one that inferred it applied a
    live edit to a node of this run citing an edit that was attached to a different
    manager's.
    """
    flat = _flat(prose)

    assert RUN_ID_ENV in flat, (
        f"the monitor's {side} side no longer names {RUN_ID_ENV}, so nothing tells this "
        f"member which of the host's concurrent runs is its own:\n{prose}"
    )
    assert RUN_ID_PLACEHOLDER not in flat, (
        f"the monitor's {side} side addresses its run as the literal "
        f"{RUN_ID_PLACEHOLDER!r} again, which a member can only fill in by inferring "
        f"which run it is watching:\n{prose}"
    )
    assert EDIT_IS_ABOUT_THIS_RUN[side] in flat, (
        f"the monitor's {side} side no longer bounds an edit to the observed run, so an "
        f"edit may again be aimed at another manager's graph:\n{prose}"
    )
    assert FINDING_NAMES_ITS_RUN[side] in flat, (
        f"the monitor's {side} side no longer requires a finding about another run to "
        f"name that run, so evidence from a neighbouring workstream reads as this "
        f"run's:\n{prose}"
    )
    assert READING_ANOTHER_RUN_IS_ALLOWED[side] in flat, (
        f"the monitor's {side} side now forbids reading another run, which costs the "
        f"one finding only a reader of both runs can make:\n{prose}"
    )
    assert GROUNDING_GUARD[side] in flat, (
        f"the monitor's {side} side lost the grounding guard — a claimed rule violation "
        f"quotes the file and the line its rule comes from — which is a separate "
        f"protection from knowing which run it is watching:\n{prose}"
    )


@pytest.mark.xdist_group("supervisory-prompts")
def test_the_monitor_is_told_which_run_it_observes(monitored: Monitored) -> None:
    """The launch's own `ONEPIPELINE_RUN_ID` is what every command in the prompt takes.

    Read off the prompt a real launch composed rather than off `personas/orchestrator.yaml`,
    because a role stated in a file the launch does not merge is a role no model is
    given — and this member's prompt is `config/onejudge.base.yaml` merged with that
    persona, which exists nowhere until a launch composes it.
    """
    _assert_bound_to_the_run_it_observes(MONITOR_MEMBER, monitored.system)

    flat = _flat(monitored.system)
    unbound = [
        verb
        for verb in RUN_SCOPED_COMMANDS
        if f"{verb} {BOUND_RUN_WORD}" not in flat and f"--kind finding {BOUND_RUN_WORD}" not in flat
    ]
    assert not unbound, (
        f"the monitor's prompt shows {unbound} without the run the launch bound it to, "
        f"so those examples name whichever run the member decides it is watching:"
        f"\n{monitored.system}"
    )
    assert SENT_ON_THIS_RUNS_CHANNEL in flat, (
        "the monitor's reply examples no longer send through the bus onto the channel of "
        f"the run the launch bound it to:\n{monitored.system}"
    )
    assert RETIRED_SEND not in flat, (
        f"the monitor's prompt still offers `{RETIRED_SEND}`, which bypasses the bus:"
        f"\n{monitored.system}"
    )


@pytest.mark.xdist_group("supervisory-prompts")
def test_the_monitors_reviewing_side_holds_it_to_that_same_run(monitored: Monitored) -> None:
    """The bar the planner rules against is bound to the same run, and read from a launch.

    This half reaches no model at all: the monitor's judge side is `onemessagebus serve
    --codec monitor`, whose binding answers a turn itself and puts only the completion bar
    to the live manager. So it is the statement of what a *person* holds the monitor to, read in
    the persona it was merged from, and a bar that still said `<run-id>` would describe an
    edit aimed anywhere.
    """
    _assert_bound_to_the_run_it_observes("review", monitored.review_bar)


@pytest.mark.xdist_group("supervisory-prompts")
def test_the_monitor_is_told_the_structured_way_to_report_a_finding(
    monitor_prompt: str,
) -> None:
    """The `finding` op, and the two properties that make it worth preferring.

    A finding raised as an op is deliberate rather than the residue of a turn having
    produced prose, which is the whole of what this host's twenty-eight-surface run
    cost. Both properties are stated because both are why it does not add to that pile:
    it compiles to no graph mutation, and it is the one op on the allowlist that raises
    no second `monitor applied an edit` surface beside itself.
    """
    flat = _flat(monitor_prompt)

    assert FINDING_OP_ENVELOPE in flat, (
        "the monitor is no longer told how to report a finding as a structured op, so "
        f"its only route is prose a turn happened to produce:\n{monitor_prompt}"
    )
    assert FINDING_MUTATES_NOTHING in flat, (
        "the monitor is no longer told the `finding` op mutates no graph, which is "
        f"what makes it safe to reach for on any observation:\n{monitor_prompt}"
    )
    assert FINDING_RAISES_ONE_SURFACE in flat, (
        "the monitor is no longer told a `finding` raises no second `edit-applied` "
        f"surface, so it may report the same observation twice:\n{monitor_prompt}"
    )


@pytest.mark.xdist_group("supervisory-prompts")
def test_the_monitor_is_told_to_report_findings_rather_than_narrate_intent(
    monitor_prompt: str,
) -> None:
    """A turn with nothing to report owes the planner nothing, and is told what to say.

    The monitor's reply text is raised as a planner update, so a turn spent saying what
    it is about to look at costs a surface and carries no finding. One run queued
    twenty-eight of them, twenty-four content-free, and a worker's blocking question sat
    unread behind that pile for fifteen minutes with the frontier stopped — while `N
    planner update(s) waiting`, the one line a planner may never filter, said only that
    there were twenty-eight.

    All three are read, because each covers a different failure. A monitor not told that
    prose reaches nobody writes its observation out and believes it reported it — the
    prose safety net is gone, so that observation reaches no planner at all. A monitor
    not told to stop narrating spends turns on what it is about to read. And a monitor
    told to be quiet without being told to produce *something* emits nothing, which its
    judge side refuses as a lost turn, killing the member on its first correct turn.
    """
    flat = _flat(monitor_prompt)

    assert PROSE_REACHES_NOBODY in flat, (
        "the monitor is no longer told that the prose of its reply raises no planner "
        "surface, so an observation it writes out instead of filing as a `finding` op "
        f"reads to it like a report and reaches nobody:\n{monitor_prompt}"
    )
    assert NARRATION_BAN in flat, (
        "the monitor is no longer told to stop narrating what it is about to do, so it "
        f"can still spend turns on what it has not read yet:\n{monitor_prompt}"
    )
    assert ALWAYS_SOME_OUTPUT in flat, (
        "the monitor is no longer told to produce some output on every turn, so the only "
        "way it has left to be quiet is to emit nothing — which its judge side refuses "
        f"as a lost turn, killing the member on its first correct turn:\n{monitor_prompt}"
    )


# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] No journey below
# adds a launch to this tier: `monitored` is module-scoped and already existed, so each
# reads a further answer off a launch `tests/e2e` was already spending, and `xdist_group`
# selects an xdist worker rather than a tier. Re-homing `tests/e2e` into an Nx project of
# its own is a restructuring of that whole tree and is enforcement configuration this
# change may not move in order to pass.
# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] Same site, same
# journeys, same reason: what an edge of their own would spare is a launch none of
# them spends, since each takes a module-scoped fixture that already existed.
@pytest.mark.xdist_group("supervisory-prompts")
def test_the_monitor_is_told_to_write_the_progress_report_when_its_note_arrives(
    monitored: Monitored,
) -> None:
    """The report is the monitor's, written on the note's turn, even when nothing is wrong.

    The note `graphs/dag-scope.yaml` schedules says only *when*; what the report is lives
    in the persona, so it is read off the prompt a real launch composed and off the bar
    the planner holds the turn to. The report the manager relays to a user who has heard
    nothing for half an hour is the whole point of the note, so a turn that skipped it
    because the run was healthy is the one failure both sides name.
    """
    flat, review = _flat(monitored.system), _flat(monitored.review_bar)

    assert REPORT_WHEN_THE_NOTE_ARRIVES.monitor in flat, (
        "the monitor is no longer told to write the report on the note's turn even when "
        f"nothing is wrong:\n{monitored.system}"
    )
    assert REPORT_WHEN_THE_NOTE_ARRIVES.review in review, (
        f"the monitor's reviewing bar no longer rejects a skipped report:\n{monitored.review_bar}"
    )
    assert REPORT_CONTENT in flat, (
        f"the monitor is no longer told what the report covers:\n{monitored.system}"
    )
    assert REPORT_DRAWN_FROM in flat, (
        "the monitor is no longer told to draw the report from the stream and its own "
        f"conversation:\n{monitored.system}"
    )
    assert REPORT_RAISED_ONCE.monitor in flat, monitored.system
    assert REPORT_RAISED_ONCE.review in review, monitored.review_bar
    assert BYTE_CARRYING_FORM["progress report"] in flat, (
        "the monitor is no longer told to raise the report with `onepipeline surface "
        f"--kind check-in` reading a single-quoted heredoc:\n{monitored.system}"
    )


@pytest.mark.xdist_group("supervisory-prompts")
def test_the_monitor_is_told_not_to_restate_what_the_engine_already_surfaces(
    monitored: Monitored,
) -> None:
    """Quiet workers, settlements, check failures and release waits are the engine's to say.

    Each already reaches the manager as the engine's own surface, so a finding restating
    one costs the manager's attention for nothing new. Named one by one, because a rule
    naming none of them is one a model cannot apply, and held on the reviewing side too.
    """
    flat, review = _flat(monitored.system), _flat(monitored.review_bar)

    assert ENGINE_SURFACES_NOT_RESTATED.monitor in flat, monitored.system
    assert ENGINE_SURFACES_NOT_RESTATED.review in review, monitored.review_bar
    for named, phrase in ENGINE_SURFACES:
        assert phrase in flat, (
            f"the monitor is no longer told the engine already surfaces {named}, so it may "
            f"restate them:\n{monitored.system}"
        )
    assert ADDS_ONLY_WHAT_THE_SURFACE_LACKS in flat, monitored.system


@pytest.mark.xdist_group("supervisory-prompts")
def test_the_monitor_raises_an_undriven_node_only_on_two_agreeing_readings(
    monitored: Monitored,
) -> None:
    """The adopted engine raises no surface for a running node nothing drives, so it is kept.

    `quiet-worker` covers only a dispatch the engine's own driver holds in flight, so the
    alarm stays the monitor's — but every false one so far came from a view lagging a
    session already opened, so it is raised only when a second reading agrees, and the
    bar rejects one that quotes fewer.
    """
    flat, review = _flat(monitored.system), _flat(monitored.review_bar)

    assert UNDRIVEN_ALARM_KEPT.monitor in flat, monitored.system
    assert UNDRIVEN_BOTH_READINGS_AGREE in flat, monitored.system
    assert UNDRIVEN_ALARM_KEPT.review in review, monitored.review_bar


@pytest.mark.xdist_group("supervisory-prompts")
def test_the_monitor_is_told_what_a_dispatch_is_before_it_may_call_a_turn_an_echo(
    monitored: Monitored,
) -> None:
    """The monitor watches two-party conversations and had no account of what one is.

    It once read a handoff as a defect — a turn whose instruction was the previous
    turn's report byte for byte at the same timestamp, filed as that report "echoed back
    as fresh user input" — on a node whose six turn events all carried one member with
    the role alternating. Nothing in its prompt said what a dispatch is.

    Both sides, because a reading rule stated only to the agent is advice while one
    stated only to the reviewer is enforced against an agent nobody told; and the
    non-narrowing half on both, because the cheap wrong repair here is a monitor that
    answers this by raising less.
    """
    agent = _flat(monitored.system)
    review = _flat(monitored.review_bar)

    assert TWO_PARTIES in agent, (
        "the monitor is no longer told a dispatch is two parties of one conversation, "
        f"so a handoff between them reads as a duplicate again:\n{monitored.system}"
    )
    for named, phrase in (
        (
            "that both parties carry one member label with the role alternating",
            SAME_MEMBER_ALTERNATING_ROLE,
        ),
        (
            "that a repeated instruction is the handoff between them",
            REPEATED_INSTRUCTION_IS_THE_HANDOFF,
        ),
        ("to read both labels together before calling one an echo", READ_BOTH_LABELS),
        ("that this narrows nothing it is asked to raise", STILL_RAISE_THE_ANOMALY),
    ):
        assert phrase.monitor in agent, (
            f"the monitor's effective prompt no longer says {named}:\n{monitored.system}"
        )
        assert phrase.review in review, (
            f"the monitor's reviewing bar no longer says {named}, so the agent's own "
            f"prompt is the only place it is stated:\n{monitored.review_bar}"
        )


@pytest.mark.xdist_group("supervisory-prompts")
def test_the_monitor_may_not_read_a_manager_ruling_off_a_turn_of_a_dispatch(
    monitored: Monitored,
) -> None:
    """A dispatch's supervisor is not the planner, and no cancel may say it was.

    The sharper form of the echo rule above: that one is about mistaking a handoff for a
    duplicate and costs a wrong finding, and this one is about mistaking a dispatch's own
    supervising side for the manager, which cost a live dispatch. The persona has the
    incident.

    Three properties, on both sides for the reason the echo rule is on both: a reading
    rule stated only to the agent is advice, and one stated only to the reviewer is
    enforced against an agent nobody told. The `cancel` is named on both rather than left
    inside "every edit", because it is the op that destroys what it is wrong about. And
    the grounding half is what makes the rule followable — told only what is *not*
    evidence, an agent has nothing to look for instead, so both sides name the record the
    engine really writes when a manager's note reaches a node.
    """
    # What a member was really told exists on no planner-facing interface at all: the
    # agent's prose is a system prompt no view renders, and the reviewing bar reaches a
    # person over the channel rather than through any read. Reading the launch's own
    # composition is the only place either is observable, which is what this module is.
    # llmlint: ignore[tests_mirror_real_usage] No operator view carries an effective prompt.
    agent = _flat(monitored.system)
    # llmlint: ignore[tests_mirror_real_usage] Nor a merged config's reviewing bar.
    review = _flat(monitored.review_bar)

    for named, phrase in (
        ("that a turn of a dispatch is never a planner ruling", A_TURN_IS_NEVER_THE_PLANNER),
        ("that no `cancel` may be grounded in one", NO_CANCEL_FROM_A_TURN),
    ):
        assert phrase.monitor in agent, (
            f"the monitor's effective prompt no longer says {named}, so a turn its own "
            "supervising side improvised can be applied as the planner's ruling again:"
            f"\n{monitored.system}"
        )
        assert phrase.review in review, (
            f"the monitor's reviewing bar no longer says {named}, so nothing refuses an "
            f"edit grounded that way:\n{monitored.review_bar}"
        )

    for side, prose in (("agent", agent), ("review", review)):
        assert THE_RECORD_THAT_GROUNDS_A_RULING in prose, (
            f"the monitor's {side} side no longer names the record a claim about a "
            "manager's ruling has to rest on, so the rule says what is not evidence "
            f"without saying what is:\n{prose}"
        )


# llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted, at the
# same two seams every journey in this module reads its launch through.
@pytest.mark.xdist_group("supervisory-prompts")
def test_the_monitor_is_told_to_read_the_stream_from_a_cursor(monitored: Monitored) -> None:
    """A paced monitor accounts for the stream since its last turn, not for a tail of it.

    `graphs/dag-scope.yaml` holds the monitor's conversation five minutes between turns,
    and this host's recorded runs show what a monitor did with the stream before that:
    every turn piped `onepipeline monitor` through a fixed `tail -N`. At one turn per five
    minutes that reads a few of the 25–50 events that landed and re-reads them next turn.
    So the persona tells the agent to carry the cursor from the resume line its previous
    turn's read ended with, to write no file for it, and what to do when there is no
    cursor or the verb refuses it — read a bounded tail and go on — rather than fail the
    turn. `tests/e2e/launch_monitor_observer/test_monitor_cursor_e2e.py` runs the command
    this prose spells.

    Both sides, for the reason every two-sided rule here is: a reading rule stated only to
    the agent is advice, and one stated only to the reviewer is enforced against an agent
    nobody told. And the fallback on both, because a reviewer that did not know the
    fallback was the instruction would reject the turn that took it.
    """
    # llmlint: ignore[tests_mirror_real_usage] No operator view carries an effective prompt.
    agent = _flat(monitored.system)
    # llmlint: ignore[tests_mirror_real_usage] Nor a merged config's reviewing bar.
    review = _flat(monitored.review_bar)

    for side, prose in (("agent", agent), ("review", review)):
        for named in (RESUME_LINE, CURSOR_OPTION):
            assert named in prose, (
                f"the monitor's {side} side no longer names `{named}`, so the cursor it "
                f"is held to is not the one the verb prints and reads:\n{prose}"
            )
        for retired in RETIRED_CURSOR_FILES:
            assert retired not in prose, (
                f"the monitor's {side} side names `{retired}` again, the file whose "
                f"presence in a publication checkout refused its landing:\n{prose}"
            )
    for named, phrase in (
        ("to read the detailed stream from a cursor rather than a tail", READ_FROM_A_CURSOR),
        (
            "to carry the cursor from its previous turn's resume line",
            CURSOR_CARRIED_FROM_THE_RESUME_LINE,
        ),
        ("to write no file for it", WRITES_NO_FILE),
        (
            "to read a bounded tail when there is no cursor or it is refused",
            CURSOR_FALLS_BACK_TO_A_TAIL,
        ),
    ):
        assert phrase.monitor in agent, (
            f"the monitor's effective prompt no longer tells it {named}, so a paced turn "
            f"reads a tail of the stream again:\n{monitored.system}"
        )
        assert phrase.review in review, (
            f"the monitor's reviewing bar no longer holds it {named}, so the agent's own "
            f"prompt is the only place it is stated:\n{monitored.review_bar}"
        )


# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]
# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


@pytest.mark.xdist_group("supervisory-prompts")
def test_the_monitors_action_space_did_not_move_with_its_reporting_discipline(
    monitor_prompt: str,
) -> None:
    """What the monitor may *do* matches what the engine really allows it.

    The monitor's ops are the engine's allowlist restated to the model it bounds —
    `tests/e2e/launch_monitor_observer/test_orchestrate_launch_e2e.py` is what drives the
    engine's own refusal of those outside it. A prompt that offered an op the engine
    refuses, or withheld one it accepts, would be invisible in the prose it shipped beside.
    """
    flat_monitor = _flat(monitor_prompt)

    assert MONITOR_ALLOWLIST_CLAUSE in flat_monitor, (
        f"the monitor's effective prompt no longer states its op allowlist:\n{monitor_prompt}"
    )
    for operation in MONITOR_ALLOWLISTED_OPS:
        assert operation in flat_monitor, (
            f"the monitor's allowlist no longer offers {operation}, so this change "
            f"moved its action space as well as its reporting:\n{monitor_prompt}"
        )
    for refused in ("`complete`", "`attest`", "`drop`", "`reparent`"):
        assert f"{refused}" in flat_monitor, (
            f"the monitor is no longer told {refused} is not its to issue:\n{monitor_prompt}"
        )
