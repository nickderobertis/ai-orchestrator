"""Passages of `AGENTS.md` a manager acts on mid-run, held to what this checkout has.

Each is read in the middle of something going wrong — a watch firing, a change request
minutes from merging, a finding to rule on — so what is reconciled here
is what such a reader would act on: every `just <recipe>` those passages name is one
the justfile declares. The watch item's cut is held against `onepipeline`'s own status
view by `tests/test_engine_contracts.py`
(`test_the_watch_cuts_the_status_view_where_the_health_report_really_starts`).
"""

from __future__ import annotations

import re

from orchestrator.root import REPO_ROOT

#: The manager's own document. Every passage here is manager judgment rather than
#: worker-facing operational text: arming a watch, reading a change request before it
#: merges, and ruling on a finding are all decisions no dispatch
#: makes, so none of this belongs in `templates/dispatch-appendix.md`.
MANAGER = "AGENTS.md"

#: The opening phrase of each passage. A claim is matched inside its own paragraph
#: rather than document-wide: a phrase that survived somewhere else would satisfy a
#: whole-file search while the passage that has to carry it was gone.
WATCH_ITEM = "6. **A grep over `just status --providers`"
SUBJECT = "**Read a change request's title against its net diff"
FINDING = "**A supervisory finding earns a check, never an action**"
DECLINED = "**A finding you decline on the merits"
SETTLED = "**Read every settled state before acting on it.**"
LEVERS = "**Two levers, and what each binds.**"

#: The decision rules the user approved keeping when the root file was cut, each as the
#: paragraph that has to carry it. Each is the rule a manager acts on at the moment a
#: node fails or is corrected, and dropping one is what sent a manager to re-run work that
#: had already published or to a replacement that never heard its correction.
KEPT_RULES: tuple[tuple[str, str], ...] = (
    (SETTLED, "so read its settlement and change request before any retry"),
    (SETTLED, "a retry of a branch a merge path refused always names that branch"),
    (SETTLED, "read it on the host and never publish it again"),
    (LEVERS, "replacement does not inherit the amendment of the node it replaces"),
)

#: The engine-written outcome table in `docs/repo-lifecycle.md`, which
#: `tests/test_engine_contracts.py` holds pairing for pairing to the adopted onepipeline.
LIFECYCLE = "docs/repo-lifecycle.md"
OUTCOME_ROW = re.compile(r"^\| `[a-z]+` \| `([a-z][a-z-]*)` \|", re.MULTILINE)
#: An outcome word the settled-state paragraph names, as it spells one.
NAMED_WORD = re.compile(r"`([a-z]+(?:-[a-z]+)+)`")

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


def test_the_kept_decision_rules_stand_in_their_own_passages() -> None:
    """Each rule the cut kept is still stated in the paragraph a manager reads it in."""
    for opener, rule in KEPT_RULES:
        assert rule in _flat(_paragraph(opener)), (
            f"{MANAGER}'s passage opening {opener!r} no longer states {rule!r}, a rule the "
            "user approved keeping when the file was cut"
        )


def test_every_outcome_the_settled_state_passage_names_is_one_the_engine_writes() -> None:
    """The settled-state rules are about outcome words, so each has to be a real one.

    Read against the lifecycle document's outcome table rather than the engine directly,
    because that table is what `tests/test_engine_contracts.py` holds to the engine both
    ways; a word here the table lacks is a settlement no node reports.
    """
    written = set(OUTCOME_ROW.findall(_text(LIFECYCLE)))
    assert written, f"no outcome row parsed out of {LIFECYCLE}, so this gate proves nothing"
    named = set(NAMED_WORD.findall(_paragraph(SETTLED)))
    assert "pushed-unverified" in named, (
        f"{SETTLED!r} no longer names `pushed-unverified`, whose rule it carries: {named}"
    )
    unknown = sorted(named - written)
    assert not unknown, (
        f"{MANAGER}'s passage opening {SETTLED!r} names {unknown}, which {LIFECYCLE}'s "
        "outcome table does not pair with any status"
    )
