"""What `AGENTS.md` offers a manager for steering a run, held to what this checkout has.

Two things here are reconciled against something other than the prose: the heading the
operational notes a task carries really open at, which is what "above the operational
notes" means when a manager places an amendment; and the live-edit table a lever is
picked from. `tests/test_engine_contracts.py` holds the other end of the second — the op
table against the engine's `Command` enum — so a release that gains a binding op fails
there rather than leaving this passage describing a lever this host does not have.
"""

from __future__ import annotations

import re

import pytest

from orchestrator.criteria_guard import APPENDIX
from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_docs

#: The manager's own document, and the region of it the lever passage lives in. A region
#: rather than the whole file, because the claim is made where a manager is doing that
#: thing — a lever named somewhere else would satisfy a document-wide search while the
#: passage that has to carry it was gone.
MANAGER = "AGENTS.md"
LOOP_SECTION = "## Your loop as manager"

#: The heading the operational notes a task carries open at, which is what "above the
#: operational notes" means when a manager places an amendment in a task.
APPENDIX_HEADING = "## Additional info"

#: The live-edit table a lever is picked from, and how a row names its op. The first
#: column only: every row also backticks the fields that op takes, and `amend` is one of
#: them — reading the whole row would report a field as though it were a lever.
OP_TABLE = re.compile(
    r"The accepted commands are:\n\n\|[^\n]*\n\|[^\n]*\n((?:\|[^\n]*\n)+)", re.DOTALL
)
OP_NAME = re.compile(r"^\| `([a-z]+)` \|", re.MULTILINE)

#: The levers the passage names, each of which has to be an op that table declares.
NAMED_LEVERS = ("retry", "cancel", "requeue", "note", "amend")


def _text(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _flat(prose: str) -> str:
    """Collapse every run of whitespace, so a claim may be quoted as one line."""
    return " ".join(prose.split())


def _region(opener: str) -> str:
    """The passage a claim has to be made in, from its opener to the next section."""
    document = _text(MANAGER)
    assert opener in document, (
        f"{MANAGER} no longer carries {opener!r}, which is where a manager is told how to "
        "steer a run that is already going"
    )
    return document.split(opener, 1)[1].split("\n## ", 1)[0]


def test_the_operational_notes_really_open_at_the_heading_the_manager_is_told_to_sit_above() -> (
    None
):
    """ "Above the operational notes" has to name a position a task really has.

    A task carries `config/dispatch-appendix.md` verbatim — `just check-plan` refuses one
    that does not — so the heading that file opens with is the boundary an amendment sits
    above. If it moved, the placement instruction would send a manager to a heading no
    task has, and the precedence sentence would sit under text it does not govern.
    """
    appendix = _text(str(APPENDIX))
    assert appendix.lstrip().startswith(APPENDIX_HEADING), (
        f"{APPENDIX} no longer opens at {APPENDIX_HEADING!r}, so {MANAGER}'s instruction to "
        f"put an amendment above the operational notes names a boundary a task does not "
        f"have:\n{appendix[:120]}"
    )


def test_every_lever_the_manager_is_offered_is_an_op_the_engine_table_declares() -> None:
    """The passage may name levers this host has, and no others.

    A binding amendment op is a real proposal, and documenting one before the adopted
    engine has it would hand a manager a lever that fails at submission in the middle of
    a live run — the exact moment the passage is read. So every lever the manager passage
    names is read back against the live-edit table. `tests/test_engine_contracts.py`
    holds that table against the engine's own `Command` enum.
    """
    table = OP_TABLE.search(_text("docs/orchestration.md"))
    assert table is not None, (
        "docs/orchestration.md no longer tabulates the accepted commands where this gate "
        "reads it, so nothing here can say which levers a manager really has"
    )
    declared = set(OP_NAME.findall(table.group(1)))
    passage = _flat(_region(LOOP_SECTION))
    for lever in NAMED_LEVERS:
        assert lever in declared, (
            f"{MANAGER} offers a manager `{lever}` and the live-edit table does not declare "
            f"it; the table has {sorted(declared)}"
        )
        assert f"`{lever}`" in passage, (
            f"{MANAGER}'s lever passage no longer names `{lever}`, which is one of the "
            "commands it tells a manager to choose between"
        )
