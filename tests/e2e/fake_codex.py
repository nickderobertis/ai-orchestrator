#!/usr/bin/env python3
"""A stand-in for the paid codex CLI: this repository's one faked boundary.

oneharness runs the selected harness's own binary, so pointing
``ONEHARNESS_BIN_CODEX`` here replaces exactly the paid provider and nothing else.
The real oneharness still selects it, spawns it, parses its stream, times and
prices the turn, and writes the history record the launch contract is read back
out of — which is what a smoke journey has to keep real to mean anything.

Nine environment variables steer it, and each exists because a journey has to
tell one outcome from another deterministically:

* ``FAKE_CODEX_ATTEMPT_LOG`` names a file this appends one line to per launch,
  which is how a journey counts the turns a chain actually spent.
* ``FAKE_CODEX_UNAVAILABLE_ATTEMPTS`` — the first N launches die the way a
  contended host made the real provider die: started, and then failed. *Which*
  turn contention kills is not something generating load can decide, so the count
  is what makes "the first launch failed and a later one did not" deterministic
  while every other party stays real.
* ``FAKE_CODEX_OMIT_USAGE`` — the launch succeeds and returns a turn carrying no
  token accounting, which is a broken recorded contract rather than weather.
* ``FAKE_CODEX_ANSWERS`` — a JSON array of the answers to give, one per launch,
  the last repeating once they run out. It exists for a **structured** run: when
  oneharness carries a schema it validates this text against it and re-prompts on
  failure, so telling one launch from the next by its answer is the only way a
  journey can watch that retry happen. Absent, every launch answers ``smoke-ok``.
* ``FAKE_CODEX_HOLD_SECONDS`` — the launch records itself, then holds that long
  before answering. It exists so a journey can interrupt a turn that is provably
  *in flight*: signalling a caller and hoping the turn had started is a race whose
  failure mode is a green test, and the attempt log is the only moment a journey can
  prove the provider was reached. Absent, a launch answers immediately.
  ``FAKE_CODEX_HOLD_RELEASE`` beside it names a file whose existence ends the hold
  early, so a journey holds the turn until it says so and the seconds are only a bound
  for a journey that died; ``FAKE_CODEX_HOLDING`` names a file the held launch writes
  its pid to before it holds, which is how a journey reads that the turn is still in
  flight — its process alive — at a moment of its choosing.
* ``FAKE_CODEX_FAIL_AFTER_TURN`` — the launch emits its whole billed turn and
  *then* exits non-zero saying something no classifier recognizes. It is the
  counterpart of ``FAKE_CODEX_UNAVAILABLE_ATTEMPTS``: both leave a failure nothing
  can name, and they differ only in whether the provider has anything to show for
  itself, which is the one reading a chain publishes rather than derives.
* ``FAKE_CODEX_PROMPT_LOG`` names a file this appends one JSON record to per
  launch, carrying the prompt the provider was actually given, the argv it was
  spawned with — which is where the approval mode a role file asked for arrives, as
  codex's own ``--sandbox`` — and every file the launch opened under
  ``FAKE_CODEX_READ_LISTED_FOR``. It is how a journey
  reads the prompt of a turn nothing else can observe: since oneagentgraph 0.2.18 a
  single-sided ``kind: oneharness`` member's turn is an in-process
  ``oneharness_core`` call rather than a spawned CLI, so
  ``ONEAGENTGRAPH_ONEHARNESS_BIN`` — and with it ``tests/e2e/fake_backend.py`` — is
  not on that member's path at all. The provider binary is, and this is it. Both
  single-sided members here take that path: the ``check-in`` pacemaker and
  ``graphs/pr-author.yaml``'s drafter.
* ``FAKE_CODEX_RUN_ON_MARKER`` names a JSON file mapping a marker to the argument
  vectors a turn whose prompt carries that marker runs, in order, where the turn runs.
  It is ``tests/e2e/fake_backend.py``'s ``FAKE_BACKEND_RUN_ON_MARKER`` at the one seam a
  single-sided member still reaches: ``graphs/follow-up.yaml``'s follow-up agent is
  such a member, and what that agent *does* is run programs — write a ticket, validate
  it, copy it onto a board — so this substitutes the model's choice of commands and
  nothing below it. The programs are the real ones and every record they leave is theirs.
  ``FAKE_CODEX_RUN_ON_MARKER_LOG`` beside it names a file each command's argv, exit status
  and output are appended to, one JSON line apiece: this process's own stderr reaches no
  record a journey can read, so that file is how a failed command explains itself.

* ``FAKE_CODEX_READ_LISTED_FOR`` names a marker, and makes a launch whose prompt lists
  task files — the plan-level review's ``task file: <path>`` lines, each under its
  ``### Node `<id>``` heading — do what a reviewer reading through those paths does:
  open each listed file from inside the turn, and, where a line of one carries the
  marker, answer a refusal whose finding names that node and quotes the line. It is
  how a journey proves the listed path is real, readable in the turn, and carries the
  detail the prompt left out — the model's choice of which file to open is the one
  thing substituted. A prompt listing no task file, or files carrying no marker, is
  answered as any other launch is.

* ``FAKE_CODEX_LLMLINT_FAIL`` scripts the one launch that is not a reviewer's: an
  llmlint judge batch, recognized by the ``## Target files`` and ``## Rules to evaluate``
  sections llmlint's prompt carries, is answered in the shape llmlint's schema asks for,
  every rule the batch lists holding — except each rule this JSON object names, mapped to
  the ``file``, ``line`` and ``message`` of the violation it is answered with. A violation
  is reported only in a file the batch lists and the rule's ``Scope:`` line covers, as a
  model reading the prompt would. The batch is answered outside the scripted launches:
  it is not counted in ``FAKE_CODEX_ATTEMPT_LOG``, takes no ``FAKE_CODEX_ANSWERS`` entry,
  and is recorded in ``FAKE_CODEX_LLMLINT_PROMPT_LOG`` rather than ``FAKE_CODEX_PROMPT_LOG``,
  so a journey scripting or reading a reviewer's turns in order is not shifted by an
  llmlint judge running beside it.

Keep this deterministic and stdlib-only — this file *is* the provider binary.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Literal, NamedTuple, NotRequired, TypedDict


class AgentMessage(TypedDict):
    """The one item this provider completes: the turn's answer text."""

    type: Literal["agent_message"]
    text: str


class Usage(TypedDict):
    """A turn's token accounting, which is the evidence it was billed."""

    input_tokens: int
    cached_input_tokens: int
    output_tokens: int


class TurnEvent(TypedDict):
    """One line of the codex event stream, as this provider emits it.

    One shape rather than a union per `type`, because the four events this file
    emits differ only by which optional field they carry, and a reader here reads
    `type` first either way. `FAKE_CODEX_OMIT_USAGE` drops `usage` from the whole
    stream, which is why it is optional at all.
    """

    type: Literal["turn.started", "thread.started", "item.completed", "turn.completed"]
    thread_id: NotRequired[str]
    item: NotRequired[AgentMessage]
    usage: NotRequired[Usage]


class PromptRecord(TypedDict):
    """One launch, as ``FAKE_CODEX_PROMPT_LOG`` records it: what it was given and opened."""

    prompt: str
    argv: list[str]
    read: list[str]


class Finding(TypedDict):
    """One criterion a refusal names, in the verdict schema's two fields."""

    criterion: str
    why: str


class Refusal(TypedDict):
    """The verdict a launch answers when the files it opened carried the marker."""

    passes: Literal[False]
    findings: list[Finding]


#: What a launch answers when no journey scripted one.
DEFAULT_ANSWER = "smoke-ok"

#: The options codex reads a configuration override from, each taking the next word;
#: `tests/test_command_ceiling_providers.py` holds both to the installed codex's parser.
CONFIG_OPTIONS = ("-c", "--config")


def answer(launches: int | None) -> str:
    """The text this launch returns, from the scripted answers if a journey set any.

    `launches` is 1-based and None when nothing is counting them, which is the same
    case as an unscripted run: there is exactly one answer to give.
    """
    scripted = os.environ.get("FAKE_CODEX_ANSWERS")
    if not scripted:
        return DEFAULT_ANSWER
    answers = json.loads(scripted)
    if not answers:
        return DEFAULT_ANSWER
    # Past the end the last answer repeats, so a journey scripts only the launches
    # whose answers differ and lets the settled one stand for every later attempt.
    return str(answers[min((launches or 1) - 1, len(answers) - 1)])


def turn_events(text: str, *, billed: bool) -> tuple[TurnEvent, ...]:
    """One complete codex-shaped turn carrying that answer.

    Token accounting is not decoration here: a record persisted without it carries
    no evidence the turn was ever billed, which is what `billed=False` produces.
    """
    completed = TurnEvent(type="turn.completed")
    if billed:
        completed["usage"] = Usage(input_tokens=4, cached_input_tokens=0, output_tokens=1)
    return (
        TurnEvent(type="turn.started"),
        TurnEvent(type="thread.started", thread_id="fake-codex-thread"),
        TurnEvent(type="item.completed", item=AgentMessage(type="agent_message", text=text)),
        completed,
    )


def record_launch() -> int | None:
    """Append this launch to the attempt log, returning how many it now holds."""
    log = os.environ.get("FAKE_CODEX_ATTEMPT_LOG")
    if log is None:
        return None
    path = Path(log)
    with path.open("a", encoding="utf-8") as stream:
        stream.write("launch\n")
    return len(path.read_text(encoding="utf-8").splitlines())


#: The codex options oneharness passes that take a value as the next word. A role's own
#: `[harness.codex] args` are appended *after* the prompt — `exec --json <prompt> -c
#: features.apps=false` — and codex accepts an option on either side of its positional,
#: so the prompt is the last word that is neither an option nor an option's value.
#: `tests/e2e/test_fake_codex_reads_each_roles_prompt_e2e.py` holds this set to the argv the
#: pinned oneharness builds for every role here, so an option it starts passing fails there.
VALUED_OPTIONS = frozenset(
    (*CONFIG_OPTIONS, "-m", "--model", "-s", "--sandbox", "-C", "--cd", "-p", "--profile")
)


def read_prompt(argv: list[str]) -> str | None:
    """The prompt this launch was given, or None when a journey asked for no reading of it.

    codex takes its prompt as the last positional word of `exec --json <prompt>`,
    which is the argv oneharness builds and `tests/e2e/test_orchestrate_launch_e2e.py`
    reads back, followed by any `args` the role's config appends; each option in
    `VALUED_OPTIONS` is skipped with its value. The one other spelling codex accepts
    is `-`, which says the prompt is on stdin — what oneharness hands over when a
    prompt outgrows a command line, as a plan reviewed whole does — so that word is
    read through rather than taken as the prompt. Read once, because stdin can be read
    once, and always, because an llmlint judge batch is told apart by what it asks.
    """
    if not argv:
        return None
    positional: list[str] = []
    words = iter(argv)
    for word in words:
        if word in VALUED_OPTIONS:
            next(words, None)
        elif word == "-" or not word.startswith("-"):
            positional.append(word)
    if not positional:
        return None
    return sys.stdin.read() if positional[-1] == "-" else positional[-1]


def record_prompt(
    prompt: str | None, argv: list[str], read: list[str], *, log: str | None = None
) -> None:
    """Append the prompt this launch was given, its argv and what it opened, when asked.

    Into ``log`` when one is named, and otherwise into ``FAKE_CODEX_PROMPT_LOG``.
    """
    log = log if log is not None else os.environ.get("FAKE_CODEX_PROMPT_LOG")
    if log is None or prompt is None:
        return
    with Path(log).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(PromptRecord(prompt=prompt, argv=argv, read=read)) + "\n")


#: How a plan-level prompt lists each node and the task file it may open.
NODE_HEADING = "### Node `"
TASK_FILE = "task file: "


def read_listed(prompt: str | None) -> tuple[list[str], list[Finding]]:
    """Open each task file ``prompt`` lists, and quote every line carrying the marker.

    Answers the paths opened, in the order listed, and one finding per marked line, naming
    the node the path was listed under. Nothing is opened unless a journey named a marker.
    """
    marker = os.environ.get("FAKE_CODEX_READ_LISTED_FOR")
    if not marker or prompt is None:
        return [], []
    read: list[str] = []
    findings: list[Finding] = []
    node = ""
    for line in prompt.splitlines():
        if line.startswith(NODE_HEADING):
            node = line.removeprefix(NODE_HEADING).split("`", 1)[0]
        elif line.startswith(TASK_FILE):
            path = Path(line.removeprefix(TASK_FILE).strip())
            if not path.is_absolute() or not path.is_file():
                continue
            read.append(str(path))
            for held in path.read_text(encoding="utf-8").splitlines():
                if marker in held:
                    findings.append(
                        {"criterion": node, "why": f"its task file reads: {held.strip()}"}
                    )
    return read, findings


def run_on_marker(argv: list[str], prompt: str | None) -> None:
    """Run the commands a turn whose prompt carries a scripted marker would have run.

    Where the turn runs: the directory codex is told with `--cd`/`-C` when oneharness names
    one, and this process's own working directory otherwise. A command that fails is
    reported on stderr and the turn still answers, because what a journey reads is what
    the commands did, never this turn's exit status.
    """
    instruction = os.environ.get("FAKE_CODEX_RUN_ON_MARKER")
    if not instruction or prompt is None:
        return
    keyed = json.loads(Path(instruction).read_text(encoding="utf-8"))
    cwd = next(
        (argv[at + 1] for at, word in enumerate(argv[:-1]) if word in ("-C", "--cd")),
        None,
    )
    for marker, commands in keyed.items():
        if marker not in prompt:
            continue
        for command in commands:
            ran = subprocess.run(  # noqa: S603 - a real program, where the turn runs it
                command,
                cwd=cwd,
                text=True,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                check=False,
            )
            if ran.returncode != 0:
                print(
                    f"fake_codex: {command} exited {ran.returncode}\n{ran.stdout}{ran.stderr}",
                    file=sys.stderr,
                )
            if log := os.environ.get("FAKE_CODEX_RUN_ON_MARKER_LOG"):
                with Path(log).open("a", encoding="utf-8") as stream:
                    stream.write(
                        json.dumps(
                            {
                                "command": command,
                                "cwd": cwd or os.getcwd(),
                                "status": ran.returncode,
                                "stdout": ran.stdout,
                                "stderr": ran.stderr,
                            }
                        )
                        + "\n"
                    )


def turn(launches: int | None, findings: list[Finding]) -> tuple[TurnEvent, ...]:
    """The stream this launch emits, with token accounting withheld on request.

    A launch that found marked lines in the files it opened refuses quoting them, which
    is the one answer its reading decides; every other launch answers as scripted.
    """
    text = json.dumps({"passes": False, "findings": findings}) if findings else answer(launches)
    return turn_events(text, billed=os.environ.get("FAKE_CODEX_OMIT_USAGE") != "1")


def hold() -> None:
    """Keep this turn in flight for as long as a journey asked, after recording it.

    Until the release file a journey names exists, when it names one, and for the hold's
    seconds at most either way.
    """
    seconds = float(os.environ.get("FAKE_CODEX_HOLD_SECONDS") or "0")
    if seconds <= 0:
        return
    holding = os.environ.get("FAKE_CODEX_HOLDING")
    if holding:
        Path(holding).write_text(f"{os.getpid()}\n", encoding="utf-8")
    release = os.environ.get("FAKE_CODEX_HOLD_RELEASE")
    if not release:
        time.sleep(seconds)
        return
    ends = time.monotonic() + seconds
    while not Path(release).exists() and time.monotonic() < ends:
        time.sleep(0.1)


#: The sections of llmlint's judge prompt that say a launch is one of its batches, and the
#: lines that name a batch's files and each rule's scope.
LLMLINT_TARGETS = "## Target files"
LLMLINT_RULES = "## Rules to evaluate"
LLMLINT_RULE = "### "
LLMLINT_SCOPE = "Scope: "
LLMLINT_ALL = "all target files"
LLMLINT_EXCEPT = "all target files except: "
LLMLINT_RELEVANCE = "Relevant only when:"


class BatchRule(NamedTuple):
    """One rule of an llmlint batch: where it applies and whether it asks for relevance.

    ``conditional`` is whether the rule carries a relevance condition, which decides
    whether llmlint's schema asks for a ``relevant`` field on its answer.
    """

    scope: str
    conditional: bool


class Batch(NamedTuple):
    """One llmlint judge batch: the files it names and each rule it asks about."""

    targets: list[str]
    rules: dict[str, BatchRule]


class Violation(TypedDict):
    """One violation of a rule, in the fields llmlint's schema asks for."""

    file: str
    line: int
    message: str


class RuleVerdict(TypedDict):
    """One rule's answer in llmlint's batch schema; `relevant` only for a conditional rule."""

    name: str
    rationale: str
    relevant: NotRequired[bool]
    holds: bool
    violations: list[Violation]


def llmlint_batch(prompt: str | None) -> Batch | None:
    """The files and rules of an llmlint judge batch, or None when ``prompt`` is not one."""
    if prompt is None or LLMLINT_TARGETS not in prompt or LLMLINT_RULES not in prompt:
        return None
    targets_part = prompt.split(LLMLINT_TARGETS, 1)[1].split(LLMLINT_RULES, 1)[0]
    targets = [
        line.removeprefix("- ").strip()
        for line in targets_part.splitlines()
        if line.startswith("- ")
    ]
    rules: dict[str, BatchRule] = {}
    name = ""
    for line in prompt.split(LLMLINT_RULES, 1)[1].splitlines():
        match line:
            case _ if line.startswith(LLMLINT_RULE):
                name = line.removeprefix(LLMLINT_RULE).strip()
                rules[name] = BatchRule(scope=LLMLINT_ALL, conditional=False)
            case _ if name and line.startswith(LLMLINT_SCOPE):
                rules[name] = rules[name]._replace(scope=line.removeprefix(LLMLINT_SCOPE).strip())
            case _ if name and line.startswith(LLMLINT_RELEVANCE):
                rules[name] = rules[name]._replace(conditional=True)
            case _ if line.startswith("## "):
                break
    return Batch(targets, rules)


def in_scope(path: str, scope: str, targets: list[str]) -> bool:
    """Whether a rule whose ``Scope:`` line reads ``scope`` applies to ``path``."""
    if path not in targets:
        return False
    if scope == LLMLINT_ALL:
        return True
    if scope.startswith(LLMLINT_EXCEPT):
        return path not in [part.strip() for part in scope.removeprefix(LLMLINT_EXCEPT).split(",")]
    return path in [part.strip() for part in scope.split(",")]


def llmlint_verdict(batch: Batch) -> str:
    """The answer to one llmlint batch: each rule holds unless scripted to fail in scope."""
    failing = json.loads(os.environ.get("FAKE_CODEX_LLMLINT_FAIL") or "{}")
    answered: dict[str, RuleVerdict] = {}
    for name, rule in batch.rules.items():
        scripted = failing.get(name)
        violations: list[Violation] = (
            [
                Violation(
                    file=str(scripted["file"]),
                    line=int(scripted.get("line", 1)),
                    message=str(scripted["message"]),
                )
            ]
            if isinstance(scripted, dict)
            and in_scope(str(scripted["file"]), rule.scope, batch.targets)
            else []
        )
        verdict = RuleVerdict(
            name=name,
            rationale=f"fake_codex: {name} {'fails' if violations else 'holds'} as scripted",
            holds=not violations,
            violations=violations,
        )
        if rule.conditional:
            verdict["relevant"] = True
        answered[name] = verdict
    return json.dumps(answered)


def main() -> int:
    prompt = read_prompt(sys.argv[1:])
    batch = llmlint_batch(prompt)
    if batch is not None:
        # Recorded only where a journey asks for llmlint's batches, and never beside the
        # reviewer's turns, which journeys read back in order.
        if llmlint_log := os.environ.get("FAKE_CODEX_LLMLINT_PROMPT_LOG"):
            record_prompt(prompt, sys.argv[1:], [], log=llmlint_log)
        for event in turn_events(llmlint_verdict(batch), billed=True):
            print(json.dumps(event), flush=True)
        return 0
    read, findings = read_listed(prompt)
    record_prompt(prompt, sys.argv[1:], read)
    launches = record_launch()
    run_on_marker(sys.argv[1:], prompt)
    hold()
    unavailable = int(os.environ.get("FAKE_CODEX_UNAVAILABLE_ATTEMPTS") or "0")
    if launches is not None and launches <= unavailable:
        print("fake_codex: the provider started and then failed", file=sys.stderr)
        return 1
    for event in turn(launches, findings):
        print(json.dumps(event), flush=True)
    if os.environ.get("FAKE_CODEX_FAIL_AFTER_TURN") == "1":
        # The turn above is complete and accounted for, so this exit is a failure
        # with work behind it — the case a chain must never spend another
        # identity's quota on, whatever the exit code fails to explain.
        print("fake_codex: the provider answered and then failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
