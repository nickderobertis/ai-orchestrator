#!/usr/bin/env python3
"""Run the plan-store instruction the composed task hands the follow-up agent, and record it.

This is the one command in the follow-up recipe journey's turn whose subject is *which
program answers a store command the agent was told to run*. The agent's own commands are
not the journey's to invent here: the instruction is read out of the task the provider was
given a moment ago, and run exactly as that task spells it.

**Why the caller's own search path cannot answer this.** Every launch reaches the driver
through `scripts/onepipeline.sh`'s `exec uv run`, and `uv run` puts the launching
checkout's `.venv/bin` first on the search path of everything below it. So an older
program a caller placed ahead of it is never what the launched process tree resolves, and
an assertion made there passes against a tree that renders the store instruction as a bare
name — which is how the first guard written for this went green while guarding nothing.

**Where the resolution really happens.** A dispatched agent runs each command in a shell of
its own, whose search path its host re-derives, and on both hosts behind this defect an
older `onetaskgraph` sat ahead of the checkout's. `OLDER_PLAN_STORE_DIR` is put first here
for exactly that reason: it is that condition, applied where the agent resolves its
commands rather than where the launcher resolved its own. A task naming the program in
full survives it; a task naming `onetaskgraph` does not.

The witness is JSON so the journey reads facts rather than parses prose: the argv the task
asked for, what that argv's first word resolves to under that search path, and what the
program answered. Beside it, `<witness>.served` is the older program's own log, written
only if it is what ran: an empty or absent one is the reading that matters, because it is
the program saying it never served the command rather than a resolution rule saying it
would not have.

Stdlib-only and deterministic — this runs as a command of a real dispatched turn.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TypedDict


class Witness(TypedDict):
    """The record this writes, and the whole of what its reader in the journey asserts on.

    One shape whether the instruction ran or not: ``problem`` names why nothing was run
    and leaves every reading below it ``None``, so a reader never has to tell an absent
    key from an unanswered one. `tests/plan_tooling/test_follow_ups_recipe_e2e.py` is the
    reader, and the keys here are its contract.
    """

    problem: str | None
    command: list[str] | None
    resolved: str | None
    returncode: int | None
    stdout: str | None
    stderr: str | None


def _unanswered(problem: str) -> Witness:
    """The witness for a run that never reached the instruction, saying why."""
    return Witness(
        problem=problem, command=None, resolved=None, returncode=None, stdout=None, stderr=None
    )


def instruction(task: str, run: str) -> str | None:
    """The store command the task gives for listing ``run``'s drafts, as it spells it.

    This one is chosen because it is the first store command the task hands the agent and
    the only one whose whole argv the task fixes: everything after it takes an id the
    agent works out. The program word is what differs between a pinned task and an
    unpinned one, and it is the first word of every store instruction alike.
    """
    found = re.search(
        rf"`([^`\n]*?task list --source drafts --project {re.escape(run)} --json)`", task
    )
    return found[1] if found else None


def main() -> int:
    parsed = argparse.ArgumentParser(description=__doc__)
    parsed.add_argument("--prompt-log", type=Path, required=True)
    parsed.add_argument("--run", required=True)
    parsed.add_argument("--checkout", type=Path, required=True)
    parsed.add_argument("--witness", type=Path, required=True)
    arguments = parsed.parse_args()

    # Absolute from here on: the witness is named relative to the turn's own directory,
    # and the older program below is handed this path while running somewhere else, so a
    # relative one would put its log in whatever directory that program was run from.
    witness = arguments.witness.resolve()
    served = witness.with_name(witness.name + ".served")

    prompts = [
        json.loads(line)["prompt"]
        for line in arguments.prompt_log.read_text(encoding="utf-8").splitlines()
        if line
    ]
    spelled = instruction(prompts[-1], arguments.run) if prompts else None
    if not prompts:
        record = _unanswered(f"no prompt was recorded at {arguments.prompt_log}")
    elif spelled is None:
        record = _unanswered(
            "the task names no `<store> task list --source drafts --project "
            f"{arguments.run} --json` instruction to run"
        )
    else:
        command = shlex.split(spelled)
        older = os.environ["OLDER_PLAN_STORE_DIR"]
        environment = dict(os.environ)
        environment["PATH"] = older + os.pathsep + environment["PATH"]
        environment["OLDER_PLAN_STORE_LOG"] = str(served)
        ran = subprocess.run(  # noqa: S603 - the program the dispatched task itself names
            command,
            cwd=arguments.checkout,
            env=environment,
            text=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            check=False,
        )
        record = Witness(
            problem=None,
            command=command,
            resolved=shutil.which(command[0], path=environment["PATH"]),
            returncode=ran.returncode,
            stdout=ran.stdout,
            stderr=ran.stderr,
        )
    witness.write_text(json.dumps(record), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
