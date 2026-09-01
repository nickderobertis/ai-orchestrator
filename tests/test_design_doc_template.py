"""The design document's shape has one source, and the role that writes it points there.

`config/design-doc-template.md` states the document's six sections, their order, and the
properties the document is judged on. `personas/design-doc.yaml` is the role dispatched
to write and to review one, and it names that path instead of restating any of it — one
question gets one answer, and the answer belongs to the file that holds it.

Two failures are what this gate is against, and they are opposite halves of the same
drift. A template that loses a section, gains one, or reorders them changes the document
every writer produces while the role goes on being judged against a shape nobody
restated. A persona that names a path that is not the template's sends its writer to
nothing at all: the role's whole statement of the shape is that pointer, so a stale one
leaves both sides of the conversation composing their own.

`AGENTS.md` records what a second copy costs on this host, in the paragraph about the
adoption instruction a producer owns: a worker handed two answers follows the one in
front of it. The check below is why there is only ever one here.
"""

from __future__ import annotations

import re

import pytest

from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_docs

#: The one statement of the document's shape, and the role bound to it.
TEMPLATE = REPO_ROOT / "config" / "design-doc-template.md"
PERSONA = REPO_ROOT / "personas" / "design-doc.yaml"

#: How the persona has to name the template: repository-relative, which is the form a
#: dispatched worker can follow from the root of whatever checkout it is working in.
TEMPLATE_REFERENCE = "config/design-doc-template.md"

#: The six sections, in the order the document carries them. Stated here rather than read
#: from the template, because a gate that took its expectation from its subject would
#: pass whatever that subject said — which is the whole of what it is here to catch.
SECTIONS = ("What", "Why", "Architecture", "Contracts", "Acceptance criteria", "Planned tasks")

#: Where the template holds the shape: one fenced block, so the skeleton a writer copies
#: is separable from the prose about it. The document's own headings are outside it and
#: are deliberately not part of the shape.
FENCE = re.compile(r"^```markdown$(?P<shape>.*?)^```$", re.MULTILINE | re.DOTALL)

#: A section heading inside that block.
HEADING = re.compile(r"^## (?P<name>.+)$", re.MULTILINE)

#: Any path into this repository's own configuration directory that the persona names.
#: Read as a class rather than searched for the one expected value: a persona repointed
#: at a second config file is a second answer, and this is what reports it.
CONFIG_PATH = re.compile(r"config/[A-Za-z0-9_.-]+\.md")


def _shape() -> str:
    """The template's fenced skeleton — the part a writer's document is shaped like."""
    found = FENCE.search(TEMPLATE.read_text(encoding="utf-8"))
    assert found is not None, (
        f"{TEMPLATE.name} no longer carries its shape in one ```markdown block, so "
        "nothing here can tell the document's sections from the prose about them"
    )
    return found["shape"]


def test_the_template_states_the_six_sections_in_order() -> None:
    """The shape is those six, in that order, and nothing else is a heading of it."""
    named = tuple(match["name"] for match in HEADING.finditer(_shape()))
    assert named == SECTIONS, (
        f"{TEMPLATE.name} states {named} as the document's sections; the shape a person "
        f"reviews a plan as is {SECTIONS}, in that order and with no others. Changing it "
        "is a change to every design document this host produces, so change it here and "
        "here only — the role that writes one restates none of it."
    )


def test_the_persona_names_the_template_and_names_nothing_else() -> None:
    """The role's whole statement of the shape is that one pointer."""
    persona = PERSONA.read_text(encoding="utf-8")
    named = set(CONFIG_PATH.findall(persona))
    assert named == {TEMPLATE_REFERENCE}, (
        f"{PERSONA.name} names {sorted(named) or 'no configuration document'} where it "
        f"must name exactly {TEMPLATE_REFERENCE}: that pointer is the only thing sending "
        "a dispatched writer and its reviewer to the shape they are both held to"
    )
    assert TEMPLATE.is_file(), (
        f"{PERSONA.name} points at {TEMPLATE_REFERENCE}, which this checkout does not "
        "have, so a dispatch of this role is sent to nothing"
    )


def test_both_sides_of_the_role_are_pointed_at_it() -> None:
    """The writer's role and the reviewer's bar each name it, because each is held to it.

    Separate from the reference check above, which would pass on a file that named the
    template once. `system_prompt` is what the worker is given and `user.persona` is what
    its supervisor is given; a pointer in only one of them leaves the other side judging
    or writing against a shape it was never shown.
    """
    persona = PERSONA.read_text(encoding="utf-8")
    # From `system_prompt:` rather than from the file's start, so a pointer sitting in
    # the header comment — which reaches no turn at all — cannot answer for the role.
    _, _, declared = persona.partition("\nsystem_prompt: |\n")
    role, _, bar = declared.partition("\nuser:\n")
    assert role, f"{PERSONA.name} declares no `system_prompt`, so it states no role"
    assert bar, f"{PERSONA.name} declares no `user:` block, so it states no review bar"
    for half, prose in (("system_prompt", role), ("user.persona", bar)):
        assert TEMPLATE_REFERENCE in prose, (
            f"{PERSONA.name}'s {half} no longer names {TEMPLATE_REFERENCE}; both sides of "
            "this conversation are held to that shape, and the side that cannot see it "
            "composes its own"
        )


def test_the_persona_does_not_restate_the_shape() -> None:
    """The sections are stated once, and the role is not the place.

    A restatement is what goes stale: the template moves, the copy does not, and the
    writer follows whichever it read last. Headings are what a restatement would carry,
    so a persona that reproduces one is refused by that.
    """
    persona = PERSONA.read_text(encoding="utf-8")
    restated = [section for section in SECTIONS if f"## {section}" in persona]
    assert not restated, (
        f"{PERSONA.name} restates {restated} as document heading(s); the sections live in "
        f"{TEMPLATE_REFERENCE} alone, and a second copy is a second answer for a writer "
        "to follow the day the two disagree"
    )
