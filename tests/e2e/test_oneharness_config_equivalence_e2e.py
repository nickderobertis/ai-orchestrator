"""Every role's effective oneharness configuration, held to a committed record.

The ten `oneharness.<role>.toml` files state their identities through `extends`, so
what each one resolves to is a claim about a chain rather than about a file. This
module holds every role's resolved configuration to a committed record under
`oneharness_resolved/`, and holds each variant's reported credential mask as a property
of its own, so a routing change is a visible update to that record in the same commit.

It reads what the real `oneharness config` resolves rather than the files' text,
because a layered config is only as correct as what the CLI makes of it.

**A resolved configuration is not evidence about a turn**, and this module is only the
first half. On the pinned release the report and the run disagree about `unset_env`: an
inherited mask is reported as `[]` whenever the child mentions the variant, which is why
several files restate masks they would inherit anyway. So a green record here says the
reported isolation is right, not that any provider was masked —
`tests/e2e/test_dispatch_environment_e2e.py` drives a real turn per identity and reads
the provider's own environment, and that is what answers the second half. Read the two
together. `docs/onejudge-integration.md` states the merge rule and the reporting defect.

The records are host-independent: `env_from` holds variable names rather than paths, and
the config-file list and every `source` annotation are dropped, since a value moving
between layers legitimately changes where it is reported from.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, TypedDict

import pytest
from harness_configs import extends_chain, harness_routing
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The committed effective configurations, one per role config, named for it.
RECORDS = Path(__file__).parent / "oneharness_resolved"

#: Every role config this repository ships: the files declaring a `harnesses` chain,
#: which is what a turn can run against. Read off the TREE rather than off the records,
#: so a role added without one fails below instead of going untested.
ROLE_CONFIGS = tuple(
    sorted(
        path.name
        for path in REPO_ROOT.glob("oneharness*.toml")
        if "harnesses" in harness_routing(path)
    )
)

#: The shared parents, which declare no chain and are reached only through a role.
SHARED_PARENTS = ("oneharness.identities.toml", "oneharness.dispatch.toml")

#: The three sides the engine starts inside a node's dispatch, and so the only ones
#: with a node scratch directory for `oneharness.dispatch.toml`'s repoint to name.
DISPATCHED_ROLES = ("oneharness.toml", "oneharness.judge.toml", "oneharness.follow-up.toml")

#: The board credential, and the three roles whose turn must still carry it: the
#: design-document pair, whose composed task reads the plan out of the store, and the
#: follow-up agent, whose whole deliverable is writing the `followups` board. Every
#: other role masks it. What is held here is that the layering did not move which side
#: of that line a role sits on.
BOARD_CREDENTIAL = "GH_PROJECTS_TOKEN"
PLAN_STORE_CONFIGS = (
    "oneharness.design-doc.toml",
    "oneharness.design-doc-judge.toml",
    "oneharness.follow-up.toml",
)

#: The credentials each harness family's variants must refuse to pass on, whatever role
#: they are routed for. Stated as the property rather than read back out of the records,
#: so this test still fails on a mask emptied in a shared parent after somebody has
#: regenerated them. `codex:primary` is deliberately absent from the codex list: it
#: honours an ambient `CODEX_HOME`, which AGENTS.md explains, and masks nothing but the
#: board credential.
ANTHROPIC_CREDENTIALS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_REFRESH_TOKEN",
)
CODEX_ALTERNATE_CREDENTIAL = "OPENAI_API_KEY"


class ReportedMask(TypedDict):
    """A variant's resolved `unset_env`, whose `value` is null when it masks nothing."""

    value: list[str] | None


class Variant(TypedDict, total=False):
    """One authentication variant of a harness, narrowed to what is read here."""

    unset_env: ReportedMask


class Harness(TypedDict, total=False):
    """One harness's resolved routing, narrowed to its variants."""

    variant: dict[str, Variant]


class EffectiveConfig(TypedDict, total=False):
    """One role's resolved configuration, narrowed to the keys this module consults.

    `oneharness` owns the whole schema and is what validates it; these are the two keys
    read here — the per-variant masks, and the file list dropped before comparing.
    """

    harness: dict[str, Harness]
    config_files: list[str]


def _effective_config(oneharness_bin: str, config: Path) -> EffectiveConfig:
    """Ask oneharness what a run loading exactly `config` would actually use.

    `ONEHARNESS_*` is dropped because every worker verifies itself by running this
    suite from inside a dispatch, where an inherited `ONEHARNESS_TIMEOUT` or
    `ONEHARNESS_HISTORY_LABELS` beats every file — so the suite would be reading the
    enclosing dispatch's routing instead of the one under test.
    """
    env = {key: value for key, value in os.environ.items() if not key.startswith("ONEHARNESS_")}
    resolved = subprocess.run(
        [oneharness_bin, "config", "--config", str(config), "--compact"],
        env=env,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
    )
    assert resolved.returncode == 0, f"{config} did not resolve: {resolved.stderr}"
    reported: EffectiveConfig = json.loads(resolved.stdout)
    return reported


def _without_sources(node: Any) -> Any:
    """Strip every `source` annotation so two effective configs compare by value.

    The annotations name the FILE each value came from, and moving a value into a
    shared parent is exactly what this change does — so leaving them in would report
    every inherited value as a difference and say nothing about the routing.
    """
    match node:
        case dict():
            return {key: _without_sources(value) for key, value in node.items() if key != "source"}
        case list():
            return [_without_sources(value) for value in node]
        case _:
            return node


def _comparable(resolved: EffectiveConfig) -> dict[str, Any]:
    """One effective config, reduced to what is a claim about this repository's routing."""
    stripped: dict[str, Any] = _without_sources(resolved)
    stripped.pop("config_files", None)
    return stripped


def _variants(resolved: EffectiveConfig) -> dict[tuple[str, str], list[str]]:
    """Each `(harness, variant)` this config resolves, mapped to its reported mask."""
    return {
        (harness_id, variant_id): variant.get("unset_env", {}).get("value") or []
        for harness_id, harness in resolved.get("harness", {}).items()
        for variant_id, variant in harness.get("variant", {}).items()
    }


def _inherited(config: Path) -> tuple[str, ...]:
    """The file names `config` inherits from, nearest parent first.

    Resolved through `harness_configs`, which owns that walk for this suite: the role
    config itself heads the chain it returns, and what is read here is what follows it.
    """
    return tuple(path.name for path in extends_chain(config)[1:])


def test_every_role_has_a_committed_record() -> None:
    """No role config is silently untested because nobody captured its record."""
    missing = [config for config in ROLE_CONFIGS if not (RECORDS / f"{config}.json").is_file()]
    assert not missing, (
        f"these role configs ship with no committed record, so nothing below compares "
        f"them: {missing}. Capture each with `oneharness config --config <file> "
        f"--compact`, dropping `config_files` and every `source`."
    )

    orphaned = [
        record.name
        for record in RECORDS.glob("*.json")
        if record.name.removesuffix(".json") not in ROLE_CONFIGS
    ]
    assert not orphaned, f"these records name no role config this repository ships: {orphaned}"


def test_every_role_inherits_from_the_shared_parents() -> None:
    """Each role reaches the identities through the layering, rather than restating it.

    The chain is resolved rather than searched for, because a `extends` key that names
    the wrong file, or a role that keeps the keyword while pointing somewhere else,
    would satisfy any textual check while inheriting none of the identities.
    """
    for parent in SHARED_PARENTS:
        assert (REPO_ROOT / parent).is_file(), f"{parent} is missing"

    chains = {config: _inherited(REPO_ROOT / config) for config in ROLE_CONFIGS}

    wrong_root = {
        config: chain
        for config, chain in chains.items()
        if not chain or chain[-1] != "oneharness.identities.toml"
    }
    assert not wrong_root, (
        f"every role's chain must end at `oneharness.identities.toml`, which is where "
        f"the six identities are stated; these do not: {wrong_root}"
    )

    unreferenced = [
        parent for parent in SHARED_PARENTS if not any(parent in chain for chain in chains.values())
    ]
    assert not unreferenced, (
        f"these shared parents are reached by no role, so nothing proves what they "
        f"carry: {unreferenced}"
    )

    # The dispatched sides are the ones with a node scratch directory to repoint at, so
    # they are exactly the roles that go through `oneharness.dispatch.toml`.
    through_dispatch = {
        config for config, chain in chains.items() if "oneharness.dispatch.toml" in chain
    }
    assert through_dispatch == set(DISPATCHED_ROLES), (
        f"the roles inheriting the runtime-directory repoint are {sorted(through_dispatch)}, "
        f"but the dispatched sides are {sorted(DISPATCHED_ROLES)}"
    )


@pytest.mark.parametrize("config", ROLE_CONFIGS)
def test_every_role_config_resolves_to_its_committed_configuration(
    oneharness_bin: str, config: str
) -> None:
    """The effective routing is what it was, value for value, for every role.

    This is the equivalence proof for the `extends` refactor and the drift gate after
    it: a later edit to a shared parent or a role file that changes ANY resolved value
    fails here, and a deliberate change is an update to the record in the same commit.
    """
    expected = json.loads((RECORDS / f"{config}.json").read_text(encoding="utf-8"))
    actual = _comparable(_effective_config(oneharness_bin, REPO_ROOT / config))

    assert actual == expected, (
        f"{config} no longer resolves to its committed effective configuration. "
        f"If this change is deliberate, update tests/e2e/oneharness_resolved/{config}.json "
        f"in the same commit so the routing change is reviewable."
    )


@pytest.mark.parametrize("config", ROLE_CONFIGS)
def test_no_variant_lost_a_credential_it_must_mask(oneharness_bin: str, config: str) -> None:
    """Every identity still REPORTS the credentials it is meant to refuse to pass on.

    Stated as a property rather than compared against the records, because a comparison
    whose expectation was regenerated after a bad merge would agree with it. Deleting
    one of the restatements in `oneharness.dispatch.toml` is the single most plausible
    future edit here — it leaves every file looking tidier — and this refuses it.

    This asserts what oneharness REPORTS, which on the pinned release is not the same
    question as what a turn applies: an inherited mask is reported as `[]` whenever the
    child mentions the variant. So a failure here means the reported isolation is
    wrong, which is worth failing on because it is what a reviewer reads — while a PASS
    here is not evidence that any provider was masked.
    `tests/e2e/test_dispatch_environment_e2e.py` is what answers that, from a real turn.
    """
    resolved = _effective_config(oneharness_bin, REPO_ROOT / config)
    masks_board = config not in PLAN_STORE_CONFIGS

    lost: list[str] = []
    for (harness_id, variant_id), masked in _variants(resolved).items():
        required: list[str] = []
        if harness_id == "claude-code":
            required.extend(ANTHROPIC_CREDENTIALS)
        if harness_id == "codex" and variant_id == "alternate":
            required.append(CODEX_ALTERNATE_CREDENTIAL)
        if masks_board:
            required.append(BOARD_CREDENTIAL)
        lost.extend(
            f"{harness_id}:{variant_id} no longer masks {name}"
            for name in required
            if name not in masked
        )

    assert not lost, (
        f"{config} reports identities that do not mask a credential they should: "
        f"{lost}. A NON-EMPTY child list replaces the parent's whole while an empty or "
        f"absent one inherits it — but oneharness REPORTS an inherited mask as `[]` "
        f"whenever the child mentions the variant, so check which layer names that "
        f"variant, and confirm against a real turn rather than against this report."
    )


@pytest.mark.parametrize("config", PLAN_STORE_CONFIGS)
def test_the_plan_store_roles_still_carry_the_board_credential(
    oneharness_bin: str, config: str
) -> None:
    """The three roles that read the board are not masked out of doing their job.

    The other half of the mask property, and the reason the three files restate a whole
    `unset_env` rather than inheriting one: folding those lists back into
    `oneharness.identities.toml` would look like deleting duplication and would leave
    each of these roles unable to reach the store it exists to write.

    `codex:primary` is the variant this nearly broke. Its only mask anywhere here is the
    board credential, so for these three roles the correct mask is EMPTY — and an empty
    child list inherits rather than clears, so no child can say it. Both parents
    therefore state no mask at that variant and the seven roles that mask it say so
    themselves.
    """
    still_masked = [
        f"{harness_id}:{variant_id}"
        for (harness_id, variant_id), masked in _variants(
            _effective_config(oneharness_bin, REPO_ROOT / config)
        ).items()
        if BOARD_CREDENTIAL in masked
    ]
    assert not still_masked, (
        f"{config} masks {BOARD_CREDENTIAL} on {still_masked}, so this role's turn "
        f"cannot reach the plan store it exists to read and write."
    )
