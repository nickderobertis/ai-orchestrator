"""The command-ceiling names the worker roles carry are the ones the installed providers read.

`tests/e2e/command_ceiling.py` states the setting each harness raises, and
`tests/e2e/fake_codex.py` the options codex reads an override from. Both names are the
providers' own, and every other journey only checks that oneharness forwards them, which
it would do for a misspelling too. This gate asks the installed providers, with no paid
turn: codex's own configuration parser, which refuses a wrongly typed value under a key it
knows and ignores one it does not, and the Claude Code bundle, which reads each variable
as a property of the environment. Its subject is the host's installed providers, outside
this workspace and every cache key, hence `reads_checkouts`.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from command_ceiling import (
    CEILING_MS,
    CLAUDE_CODE_CEILING,
    CLAUDE_CODE_DEFAULT,
    CODEX_CEILING_ARGS,
    CODEX_CEILING_KEY,
)
from fake_codex import CONFIG_OPTIONS

# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] `reads_checkouts` is
# this repository's tier mechanism rather than a shortcut around one: it routes a test
# to the uncached target `orchestrator:test-checkouts`, the tier that exists because no
# key over this workspace can describe state outside it, and the installed `codex` and
# `claude` are such state. `tests/test_nx_cache_scope.py` holds the selectors to a
# partition of the suite.
# llmlint: ignore-block[shell_test_tiers_stay_split] Same site, same reason; and this is
# a pytest gate over installed providers, not a shell test suite.
pytestmark = pytest.mark.reads_checkouts


def _installed(binary: str) -> str:
    found = shutil.which(binary)
    assert found is not None, (
        f"no {binary} on this host's PATH, which is the one oneharness spawns a dispatched "
        "turn's provider from"
    )
    return found


def _codex_prompt_input(codex_home: Path, *config: str) -> subprocess.CompletedProcess[str]:
    """Load codex's configuration as a turn would and render a prompt, spending no turn.

    The overrides follow the positional prompt, where `[harness.codex] args` puts them.
    """
    return subprocess.run(
        [_installed("codex"), "debug", "prompt-input", "probe", *config],
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
        env={**os.environ, "CODEX_HOME": str(codex_home)},
    )


def test_codex_accepts_the_worker_roles_ceiling_arguments(tmp_path: Path) -> None:
    loaded = _codex_prompt_input(tmp_path, *CODEX_CEILING_ARGS)

    assert loaded.returncode == 0, (
        f"codex refused {list(CODEX_CEILING_ARGS)}, the arguments every worker turn "
        f"carries: {loaded.stderr}"
    )


@pytest.mark.parametrize("option", CONFIG_OPTIONS)
def test_codex_reads_the_ceiling_key_through_each_option_the_stand_in_skips(
    tmp_path: Path, option: str
) -> None:
    """A wrongly typed value under the key is refused, naming it, which an unknown key is not.

    So a renamed or removed key fails here rather than leaving every worker on codex's
    five-minute default wait, and an option codex stopped reading fails here rather than
    leaving the stand-in skipping words codex now treats as the prompt.
    """
    loaded = _codex_prompt_input(tmp_path, option, f'{CODEX_CEILING_KEY}="not-a-number"')

    assert loaded.returncode != 0, (
        f"codex ignored `{option} {CODEX_CEILING_KEY}=...`, so it no longer reads that key "
        f"through that option, and the worker roles' {CEILING_MS} ms wait is not applied"
    )
    assert CODEX_CEILING_KEY in loaded.stderr, (
        f"codex refused `{option} {CODEX_CEILING_KEY}=...` without naming the key, so it "
        f"no longer reads `{option}` as a configuration override: {loaded.stderr}"
    )


@pytest.mark.parametrize("variable", [CLAUDE_CODE_CEILING, CLAUDE_CODE_DEFAULT])
def test_claude_code_reads_each_named_timeout_from_its_environment(variable: str) -> None:
    """The installed bundle reads the variable as an environment property.

    Claude Code answers nothing about its own limits without a turn, so the bundle
    `claude` resolves to is what is read; a variable it stopped reading is one the
    worker roles' `[harness.claude-code] env` sets for nothing.
    """
    bundle = Path(_installed("claude")).resolve().read_bytes()

    assert re.search(rb"\." + variable.encode() + rb"\b", bundle), (
        f"the Claude Code bundle at {Path(_installed('claude')).resolve()} reads no "
        f"`{variable}` from its environment"
    )


# llmlint: ignore-end[shell_test_tiers_stay_split]
# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]
