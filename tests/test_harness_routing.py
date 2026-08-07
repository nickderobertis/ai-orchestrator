"""The committed per-role harness routing: one file per side of the harness.

Every role now names the SAME five identities — two alternate Claude
subscriptions, two Codex accounts, and the primary Claude subscription — and the
roles differ only in the order they try them. Workers keep both alternate Claude
plans first, because the personas are tuned against that model tier; the judge,
llmlint, and the long-lived orchestrator lead with Codex so they do not stand in
front of workers for that quota, but they no longer STOP there. Reaching the
workers' subscriptions is the operator's deliberate trade: a supervisory tier that
can still run beats one isolated from the quota that is left. The primary Claude
identity is the last resort everywhere.

Nothing else in the suite pins those orders, so a routing edit meant for one role
could silently change another's. The credential routing itself is not a per-role
choice at all: each shared identity has exactly one definition, and only `model`
may differ per role.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest

from orchestrator import REPO_ROOT
from orchestrator.provider_health import IDENTITIES

WORKER_CONFIG = REPO_ROOT / "oneharness.toml"
JUDGE_CONFIG = REPO_ROOT / "oneharness.judge.toml"
LLMLINT_CONFIG = REPO_ROOT / "oneharness.llmlint.toml"
ORCHESTRATOR_CONFIG = REPO_ROOT / "oneharness.orchestrator.toml"


def _config(path: Path) -> dict[str, Any]:
    # `Any` is what tomllib returns: these configs are arbitrarily nested harness and
    # variant tables, and the assertions below compare whole subtrees rather than
    # reading through a fixed shape, so a narrower type would only be a cast.
    return tomllib.loads(path.read_text(encoding="utf-8"))


ROLE_CONFIGS = (WORKER_CONFIG, JUDGE_CONFIG, LLMLINT_CONFIG, ORCHESTRATOR_CONFIG)
SUPERVISORY_CHAIN = [
    "codex",
    "codex:alternate",
    "claude-code:alternate",
    "claude-code:alternate2",
    "claude-code:primary",
]


@pytest.mark.parametrize(
    ("config", "harnesses"),
    [
        (
            WORKER_CONFIG,
            [
                "claude-code:alternate",
                "claude-code:alternate2",
                "codex",
                "codex:alternate",
                "claude-code:primary",
            ],
        ),
        (JUDGE_CONFIG, SUPERVISORY_CHAIN),
        (LLMLINT_CONFIG, SUPERVISORY_CHAIN),
        (ORCHESTRATOR_CONFIG, SUPERVISORY_CHAIN),
    ],
)
def test_each_role_keeps_its_own_harness_priority(config: Path, harnesses: list[str]) -> None:
    assert _config(config)["harnesses"] == harnesses


@pytest.mark.parametrize("config", ROLE_CONFIGS)
def test_every_role_offers_the_whole_identity_roster(config: Path) -> None:
    """Order is the per-role choice; the roster is not.

    A role that omits an identity loses that quota entirely when everything ahead
    of it is exhausted — the failure this whole arrangement exists to prevent.
    """
    assert sorted(_config(config)["harnesses"]) == sorted(SUPERVISORY_CHAIN)


@pytest.mark.parametrize("variant", ["alternate", "alternate2", "primary"])
def test_every_role_defines_each_claude_identity_identically(variant: str) -> None:
    """Every role now names all three Claude identities; hold each to one definition.

    A variant carries the portable `env_from` indirection and the credential
    masking that stops ambient API/OAuth values from shadowing subscription auth —
    a copy that drifted would silently change how one role authenticates. Only
    `model` is a per-role choice (the judge supervises on a cheaper tier), so it is
    compared separately by its own test below.
    """
    definitions = {}
    for config in ROLE_CONFIGS:
        claude = _config(config)["harness"]["claude-code"]
        assert claude["env"] == {"IS_SANDBOX": "1"}, f"{config.name} lacks the shared env"
        definitions[config.name] = {
            key: value for key, value in claude["variant"][variant].items() if key != "model"
        }
    worker = definitions[WORKER_CONFIG.name]
    for name, definition in definitions.items():
        assert definition == worker, f"{name} drifted from the worker definition"
    expected_source = {
        "alternate": "ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR",
        "alternate2": "ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR",
    }.get(variant)
    if expected_source is None:
        # The primary identity is Claude's own default directory, so it names no
        # indirection and must instead MASK any it inherited.
        assert "env_from" not in worker
        assert "CLAUDE_CONFIG_DIR" in worker["unset_env"]
    else:
        assert worker["env_from"] == {"CLAUDE_CONFIG_DIR": expected_source}
    assert {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_REFRESH_TOKEN",
    } <= set(worker["unset_env"])


def test_only_the_judge_supervises_on_the_cheaper_claude_tier() -> None:
    """`model` is the one thing a role may choose per identity, and it says why.

    The judge is the simulated-user side of every dispatch, so it stays on the
    cheaper tier wherever it lands; every other role wants the tier its personas
    were tuned against.
    """
    for config in ROLE_CONFIGS:
        variants = _config(config)["harness"]["claude-code"]["variant"]
        expected = "claude-sonnet-5" if config is JUDGE_CONFIG else "claude-opus-5"
        for name, variant in variants.items():
            assert variant["model"] == expected, f"{config.name}:{name}"


def test_every_role_defines_the_alternate_codex_identity_identically() -> None:
    """All four chains reach a second Codex identity; hold them to one definition.

    Unlike the Claude variant, this one is named by every role — a drifted copy
    would send one role's overflow to a different account, or to the same account
    with `OPENAI_API_KEY` left unmasked and outranking its subscription tokens.
    """
    worker = _config(WORKER_CONFIG)["harness"]["codex"]["variant"]["alternate"]
    assert worker["env_from"] == {"CODEX_HOME": "ORCHESTRATOR_CODEX_ALT_HOME"}
    # An ambient API key would outrank the ChatGPT tokens `codex login` writes into
    # the alternate home, silently billing the wrong account.
    assert worker["unset_env"] == ["OPENAI_API_KEY"]
    for config in (JUDGE_CONFIG, LLMLINT_CONFIG, ORCHESTRATOR_CONFIG):
        role = _config(config)["harness"]["codex"]["variant"]["alternate"]
        assert role == worker, f"{config.name} drifted from the worker definition"


@pytest.mark.parametrize("config", ROLE_CONFIGS)
def test_a_named_variant_is_always_defined(config: Path) -> None:
    """A chain may not name a variant no table defines.

    oneharness resolves `codex:alternate` against `[harness.codex.variant.alternate]`
    and `claude-code:alternate2` against its claude-code counterpart; naming one
    without that table would drop the `env_from` indirection and the credential
    masking, so the candidate would quietly run as the primary identity instead of
    failing.
    """
    parsed = _config(config)
    for harness_id in parsed["harnesses"]:
        base, _, variant = harness_id.partition(":")
        if not variant:
            continue
        assert variant in parsed["harness"][base]["variant"], harness_id


def test_the_llmlint_codex_sandbox_grant_cannot_reach_a_claude_candidate() -> None:
    """The grant is Codex-specific, and this tier's chain no longer is.

    As a trailing `-- <HARNESS_ARG>` it would be appended to whichever harness
    fallback selected, and claude's `-c` is `--continue` — so the permission string
    would land as a positional prompt and silently replace the lint prompt. Under
    `[harness.codex]` it can only ever reach a Codex identity.
    """
    llmlint = _config(LLMLINT_CONFIG)["harness"]
    assert llmlint["codex"]["args"] == [
        "-c",
        'sandbox_permissions=["disk-full-read-access","network-full-access"]',
    ]
    assert "args" not in llmlint["claude-code"]
    assert all("args" not in variant for variant in llmlint["claude-code"]["variant"].values())


@pytest.mark.parametrize("config", [JUDGE_CONFIG, LLMLINT_CONFIG, ORCHESTRATOR_CONFIG])
def test_supervisory_roles_reach_a_second_codex_before_the_worker_subscriptions(
    config: Path,
) -> None:
    """Overflow must reach another Codex account before the workers' Claude ones.

    These roles may now reach those subscriptions — that is the accepted trade —
    but only after Codex is genuinely out. An alternate Codex candidate ordered
    after `claude-code:alternate` would defeat that on the exact path, Codex quota
    exhaustion, where it matters.
    """
    harnesses = _config(config)["harnesses"]
    assert harnesses.index("codex:alternate") < harnesses.index("claude-code:alternate")


def test_orchestrator_runs_fallback_and_records_agent_side_history() -> None:
    orchestrator = _config(ORCHESTRATOR_CONFIG)
    assert orchestrator["run_mode"] == "fallback"
    assert orchestrator["history"] is True
    # `role` is the conversation side, not the persona: telemetry and the DAG UI
    # read the orchestrator's own session as an agent-side one.
    assert orchestrator["history_labels"] == {"role": "agent"}


#: Every `ORCHESTRATOR_*` indirection named in prose, ignoring `AI_ORCHESTRATOR_HOME`
#: and any other name this one is only a suffix of.
_INDIRECTION_PATTERN = re.compile(r"(?<![A-Z_])ORCHESTRATOR_[A-Z0-9_]+")
HOST_SETUP_DOC = REPO_ROOT / "docs" / "host-setup.md"


def _env_from_sources(config: Path) -> set[str]:
    """The parent variables this role's variants map into a child's environment."""
    return {
        source
        for harness in _config(config).get("harness", {}).values()
        for variant in harness.get("variant", {}).values()
        for source in variant.get("env_from", {}).values()
    }


@pytest.mark.reads_docs
def test_host_setup_names_exactly_the_indirections_the_configs_source() -> None:
    """DRIFT-GATE the documented variables against the configs that name them.

    `docs/host-setup.md` tells an operator which variables the probe wrapper must
    export, and oneharness refuses to start when a selected variant's `env_from`
    source is unset — so a renamed or added indirection that the document missed
    would leave the operator exporting a name nothing reads.
    """
    documented = set(_INDIRECTION_PATTERN.findall(HOST_SETUP_DOC.read_text(encoding="utf-8")))
    sourced: set[str] = set()
    for config in ROLE_CONFIGS:
        sourced |= _env_from_sources(config)

    assert sourced, "no role config maps an env_from source; the gate would prove nothing"
    assert documented == sourced


#: An inline-code span holding exactly a harness id, e.g. `` `claude-code:alternate2` ``.
_IDENTITY_PATTERN = re.compile(r"`((?:claude-code|codex)(?::[a-z0-9]+)?)`")


@pytest.mark.reads_docs
def test_host_setup_names_exactly_the_identities_the_roles_select() -> None:
    """DRIFT-GATE the documented login list against the committed chains.

    The document tells an operator to authenticate five accounts, and its whole
    point is that an identity nobody logged into is quota the role loses. A sixth
    identity added to the chains — or one renamed — has to reach that list, so take
    the roster from the configs rather than trusting the prose to have kept up.
    """
    documented = set(_IDENTITY_PATTERN.findall(HOST_SETUP_DOC.read_text(encoding="utf-8")))
    selected: set[str] = set()
    for config in ROLE_CONFIGS:
        selected |= set(_config(config)["harnesses"])

    assert documented == selected, (
        "docs/host-setup.md must name every identity the role configs select, and no "
        "other; write a harness id there only as the login list's own entry"
    )


def test_provider_health_probes_exactly_the_identities_the_roles_select() -> None:
    """DRIFT-GATE the probed roster against the chains that define it.

    `provider_health.IDENTITIES` is what `just status` and `just runs` probe, and
    the whole promise of that block is that a chain is never silently short one
    member — a failed probe is rendered `unknown` rather than dropped. An identity
    added to or renamed in the role chains would defeat that by never being asked
    about at all, which is indistinguishable from a healthy one in every view. So
    take the roster from the configs rather than trusting the tuple to have kept up.
    """
    selected: set[str] = set()
    for config in ROLE_CONFIGS:
        selected |= set(_config(config)["harnesses"])

    assert selected, "no role config names a harness chain; the gate would prove nothing"
    assert set(IDENTITIES) == selected, (
        "orchestrator/provider_health.py IDENTITIES must probe every identity the role "
        "configs select, and no other; an unprobed identity reads as healthy in every view"
    )
    assert len(IDENTITIES) == len(set(IDENTITIES)), "IDENTITIES must not repeat an identity"
