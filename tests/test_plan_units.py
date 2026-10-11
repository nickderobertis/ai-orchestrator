"""A task's unit, held to its one rule by `orchestrator/plan_units.py`.

`orchestrator.unit` is the one record the design document's graph, its Planned tasks table
and its Architecture read a task's unit from, so its key and its value rule are stated once
in that module and read by `just check-plan`'s host tier and the finish-plan flow alike.
`tests/plan_tooling/plan_flow/test_check_plan_units_e2e.py` drives each refusal through the
real recipe over a real store; what is here is the rule itself, part by part, and the
statement the planner is given of it.
"""

from __future__ import annotations

import json
import re
import shlex

import pytest
import yaml

from orchestrator import plan_units
from orchestrator.root import REPO_ROOT

#: Each way a value breaks the rule, and the words the refusal says it in.
BROKEN = {
    "absent": (plan_units.ABSENT, "carries no `orchestrator.unit` metadata"),
    "null": (None, "holds null under `orchestrator.unit`, which is not a JSON string"),
    "number": (7, "holds 7 under `orchestrator.unit`, which is not a JSON string"),
    "list": (["Plan graph"], 'holds ["Plan graph"] under'),
    "empty": ("", "holds an empty string under `orchestrator.unit`"),
    "two lines": ("Plan\ngraph", "a string of more than one line"),
    "carriage return": ("Plan\rgraph", "a string of more than one line"),
    "61 characters": ("x" * 61, "a string of 61 characters under `orchestrator.unit`"),
}


@pytest.mark.parametrize("name", list(BROKEN))
def test_each_break_of_the_value_rule_is_named(name: str) -> None:
    value, said = BROKEN[name]
    stated = plan_units.problem(value)
    assert stated is not None and said in stated, stated
    with pytest.raises(ValueError, match=re.escape(stated)):
        plan_units.unit_of({} if value is plan_units.ABSENT else {plan_units.UNIT_KEY: value})


@pytest.mark.parametrize("value", ["x" * 60, "Plan graph", "Design document flow"])
def test_a_one_line_string_of_at_most_sixty_characters_is_a_unit(value: str) -> None:
    assert plan_units.problem(value) is None
    assert plan_units.unit_of({plan_units.UNIT_KEY: value}) == value


def test_the_repair_command_is_the_stores_own_metadata_write_and_parses_as_one() -> None:
    command = plan_units.set_command("authoring:plan/the task")
    assert shlex.split(command) == [
        "onetaskgraph",
        "task",
        "metadata",
        "set",
        "authoring:plan/the task",
        "orchestrator.unit",
        '"<unit>"',
    ], command


def test_the_planner_is_told_the_key_the_readers_import() -> None:
    """The persona's statement of the key is the constant, not a second spelling of it."""
    loaded = yaml.safe_load((REPO_ROOT / "personas" / "planner.yaml").read_text("utf-8"))
    stated = " ".join(json.dumps(loaded).replace("\\n", " ").split())
    instruction = re.search(r"Give every task you create the task metadata key `([^`]+)`", stated)
    assert instruction is not None, stated
    assert instruction.group(1) == plan_units.UNIT_KEY
    (example,) = re.findall(r"for example `--metadata '([\w.]+)=", stated)
    assert example == plan_units.UNIT_KEY
    assert f"`--metadata {plan_units.UNIT_KEY}=<JSON string>`" in stated, stated
    assert f"task metadata set <id> {plan_units.UNIT_KEY} " in stated, stated
    assert f"at most {plan_units.UNIT_MAX_CHARS} characters" in stated, stated
