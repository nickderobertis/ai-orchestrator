"""Unit tests for the per-side overrides each dispatch entry point offers.

The wrapper's behaviour is proven against the real script in
`test_oneharness_agent_wrapper.py`, and the whole journey — two providers, two
sides, two models, one dispatch — in
`tests/e2e/test_harness_side_selection_e2e.py`. What is under test here is the
boundary that decides which values are allowed to reach either of them.
"""

from __future__ import annotations

import argparse
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
    add_side_options,
    configured_harnesses,
    harness_option_help,
    model_option_help,
    side_override_env,
)
from orchestrator.labels import LABEL_ENV

#: The wrapper every variable above is resolved by, and the only place in this
#: repository that exports a harness selection or a model into a process's
#: environment.
AGENT_WRAPPER = REPO_ROOT / "scripts" / "oneharness-agent.sh"


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

    A reader that scrubs a dispatch's choice has to scrub *every* name one can arrive
    under, and the per-side variables are not that set: the wrapper resolves each of
    them into one of oneharness's process-wide variables and exports that, so the
    value reaching a worker's own gate is the one nothing dropped. Assert the
    relationship rather than restating the list — a wrapper that starts carrying a
    choice under a seventh name fails here, where `DISPATCH_SELECTION_ENV` is
    defined, instead of in a journey that mysteriously reads someone else's.
    """
    wrapper = AGENT_WRAPPER.read_text(encoding="utf-8")
    exported = set(re.findall(r"^\s*export ([A-Za-z_][A-Za-z0-9_]*)=", wrapper, re.MULTILINE))

    # The history labels are the wrapper's other per-side rewrite — the judge side
    # drops the worker's `agent_role` — and carry no choice. Named exhaustively
    # rather than filtered, so a choice arriving under a seventh name still fails.
    assert exported == {PROCESS_WIDE_HARNESS_ENV, PROCESS_WIDE_MODEL_ENV, LABEL_ENV}
    # Everything the wrapper exports but the labels is a choice a reader has to drop,
    # so the one-place list has to already name it: this is what fails when a future
    # variable is added to the wrapper without joining the list.
    assert exported - {LABEL_ENV} < set(DISPATCH_SELECTION_ENV)
    for variable in DISPATCH_SELECTION_ENV:
        assert variable in wrapper
    # Every variable is distinct and both per-side pairs are subsets of the whole, so
    # a site that isolates itself by iterating the tuple cannot silently cover two
    # names — nor drop only the harness half of an enclosing dispatch's choice.
    assert len(set(DISPATCH_SELECTION_ENV)) == len(DISPATCH_SELECTION_ENV)
    assert {WORKER_HARNESS_ENV, JUDGE_HARNESS_ENV} < set(DISPATCH_SELECTION_ENV)
    assert {WORKER_MODEL_ENV, JUDGE_MODEL_ENV} < set(DISPATCH_SELECTION_ENV)
    assert set(HARNESS_SELECTION_ENV) | set(MODEL_SELECTION_ENV) == set(DISPATCH_SELECTION_ENV)


def test_option_help_names_the_side_its_variable_and_its_config() -> None:
    """`--help` is where an operator learns the seam exists; it must name it."""
    for side in (WORKER_SIDE, JUDGE_SIDE):
        help_text = harness_option_help(side)
        assert side.name in help_text
        assert side.env in help_text
        assert side.config.name in help_text


def test_model_option_help_names_the_harness_option_it_must_be_paired_with() -> None:
    """The one thing an operator cannot guess from the flag name is the pairing."""
    for side in (WORKER_SIDE, JUDGE_SIDE):
        help_text = model_option_help(side)
        assert side.name in help_text
        assert side.model_env in help_text
        assert side.config.name in help_text
        assert side.option in help_text
        assert PROCESS_WIDE_MODEL_ENV in help_text


def test_both_halves_of_the_group_come_from_one_source() -> None:
    """Every entry point takes the whole group, so the halves cannot drift apart.

    `just run-plan` and `just orchestrate` each add these four options by calling
    `add_side_options`, which is why a change to one command's option group is a
    change to both. The scope suffix a command appends reaches every one of them.
    """
    parser = argparse.ArgumentParser()
    add_side_options(parser, scope="; applies to every dispatch of the run")
    parsed = parser.parse_args(
        [
            "--worker-harness",
            "codex",
            "--worker-model",
            "gpt-5.6-sol",
            "--judge-model",
            "claude-opus-5",
        ]
    )

    assert (parsed.worker_harness, parsed.worker_model) == ("codex", "gpt-5.6-sol")
    assert (parsed.judge_harness, parsed.judge_model) == (None, "claude-opus-5")
    rendered = parser.format_help()
    for side in (WORKER_SIDE, JUDGE_SIDE):
        assert side.option in rendered
        assert side.model_option in rendered
    assert rendered.count("applies to every dispatch of the run") == 4


def test_each_side_carries_its_own_model_variable() -> None:
    env = side_override_env(
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


def test_one_side_model_alone_leaves_the_other_side_unset() -> None:
    """The one-sided case: a supervisor lifted off its config's tier, worker untouched."""
    assert side_override_env(judge="claude-code:primary", judge_model="claude-opus-5") == {
        JUDGE_HARNESS_ENV: "claude-code:primary",
        JUDGE_MODEL_ENV: "claude-opus-5",
    }


def test_a_model_override_may_name_a_chain_within_one_family() -> None:
    """A fallback chain is still selectable; it just has to be one provider's."""
    env = side_override_env(
        worker="claude-code:alternate2, claude-code:primary", worker_model="claude-opus-5"
    )

    assert env == {
        WORKER_HARNESS_ENV: "claude-code:alternate2,claude-code:primary",
        WORKER_MODEL_ENV: "claude-opus-5",
    }


def test_a_model_without_its_side_harness_is_refused_by_name() -> None:
    """A model is applied to every candidate, so an unpaired one picks its own victim."""
    with pytest.raises(ConfigError) as excinfo:
        side_override_env(judge_model="claude-opus-5")

    message = str(excinfo.value)
    assert JUDGE_SIDE.model_option in message
    assert repr("claude-opus-5") in message
    # The option that fixes it, and why the pairing is required rather than advised.
    assert JUDGE_SIDE.option in message
    assert JUDGE_SIDE.config.name in message
    assert "falls through" in message
    # A model is refused on the side that named it; the other side is unaffected.
    assert WORKER_SIDE.model_option not in message


def test_a_model_over_a_chain_spanning_two_families_is_refused_by_name() -> None:
    """A chain that can fall through to another provider cannot carry one model."""
    with pytest.raises(ConfigError) as excinfo:
        side_override_env(worker="claude-code:primary,codex", worker_model="claude-opus-5")

    message = str(excinfo.value)
    assert WORKER_SIDE.model_option in message
    assert WORKER_SIDE.option in message
    # Both families, so the operator can see which identity to drop.
    assert "claude-code" in message
    assert "codex" in message


def test_a_model_is_refused_before_the_harness_it_names_is_trusted() -> None:
    """An unconfigured identity still refuses first, with its own message."""
    with pytest.raises(ConfigError) as excinfo:
        side_override_env(worker="opencode", worker_model="claude-opus-5")

    assert "is not a harness" in str(excinfo.value)


@pytest.mark.parametrize("value", ["", "   ", "claude\nopus-5", "claude\x7fopus"])
def test_a_model_that_is_not_a_usable_environment_value_is_refused(value: str) -> None:
    """Not an allowlist — the name is the harness's to reject — but it must be a value."""
    with pytest.raises(ConfigError) as excinfo:
        side_override_env(worker="codex", worker_model=value)

    assert WORKER_SIDE.model_option in str(excinfo.value)


def test_a_model_that_arrived_through_a_shell_keeps_its_surrounding_whitespace_off() -> None:
    """A command substitution's trailing newline is not a malformed model name."""
    assert side_override_env(worker="codex", worker_model="  gpt-5.6-sol\n") == {
        WORKER_HARNESS_ENV: "codex",
        WORKER_MODEL_ENV: "gpt-5.6-sol",
    }


def test_a_model_name_is_not_checked_against_an_allowlist() -> None:
    """Deliberately asymmetric with the harness option, and load-bearing.

    A harness identity selects credentials and environment routing only this
    repository configures; a model name is passed straight through to the harness
    the operator named in the same breath, where an unknown one fails loudly.
    """
    env = side_override_env(worker="codex", worker_model="gpt-6-nobody-has-shipped")

    assert env[WORKER_MODEL_ENV] == "gpt-6-nobody-has-shipped"
