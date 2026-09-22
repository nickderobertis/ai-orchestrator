#!/usr/bin/env python3
"""A deterministic stand-in for the paid model, at the seam a dispatch reaches it.

`ONEAGENTGRAPH_ONEHARNESS_BIN` points at this file, so every harness turn a launched
run reaches by *spawning a CLI* — the monitor that watches it, its supervisor, each
dispatched worker, and each worker's supervisor — arrives here as one `oneharness
run` invocation. Everything above it stays real: the real
`just` recipe, the real `onepipeline` driver and engine verbs, the real
`oneagentgraph` graph configs in `graphs/`, and the real onejudge conversation
those two compose.

**A single-sided `kind: oneharness` member does not reach here, and since
oneagentgraph 0.2.18 it cannot.** That member runs oneharness *in process* —
`member-started` reports `runner: library` — so no oneharness binary is spawned for
this file to be, and the variable above cannot redirect it. Two members here are that
shape: the `check-in` pacemaker and `graphs/pr-author.yaml`'s change-request drafter.
Nothing here is wrong about them; they simply never arrive, and a journey that
assumed otherwise would spend real provider quota rather than fail. The seam that
still covers those members is one layer lower, at the provider binary:
`ONEHARNESS_BIN_CODEX` pointed at `fake_codex.py`, which
`tests/e2e/test_orchestrate_launch_e2e.py` sets for exactly this reason. The
classification below keeps its single-sided branch because a graph run through an
*older* pinned oneagentgraph still lands one here.

It stands in for the paid model and **only** the paid model: each invocation is
delegated to the real `oneharness` CLI with `--mock-harness`, so the real
argument validation, the real fallback chain, the real history record, and the
real report all run, with a scripted answer substituted for the provider's.
`MOCK_STDOUT` is how the shipped mock responder is told what to answer.

The harness invocation is the only seam the side that does the work can be faked
at: a graph member's agent side is an oneharness config, because
`oneagentgraph`'s graph schema has no command agent, so onejudge's own
`command` provider never serves an agent turn.

Which side an invocation is depends on how `oneagentgraph` pinned it, and it is read
here by the config's NAME:

* `--config .../oneharness.judge.toml` — a two-party member's supervisor. It
  answers the two JSON shapes onejudge asks it for: the supervisor verdict, and
  the `done_when` evaluation.
* `--config .../oneharness.toml` with NO judge config beside it — a single-sided
  `kind: oneharness` member (the check-in pacemaker). `oneagentgraph` writes both
  configs into a two-party member's scratch and only the one into a single-sided
  member's, so the sibling is what tells those two apart.
* anything else — the agent side of a two-party member. Since onepipeline 0.3.1 that
  arrives carrying `--config .../oneharness.toml`; before it, the config was implicit
  and discovered from the member's scratch, so no `--config` appeared at all. Both
  shapes land here, which is why neither the presence of a config nor its absence is
  what this reads.

Every agent turn here reports without changing anything, and that is the whole
script. It used to be conditional: a turn whose system prompt carried the
orchestrator's drive role was answered by really running the round verbs, because a
launched plan only settled if something drove it. The engine drives its own DAG
continuously to settlement now — there is no verb that advances a run, and
`personas/orchestrator.yaml` says in as many words that nothing there starts,
advances, or ends one. So the branch that drove is gone rather than kept as a
no-op: left in place it would have gone on claiming that a launch needs a
driver, and the round verbs it called are not in the adopted CLI's surface at all —
`tests/e2e/test_orchestrate_launch_e2e.py` reads that surface and holds it.
"""

# llmlint: ignore-file[boundary_inputs_validated] this deterministic test backend
# consumes one command line written by oneagentgraph and validates every field it
# reads from it below; an invocation it cannot classify fails closed.

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import TypedDict

#: The real CLI every invocation is delegated to. Named rather than discovered:
#: this file *is* `oneharness` as far as the run is concerned, so resolving the
#: name again would re-enter this script.
REAL_BINARY_ENV = "REAL_ONEHARNESS_BIN"

#: The one identity every delegated turn is pinned to, and mocked at.
#:
#: Both flags, and neither is optional. `--mock-harness ID` replaces the provider
#: process of **that exact identity** and no other — not `ID:variant`, and not the
#: rest of a `run_mode = "fallback"` chain — so mocking one candidate of a chain
#: that names six leaves the other five free to reach a paid subscription for
#: real. `--harness` is what makes the mock total, by leaving exactly one
#: candidate.
MOCK_HARNESS = "codex"

#: How the shipped mock responder is told what to answer.
MOCK_STDOUT_ENV = "MOCK_STDOUT"

#: The judge side's config file name, written by `oneagentgraph` into the
#: member's scratch. The agent side's is `oneharness.toml`, and it is not named on
#: the command line at all.
JUDGE_CONFIG_NAME = "oneharness.judge.toml"

#: What this stand-in answers for a single-sided member — one with no judge config
#: beside it, which on this host is `graphs/dag-scope.yaml`'s `check-in` pacemaker.
#: Named rather than inlined because a journey asserting that member's surface still
#: reaches the planner's queue has to recognise it, and two copies of the sentence
#: would let that assertion pass against a report nothing produced.
PACEMAKER_REPORT = "the stand-in pacemaker reported"

#: The task `onepipeline` composes for the dag-scope graph. Since onepipeline 0.2.0 it
#: says what the run *is* — its id and its goal — and no longer what to do with it;
#: before that it opened `Drive run <RUN> to settlement`, and every member that took a
#: task was handed that instruction whether or not driving was its job.
RUN_TASK = re.compile(r"onepipeline run `([^`]+)`")

#: Where a turn's harness config arrives, which is what says which member — and
#: which side of it — this invocation serves.
CONFIG_FLAG = "--config"

#: Where onejudge puts the composed system prompt on the harness command line.
SYSTEM_FLAG = "--system"

#: Where the directory a turn is to be served in arrives. It is what `oneharness`
#: hands the provider as its working directory, so it — and not this process's own
#: `cwd`, which is the member's scratch — is where a dispatched agent starts.
CWD_FLAG = "--cwd"

#: The fragment onejudge's `done_when` evaluation prompt ends with. The supervisor
#: turn and the evaluation turn reach the same config, and they want different
#: JSON, so the prompt is what tells them apart.
EVALUATION_MARKER = '{"value": true or false'

#: Optional test-owned transcript sink for assertions about how a turn was pinned:
#: its config, its directory, its prompt, its composed system prompt, and whichever
#: of its environment variables the journey asked for.
PROMPT_LOG_ENV = "FAKE_BACKEND_PROMPT_LOG"

#: Which environment variables the record above carries, comma-separated.
#:
#: Opt-in and named rather than the whole environment, because the whole environment
#: is this host's — credentials, config directory indirections, the enclosing
#: dispatch's own identity — and a journey's evidence file is not the place for it.
#: What a turn was *given* is otherwise unobservable from outside: a variable reaches
#: a dispatch by inheritance through `onepipeline`, `oneagentgraph`, and `oneharness`,
#: and none of them reports what it passed on. A name that is unset is recorded absent,
#: which is the whole point: what a launch establishes is exactly what its dispatches
#: have, and measurement is the only thing that says which launch shape establishes
#: what. Both `ONEPIPELINE_RUN_ID` and `ORCHESTRATOR_ASK_MANAGER` are read this way by
#: `tests/ask_seam/launch/test_launch_ask_seam_e2e.py`, whose journeys own the current
#: answers.
ENVIRONMENT_KEYS_ENV = "FAKE_BACKEND_ENVIRONMENT_KEYS"

#: The seam a dispatched agent reaches its manager through, run by the branch below
#: exactly as `personas/planner.yaml` tells an agent to run it.
ASK_MANAGER_ENV = "ORCHESTRATOR_ASK_MANAGER"

#: The question one dispatched agent turn puts to its manager, and where what came
#: back is written. Both, or neither: an ask is a blocking round trip through the
#: run's own channel, so it happens only for a journey that is also playing the
#: manager. Asking is otherwise unobservable for the same reason the environment is —
#: a real agent runs that command inside its own turn, and nothing above the turn
#: reports that it did.
ASK_QUESTION_ENV = "FAKE_BACKEND_ASK_QUESTION"
ASK_RECORD_ENV = "FAKE_BACKEND_ASK_RECORD"
#: The reply window that ask waits, passed as the verb's own `--timeout` when a journey
#: names one; unset, the ask waits the window the run's launch record carries.
ASK_TIMEOUT_ENV = "FAKE_BACKEND_ASK_TIMEOUT"

#: The `graphs/node-scope.yaml` member a dispatched plan node runs as, which is what
#: makes the branch below about a *dispatch*: `oneagentgraph` names every member's
#: scratch after it and pins that member's configs inside it, so the recorded
#: `--config` is what says whose turn this is. The dag-scope monitor reaches the same
#: agent branch and must never be the one that asks.
DISPATCHED_MEMBER = "worker"
MEMBER_OF_CONFIG = re.compile(r"/members/([^/]+)/")

#: Optionally answer THAT member's agent turn with this text instead of the default.
#:
#: Opt-in and unset everywhere else, so every other journey reads the same monitor turn
#: it always did. It exists for one thing a fixed answer cannot reach: the monitor's
#: reply is what its judge side is handed, and a reply carrying no finding is the turn
#: that used to KILL the member. Proving it no longer does means a real launch whose
#: monitor really takes one, and the monitor's words are the paid model's, which is the
#: one thing doubled here.
OBSERVER_ANSWER_ENV = "FAKE_BACKEND_OBSERVER_ANSWER"
OBSERVER_MEMBER_ENV = "FAKE_BACKEND_OBSERVER_MEMBER"

#: Optionally have THAT member's every agent turn LOST instead of answered.
#:
#: A lost turn is the provider failing, so what is scripted is the provider process
#: exiting non-zero under oneharness's own mock responder (`MOCK_EXIT`): the real
#: oneharness reports the candidate failed, and the real onejudge reports the turn to its
#: judge side as `turn.outcome: lost`, naming the cause and harness it read off that
#: report. Nothing here writes what a lost turn looks like.
OBSERVER_LOSES_ENV = "FAKE_BACKEND_OBSERVER_LOSES"
MOCK_EXIT_ENV = "MOCK_EXIT"

#: Optionally have the dispatched agent turn WRITE the files a real one would.
#:
#: A planner's whole deliverable is a plan it authors as records in a local Markdown
#: store, and `scripts/plan.sh` records a planner pass for whatever that run authored.
#: A stand-in that only reports leaves nothing for the closeout to find, so a journey
#: about the closeout would be measuring an empty diff. This names a JSON file mapping
#: each destination path to its content, written once, by the dispatched member alone —
#: the one action a planner takes that anything downstream of the turn can observe.
AUTHOR_PLAN_ENV = "FAKE_BACKEND_AUTHOR_PLAN"

#: Optionally have a dispatched agent turn RUN commands, chosen by a marker its own task
#: carries.
#:
#: What a dispatched agent *does* is run programs in its own working directory, so this
#: substitutes the model's decision — which commands this turn runs — and nothing below
#: it: the programs are the real ones, they run where the dispatch runs, and every record
#: they leave is theirs. That is the difference between a journey that proves a dispatch
#: produced something and one that proves this file can write a path.
#:
#: `AUTHOR_PLAN_ENV` above is the other seam and answers a different question: a planner's
#: deliverable is prose, and no program turns a model's prose into a plan. Where one
#: exists — the store's own command line, for a document — the turn runs it.
#:
#: The marker is how a run with two dispatched nodes says which of them acts: both run as
#: `worker`, so the member cannot tell them apart and the composed task is the only thing
#: that can. This names a JSON file mapping a marker to the argument vectors a turn whose
#: prompt carries that marker runs, in order.
#:
#: Unclaimed, unlike the one above: a supervisor sends a dispatch back for more, so this
#: branch is reached repeatedly. A command named here is one a real turn could repeat —
#: which is what a claim would otherwise be hiding — and what a claim would cost is a
#: second node's commands being swallowed by the first node's having consumed the file.
RUN_ON_MARKER_ENV = "FAKE_BACKEND_RUN_ON_MARKER"

#: Optionally have the dag-scope MONITOR's agent turn run the stream read its own
#: effective prompt spells, carrying the cursor from one turn to the next.
#:
#: What a monitor does on a turn is run `onepipeline monitor` from the resume line its
#: previous turn's read ended with. A real provider keeps that line in the held
#: conversation; the prompt a turn arrives with here does not carry it, because the graph
#: hands each turn only its new message and the provider session holds the rest. So this
#: names a JSONL file standing in for that conversation and for nothing else: each turn
#: appends what it said, and the next turn of the same session reads its resume line back
#: out of it. The command is the `sh` block of the member's own `--system` prompt, run
#: where the turn runs — `--cwd`, which is the launch directory — through whichever
#: `onepipeline` the member's PATH resolves. So a prompt that wrote a file, or spelled a
#: read the verb refuses, is exactly what a journey reading this sees.
MONITOR_READS_ENV = "FAKE_BACKEND_MONITOR_READS"
#: Which of those turns, counted from 1 per session and comma-separated, are handed a
#: cursor the verb refuses in place of the carried one. The model's choice of cursor is
#: the decision substituted; the refusal, and the fallback it takes, are the real ones.
MONITOR_REFUSED_TURNS_ENV = "FAKE_BACKEND_MONITOR_REFUSED_TURNS"
REFUSED_CURSOR = "1:not-the-watched-run:0"
MONITOR_MEMBER = "monitor"
#: Where a turn's conversation handle arrives, which is what says two turns are one
#: conversation.
SESSION_FLAG = "--session"
#: A fenced shell block of the composed system prompt, the one assignment in it this
#: stand-in fills in with the cursor it carries, and the resume line a read ends with.
SHELL_BLOCK = re.compile(r"^```sh\n(?P<body>.*?)^```", re.MULTILINE | re.DOTALL)
CURSOR_ASSIGNMENT = re.compile(r"^CURSOR='[^'\n]*'$", re.MULTILINE)
RESUME_LINE = re.compile(r"^-- cursor (\S+)$", re.MULTILINE)
#: What a carried cursor has to be spelled as before it is handed to the shell; anything
#: else is carried as no cursor, which the prompt's own read refuses and falls back from.
CURSOR_SPELLING = re.compile(r"1:[A-Za-z0-9_.-]+:[0-9]+")
#: What the monitor says above what its read rendered: words, so the turn is a quiet one.
MONITOR_READ_PREAMBLE = "read the detailed stream since my cursor; nothing needed raising"
#: The environment a read turn records, which is what a later turn of the same member
#: has to be given to be that member — `XDG_STATE_HOME` among it, because the member's
#: session store, and the `--control` socket in it, live under that directory.
MONITOR_ENVIRONMENT_KEYS = ("ONEPIPELINE_RUN_ID", "ONEPIPELINE_RUNS_DIR", "PATH", "XDG_STATE_HOME")


class MonitorRead(TypedDict):
    """One monitor turn's read, as `MONITOR_READS_ENV` records it."""

    session: str
    turn: int
    cursor: str
    cwd: str | None
    onepipeline: str | None
    status: int | None
    output: str
    answer: str
    argv: list[str]
    environment: dict[str, str | None]


def monitor_reads(log: Path) -> list[MonitorRead]:
    """Every read turn recorded so far, oldest first; `[]` before the first."""
    if not log.is_file():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]


def _read_the_stream(
    original: list[str], config: str | None, system: str, cwd: str | None
) -> str | None:
    """Run the monitor's own stream read for this turn and return what the turn says.

    `None` when this turn is not a monitor read turn at all, so the caller answers it the
    way it answers every other turn.
    """
    log = os.environ.get(MONITOR_READS_ENV)
    named = MEMBER_OF_CONFIG.search(config or "")
    if not log or named is None or named.group(1) != MONITOR_MEMBER:
        return None
    session = _flag(original, SESSION_FLAG) or config or ""
    earlier = [read for read in monitor_reads(Path(log)) if read["session"] == session]
    turn = len(earlier) + 1
    carried = RESUME_LINE.findall(earlier[-1]["answer"]) if earlier else []
    cursor = carried[-1] if carried and CURSOR_SPELLING.fullmatch(carried[-1]) else ""
    if str(turn) in os.environ.get(MONITOR_REFUSED_TURNS_ENV, "").split(","):
        cursor = REFUSED_CURSOR
    spelled = [
        block.group("body")
        for block in SHELL_BLOCK.finditer(system)
        if "onepipeline monitor" in block.group("body")
        and CURSOR_ASSIGNMENT.search(block.group("body"))
    ]
    status: int | None = None
    if len(spelled) != 1:
        output = (
            f"fake_backend: the monitor's system prompt spells {len(spelled)} cursor reads "
            "(a `sh` block running `onepipeline monitor` with one `CURSOR='…'` line), not one"
        )
    else:
        assigned = f"CURSOR={shlex.quote(cursor)}"
        script = CURSOR_ASSIGNMENT.sub(lambda _: assigned, spelled[0], count=1)
        # llmlint: ignore[no_injection_from_untrusted_input] Running the shell the member's
        # own system prompt spells is the model's action this stand-in substitutes for, so
        # the prompt is the program by design; the one value spliced in is shell-quoted.
        ran = subprocess.run(  # noqa: S603 - the prompt's own command, where the turn runs
            ["bash", "-c", script],
            cwd=cwd or None,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            check=False,
        )
        status, output = ran.returncode, ran.stdout
    answer = f"{MONITOR_READ_PREAMBLE}\n\n{output}"
    resolved = shutil.which("onepipeline")
    read: MonitorRead = {
        "session": session,
        "turn": turn,
        "cursor": cursor,
        "cwd": cwd,
        "onepipeline": str(Path(resolved).resolve()) if resolved else None,
        "status": status,
        "output": output,
        "answer": answer,
        "argv": original,
        "environment": {key: os.environ.get(key) for key in MONITOR_ENVIRONMENT_KEYS},
    }
    with Path(log).open("a", encoding="utf-8") as recorded:
        recorded.write(json.dumps(read) + "\n")
    return answer


#: Optionally hold every two-party AGENT turn open this many seconds before answering.
#:
#: A journey about a run that is *live* needs one, and a stand-in that answers at
#: process speed leaves no window to observe: the node is dispatched and settled
#: between two reads. This is the only way to make "while a node is running" a state
#: a test can be in rather than a race it can lose. It delays the answer and nothing
#: else — the same turn, through the same real CLI, with the same scripted reply.
#:
#: Named for the side rather than for the member, because that is what it does: a
#: dispatched worker and a dag-scope monitor reach the same branch below, so a
#: journey wanting only the worker delayed launches with no observer graph.
AGENT_DELAY_ENV = "FAKE_BACKEND_AGENT_DELAY_SECONDS"

#: Optionally hold a dispatched worker's first turn at this boundary — before the turn is
#: recorded or answered — until a journey lets it go. Names a directory: the turn writes
#: `TURN_GATE_REACHED` there and waits for `TURN_GATE_RELEASED`, so a journey can read a run's
#: state at the one moment the engine has dispatched work and no worker has taken a turn.
#:
#: A gate rather than a delay because what it replaces is a race: a delay lets a turn be
#: recorded and then waits, so a journey polling for "no turn yet" can only ever lose. The
#: judge side is never held, and once released the gate stays open for every later turn.
TURN_GATE_ENV = "FAKE_BACKEND_TURN_GATE"
TURN_GATE_REACHED = "reached"
TURN_GATE_RELEASED = "released"
#: How long a held turn waits before giving up, so a journey that failed before releasing
#: the gate ends its run instead of leaving a dispatch parked for ever.
TURN_GATE_CEILING_SECONDS = 600

#: Set to anything non-empty, the stand-in's supervisor sends the worker back **once**
#: before it accepts. It is what gives a journey a conversation of more than one worker
#: turn: the second turn opens on the supervisor's own words, which is the turn
#: `oneagentgraph` stamps `origin: supervisor`, and a supervisor that accepts on the
#: first never produces one. The decision is read off the prompt — how many worker
#: replies the transcript it carries holds — rather than remembered in a file, because
#: one supervisor decision can invoke this process more than once: a judge turn asked
#: under `--control` whose socket address the harness refuses is asked again without
#: it, and a marker written by the refused invocation would make the retry accept.
JUDGE_SEND_BACK_ENV = "FAKE_BACKEND_JUDGE_SENDS_BACK_ONCE"
#: What the stand-in worker says on every turn, and what the supervisor counts.
WORKER_REPLY = "the stand-in worker reported without changing anything"


class RecordedTurn(TypedDict):
    """One turn this backend records when `PROMPT_LOG_ENV` names a sink.

    Declared here, beside the only thing that writes it, so a reader imports this rather
    than restating it: the fields below and the `json.dumps` that persists them are one
    declaration, and adding a field cannot leave a reader describing the old shape.
    """

    config: str | None
    cwd: str | None
    prompt: str
    system: str | None
    environment: dict[str, str | None]
    scripted_answer: str | None


def _flag(argv: list[str], name: str) -> str | None:
    """The value this invocation passed for `name`, if it passed one at all."""
    for index, argument in enumerate(argv):
        if argument == name and index + 1 < len(argv):
            return argv[index + 1]
    return None


def _prompt(argv: list[str]) -> tuple[list[str], str]:
    """This invocation's prompt, and an argv the real CLI can still be given.

    onejudge writes the prompt to `--prompt-file -`, this process's standard
    input, which can only be read once. So a piped prompt is read here and spilled
    to a file the delegated run is pointed at instead; anything else is left
    exactly as it arrived.
    """
    for index, argument in enumerate(argv):
        if argument == "--prompt" and index + 1 < len(argv):
            return argv, argv[index + 1]
        if argument == "--prompt-file" and index + 1 < len(argv):
            named = argv[index + 1]
            if named != "-":
                return argv, Path(named).read_text(encoding="utf-8")
            text = sys.stdin.read()
            # `delete=False`: the delegated `oneharness` is a child process that
            # opens this path after the block closes it, so the file has to outlive
            # the handle. It is one prompt in the run's own temporary directory.
            with tempfile.NamedTemporaryFile(
                "w", suffix=".prompt", delete=False, encoding="utf-8"
            ) as spilled:
                spilled.write(text)
            replaced = list(argv)
            replaced[index + 1] = spilled.name
            return replaced, text
    return argv, ""


def _ask_manager(config: str | None) -> None:
    """Put one blocking question to the manager, from a dispatched agent's own turn.

    This is where a stand-in has to *act* rather than record: the seam is a command an
    agent runs, so the only way to prove a dispatch can use it is for the process
    serving that dispatch to run it and report what came back. Everything it needs —
    the wrapper's path, the run to ask on, the reply window — comes from this turn's
    own inherited environment, so a launch that established one of them badly fails
    here exactly as it would for a real agent.

    Exactly one turn asks, claimed by creating the record exclusively: a dispatch
    reaches this repeatedly (the supervisor sends it back for more), and a manager
    playing one answer would leave every later ask waiting on a reply nobody sends.

    A wrapper that is not in the environment is recorded as absent rather than skipped,
    because that absence IS the finding a journey is here to read.
    """
    question = os.environ.get(ASK_QUESTION_ENV)
    record = os.environ.get(ASK_RECORD_ENV)
    if not question or not record:
        return
    named = MEMBER_OF_CONFIG.search(config or "")
    if named is None or named.group(1) != DISPATCHED_MEMBER:
        return
    claimed = Path(record)
    try:
        # The claim is the file's creation and the answer is its content, so a reader
        # waits for the record to be non-empty rather than to exist: between the two is
        # the whole round trip this is here to make.
        claimed.touch(exist_ok=False)
    except FileExistsError:
        return
    wrapper = os.environ.get(ASK_MANAGER_ENV)
    if not wrapper:
        claimed.write_text(
            json.dumps({"wrapper": None, "status": None, "out": "", "err": ""}), encoding="utf-8"
        )
        return
    window = os.environ.get(ASK_TIMEOUT_ENV)
    asked = subprocess.run(  # noqa: S603 - the real wrapper, as a dispatched agent runs it
        [wrapper, *(["--timeout", window] if window else []), question],
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    claimed.write_text(
        json.dumps(
            {
                "wrapper": wrapper,
                "status": asked.returncode,
                "out": asked.stdout,
                "err": asked.stderr,
            }
        ),
        encoding="utf-8",
    )


def _author_plan(config: str | None) -> None:
    """Write the records a dispatched planner would have authored, once.

    Claimed by removing the instruction file, so a dispatch that reaches this branch
    repeatedly — the supervisor sends it back for more — authors the plan exactly once
    rather than rewriting it per turn.
    """
    instruction = os.environ.get(AUTHOR_PLAN_ENV)
    if not instruction:
        return
    named = MEMBER_OF_CONFIG.search(config or "")
    if named is None or named.group(1) != DISPATCHED_MEMBER:
        return
    claimed = Path(instruction)
    try:
        written = json.loads(claimed.read_text(encoding="utf-8"))
        claimed.unlink()
    except (OSError, json.JSONDecodeError):
        return
    for destination, content in written.items():
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _run_on_marker(config: str | None, prompt: str, cwd: str | None) -> None:
    """Run the commands a dispatched agent whose task carries a marker would have run.

    In the dispatch's own working directory — `--cwd` is what `oneharness` hands the
    provider, and this process's own is the member's scratch — so what a command reads
    there is what a real dispatch would have read: the repository it was given, and the
    configuration that repository tracks.

    A command that fails is reported on stderr and the turn still answers. The turn is
    not what a journey about this reads: what the commands did is, and a store that never
    received the write fails the read that was the point of the journey, naming what is
    missing rather than a harness turn's exit status.
    """
    instruction = os.environ.get(RUN_ON_MARKER_ENV)
    if not instruction:
        return
    named = MEMBER_OF_CONFIG.search(config or "")
    if named is None or named.group(1) != DISPATCHED_MEMBER:
        return
    try:
        keyed = json.loads(Path(instruction).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    for marker, commands in keyed.items():
        if marker not in prompt:
            continue
        for argv in commands:
            ran = subprocess.run(  # noqa: S603 - a real program, where the dispatch runs it
                argv,
                cwd=cwd or None,
                text=True,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                check=False,
            )
            if ran.returncode != 0:
                print(
                    f"fake_backend: {argv} exited {ran.returncode} in {cwd}\n"
                    f"{ran.stdout}{ran.stderr}",
                    file=sys.stderr,
                )


def _answer(argv: list[str], text: str, *, lost: bool = False) -> int:
    """Run the real CLI for this turn, with `text` as the provider's answer.

    `lost` has the provider process fail instead, which is how a turn is lost.
    """
    real = os.environ.get(REAL_BINARY_ENV)
    if not real:
        print(f"fake_backend: {REAL_BINARY_ENV} is not set", file=sys.stderr)
        return 2
    environment = dict(os.environ)
    environment[MOCK_STDOUT_ENV] = json.dumps({"result": text})
    if lost:
        environment[MOCK_EXIT_ENV] = "1"
    # `--mock-harness ID` replaces the selected harness's *provider process* and
    # nothing above it — this repository's one sanctioned fake, under a different
    # name. Proof: under `--mock-harness codex`
    # the run record still reports `harness_id: codex`, `available: true`, and the
    # real codex argv (`exec --dangerously-bypass-approvals-and-sandbox --json
    # <prompt>`) with only the executable substituted, so config resolution,
    # identity selection, the fallback chain, the history record and the report are
    # the real ones. It is the seam `tests/e2e/mock_oneharness.py` and
    # `tests/e2e/test_quota_fallthrough_e2e.py` already fake at.
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    completed = subprocess.run(
        [real, argv[0], "--harness", MOCK_HARNESS, "--mock-harness", MOCK_HARNESS, *argv[1:]],
        env=environment,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode


def _await_turn_gate(config: str | None) -> None:
    """Hold a dispatched worker's turn at `TURN_GATE_ENV`'s gate until it is released."""
    gate = os.environ.get(TURN_GATE_ENV)
    member = MEMBER_OF_CONFIG.search(config or "")
    if (
        not gate
        or config is None
        or member is None
        or member.group(1) != DISPATCHED_MEMBER
        or Path(config).name == JUDGE_CONFIG_NAME
    ):
        return
    released = Path(gate) / TURN_GATE_RELEASED
    if released.exists():
        return
    (Path(gate) / TURN_GATE_REACHED).touch()
    deadline = time.monotonic() + TURN_GATE_CEILING_SECONDS
    while not released.exists():
        if time.monotonic() > deadline:
            raise SystemExit("fake_backend: the turn gate was never released")
        time.sleep(0.05)


def main(argv: list[str]) -> int:
    """Answer one harness turn."""
    if not argv or argv[0] != "run":
        print(f"fake_backend: unsupported invocation {argv}", file=sys.stderr)
        return 2
    original = list(argv)
    argv, prompt = _prompt(argv)
    config = _flag(argv, CONFIG_FLAG)
    # First, before anything records the turn: that ordering is the gate's whole promise.
    _await_turn_gate(config)
    system = _flag(argv, SYSTEM_FLAG) or ""
    watching = MEMBER_OF_CONFIG.search(config or "")
    scripted = os.environ.get(OBSERVER_ANSWER_ENV)
    observing_member = os.environ.get(OBSERVER_MEMBER_ENV)
    observed = watching is not None and watching.group(1) == observing_member
    scripted_answer = scripted if scripted and observed else None
    loses = observed and bool(os.environ.get(OBSERVER_LOSES_ENV))
    if prompt_log := os.environ.get(PROMPT_LOG_ENV):
        named = [key for key in os.environ.get(ENVIRONMENT_KEYS_ENV, "").split(",") if key]
        with Path(prompt_log).open("a", encoding="utf-8") as recorded:
            turn: RecordedTurn = {
                "config": config,
                "cwd": _flag(argv, CWD_FLAG),
                "prompt": prompt,
                "system": system,
                "environment": {key: os.environ.get(key) for key in named},
                "scripted_answer": scripted_answer,
            }
            recorded.write(json.dumps(turn) + "\n")
    if config and Path(config).name == JUDGE_CONFIG_NAME:
        if EVALUATION_MARKER in prompt:
            return _answer(argv, json.dumps({"value": True, "reason": "the stand-in accepts"}))
        if os.environ.get(JUDGE_SEND_BACK_ENV) and prompt.count(WORKER_REPLY) < 2:
            return _answer(
                argv,
                # `completion: false` is usable only with the next instruction in
                # `message`; without one the supervisor is asked again and the sending
                # back never happens.
                json.dumps(
                    {
                        "completion": False,
                        "message": "Say what you did in one line, then report again.",
                        "reason": "the stand-in sends the worker back once",
                    }
                ),
            )
        return _answer(
            argv, json.dumps({"completion": True, "reason": "the stand-in accepts the work"})
        )
    # Before the single-sided branch below, and that ordering is the whole of what makes
    # this seam reach the member it names. `graphs/dag-scope.yaml`'s monitor is two-party
    # but its JUDGE side is a `command` — `onemessagebus serve --codec monitor` — so
    # `oneagentgraph` writes no judge harness config beside its agent one, and the "no judge
    # sibling" test below reads that member as single-sided. Answered there, every scripted
    # monitor answer was replaced by the pacemaker's report while the prompt log went on
    # recording the script, so a journey asserting on the log passed while the model said
    # something else entirely.
    if scripted_answer is not None or loses:
        return _answer(argv, scripted_answer or "", lost=loses)
    # Before the single-sided branch for the same reason as the scripted answer above.
    read = _read_the_stream(original, config, system, _flag(argv, CWD_FLAG))
    if read is not None:
        return _answer(argv, read)
    if config and not Path(config).with_name(JUDGE_CONFIG_NAME).exists():
        return _answer(argv, PACEMAKER_REPORT)
    _ask_manager(config)
    _author_plan(config)
    _run_on_marker(config, prompt, _flag(argv, CWD_FLAG))
    if held := os.environ.get(AGENT_DELAY_ENV):
        time.sleep(float(held))
    return _answer(argv, WORKER_REPLY)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
