"""A scratch checkout standing in for the repository a repo-specific persona reviews.

The journeys that prove a persona reconciliation *reports* drift drive the real readers
against a repository whose tree they control, rather than against the registered checkout
of the repository the persona reviews: that checkout moves in its own repository, and a
journey reading it would fail on a sibling's commit rather than on the reader it proves.
What the persona says of the live repository is the reconciliation's own question, settled
by `tests/sibling_facts.py`.

The stand-in is a real git checkout tracking every path the persona names, defining every
`just` recipe it names, and carrying the type declarations the caller hands it — so the
tracked persona reconciles cleanly against it, and a drifted copy is reported against a
tree that holds what the corrected one names.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from persona_identifiers import identifiers_at
from persona_recipes import persona_at
from sibling_facts import scratch_checkout

#: What a named directory or glob is made concrete as: a persona names `expected/` and
#: `src/*.rs`, and a stand-in tracks one file each so both resolve.
STAND_IN_STEM = "stand_in"


def _tracked_file(named: str) -> str:
    """A file path that ``named`` — a file, a directory or a glob — resolves to."""
    concrete = named.replace("*", STAND_IN_STEM)
    return f"{concrete}{STAND_IN_STEM}" if concrete.endswith("/") else concrete


def _justfile(recipes: frozenset[str]) -> str:
    return "".join(f"{recipe}:\n    @true\n" for recipe in sorted(recipes))


def stand_in_for(persona: Path, directory: Path, declarations: Mapping[str, str]) -> Path:
    """A checkout under ``directory`` holding what ``persona`` names, and ``declarations``.

    ``declarations`` maps a tracked path to its source, and wins over the empty file a
    named path would otherwise be, so a caller states the types the repository declares.
    """
    repository = persona.parent.name
    named = identifiers_at(persona)
    files = {_tracked_file(path): "" for path in named.paths}
    files.update(declarations)
    files["justfile"] = _justfile(persona_at(persona).recipes)
    return scratch_checkout(directory / repository, f"example.invalid/stand-in/{repository}", files)
