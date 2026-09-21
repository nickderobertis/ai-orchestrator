"""The pacemaker's discipline is stated twice, and the two copies must agree.

`graphs/dag-scope.yaml`'s `check-in` member names `personas/check-in.yaml` for its
label alone: a single-sided `kind: oneharness` member has no onejudge base config to
layer a persona delta over, so neither that file's `system_prompt` nor its
`user.persona` reaches the turn — the member's own `task` is the whole of what the
model is given. `tests/e2e/test_path_dispatched_personas_e2e.py` is what measures that
arrangement, and `tests/e2e/test_supervisory_prompt_discipline_e2e.py` reads the `task`
out of a real run of the shipped member.

Which leaves the persona a document nothing reads at dispatch and everything reads as
the role. Both failure modes are silent and this gate exists for the pair of them: an
editor who tightens the persona and believes the pacemaker changed has changed nothing,
and an editor who tightens the `task` alone leaves the catalog describing a role the
member no longer has — and a two-party dispatch of that persona would get the old one.

So the *rules* are reconciled here rather than the prose. Wording is each copy's own —
the `task` carries the incidents at length because it is what the model reads, and the
persona states the rule — but a rule present in one and absent from the other is drift.
"""

from __future__ import annotations

import re
from typing import NamedTuple

import pytest

from orchestrator.root import REPO_ROOT

#: The two documents, and the block of each that states the role.
GRAPH = "graphs/dag-scope.yaml"
PERSONA = "personas/check-in.yaml"

#: The member whose `task` is the copy the model is actually given.
PACEMAKER_MEMBER = "check-in"


class Rule(NamedTuple):
    """One rule the pacemaker is held to, and the phrase each copy has to carry."""

    #: What the rule is, for the failure message — the reader is being told which
    #: half of the pair to go and repair, not merely that two strings differ.
    name: str
    #: A phrase in `graphs/dag-scope.yaml`'s `check-in` task.
    graph: str
    #: A phrase in `personas/check-in.yaml`'s `system_prompt`.
    persona: str


#: Every rule this node's change put in both copies. Each is a property of what the
#: pacemaker may say, not a sentence either document is frozen to: the phrases are the
#: shortest span that cannot survive the rule being dropped.
RULES = (
    Rule(
        name="the update is handed to the verb as bytes on its stdin",
        graph="onepipeline surface --kind check-in <run-id> <<'UPDATE'",
        persona="onepipeline surface --kind check-in <run-id> <<'UPDATE'",
    ),
    Rule(
        name="and the inline `--message` form is refused by name, not merely unused",
        graph="inline `--message`",
        persona="inline `--message`",
    ),
    Rule(
        name="and the instruction says why, so it cannot be simplified back",
        graph="command substitution",
        persona="command substitution",
    ),
    Rule(
        name="nothing is called a hang until its elapsed time is measured against a bound",
        graph="compared its elapsed time against a bound you located and can name",
        persona="compared its elapsed time against a bound you located and can name",
    ),
    Rule(
        name="an absence of events beside a live heartbeat is generation, not silence",
        graph="absence of events beside a live heartbeat as generation",
        persona="absence of events beside a live heartbeat as generation",
    ),
    Rule(
        name="a liveness claim shows the command it ran and that command's own output",
        graph="Quote the command you ran and that command's own output",
        persona="Quote the command you ran and that command's own output",
    ),
    Rule(
        name="and is read a second time before it becomes a terminal verdict",
        graph="read it a second time before you escalate to a terminal verdict",
        persona="read it a second time before you escalate to a terminal verdict",
    ),
    Rule(
        name="the update reports how many follow-up drafts the run holds, and their titles",
        graph="Report how many follow-up drafts the run's draft project holds and their titles",
        persona="Report how many follow-up drafts the run's draft project holds and their titles",
    ),
    Rule(
        name="read from the draft root the launch exported, through the plan store",
        graph="`onetaskgraph task list --source drafts --project <run-id> --json`",
        persona="`onetaskgraph task list --source drafts --project <run-id> --json`",
    ),
    Rule(
        name="the pacemaker may draft a non-blocking follow-up itself, as itself",
        graph='`"$ORCHESTRATOR_FOLLOW_UP_DRAFT" --as pacemaker --member check-in`',
        persona='`"$ORCHESTRATOR_FOLLOW_UP_DRAFT" --as pacemaker --member check-in`',
    ),
    Rule(
        name="and anything the planner must act on now stays in the update, never only a draft",
        graph="anything the planner must act on now stays in this update, never only in a draft",
        persona="anything the planner must act on now stays in this update, never only in a draft",
    ),
    # The bounded sequence, and the three things it forbids by name. Two recorded turns
    # spent the member's whole finite deadline on command discovery, a host-wide run
    # listing and recursive searches, and were killed having raised no surface.
    Rule(
        name="the run is read with one run-scoped status view",
        graph="`onepipeline status <run-id>`",
        persona="`onepipeline status <run-id>`",
    ),
    Rule(
        name="the two readings are the whole of the reading",
        graph="Those two readings are the whole of",
        persona="Those two readings are the whole of",
    ),
    Rule(
        name="command discovery is forbidden by name",
        graph="Never discover commands: no `--help` walk, no `just --list`",
        persona="Never discover commands: no `--help` walk, no `just --list`",
    ),
    Rule(
        name="host-wide run listings are forbidden by name",
        graph="Never list runs host-wide: no bare `just runs`, no `onepipeline runs`",
        persona="Never list runs host-wide: no bare `just runs`, no `onepipeline runs`",
    ),
    Rule(
        name="recursive filesystem searches are forbidden by name",
        graph="Never search the filesystem recursively: no `rg`, no `grep -r`, no `find`",
        persona="Never search the filesystem recursively: no `rg`, no `grep -r`, no `find`",
    ),
    Rule(
        name="a question the readings cannot answer is reported unanswered, not investigated",
        graph="reported in the update as unanswered",
        persona="reported in the update as unanswered",
    ),
)


def _block(document: str, opener: str) -> str:
    """The body of one block scalar of a YAML document, by the line that opens it.

    A reader written for this rather than a YAML library: the workspace installs none,
    and `oneagentgraph validate` over both documents is what holds them well-formed.
    """
    lines = (REPO_ROOT / document).read_text(encoding="utf-8").splitlines()
    opened = next((index for index, line in enumerate(lines) if line.strip() == opener), None)
    assert opened is not None, f"{document} no longer opens a `{opener}` block"
    indent = len(lines[opened]) - len(lines[opened].lstrip())
    body = []
    for line in lines[opened + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        body.append(line.strip())
    return " ".join(body)


def _pacemaker_task() -> str:
    """The `check-in` member's own `task`, which is the copy the model is given."""
    lines = (REPO_ROOT / GRAPH).read_text(encoding="utf-8").splitlines()
    opened = next(
        (index for index, line in enumerate(lines) if line.strip() == f"{PACEMAKER_MEMBER}:"),
        None,
    )
    assert opened is not None, f"{GRAPH} declares no `{PACEMAKER_MEMBER}` member"
    indent = len(lines[opened]) - len(lines[opened].lstrip())
    within = []
    for line in lines[opened + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        within.append(line)
    task = next((index for index, line in enumerate(within) if line.strip() == "task: |"), None)
    assert task is not None, (
        f"the `{PACEMAKER_MEMBER}` member no longer claims its own `task`, which is the "
        "only prose a single-sided member is given"
    )
    body_indent = len(within[task]) - len(within[task].lstrip())
    body = []
    for line in within[task + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) <= body_indent:
            break
        body.append(line.strip())
    return " ".join(body)


@pytest.mark.parametrize("rule", RULES, ids=lambda rule: rule.name)
def test_the_pacemakers_task_and_its_persona_state_the_same_rule(rule: Rule) -> None:
    """Neither copy may carry a rule the other has lost."""
    task = _pacemaker_task()
    persona = _block(PERSONA, "system_prompt: |")

    assert rule.graph in task, (
        f"{GRAPH}'s `{PACEMAKER_MEMBER}` task no longer states that {rule.name}. That "
        "is the copy the pacemaker is actually given, so the rule is not in force; "
        f"{PERSONA} still states it, which is the drift this gate exists for."
    )
    assert rule.persona in persona, (
        f"{PERSONA} no longer states that {rule.name}, while {GRAPH}'s "
        f"`{PACEMAKER_MEMBER}` task still does. The catalog now describes a role the "
        "member does not have, and a two-party dispatch of this persona would get the "
        "weaker one."
    )


def test_the_persona_records_that_the_pacemaker_never_reads_it() -> None:
    """The persona says why it is a second copy, so its next editor knows which one bites.

    Without it the file reads as the pacemaker's prompt, which is how a tightening
    lands somewhere no model will ever see it.
    """
    text = (REPO_ROOT / PERSONA).read_text(encoding="utf-8")
    header = text.split("\n", 1)[0]

    assert "llmlint: ignore-file[contracts_have_one_source_or_a_drift_gate]" in header, (
        f"{PERSONA} no longer declares itself the second copy, so the duplication "
        "below it is unexplained"
    )
    assert re.search(rf"\b{re.escape(GRAPH)}\b", header), (
        f"{PERSONA}'s directive no longer names {GRAPH} as the copy that reaches the "
        "model, which is the whole of what it has to say"
    )
    assert "tests/test_pacemaker_discipline.py" in header, (
        f"{PERSONA}'s directive no longer names this gate, so its claim to have one is "
        "unverifiable by the reader it is written for"
    )
