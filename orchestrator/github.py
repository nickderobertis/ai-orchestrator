"""GitHub operations for the repo lifecycle, over the ``gh`` CLI.

The lifecycle opens a PR and merges it once the repo's own **blocking (required)
status checks** are green — never off a non-required check. The clean way to get
exactly that gate is GitHub's *native auto-merge*: it merges only when required
checks pass and branch protection is satisfied, and ignores optional checks. So
`CliGitHubBackend.enable_auto_merge` is the default path; `merge` is the direct
fallback for repos where auto-merge is disabled.

GitHub is the one boundary the offline gate cannot drive for free (like the paid
model in onejudge's e2e), so it sits behind the small `GitHubBackend` protocol.
The offline tests inject a fake backend that performs the *actual* merge with real
git against a local bare repo — so the merge is never mocked, only GitHub's PR /
CI *decisioning* is. The real backend shells to ``gh`` through an injectable
``run`` seam, itself unit-tested without a network.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

__all__ = [
    "AutoMergeUnavailable",
    "Check",
    "CliGitHubBackend",
    "GitHubBackend",
    "GitHubError",
    "PRStatus",
    "PullRequest",
]

# Check states, normalized across gh's CheckRun (status/conclusion) and
# StatusContext (state) shapes into one vocabulary the merge logic reasons over.
_GREEN = {"SUCCESS", "SKIPPED", "NEUTRAL", "EXPECTED"}
_RED = {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"}


class GitHubError(Exception):
    """A ``gh`` invocation failed (carries gh's stderr)."""


class AutoMergeUnavailable(GitHubError):
    """The repo does not allow GitHub native auto-merge — use the direct merge."""


@dataclass(frozen=True)
class PullRequest:
    number: int
    url: str
    repo: str
    head: str
    base: str


@dataclass(frozen=True)
class Check:
    name: str
    state: str  # SUCCESS | FAILURE | PENDING | ERROR | SKIPPED | NEUTRAL | ...
    required: bool

    @property
    def green(self) -> bool:
        return self.state in _GREEN

    @property
    def red(self) -> bool:
        return self.state in _RED


@dataclass(frozen=True)
class PRStatus:
    number: int
    state: str  # OPEN | MERGED | CLOSED
    merged: bool
    merge_state_status: str  # CLEAN | BLOCKED | BEHIND | UNSTABLE | DIRTY | ""
    checks: tuple[Check, ...]

    @property
    def blocking(self) -> tuple[Check, ...]:
        """The required checks — the only ones that may gate a merge."""
        return tuple(c for c in self.checks if c.required)

    @property
    def blocking_failed(self) -> bool:
        return any(c.red for c in self.blocking)

    @property
    def blocking_green(self) -> bool:
        """All required checks concluded green (vacuously true if none exist)."""
        return all(c.green for c in self.blocking)


class GitHubBackend(Protocol):
    """The GitHub operations the lifecycle needs; faked at this seam in tests."""

    def default_branch(self, repo: str) -> str: ...

    def create_pr(
        self, repo: str, *, head: str, base: str, title: str, body: str
    ) -> PullRequest: ...

    def enable_auto_merge(self, pr: PullRequest, *, method: str) -> None: ...

    def merge(self, pr: PullRequest, *, method: str) -> None: ...

    def status(self, pr: PullRequest) -> PRStatus: ...


def _normalize_check(raw: dict[str, object]) -> Check:
    """Fold a gh ``statusCheckRollup`` entry into a normalized `Check`."""
    required = bool(raw.get("isRequired"))
    typename = raw.get("__typename")
    if typename == "CheckRun":
        name = str(raw.get("name") or "check")
        status = str(raw.get("status") or "").upper()
        if status != "COMPLETED":
            return Check(name=name, state="PENDING", required=required)
        conclusion = str(raw.get("conclusion") or "").upper()
        return Check(name=name, state=conclusion or "PENDING", required=required)
    # StatusContext (a commit status) or anything else with a ``state``.
    name = str(raw.get("context") or raw.get("name") or "status")
    return Check(name=name, state=str(raw.get("state") or "PENDING").upper(), required=required)


def _is_auto_merge_unavailable(message: str) -> bool:
    lowered = message.lower()
    return "auto-merge is not enabled" in lowered or "auto merge is not allowed" in lowered


def _default_run(argv: list[str]) -> str:
    proc = subprocess.run(["gh", *argv], text=True, capture_output=True)
    if proc.returncode != 0:
        raise GitHubError(
            f"gh {' '.join(argv)} failed (exit {proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip() or '<no output>'}"
        )
    return proc.stdout


class CliGitHubBackend:
    """`GitHubBackend` backed by the real ``gh`` CLI (the live path)."""

    def __init__(self, *, run: Callable[[list[str]], str] | None = None) -> None:
        self._run = run if run is not None else _default_run

    def default_branch(self, repo: str) -> str:
        out = self._run(
            ["repo", "view", repo, "--json", "defaultBranchRef", "-q", ".defaultBranchRef.name"]
        )
        return out.strip() or "main"

    def create_pr(self, repo: str, *, head: str, base: str, title: str, body: str) -> PullRequest:
        out = self._run(
            [
                "pr",
                "create",
                "--repo",
                repo,
                "--head",
                head,
                "--base",
                base,
                "--title",
                title,
                "--body",
                body,
            ]
        )
        url = out.strip().splitlines()[-1].strip() if out.strip() else ""
        try:
            number = int(url.rstrip("/").rsplit("/", 1)[-1])
        except ValueError as exc:  # gh printed something unexpected instead of the PR URL
            raise GitHubError(f"could not parse PR number from gh output: {out!r}") from exc
        return PullRequest(number=number, url=url, repo=repo, head=head, base=base)

    def enable_auto_merge(self, pr: PullRequest, *, method: str) -> None:
        try:
            self._run(["pr", "merge", str(pr.number), "--repo", pr.repo, f"--{method}", "--auto"])
        except GitHubError as exc:
            if _is_auto_merge_unavailable(str(exc)):
                raise AutoMergeUnavailable(str(exc)) from exc
            raise

    def merge(self, pr: PullRequest, *, method: str) -> None:
        self._run(["pr", "merge", str(pr.number), "--repo", pr.repo, f"--{method}"])

    def status(self, pr: PullRequest) -> PRStatus:
        raw = self._run(
            [
                "pr",
                "view",
                str(pr.number),
                "--repo",
                pr.repo,
                "--json",
                "number,state,mergeStateStatus,statusCheckRollup",
            ]
        )
        data = json.loads(raw)
        rollup = data.get("statusCheckRollup") or []
        checks = tuple(_normalize_check(c) for c in rollup if isinstance(c, dict))
        state = str(data.get("state") or "OPEN")
        return PRStatus(
            number=int(data.get("number", pr.number)),
            state=state,
            merged=state == "MERGED",
            merge_state_status=str(data.get("mergeStateStatus") or ""),
            checks=checks,
        )
