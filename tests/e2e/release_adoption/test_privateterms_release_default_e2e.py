"""A node behind privateterms waits on its published `crate`, by this host's tracked override.

`config/onevcs.releases.yml` gives privateterms the default target `crate`, the Rust crate
the tools that detect or rewrite private terms build against, and
`orchestrator/host_installs.py` names it a consumer default this host installs nothing of.
That is what a `published` node depending on privateterms' work and naming no `consumes`
waits for, and a privateterms base that stops declaring it is what the release-contradiction
check refuses. `tests/e2e/publish_and_landing/test_release_adoption_in_force_e2e.py` reads the
same resolution off this host's own registry once `just repos-apply` has installed the
override; this journey proves it on any host, from a scratch state root the same recipe
installs the tracked override into.

It runs in the `release-adoption` project of its own, keyed on the override, the registry
installer and the support it imports, so an unrelated change never re-runs its real
registration and publications.

llmlint: ignore-file[e2e_not_mocked] The fake `ssh` `scratch_identity` serves a hosted origin
through stands in for GitHub's transport alone, which a test may not reach; the recipe,
`onevcs`, git and every fetch and push are real.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NotRequired, TypedDict, cast

import pytest
from scratch_identity import (
    RELEASES,
    Identity,
    land,
    onevcs_in,
    release_status,
    seeded_producer,
)
from sibling_facts import (
    check_release_contradictions,
    commit_change,
    declaration,
    default_target_complaint,
    override_default_targets,
    published_clone,
)

from orchestrator.host_installs import CONSUMER_DEFAULTS, by_producer
from orchestrator.root import REPO_ROOT

#: The override this host tracks, which the recipe installs into every scratch state root.
TRACKED_OVERRIDE = REPO_ROOT / "config" / "onevcs.releases.yml"
#: privateterms as `onevcs` files it, spelled out rather than read from the override so the
#: claim is stated independently of the configuration under test, with the target the
#: override names and the artifact id the controlled declaration carries it under.
PRIVATETERMS = "github.com/nickderobertis/privateterms"
PRIVATETERMS_TARGET = "crate"
PRIVATETERMS_ARTIFACT = "crate:privateterms"
#: The rung the override leaves every producer on: `onevcs`'s own default.
GLOBAL_RUNG = "fast"


class ReleaseTargets(TypedDict):
    """The `onevcs release targets --json` answer, as far as this journey reads it.

    `default_target` is present only where the override resolves one, which is itself
    the subject: an override naming no default for privateterms answers without the key.
    """

    identity: str
    adoption: str
    default_target: NotRequired[str]
    targets: list[dict[str, object]]


def test_the_tracked_override_holds_a_node_behind_privateterms_on_its_crate(
    tmp_path: Path,
) -> None:
    """privateterms resolves `crate` as its default, and a landing there is held unreleased.

    privateterms is registered under its hosted origin and declares `crate` at its base,
    landed through `onevcs publish-branch`. The tracked override resolves that `crate` as
    the default a node naming no `consumes` waits on, and once a change lands there
    `release status` — named no target, so the default decides — answers not released
    while no release exists: the answer a `published` node depending on privateterms is
    held on.
    """
    assert by_producer(PRIVATETERMS) is None and PRIVATETERMS in CONSUMER_DEFAULTS
    identity = seeded_producer(
        tmp_path, PRIVATETERMS, declaration({PRIVATETERMS_TARGET: PRIVATETERMS_ARTIFACT})
    )
    assert (identity.home / RELEASES).read_bytes() == TRACKED_OVERRIDE.read_bytes()

    asked = onevcs_in(identity, "release", "targets", PRIVATETERMS, "--json")
    assert asked.returncode == 0, asked.stdout + asked.stderr
    # `cast` and no runtime validation, because the installed `onevcs`'s own answer is the
    # subject: a key it lost fails the reads below as the drift it is, naming the key.
    resolved = cast(ReleaseTargets, json.loads(asked.stdout))
    assert resolved["adoption"] == GLOBAL_RUNG, resolved
    assert resolved.get("default_target") == PRIVATETERMS_TARGET, resolved
    complaint = default_target_complaint(
        PRIVATETERMS,
        resolved.get("default_target"),
        override_default_targets(TRACKED_OVERRIDE.read_text(encoding="utf-8")),
    )
    assert complaint is None, complaint

    branch = "feat/specification"
    land(identity, branch, {"spec.md": "the specification\n"})
    held = release_status(identity, branch)
    assert held == {"state": "not-released", "at_landing": {"state": "no-release"}, "now": ""}, held
    assert release_status(identity, branch, "--target", PRIVATETERMS_TARGET) == held


def test_privateterms_declaring_no_target_contradicts_the_override(tmp_path: Path) -> None:
    """The release-contradiction check refuses a privateterms base that declares nothing.

    `tests/sibling_facts.py`'s `check_release_contradictions`, the callable the merge path
    runs over this host's tracked checkout list, handed instead each of two controlled
    registrations' lists and a gated clone whose change edits the release override, each
    producer read at the fetched base its publication left: one declaring `crate` passes,
    and one declaring no target refuses the change, naming privateterms and the `crate`
    the override names for it, because a `published` node behind it would wait on a
    target its producer does not declare.
    """
    defaults = override_default_targets(TRACKED_OVERRIDE.read_text(encoding="utf-8"))
    assert defaults[PRIVATETERMS] == PRIVATETERMS_TARGET
    clone = published_clone(tmp_path / "gated")
    commit_change(clone, "config/onevcs.releases.yml")

    def gate(identity: Identity) -> None:
        # llmlint: ignore[tests_mirror_real_usage] The merge-path gate is a pytest test bound to the tracked checkout list and REPO_ROOT, so this shared callable is its only entry point a scratch registration can be handed.  # noqa: E501
        check_release_contradictions(
            identity.home.parent / "onevcs.checkouts", root=clone, comparison={}
        )

    declared = declaration({PRIVATETERMS_TARGET: PRIVATETERMS_ARTIFACT})
    gate(seeded_producer(tmp_path / "declaring", PRIVATETERMS, declared))

    with pytest.raises(pytest.fail.Exception) as refused:
        gate(seeded_producer(tmp_path / "silent", PRIVATETERMS, None))
    assert "this change edits config/onevcs.releases.yml" in str(refused.value)
    assert (
        f"{PRIVATETERMS} declares no release targets at its base, and "
        f"config/onevcs.releases.yml names {PRIVATETERMS_TARGET!r} as its default target"
    ) in str(refused.value)
