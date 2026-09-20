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


#: The onevcs release that let a session open its own change request **as a draft it
#: holds**, describe it after it exists, and lift the draft as a verb —
#: https://github.com/nickderobertis/onevcs/pull/138, cut as 0.21.0. Below it a
#: lifecycle worker granted `config/dispatch-appendix.md`'s early-publication carve-out
#: has no `onevcs publish "$ONEVCS_SESSION" --draft` to run: the linked copy is what a
#: dispatch publishes through, and a `--draft` it does not know is refused by the verb
#: while every version file on this host reads current. What the floor buys is that the
#: carve-out the appendix grants, the criteria `just check-plan` admits under it, and the
#: drafter that finishes a worker's description are all about a verb the worker's own
#: dispatch can reach — and that a bump to `config/onepipeline.version` resolving an
#: older onevcs fails here rather than at a worker's first `publish --draft`.
SESSION_DRAFT_FLOOR = Release(0, 21, 0)
#: The oneharness-core release that runs a controlled codex turn under its candidate's
#: own model — https://github.com/nickderobertis/oneharness/pull/1284, cut as
#: `oneharness-core` 0.13.0 — and refuses one the server would run elsewhere. Below it
#: the control path was handed the run-level model alone, so a side's
#: `[harness.codex].model` reached the record and never `thread/start`, and codex ran
#: the default its own config named: on this host Astra at roughly ten times the weekly
#: quota per token, recorded as Sol throughout. Every codex-first supervisory side here
#: takes `--control`, so what this floor buys is a dispatched codex turn under
#: `--control` running the model its side's config names, the server's own answer
#: recorded beside it as `observed_model`, and a `model_mismatch` refusal — falling
#: through a chain as `model-mismatch`, before any token is spent — in place of a
#: silent substitution.
#:
#: Held on the *linked* core rather than on `config/oneharness.version`, because that
#: pin is the CLI the wrapper scripts and the smoke spawn, and a dispatched member's
#: turn goes through the core its engine links — a bump to `config/onepipeline.version`
#: that resolved an older core would put every supervisory side back on the misrouted
#: path while every version file on the host read current.
CONTROL_MODEL_CORE_FLOOR = Release(0, 13, 0)


#: The four sibling releases carrying the root-causes plan's linked-library work, each
#: established by reading that library's own source at the release rather than by the
#: number: the change request, the commit it landed as, and the symbol the source
#: carries from that release on. A floor is what holds the adoption from regressing
#: while every pin still reads current — the reading is what put the floor here.
#:
#: onevcs https://github.com/nickderobertis/onevcs/pull/136 (`8cb92450`), first cut as
#: 0.20.0: `store::VERSION` is 6 and `INFERRED_KEYS` drops `workflow` and `repo_type`,
#: `integrate::run` refuses on the resolved `MergePolicy` rather than on either, and
#: `host::RemoteHost::required_checks_on` reads an identity's required checks off the
#: host for `repos --audit-gates`.
POLICY_GATED_ONEVCS_FLOOR = Release(0, 20, 0)
#: oneagentgraph https://github.com/nickderobertis/oneagentgraph/pull/95 (`ba636b5b`),
#: first cut as 0.3.16: `event::Origin` — `task`, `supervisor`, `delivered` — and
#: `judge::opening_origin`, which stamps a worker turn's opening with it.
TURN_PROVENANCE_FLOOR = Release(0, 3, 16)
#: onejudge https://github.com/nickderobertis/onejudge/pull/74 (`464abaed`), first cut
#: as 0.8.0: `provider::SUPERVISOR_REASK_LIMIT` and `SupervisorOutcome::Unparseable`,
#: which re-ask a supervisor whose answer nothing could act on instead of failing the
#: member on it.
SUPERVISOR_REASK_FLOOR = Release(0, 8, 0)
#: onejudge https://github.com/nickderobertis/onejudge/pull/80 (`eacb4cf9`), first cut as
#: 0.9.0, and https://github.com/nickderobertis/onejudge/pull/82 (`12ed44a5`), cut as
#: 0.10.0: `JudgePanel` behind `judges:`, with `judge:` its one-element shorthand;
#: `LlmlintProvider` behind a judge entry's `kind: llmlint`; and `JudgedTurn` /
#: `JudgeDecision` on the report's `judge_decisions`.
JUDGE_PANEL_FLOOR = Release(0, 10, 0)
#: oneharness https://github.com/nickderobertis/oneharness/pull/1286 (`66e868f5`), cut
#: as `oneharness-core` 0.13.1 and `oneharness-cli` 0.12.1: `fallback.rs` decides what
#: an unclassified failure stops and `report::FallbackReport::stopped_without_work`
#: attributes it, so a chain that stopped on a candidate with nothing to show for
#: itself is told apart from one that stopped after work.
CHAIN_CLASSIFICATION_CORE_FLOOR = Release(0, 13, 1)
#: oneharness https://github.com/nickderobertis/oneharness/issues/1290, first linked by
#: onepipeline in #314 and driven through its real dispatch graph in #319: a Claude
#: `Not logged in` refusal is an authentication failure, not a rate limit. This is the
#: operator-facing distinction between authenticating the named configuration directory
#: and waiting for quota that was never exhausted. The installed-engine journey in
#: `tests/e2e/test_claude_identity_routing_e2e.py` drives the linked classifier through
#: a single-sided node graph and holds the host-visible verdict to that distinction.
CLAUDE_LOGIN_CLASSIFICATION_CORE_FLOOR = Release(0, 13, 2)


#: The three sibling releases carrying https://github.com/nickderobertis/ai-orchestrator/issues/1004's
#: linked-library fixes, which the engine linked in
#: https://github.com/nickderobertis/onepipeline/pull/283 (`ed6e188`), first cut as 0.29.3.
#:
#: onevcs https://github.com/nickderobertis/onevcs/pull/143 (`f7f9a98`), first cut as
#: 0.22.0: `host::Check::no_verdict`, which reads a required check the host completed
#: `cancelled` or `stale` as no verdict rather than as red, so a publication over one
#: settles `checks-unsettled`; and `ReleaseSource::Acknowledged`, the answer a
#: `release acknowledge` on an automated landing with no baseline records.
NO_VERDICT_ONEVCS_FLOOR = Release(0, 22, 0)
#: onejudge https://github.com/nickderobertis/onejudge/pull/86 (`3420b44`), first cut as
#: 0.11.0: `SimulatedUser::artifacts` behind `user.artifacts`, and `provider::artifacts_prompt`,
#: which names each resolved path in every judge-side prompt.
NAMED_ARTIFACTS_ONEJUDGE_FLOOR = Release(0, 11, 0)
#: oneagentgraph https://github.com/nickderobertis/oneagentgraph/pull/111 (`18daa25`), first
#: cut as 0.4.1: the release linking that onejudge, which is what lets a `kind: onejudge`
#: member's `user.artifacts` reach its judge.
ARTIFACTS_ONEAGENTGRAPH_FLOOR = Release(0, 4, 1)


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
    # The engine's own crate is the document's subject rather than one of its
    # components, and from onepipeline 0.37.0 it depends on the core directly.
    subject = document.get("metadata", {}).get("component", {})
    if "bom-ref" in subject:
        components.setdefault(subject["bom-ref"], {**subject, "name": "onepipeline"})
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
        # Which onevcs the lifecycle page's engine-behaviour claims were read at. Every
        # one of them is about the copy a *dispatched* node publishes through, so the
        # linked version is the only one that answers for them — and `DRAIN_SECONDS`
        # becoming `DRAIN` between 0.4.2 and 0.8.0 is what this gate is for: the
        # constant gate below caught the rename, and nothing held the header sentence
        # that had just stopped naming the release the rename happened in.
        "docs/repo-lifecycle.md": (
            "the **`onevcs` {version}** its `Cargo.lock` resolves",
            # This site used to be a *denial* — "`onevcs` {version} has none to open" —
            # and onevcs 0.18.0 made it false: a publication can now answer
            # `PublishOutcome::ChangeDraft`, which onepipeline settles a node
            # `complete-but-draft` on. The gated sentence moved with the fact rather than
            # being deleted, because the paragraph's conclusion did not move — a pause
            # still opens nothing — and a reader who was told the reason had gone would
            # otherwise have no way to tell that from the conclusion having gone too.
            "`onevcs` {version} answers `PublishOutcome::ChangeDraft`",
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


def test_the_linked_oneharness_core_runs_a_controlled_turn_under_its_own_model() -> None:
    """Above the floor, a controlled codex turn runs the model its side's config names.

    The regression this stops is the one that cost this host two Pro identities' weekly
    windows in about a day each, and it is invisible from every pin in `config/`: the
    record says the configured model whether or not the wire carried it, and
    `config/oneharness.version` answers for the CLI the smoke spawns rather than for the
    core a dispatched member's turn goes through. Only the linked release says which
    path a dispatch is on. The property itself is driven for real, offline, by
    `tests/e2e/test_controlled_turn_model_e2e.py`; this holds the engine to a core that
    can pass it.
    """
    linked = Release.parse(_linked_version(UNRECONCILABLE_PIN.crate))

    assert linked >= CONTROL_MODEL_CORE_FLOOR, (
        f"the adopted engine links {UNRECONCILABLE_PIN.crate} {linked}, below the "
        f"{CONTROL_MODEL_CORE_FLOOR} that runs a controlled codex turn under its "
        "candidate's own model; every codex-first supervisory side dispatched under it "
        "would run the server's own default while its record named the configured one"
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


def test_the_linked_onevcs_lets_a_session_open_its_own_draft() -> None:
    """Above the floor, the carve-out a task grants names a verb the dispatch can run.

    Asserted as a floor on the *linked* onevcs rather than on `config/onevcs.version`,
    which is the CLI the manager verbs run and the one a worker types in its session
    worktree: the closeout that reads a worker's draft back and finishes its description
    runs through the copy the engine links, and a `publish --draft` the linked copy
    refused would leave a granted worker with a carve-out it cannot use and a closeout
    that never sees a draft to finish.
    """
    linked = Release.parse(_linked_version("onevcs"))

    assert linked >= SESSION_DRAFT_FLOOR, (
        f"the adopted engine links onevcs {linked}, below the {SESSION_DRAFT_FLOOR} that "
        "lets a session open its change request as a draft it holds; a worker granted the "
        "appendix's early-publication carve-out would have no verb to publish its draft "
        "through, and the closeout no draft to finish"
    )


def test_the_linked_onejudge_lets_a_workers_judge_side_be_a_list() -> None:
    """Above the floor, a graph naming `judges:` reaches a onejudge that can run it.

    What the floor buys is three things a dispatched member's own onejudge has to carry:
    a judge side that can be a list, its judges run concurrently with the failing ones'
    messages combined and attributed; an `llmlint` judge kind; and per-judge decisions on
    the report. Held on the *linked* copy because that is what a dispatch settles on — a
    bump to `config/onepipeline.version` that resolved an older onejudge would leave every
    pin on this host reading current, and would fail only when a graph first named
    `judges:`, as a config error on a member. `tests/e2e/test_judge_panel_e2e.py` drives
    the property itself through the pinned CLI; this holds the engine to a onejudge that
    has it.
    """
    linked = Release.parse(_linked_version("onejudge"))

    assert linked >= JUDGE_PANEL_FLOOR, (
        f"the adopted engine links onejudge {linked}, below the {JUDGE_PANEL_FLOOR} that "
        "lets a judge side be a list, adds the `llmlint` judge kind, and records per-judge "
        "decisions on the report; a graph naming `judges:` would be refused at dispatch"
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
    # The `onemessagebus-cli` wheel and the `onemessagebus` crate share one workspace
    # version, so the engine's SBOM names the release of the bus CLI the host installs.
    "onemessagebus": "onemessagebus.version",
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


#: The pin and the crate are separate artifacts on separate cadences, so no equality
#: between them would mean anything. Measured 2026-09-18 on this host's installed
#: wheels: `config/oneharness.version` reads 0.12.1 and names the `oneharness-cli`
#: wheel, whose own CycloneDX SBOM declares the `oneharness-core` it is compiled
#: against as 0.13.1, while the engine wheel now links 0.14.1. They are independently
#: released artifacts, so the mismatch is expected and equality would assert no contract.
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
    LinkedCore(dependent="oneagentgraph", dependent_version="0.4.5", core="0.14.1"),
    LinkedCore(dependent="onejudge", dependent_version="0.13.2", core="0.14.1"),
    LinkedCore(dependent="onepipeline", dependent_version="0.39.0", core="0.14.1"),
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
#: hatch — `Divergence`, this registry, and the pin gate that reads it — because the
#: next adoption that meets an uninstallable linked release needs to declare one
#: without first rebuilding the machinery to do it in.
DECLARED_DIVERGENCES: dict[str, Divergence] = {}


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
    separate artifacts on separate cadences — the installed CLI read 0.11.2 and its
    own SBOM declared the core it is compiled against as 0.12.1 — so an equality
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


#: The sibling CLI wheels this host installs beside the engine, each of which links
#: `oneharness-core` on its own account. Named rather than derived because what is being
#: reconciled is a claim about *these* artifacts: the standalone CLI a recipe spawns is
#: published from the same repository as the crate the engine links, and is not evidence
#: about it.
SIBLING_CLI_DISTRIBUTIONS = ("oneagentgraph-cli", "onejudge-cli")


def _sibling_core(distribution: str) -> str:
    """The `oneharness-core` one installed sibling CLI wheel was compiled against."""
    installed = importlib.metadata.distribution(distribution)
    sboms = [entry for entry in (installed.files or ()) if SBOM_DIRECTORY in Path(entry).parts]
    assert len(sboms) == 1, (
        f"{distribution} must ship exactly one SBOM for the core it was built against to "
        f"be read from; found {[str(entry) for entry in sboms]}"
    )
    document = json.loads(Path(str(installed.locate_file(sboms[0]))).read_text("utf-8"))
    declared = {
        component["version"]
        for component in document["components"]
        if component["name"] == UNRECONCILABLE_PIN.crate
    }
    assert len(declared) == 1, (
        f"{distribution}'s SBOM declares {UNRECONCILABLE_PIN.crate} at "
        f"{sorted(declared) or 'no version'}; this repository's prose compares one "
        "version of it against the engine's, which nothing here can reconcile against that"
    )
    return declared.pop()


def test_a_siblings_own_cli_wheel_is_not_evidence_about_what_a_dispatch_runs() -> None:
    """Each sibling CLI's own core, against the one the engine resolved for that crate.

    The sentence this backs is the sharpest form of the whole module's subject: the
    `oneagentgraph` and `onejudge` a dispatch runs are compiled *into the engine wheel*,
    and this host also installs each of them as a standalone CLI whose wheel resolved its
    own `oneharness-core`. Those two numbers are free to differ and, on this adoption,
    do — so a reader who measured the sibling's own wheel would be measuring an artifact
    no dispatch loads.

    Written as the pairing rather than as an inequality. An inequality would go green on
    a build where both moved together, which is the reading it exists to deny, and would
    fail the day the two coincide — a coincidence, not a regression. What fails here is
    either number moving, which is the prompt to re-read the paragraph that quotes them.
    """
    engine = {resolved.dependent: resolved.core for resolved in LINKED_HARNESS_CORES}
    measured = {
        distribution: (
            _sibling_core(distribution),
            engine[distribution.removesuffix("-cli")],
        )
        for distribution in SIBLING_CLI_DISTRIBUTIONS
    }

    assert measured == {
        "oneagentgraph-cli": ("0.14.0", "0.14.1"),
        "onejudge-cli": ("0.14.0", "0.14.1"),
    }, (
        f"this host measures (sibling CLI wheel's own core, engine's core) as {measured}, "
        "not the pair this check was written against. Re-read what is installed now and "
        "update the pair in the same change"
    )


@pytest.mark.parametrize(
    ("crate", "floor", "carries"),
    [
        (
            "onevcs",
            POLICY_GATED_ONEVCS_FLOOR,
            "gates the merge train on the resolved policy and reads required checks off the host",
        ),
        ("oneagentgraph", TURN_PROVENANCE_FLOOR, "stamps every turn with who authored it"),
        (
            "onejudge",
            SUPERVISOR_REASK_FLOOR,
            "re-asks an unparseable supervisor answer instead of failing the member",
        ),
        (
            UNRECONCILABLE_PIN.crate,
            CLAUDE_LOGIN_CLASSIFICATION_CORE_FLOOR,
            "classifies a Claude login refusal as authentication rather than rate limiting",
        ),
    ],
    ids=("onevcs", "oneagentgraph", "onejudge", UNRECONCILABLE_PIN.crate),
)
def test_the_linked_sibling_carries_this_plans_change_to_it(
    crate: str, floor: Release, carries: str
) -> None:
    """Each linked sibling is at or past the release whose source carries the change.

    The number is the gate; the reading is the evidence, and it is written beside each
    floor above — the change request, its commit, and the symbols the source carries
    from that release on. Held on the *linked* copy because that is what a dispatch runs:
    an engine that reconciled perfectly against unfixed siblings would leave every pin
    reading current with none of this in force, which is the failure this repository
    has recorded twice.
    """
    linked = Release.parse(_linked_version(crate))

    assert linked >= floor, (
        f"the adopted engine links {crate} {linked}, below the {floor} that {carries}; "
        "the pins reconcile and the change is not in force"
    )


@pytest.mark.parametrize(
    ("crate", "floor", "carries"),
    [
        (
            "onevcs",
            NO_VERDICT_ONEVCS_FLOOR,
            "reads a cancelled required check as no verdict and accepts an acknowledged baseline",
        ),
        (
            "onejudge",
            NAMED_ARTIFACTS_ONEJUDGE_FLOOR,
            "names the `user.artifacts` a judge reads directly",
        ),
        (
            "oneagentgraph",
            ARTIFACTS_ONEAGENTGRAPH_FLOOR,
            "links the onejudge that knows `user.artifacts`",
        ),
    ],
    ids=("onevcs", "onejudge", "oneagentgraph"),
)
def test_the_linked_sibling_carries_the_issue_1004_fix_to_it(
    crate: str, floor: Release, carries: str
) -> None:
    """Each linked sibling is at or past the release carrying that plan's fix to it.

    Held on the *linked* copy for the reason the test above gives: the CLI pins can all
    read current over an engine that resolved the siblings from before these fixes, and a
    dispatch would then spend a publication on a cancelled check and hand a judge no
    artifact while nothing on this host said so.
    """
    linked = Release.parse(_linked_version(crate))

    assert linked >= floor, (
        f"the adopted engine links {crate} {linked}, below the {floor} that {carries}; "
        "the pins reconcile and the fix is not in force"
    )


def test_the_installed_oneharness_cli_is_built_from_a_core_carrying_the_chain_classification() -> (
    None
):
    """The CLI the wrapper scripts and the smoke spawn is held to the same core floor.

    It is a separate artifact from the core the engine links — `config/oneharness.version`
    names the `oneharness-cli` wheel — so the engine's floor says nothing about it, and
    it is read from that wheel's own SBOM the way the sibling CLIs are read below.
    """
    built_from = Release.parse(_sibling_core("oneharness-cli"), "oneharness-cli's SBOM")

    assert built_from >= CHAIN_CLASSIFICATION_CORE_FLOOR, (
        f"the installed oneharness-cli is built from {UNRECONCILABLE_PIN.crate} {built_from}, "
        f"below the {CHAIN_CLASSIFICATION_CORE_FLOOR} that decides and attributes what an "
        "unclassified fallback failure stops; a wrapper-spawned turn's stopped chain would "
        "read as it did before"
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
