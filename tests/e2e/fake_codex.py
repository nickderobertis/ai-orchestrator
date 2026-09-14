#!/usr/bin/env python3
"""A stand-in for the paid codex CLI: this repository's one faked boundary.

oneharness runs the selected harness's own binary, so pointing
``ONEHARNESS_BIN_CODEX`` here replaces exactly the paid provider and nothing else.
The real oneharness still selects it, spawns it, parses its stream, times and
prices the turn, and writes the history record the launch contract is read back
out of — which is what a smoke journey has to keep real to mean anything.

Eight environment variables steer it, and each exists because a journey has to
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
* ``FAKE_CODEX_FAIL_AFTER_TURN`` — the launch emits its whole billed turn and
  *then* exits non-zero saying something no classifier recognizes. It is the
  counterpart of ``FAKE_CODEX_UNAVAILABLE_ATTEMPTS``: both leave a failure nothing
  can name, and they differ only in whether the provider has anything to show for
  itself, which is the one reading a chain publishes rather than derives.
* ``FAKE_CODEX_PROMPT_LOG`` names a file this appends one JSON record to per
  launch, carrying the prompt the provider was actually given. It is how a journey
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

Keep this deterministic and stdlib-only — this file *is* the provider binary.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Literal, NotRequired, TypedDict


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


#: What a launch answers when no journey scripted one.
DEFAULT_ANSWER = "smoke-ok"


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


def read_prompt(argv: list[str]) -> str | None:
    """The prompt this launch was given, or None when a journey asked for no reading of it.

    codex takes its prompt as the last positional word of `exec --json <prompt>`,
    which is the argv oneharness builds and `tests/e2e/test_orchestrate_launch_e2e.py`
    reads back; nothing is inferred from flags this stand-in does not implement. The
    one other spelling codex accepts is `-`, which says the prompt is on stdin — what
    oneharness hands over when a prompt outgrows a command line, as a plan reviewed
    whole does — so that word is read through rather than taken as the prompt. Read
    once, because stdin can be read once, and only when something reads the prompt.
    """
    wanted = ("FAKE_CODEX_PROMPT_LOG", "FAKE_CODEX_RUN_ON_MARKER")
    if not argv or not any(os.environ.get(name) for name in wanted):
        return None
    return sys.stdin.read() if argv[-1] == "-" else argv[-1]


def record_prompt(prompt: str | None) -> None:
    """Append the prompt this launch was given, when a journey asked for it."""
    log = os.environ.get("FAKE_CODEX_PROMPT_LOG")
    if log is None or prompt is None:
        return
    with Path(log).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"prompt": prompt}) + "\n")


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


def turn(launches: int | None) -> tuple[TurnEvent, ...]:
    """The stream this launch emits, with token accounting withheld on request."""
    return turn_events(answer(launches), billed=os.environ.get("FAKE_CODEX_OMIT_USAGE") != "1")


def hold() -> None:
    """Keep this turn in flight for as long as a journey asked, after recording it."""
    seconds = float(os.environ.get("FAKE_CODEX_HOLD_SECONDS") or "0")
    if seconds > 0:
        time.sleep(seconds)


def main() -> int:
    prompt = read_prompt(sys.argv[1:])
    record_prompt(prompt)
    launches = record_launch()
    run_on_marker(sys.argv[1:], prompt)
    hold()
    unavailable = int(os.environ.get("FAKE_CODEX_UNAVAILABLE_ATTEMPTS") or "0")
    if launches is not None and launches <= unavailable:
        print("fake_codex: the provider started and then failed", file=sys.stderr)
        return 1
    for event in turn(launches):
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
