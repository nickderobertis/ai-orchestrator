"""The out-of-band drafter still speaks `onepipeline`'s words for the same things.

`scripts/draft-pr-body.sh` exists to produce, for a branch no run will publish, the
body the run path would have produced. Everything else about the two is shared — the
same `graphs/pr-author.yaml`, the same `oneharness.pr-author.toml`, the same schema —
except the composed task, which the script writes itself because `onepipeline` is not
there to write it. Its opening sentence is therefore the one piece of that contract
this repository holds a copy of, and a copy of somebody else's interface goes stale
without saying so: the drafter would keep answering, under a prompt that no longer
matches the one every drafted body on this host was written under.

So the copy is checked against the original rather than remembered. The original is
the pinned `onepipeline` itself, which is the same thing `tests/published_surface.py`
does for the verbs and flags this repository's prose teaches — ask the binary, so a
bump re-derives the answer instead of replaying one.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

import pytest
from drafting_task_contract import drafting_endings, drafting_starting_points, onepipeline_opening

from orchestrator.root import REPO_ROOT

#: The pinned engine that owns both. Read as bytes because it is a stripped native
#: binary: each of these is a literal in its read-only data, and that is the only place
#: they exist outside this repository.
ONEPIPELINE = REPO_ROOT / ".venv" / "bin" / "onepipeline"


@cache
def _pinned_engine() -> bytes:
    """The pinned onepipeline, or a skip when this checkout has not installed one."""
    if not ONEPIPELINE.is_file():
        pytest.skip(f"the pinned onepipeline is not installed at {ONEPIPELINE}")
    return Path(ONEPIPELINE).read_bytes()


def test_the_drafters_opening_sentence_is_the_one_the_pinned_onepipeline_composes() -> None:
    """The sentence the script declares is a sentence the adopted engine still carries.

    A miss here is not a typo to fix in passing. Either the script drifted from the
    engine — in which case an out-of-band body was drafted under a prompt no run-path
    body was — or the engine reworded its own task, in which case the two paths have
    silently diverged and the decision of which wording to keep is a real one.
    """
    sentence = onepipeline_opening()

    assert sentence.encode("utf-8") in _pinned_engine(), (
        f"the pinned onepipeline does not carry {sentence!r}, which "
        "scripts/draft-pr-body.sh composes its drafting task with; either the script "
        "drifted from the engine or the engine reworded the task, and the two paths now "
        "prompt their drafters differently"
    )


@pytest.mark.parametrize("ending", drafting_endings())
def test_every_ending_the_drafter_reports_is_one_the_pinned_onepipeline_knows(
    ending: str,
) -> None:
    """Each name a bodyless run reports is a name the engine uses for the same thing.

    The ending is the whole of what an operator gets when nothing was drafted, and it
    is what tells them where to look: `dispatch-failed` points at the graph and its
    quota, `schema-refused` at the response schema or the prompt that answers it, and
    `no-body` at the drafting persona's prose. A name this script invented would send
    them through `onepipeline`'s own account of drafting looking for a word that is not
    in it — so the vocabulary is checked rather than remembered.
    """
    assert ending.encode("utf-8") in _pinned_engine(), (
        f"the pinned onepipeline does not carry the ending {ending!r}, which "
        "scripts/draft-pr-body.sh reports a bodyless run with; either the script "
        "invented a name or the engine renamed one, and an operator reading it would "
        "find nothing under it"
    )


@pytest.mark.parametrize("heading", drafting_starting_points())
def test_every_section_the_drafter_starts_from_is_one_the_pinned_onepipeline_composes(
    heading: str,
) -> None:
    """Each heading the prompt reads by name is a heading the adopted engine writes.

    The drafter is told to finish the description under `## Change request` and may run
    the command under `## Worker transcript`; both headings are the engine's literals,
    composed into the drafting task by the closeout. `scripts/draft-pr-body.sh` composes
    neither, so the graph is the one place this repository names them — and a heading
    the engine reworded would leave the drafter looking for a section the task no longer
    opens, silently starting over on a description the worker had begun.
    """
    assert heading.encode("utf-8") in _pinned_engine(), (
        f"the pinned onepipeline does not carry {heading!r}, which graphs/pr-author.yaml "
        "tells the drafter to read its starting point from; either the prompt named a "
        "section the engine never composes or the engine reworded the heading, and the "
        "drafter would start over on a description the worker had begun"
    )
