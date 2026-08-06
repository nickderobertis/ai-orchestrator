"""Unit tests for the per-side harness selection each dispatch entry point offers.

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
    HARNESS_SELECTION_ENV,
    JUDGE_HARNESS_ENV,
    JUDGE_SIDE,
    PROCESS_WIDE_HARNESS_ENV,
    WORKER_HARNESS_ENV,
    WORKER_SIDE,
    configured_harnesses,
    harness_option_help,
    harness_override_env,
)
from orchestrator.labels import LABEL_ENV

#: The wrapper both variables above are resolved by, and the only place in this
#: repository that exports a harness selection into a process's environment.
AGENT_WRAPPER = REPO_ROOT / "scripts" / "oneharness-agent.sh"


def test_no_override_leaves_the_environment_exactly_as_it_was() -> None:
    """The default path has to stay byte-identical: no variable, no wrapper change."""
    assert harness_override_env() == {}
    assert harness_override_env(worker=None, judge=None) == {}


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


def test_the_selection_seam_names_every_variable_the_wrapper_reads_or_exports() -> None:
    """The drift gate behind the suite's own isolation from an enclosing dispatch.

    A reader that scrubs a harness selection has to scrub *every* name one can arrive
    under, and the pair of per-side variables was not that set: the wrapper resolves
    each of them into oneharness's process-wide variable and exports that, so the value
    reaching a worker's own gate was the one nothing dropped. Assert the relationship
    rather than restating the list — a wrapper that starts carrying a selection under a
    fourth name fails here, where `HARNESS_SELECTION_ENV` is defined, instead of in a
    journey that mysteriously reads someone else's choice.
    """
    wrapper = AGENT_WRAPPER.read_text(encoding="utf-8")
    exported = set(re.findall(r"^\s*export ([A-Za-z_][A-Za-z0-9_]*)=", wrapper, re.MULTILINE))

    # The history labels are the wrapper's other per-side rewrite — the judge side
    # drops the worker's `agent_role` — and carry no selection. Named exhaustively
    # rather than filtered, so a selection arriving under a fourth name still fails.
    assert exported == {PROCESS_WIDE_HARNESS_ENV, LABEL_ENV}
    for variable in HARNESS_SELECTION_ENV:
        assert variable in wrapper
    # Every selection variable is distinct and the pair is a subset of the whole, so a
    # site that isolates itself by iterating the tuple cannot silently cover two names.
    assert len(set(HARNESS_SELECTION_ENV)) == len(HARNESS_SELECTION_ENV)
    assert {WORKER_HARNESS_ENV, JUDGE_HARNESS_ENV} < set(HARNESS_SELECTION_ENV)


def test_option_help_names_the_side_its_variable_and_its_config() -> None:
    """`--help` is where an operator learns the seam exists; it must name it."""
    for side in (WORKER_SIDE, JUDGE_SIDE):
        help_text = harness_option_help(side)
        assert side.name in help_text
        assert side.env in help_text
        assert side.config.name in help_text
