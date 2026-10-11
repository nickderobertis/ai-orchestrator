"""The half of `tests/test_envelope_review.py` that reads this repository's prose.

It sits in the `dispatch-bar` project.

These hold the envelope review to the bar and the prose that state it. That project's key
names each file they read, and `tests/conftest.py` holds them to it; the tests of that
module that read no prose stay there, in `orchestrator:test`.
"""

from __future__ import annotations

from test_envelope_review import (
    APPENDIX_TEXT,
    LIVE_EDIT_TABLE,
    LIVE_EDIT_TABLE_OPENS,
    SOUND_CRITERION,
    _envelope,
    _live_edit_rows,
    _retry,
    _task,
    refusal,
)

from orchestrator import envelope_review
from orchestrator.criteria_guard import APPENDIX
from orchestrator.root import REPO_ROOT


def test_the_task_bearing_ops_are_the_ones_the_live_edit_table_says_carry_a_task() -> None:
    """Which ops put task prose in front of a dispatch is the engine's answer, gated here.

    The module restates three things of the live-edit table — which ops state a whole
    task, in which field each states it, and which op states an amendment — and this is
    what holds the restatement to the table rather than to memory.
    """
    rows = _live_edit_rows()
    assert rows, f"no live-edit table under {LIVE_EDIT_TABLE_OPENS!r} in {LIVE_EDIT_TABLE}"

    stated_in = {
        op: "node" if "`node`: full" in fields else "amend"
        for op, fields in rows.items()
        if "`node`: full" in fields or "`amend`: partial node overrides" in fields
    }
    assert stated_in == envelope_review.STATED_IN, (rows, envelope_review.STATED_IN)
    assert set(stated_in) == set(envelope_review.WHOLE_TASK_OPS)

    amending = [op for op, fields in rows.items() if fields == "`id`; `text`"]
    assert amending == [envelope_review.AMEND_OP], rows

    stated = LIVE_EDIT_TABLE.read_text(encoding="utf-8")
    assert f"`{envelope_review.NO_DISPATCH}: true`" in stated, (
        f"the field a node declares it dispatches nobody by is not the one {LIVE_EDIT_TABLE} "
        f"documents"
    )
    assert "without dispatching onejudge" in stated


def test_the_tracked_appendix_itself_is_left_alone() -> None:
    """The exemption proven against the text this host actually requires.

    Every dispatched task carries `templates/dispatch-appendix.md` verbatim, and that text
    names `pkill`, `git status`, a `just` invocation and a chained shell command — so a
    reader that examined its section would refuse every task this repository demands.
    """
    tracked = (REPO_ROOT / APPENDIX).read_text(encoding="utf-8").strip()

    assert (
        refusal(_envelope(_retry(_task(criteria=SOUND_CRITERION).replace(APPENDIX_TEXT, tracked))))
        is None
    )
