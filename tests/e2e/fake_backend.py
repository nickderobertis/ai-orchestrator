#!/usr/bin/env python3
"""A deterministic stand-in for the paid model, at the seam a dispatch reaches it.

`ONEAGENTGRAPH_ONEHARNESS_BIN` points at this file, so every harness turn of a
launched run — the orchestrator that drives the rounds, its supervisor, each
dispatched worker, each worker's supervisor, and the check-in pacemaker — arrives
here as one `oneharness run` invocation. Everything above it stays real: the real
`just` recipe, the real `onepipeline` driver and engine verbs, the real
`oneagentgraph` graph configs in `graphs/`, and the real onejudge conversation
those two compose.

It stands in for the paid model and **only** the paid model: each invocation is
delegated to the real `oneharness` CLI with `--mock-harness`, so the real
argument validation, the real fallback chain, the real history record, and the
real report all run, with a scripted answer substituted for the provider's.
`MOCK_STDOUT` is how the shipped mock responder is told what to answer.

This replaced a `fake_backend.py` that sat at onejudge's `command`-provider seam.
That seam is no longer reachable for the side that does the work: a graph member's
agent side is an oneharness config (`oneagentgraph`'s graph schema has no command
agent), so the harness invocation is where a dispatch now meets the model.

Which side an invocation is depends on how `oneagentgraph` pinned it, which is the
same distinction `scripts/oneharness-agent.sh` reads:

* `--config .../oneharness.judge.toml` — a two-party member's supervisor. It
  answers the two JSON shapes onejudge asks it for: the supervisor verdict, and
  the `done_when` evaluation.
* any other `--config` — a single-sided `kind: oneharness` member (the check-in
  pacemaker), which has its config named on its own command line.
* no `--config` at all — the agent side, pinned by the `oneharness.toml`
  `oneagentgraph` wrote into the member's scratch and discovered from there.

The agent side is scripted by what it is asked to do. The orchestrator's task
names the run it must drive, so this drives it for real — `onepipeline round run`
and `onepipeline round next` until the run reports itself complete — which is
exactly what the paid orchestrator would do and what makes a launched plan settle.
Any other agent turn is a dispatched worker, which reports without changing
anything.
"""

# llmlint: ignore-file[boundary_inputs_validated] this deterministic test backend
# consumes one command line written by oneagentgraph and validates every field it
# reads from it below; an invocation it cannot classify fails closed.

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

#: The real CLI every invocation is delegated to. Named rather than discovered:
#: this file *is* `oneharness` as far as the run is concerned, so resolving the
#: name again would re-enter this script.
REAL_BINARY_ENV = "REAL_ONEHARNESS_BIN"

#: The one identity every delegated turn is pinned to, and mocked at.
#:
#: Both flags, and neither is optional. `--mock-harness ID` replaces the provider
#: process of **that exact identity** and no other — not `ID:variant`, and not the
#: rest of a `run_mode = "fallback"` chain — so mocking one candidate of a chain
#: that names five leaves the other four able to run for real. They can and did:
#: a suite run whose `claude-code:alternate` had quota spent twenty minutes of a
#: paid subscription exploring this checkout before the stall watchdog killed it.
#: `--harness` is what makes the mock total, by leaving exactly one candidate.
MOCK_HARNESS = "codex"

#: How the shipped mock responder is told what to answer.
MOCK_STDOUT_ENV = "MOCK_STDOUT"

#: The judge side's config file name, written by `oneagentgraph` into the
#: member's scratch. The agent side's is `oneharness.toml`, and it is not named on
#: the command line at all.
JUDGE_CONFIG_NAME = "oneharness.judge.toml"

#: The task `onepipeline` gives the orchestrator member, which names the run.
DRIVE_TASK = re.compile(r"Drive run (\S+) to settlement")

#: The fragment onejudge's `done_when` evaluation prompt ends with. The supervisor
#: turn and the evaluation turn reach the same config, and they want different
#: JSON, so the prompt is what tells them apart.
EVALUATION_MARKER = '{"value": true or false'

#: How many round transitions the scripted orchestrator will drive before giving
#: up. A bound rather than a loop: a run that never reports itself complete is a
#: failure to report, and a test that hangs on it says nothing.
MAX_TRANSITIONS = 12


def _config(argv: list[str]) -> str | None:
    """The `--config` this invocation was pinned with, if any."""
    for index, argument in enumerate(argv):
        if argument == "--config" and index + 1 < len(argv):
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


def _answer(argv: list[str], text: str) -> int:
    """Run the real CLI for this turn, with `text` as the provider's answer."""
    real = os.environ.get(REAL_BINARY_ENV)
    if not real:
        print(f"fake_backend: {REAL_BINARY_ENV} is not set", file=sys.stderr)
        return 2
    environment = dict(os.environ)
    environment[MOCK_STDOUT_ENV] = json.dumps({"result": text})
    completed = subprocess.run(
        [real, argv[0], "--harness", MOCK_HARNESS, "--mock-harness", MOCK_HARNESS, *argv[1:]],
        env=environment,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode


def _drive(run: str) -> str:
    """Drive a launched run to settlement the way the paid orchestrator would."""
    reported = []
    for _ in range(MAX_TRANSITIONS):
        executed = subprocess.run(
            ["onepipeline", "round", "run", run], capture_output=True, text=True, check=False
        )
        reported.append(f"round run: exit {executed.returncode} {executed.stdout.strip()}")
        transitioned = subprocess.run(
            ["onepipeline", "round", "next", run], capture_output=True, text=True, check=False
        )
        reported.append(f"round next: exit {transitioned.returncode} {transitioned.stdout.strip()}")
        if transitioned.returncode != 0:
            break
        try:
            state = json.loads(transitioned.stdout).get("state")
        except json.JSONDecodeError:
            break
        if state != "continuing":
            break
    return " | ".join(reported)


def main(argv: list[str]) -> int:
    """Answer one harness turn."""
    if not argv or argv[0] != "run":
        print(f"fake_backend: unsupported invocation {argv}", file=sys.stderr)
        return 2
    argv, prompt = _prompt(argv)
    config = _config(argv)
    if config and Path(config).name == JUDGE_CONFIG_NAME:
        if EVALUATION_MARKER in prompt:
            return _answer(argv, json.dumps({"value": True, "reason": "the stand-in accepts"}))
        return _answer(
            argv, json.dumps({"completion": True, "reason": "the stand-in accepts the work"})
        )
    if config:
        return _answer(argv, "the stand-in pacemaker reported")
    driving = DRIVE_TASK.search(prompt)
    if driving:
        return _answer(argv, _drive(driving.group(1)))
    return _answer(argv, "the stand-in worker reported without changing anything")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
