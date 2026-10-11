"""The half of `tests/test_agent_allowlist.py` that reads this repository's prose.

It sits in the `manager-guidance` project.

These hold each allowlist row and the watch budget to the passage and settings that decide
them. That project's key names each file they read, and `tests/conftest.py` holds them to
it; the tests of that module that read no prose stay there, in `orchestrator:test`.
"""

from __future__ import annotations

import json
import re
import shlex

from manager_invocations import INVOCATIONS

from orchestrator.root import REPO_ROOT


def test_every_row_quotes_the_agents_md_passage_that_decides_it() -> None:
    document = " ".join((REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8").split())
    missing = [
        (invocation.id, invocation.sanctioned_by)
        for invocation in INVOCATIONS
        if " ".join(invocation.sanctioned_by.split()) not in document
    ]
    assert not missing, f"rows quote AGENTS.md passages it no longer states: {missing}"


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
