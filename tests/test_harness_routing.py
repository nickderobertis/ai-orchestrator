"""The committed per-role harness routing: one file per side of the harness.

Each role's oneharness config names its own priority chain, and those chains are
deliberately different: workers take the alternate Claude subscription first, the
judge and llmlint stay on Codex, and the long-lived orchestrator must not queue in
front of the workers for the subscription they depend on. Nothing else in the
suite pins those orders, so a routing edit meant for one role could silently
change another's.

Every chain also ends in a second Codex identity, so an exhausted quota has
somewhere to go without changing which subscription a role competes for.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

from orchestrator import REPO_ROOT

WORKER_CONFIG = REPO_ROOT / "oneharness.toml"
JUDGE_CONFIG = REPO_ROOT / "oneharness.judge.toml"
LLMLINT_CONFIG = REPO_ROOT / "oneharness.llmlint.toml"
ORCHESTRATOR_CONFIG = REPO_ROOT / "oneharness.orchestrator.toml"


def _config(path: Path) -> dict[str, Any]:
    # `Any` is what tomllib returns: these configs are arbitrarily nested harness and
    # variant tables, and the assertions below compare whole subtrees rather than
    # reading through a fixed shape, so a narrower type would only be a cast.
    return tomllib.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("config", "harnesses"),
    [
        (WORKER_CONFIG, ["claude-code:alternate", "codex", "codex:alternate"]),
        (JUDGE_CONFIG, ["codex", "codex:alternate", "claude-code:primary"]),
        (LLMLINT_CONFIG, ["codex", "codex:alternate"]),
        (ORCHESTRATOR_CONFIG, ["codex", "codex:alternate", "claude-code:alternate"]),
    ],
)
def test_each_role_keeps_its_own_harness_priority(config: Path, harnesses: list[str]) -> None:
    assert _config(config)["harnesses"] == harnesses


def test_orchestrator_reuses_the_worker_alternate_variant_verbatim() -> None:
    """Two configs name the same alternate identity; hold them to one definition.

    The variant carries the portable `env_from` indirection and the credential
    masking that stops ambient API/OAuth values from shadowing subscription auth —
    a copy that drifted would silently change how one role authenticates.
    """
    worker = _config(WORKER_CONFIG)["harness"]["claude-code"]
    orchestrator = _config(ORCHESTRATOR_CONFIG)["harness"]["claude-code"]
    assert orchestrator["variant"]["alternate"] == worker["variant"]["alternate"]
    assert orchestrator["env"] == worker["env"]
    assert orchestrator["variant"]["alternate"]["env_from"] == {
        "CLAUDE_CONFIG_DIR": "ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR"
    }


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


@pytest.mark.parametrize(
    "config", [WORKER_CONFIG, JUDGE_CONFIG, LLMLINT_CONFIG, ORCHESTRATOR_CONFIG]
)
def test_a_named_codex_variant_is_always_defined(config: Path) -> None:
    """A chain may not name a variant no table defines.

    oneharness resolves `codex:alternate` against `[harness.codex.variant.alternate]`;
    naming it without that table would drop the `env_from` indirection and the
    credential masking, so the candidate would quietly run as the primary identity
    instead of failing.
    """
    parsed = _config(config)
    variants = parsed.get("harness", {}).get("codex", {}).get("variant", {})
    for harness_id in parsed["harnesses"]:
        base, _, variant = harness_id.partition(":")
        if base == "codex" and variant:
            assert variant in variants


def test_llmlint_keeps_both_candidates_on_codex() -> None:
    """The tier that must not reach Claude Code still has somewhere to fall.

    llmlint is deliberately Codex-only, so its backup for an exhausted quota can
    only be a second Codex identity; a Claude candidate here would contend for a
    subscription this tier is meant to stay off.
    """
    harnesses = _config(LLMLINT_CONFIG)["harnesses"]
    assert all(h.split(":")[0] == "codex" for h in harnesses)
    assert len(harnesses) > 1


def test_orchestrator_prefers_a_second_codex_over_the_worker_subscription() -> None:
    """Overflow must reach another Codex account before the workers' Claude one.

    The orchestrator's whole ordering rationale is that it never queues in front of
    the workers; an alternate Codex candidate ordered after `claude-code:alternate`
    would defeat that on the exact path — Codex quota exhaustion — where it matters.
    """
    harnesses = _config(ORCHESTRATOR_CONFIG)["harnesses"]
    assert harnesses.index("codex:alternate") < harnesses.index("claude-code:alternate")


def test_orchestrator_runs_fallback_and_records_agent_side_history() -> None:
    orchestrator = _config(ORCHESTRATOR_CONFIG)
    assert orchestrator["run_mode"] == "fallback"
    assert orchestrator["history"] is True
    # `role` is the conversation side, not the persona: telemetry and the DAG UI
    # read the orchestrator's own session as an agent-side one.
    assert orchestrator["history_labels"] == {"role": "agent"}
