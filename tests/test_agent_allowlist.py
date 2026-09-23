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
#: The `Stop` hook's command: the engine's stop guard in its own docs page's
#: Claude Code wiring, named by path out of this checkout's locked install — through
#: `$CLAUDE_PROJECT_DIR`, which is what makes one registration work from every worktree of
#: this repository — with nothing of this repository's between the harness and the verb.
STOP_HOOK = '"$CLAUDE_PROJECT_DIR/.venv/bin/onepipeline" stop-guard --format claude-code'
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
#: The recipes that act on runs, each withheld so that every use is approved on its own:
#: `stop` ends a run, and `shutdown` at `--host` acts on runs this session does not own.
#: `AGENTS.md` says so where it names them; this holds the allowlist to it.
WITHHELD = ("just stop", "just shutdown")
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

    Registered beside the `SessionStart` hook already there, as exactly the engine's
    documented wiring and nothing more, and with a bound of its own: a killed hook is a
    stop that was never guarded, so the harness's bound is kept as the page keeps it.
    `tests/unwatched/test_unwatched_and_stop_hook_e2e.py` runs this command.
    """
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    hooks = settings["hooks"]
    assert "SessionStart" in hooks, "the session-setup hook is gone, so this is not that file"
    registered = hooks.get("Stop")
    assert registered, "no Stop hook is registered, so nothing asks what is unwatched"
    commands = [
        command
        for matcher in registered
        for command in matcher["hooks"]
        if command["type"] == "command"
    ]
    assert [command["command"] for command in commands] == [STOP_HOOK], (
        f"the Stop hook is not the engine's stop guard wired as its page gives: {commands}"
    )
    assert (REPO_ROOT / ".venv" / "bin" / "onepipeline").is_file(), (
        "the Stop hook names this checkout's locked engine, and there is none installed"
    )
    bounds = [command.get("timeout") for command in commands]
    assert all(isinstance(bound, int) and bound > 0 for bound in bounds), (
        f"the Stop hook is registered without a bound of its own: {bounds}"
    )


def test_the_recipes_that_act_on_runs_stay_outside_the_allowlist() -> None:
    """No rule grants a run-acting recipe, however it is spelled: a claude-code rule
    matches by prefix, so a bare `Bash(just stop)` and a `Bash(just stop:*)` both would."""
    granted = [
        rule
        for rule in _allowed()
        for recipe in WITHHELD
        if rule.startswith(f"Bash({recipe}") or rule.startswith(f"Bash({recipe.split()[0]}:")
    ]
    assert not granted, f"the allowlist grants a recipe that acts on runs: {granted}"
