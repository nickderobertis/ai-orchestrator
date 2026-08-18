"""The three contracts the manager/planner seam restates are reconciled with their source.

`scripts/plan.sh` and `scripts/ask-manager.sh` are shell, and shell cannot import. So
each of them holds a copy of a contract that is owned somewhere else — the task
template `personas/planner.yaml` states, and the reference grammar
`scripts/channel-serve.py` checks — and a copy is only sound while something
reconciles it. The third copy runs the other way: the wrapper declares what its
environment must carry, and the journeys that prove a launch builds it hold their own
list of those names.

Every copy fails quietly if it drifts, which is why they are gated here rather than
reviewed. A `REQUIRED_SECTIONS` that no longer matches the template lets a brief
through that is not a task, or refuses one that is. A `SAFE_REFERENCE` that no longer
matches the grammar lets the two ends of the same channel disagree about what a run id
is, so a value one of them passes to `onepipeline` is one the other would have refused.
And an input the wrapper starts requiring that no journey checks for is a launch path
free to stop providing it — which is exactly how a whole launch path came to export the
seam nowhere at all, unnoticed for every run this host had ever driven.
"""

from __future__ import annotations

import re

from orchestrator.root import REPO_ROOT

#: The recipe that turns a manager's brief into a dispatched task, and the prose that
#: says what a task is written in. That template is the **planner's** half of the
#: decomposition doctrine, so it lives in the persona that travels with the dispatch
#: rather than in `AGENTS.md`, which stays in this checkout;
#: `tests/test_decomposition_guidance.py` is what keeps it in exactly one of them.
PLAN_SCRIPT = REPO_ROOT / "scripts" / "plan.sh"
TASK_TEMPLATE = REPO_ROOT / "personas" / "planner.yaml"

#: The two ends of the planner channel that each check a reference before passing it
#: to `onepipeline`: the filter that serves the monitor's side, and the wrapper a
#: dispatched agent asks through.
CHANNEL_FILTER = REPO_ROOT / "scripts" / "channel-serve.py"
ASK_SCRIPT = REPO_ROOT / "scripts" / "ask-manager.sh"

#: The journeys that measure what each launch shape hands a dispatch, and the shape
#: their list of required inputs is written in. Read textually rather than imported: it
#: is a pytest module whose import would collect fixtures, and reading a declaration is
#: what every other gate in this file does.
LAUNCH_JOURNEYS = REPO_ROOT / "tests" / "e2e" / "test_launch_ask_seam_e2e.py"
CHECKED_INPUT = re.compile(r'Input\(\s*"([A-Z0-9_]+)"')

#: How `scripts/ask-manager.sh` declares an environment variable it cannot ask without,
#: in the `Environment:` block of its own header. The `(optional)` ones are deliberately
#: not matched: a launch that provides none of them is still a launch an agent can ask
#: from.
REQUIRED_INPUT = re.compile(r"^#\s+([A-Z0-9_]+)\s+\(required\)", re.MULTILINE)

#: `REQUIRED_SECTIONS=("## What" "## Why" "## Acceptance criteria")`, read out of the
#: script rather than restated, so this gate compares the shell's own list.
DECLARED_SECTIONS = re.compile(r"REQUIRED_SECTIONS=\(([^)]*)\)")

#: How `personas/planner.yaml` names the same three, in the list that requires them of
#: every task: "Write every node's task — and every step's task — with these headings,
#: in this order:", followed by one bullet per heading. Deliberately stopping at
#: `## Additional info`: the same list states that fourth heading and states it as
#: conditional, so it is not one a brief can be refused for lacking. Matched as code
#: spans, so the prose around them can be reworded without this gate caring, and
#: matched against flattened prose, because the persona is a hard-wrapped YAML block
#: scalar and every one of these phrases is longer than the line it sits on.
TEMPLATE_LIST = re.compile(
    r"Write every node's task.*?in this order:(?P<named>.*?)`## Additional info`"
)
SPAN = re.compile(r"`(##[^`]+)`")

#: Each script's declaration of what a run id may be. Read from both rather than
#: written here: this gate's whole job is that the two agree, and a third copy in the
#: test would be one more thing to keep current.
PYTHON_GRAMMAR = re.compile(r"SAFE_RUN_ID = re\.compile\(r\"(?P<pattern>[^\"]+)\"\)")
SHELL_GRAMMAR = re.compile(r"SAFE_REFERENCE='(?P<pattern>[^']+)'")

#: The anchors each language spells differently for the same thing: Python's `\A`/`\Z`
#: match the whole string, and in a bash `[[ =~ ]]` that is what `^`/`$` do. Stripping
#: them is what leaves the grammar itself to compare.
ANCHORS = (("\\A", ""), ("\\Z", ""), ("^", ""), ("$", ""))


def _flat(prose: str) -> str:
    """Collapse every run of whitespace, so a wrapped phrase reads as one line."""
    return " ".join(prose.split())


def _body(pattern: str) -> str:
    """One reference grammar with its anchors removed, so two spellings compare."""
    for anchor, replacement in ANCHORS:
        pattern = pattern.replace(anchor, replacement)
    return pattern


def test_the_brief_template_the_plan_recipe_requires_is_the_one_the_doctrine_states() -> None:
    """`just plan` refuses exactly the sections a task is required to have.

    The brief a manager writes IS the dispatched task, so the recipe checks it against
    the template every task here is written in — and holds the copy of that template in
    shell, where nothing can import the prose that owns it. Drift in either direction
    is silent and costly: a section dropped here lets a brief through that dispatches a
    planner with no review bar, and one added here refuses a brief that is a perfectly
    good task — which is why `## Additional info`, stated in the same list but stated as
    conditional, is on neither side of this comparison.
    """
    declared = DECLARED_SECTIONS.search(PLAN_SCRIPT.read_text(encoding="utf-8"))
    assert declared is not None, f"{PLAN_SCRIPT.name} declares no REQUIRED_SECTIONS"
    required = tuple(re.findall(r'"([^"]+)"', declared.group(1)))
    assert required, f"{PLAN_SCRIPT.name} requires no section at all"

    listed = TEMPLATE_LIST.search(_flat(TASK_TEMPLATE.read_text(encoding="utf-8")))
    assert listed is not None, (
        f"{TASK_TEMPLATE.name} no longer states the task template in the list this gate "
        "reads it from; update the pattern here together with that list"
    )
    stated = tuple(SPAN.findall(listed.group("named")))

    assert required == stated, (
        f"{PLAN_SCRIPT.name} requires {list(required)} of a brief, but "
        f"{TASK_TEMPLATE.name} states a task is written with {list(stated)}"
    )


def test_both_ends_of_the_planner_channel_check_one_reference_grammar() -> None:
    """A run id is the same thing to the filter that serves the channel and to the asker.

    Both hold the value at the same boundary and put it to the same two uses — an argv
    word `onepipeline` takes, and a `runs/<run-id>/` directory it resolves — so a
    grammar that drifted apart would mean one end passing on a value the other refuses,
    and the difference would only ever show up as a channel that would not answer.
    """
    named = PYTHON_GRAMMAR.search(CHANNEL_FILTER.read_text(encoding="utf-8"))
    checked = SHELL_GRAMMAR.search(ASK_SCRIPT.read_text(encoding="utf-8"))
    assert named is not None, f"{CHANNEL_FILTER.name} declares no SAFE_RUN_ID"
    assert checked is not None, f"{ASK_SCRIPT.name} declares no SAFE_REFERENCE"

    assert _body(named.group("pattern")) == _body(checked.group("pattern")), (
        f"{CHANNEL_FILTER.name} accepts {named.group('pattern')!r} as a run id while "
        f"{ASK_SCRIPT.name} accepts {checked.group('pattern')!r}; one end of the channel "
        "would refuse a run the other passed on"
    )


def test_every_input_the_wrapper_requires_is_one_a_launch_is_measured_for() -> None:
    """A launch is proven to build exactly what the wrapper refuses without.

    The wrapper's header names each variable it cannot ask without, and the journeys
    name each one they read out of a real dispatch's environment. Only one direction is
    an error: a required input nothing measures is a launch path free to drop it, and
    the failure lands on some agent's first blocking question rather than in the suite.
    The journeys may check *more* than the wrapper strictly requires — the seam itself
    is one such name, since a wrapper never reads the variable that names it.
    """
    required = set(REQUIRED_INPUT.findall(ASK_SCRIPT.read_text(encoding="utf-8")))
    assert required, (
        f"{ASK_SCRIPT.name}'s header declares no required environment at all; this gate "
        "reads the `(required)` lines of its `Environment:` block"
    )

    measured = set(CHECKED_INPUT.findall(LAUNCH_JOURNEYS.read_text(encoding="utf-8")))

    assert required <= measured, (
        f"{ASK_SCRIPT.name} requires {sorted(required - measured)} of the environment a "
        f"launch builds, and {LAUNCH_JOURNEYS.name} measures no launch shape for it"
    )
