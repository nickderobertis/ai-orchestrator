"""Unit tests for the per-side harness and model selection each entry point offers.

The wrapper's behaviour is proven against the real script in
`test_oneharness_agent_wrapper.py`, and the whole journey — two providers, two
sides, one dispatch — in `tests/e2e/test_harness_side_selection_e2e.py`. What is
under test here is the boundary that decides which values are allowed to reach
either of them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from orchestrator import REPO_ROOT
from orchestrator.config import ConfigError
from orchestrator.harnesses import (
    DISPATCH_SELECTION_ENV,
    HARNESS_SELECTION_ENV,
    JUDGE_HARNESS_ENV,
    JUDGE_MODEL_ENV,
    JUDGE_SIDE,
    MODEL_SELECTION_ENV,
    PROCESS_WIDE_HARNESS_ENV,
    PROCESS_WIDE_MODEL_ENV,
    WORKER_HARNESS_ENV,
    WORKER_MODEL_ENV,
    WORKER_SIDE,
    HarnessSide,
    configured_harnesses,
    harness_option_help,
    model_option_help,
    side_override_env,
)
from orchestrator.labels import LABEL_ENV

#: The wrapper every variable above is resolved by, and the only place in this
#: repository that exports a per-side selection into a process's environment.
AGENT_WRAPPER = REPO_ROOT / "scripts" / "oneharness-agent.sh"

#: A worker chain that is one harness family, so a model may be paired with it.
ONE_FAMILY_CHAIN = "claude-code:primary,claude-code:alternate"
#: A worker chain that is two, so a model may not: one model name cannot be right
#: for both providers, and a fallback chain does not fall through a task failure.
TWO_FAMILY_CHAIN = "claude-code:primary,codex"


def test_no_override_leaves_the_environment_exactly_as_it_was() -> None:
    """The default path has to stay byte-identical: no variable, no wrapper change."""
    assert side_override_env() == {}
    assert side_override_env(worker=None, judge=None) == {}


def test_each_side_carries_its_own_variable() -> None:
    env = side_override_env(worker="codex", judge="claude-code:primary")

    assert env == {
        WORKER_HARNESS_ENV: "codex",
        JUDGE_HARNESS_ENV: "claude-code:primary",
    }


def test_one_side_alone_leaves_the_other_side_unset() -> None:
    assert side_override_env(judge="codex") == {JUDGE_HARNESS_ENV: "codex"}
    assert side_override_env(worker="codex") == {WORKER_HARNESS_ENV: "codex"}


def test_an_override_may_name_a_fallback_chain_of_its_own() -> None:
    env = side_override_env(worker="claude-code:alternate2, codex:alternate")

    assert env == {WORKER_HARNESS_ENV: "claude-code:alternate2,codex:alternate"}


def test_both_committed_configs_are_selectable_in_their_own_order() -> None:
    """Every identity a role's chain names must be addressable on that side."""
    assert configured_harnesses(WORKER_SIDE.config)[0] == "claude-code:alternate"
    assert configured_harnesses(JUDGE_SIDE.config)[0] == "codex"
    assert set(configured_harnesses(WORKER_SIDE.config)) == set(
        configured_harnesses(JUDGE_SIDE.config)
    )


@pytest.mark.parametrize("value", ["opencode", "claude-code", "codex:alternate3", "", "codex,"])
def test_an_unconfigured_identity_is_refused_by_name(value: str) -> None:
    with pytest.raises(ConfigError) as excinfo:
        side_override_env(worker=value)

    message = str(excinfo.value)
    assert WORKER_SIDE.option in message
    # The rejected value and every usable one, so the operator can correct it here.
    assert repr(value) in message
    for identity in configured_harnesses(WORKER_SIDE.config):
        assert identity in message


def test_each_side_is_validated_against_its_own_config() -> None:
    """A judge value is judged by the judge config, whatever the worker config says."""
    with pytest.raises(ConfigError) as excinfo:
        side_override_env(judge="opencode")

    assert JUDGE_SIDE.config.name in str(excinfo.value)
    assert JUDGE_SIDE.option in str(excinfo.value)


def test_a_config_without_a_chain_says_so_rather_than_selecting_nothing(tmp_path: Path) -> None:
    empty = tmp_path / "oneharness.toml"
    empty.write_text('run_mode = "fallback"\n', encoding="utf-8")

    with pytest.raises(ConfigError, match="declares no 'harnesses' chain"):
        configured_harnesses(empty)


def test_an_unreadable_config_is_reported_at_the_boundary(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read harness config"):
        configured_harnesses(tmp_path / "absent.toml")


def test_the_selection_seam_names_every_variable_the_wrapper_reads_or_exports() -> None:
    """The drift gate behind the suite's own isolation from an enclosing dispatch.

    A reader that scrubs an enclosing dispatch's choice has to scrub *every* name one
    can arrive under, and the per-side variables alone were not that set: the wrapper
    resolves each of them into oneharness's process-wide variable and exports that, so
    the value reaching a worker's own gate was the one nothing dropped. Derive the set
    from the wrapper rather than restating it — a wrapper that starts carrying a
    choice under a name nobody added to `DISPATCH_SELECTION_ENV` fails here, where
    that constant is defined, instead of in a journey that mysteriously reads someone
    else's choice.
    """
    wrapper = AGENT_WRAPPER.read_text(encoding="utf-8")
    exported = set(re.findall(r"^\s*export ([A-Za-z_][A-Za-z0-9_]*)=", wrapper, re.MULTILINE))

    # The history labels are the wrapper's other per-side rewrite — the judge side
    # drops the worker's `agent_role` — and carry no selection. Every *other* variable
    # the wrapper exports is a choice a reader must be able to drop, so a new one has
    # to join the list here rather than leaking into a suite that believes it isolated.
    assert exported - {LABEL_ENV} <= set(DISPATCH_SELECTION_ENV)
    assert {PROCESS_WIDE_HARNESS_ENV, PROCESS_WIDE_MODEL_ENV} <= exported
    for variable in DISPATCH_SELECTION_ENV:
        assert variable in wrapper
    # Every variable is distinct and each half is a proper subset of the whole, so a
    # site that isolates itself by iterating one tuple cannot silently cover two names
    # — nor iterate the harness half alone and believe it covered the model.
    assert len(set(DISPATCH_SELECTION_ENV)) == len(DISPATCH_SELECTION_ENV)
    assert set(HARNESS_SELECTION_ENV) < set(DISPATCH_SELECTION_ENV)
    assert set(MODEL_SELECTION_ENV) < set(DISPATCH_SELECTION_ENV)
    assert {WORKER_MODEL_ENV, JUDGE_MODEL_ENV} < set(MODEL_SELECTION_ENV)


def test_option_help_names_the_side_its_variable_and_its_config() -> None:
    """`--help` is where an operator learns the seam exists; it must name it."""
    for side in (WORKER_SIDE, JUDGE_SIDE):
        help_text = harness_option_help(side)
        assert side.name in help_text
        assert side.env in help_text
        assert side.config.name in help_text


def test_model_option_help_comes_from_the_same_source_and_names_the_pairing() -> None:
    """The two halves of one option group cannot drift apart if both are generated."""
    for side in (WORKER_SIDE, JUDGE_SIDE):
        help_text = model_option_help(side)
        assert side.name in help_text
        assert side.model_env in help_text
        assert side.config.name in help_text
        # The rule an operator would otherwise learn only from a refusal.
        assert side.option in help_text


def test_a_model_reaches_only_its_own_side() -> None:
    env = side_override_env(
        worker=ONE_FAMILY_CHAIN,
        judge="codex",
        worker_model="claude-opus-5",
        judge_model="gpt-5.6-sol",
    )

    assert env == {
        WORKER_HARNESS_ENV: ONE_FAMILY_CHAIN,
        JUDGE_HARNESS_ENV: "codex",
        WORKER_MODEL_ENV: "claude-opus-5",
        JUDGE_MODEL_ENV: "gpt-5.6-sol",
    }


def test_one_side_may_name_a_model_while_the_other_names_only_an_identity() -> None:
    env = side_override_env(
        judge="claude-code:primary", judge_model="claude-opus-5", worker="codex"
    )

    assert env == {
        JUDGE_HARNESS_ENV: "claude-code:primary",
        JUDGE_MODEL_ENV: "claude-opus-5",
        WORKER_HARNESS_ENV: "codex",
    }


def test_a_model_value_is_passed_through_rather_than_checked_against_an_allowlist() -> None:
    """Deliberately asymmetric with the identity: see `orchestrator.harnesses`.

    An identity selects credentials and environment routing only this repository
    configures, so an unconfigured one must refuse. A model name is handed to the
    harness the operator named in the same breath, where an unknown one fails loudly.
    """
    env = side_override_env(worker="claude-code:primary", worker_model=" not-a-real-model ")

    assert env[WORKER_MODEL_ENV] == "not-a-real-model"


@pytest.mark.parametrize("side", [WORKER_SIDE, JUDGE_SIDE])
def test_a_model_without_its_side_s_identity_is_refused(side: HarnessSide) -> None:
    """The mismatch the pairing rule exists to make unconstructable."""
    with pytest.raises(ConfigError) as excinfo:
        side_override_env(**{f"{side.name}_model": "claude-opus-5"})

    message = str(excinfo.value)
    assert side.model_option in message
    assert side.option in message
    # Why, not just what: an unpaired model dies at a provider rather than degrading.
    assert "fall through" in message


@pytest.mark.parametrize("side", [WORKER_SIDE, JUDGE_SIDE])
def test_a_model_paired_with_a_chain_spanning_two_harnesses_is_refused(side: HarnessSide) -> None:
    with pytest.raises(ConfigError) as excinfo:
        side_override_env(**{side.name: TWO_FAMILY_CHAIN, f"{side.name}_model": "claude-opus-5"})

    message = str(excinfo.value)
    assert side.model_option in message
    assert repr(TWO_FAMILY_CHAIN) in message
    # Both families, so the operator can see which half to drop.
    assert "claude-code" in message
    assert "codex" in message


def test_an_empty_model_says_so_rather_than_pinning_nothing() -> None:
    with pytest.raises(ConfigError) as excinfo:
        side_override_env(worker="claude-code:primary", worker_model="   ")

    assert WORKER_SIDE.model_option in str(excinfo.value)


def test_a_model_paired_with_an_unconfigured_identity_refuses_on_the_identity() -> None:
    """The identity is checked first, so the correctable message is the specific one."""
    with pytest.raises(ConfigError) as excinfo:
        side_override_env(worker="opencode", worker_model="claude-opus-5")

    assert "is not a harness" in str(excinfo.value)
