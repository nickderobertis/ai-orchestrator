"""What this host's two supervisory members are really told, read out of real launches.

Three defects in that prose each had a supervisor misinform the operator it exists to
inform: a message body the shell executed before the verb ever saw it, a monitor that
surfaced its own turn preamble as a planner update, and a pacemaker that could not tell
a bounded wait from a hang. Whether a model then writes a *good* finding is the paid
model's business and untestable here. Which prose the model is given is neither, and it
is the half that kept regressing — because the two members are given their prose by two
different mechanisms, and an editor who knows only one of them changes nothing:

* the **monitor** is `graphs/dag-scope.yaml`'s two-party `kind: onejudge` member, so its
  effective system prompt is `config/onejudge.base.yaml` merged with
  `personas/orchestrator.yaml`, and it is readable off a launched run's own turns;
* the **check-in** pacemaker is single-sided `kind: oneharness`, which layers no persona
  at all — its effective prompt is the member's own `task`, so editing
  `personas/check-in.yaml` alone changes nothing the model reads.

Each is therefore read at the seam its own shape reaches a provider through, and only
the paid model is substituted. The pacemaker gets its own launch rather than riding the
first one for a reason the design makes unavoidable: its schedule is resettable, so a
monitor — which raises a planner surface on every turn — restarts its clock before it
can ever come due, and a journey that waited for it on a supervised run would wait
forever. So its member is launched on its own, taken verbatim out of the shipped graph
rather than retyped, which is the only way its own turn is observable at all.
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
from fake_backend import AGENT_DELAY_ENV, JUDGE_CONFIG_NAME, PROMPT_LOG_ENV
from harness_indirections import established_indirections
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The stand-in for the paid model at the `oneharness` seam a two-party member reaches
#: it through, and the stand-in one layer lower for the provider a single-sided member's
#: in-library turn spawns. Both, because the two members here are one of each.
FAKE_BACKEND = Path(__file__).resolve().parent / "fake_backend.py"
FAKE_CODEX = Path(__file__).resolve().parent / "fake_codex.py"

#: The directory put ahead of everything on `PATH`, holding a `claude` that refuses the
#: turn — the identities `ONEHARNESS_BIN_*` cannot reach, since that seam keys on a
#: harness id and reaches no variant.
PAID_PROVIDER_GUARD = Path(__file__).resolve().parent / "no-paid-provider"

#: The variable `tests/e2e/fake_codex.py` records a single-sided member's prompt to.
CODEX_PROMPT_LOG_ENV = "FAKE_CODEX_PROMPT_LOG"

#: The shipped example the monitor's launch runs, and the run id `onepipeline` mints
#: from its `name`. A one-node plan: what is under test is the observer graph every
#: launch attaches, not anything the plan's own node does.
SHIPPED_PROJECT = "examples:scheduler-research"
SHIPPED_RUN = "scheduler-research"

#: How long the stand-in holds the dispatched worker's turn. The monitor's own turns
#: answer before reaching that delay, so this buys a window in which the monitor is
#: certain to have taken one without slowing the watch.
WORKER_HELD_SECONDS = 20

#: The shipped observer graph, and the member of it each half of this journey reads.
DAG_SCOPE_GRAPH = "graphs/dag-scope.yaml"
MONITOR_MEMBER = "monitor"
PACEMAKER_MEMBER = "check-in"

#: Who `harness_indirections` attributes an unresolvable alternate identity to.
INDIRECTION_CALLER = "tests/e2e/test_supervisory_prompt_discipline_e2e.py"

#: The monitor's role, as `personas/orchestrator.yaml` states it. The composed run task
#: says only what the run is, so this is what tells a monitor turn from any other.
WATCH_ROLE = "Actively monitor one executing tracked graph"

#: The task the pacemaker's own launch composes, stated here rather than copied from
#: `onepipeline`: the member replaces whatever it is handed and must interpolate it
#: back in, so any distinctive text proves the expansion.
PACEMAKER_TASK = "onepipeline run `supervisory-prompt-probe`."

#: The byte-carrying form each member is told to raise a surface through: the verb
#: reading its text off stdin, with a single-quoted heredoc delimiter so the shell
#: expands nothing on the way there. Per member, because each names its own kind and
#: its own delimiter, and neither is interchangeable with a command-line word.
BYTE_CARRYING_FORM = {
    MONITOR_MEMBER: "onepipeline surface --kind finding \"$ONEPIPELINE_RUN_ID\" <<'FINDING'",
    PACEMAKER_MEMBER: "onepipeline surface --kind check-in <run-id> <<'UPDATE'",
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
#: is a file in the member's own scratch holding the timestamp of the last line read —
#: named on both sides, because a bar that requires "the stream since the cursor" of an
#: agent never told which file that is holds it to a file it cannot find.
CURSOR_FILE = "monitor.cursor"
READ_FROM_A_CURSOR = OnBothSides(
    monitor="read the stream from a cursor, never from a tail",
    review="Require the stream **since the cursor** as the evidence",
)
#: What happens when the cursor is not there to read: a bounded tail, and the turn goes
#: on. Both halves, because the failure this guards is a monitor that treated its own
#: missing or garbled scratch file as a reason to fail the turn, and a reviewer that
#: would then have rejected the fallback the agent was told to take.
CURSOR_FALLS_BACK_TO_A_TAIL = OnBothSides(
    monitor="is treated exactly as absent rather than failing the turn",
    review="reject a turn that failed rather than falling back",
)
CURSOR_WRITTEN_BACK = OnBothSides(
    monitor="then writes the last line's timestamp back",
    review="to have written the cursor forward",
)

#: The monitor's discipline on what a turn is allowed to say. Three halves now, because
#: the reporting route and the liveness rule are separate claims and each has its own
#: failure: prose reaches nobody, so a monitor told only to stop narrating would still
#: think a written-out observation had been reported; and a turn producing nothing at all
#: is what a lost turn looks like, so a prompt telling it to fall silent kills the member
#: on its first correct turn. `tests/e2e/test_monitor_quiet_turn_e2e.py` drives both.
NARRATION_BAN = "Never narrate what you are about to do"
PROSE_REACHES_NOBODY = "The prose of your reply reaches nobody"
ALWAYS_SOME_OUTPUT = "**Always produce some output, on every turn"

#: The pacemaker's discipline on what it may call a hang. Both halves, because the three
#: false escalations in one session split evenly between them: an elapsed time read
#: without the bound it was running against, and an absence of events read as silence
#: while the dispatch's own heartbeat was live.
BOUND_RULE = "compared its elapsed time against a bound you located and can name"
HEARTBEAT_RULE = "absence of events beside a live heartbeat as generation"

#: The pacemaker's discipline on what it may call dead. Both halves again, and for the
#: same reason the two above are separate: nine claims in one run that its driver had
#: gone were refuted by that run's own records, and each half is one of the two things
#: that would have caught them — showing the command and its output, which none of the
#: nine did, and reading again before the verdict, which is what parts a momentary gap
#: from a state.
LIVENESS_EVIDENCE_RULE = "Quote the command you ran and that command's own output"
LIVENESS_SECOND_READ_RULE = "read it a second time before you escalate to a terminal verdict"

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
    "onepipeline reply",
    "onepipeline surface",
)

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

#: The monitor's edit allowlist and the pacemaker's prohibition on issuing an edit at
#: all, asserted from the same effective prompts. The allowlist moves with the adopted
#: engine — `finding` joined it, and the weaker manager-note op left it when the engine
#: collapsed that op into `note` and kept `note` off the list — so what is held here is
#: that the five the engine really accepts are the five the model is offered, and that
#: what it refuses is still named as refusals.
MONITOR_ALLOWLISTED_OPS = (
    "`retry`",
    "`cancel`",
    "`requeue`",
    "`add`",
    "`finding`",
)
MONITOR_ALLOWLIST_CLAUSE = "You may issue exactly these five ops"
PACEMAKER_EDIT_PROHIBITION = "Never send `onepipeline reply`"


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
    environment["XDG_STATE_HOME"] = str(tmp_path / "state")
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
    examples = tmp_path / "examples"
    examples.mkdir()
    for records in ("projects", "tasks", "documents"):
        shutil.copytree(REPO_ROOT / "examples" / records, examples / records)
    environment["ONETASKGRAPH_SOURCES__EXAMPLES__CONFIG__ROOT"] = str(examples)
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
        # is: the monitor's judge side is `scripts/channel-serve.py`, so no turn of the
        # run carries that prose and the two files it is merged from each hold half of
        # it.
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


def _shipped_pacemaker_member() -> str:
    """The `check-in` member of the shipped graph, verbatim, with its schedule removed.

    Verbatim because retyping the member would prove a copy rather than the document an
    operator's runs actually attach; the schedule goes because it is half an hour, and
    what this fixture needs is the turn rather than the wait. Refs are rewritten from
    `../` to absolute, which is what they already resolve to: `oneagentgraph` resolves a
    relative ref against the directory the document was read from, and this copy is read
    from somewhere else.

    Read with a reader written for this one block rather than with a YAML library — the
    workspace installs none, and `oneagentgraph validate` on the real document is what
    holds it well-formed. Read as text at the same time: a graph the reader below could
    not find its member in would otherwise silently probe an empty document.
    """
    lines = (REPO_ROOT / DAG_SCOPE_GRAPH).read_text(encoding="utf-8").splitlines()
    opened = next(
        (index for index, line in enumerate(lines) if line.strip() == f"{PACEMAKER_MEMBER}:"),
        None,
    )
    assert opened is not None, (
        f"{DAG_SCOPE_GRAPH} declares no `{PACEMAKER_MEMBER}` member, so this journey "
        "is reading a document that has moved on without it"
    )
    indent = len(lines[opened]) - len(lines[opened].lstrip())
    block = [lines[opened]]
    for line in lines[opened + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        block.append(line)
    kept = [line for line in block if not line.strip().startswith("schedule:")]
    resolved = "\n".join(kept).replace("../", f"{REPO_ROOT}/")
    assert "task: |" in resolved, (
        f"the `{PACEMAKER_MEMBER}` member no longer claims its own `task`, which is the "
        "only prose a single-sided member is given"
    )
    return resolved


@pytest.fixture(scope="module")
def pacemaker_prompt(
    tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str
) -> Iterator[str]:
    """The pacemaker's whole effective prompt, off a real run of the shipped member.

    Its own launch, because on a supervised run it never comes due — the monitor resets
    its resettable clock on every turn — and because `oneagentgraph` is the party that
    composes this prompt: the member's `task` with `{task}` expanded, its persona
    contributing nothing, and `oneharness.check-in.toml` selecting the provider. The
    prompt is read out of the member's own report, which is where a library turn records
    what it composed, and is a better witness than a log because it is the producing
    library's own record.
    """
    tmp_path = tmp_path_factory.mktemp("pacemaker-prompt")
    environment = _environment(tmp_path, oneharness_bin)
    environment[CODEX_PROMPT_LOG_ENV] = str(tmp_path / "codex-prompts.jsonl")

    declared = next(
        line
        for line in (REPO_ROOT / DAG_SCOPE_GRAPH).read_text(encoding="utf-8").splitlines()
        if line.startswith("version:")
    )
    graph = tmp_path / "pacemaker-probe.yaml"
    graph.write_text(
        f"{declared}\nname: supervisory-prompt-probe\nmembers:\n{_shipped_pacemaker_member()}\n",
        encoding="utf-8",
    )

    ran = subprocess.run(
        ["oneagentgraph", "run", str(graph), "--task", PACEMAKER_TASK, "--dir", str(tmp_path)],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        check=False,
    )
    assert ran.returncode == 0, (
        f"the shipped `{PACEMAKER_MEMBER}` member did not run to a settlement:\n"
        f"{ran.stdout}\n{ran.stderr}"
    )
    settled = [
        json.loads(line)
        for line in ran.stdout.splitlines()
        if line.startswith("{") and json.loads(line)["kind"] == "member-settled"
    ]
    assert settled, f"the pacemaker member never settled:\n{ran.stdout}\n{ran.stderr}"
    report = json.loads(Path(settled[0]["payload"]["report_path"]).read_text(encoding="utf-8"))
    assert PACEMAKER_TASK in report["prompt"], (
        "the member was given a prompt that never expanded `{task}`, so this is not the "
        f"prompt a launched run composes:\n{report['prompt']}"
    )
    yield cast(str, report["prompt"])


def _assert_surface_text_travels_as_bytes(member: str, prompt: str) -> None:
    """One member's prompt hands the engine a surface's text rather than a shell word.

    The same four properties for each member, because the incident and the protection
    are the same: the verb reads the text from a file it is named or from stdin when it
    is named none, so a single-quoted heredoc on that stdin is prose the shell never
    parsed. The inline option survives for text a person typed, which is why the
    prohibition has to be present rather than the option merely unused.
    """
    flat = _flat(prompt)

    assert BYTE_CARRYING_FORM[member] in flat, (
        f"the {member} is no longer told to hand a surface's text to the verb as "
        f"bytes on its stdin, so its prose is shell input again:\n{prompt}"
    )
    assert SUBSTITUTING_FORM not in flat, (
        f"the {member} is told to build a surface message with command substitution "
        f"again, which is the exact form the incident was measured on:\n{prompt}"
    )
    # Every mention, not merely one: an instruction that also offers the inline form is
    # an instruction to use the inline form, and that is how this came back before.
    assert flat.count(INLINE_OPTION) == flat.count(INLINE_OPTION_PROHIBITION), (
        f"the {member}'s prompt mentions `{INLINE_OPTION}` somewhere other than the "
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
    _assert_surface_text_travels_as_bytes(MONITOR_MEMBER, monitor_prompt)


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


@pytest.mark.xdist_group("supervisory-prompts")
def test_the_monitors_reviewing_side_holds_it_to_that_same_run(monitored: Monitored) -> None:
    """The bar the planner rules against is bound to the same run, and read from a launch.

    This half reaches no model at all: the monitor's judge side is
    `scripts/channel-serve.py`, which raises `user.persona` to the live manager as its
    own surface. So it is what a *person* is asked to rule on, and a bar that still said
    `<run-id>` would ask them to accept an edit aimed anywhere.
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
        "the monitor is no longer told a `finding` raises no second `monitor-edit` "
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


@pytest.mark.xdist_group("supervisory-prompts")
def test_the_pacemaker_is_told_to_hand_its_update_over_as_bytes(
    pacemaker_prompt: str,
) -> None:
    """The same defect, in the member whose prose actually executed a command here.

    Read off the pacemaker's own prompt rather than off `personas/check-in.yaml`,
    because that file reaches this member only as a label: a single-sided
    `kind: oneharness` member layers no persona, so the `task` below is the whole of
    what the model is given and the only copy that can be relied on.
    """
    _assert_surface_text_travels_as_bytes(PACEMAKER_MEMBER, pacemaker_prompt)


@pytest.mark.xdist_group("supervisory-prompts")
def test_the_pacemaker_is_told_to_measure_a_bound_before_calling_anything_a_hang(
    pacemaker_prompt: str,
) -> None:
    """Elapsed time is not a verdict without the bound it was running against.

    Three escalations from this member in one session were false, and two of them are
    these: a command reported as unable to advance was 3m25s into a 360s bound and
    completed on its own at 360.068s, and "no turn activity for over two minutes" was a
    model mid-generation with a live heartbeat. A supervisor whose alarms are usually
    wrong stops being read, which costs more than the alarm was worth.
    """
    flat = _flat(pacemaker_prompt)

    assert BOUND_RULE in flat, (
        "the pacemaker may call something a hang again without naming the bound it "
        f"measured against:\n{pacemaker_prompt}"
    )
    assert HEARTBEAT_RULE in flat, (
        "the pacemaker is no longer told to read an absence of events beside a live "
        f"heartbeat as generation rather than as silence:\n{pacemaker_prompt}"
    )


# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] No journey below
# adds a launch to this tier: `pacemaker_prompt` and `monitored` are module-scoped and
# both already existed, so each reads a further answer off a launch `tests/e2e` was
# already spending, and `xdist_group` selects an xdist worker rather than a tier.
# Re-homing `tests/e2e` into an Nx project of its own is a restructuring of that whole
# tree and is enforcement configuration this change may not move in order to pass.
# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] Same site, same
# journeys, same reason: what an edge of their own would spare is a launch none of
# them spends, since each takes a module-scoped fixture that already existed.
@pytest.mark.xdist_group("supervisory-prompts")
def test_the_pacemaker_is_told_to_show_the_reading_behind_a_liveness_verdict(
    pacemaker_prompt: str,
) -> None:
    """Nothing is alive or dead until a command and its output say so, read twice.

    The false-escalation shape the bound rule above cannot reach: an elapsed time is at
    least a measurement, while a driver reported gone is a claim about a state with no
    reading shown for it at all. Nine such claims came from this member in one run, none
    quoting an output. What it cost was not a wrong action — the verb refuses to adopt a
    run something is still driving — but a supervisor whose terminal verdicts a planner
    learns to discount.

    Read off the pacemaker's own prompt, for the reason the byte-carrying journey above
    reads it there: `personas/check-in.yaml` reaches this member as a label and nothing
    else, so the `task` is the only copy that is in force.
    """
    flat = _flat(pacemaker_prompt)

    assert LIVENESS_EVIDENCE_RULE in flat, (
        "the pacemaker may report something dead again without showing the command it "
        f"ran or what that command answered:\n{pacemaker_prompt}"
    )
    assert LIVENESS_SECOND_READ_RULE in flat, (
        "the pacemaker may escalate to a terminal verdict on one reading again, which "
        f"cannot tell a momentary gap from a state:\n{pacemaker_prompt}"
    )


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
    So the persona names a cursor file in the member's own scratch, tells the agent to read
    everything after the timestamp it holds and write the last one back, and tells it what
    to do when the file is absent or garbled — read a bounded tail and go on — rather than
    fail the turn on its own bookkeeping.

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
        assert CURSOR_FILE in prose, (
            f"the monitor's {side} side no longer names the cursor file `{CURSOR_FILE}`, so "
            f"a transcript reader cannot tell what the member is writing to:\n{prose}"
        )
    for named, phrase in (
        ("to read the detailed stream from a cursor rather than a tail", READ_FROM_A_CURSOR),
        ("to write the cursor forward after each read", CURSOR_WRITTEN_BACK),
        (
            "to treat an absent, unreadable or non-timestamp cursor as a bounded tail",
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
def test_neither_members_action_space_moved_with_its_reporting_discipline(
    monitor_prompt: str, pacemaker_prompt: str
) -> None:
    """What these agents may *do* matches what the engine really allows them.

    The monitor's six ops are the engine's allowlist restated to the model it bounds —
    `tests/e2e/test_orchestrate_launch_e2e.py` is what drives the engine's own refusal
    of the four outside it — and the pacemaker is forbidden the edit verb outright
    because it takes one finitely deadlined turn and exits, so an edit it issued would
    be answered after it had stopped watching. A prompt that offered an op the engine
    refuses, or withheld one it accepts, would be invisible in the prose it shipped
    beside.
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
    assert PACEMAKER_EDIT_PROHIBITION in _flat(pacemaker_prompt), (
        "the pacemaker's prohibition on issuing a live edit is gone from the only prose "
        f"it is given:\n{pacemaker_prompt}"
    )
