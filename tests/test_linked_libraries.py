"""What the adopted `onepipeline` release *links*, as opposed to what this host installs.

`onepipeline` links `oneagentgraph`, `onevcs`, and `onejudge` as Rust libraries, so a
dispatch runs the copy that release resolved — never the CLI `config/<tool>.version`
pins. That distinction has produced a wrong diagnosis here twice: once for `onevcs`,
where widening a `Cargo.toml` requirement was mistaken for the fix, and once for
`oneagentgraph`, where `config/oneagentgraph.version` read 0.3.3 for a release cycle
while every dispatched run came out with no transcript in it because the engine linked
0.3.0.

Both times the missing thing was a source. There is one, published with the release
and installed alongside it: the `onepipeline-cli` wheel ships a CycloneDX SBOM under
its `dist-info/sboms/`, declaring one version per linked crate. Reading it needs no
network, no clone of the engine, and no `Cargo.lock` — so the claims this repository's
prose makes about the linked versions are reconciled here rather than mirrored by hand.
"""

from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path
from typing import NamedTuple

import pytest

from orchestrator.root import REPO_ROOT

#: The distribution whose SBOM answers the question. It is the same wheel
#: `tests/published_tools.py` pins and `scripts/session-setup.sh` verifies; this
#: module reads what that wheel *contains* rather than which version it is.
ENGINE_DISTRIBUTION = "onepipeline-cli"
#: Where a wheel is required to put SBOMs, per PEP 770.
SBOM_DIRECTORY = "sboms"


class Release(NamedTuple):
    """One crate release, ordered so a floor can be stated as a comparison.

    A named type rather than a bare tuple because both sides of that comparison are
    read from somewhere else — one from the engine wheel's SBOM, one declared here —
    and a positional triple gives a reader nothing to check the pairing against.
    """

    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, declared: str) -> Release:
        """Read a release the SBOM declares, rejecting anything that is not one."""
        parts = declared.split(".")
        assert len(parts) == 3 and all(part.isdigit() for part in parts), (
            f"{ENGINE_DISTRIBUTION}'s SBOM declares the version {declared!r}, which is not "
            "one this gate can order against a floor"
        )
        return cls(*(int(part) for part in parts))

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


#: The oneagentgraph release that added the session-conversation producer: the
#: `session` label on a member's turn envelopes and the `oneharness-session` event
#: carrying the pointer to that turn's retained transcript. Below it the DAG
#: Observatory's transcript route renders and finds nothing behind it — silently, which
#: is why this floor is asserted rather than left to a reader noticing an empty surface.
PRODUCER_FLOOR = Release(0, 3, 3)


def _linked_versions() -> dict[str, set[str]]:
    """Every crate the adopted engine wheel declares it links, as name → versions.

    A version *set* rather than one version, because a Cargo graph legitimately carries
    two majors of a transitive crate at once. Only the crates this repository makes a
    claim about have to resolve to one, and `_linked_version` is where that is required.
    """
    distribution = importlib.metadata.distribution(ENGINE_DISTRIBUTION)
    files = distribution.files or ()
    sboms = [entry for entry in files if SBOM_DIRECTORY in Path(entry).parts]
    assert len(sboms) == 1, (
        f"{ENGINE_DISTRIBUTION} must ship exactly one SBOM for the linked versions to be "
        f"read from; found {[str(entry) for entry in sboms]}"
    )
    document = json.loads(Path(str(distribution.locate_file(sboms[0]))).read_text("utf-8"))
    linked: dict[str, set[str]] = {}
    for component in document["components"]:
        linked.setdefault(component["name"], set()).add(component["version"])
    return linked


def _linked_version(crate: str) -> str:
    """The one version of `crate` the adopted engine links, or a failure naming why not."""
    declared = _linked_versions().get(crate, set())
    assert len(declared) == 1, (
        f"{ENGINE_DISTRIBUTION}'s SBOM declares {crate} at {sorted(declared) or 'no version'}; "
        "this repository's prose claims one linked version, which nothing here can "
        "reconcile against that"
    )
    return declared.pop()


#: Per-crate claims about what the adopted engine links, as crate → file → the sentence
#: that file must spell for the *measured* version. Same shape as the pin drift gates in
#: `tests/test_onejudge_version.py` and a different source: those hold a sentence to a
#: file this repository writes, this one holds a sentence to a fact the engine publishes.
LINKED_VERSION_CLAIMS: dict[str, dict[str, tuple[str, ...]]] = {
    "oneagentgraph": {
        # Which oneagentgraph reads a persona at dispatch, which is what decides the
        # shape every file in `personas/` has to be written in.
        "personas/README.md": (
            "oneagentgraph `onepipeline` links, which is `{version}` at onepipeline",
            "and the linked reader is {version}",
        ),
        # Why an empty transcript surface is an engine-pin question and not a reader one.
        "docs/dag-ui.md": ("the adopted release that is **oneagentgraph {version}**",),
    },
    "onevcs": {
        # The retry floor: a dispatched session publishes through the linked onevcs, so
        # the CLI pin beside it is not the version in force.
        "AGENTS.md": ("its lock still resolves {version}",),
    },
}


def test_the_engine_wheel_declares_one_version_per_linked_crate() -> None:
    """The SBOM is only a source if it answers for the crates the prose asks about."""
    linked = _linked_versions()

    for crate in LINKED_VERSION_CLAIMS:
        assert linked.get(crate), (
            f"{ENGINE_DISTRIBUTION}'s SBOM declares no {crate}; this repository's prose "
            f"claims a linked {crate} version that nothing here can now reconcile"
        )


def test_the_linked_oneagentgraph_carries_the_session_conversation_producer() -> None:
    """Below the producer release a run records no transcript, and says nothing about it.

    This is the failure the pin bump to onepipeline 0.8.3 fixed, and it is invisible from
    every other angle: `just check`, `just status`, and the read API are all green over a
    run whose turns were never published, because a missing event looks exactly like a
    turn that did nothing.
    """
    linked = Release.parse(_linked_version("oneagentgraph"))

    assert linked >= PRODUCER_FLOOR, (
        f"the adopted engine links oneagentgraph {linked}, below the {PRODUCER_FLOOR} that "
        "publishes a member's conversation; dispatched runs under it record no "
        "`oneharness-session` event and no `session` label, and the DAG Observatory's "
        "transcript route finds nothing"
    )


@pytest.mark.reads_docs
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
