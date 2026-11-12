"""The half of `tests/test_agent_allowlist.py` that reads this repository's prose.

It sits in the `manager-guidance` project.

This holds the watch budget to the settings that decide it and to `AGENTS.md`'s statement
of it. That project's key names each file they read, and `tests/conftest.py` holds them to
it; the tests of that module that read no prose stay there, in `orchestrator:test`.
"""

from __future__ import annotations

import json
import re
import shlex

from orchestrator.root import REPO_ROOT


def test_the_watch_environment_and_stop_guard_share_the_budget() -> None:
    settings = json.loads((REPO_ROOT / ".claude/settings.json").read_text("utf-8"))
    budget = int(settings["env"]["ONEPIPELINE_WAKE_BUDGET"])
    assert budget == 2100
    commands = [
        shlex.split(hook["command"])
        for matcher in settings["hooks"]["Stop"]
        for hook in matcher["hooks"]
        if hook["type"] == "command" and "stop-guard" in hook["command"]
    ]
    assert len(commands) == 1
    arguments = commands[0]
    assert int(arguments[arguments.index("--wake-budget") + 1]) == budget
    # AGENTS.md restates the budget for the manager who reads it; every copy there is
    # held to the one the harness configures, so neither can move alone.
    document = " ".join((REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8").split())
    seconds = {int(n) for n in re.findall(r"(?:WAKE_BUDGET=|--wake-budget )(\d+)", document)}
    minutes = {int(n) for n in re.findall(r"(\d+)-minute", document)}
    assert seconds == {budget}, f"AGENTS.md states the wake budget as {seconds}s"
    assert minutes == {budget // 60}, f"AGENTS.md states the wake budget as {minutes} min"
