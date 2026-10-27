"""The half of `tests/test_linked_libraries.py` that reads this repository's prose.

It sits in the `release-pins` project.

These hold each claim about a linked library to the version the installed engine links. That
project's key names each file they read, and `tests/conftest.py` holds them to it; the tests
of that module that read no prose stay there, in `orchestrator:test`, and those reading a
registered checkout in `orchestrator:test-checkouts`.
"""

from __future__ import annotations

import pytest
from test_linked_libraries import (
    ENGINE_DISTRIBUTION,
    LINKED_VERSION_CLAIMS,
    _linked_version,
    npm_sbom,
)

from orchestrator.root import REPO_ROOT


@pytest.mark.parametrize(
    ("crate", "relative_path", "templates"),
    [
        (crate, path, templates)
        for crate, files in LINKED_VERSION_CLAIMS.items()
        for path, templates in files.items()
    ],
)
def test_claims_about_a_linked_library_name_the_linked_version(
    crate: str, relative_path: str, templates: tuple[str, ...]
) -> None:
    """Each sentence is required exactly once, against the version the wheel declares.

    Exactly once for the reason the pin gates give: a claim satisfied anywhere in its
    file leaves a second site ungated, and a duplicate left by an edit goes unnoticed
    the same way. Against the wheel rather than a literal because the whole defect this
    gate exists for is prose that agreed with a `Cargo.toml` requirement while the build
    resolved something else.
    """
    linked = _linked_version(crate)
    # Whitespace-normalized: these sentences wrap across lines, and a reflow is not a
    # change to what they assert.
    written = " ".join((REPO_ROOT / relative_path).read_text(encoding="utf-8").split())

    for template in templates:
        stated = template.format(version=linked)
        assert written.count(stated) == 1, (
            f"{relative_path} states {stated!r} {written.count(stated)} times, not once; "
            f"the adopted engine links {crate} {linked}, so re-read the claim against "
            "that and update the one site in the same change"
        )


#: The view lines the documents quote from the adopted engine, each as the binary spells
#: it, and the documents that quote it: what `status` and `results` print for scratch a
#: run kept, the discard verb that line names, and what `results` prints for space a
#: maintenance pass freed.
ENGINE_VIEW_LINES: dict[str, tuple[str, ...]] = {
    "scratch kept: ": ("AGENTS.md", "docs/host-setup.md", "docs/orchestration.md"),
    "onepipeline scratch discard ": (
        "AGENTS.md",
        "docs/host-setup.md",
        "docs/orchestration.md",
        "docs/repo-lifecycle.md",
    ),
    "pool maintenance: freed ": ("docs/repo-lifecycle.md",),
}


def test_the_engine_prints_the_scratch_and_maintenance_lines_the_documents_quote() -> None:
    """Each line a document quotes from the engine's views is one the installed binary
    carries, so a release that respells it fails here rather than leaving the documents
    naming a line no view prints."""
    # The platform package's own binary, beside the SBOM it ships: the launcher in
    # `node_modules/.bin` is a script that runs it.
    binary = npm_sbom(ENGINE_DISTRIBUTION).parent.parent / "bin" / "onepipeline"
    assert binary.is_file(), f"{ENGINE_DISTRIBUTION} installs no engine at {binary}"
    carried = binary.read_bytes()

    for spelling, documents in ENGINE_VIEW_LINES.items():
        assert spelling.encode() in carried, (
            f"the installed onepipeline no longer carries {spelling!r}, which {documents} "
            "quote; re-read the engine's views and update each document"
        )
        for document in documents:
            text = " ".join((REPO_ROOT / document).read_text(encoding="utf-8").split())
            assert spelling.strip() in text, f"{document} no longer quotes {spelling!r}"
