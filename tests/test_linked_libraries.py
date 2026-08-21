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


#: The oneagentgraph release that stopped publishing an *outline* of a turn and
#: started publishing the turn: the `turn-message` kind, a turn's opening and the
#: instruction it answers, the observation that answered a tool call and the id
#: joining the two, and a `turn-completed` closing one turn on that turn's own
#: account. Beneath it a run records that a turn happened and almost nothing of what
#: was in it — so the Observatory renders a transcript with no tool results in it and
#: reports a cost of "Not reported", both of which read as a quiet run rather than as
#: a missing producer. Asserted for the same reason the floor above is: nothing else
#: on this host would say.
TURN_CONTENT_FLOOR = Release(0, 3, 6)


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
        # The pre-extraction callout's third denial, beside the onepipeline and onevcs
        # halves their own gates hold.
        "docs/onejudge-integration.md": ("`oneagentgraph` {version}, nor `onevcs`",),
    },
    "onevcs": {
        # The retry floor: a dispatched session publishes through the linked onevcs, so
        # the CLI pin beside it is not the version in force.
        "AGENTS.md": ("its lock still resolves {version}",),
        # Which onevcs the engine-behaviour claims in that document were read at. Every
        # one of them is about the copy a *dispatched* node publishes through, so the
        # linked version is the only one that answers for them — and `DRAIN_SECONDS`
        # becoming `DRAIN` between 0.4.2 and 0.8.0 is what this gate is for: the
        # constant gate below caught the rename, and nothing held the header sentence
        # that had just stopped naming the release the rename happened in.
        "docs/repo-lifecycle.md": (
            "the **`onevcs` {version}** its `Cargo.lock` resolves",
            "`onevcs` {version} has none to open",
        ),
        # The pre-extraction callout's denial: those symbols are absent from the engines
        # at named releases, and a bump that left the numbers behind would be a denial
        # about releases nothing runs.
        "docs/onejudge-integration.md": ("nor `onevcs` {version}.",),
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


def test_the_linked_oneagentgraph_carries_the_whole_turn_it_relays() -> None:
    """Above the outline release a turn's own content reaches the run's record.

    The bump this floor was added for, and it is invisible from exactly the angles
    the last one was: every gate here passes over a run whose turns carry no tool
    results and no per-turn usage, because a producer that publishes less looks
    identical to agents that did less. Only the linked release says which it was.
    """
    linked = Release.parse(_linked_version("oneagentgraph"))

    assert linked >= TURN_CONTENT_FLOOR, (
        f"the adopted engine links oneagentgraph {linked}, below the {TURN_CONTENT_FLOOR} "
        "that publishes tool results, live turn text, and per-turn usage; dispatched runs "
        "under it record an outline of each turn and the Observatory reports their cost "
        "as not reported"
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


class Divergence(NamedTuple):
    """One pin this host deliberately holds away from what the engine linked.

    Both versions are named, so the declaration goes stale the moment either moves:
    a divergence inherited across a bump is exactly the state these gates exist to
    end, and one that only named a reason would be inherited silently.
    """

    #: What the adopted engine wheel resolved.
    linked: str
    #: What `config/<crate>.version` names instead.
    pinned: str
    #: Why the two cannot be the same release today.
    because: str


#: Every crate the adopted engine links that this repository also pins a CLI for,
#: as crate → the version file beside it. The pin and the linked copy answer
#: different questions — one is what this host's own verbs run, the other is what a
#: dispatched node runs — and the point of naming them together is that they should
#: nonetheless resolve to the *same release*, so a manager reading either is reading
#: one number. See `AGENTS.md`'s "Which pin governs a dispatch".
RECONCILED_PINS = {
    "oneagentgraph": "oneagentgraph.version",
    "onevcs": "onevcs.version",
    "onejudge": "onejudge.version",
}

#: The pins that may not be reconciled today, each with the measured pair it was
#: declared against. A divergence is permitted only where the linked release is not
#: installable from PyPI at all — never as a convenience, and never as "not yet
#: looked at", because both read identically from here a release cycle later.
DECLARED_DIVERGENCES = {
    "onejudge": Divergence(
        linked="0.5.0",
        pinned="0.4.0",
        because=(
            "onejudge v0.5.0 is tagged and the PyPI `onejudge` distribution stops at "
            "0.4.0, so `scripts/session-setup.sh` has nothing to install; the pin "
            "moves the moment that release is on the registry"
        ),
    )
}

#: Where the one divergence is explained to an operator, and the sentence naming
#: both of its versions. Prose rather than only a comment, because the person who
#: meets this is a manager reading why two version files disagree, and the numbers
#: in that explanation go stale the same way every other restated measurement does.
DIVERGENCE_PROSE = (
    "AGENTS.md",
    "onepipeline 0.10.1 links the `onejudge` crate {linked}, and PyPI carries no "
    "`onejudge` {linked} — the tag is published and the distribution is not — so "
    "`config/onejudge.version` stays at {pinned}",
)


def _pinned_cli(version_file: str) -> str:
    """The release `config/<file>` adopts for a CLI this host runs itself."""
    return (REPO_ROOT / "config" / version_file).read_text(encoding="utf-8").strip()


def test_every_linked_crate_with_a_cli_pin_here_is_reconciled() -> None:
    """A pin added beside a linked crate joins this gate rather than going unread.

    The hole this closes is the one the module docstring's two incidents came
    through: a `config/<tool>.version` that nothing compares against the engine's own
    resolution reads as authoritative while a dispatch runs something else entirely.
    """
    linked = _linked_versions()
    pinnable = {
        crate: f"{crate}.version"
        for crate in linked
        if (REPO_ROOT / "config" / f"{crate}.version").is_file()
    }

    assert pinnable == RECONCILED_PINS, (
        f"{ENGINE_DISTRIBUTION} links {sorted(pinnable)} and this gate reconciles "
        f"{sorted(RECONCILED_PINS)}; a crate in one and not the other is a pin nothing "
        "holds to what the engine resolved"
    )


@pytest.mark.parametrize(("crate", "version_file"), sorted(RECONCILED_PINS.items()))
def test_the_cli_pin_names_the_release_the_engine_linked(crate: str, version_file: str) -> None:
    """`config/<crate>.version` is the release the adopted engine wheel resolved.

    Not because the two *have* to be one number — they are genuinely different
    adoptions — but because a host on which they differ has two answers to "which
    `onevcs` is in force", and this host has twice acted on the wrong one. Held
    against the wheel's own SBOM rather than against a lockfile or a `Cargo.toml`
    requirement, for the reason the module docstring gives.
    """
    linked = _linked_version(crate)
    pinned = _pinned_cli(version_file)
    declared = DECLARED_DIVERGENCES.get(crate)

    if declared is None:
        assert pinned == linked, (
            f"config/{version_file} adopts {crate} {pinned} while the adopted engine "
            f"links {linked}. Move the pin, or — only if {linked} cannot be installed — "
            "declare the divergence in DECLARED_DIVERGENCES with both versions and why"
        )
        return
    assert (declared.linked, declared.pinned) == (linked, pinned), (
        f"the declared {crate} divergence was written against linked {declared.linked} / "
        f"pinned {declared.pinned}, and this host measures {linked} / {pinned}. Re-read "
        f"whether it still holds — {declared.because} — and reconcile the pin or "
        "re-declare it against the pair that is really there"
    )


@pytest.mark.reads_docs
def test_the_declared_divergence_is_explained_where_an_operator_meets_it() -> None:
    """The prose naming both versions is held to the pair this host measures.

    A divergence is a thing a manager has to be told, not only a thing a gate
    tolerates: the whole failure mode is somebody reading one version file and
    concluding a fix is in force. So the explanation names both numbers, and this
    fails when either moves — which is the prompt to re-read the paragraph rather
    than to inherit it.
    """
    relative_path, template = DIVERGENCE_PROSE
    stated = {
        crate: template.format(linked=declared.linked, pinned=declared.pinned)
        for crate, declared in DECLARED_DIVERGENCES.items()
    }
    written = " ".join((REPO_ROOT / relative_path).read_text(encoding="utf-8").split())

    for crate, sentence in stated.items():
        assert written.count(sentence) == 1, (
            f"{relative_path} states the {crate} divergence {written.count(sentence)} "
            f"times, not once; it must say {sentence!r} so the operator who meets two "
            "disagreeing version files is told which one governs a dispatch"
        )
