"""`.claude/settings.json` must allow the commands this repo's own agents run.

The orchestrator persona mandates commands (`just monitor`) and the planner surface
documents others (`just repos`, the run views, read-only `git -C` inspection). A
claude-code agent whose permission environment forbids a command its instructions
require cannot supervise its own run, so the allowlist is part of that contract —
while staying narrow: routine, read-only entries, never a blanket wildcard.
"""

from __future__ import annotations

import json

from orchestrator.root import REPO_ROOT

SETTINGS = REPO_ROOT / ".claude" / "settings.json"
#: The checked-in script the `Stop` hook runs, named relative to this checkout the way
#: the registration names it — through `$CLAUDE_PROJECT_DIR`, which is what makes one
#: registration work from every worktree of this repository.
STOP_HOOK = "scripts/stop-unwatched-guard.sh"
REQUIRED = (
    "Bash(just monitor:*)",
    "Bash(just repos:*)",
    "Bash(just runs:*)",
    "Bash(just status:*)",
    # The read behind the `Stop` hook below, offered as a command: a manager whose turn
    # was refused reads the same answer by hand rather than approving it afresh each
    # session.
    "Bash(just unwatched:*)",
    "Bash(just results:*)",
    "Bash(just channel-next:*)",
    "Bash(just channel-reply:*)",
    # The one bus verb a manager inspects a run's channel with directly, beside the recipes
    # above that wrap it: what each queue holds. It claims and appends nothing. Neither
    # `next` nor `validate` is granted: handing a surface out stays `just channel-next`'s,
    # and `validate` runs whatever command validator the configuration it is named declares.
    "Bash(onemessagebus status:*)",
    # The manager launches a planner on a brief. A claude-code rule matches by prefix,
    # and `just plan` cannot be invoked without naming a brief, so a bare grant would
    # prompt on every real use; what bounds the recipe instead is the recipe: it refuses
    # every input it does not understand before starting anything, and reaches exactly
    # one published verb. The planner it dispatches asks back through the command
    # `ORCHESTRATOR_ASK_MANAGER` names, an absolute path no checkout-relative grant
    # matches, so no grant is kept for the wrapper by a spelling nothing runs.
    "Bash(just plan:*)",
    "Bash(git -C * status*)",
    "Bash(git -C * log*)",
    "Bash(git -C * diff*)",
)
BLANKET = {"Bash(*)", "Bash(:*)", "Bash(git:*)", "Bash(git -C:*)", "Bash(just:*)", "*"}


def _allowed() -> list[str]:
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    allow = settings["permissions"]["allow"]
    assert isinstance(allow, list)
    return [str(rule) for rule in allow]


def test_allowlist_covers_the_orchestrator_and_planner_command_surface() -> None:
    missing = [rule for rule in REQUIRED if rule not in _allowed()]
    assert not missing, f"allowlist is missing required entries: {missing}"


def test_allowlist_grants_no_blanket_wildcard() -> None:
    assert not BLANKET.intersection(_allowed())


def test_the_stop_hook_asks_which_runs_nothing_is_watching() -> None:
    """AGENTS.md's watch rule names this hook as what enforces its first property.

    Registered beside the `SessionStart` hook already there and running the checked-in
    script rather than an inline command, for the reason that hook is written that way:
    what the hook does is a file in this repository, reviewed and tested like everything
    else here, and a command line in a settings file is neither. The bound is its own
    because the hook has one of its own — it gives the verb a shorter one and, when that
    runs out, ends the turn with a warning that it went unguarded — so this is the outer
    bound the harness enforces past it.
    """
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    hooks = settings["hooks"]
    assert "SessionStart" in hooks, "the session-setup hook is gone, so this is not that file"
    registered = hooks.get("Stop")
    assert registered, "no Stop hook is registered, so nothing asks what is unwatched"
    commands = [
        command["command"]
        for matcher in registered
        for command in matcher["hooks"]
        if command["type"] == "command"
    ]
    assert any(STOP_HOOK in command for command in commands), (
        f"no registered Stop hook runs {STOP_HOOK}: {commands}"
    )
    assert (REPO_ROOT / STOP_HOOK).is_file(), f"{STOP_HOOK} is registered and is not there"
    bounds = [
        command.get("timeout")
        for matcher in registered
        for command in matcher["hooks"]
        if STOP_HOOK in command.get("command", "")
    ]
    assert all(isinstance(bound, int) and bound > 0 for bound in bounds), (
        f"the Stop hook is registered without a bound of its own: {bounds}"
    )
