"""Every preserved-but-unpublished branch on this host, and the command that lands it.

The lifecycle already records everything needed to resume interrupted work: a killed
dispatch's branch survives in the run clone it was cut in, an incomplete step leaves a
provenance marker on it, and the round result says why the workstream stopped. None of
it was surfaced. Twice in one week a quota-killed dispatch left a complete, gate-worthy
branch that had to be reconstructed by diffing clones by hand — twenty minutes of
forensics, one `integrate` aimed at a branch the canonical checkout did not have, and
the repo-recover-versus-integrate distinction rediscovered from an error message each
time.

This view is that record, read back. It is read-only in the strongest sense: it opens
git repositories to ask questions and writes nothing, takes no lease, and never touches
a run root a live dispatch may be working in — so it is safe to run beside live work,
which is exactly when a planner reaches for it.

Two rules decide a row:

* **Unpublished** means the branch holds commits no ``origin`` remote-tracking ref has
  (`gitops.unpublished_branches`). A branch that merged, or that its identity's base has
  since reached, therefore drops out on its own rather than by a name-shaped guess.
* **Incomplete** means a lifecycle provenance marker on it, and that is the whole
  difference between the two recovery verbs. `just repo-recover` publishes interrupted
  work through the incomplete-provenance path; `just integrate` publishes a branch whose
  commits are all complete. Offering the wrong one is what cost the twenty minutes.
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

from . import gitops
from .config import ConfigError
from .provenance import is_incomplete_marker
from .registry import Registry, RegistryError
from .runs import as_result_payload, latest_round, load_mapping
from .workspace import (
    CLONE_DIR_NAME,
    RUNS_DIR_NAME,
    IdentityKey,
    default_worktree_root,
    normalize_repo,
)


@dataclass(frozen=True)
class RecoverableBranch:
    """One preserved branch, with everything needed to act on it in a single step."""

    branch: str
    identity: IdentityKey
    #: The checkout the branch was found in — a registered checkout, or the run clone
    #: a killed dispatch left it in.
    checkout: Path
    publication_checkout: Path
    #: Whether the publication checkout already has this branch as a ref. When it does
    #: not, the suggested command has to bring it there first; aiming `integrate` at a
    #: branch a checkout does not have is the exact mistake this records.
    in_publication_checkout: bool
    tip_sha: str
    tip_subject: str
    #: When the tip commit was made, so every rendering of this row ages it against
    #: one clock rather than against whenever the row happened to be collected.
    tip_committed_at: float | None
    incomplete: bool
    #: Why the workstream stopped, from the lifecycle's own records — the round result
    #: that named this branch, or the provenance marker on the branch itself.
    stopped_because: str

    @property
    def command(self) -> str:
        """The exact command that resumes this branch, fetch included when it is needed.

        The verb is decided by the provenance and never by the branch's name: a marker
        means interrupted work, which only `repo-recover` may publish, and its absence
        means a complete branch, which `integrate` takes through the same gate.
        """
        branch = shlex.quote(self.branch)
        publication = shlex.quote(str(self.publication_checkout))
        if self.incomplete:
            command = f"just repo-recover {branch} --repo {publication}"
            if not self.in_publication_checkout:
                # `repo-recover` fetches the branch itself, given the checkout it is in.
                command += f" --execution-checkout {shlex.quote(str(self.checkout))}"
            return command
        command = f"just integrate {branch} --repo {publication}"
        if not self.in_publication_checkout:
            # `integrate` only ever reads local branches, so the fetch is the caller's.
            # It is a ref-only import into the publication checkout, which is all that
            # checkout is ever allowed to receive.
            # The refspec is quoted like every other interpolated value here: git
            # permits `;`, `&`, `|`, `$` and backticks in a branch name, and this
            # string is meant to be pasted into a shell.
            refspec = shlex.quote(f"refs/heads/{self.branch}:refs/heads/{self.branch}")
            fetch = f"git -C {publication} fetch {shlex.quote(str(self.checkout))} {refspec}"
            return f"{fetch} && {command}"
        return command

    def age_seconds(self, *, now: float) -> float | None:
        """How long ago this branch was last written to, or ``None`` if unreadable."""
        if self.tip_committed_at is None:
            return None
        return max(0.0, now - self.tip_committed_at)

    def describe(self, *, now: float) -> str:
        provenance = (
            "incomplete step (lifecycle provenance marker)" if self.incomplete else "complete"
        )
        elapsed = self.age_seconds(now=now)
        age = (
            "age unknown"
            if elapsed is None
            else f"{int(elapsed) // 3600}h{int(elapsed) % 3600 // 60:02d}m old"
        )
        where = (
            ""
            if self.in_publication_checkout
            else " — NOT in the publication checkout; the command below fetches it first"
        )
        return "\n".join(
            [
                f"{self.branch}  [{self.identity}]  {provenance}",
                f"    Tip: {self.tip_sha} {self.tip_subject} ({age})",
                f"    Found in: {self.checkout}{where}",
                f"    Stopped because: {self.stopped_because}",
                f"    Resume: {self.command}",
            ]
        )


def _run_clones(workspace_root: Path, identity_dir_key: str) -> Iterator[Path]:
    """Every retained run clone of one repository, newest first.

    Only the clone is read, never a worktree: a live dispatch is working in its
    worktree, and this view opens nothing it could be mid-write in.
    """
    runs = workspace_root / identity_dir_key / RUNS_DIR_NAME
    if not runs.is_dir():
        return
    try:
        candidates = [path for path in runs.iterdir() if path.is_dir() and not path.is_symlink()]
    except OSError:
        return
    for run_root in sorted(candidates, key=lambda path: path.name, reverse=True):
        clone = run_root / CLONE_DIR_NAME
        if clone.is_dir() and gitops.is_repo(clone):
            yield clone


def _stopped_because(runs_dir: Path, branch: str) -> str | None:
    """The recorded outcome of the workstream that produced this branch.

    Read from the round results the executor writes, which is where a quota death, a
    failing gate, a judge refusal, and an interrupted round each already have a name.
    """
    if not runs_dir.is_dir():
        return None
    newest: tuple[float, str] | None = None
    try:
        run_dirs = [path for path in runs_dir.iterdir() if path.is_dir()]
    except OSError:
        return None
    for run_dir in run_dirs:
        latest = latest_round(run_dir)
        if latest is None or not (result_path := latest[1] / "result.json").is_file():
            continue
        try:
            payload = as_result_payload(load_mapping(result_path))
            stamp = result_path.stat().st_mtime
        except (ConfigError, OSError):
            continue
        for node, item in payload["results"].items():
            if item.get("branch") != branch:
                continue
            reason = item.get("outcome") or item.get("error") or item["status"]
            recorded = f"{run_dir.name} node {node}: {item['status']} ({reason})"
            if newest is None or stamp > newest[0]:
                newest = (stamp, recorded)
    return None if newest is None else newest[1]


class BranchTip(NamedTuple):
    """What one preserved branch's base-relative history says about itself."""

    sha: str
    subject: str
    committed_at: float | None
    incomplete: bool


def _tip(repo: Path, branch: str, base: str) -> BranchTip | None:
    """The branch tip's short sha, subject, commit time, and whether it is incomplete.

    The incompleteness question is asked of the whole base-relative history rather
    than of the tip alone: a marker is left where the step stopped, and a later commit
    on the same branch does not make the preserved work complete.
    """
    try:
        commits = gitops.log_delta(repo, base, branch)
        messages = gitops.log_messages(repo, base, branch)
        stamp = gitops.committed_at(repo, branch)
    except gitops.GitError:
        return None
    if not commits:
        return None
    incomplete = any(is_incomplete_marker(commit.message) for commit in messages)
    return BranchTip(commits[0].sha, commits[0].subject, stamp, incomplete)


def _base_ref(repo: Path, publication: Path) -> str:
    """The ref a branch's commits are counted against: its identity's own base.

    Taken from the publication checkout's default branch, resolved in the repository
    the branch is actually in — a run clone tracks the same origin, so the remote-
    tracking ref is present in both.
    """
    default = gitops.default_branch(publication)
    remote = f"origin/{default}"
    try:
        gitops.ref_sha(repo, remote)
    except gitops.GitError:
        return default
    return remote


def collect(
    *,
    registry: Registry | None = None,
    runs_dir: Path = Path("runs"),
    workspace_root: Path | None = None,
) -> list[RecoverableBranch]:
    """Every preserved, unpublished branch across registered identities, newest first."""
    registry = registry or Registry()
    root = workspace_root or default_worktree_root()
    found: dict[tuple[str, str], RecoverableBranch] = {}
    for entry in sorted(registry.entries.values(), key=lambda item: item.path):
        checkout = Path(entry.path).expanduser()
        identity = registry.identity_for_checkout(checkout)
        if identity is None or not gitops.is_repo(checkout):
            continue
        publication = identity.publication_checkout
        try:
            base = _base_ref(publication, publication)
        except gitops.GitError:
            continue
        searched = [checkout, *(() if publication == checkout else (publication,))]
        searched.extend(_run_clones(root, normalize_repo(entry.origin).dir_key))
        for repo in searched:
            for branch in _unpublished(repo):
                key = (str(identity.identity), branch)
                if key in found:
                    continue
                described = _tip(
                    repo, branch, base if repo == publication else _base_ref(repo, publication)
                )
                if described is None:
                    continue

                found[key] = RecoverableBranch(
                    branch=branch,
                    identity=identity.identity,
                    checkout=repo,
                    publication_checkout=publication,
                    in_publication_checkout=gitops.branch_exists(publication, branch),
                    tip_sha=described.sha,
                    tip_subject=described.subject,
                    tip_committed_at=described.committed_at,
                    incomplete=described.incomplete,
                    stopped_because=(
                        _stopped_because(runs_dir, branch)
                        or (
                            "no round result names this branch; its dispatch died before "
                            "recording one"
                        )
                    ),
                )
    # Newest work first: the branch a planner is most likely looking for is the one a
    # dispatch just lost, not the one that has been sitting for days.
    return sorted(
        found.values(),
        key=lambda item: (item.tip_committed_at is None, -(item.tip_committed_at or 0.0)),
    )


def _unpublished(repo: Path) -> list[str]:
    try:
        return gitops.unpublished_branches(repo)
    except gitops.GitError:
        return []


def render(branches: Sequence[RecoverableBranch], *, now: float) -> str:
    if not branches:
        return (
            "No preserved unpublished branches. Every branch across the registered "
            "identities has reached its base or a remote."
        )
    return "\n".join(
        [
            f"{len(branches)} preserved unpublished branch(es):",
            *(branch.describe(now=now) for branch in branches),
        ]
    )


def _json_value(branch: RecoverableBranch) -> dict[str, Any]:
    return {
        "branch": branch.branch,
        "identity": str(branch.identity),
        "checkout": str(branch.checkout),
        "publication_checkout": str(branch.publication_checkout),
        "in_publication_checkout": branch.in_publication_checkout,
        "tip_sha": branch.tip_sha,
        "tip_subject": branch.tip_subject,
        "tip_committed_at": branch.tip_committed_at,
        "incomplete": branch.incomplete,
        "stopped_because": branch.stopped_because,
        "command": branch.command,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="List preserved, unpublished branches and the command that lands each."
    )
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="lifecycle worktree root to search for run clones "
        f"(default: {default_worktree_root()})",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    try:
        branches = collect(runs_dir=args.runs_dir, workspace_root=args.workspace)
    except (RegistryError, gitops.GitError, ConfigError) as exc:
        print(f"recoverable: {exc}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps([_json_value(branch) for branch in branches]))
    else:
        print(render(branches, now=time.time()))
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
