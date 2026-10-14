"""The documents and callers of `just unpublished`, held to the engine verb it now is.

It sits in the `tree-scans` project, under `reads_docs`, because what it reads is this
repository's own prose and the callers beside it, which that project's whole-workspace
`test-docs` tier is keyed on. It runs no installed tool, so an unrelated edit replays only
a read of the tree: the flags the recipe does accept are driven against the real engine by
the `unpublished-view` journeys, which read none of this prose.

The recipe is the engine's own `onepipeline unpublished` passed through untouched, so the
flags the engine took the decision over without — `--json`, `--no-disk`, `--own`,
`--stop-verdict` and `--print-surface` — are held absent from every document and caller in
the tree, which is where a stale copy of the old interface would be read and run.
"""

from __future__ import annotations

import re

import pytest

from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_docs

#: The flags and forms of the recipe the engine's verb replaced, and the script it ran.
REMOVED = ("--json", "--no-disk", "--own", "--stop-verdict", "--print-surface")
#: Every tracked file that documents, grants or calls the recipe.
CALLERS = (
    "AGENTS.md",
    "docs/orchestration.md",
    "docs/repo-lifecycle.md",
    "justfile",
    "config/manager-allowlist.toml",
    ".claude/settings.json",
    ".codex/rules/oneharness.rules",
    "scripts/plan.sh",
    "scripts/unfinished.sh",
    "orchestrator/unfinished.py",
    "tests/manager_invocations.py",
)


def test_no_document_grant_or_caller_carries_a_removed_form_of_the_recipe() -> None:
    for relative in CALLERS:
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert "unpublished.sh" not in text, f"{relative} still names scripts/unpublished.sh"
        for line in text.splitlines():
            if "unpublished" not in line:
                continue
            for flag in REMOVED:
                assert not re.search(rf"unpublished\b[^\n]*{re.escape(flag)}\b", line), (
                    f"{relative} still carries `{flag}` beside the unpublished recipe: {line}"
                )
