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


def instruction(
    task: str, *, run: str | None = None, board: str | None = None, search: str | None = None
) -> str | None:
    """A listing the task spells whole, as it spells it: ``run``'s drafts, or one of the board's.

    The drafts listing is chosen for the pin because it is the first store command the
    task hands the agent and the only one whose whole argv the task fixes: everything
    after it takes an id the agent works out. The program word is what differs between a
    pinned task and an unpinned one, and it is the first word of every store instruction
    alike. The two board listings are the other whole argvs the task fixes, both through
    the module's own `board-items` command, which reads every page the store answers:
    the accepted listing — the one the agent reads the board's accepted tickets with, its
    `--status` flags rendered from the module's own vocabulary — and, with ``search``, the
    duplicate search by text, whose `<text>` the task leaves to the agent and this fills
    in. Running each as spelled is how a journey reads what that step selects.
    """
    if run is not None:
        found = re.search(
            rf"`([^`\n]*?task list --source drafts --project {re.escape(run)} --json)`", task
        )
    elif search is not None:
        found = re.search(
            rf"`([^`\n]*?board-items --board {re.escape(board or '')} --search) <text>`", task
        )
        return f"{found[1]} {shlex.quote(search)}" if found else None
    else:
        found = re.search(
            rf"`([^`\n]*?board-items --board {re.escape(board or '')}(?: --status [a-z-]+)+)`",
            task,
        )
    return found[1] if found else None


def _paged(
    command: list[str], checkout: Path, environment: dict[str, str], *, paged: bool
) -> subprocess.CompletedProcess[str]:
    """Run ``command``, and for the drafts listing every further page the task says to list.

    The drafts listing is the store's own `task list`, one page, and the task tells the agent
    to list again with `--page <cursor>` while it answers a `next` cursor; this does exactly
    that, and answers one run whose stdout carries every page's items under `items` and
    `pages`, the number of pages read. The board listings are the module's `board-items`,
    which pages itself, so they run once as spelled.
    """
    items: list[object] = []
    followed: set[str] = set()
    cursor: str | None = None
    for pages in range(1, 100):
        ran = subprocess.run(  # noqa: S603 - the program the dispatched task itself names
            [*command, *(["--page", cursor] if cursor is not None else [])],
            cwd=checkout,
            env=environment,
            text=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            check=False,
        )
        if not paged or ran.returncode != 0:
            return ran
        answered = json.loads(ran.stdout)
        items.extend(answered["items"])
        cursor = answered.get("next")
        if cursor is None:
            return subprocess.CompletedProcess(
                ran.args, 0, json.dumps({"items": items, "pages": pages}), ran.stderr
            )
        if cursor in followed:
            return subprocess.CompletedProcess(
                ran.args, 1, "", f"the store answered the cursor {cursor!r} twice"
            )
        followed.add(cursor)
    return subprocess.CompletedProcess(command, 1, "", "the drafts listing never ended")


def main() -> int:
    parsed = argparse.ArgumentParser(description=__doc__)
    parsed.add_argument("--prompt-log", type=Path, required=True)
    which = parsed.add_mutually_exclusive_group(required=True)
    which.add_argument("--run", help="run the listing of this run's drafts")
    which.add_argument("--board", help="run the listing of this board's accepted items")
    parsed.add_argument(
        "--search", help="with --board: run the board's duplicate search for this text instead"
    )
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
    spelled = (
        instruction(prompts[-1], run=arguments.run, board=arguments.board, search=arguments.search)
        if prompts
        else None
    )
    if not prompts:
        record = _unanswered(f"no prompt was recorded at {arguments.prompt_log}")
    elif spelled is None:
        if arguments.run is not None:
            wanted = f"<store> task list --source drafts --project {arguments.run} --json"
        elif arguments.search is not None:
            wanted = f"<board-items> --board {arguments.board} --search <text>"
        else:
            wanted = f"<board-items> --board {arguments.board} --status <accepted>…"
        record = _unanswered(f"the task names no `{wanted}` instruction")
    else:
        command = shlex.split(spelled)
        older = os.environ["OLDER_PLAN_STORE_DIR"]
        environment = dict(os.environ)
        environment["PATH"] = older + os.pathsep + environment["PATH"]
        environment["OLDER_PLAN_STORE_LOG"] = str(served)
        ran = _paged(command, arguments.checkout, environment, paged=arguments.run is not None)
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
