"""A plan task's unit: the one record its design document's picture, table and Architecture read.

A unit is what a task changes — a repository, or a part of one — and the design document is
organised by it: the Architecture gives each one a subsection, the Planned tasks table names
each task's in its Unit column, and the graph above that table draws each task inside its
unit's box. The three agree only because the plan records a task's unit once, as the task
metadata key :data:`UNIT_KEY`, which the planner writes (`personas/planner.yaml`) and nothing
downstream restates: `onetaskgraph project graph --group-by` reads it for the picture, and
:mod:`orchestrator.planned_tasks` derives the table's rows and the Architecture's unit names
from that same graph.

**The value rule, stated here and nowhere else.** A non-empty JSON string on one line, of at
most :data:`UNIT_MAX_CHARS` characters, byte-identical across the tasks of one unit. A task
whose value breaks it is refused by `just check-plan` (`orchestrator/plan_check.py`, which
asks :func:`problem` of every task) with no fallback, because a task the graph cannot place
in a unit is a picture and a table that disagree.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Mapping

#: The task metadata key a task's unit is recorded under.
UNIT_KEY = "orchestrator.unit"

#: The most characters a unit's name may hold: it labels a box in a picture read at a
#: board column's width, and a subsection heading.
UNIT_MAX_CHARS = 60

#: What :func:`problem` is handed for a task whose metadata carries no unit at all, so an
#: absent key and a JSON `null` are told apart in what a refusal says.
ABSENT = object()


def problem(value: object) -> str | None:
    """What is wrong with ``value`` as a task's unit, or ``None`` when it is one.

    ``value`` is the decoded JSON the store holds under :data:`UNIT_KEY`, or :data:`ABSENT`.
    """
    match value:
        case _ if value is ABSENT:
            return f"carries no `{UNIT_KEY}` metadata"
        case str() if not value:
            return f"holds an empty string under `{UNIT_KEY}`"
        case str() if "\n" in value or "\r" in value:
            return f"holds a string of more than one line under `{UNIT_KEY}`"
        case str() if len(value) > UNIT_MAX_CHARS:
            return (
                f"holds a string of {len(value)} characters under `{UNIT_KEY}`, more than the "
                f"{UNIT_MAX_CHARS} a unit's name may hold"
            )
        case str():
            return None
        case _:
            return f"holds {json.dumps(value)} under `{UNIT_KEY}`, which is not a JSON string"


def set_command(task: str) -> str:
    """The plan-store command that records ``task``'s unit, with a placeholder for its name."""
    return (
        f"onetaskgraph task metadata set {shlex.quote(task)} {UNIT_KEY} "
        f"{shlex.quote(json.dumps('<unit>'))}"
    )


def unit_of(metadata: Mapping[str, object]) -> str:
    """The unit ``metadata`` records, or :class:`ValueError` saying what is wrong with it."""
    value = metadata.get(UNIT_KEY, ABSENT)
    wrong = problem(value)
    if wrong is not None:
        raise ValueError(wrong)
    assert isinstance(value, str)
    return value
