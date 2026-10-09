"""The planner is told when and how to author the visual spike, and the writer how to restate it.

A plan that changes what a user sees authors the reserved spike `orchestrator/spike_plan.py`
names, whose before-and-after screenshots the design document shows. What a planner needs to
author it is judgment, so it lives in `personas/planner.yaml`'s spikes guidance, which travels
into whatever repository is planned against; what the design-document writer needs is that the
answer is quoted to it, never composed, which `personas/design-doc.yaml` says. This holds both
to saying so, read off the files a dispatch is given, and holds the spike's turn cap — the
manager's ruling in place of a spend or planning-latency budget — to the one statement of it.
"""

from __future__ import annotations

import re
import subprocess

import pytest
import yaml

from orchestrator import spike_plan
from orchestrator.root import REPO_ROOT

PLANNER = REPO_ROOT / "personas" / "planner.yaml"
DESIGN_DOC = REPO_ROOT / "personas" / "design-doc.yaml"

#: The heading the planner's spikes guidance sits under, and the one after it.
SPIKES = "## Spikes: measuring before the plan is final"
NEXT = "## How to write a task"

#: The ceiling on the visual spike's turns, as the manager ruled it.
CAP = 40
#: How the guidance states the cap: the one sentence any reader of it can find.
CAP_STATEMENT = re.compile(r"Give it `max_turns` no larger\s+than (\d+): a hard cap")

#: One phrase per thing the guidance has to say, keyed by what it is.
GUIDANCE = {
    "the reserved id": f"authors one visual spike**, `{spike_plan.VISUAL_SPIKE}`",
    "a web view": "a web app's pages or components",
    "a TUI": "a TUI",
    "a CLI's output": "a CLI's printed output",
    "no cost without a visual change": "A plan with none authors no visual spike",
    "capture first": "Check that each view can be captured before authoring it",
    "ask rather than improvise": "author no visual spike and ask the manager instead",
    "a reusable capture command first": "lands a reusable capture command",
    "resume at spikes": "the flow resumes at its spikes stage",
    "minimal and mocked": "mocked or fixture data, the quickest hacky approach",
    "no verification": "no tests, no lint and no verification of any kind",
    "before from existing baselines": "existing baseline screenshots of its unchanged base",
    "recapture only what no baseline covers": "captured on the unchanged base only where",
    "after on its change": 'the "after" is captured on the spike\'s own change',
    "same view per pair": "both show the same view",
    "screencomp's gallery": "github.com/nickderobertis/screencomp",
    "no image cache": "builds an image cache or baseline store",
    "the report's answer": "report with `visual_changes` filled",
    "assets": "gives each image to the store with `--asset`",
    "nothing lands": "nothing it produces lands",
}


def _flat(text: str) -> str:
    return " ".join(text.split())


def _spikes_guidance() -> str:
    """The planner's spikes guidance, as the dispatched planner's system prompt carries it."""
    role = yaml.safe_load(PLANNER.read_text(encoding="utf-8"))["system_prompt"]
    assert SPIKES in role and NEXT in role, f"{PLANNER.name} moved its spikes guidance"
    return role.split(SPIKES, 1)[1].split(NEXT, 1)[0]


def test_the_planners_spikes_guidance_says_when_and_how_to_author_the_visual_spike() -> None:
    guidance = _flat(_spikes_guidance())
    missing = [what for what, phrase in GUIDANCE.items() if _flat(phrase) not in guidance]
    assert not missing, f"{PLANNER.name}'s spikes guidance no longer states {missing}"


def test_the_visual_spike_is_capped_at_forty_turns_as_a_hard_cap() -> None:
    """The cap keeps the spike a throwaway, and is stated as the manager's ruling."""
    guidance = _spikes_guidance()
    stated = [int(found) for found in CAP_STATEMENT.findall(guidance)]
    assert stated == [CAP], f"{PLANNER.name} states the visual spike's cap as {stated}"
    flat = _flat(guidance)
    assert "the manager's ruling, an accepted exception in place of a spend or" in flat, flat


@pytest.mark.reads_docs
def test_the_cap_is_stated_in_the_planner_persona_and_nowhere_else() -> None:
    """A second statement of the number is a second answer to drift from the first."""
    tracked = subprocess.run(
        ["git", "grep", "-l", "-E", "-z", r"max_turns`? no larger", "--", ".", ":!tests"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert tracked.returncode in (0, 1), tracked.stderr
    assert [one for one in tracked.stdout.split("\0") if one] == ["personas/planner.yaml"]


def test_the_writer_is_told_visual_changes_is_quoted_by_its_task_and_never_composed() -> None:
    role = _flat(yaml.safe_load(DESIGN_DOC.read_text(encoding="utf-8"))["system_prompt"])
    assert "The budget answers and `visual_changes` are the parts you do not compose" in role
    assert "task quotes `visual_changes` from the plan's visual spike report" in role, role
    assert "giving each image with `--asset`" in role, role
