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
import re
import urllib.error
import urllib.request
import warnings
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
    def parse(cls, declared: str, source: str = f"{ENGINE_DISTRIBUTION}'s SBOM") -> Release:
        """Read a release from `source`, rejecting anything that is not one.

        `source` is named by the caller because this model is shared: the SBOM is
        where the versions this module orders come from, and a `config/*.version` pin
        is where another gate's come from. A failure that named the wrong one would
        send a reader to a file that is not the one carrying the bad value.
        """
        parts = declared.split(".")
        assert len(parts) == 3 and all(part.isdigit() for part in parts), (
            f"{source} declares the version {declared!r}, which is not one this gate "
            "can order against a floor"
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


#: The onevcs release that removed the **gate**: the tier that library ran itself in a
#: publication's own clone, before it would push. Below it a `gate:` on a rule is not
#: only accepted but *executed*, so a host whose rules file has been migrated to schema
#: 3 does not merely lose a deprecation warning — its rules file stops loading at all
#: (`default: missing field `gate``), and a host that has not migrated silently returns
#: to running a verifier beside the real one and throwing its answer away.
#:
#: Asserted as a floor rather than left to the pin reconciliation below, because the two
#: fail differently and only this one is about a *dispatch*. `config/onevcs.version` is
#: the CLI the manager verbs run; what a dispatched node publishes through is this
#: linked copy, and a bump to `config/onepipeline.version` that resolved an older onevcs
#: would put the gate back into every dispatch while every version file on the host
#: still read 0.11.2.
GATE_FREE_FLOOR = Release(0, 11, 0)


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


def _linked_by_dependent(crate: str) -> tuple[LinkedCore, ...]:
    """Who brings `crate` into the adopted engine, and at which release each one does.

    The SBOM carries a `dependencies` graph beside its component list, and it is the
    only published thing that answers this: the component list says a crate is linked
    twice and cannot say *why*, which for a crate linked twice is the whole of the
    answer.
    """
    distribution = importlib.metadata.distribution(ENGINE_DISTRIBUTION)
    sboms = [entry for entry in (distribution.files or ()) if SBOM_DIRECTORY in Path(entry).parts]
    document = json.loads(Path(str(distribution.locate_file(sboms[0]))).read_text("utf-8"))
    components = {component["bom-ref"]: component for component in document["components"]}
    brought: list[LinkedCore] = []
    for edge in document.get("dependencies", ()):
        for depended in edge.get("dependsOn", ()):
            if components.get(depended, {}).get("name") != crate:
                continue
            dependent = components.get(edge["ref"])
            assert dependent is not None, (
                f"{ENGINE_DISTRIBUTION}'s SBOM has {edge['ref']} depending on {crate} and "
                "declares no component for it, so nothing here can name what brought it in"
            )
            brought.append(
                LinkedCore(
                    dependent=dependent["name"],
                    dependent_version=dependent["version"],
                    core=components[depended]["version"],
                )
            )
    return tuple(sorted(brought))


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
        "AGENTS.md": ("its lock still resolves onevcs {version}",),
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


def test_the_linked_onevcs_runs_no_gate_of_its_own() -> None:
    """Above the removal, the repository's own merge path is the only verifier.

    The regression this floor stops is silent in the direction that matters. A
    dispatch publishing through an older linked onevcs would resolve the `gate:` a
    version 1 or 2 rules file names and run it — and this host's tracked file is
    version 3, which such a release cannot load at all, so what a dispatch would
    actually meet is a publication refused for a malformed rules file. Neither
    outcome is visible from `config/onevcs.version`, which names the CLI the manager
    verbs run rather than the copy a node publishes through.
    """
    linked = Release.parse(_linked_version("onevcs"))

    assert linked >= GATE_FREE_FLOOR, (
        f"the adopted engine links onevcs {linked}, below the {GATE_FREE_FLOOR} that "
        "removed the gate; a dispatch would publish through a release that still runs "
        "a tier of its own, and cannot read this host's version 3 rules file at all"
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


class UnreconcilablePin(NamedTuple):
    """A CLI pin here whose adopted release no linked crate can be compared against.

    Such a pin can never join `RECONCILED_PINS`, because the thing it names is not a
    thing the engine links. Both spellings are named because they differ, and that
    near-miss is exactly why this crate went ungated while its three siblings did not:
    `_linked_versions()` is keyed on `oneharness-core` and nothing was looking for a
    file called `oneharness-core.version`.
    """

    #: The stem of `config/<pin>.version`, which is a CLI release.
    pin: str
    #: The crate the engine links beside it, which is a different artifact.
    crate: str


class LinkedCore(NamedTuple):
    """One dependent of that crate, and the release of it that dependent brings in.

    A dependent's own version is part of the identity rather than context: a build
    where `oneagentgraph` moved but still resolved 0.10.1 is a different build, and
    a declaration that could not tell the two apart would go stale silently.
    """

    #: The crate that depends on it, as the SBOM names it.
    dependent: str
    #: That dependent's own release.
    dependent_version: str
    #: The core release it resolves.
    core: str


class ProseClaim(NamedTuple):
    """A sentence some document must spell, against a fact measured elsewhere."""

    #: The document, relative to the repository root.
    relative_path: str
    #: The sentence, as a format template over that measurement's own fields.
    template: str


#: The pin and the crate are separate artifacts on separate cadences, so no equality
#: between them would mean anything. Measured 2026-08-25 on this host's installed
#: wheels: `config/oneharness.version` reads 0.11.2 and names the `oneharness-cli`
#: wheel, whose own CycloneDX SBOM declares the `oneharness-core` it is compiled
#: against as 0.12.1. One wheel, its own library, two numbers.
UNRECONCILABLE_PIN = UnreconcilablePin(pin="oneharness", crate="oneharness-core")

#: What each dependent resolves that crate at in the adopted engine. The whole
#: mapping rather than a version, and rather than a floor, because the fact worth
#: gating is which dependent brings which — that is what says whether a fix landing
#: in `oneharness` reaches a dispatched member's agent side, its judge side, or
#: neither. A bare "they all agree" would pass a build where every dependent moved
#: together; a floor would pass one that dropped a dependent's core entirely. This
#: fails when any core moves, when a dependent stops bringing its own, or when a
#: third dependent appears — and it is the shape that survived the split collapsing,
#: because it never counted the cores in the first place.
LINKED_HARNESS_CORES = (
    LinkedCore(dependent="oneagentgraph", dependent_version="0.3.13", core="0.12.1"),
    LinkedCore(dependent="onejudge", dependent_version="0.6.2", core="0.12.1"),
)

#: Where an operator meets the CLI-versus-core reality, and the sentence that has to
#: name every half of it. Prose rather than only a comment for the reason the
#: divergence below has prose: the person who reads `config/oneharness.version` and
#: concludes the dispatched turn runs 0.11.2 is making the same mistake the whole of
#: this module is about. One fragment, spelled once per dependent, with every number
#: in it interpolated — a sentence that hardcoded the *dependent's* version would go
#: stale the day oneagentgraph moved without this gate saying so.
HARNESS_CORE_PROSE = ProseClaim(
    relative_path="AGENTS.md",
    template="`{dependent}` {dependent_version} brings `{crate}` {core}",
)

#: The pins that may not be reconciled today, each with the measured pair it was
#: declared against. A divergence is permitted only where the linked release is not
#: installable from PyPI at all — never as a convenience, and never as "not yet
#: looked at", because both read identically from here a release cycle later.
#:
#: **Empty, and kept.** The one entry this carried was `onejudge`, pinned at 0.4.0
#: against a linked 0.5.0 because the tag was published and the PyPI distribution was
#: not. Both halves of that ground went at once: the registry now carries every
#: `onejudge` from 0.5.0 to 0.6.2, and the adopted engine links 0.6.2, so there is nothing left to
#: except and the entry is retired rather than re-dated. What stays is the escape
#: hatch — `Divergence`, this registry, and the two tests that read it — because the
#: next adoption that meets an uninstallable linked release needs to declare one
#: without first rebuilding the machinery to do it in.
DECLARED_DIVERGENCES: dict[str, Divergence] = {}

#: Where a divergence is explained to an operator, and the sentence naming both of
#: its versions. Prose rather than only a comment, because the person who meets this
#: is a manager reading why two version files disagree, and the numbers in that
#: explanation go stale the same way every other restated measurement does.
#:
#: The template survives the registry being empty on purpose: it is parametrized over
#: the declaration rather than over a version literal, so a re-declared divergence
#: gets its prose gate back with no test to rewrite. What is *also* gated, below, is
#: that an empty registry leaves no such paragraph behind — an explanation of a
#: divergence that no longer exists is exactly the stale claim these gates are for.
DIVERGENCE_PROSE = (
    "AGENTS.md",
    "links the `onejudge` crate {linked}, and PyPI carries no "
    "`onejudge` {linked} — the tag is published and the distribution is not — so "
    "`config/onejudge.version` stays at {pinned}",
)


def _pinned_cli(version_file: str) -> str:
    """The release `config/<file>` adopts for a CLI this host runs itself."""
    return (REPO_ROOT / "config" / version_file).read_text(encoding="utf-8").strip()


def test_every_cli_pin_beside_a_linked_crate_is_reconciled_or_declared_unreconcilable() -> None:
    """A `config/<tool>.version` beside a linked crate is held to it, or says why not.

    The hole this closes is the one the module docstring's two incidents came
    through: a `config/<tool>.version` that nothing compares against the engine's own
    resolution reads as authoritative while a dispatch runs something else entirely.

    Asked from the *pin* side rather than the crate side, which is a widening and not
    a restatement. Crate-side, a pin only joined when the crate's name and the file's
    stem matched exactly — so `config/oneharness.version` sat beside a linked
    `oneharness-core` and was found by nothing, which is how the one pin here whose
    crate is linked twice went ungated while its three siblings did not. Matching a
    pin to `<pin>` or `<pin>-*` is what catches that near-miss, and a crate it finds
    has to be reconciled or be the declared unreconcilable one.
    """
    linked = _linked_versions()
    pins = sorted(path.stem for path in (REPO_ROOT / "config").glob("*.version"))
    covered = {
        pin: sorted(crate for crate in linked if crate == pin or crate.startswith(f"{pin}-"))
        for pin in pins
    }

    unreconciled = {
        pin: crates
        for pin, crates in covered.items()
        if crates and pin not in RECONCILED_PINS and pin != UNRECONCILABLE_PIN.pin
    }
    assert not unreconciled, (
        f"{ENGINE_DISTRIBUTION} links {unreconciled} and nothing here holds those pins to "
        "it; reconcile each against the engine's own resolution, or — where the crate is "
        "linked at more than one version — declare it as UNRECONCILABLE_PIN is"
    )
    assert covered.get(UNRECONCILABLE_PIN.pin) == [UNRECONCILABLE_PIN.crate], (
        f"config/{UNRECONCILABLE_PIN.pin}.version was declared unreconcilable because the "
        f"engine links {UNRECONCILABLE_PIN.crate} beside it, and it now sits beside "
        f"{covered.get(UNRECONCILABLE_PIN.pin)}; re-read whether the pin can join "
        "RECONCILED_PINS after all"
    )
    assert {pin: crates[0] for pin, crates in covered.items() if pin in RECONCILED_PINS} == {
        pin: pin for pin in RECONCILED_PINS
    }, (
        f"a pin in RECONCILED_PINS no longer names a crate of its own name: {covered}. "
        "Reconciliation compares one release to one release, so a pin whose crate is "
        "gone or renamed is holding nothing"
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

    With no divergence declared this asserts nothing, and the test below is the half
    that then does the work.
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


@pytest.mark.reads_docs
def test_no_divergence_is_explained_that_this_host_no_longer_has() -> None:
    """A retired divergence takes its paragraph with it.

    The other direction of the gate above, and the one that comes due on an adoption
    rather than on a bump: prose explaining why two version files disagree, left
    standing after they stopped disagreeing, tells a manager to distrust a pin that is
    now correct. That is worse than the stale claim it replaced, because it reads as
    a deliberate exception somebody thought about.

    Written against the *shape* of the explanation rather than against the retired
    onejudge numbers, so it also catches a future divergence whose registry entry was
    removed and whose paragraph was not.
    """
    relative_path, template = DIVERGENCE_PROSE
    written = " ".join((REPO_ROOT / relative_path).read_text(encoding="utf-8").split())
    # The invariant half of the template — everything up to the first interpolation —
    # is what a paragraph explaining any divergence has to spell.
    shape = template.split("{", 1)[0].strip()

    for crate in RECONCILED_PINS:
        if crate in DECLARED_DIVERGENCES:
            continue
        assert shape not in written, (
            f"{relative_path} still explains a divergence for {crate}, and "
            f"DECLARED_DIVERGENCES has none: config/{RECONCILED_PINS[crate]} equals what "
            "the adopted engine linked. Delete the paragraph rather than re-dating it"
        )


def test_the_engine_links_one_oneharness_core_for_every_dependent() -> None:
    """Which dependent brings which core — asserted as the mapping, not as a count.

    The turn-running engine is the one crate here that `config/oneharness.version`
    cannot answer for: that pin is a release of the *CLI* the wrapper scripts and the
    smoke spawn, and this is the library a dispatched member's turn runs through.

    Gated as the whole mapping because every cheaper shape is wrong in a way that
    matters. A floor passes a build that dropped a dependent's core entirely. A bare
    "they all agree" passes a build where every dependent moved together. What an
    operator needs to be told is which dependent brings which, because that is what
    says whether a fix landing in `oneharness` reaches a dispatched member's agent
    side, its judge side, or neither — and that question is the same one whether the
    dependents agree on a core or split over two, which is why this half of the gate
    survived the split collapsing untouched.
    """
    brought = _linked_by_dependent(UNRECONCILABLE_PIN.crate)

    assert brought == tuple(sorted(LINKED_HARNESS_CORES)), (
        f"the adopted engine resolves {UNRECONCILABLE_PIN.crate} as {brought}, and this "
        f"repository is written against {LINKED_HARNESS_CORES}. Re-read what a dispatched "
        f"turn runs through — `config/{UNRECONCILABLE_PIN.pin}.version` answers for the CLI "
        "and not for this — and update the pin table's oneharness row in the same change"
    )


def test_the_oneharness_pin_names_an_artifact_the_engine_does_not_link() -> None:
    """Why that pin can never join `RECONCILED_PINS`, asserted rather than asserted-of.

    This replaces a `len(cores) > 1` check that read the *count* of linked cores as
    the reason, and whose failure message told a reader who met one core to retire
    `UNRECONCILABLE_PIN` into `RECONCILED_PINS`. Re-measured 2026-08-25 on this host's
    installed wheels, that instruction is wrong: the engine does link one core now,
    and the pin still cannot be reconciled, because `config/oneharness.version` names
    the `oneharness-cli` wheel and the linked crate is `oneharness-core`. Those are
    separate artifacts on separate cadences — the installed CLI reads 0.11.2 and its
    own SBOM declares the core it is compiled against as 0.12.1 — so an equality
    between them would assert that two artifacts carry one number.

    So the property gated is the one that was always the real reason and was only ever
    approximated by the count: nothing named `oneharness` is a crate the engine links,
    so there is no release for that pin to equal. This fails on the day the engine
    starts linking such a crate, which is the one thing that would make reconciliation
    meaningful — and it goes on holding while the CLI and the core happen to carry the
    same number, which they have done before and which proves nothing either way.
    """
    linked = _linked_versions()
    reconcilable = {
        crate: sorted(versions)
        for crate, versions in linked.items()
        if crate == UNRECONCILABLE_PIN.pin
    }

    assert not reconcilable, (
        f"the adopted engine now links {reconcilable}, so config/"
        f"{UNRECONCILABLE_PIN.pin}.version finally has a release of its own artifact to "
        f"be held to. Re-read whether it can join RECONCILED_PINS — note that "
        f"{UNRECONCILABLE_PIN.crate} beside it is still a different artifact and is not "
        "what that pin names"
    )


@pytest.mark.reads_docs
def test_the_linked_oneharness_cores_are_named_where_an_operator_meets_them() -> None:
    """The prose naming each dependent's core is held to what this host measures.

    Same reason the declared divergence has prose: the failure is a manager reading one
    version file and concluding a fix is in force. `config/oneharness.version` is the
    most inviting of the six to read that way, because it is the only one whose number
    matches neither thing a dispatch runs — and that stayed true when the two linked
    cores collapsed into one, since the number the pin carries is still the CLI's.
    """
    claim = HARNESS_CORE_PROSE
    brought = _linked_by_dependent(UNRECONCILABLE_PIN.crate)
    written = " ".join((REPO_ROOT / claim.relative_path).read_text(encoding="utf-8").split())

    for resolved in brought:
        stated = claim.template.format(crate=UNRECONCILABLE_PIN.crate, **resolved._asdict())
        assert written.count(stated) == 1, (
            f"{claim.relative_path} states {stated!r} {written.count(stated)} times, not "
            f"once; the adopted engine links {brought}, and an operator meeting "
            "disagreeing oneharness numbers has to be told which one a dispatched turn runs"
        )


# Everything above is an *internal* consistency check: the pins in `config/` against
# the bill of materials of the wheel this host installed. It is silent about the one
# question an adoption starts from — whether that wheel is the newest the registry
# publishes — so a host a release cycle behind reads exactly like a host that is
# current, on every gate run, for as long as nobody thinks to look.
#
# So the reading is taken and *reported*, never enforced. Adopting a release is a
# manager's decision made between runs, on evidence about what the release changed; a
# gate that failed on the registry moving would fail this repository on somebody else's
# publish, with nothing in this tree to fix. The reading belongs in the uncached tier
# for the same reason `tests/test_credential_dialect_drift.py` does: it reconciles
# against state outside this workspace, and a memo keyed on this tree would replay a
# green across the very registry move it exists to notice.

#: Where a distribution's published releases are read from. The JSON API rather than
#: the simple index, because it names each release as its own key and needs no HTML.
REGISTRY_URL = "https://pypi.org/pypi/{distribution}/json"

#: How long this reading waits on the registry. Short deliberately: an unreachable
#: registry is one of this reading's ordinary answers, and a check that reports rather
#: than enforces has no business holding the tier open for a network.
REGISTRY_TIMEOUT_SECONDS = 20

#: The only release spellings this reading orders. A pre-release or a post-release is
#: skipped rather than parsed, because ordering it correctly is `packaging`'s job and
#: getting it wrong here would turn a report into a wrong report.
RELEASE_SPELLING = re.compile(r"^\d+\.\d+\.\d+$")


class RegistryReading(NamedTuple):
    """What the registry says about the wheel this host installed.

    ``newest`` is `None` when the registry could not be read at all, which is a third
    answer rather than "not behind": an unreachable registry has said nothing about the
    world, exactly as an unanswered release probe has not said a release is missing.
    """

    distribution: str
    installed: Release
    newest: Release | None
    unread: str | None

    @property
    def behind(self) -> bool:
        """Whether the registry publishes a release later than the installed one."""
        return self.newest is not None and self.newest > self.installed

    def sentence(self) -> str:
        """The reading, as the one line an operator is shown."""
        if self.newest is None:
            return (
                f"{self.distribution} {self.installed} is installed and the registry "
                f"could not be read ({self.unread}), so nothing here says whether a "
                "later release exists"
            )
        if self.behind:
            return (
                f"{self.distribution} {self.installed} is installed and PyPI publishes "
                f"{self.newest}; the pins in config/ are reconciled against the older "
                "wheel, so adopting is a decision to make between runs"
            )
        return f"{self.distribution} {self.installed} is installed and is the newest PyPI publishes"


class RegistryReadingWarning(UserWarning):
    """The category the reading is reported under, whichever of its three answers it is.

    Named for the reading rather than for one of its answers: the same category carries
    "current", "behind", and "the registry could not be read", and a reader filtering on
    a lag-shaped name would miss the two that are not lag.

    A warning rather than a failure or a bare `print`: pytest lists it in its own
    summary, so the reading reaches an operator reading a green tier — where a `print`
    reaches nobody without `-s` and a failure would fail this repository on somebody
    else's publish.
    """


def orderable_releases(distribution: str) -> tuple[tuple[Release, ...], str | None]:
    """The registry's releases this reading can order, and why it could not be read.

    Named for what it returns rather than for the registry's whole answer: a
    pre-release or a post-release is published and is deliberately not here, because
    ordering one correctly is `packaging`'s job and getting it wrong would turn a
    report into a wrong report.
    """
    try:
        with urllib.request.urlopen(  # noqa: S310 - a literal https URL, not caller input
            REGISTRY_URL.format(distribution=distribution), timeout=REGISTRY_TIMEOUT_SECONDS
        ) as answer:
            payload = json.loads(answer.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as unread:
        return (), f"{type(unread).__name__}: {unread}"
    releases = payload.get("releases")
    if not isinstance(releases, dict):
        return (), "the registry answered without a releases object"
    return (
        tuple(
            sorted(
                Release(*(int(part) for part in version.split(".")))
                for version in releases
                if RELEASE_SPELLING.match(version)
            )
        ),
        None,
    )


def registry_reading(
    distribution: str, installed: Release, published: tuple[Release, ...], unread: str | None
) -> RegistryReading:
    """Compose the reading from an installed release and what the registry published.

    Pure, and separate from the read above, so the three answers — current, behind, and
    unreadable — are each provable without a registry that happens to be in that state.
    """
    return RegistryReading(
        distribution=distribution,
        installed=installed,
        newest=published[-1] if published else None,
        unread=unread if published == () else None,
    )


# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] The marker is this
# repository's tier mechanism rather than a shortcut around one: it runs four tiers over
# one Nx project, keyed on four `nx.json` named inputs, and `reads_checkouts` is the
# selector for the *uncached* target `orchestrator:test-checkouts` — the tier that exists
# precisely because no key over this workspace can describe state outside it. A second Nx
# project would need its own key over the same nothing. `tests/test_nx_cache_scope.py`
# holds the four selectors to a partition of the suite, so a marker that stopped routing
# is a failing check rather than a test nothing runs.
@pytest.mark.reads_checkouts
def test_whether_the_installed_engine_wheel_is_behind_the_registry_is_read_and_reported() -> None:
    """Take the reading against the real registry, and report it without enforcing it.

    The gates above hold this host's pins to what the installed wheel linked and say
    nothing about whether that wheel is current — which is the question an adoption
    starts from. This asks it every time the uncached tier runs, and the only thing it
    asserts is that an answer was produced: being behind is news for a manager, not a
    failure of this tree, and an unreachable registry is a third answer rather than
    evidence that nothing newer exists.
    """
    installed = Release.parse(
        importlib.metadata.version(ENGINE_DISTRIBUTION), f"the installed {ENGINE_DISTRIBUTION}"
    )
    published, unread = orderable_releases(ENGINE_DISTRIBUTION)
    reading = registry_reading(ENGINE_DISTRIBUTION, installed, published, unread)

    warnings.warn(reading.sentence(), RegistryReadingWarning, stacklevel=1)

    assert reading.sentence(), "the registry reading produced no sentence to report"


# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]


@pytest.mark.parametrize(
    ("published", "unread", "behind", "fragment"),
    (
        (("0.17.5", "0.18.0"), None, True, "PyPI publishes 0.18.0"),
        (("0.17.5",), None, False, "is the newest PyPI publishes"),
        ((), "URLError: unreachable", False, "the registry could not be read"),
    ),
)
def test_a_registry_answer_is_reported_in_every_shape_and_fails_nothing(
    published: tuple[str, ...], unread: str | None, behind: bool, fragment: str
) -> None:
    """All three answers produce a reading, and none of them is a failure.

    Driven over a stated registry answer rather than the real one, because the answer
    that matters most — the installed wheel being behind — is the one this host is not
    in today and cannot be put into. Reporting is asserted the way an operator meets
    it: the warning is raised and caught here, which is a failure of nothing.
    """
    installed = Release.parse("0.17.5", "this journey")
    reading = registry_reading(
        ENGINE_DISTRIBUTION,
        installed,
        tuple(Release.parse(version, "this journey") for version in published),
        unread,
    )

    assert reading.behind is behind, reading
    assert fragment in reading.sentence(), reading.sentence()

    with warnings.catch_warnings(record=True) as reported:
        warnings.simplefilter("always")
        warnings.warn(reading.sentence(), RegistryReadingWarning, stacklevel=1)

    assert [str(one.message) for one in reported] == [reading.sentence()], (
        "the reading has to reach an operator as a report; a behind-the-registry answer "
        "that failed the tier would fail this repository on somebody else's publish"
    )
