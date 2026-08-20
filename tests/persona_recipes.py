"""What a persona demands of the repository it reviews, and whether that repository has it.

A persona's `user.persona` becomes a supervisor's review bar verbatim, and the worker
never sees it. So a bar demanding a recipe its repository does not define fails
finished, gate-green work for a reason this host authored, and the worker cannot argue
its way out of it. That is measured rather than imagined:
`personas/crozier/crozier-corpus.yaml` demanded `just gate` be fully green, and crozier
has no `gate` recipe at all — its deterministic tier is `check` and its judged tier is
a separate `lint-llm-diff`, which is exactly the pair `config/onevcs.rules.yml`
resolves for crozier's merge path.

Which recipes a repository defines is a fact no file here decides, so the only way to
hold the prose to it is to ask that repository. Two callers do, at the two places the
demand exists: `tests/test_persona_recipe_drift.py` reads it out of the tracked file,
and `tests/e2e/test_persona_review_bar_e2e.py` reads it out of the bar a real
supervisor was handed on a real graph run.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path
from typing import NamedTuple

from registered_checkouts import defined_recipes, registered_checkouts

from orchestrator.root import REPO_ROOT

#: The tracked persona catalog, whose subdirectories are the repo-specific roles.
PERSONA_ROOT = REPO_ROOT / "personas"

#: How a persona names a recipe: inside backticks, which is how every command in this
#: catalog's prose is written. Anchoring on the backtick is what keeps an ordinary
#: adverbial "just" out of the reconciliation, and `(?!-)` keeps a flag out of it.
NAMED_RECIPE = re.compile(r"`just\s+(?!-)(?P<recipe>[A-Za-z0-9_][A-Za-z0-9_-]*)")


class RepoPersona(NamedTuple):
    """One persona that states the repository it reviews, and what it demands of it."""

    #: How a failure names the demand's source: repository-relative for a tracked file.
    named: str
    #: The repository it is for, which is the subdirectory it lives in.
    repository: str
    #: Every `just` recipe its prose names — the role's and the review bar's alike,
    #: since both reach a dispatch and either can demand one that does not exist.
    recipes: frozenset[str]


def named_recipes(prose: str) -> frozenset[str]:
    """Every `just` recipe `prose` names."""
    return frozenset(match["recipe"] for match in NAMED_RECIPE.finditer(prose))


def persona_at(path: Path) -> RepoPersona:
    """Read one persona file into what the reconciliation needs of it."""
    try:
        named = str(path.relative_to(REPO_ROOT))
    except ValueError:
        named = str(path)  # a persona a test built, which lives outside this checkout
    return RepoPersona(
        named=named,
        repository=path.parent.name,
        recipes=named_recipes(path.read_text(encoding="utf-8")),
    )


def repo_specific_persona_files() -> tuple[Path, ...]:
    """Every tracked persona file that states a repository.

    **This is where the scope is decided**, for every reconciliation that holds a
    persona to the repository it reviews — the recipes it names below, and the
    identifiers `tests/persona_identifiers.py` holds to the same checkout. A
    repo-specific persona states its repository by the subdirectory it lives in
    (`personas/<repo>/<name>.yaml`), so that directory name is what resolves the
    checkout to reconcile it against. A flat persona at the top of the catalog is a
    general, cross-repo role: it names no repository, no one checkout is the one to ask
    about its recipes, and it is deliberately outside these reconciliations rather than
    merely unmatched by them.

    Underscore-prefixed files and directories are skipped for the reason
    `just validate-personas` skips them — they are templates, not dispatchable
    personas.
    """
    return tuple(
        path
        for path in sorted(PERSONA_ROOT.glob("*/*.yaml"))
        if not path.name.startswith("_") and not path.parent.name.startswith("_")
    )


def repo_specific_personas() -> tuple[RepoPersona, ...]:
    """Every tracked persona that states a repository, and the recipes it names."""
    return tuple(persona_at(path) for path in repo_specific_persona_files())


@cache
def checkout_of(repository: str) -> Path | None:
    """Where this host holds a checkout of `repository`, if it holds one.

    A persona's subdirectory names the repository, which is the last segment of the
    identity `onevcs` files it under. Resolved once per repository: each lookup asks
    git which identity every listed checkout really is.
    """
    for identity, path in sorted(registered_checkouts().items()):
        if identity.rpartition("/")[2] == repository:
            return path
    return None


def undefined_recipes(persona: RepoPersona, checkout: Path) -> str | None:
    """What `persona` demands that `checkout` cannot run, reported, or `None`.

    The report names all three things a reader needs to act without re-deriving the
    investigation: what made the demand, which recipe it named, and which checkout was
    searched for it.
    """
    defined = defined_recipes(checkout)
    if not defined.readable:
        return (
            f"`just --dump` could not parse the recipes of {checkout}, so "
            f"{persona.named} could not be reconciled against it at all"
        )
    missing = sorted(persona.recipes - defined.names)
    if not missing:
        return None
    return (
        f"{persona.named} names `just {'`, `just '.join(missing)}`, which the registered "
        f"checkout {checkout} does not define — a supervisor holding a worker to a recipe "
        "nobody can run fails finished work, so name verification that repository has"
    )
