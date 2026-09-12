"""Four passages of `AGENTS.md` a manager acts on mid-run, held to what this checkout has.

Each is read in the middle of something going wrong — a watch firing, a change request
minutes from merging, a finding to rule on — so what is reconciled here
is what such a reader would act on: every `just <recipe>` those passages name is one
the justfile declares, and the subject passage's contrast rests on a drafting graph this
repository really has. The watch item's cut is held against `onepipeline`'s own status
view by `tests/test_engine_contracts.py`
(`test_the_watch_cuts_the_status_view_where_the_health_report_really_starts`).
"""

from __future__ import annotations

import re

import pytest

from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_docs

#: The manager's own document. Every passage here is manager judgment rather than
#: worker-facing operational text: arming a watch, reading a change request before it
#: merges, and ruling on a finding are all decisions no dispatch
#: makes, so none of this belongs in `config/dispatch-appendix.md`.
MANAGER = "AGENTS.md"

#: The opening phrase of each passage. A claim is matched inside its own paragraph
#: rather than document-wide: a phrase that survived somewhere else would satisfy a
#: whole-file search while the passage that has to carry it was gone.
WATCH_ITEM = "6. **A grep over the whole of `just status`"
SUBJECT = "**The body is re-derived at every publication"
FINDING = "**A supervisory finding earns a check, never an action**"
DECLINED = "**A finding you decline on the merits"

#: The drafting passage the subject passage is the counterpart of, and the graph that
#: makes its "the body is re-derived" half true.
DRAFTING = "A remote lifecycle change request's body is drafted by an agent graph"
DRAFTING_GRAPH = "graphs/pr-author.yaml"

#: How the justfile declares a recipe, so a `just <recipe>` these passages name can be
#: read back against the ones this repository has.
RECIPE = re.compile(r"^([a-z][a-z0-9-]*)(?:\s+[^:\n]*)?:", re.MULTILINE)
#: A `just <recipe>` as the prose spells it.
NAMED_RECIPE = re.compile(r"`just ([a-z][a-z0-9-]*)")


def _text(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _flat(prose: str) -> str:
    """Collapse every run of whitespace, so a claim may be quoted as one line."""
    return " ".join(prose.split())


def _paragraph(opener: str) -> str:
    """The one paragraph a claim has to be made in, from its opener to the blank line."""
    document = _text(MANAGER)
    assert opener in document, (
        f"{MANAGER} no longer carries a passage opening {opener!r}; a manager reading it "
        "relearns what it says by paying for it again"
    )
    return opener + document.split(opener, 1)[1].split("\n\n", 1)[0]


def test_the_subject_passage_rests_on_a_drafting_graph_this_repository_has() -> None:
    """ "The body is re-derived" is half the asymmetry, and it has to still be true.

    The passage is an argument from contrast: the body is drafted from the branch's own
    diff at every publication, and the subject is not. Take the drafting graph away and
    the contrast is gone — both artifacts would be stale, and the paragraph would be
    telling a manager to check the one field that is no worse than the other.
    """
    drafting = _paragraph(DRAFTING)
    assert DRAFTING_GRAPH in drafting, (
        f"{MANAGER}'s drafting passage no longer names {DRAFTING_GRAPH}, which is what "
        "re-derives a body per publication and so what the subject is contrasted against"
    )
    assert (REPO_ROOT / DRAFTING_GRAPH).exists(), (
        f"{DRAFTING_GRAPH} is gone from this checkout, so no publication drafts a body "
        f"and {MANAGER}'s claim that the body is re-derived at every publication is false"
    )


def test_every_recipe_these_passages_tell_a_manager_to_run_exists() -> None:
    """A passage may name commands this checkout has, and no others.

    Each of these is read in the middle of something going wrong — a watch firing, a
    change request minutes from merging — which is the worst moment to
    discover that the command in front of you is not a recipe here.
    """
    declared = set(RECIPE.findall(_text("justfile")))
    assert declared, "no recipe parsed out of the justfile, so this gate proves nothing"
    for passage in (WATCH_ITEM, SUBJECT, FINDING, DECLINED):
        for recipe in NAMED_RECIPE.findall(_paragraph(passage)):
            assert recipe in declared, (
                f"{MANAGER}'s passage opening {passage!r} tells a manager to run `just "
                f"{recipe}`, and the justfile declares no such recipe"
            )
