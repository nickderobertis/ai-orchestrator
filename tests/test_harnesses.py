"""Unit tests for the per-side harness and model choice each entry point offers.

The wrapper's behaviour is proven against the real script in
`test_oneharness_agent_wrapper.py`, and the whole journey — two providers, two
models, two sides, one dispatch — in `tests/e2e/test_harness_side_selection_e2e.py`.
What is under test here is the boundary that decides which values are allowed to
reach either of them.
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
    harness_override_env,
    model_option_help,
)
from orchestrator.labels import LABEL_ENV

#: The wrapper every variable above is resolved by, and the only place in this
#: repository that exports a harness or model choice into a process's environment.
AGENT_WRAPPER = REPO_ROOT / "scripts" / "oneharness-agent.sh"


def test_no_override_leaves_the_environment_exactly_as_it_was() -> None:
    """The default path has to stay byte-identical: no variable, no wrapper change."""
    assert harness_override_env() == {}
    assert harness_override_env(worker=None, judge=None) == {}
    assert harness_override_env(worker=None, judge=None, worker_model=None, judge_model=None) == {}


def test_each_side_carries_its_own_variable() -> None:
    env = harness_override_env(worker="codex", judge="claude-code:primary")

    assert env == {
        WORKER_HARNESS_ENV: "codex",
        JUDGE_HARNESS_ENV: "claude-code:primary",
    }


def test_one_side_alone_leaves_the_other_side_unset() -> None:
    assert harness_override_env(judge="codex") == {JUDGE_HARNESS_ENV: "codex"}
    assert harness_override_env(worker="codex") == {WORKER_HARNESS_ENV: "codex"}


def test_an_override_may_name_a_fallback_chain_of_its_own() -> None:
    env = harness_override_env(worker="claude-code:alternate2, codex:alternate")

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
        harness_override_env(worker=value)

    message = str(excinfo.value)
    assert WORKER_SIDE.option in message
    # The rejected value and every usable one, so the operator can correct it here.
    assert repr(value) in message
    for identity in configured_harnesses(WORKER_SIDE.config):
        assert identity in message


def test_each_side_is_validated_against_its_own_config() -> None:
    """A judge value is judged by the judge config, whatever the worker config says."""
    with pytest.raises(ConfigError) as excinfo:
        harness_override_env(judge="opencode")

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


def test_each_side_carries_its_own_model_variable() -> None:
    """The model half of the seam: one value per side, beside its own identity."""
    env = harness_override_env(
        worker="codex",
        judge="claude-code:primary",
        worker_model="gpt-5.6-sol",
        judge_model="claude-opus-5",
    )

    assert env == {
        WORKER_HARNESS_ENV: "codex",
        JUDGE_HARNESS_ENV: "claude-code:primary",
        WORKER_MODEL_ENV: "gpt-5.6-sol",
        JUDGE_MODEL_ENV: "claude-opus-5",
    }


def test_one_side_may_take_a_model_while_the_other_takes_none() -> None:
    """The run this seam was built for: the judge moved off the tier its config pins."""
    env = harness_override_env(judge="claude-code:primary", judge_model="claude-opus-5")

    assert env == {
        JUDGE_HARNESS_ENV: "claude-code:primary",
        JUDGE_MODEL_ENV: "claude-opus-5",
    }


def test_a_model_may_name_a_chain_within_one_harness_family() -> None:
    """A fallback chain is still addressable: one provider, several of its identities."""
    env = harness_override_env(
        worker="claude-code:alternate,claude-code:primary", worker_model="claude-opus-5"
    )

    assert env[WORKER_MODEL_ENV] == "claude-opus-5"


@pytest.mark.parametrize(
    ("side", "unpaired"),
    [
        (WORKER_SIDE, {"worker_model": "claude-opus-5"}),
        (JUDGE_SIDE, {"judge_model": "claude-opus-5"}),
        # The other side's identity is no substitute: each half of the pair belongs
        # to one side, and the judge below still has a whole chain of its own.
        (JUDGE_SIDE, {"worker": "claude-code:primary", "judge_model": "claude-opus-5"}),
    ],
)
def test_a_model_without_its_sides_harness_override_is_refused(
    side: HarnessSide, unpaired: dict[str, str]
) -> None:
    """The pairing rule, in the shape a harness refusal already takes.

    `ONEHARNESS_MODEL` and the flag the wrapper adds beside it both reach whichever
    candidate that side's configured chain settles on, so an unpaired model would be
    put in front of another provider's identity — and a fallback chain moves past a
    candidate that cannot run, not one whose task the provider rejected.
    """
    with pytest.raises(ConfigError) as excinfo:
        harness_override_env(**unpaired)

    message = str(excinfo.value)
    assert side.model_option in message
    assert side.option in message
    assert side.config.name in message


def test_a_model_over_a_chain_spanning_two_harness_families_is_refused() -> None:
    """One model name cannot be right for a Claude identity and a codex one at once."""
    with pytest.raises(ConfigError) as excinfo:
        harness_override_env(worker="claude-code:primary,codex", worker_model="claude-opus-5")

    message = str(excinfo.value)
    assert WORKER_SIDE.model_option in message
    assert "claude-code" in message and "codex" in message


def test_an_empty_model_is_refused_rather_than_exported_as_one() -> None:
    """An empty `ONEHARNESS_MODEL` selects nothing; it must not look like a choice."""
    with pytest.raises(ConfigError, match="name the model"):
        harness_override_env(worker="codex", worker_model="   ")


def test_a_model_value_is_taken_as_given_rather_than_checked_against_a_list() -> None:
    """Deliberately asymmetric with the harness option, and the reason is the boundary.

    An identity selects credentials and environment routing only this repository
    configures, so an unconfigured one must refuse. A model name is handed to the
    harness the operator named in the same breath, where an unknown one fails loudly.
    """
    env = harness_override_env(worker="codex", worker_model=" no-such-model-9000 ")

    assert env[WORKER_MODEL_ENV] == "no-such-model-9000"


def test_the_selection_seam_names_every_variable_the_wrapper_reads_or_exports() -> None:
    """The drift gate behind the suite's own isolation from an enclosing dispatch.

    A reader that scrubs a dispatch's choice has to scrub *every* name one can arrive
    under, and the pair of per-side variables was not that set: the wrapper resolves
    each of them into oneharness's process-wide variable and exports that, so the value
    reaching a worker's own gate was the one nothing dropped. The model half arrives
    the same way, under three more names. Assert the relationship rather than restating
    the list — a wrapper that starts carrying a choice under a further name fails here,
    where `DISPATCH_SELECTION_ENV` is defined, instead of in a journey that mysteriously
    reads someone else's.
    """
    wrapper = AGENT_WRAPPER.read_text(encoding="utf-8")
    exported = set(re.findall(r"^\s*export ([A-Za-z_][A-Za-z0-9_]*)=", wrapper, re.MULTILINE))

    # The history labels are the wrapper's other per-side rewrite — the judge side
    # drops the worker's `agent_role` — and carry no selection. Named exhaustively
    # rather than filtered, so a choice arriving under a further name still fails.
    assert exported == {PROCESS_WIDE_HARNESS_ENV, PROCESS_WIDE_MODEL_ENV, LABEL_ENV}
    # Everything the wrapper exports but the labels is something a reader has to drop,
    # so the one-place list cannot fall behind the wrapper without failing here.
    assert exported - {LABEL_ENV} <= set(DISPATCH_SELECTION_ENV)
    for variable in DISPATCH_SELECTION_ENV:
        assert variable in wrapper
    # Every variable is distinct and each per-side pair is a strict subset of the whole,
    # so a site that isolates itself by iterating the tuple cannot silently cover two.
    assert len(set(DISPATCH_SELECTION_ENV)) == len(DISPATCH_SELECTION_ENV)
    assert DISPATCH_SELECTION_ENV == HARNESS_SELECTION_ENV + MODEL_SELECTION_ENV
    assert {WORKER_HARNESS_ENV, JUDGE_HARNESS_ENV} < set(HARNESS_SELECTION_ENV)
    assert {WORKER_MODEL_ENV, JUDGE_MODEL_ENV} < set(MODEL_SELECTION_ENV)


def test_option_help_names_the_side_its_variable_and_its_config() -> None:
    """`--help` is where an operator learns the seam exists; it must name it."""
    for side in (WORKER_SIDE, JUDGE_SIDE):
        help_text = harness_option_help(side)
        assert side.name in help_text
        assert side.env in help_text
        assert side.config.name in help_text


def test_model_option_help_names_the_pairing_its_validation_requires() -> None:
    """An operator who reads only `--help` must not construct the refused shape."""
    for side in (WORKER_SIDE, JUDGE_SIDE):
        help_text = model_option_help(side)
        assert side.name in help_text
        assert side.model_env in help_text
        assert side.config.name in help_text
        # The pairing is a rule of this option, not a footnote in the documentation.
        assert side.option in help_text
