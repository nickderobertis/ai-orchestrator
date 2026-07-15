"""Stable provenance for partial lifecycle commits and their verified recovery."""

from __future__ import annotations

from pathlib import Path

from . import gitops

INCOMPLETE_TRAILER = "Orchestrator-Status: incomplete"
RECOVERY_TRAILER = "Orchestrator-Recovered-Incomplete:"
LEGACY_INCOMPLETE_MARKERS = (
    "(incomplete step)",
    "preserved by ai-orchestrator after the dispatch did not complete",
)


def incomplete_commits(repo: str | Path, base: str, branch: str) -> set[str]:
    """Return incomplete-provenance commit SHAs in a base-relative history."""
    return {
        commit.sha
        for commit in gitops.log_messages(repo, base, branch)
        if INCOMPLETE_TRAILER in commit.message
        or any(marker in commit.message for marker in LEGACY_INCOMPLETE_MARKERS)
    }


def unattested_incomplete(repo: str | Path, base: str, branch: str) -> set[str]:
    """Return incomplete commits not covered by a lifecycle recovery attestation."""
    commits = gitops.log_messages(repo, base, branch)
    incomplete = {
        commit.sha
        for commit in commits
        if INCOMPLETE_TRAILER in commit.message
        or any(marker in commit.message for marker in LEGACY_INCOMPLETE_MARKERS)
    }
    recovered = {
        line.removeprefix(RECOVERY_TRAILER).strip()
        for commit in commits
        for line in commit.message.splitlines()
        if line.startswith(RECOVERY_TRAILER)
    }
    return incomplete - recovered
