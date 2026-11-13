"""The `llmlint` this host installs runs under the `oneharness` this host pins.

`config/oneharness.version` names the `oneharness` every judged run reaches, and
`scripts/setup-llmlint.sh` installs `llmlint-cli` host-wide on every session start:
two adoptions, and a `llmlint` requiring a newer `oneharness` refuses every run
before judging anything. The installer derives its ceiling from the pin; this gate
says when the two part anyway — a stop-gap by hand, an older installer, a pin moved
without re-running setup — naming both versions, off the installed distribution's
own metadata and nothing on the network. Its subject is the host's tool directory,
outside this workspace and every cache key, hence `reads_checkouts`.
`tests/e2e/llmlint_installer/test_setup_llmlint_e2e.py` drives the installer's selection itself.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml
from llmlint_install import (
    LLMLINT_DISTRIBUTION,
    ONEHARNESS_DISTRIBUTION,
    declared_oneharness_requirement,
    host_tool_dir,
    installed_llmlint,
    installer_floor,
    pinned_oneharness,
)
from packaging.version import Version

from orchestrator.root import REPO_ROOT

#: The first `llmlint-cli` carrying `llmlint rules`, which prints the rules that would
#: judge given paths with no harness started and no model called
#: (https://github.com/nickderobertis/llmlint/pull/268).
RULES_COMMAND_RELEASE = Version("0.4.13")
PLAN_CHECKLIST = REPO_ROOT / "config" / "plan-checklist.llmlint.yml"
#: A task document the plan store would write, named before it exists: the command takes
#: paths that need not exist, which is the point of asking it before a task is written.
HYPOTHETICAL_TASK = "tasks/a-task-not-yet-written.md"

# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] `reads_checkouts` is
# this repository's tier mechanism rather than a shortcut around one: it routes a test
# to the uncached target `orchestrator:test-checkouts`, the tier that exists because no
# key over this workspace can describe state outside it, and the host's `uv tool`
# directory is such state. `tests/tree_scans/test_nx_cache_scope.py` holds the selectors to a
# partition of the suite.
# llmlint: ignore-block[shell_test_tiers_stay_split] Same site, same reason; and this is
# a pytest gate over an installed distribution's metadata, not a shell test suite.
pytestmark = pytest.mark.reads_checkouts


def test_the_installed_llmlint_declares_a_oneharness_requirement_the_pin_satisfies() -> None:
    """Fail naming both versions; skip naming the path when nothing is installed.

    Read off `Requires-Dist` rather than by running `llmlint`, because the refusal
    this guards against is what running it produces, and the metadata is what the
    installer's rule is stated over.
    """
    tool_dir = host_tool_dir()
    llmlint = installed_llmlint(tool_dir)
    if llmlint is None:
        pytest.skip(
            f"no {LLMLINT_DISTRIBUTION} is installed under {tool_dir / LLMLINT_DISTRIBUTION}; "
            "run 'just setup-llmlint' to install the release the pin admits"
        )
    pinned = pinned_oneharness()
    required = declared_oneharness_requirement(llmlint)

    assert required.specifier.contains(pinned, prereleases=True), (
        f"the installed {LLMLINT_DISTRIBUTION} {llmlint.version} requires "
        f"{required}, and config/oneharness.version pins {ONEHARNESS_DISTRIBUTION} "
        f"{pinned}; every judged run on this host "
        "would refuse on the version. Re-run 'just setup-llmlint', which installs the "
        "highest release the pin admits — or, to adopt the newer llmlint, move the pin "
        "in its own change"
    )


def _installed_or_skip() -> tuple[Path, str]:
    """The host's `uv tool` directory and the installed llmlint's version, or a skip."""
    tool_dir = host_tool_dir()
    llmlint = installed_llmlint(tool_dir)
    if llmlint is None:
        pytest.skip(
            f"no {LLMLINT_DISTRIBUTION} is installed under {tool_dir / LLMLINT_DISTRIBUTION}; "
            "run 'just setup-llmlint'"
        )
    return tool_dir, llmlint.version


def test_the_installer_floor_and_the_installed_llmlint_carry_the_rules_command() -> None:
    """Both the floor the installer declares and the release it installed have `rules`."""
    _, version = _installed_or_skip()
    assert installer_floor() >= RULES_COMMAND_RELEASE, (
        f"scripts/setup-llmlint.sh declares LLMLINT_MIN {installer_floor()}, below "
        f"{RULES_COMMAND_RELEASE}, the first {LLMLINT_DISTRIBUTION} carrying `llmlint rules`"
    )
    assert Version(version) >= RULES_COMMAND_RELEASE, (
        f"the installed {LLMLINT_DISTRIBUTION} {version} predates `llmlint rules` "
        f"({RULES_COMMAND_RELEASE}); re-run 'just setup-llmlint'"
    )


def test_the_installed_rules_command_names_a_plan_checklist_rule_for_a_task_path(
    tmp_path: Path,
) -> None:
    """The installed binary lists the checklist's rules for a task document not yet written.

    Run as `scripts/plan-checklist.sh` runs llmlint: from this checkout's root, so the
    checklist's `oneharness.config` resolves, with `--cwd` at a plan-authoring root —
    here an empty one, since the path is asked about before anything is written.
    """
    tool_dir, _ = _installed_or_skip()
    binary = tool_dir / LLMLINT_DISTRIBUTION / "bin" / "llmlint"
    declared = yaml.safe_load(PLAN_CHECKLIST.read_text(encoding="utf-8"))
    checklist_rules = {rule["name"] for rule in declared["rules"]}
    completed = subprocess.run(
        [
            str(binary),
            "rules",
            "-c",
            str(PLAN_CHECKLIST),
            "--cwd",
            str(tmp_path),
            "--files",
            HYPOTHETICAL_TASK,
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    assert completed.returncode == 0, f"`llmlint rules` failed:\n{completed.stderr}"
    listed = {rule["name"] for rule in json.loads(completed.stdout)["rules"]}
    assert listed & checklist_rules, (
        f"`llmlint rules` listed {sorted(listed)} for {HYPOTHETICAL_TASK}, none of them a "
        f"rule {PLAN_CHECKLIST.name} declares ({sorted(checklist_rules)})"
    )


# llmlint: ignore-end[shell_test_tiers_stay_split]
# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]
