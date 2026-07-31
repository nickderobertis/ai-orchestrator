"""Stable provenance for partial lifecycle commits and their verified recovery."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import gitops

INCOMPLETE_TRAILER = "Orchestrator-Status: incomplete"
PR_BASE_TRAILER = "Orchestrator-PR-Base:"
RECOVERY_TRAILER = "Orchestrator-Recovered-Incomplete:"
LEGACY_INCOMPLETE_MARKERS = (
    "(incomplete step)",
    "preserved by ai-orchestrator after the dispatch did not complete",
)
_PRESERVED_STEP = re.compile(r"Partial work from step (\S+) \(persona: ([^)]+)\)")


@dataclass(frozen=True)
class PreservedStepMetadata:
    step_id: str
    persona: str


def format_preserved_step_metadata(step_id: str, persona: str | None) -> str:
    """Format the stable worker metadata embedded in incomplete commits."""
    return f"Partial work from step {step_id} (persona: {persona})"


def parse_preserved_step_metadata(message: str) -> PreservedStepMetadata | None:
    """Parse worker metadata from an incomplete commit message."""
    match = _PRESERVED_STEP.search(message)
    if match is None:
        return None
    return PreservedStepMetadata(*match.groups())


def is_incomplete_marker(message: str) -> bool:
    """True for a commit that marks a step as having been left incomplete."""
    return INCOMPLETE_TRAILER in message or any(
        marker in message for marker in LEGACY_INCOMPLETE_MARKERS
    )


def is_provenance_commit(message: str) -> bool:
    """True for a marker or its recovery attestation.

    These commits record what happened to the *run* — a step stopped, a later gate
    recovered it — and never describe the change. A caller summarizing a branch has
    to skip them: `chore: ... (incomplete step)` is a valid Conventional Commit
    subject, so a subject synthesizer that reads every commit folds the marker's
    text into the published subject.
    """
    return is_incomplete_marker(message) or RECOVERY_TRAILER in message


def incomplete_commits(repo: str | Path, base: str, branch: str) -> set[str]:
    """Return incomplete-provenance commit SHAs in a base-relative history."""
    return {
        commit.sha
        for commit in gitops.log_messages(repo, base, branch)
        if is_incomplete_marker(commit.message)
    }


def attestation_trailers(repo: str | Path, base: str, branch: str) -> tuple[str, ...]:
    """Return one trailer per attested incomplete marker in a base-relative history.

    Publication squashes the branch, so these lines are the only thing that can carry
    "an incomplete step happened here, and a green gate recovered it" onto the base
    branch. They are derived from the markers rather than copied off the commits that
    attest them: a branch's messages are agent-written, and a value repeated verbatim
    into a publication commit would let any line spelled like a trailer claim a
    recovery that never happened. A trailer naming a commit this history does not mark
    as incomplete attests nothing and is dropped. Emitted in marker history order,
    once each.
    """
    commits = gitops.log_messages(repo, base, branch)
    attested = {
        line.removeprefix(RECOVERY_TRAILER).strip()
        for commit in commits
        for line in commit.message.splitlines()
        if line.startswith(RECOVERY_TRAILER)
    }
    return tuple(
        f"{RECOVERY_TRAILER} {commit.sha}"
        for commit in commits
        if commit.sha in attested and is_incomplete_marker(commit.message)
    )


def recorded_pr_base(repo: str | Path, base: str, branch: str) -> str | None:
    """Return the PR base recorded by the newest preserved incomplete commit."""
    for commit in reversed(gitops.log_messages(repo, base, branch)):
        if not is_incomplete_marker(commit.message):
            continue
        values = {
            line.removeprefix(PR_BASE_TRAILER).strip()
            for line in commit.message.splitlines()
            if line.startswith(PR_BASE_TRAILER)
        }
        if "" in values:
            raise ValueError(f"incomplete commit {commit.sha} records an empty PR base")
        if len(values) > 1:
            raise ValueError(
                f"incomplete commit {commit.sha} records conflicting PR bases: "
                + ", ".join(sorted(values))
            )
        return next(iter(values), None)
    return None


def unattested_incomplete(repo: str | Path, base: str, branch: str) -> set[str]:
    """Return incomplete commits not covered by a lifecycle recovery attestation."""
    commits = gitops.log_messages(repo, base, branch)
    incomplete = {commit.sha for commit in commits if is_incomplete_marker(commit.message)}
    recovered = {
        line.removeprefix(RECOVERY_TRAILER).strip()
        for commit in commits
        for line in commit.message.splitlines()
        if line.startswith(RECOVERY_TRAILER)
    }
    return incomplete - recovered
