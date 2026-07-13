"""Test doubles for the two genuinely-external seams the offline gate can't run.

Git is always real (a local bare repo). Only the *paid harness* and *GitHub's
PR/CI decisioning* are faked, each at its own seam:

* `writing_dispatch` stands in for `dispatch` — it makes a real edit in the
  worktree (what a real agent would do) and returns a completed `Report`.
* `FakeGitHub` implements `GitHubBackend`, but performs the **actual merge with
  real git** against the bare origin (fast-forwarding base to the PR head) — so
  the merge is never mocked, only GitHub's PR-object and check *decisioning* is.
"""

# llmlint: ignore-file[e2e_not_mocked, protocol_based_seams] these are the two
# deliberately-faked seams for the repo-lifecycle e2e (see the module docstring and
# AGENTS.md "Tests are context engineering"): only the paid harness (the dispatch_fn
# callable) and GitHub's PR/CI decisioning are faked — git and the merge stay real, and
# the real onejudge dispatch boundary is exercised in test_dispatch_e2e.py. dispatch_fn
# is an intentional injected callable seam (typed as DispatchFn in lifecycle), not an
# ad-hoc dependency.

from __future__ import annotations

import subprocess
from pathlib import Path

from orchestrator.dispatch import Report
from orchestrator.github import AutoMergeUnavailable, Check, PRStatus, PullRequest


def _git(*args: str, cwd: str | Path) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), text=True, capture_output=True)
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr or proc.stdout}")
    return proc.stdout


def make_writing_dispatch(
    *, filename: str = "CHANGE.txt", content: str = "change", completed: bool = True
):
    """A `dispatch`-shaped fn that writes a real file into the worktree.

    ``filename=None`` writes nothing (to exercise the no-changes path);
    ``completed=False`` simulates an agent that hit the turn cap.
    """

    def dispatch_fn(persona: str, task: str, *, project_dir: str, **_: object) -> Report:
        if filename is not None:
            (Path(project_dir) / filename).write_text(f"{content} by {persona}\n", encoding="utf-8")
        return Report(
            persona=persona,
            exit_code=0 if completed else 1,
            completed=completed,
            stopped_early=False,
            assistant_turns=2,
            verdicts=[],
            usage={"output_tokens": 7},
            raw={},
            stderr="",
        )

    return dispatch_fn


class FakeGitHub:
    """A `GitHubBackend` that decides PR/CI state but merges with real git.

    ``required``: the required (blocking) check names. ``fail_checks``: the
    required checks conclude red (never merges). ``auto_available``: whether the
    repo allows native auto-merge (else `enable_auto_merge` raises, exercising the
    direct-merge fallback). The merge fast-forwards ``base`` to the PR head in the
    bare origin, so the change genuinely lands on the remote's default branch.
    """

    def __init__(
        self,
        origin: Path,
        *,
        required: tuple[str, ...] = ("ci",),
        fail_checks: bool = False,
        auto_available: bool = True,
    ) -> None:
        self.origin = origin
        self.required = required
        self.fail_checks = fail_checks
        self.auto_available = auto_available
        self._prs: dict[int, dict[str, object]] = {}
        self._n = 0

    def default_branch(self, repo: str) -> str:
        return "main"

    def create_pr(self, repo: str, *, head: str, base: str, title: str, body: str) -> PullRequest:
        self._n += 1
        self._prs[self._n] = {"head": head, "base": base, "merged": False, "auto": False}
        return PullRequest(
            number=self._n,
            url=f"https://github.com/{repo}/pull/{self._n}",
            repo=repo,
            head=head,
            base=base,
        )

    def enable_auto_merge(self, pr: PullRequest, *, method: str) -> None:
        if not self.auto_available:
            raise AutoMergeUnavailable("auto-merge is not enabled for this repository")
        self._prs[pr.number]["auto"] = True

    def merge(self, pr: PullRequest, *, method: str) -> None:
        self._do_merge(pr)

    def status(self, pr: PullRequest) -> PRStatus:
        st = self._prs[pr.number]
        state = "FAILURE" if self.fail_checks else "SUCCESS"
        checks = tuple(Check(name=c, state=state, required=True) for c in self.required)
        green = all(c.green for c in checks)
        if st["auto"] and green and not st["merged"]:
            self._do_merge(pr)  # native auto-merge fires once required checks are green
        merged = bool(st["merged"])
        return PRStatus(
            number=pr.number,
            state="MERGED" if merged else "OPEN",
            merged=merged,
            merge_state_status="CLEAN",
            checks=checks,
        )

    def _do_merge(self, pr: PullRequest) -> None:
        st = self._prs[pr.number]
        if st["merged"]:
            return
        head_sha = _git("rev-parse", f"refs/heads/{st['head']}", cwd=self.origin).strip()
        # Fast-forward base to the PR head in the bare origin — a real ref update
        # (head was branched from base, so this is always a valid fast-forward).
        _git("update-ref", f"refs/heads/{st['base']}", head_sha, cwd=self.origin)
        st["merged"] = True
