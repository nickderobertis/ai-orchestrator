"""The codex stand-in's refusal, reconciled against the `oneharness` that has to read it.

`tests/e2e/fake_codex_untrusted_directory.py` restates two things about the real codex: the
stderr it refuses an untrusted directory with, and the argument a `bypass` turn carries past
that refusal. Neither is worth anything unless the pinned `oneharness` agrees — it is what
classifies that stderr as `untrusted-directory` and moves a chain on, and what builds the bypass
command — so both are read back here out of a real `oneharness run` over the stand-in. A release
that moves either one fails here, rather than leaving
`tests/plan_tooling/test_follow_ups_recipe_e2e.py` passing against a refusal nothing reads.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from fake_codex_untrusted_directory import TRUST_ARGUMENTS
from project_fixtures import helper
from waits import timeout as e2e_timeout

STAND_IN = helper("fake_codex_untrusted_directory.py")

#: How `oneharness` spells the classification its fallback chain reports as `untrusted-directory`.
UNTRUSTED = "untrusted_directory"


def _codex_turn(oneharness_bin: str, directory: Path, mode: str) -> dict[str, Any]:
    """One real `oneharness run` of the codex harness from ``directory``, over the stand-in."""
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("ONEHARNESS_", "FAKE_CODEX_"))
    }
    ran = subprocess.run(  # noqa: S603 - the pinned harness CLI
        [
            oneharness_bin,
            "run",
            "--no-config",
            "--harness",
            "codex",
            # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
            "--bin",
            f"codex={STAND_IN}",
            "--mode",
            mode,
            "--cwd",
            str(directory),
            "--prompt",
            "hi",
            "--compact",
        ],
        cwd=directory,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    # llmlint: ignore[boundary_inputs_validated] The pinned CLI's own report; each field this
    # module reads is asserted on directly.
    result: dict[str, Any] = json.loads(ran.stdout)["results"][0]
    return result


def _outside_every_repository(tmp_path: Path) -> Path:
    directory = tmp_path / "scratch"
    directory.mkdir(parents=True)
    probed = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=directory,
        text=True,
        capture_output=True,
        check=False,
    )
    assert probed.returncode != 0, (
        f"{directory} is inside the repository at {probed.stdout.strip()}, so it cannot stand "
        "for a directory no repository holds"
    )
    return directory


def test_a_default_mode_turn_outside_every_repository_is_classified_untrusted(
    tmp_path: Path, oneharness_bin: str
) -> None:
    result = _codex_turn(oneharness_bin, _outside_every_repository(tmp_path), "default")

    assert result["exit_code"] == 1, result
    assert result["failure_kind"] == UNTRUSTED, (
        f"oneharness no longer reads the stand-in's refusal as an untrusted directory: {result}"
    )
    assert not TRUST_ARGUMENTS.intersection(result["command"]), result["command"]


def test_a_bypass_turn_outside_every_repository_carries_the_trust_argument_and_runs(
    tmp_path: Path, oneharness_bin: str
) -> None:
    result = _codex_turn(oneharness_bin, _outside_every_repository(tmp_path), "bypass")

    assert TRUST_ARGUMENTS.intersection(result["command"]), (
        f"oneharness's bypass command {result['command']} carries none of "
        f"{sorted(TRUST_ARGUMENTS)}, so the stand-in would refuse a turn the real codex runs"
    )
    assert result["status"] == "ok", result


def test_a_stray_ancestor_git_entry_that_is_no_repository_leaves_a_turn_untrusted(
    tmp_path: Path, oneharness_bin: str
) -> None:
    ancestor = tmp_path / "ancestor"
    (ancestor / ".git").mkdir(parents=True)
    directory = _outside_every_repository(ancestor / "below")

    result = _codex_turn(oneharness_bin, directory, "default")

    assert result["exit_code"] == 1, result
    assert result["failure_kind"] == UNTRUSTED, (
        f"an empty {ancestor / '.git'} above {directory} made the stand-in trust a directory "
        f"git says no work tree holds: {result}"
    )


def test_a_default_mode_turn_inside_a_repository_runs(tmp_path: Path, oneharness_bin: str) -> None:
    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "--quiet", str(repository)], check=True)

    result = _codex_turn(oneharness_bin, repository, "default")

    assert result["status"] == "ok", result
    assert result["failure_kind"] is None, result
