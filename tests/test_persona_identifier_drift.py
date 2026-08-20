"""The tracked catalog's identifiers, against the repositories those personas review.

`tests/persona_identifiers.py` states the failure this reconciles away, and why it is a
separate check from `tests/test_persona_recipe_drift.py` rather than an extension of it:
`personas/crozier/crozier-corpus.yaml` named `just fixtures-candidates`, which crozier
still defines as an alias, while the prose around it described a corpus model that had
inverted — an allowlist of files that match, where crozier had moved to an exclusion list
of files that do not. A recipe-existence check passes on every one of those names. Two
different failures, so two checks: a recipe that disappears is a command nobody can run,
and an identifier that changes meaning is a command that still runs and now asks for the
opposite of the work.

This belongs to the uncached tier for the reason the recipe reconciliation does. Its input
is a registered checkout outside this workspace, which no `nx.json` key covers, so a
memoized green would be a verdict on whatever that repository declared when it was
recorded — and a field changing meaning is precisely what it exists to catch.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from persona_identifiers import NamedIdentifiers, identifiers_at, undefined_identifiers
from persona_recipes import PERSONA_ROOT, checkout_of, repo_specific_persona_files

from orchestrator.root import REPO_ROOT

#: The persona whose drift this check was written from, and the repository it reviews.
CROZIER_PERSONA = REPO_ROOT / "personas" / "crozier" / "crozier-corpus.yaml"

#: The field the uncorrected file registered a corpus with. crozier's `tests/e2e.rs`
#: declares `unmatched`; `matched` is the inverted model, and the word occurs in crozier's
#: own prose often enough that only the declaration can settle it.
DRIFTED_FIELD = "matched"
CORRECT_FIELD = "unmatched"


def _identifiers(personas: tuple[Path, ...]) -> list[NamedIdentifiers]:
    return [identifiers_at(path) for path in personas]


def test_the_catalog_still_holds_a_repo_specific_persona_that_names_an_identifier() -> None:
    """A reconciliation that resolved nothing would pass silently forever.

    The check below is parametrized over what the catalog holds, so a catalog that stopped
    naming anything of the repositories it reviews — or a persona layout that stopped
    matching — would collect no cases and report the same green as a clean reconciliation.
    Both classes are demanded, because either one going quiet halves the check invisibly.
    """
    personas = repo_specific_persona_files()
    assert personas, (
        f"no repo-specific persona was found under {PERSONA_ROOT}; if the catalog's "
        "layout changed, repo_specific_persona_files must change with it or the "
        "reconciliation below silently covers nothing"
    )
    named = _identifiers(personas)
    assert any(identifiers.paths for identifiers in named), (
        "no repo-specific persona names a path into the repository it reviews any more, "
        "so half of this reconciliation has nothing to reconcile"
    )
    assert any(identifiers.literals for identifiers in named), (
        "no repo-specific persona writes a structured literal any more, so the field "
        "reconciliation — the half that catches an identifier changing meaning — has "
        "nothing to reconcile"
    )


@pytest.mark.reads_checkouts
@pytest.mark.parametrize(
    "identifiers", _identifiers(repo_specific_persona_files()), ids=lambda named: named.named
)
def test_a_repo_specific_persona_only_names_identifiers_its_repository_has(
    identifiers: NamedIdentifiers,
) -> None:
    """The drift gate: a persona's prose, against the tree that owns the fact."""
    checkout = checkout_of(identifiers.repository)
    if checkout is None:
        pytest.skip(
            f"this host holds no registered checkout of {identifiers.repository}, so "
            f"{identifiers.named} cannot be reconciled against it here"
        )

    report = undefined_identifiers(identifiers, checkout)
    assert report is None, report


@pytest.fixture(scope="module")
def crozier_checkout() -> Path:
    """crozier's own registered checkout, which owns the facts these journeys read."""
    found = checkout_of(CROZIER_PERSONA.parent.name)
    if found is None:
        pytest.skip(
            f"this host holds no registered checkout of {CROZIER_PERSONA.parent.name}, so "
            "the identifiers its persona names cannot be reconciled against it here"
        )
    return found


@pytest.mark.reads_checkouts
def test_the_inverted_corpus_field_is_reported_before_the_corrected_file_passes(
    tmp_path: Path, crozier_checkout: Path
) -> None:
    """The regression this check was written from, driven rather than described.

    A real copy of the tracked persona with the pre-correction `Corpus { .., matched: &[] }`
    put back is reconciled against crozier's own registered checkout, and the report is
    read for all three facts a reader needs — one saying only that something drifted sends
    them back through the whole investigation. The corrected file is then reconciled
    against the same checkout, in that order, so the green below is a green this drift
    would have broken.
    """
    corrected = CROZIER_PERSONA.read_text(encoding="utf-8")
    assert f"{CORRECT_FIELD}: &[]" in corrected, (
        f"the tracked persona no longer registers a corpus with `{CORRECT_FIELD}: &[]`, so "
        "this journey cannot drift it back to the model it was corrected from"
    )
    drifted = tmp_path / "personas" / CROZIER_PERSONA.parent.name / CROZIER_PERSONA.name
    drifted.parent.mkdir(parents=True)
    drifted.write_text(corrected.replace(CORRECT_FIELD, DRIFTED_FIELD), encoding="utf-8")

    report = undefined_identifiers(identifiers_at(drifted), crozier_checkout)
    assert report is not None, (
        f"a persona registering a corpus with `{DRIFTED_FIELD}` was accepted against "
        f"{crozier_checkout}, whose `Corpus` declares no such field"
    )
    assert str(drifted) in report
    assert f"Corpus {{ {DRIFTED_FIELD}: … }}" in report
    assert "tests/e2e.rs:" in report, (
        f"the report does not say where it looked, so a reader cannot check it: {report}"
    )
    assert str(crozier_checkout) in report
    assert CORRECT_FIELD in report, (
        f"the report does not name the field crozier actually declares: {report}"
    )

    assert undefined_identifiers(identifiers_at(CROZIER_PERSONA), crozier_checkout) is None


@pytest.mark.reads_checkouts
def test_a_persona_naming_a_path_its_repository_dropped_is_reported(
    tmp_path: Path, crozier_checkout: Path
) -> None:
    """The other class, driven the same way: a file the reviewed repository does not track.

    A rename is what this catches — crozier's `tests/e2e.rs` is named by both the role and
    the review bar, and a repository that moved it would leave every dispatch pointed at a
    path nobody can open.
    """
    renamed = "tests/corpus-e2e.rs"
    persona = tmp_path / CROZIER_PERSONA.parent.name / CROZIER_PERSONA.name
    persona.parent.mkdir(parents=True)
    tracked = CROZIER_PERSONA.read_text(encoding="utf-8")
    assert "`tests/e2e.rs`" in tracked, (
        "the tracked persona no longer names `tests/e2e.rs`, so this journey cannot drift "
        "that path to one crozier does not have"
    )
    persona.write_text(tracked.replace("tests/e2e.rs", renamed), encoding="utf-8")

    report = undefined_identifiers(identifiers_at(persona), crozier_checkout)
    assert report is not None, (
        f"a persona naming `{renamed}` was accepted against {crozier_checkout}, which "
        "tracks no such path"
    )
    assert str(persona) in report
    assert f"`{renamed}`" in report
    assert str(crozier_checkout) in report


@pytest.mark.reads_checkouts
def test_a_persona_naming_a_type_its_repository_never_declares_is_reported(
    tmp_path: Path, crozier_checkout: Path
) -> None:
    """A literal whose type is gone is reported, never quietly resolved as having no fields.

    This is the direction the parser has to fail in: it reads brace-delimited declarations,
    so a type it cannot find that way — renamed, or written in a shape it does not parse —
    is a report a reader can act on rather than a silent pass that reads like agreement.
    """
    persona = tmp_path / CROZIER_PERSONA.parent.name / CROZIER_PERSONA.name
    persona.parent.mkdir(parents=True)
    persona.write_text(
        "name: probe\nsystem_prompt: |\n  Register a `Fixture { gaps: &[] }` const.\n",
        encoding="utf-8",
    )

    report = undefined_identifiers(identifiers_at(persona), crozier_checkout)
    assert report is not None, (
        f"a persona writing a `Fixture {{ … }}` literal was accepted against "
        f"{crozier_checkout}, which declares no such type"
    )
    assert str(persona) in report
    assert "`Fixture`" in report
    assert str(crozier_checkout) in report
