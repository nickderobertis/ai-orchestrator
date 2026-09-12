"""What `scripts/draft-pr-body.sh` borrows from `onepipeline`, and where it declares it.

`onepipeline` composes the task its `--pr-author-graph` runs under, and that task
opens with one sentence naming what the answer is for. `scripts/draft-pr-body.sh`
composes the same task out of band — for a branch no run is publishing — so it has to
open with that same sentence, or the drafter is answering a different question than
the run path's and `graphs/pr-author.yaml`'s member is the only thing between the two.

The same goes for the three **endings** it reports when no body was drafted:
`dispatch-failed`, `schema-refused`, and `no-body` are `onepipeline`'s names for what
went wrong, and an operator reads one of them to know whether to fix the graph, the
schema, or the drafting persona's prose. A name this script invented on its own would
send them looking through `onepipeline`'s documentation for a word it never uses.

The same goes for the two **sections** `graphs/pr-author.yaml`'s member reads its
starting point from: `## Change request`, under which the engine puts the change request
the session already holds and the description as the worker left it, and
`## Worker transcript`, under which it names the command that renders every tool call
the worker made. Those headings are the engine's literals — the script composes
neither, having no run to read a transcript from and no change request to read — and the
prompt relies on them by name, so a heading the engine reworded would leave the drafter
looking for a section the task no longer opens.

All three are copies of somebody else's contract, and a copy drifts in silence. So each
is written in exactly one place — the script for what it composes, the graph for what it
reads — and this module is how everything else reads them back rather than restating
them. `tests/test_drafting_task_contract.py` is the other half: it holds those
declarations against the pinned `onepipeline` that owns the originals.
"""

from __future__ import annotations

import re
from functools import cache

from orchestrator.root import REPO_ROOT

#: The script that composes the out-of-band drafting task, and so declares the
#: sentence it opens with.
DRAFTER = REPO_ROOT / "scripts" / "draft-pr-body.sh"

#: That declaration, as the script writes it: one double-quoted shell assignment on
#: one line. Anchored to the start of a line so the same name inside a comment or a
#: diagnostic cannot be mistaken for it.
DECLARATION = re.compile(r'^ONEPIPELINE_OPENING="(?P<sentence>[^"]+)"$', re.MULTILINE)


@cache
def _drafter() -> str:
    """The script's source, read once for every declaration read out of it."""
    return DRAFTER.read_text(encoding="utf-8")


#: One ending name, as the script declares it: an upper-case constant assigned a
#: hyphenated lower-case word on a line of its own. The hyphen is what separates these
#: from the sentences beside them, which are prose with spaces in.
ENDING = re.compile(r'^[A-Z_]+_ENDING = "(?P<ending>[a-z]+(?:-[a-z]+)+)"$', re.MULTILINE)

#: How many endings `onepipeline` distinguishes, and so how many the script must
#: declare. Stated because a reader that silently found two would gate two.
ENDING_COUNT = 3

#: The drafting graph, whose one member's prompt names the sections it starts from.
DRAFTING_GRAPH = REPO_ROOT / "graphs" / "pr-author.yaml"

#: A section heading that prompt relies on, as it writes one: a backticked `## …`
#: heading. Only the level-two headings the *engine* composes, so the `## What` and
#: `## Why` the prompt tells the drafter to write are read out of the body template's
#: vocabulary rather than gated against the engine.
SECTION = re.compile(r"`(?P<heading>## [A-Z][a-z]+(?: [a-z]+)*)`")

#: The headings that name what the drafter starts from, rather than what it writes.
#: Stated because a reader that gated `## What` against the engine would be holding
#: the pull request template's words to a binary that never composes them.
STARTING_POINT_HEADINGS = ("## Change request", "## Worker transcript")


@cache
def drafting_starting_points() -> tuple[str, ...]:
    """The composed-task sections the drafter *starts from*, as its member names them.

    Only those two: the sections the drafter writes are the pull request template's
    words, which the engine never composes.
    """
    prompt = DRAFTING_GRAPH.read_text(encoding="utf-8")
    named = tuple(
        match.group("heading")
        for match in SECTION.finditer(prompt)
        if match.group("heading") in STARTING_POINT_HEADINGS
    )
    assert set(named) == set(STARTING_POINT_HEADINGS), (
        f"{DRAFTING_GRAPH.name} names {sorted(set(named))} of the composed task's sections; "
        f"its member reads its starting point from {STARTING_POINT_HEADINGS}, each named in "
        "backticks, or move this reader with it"
    )
    return tuple(dict.fromkeys(named))


@cache
def drafting_endings() -> tuple[str, ...]:
    """Every ending name `scripts/draft-pr-body.sh` reports a bodyless run with."""
    declared = tuple(match.group("ending") for match in ENDING.finditer(_drafter()))
    assert len(declared) == ENDING_COUNT, (
        f"{DRAFTER.name} declares {len(declared)} ending name(s) — {declared} — where "
        f'{ENDING_COUNT} were expected; keep each on one line as `<NAME>_ENDING = "…"` '
        "or move this reader with them"
    )
    return declared


@cache
def onepipeline_opening() -> str:
    """The sentence `scripts/draft-pr-body.sh` opens its composed task with."""
    declared = DECLARATION.search(_drafter())
    assert declared is not None, (
        f'{DRAFTER.name} declares no `ONEPIPELINE_OPENING="…"` on a line of its own, so '
        "nothing here can read the sentence it composes with; keep the declaration on one "
        "line or move this reader with it"
    )
    return declared.group("sentence")
