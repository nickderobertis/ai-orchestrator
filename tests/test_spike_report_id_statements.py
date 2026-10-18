"""Every statement of a spike report's document id is the shape `report_id` gives.

`orchestrator/spike_plan.py`'s `report_id` is the one definition of the id a spike writes its
report under, `<plan native id>-<spike id>-report`. The planner, the templates a plan is
rendered from and the documents a manager reads each restate it, and a restatement that
drifted would brief a spike to write under an id no reader looks for — or, as the bare
`<spike id>-report` once did, under one another plan's spike shares in a flat source. So each
of them is read for every placeholder id ending `-report` it quotes, and each must quote
`REPORT_SHAPE` and nothing else.
"""

from __future__ import annotations

import re

import pytest

from orchestrator import spike_plan
from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_docs

#: Each file that states the convention.
STATEMENTS = (
    "personas/planner.yaml",
    "templates/plan-task.md.j2",
    "templates/spike-report.md.j2",
    "templates/templates.yaml",
    "docs/orchestration.md",
    "AGENTS.md",
    "orchestrator/spike_plan.py",
)

#: A quoted id with a placeholder in it, ending as every report id does.
STATED = re.compile(rf"`([^`\n]*<[^`\n]*>[^`\n]*{re.escape(spike_plan.REPORT_SUFFIX)})`")


def stated_shapes(text: str) -> list[str]:
    """Every placeholder report id ``text`` quotes, in order."""
    return STATED.findall(text)


def test_the_reader_finds_a_stated_shape_and_a_drifted_one() -> None:
    assert stated_shapes("the document `<plan native id>-<spike id>-report` of it") == [
        "<plan native id>-<spike id>-report"
    ]
    assert stated_shapes("as ``<spike id>-report`` of the plan") == ["<spike id>-report"]
    assert stated_shapes("the `spike-report` template and `<project>-budgets`") == []


@pytest.mark.parametrize("path", STATEMENTS)
def test_each_statement_of_the_report_id_is_report_ids_shape(path: str) -> None:
    shapes = stated_shapes((REPO_ROOT / path).read_text(encoding="utf-8"))

    assert shapes, f"{path} no longer states a spike report's id"
    assert set(shapes) == {spike_plan.REPORT_SHAPE}, (
        f"{path} states a spike report's id as {sorted(set(shapes))}, and `report_id` gives "
        f"`{spike_plan.REPORT_SHAPE}`"
    )
