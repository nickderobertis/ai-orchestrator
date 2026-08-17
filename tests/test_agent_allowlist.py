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
REQUIRED = (
    "Bash(just monitor:*)",
    "Bash(just repos:*)",
    "Bash(just runs:*)",
    "Bash(just status:*)",
    "Bash(just results:*)",
    "Bash(just channel-next:*)",
    "Bash(just channel-reply:*)",
    # The two halves of the manager/planner split: the manager launches a planner on a
    # brief, and the planner it dispatches asks back over the same run's channel.
    #
    # Each granted at the narrowest form that still works, which is not the same form
    # for the two: a claude-code rule matches by prefix, and `just plan` cannot be
    # invoked without naming a brief, so a bare grant would prompt on every real use.
    # The wrapper can — it reads the question from stdin with no arguments at all — so
    # its grant carries no wildcard, and `--file` and a question typed as text are
    # approved one at a time. What bounds the recipe instead is the recipe: it refuses
    # every input it does not understand before starting anything, and reaches exactly
    # one published verb.
    "Bash(just plan:*)",
    "Bash(./scripts/ask-manager.sh)",
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
