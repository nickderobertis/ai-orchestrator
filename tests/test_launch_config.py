"""This host's launch configs name only commands and files this checkout carries.

The engine reads a launch config once, at a launch, and resolves every path in it against
the launch directory, which `scripts/onepipeline.sh` makes this checkout's root. A path it
resolves (the template root, the bus configuration, the maintenance schedule, the drafting
graph) is refused at that launch when it names nothing; a command it spawns (the hooks, the
registered launch check, the plan-edit hook) is spawned only later — a run-end hook once the
run has ended, the plan-edit hook on the first reply carrying an edit — so one that cannot
start is recorded in a log nobody is reading for it. So both are held here, before any
launch, for `config/onepipeline.launch.yaml` (`just orchestrate`'s) and
`config/onepipeline.workflows.yaml` (the planning and follow-up launches'): every key states
a file of this checkout, and every command names one git keeps executable.
`tests/e2e/launch_monitor_observer/test_orchestrate_launch_e2e.py` holds what a real launch
records from it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from orchestrator.root import REPO_ROOT

#: The tracked launch configs: the one every `start` naming none gets, and the workflows'.
LAUNCH_CONFIG = REPO_ROOT / "config" / "onepipeline.launch.yaml"
WORKFLOWS_CONFIG = REPO_ROOT / "config" / "onepipeline.workflows.yaml"
CONFIGS = (LAUNCH_CONFIG, WORKFLOWS_CONFIG)

#: The keys whose value is a command the engine spawns in the launch directory.
COMMANDS = ("dispatch_env_hook", "success_hook", "failure_hook")

#: The keys whose value is a path the engine resolves against the launch directory.
PATHS = ("template_root", "bus_config", "maintenance_config", "pr_author_graph")


def _stated(config: Path) -> dict[str, object]:
    """Every top-level key the document states, with its value."""
    loaded = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), f"{config.name} is not a mapping"
    return {str(key): value for key, value in loaded.items()}


def _programs(config: Path) -> list[tuple[str, str]]:
    """Every (key, program) the document names: scalar hooks, then each argv's program."""
    stated = _stated(config)
    found = [(key, str(stated[key])) for key in COMMANDS if key in stated]
    found += [("checks", str(argv[0])) for argv in stated.get("checks") or []]
    hook = stated.get("plan_edit_hook")
    if isinstance(hook, dict):
        found.append(("plan_edit_hook", str(hook["command"][0])))
    return found


@pytest.mark.parametrize("config", CONFIGS, ids=lambda one: one.name)
def test_every_command_a_launch_config_names_is_an_executable_tracked_file(config: Path) -> None:
    """A command that could not start would be found only when the engine first spawns it."""
    for key, command in _programs(config):
        staged = subprocess.run(
            ["git", "ls-files", "--stage", "--", command],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        assert staged.startswith("100755 "), (
            f"{config.name} names {command!r} as {key}, which git does not keep as an "
            f"executable file: {staged!r}"
        )


@pytest.mark.parametrize("config", CONFIGS, ids=lambda one: one.name)
def test_every_path_a_launch_config_names_is_in_this_checkout(config: Path) -> None:
    """A path relative to the checkout root, naming something the checkout carries."""
    stated = _stated(config)
    for key in PATHS:
        if key not in stated:
            continue
        path = str(stated[key])
        assert not path.startswith("/"), f"{key} is {path!r}, which no other checkout carries"
        assert (REPO_ROOT / path).exists(), f"{config.name} names {path!r} as {key}"


def test_only_orchestrates_config_gates_a_launch_and_judges_live_edits() -> None:
    """The plan's own launch carries its registered check and the plan-edit hook."""
    launch, workflows = _stated(LAUNCH_CONFIG), _stated(WORKFLOWS_CONFIG)
    assert launch["checks"] == [["scripts/plan-attestation-check.sh"]]
    assert launch["plan_edit_hook"] == {"command": ["scripts/review-plan-item.sh"]}
    for key in ("checks", "plan_edit_hook", "success_hook", "failure_hook", "pr_author_graph"):
        assert key not in workflows, f"{WORKFLOWS_CONFIG.name} states {key}"


@pytest.mark.parametrize("config", CONFIGS, ids=lambda one: one.name)
def test_each_document_declares_the_schema_the_pinned_engine_writes(config: Path) -> None:
    """`schema_version: 14` is the first that carries `checks` and `plan_edit_hook`."""
    assert _stated(config)["schema_version"] == 14
