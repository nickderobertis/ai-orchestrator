"""Unit tests for the GitHub merge strategy's policy logic (no real git/gh)."""

from __future__ import annotations

from pathlib import Path

from orchestrator import gitops
from orchestrator.github import AutoMergeUnavailable, Check, PRStatus, PullRequest
from orchestrator.gitops import GitError
from orchestrator.merge import (
    GitHubMergeStrategy,
    LocalMergeStrategy,
    MergeContext,
    _is_push_race,
)


class PolicyBackend:
    """A minimal GitHubBackend to exercise _drive_github_merge branches."""

    def __init__(self, *, checks, auto_available=True, merge_on="auto") -> None:
        self.checks = checks  # tuple[Check, ...]
        self.auto_available = auto_available
        self.merge_on = merge_on  # "auto" (native), "direct" (only on merge()), "never"
        self.merged = False
        self.state = "OPEN"
        self.enabled = False
        self.ready = False

    def default_branch(self, repo):  # pragma: no cover - unused here
        return "main"

    def create_pr(self, repo, *, head, base, title, body):
        return PullRequest(number=1, url="u", repo=repo, head=head, base=base)

    def mark_ready(self, pr):
        self.ready = True

    def enable_auto_merge(self, pr, *, method):
        if not self.auto_available:
            raise AutoMergeUnavailable("auto-merge is not enabled")
        self.enabled = True

    def merge(self, pr, *, method):
        if self.merge_on in ("direct", "auto"):
            self.merged = True
            self.state = "MERGED"

    def status(self, pr):
        green = all(c.green for c in self.checks if c.required) if self.checks else True
        if self.merge_on == "auto" and self.enabled and green and not self.merged:
            self.merged = True
            self.state = "MERGED"
        return PRStatus(1, self.state, self.merged, "CLEAN", self.checks)


class DraftBackend(PolicyBackend):
    def __init__(self) -> None:
        super().__init__(checks=(Check("ci", "SUCCESS", True),), merge_on="direct")
        self._draft = True

    def mark_ready(self, pr):
        super().mark_ready(pr)
        self._draft = False

    def status(self, pr):
        status = super().status(pr)
        return PRStatus(
            status.number,
            status.state,
            status.merged,
            status.merge_state_status,
            status.checks,
            draft=self._draft,
        )


def _ctx(**kw) -> MergeContext:
    base = dict(
        repo_slug="o/r",
        clone_dir=Path("."),
        base="main",
        branch="feat",
        title="t",
        body="b",
        sleep=lambda _: None,
    )
    base.update(kw)
    return MergeContext(**base)


def test_policy_none_opens_pr_only() -> None:
    backend = PolicyBackend(checks=())
    out = GitHubMergeStrategy(backend).publish_and_merge(_ctx(policy="none"))
    assert out.outcome == "pr-open" and out.pr is not None and not backend.merged


def test_auto_merges_when_required_green() -> None:
    checks = (Check("ci", "SUCCESS", True),)
    backend = PolicyBackend(checks=checks, merge_on="auto")
    out = GitHubMergeStrategy(backend).publish_and_merge(_ctx(policy="auto"))
    assert out.outcome == "merged" and backend.enabled


def test_auto_unavailable_falls_back_to_direct() -> None:
    checks = (Check("ci", "SUCCESS", True),)
    backend = PolicyBackend(checks=checks, auto_available=False, merge_on="direct")
    out = GitHubMergeStrategy(backend).publish_and_merge(_ctx(policy="auto"))
    assert out.outcome == "merged"
    assert "unavailable" in out.detail


def test_direct_merges_on_green() -> None:
    checks = (Check("ci", "SUCCESS", True),)
    backend = PolicyBackend(checks=checks, merge_on="direct")
    out = GitHubMergeStrategy(backend).publish_and_merge(_ctx(policy="direct"))
    assert out.outcome == "merged"


def test_draft_pr_is_marked_ready_before_merge() -> None:
    backend = DraftBackend()
    out = GitHubMergeStrategy(backend).publish_and_merge(_ctx(policy="direct"))
    assert out.outcome == "merged"
    assert backend.ready


def test_required_failure_is_checks_failed() -> None:
    checks = (Check("ci", "FAILURE", True),)
    backend = PolicyBackend(checks=checks, merge_on="never")
    out = GitHubMergeStrategy(backend).publish_and_merge(_ctx(policy="auto"))
    assert out.outcome == "checks-failed" and "ci" in out.detail


def test_closed_pr_reported() -> None:
    backend = PolicyBackend(checks=())
    backend.state = "CLOSED"
    out = GitHubMergeStrategy(backend).publish_and_merge(_ctx(policy="direct"))
    assert out.outcome == "closed"


class DelayedDirectBackend:
    """A backend whose merge is observed only on a later poll (exercises the loop)."""

    def __init__(self) -> None:
        self.polls = 0

    def create_pr(self, repo, *, head, base, title, body):
        return PullRequest(1, "u", repo, head, base)

    def merge(self, pr, *, method):
        pass  # merge is "in flight"; status reflects it a poll later

    def status(self, pr):
        self.polls += 1
        merged = self.polls >= 4
        checks = (Check("ci", "SUCCESS", True),)
        return PRStatus(1, "MERGED" if merged else "OPEN", merged, "CLEAN", checks)


def test_direct_merge_observed_after_a_later_poll() -> None:
    slept = []
    backend = DelayedDirectBackend()
    out = GitHubMergeStrategy(backend).publish_and_merge(
        _ctx(policy="direct", timeout=1000.0, sleep=slept.append)
    )
    assert out.outcome == "merged"
    assert slept  # it looped (slept) before observing the merge


def test_timeout_when_never_merges() -> None:
    # Checks are green and auto is enabled, but the backend never flips to merged;
    # an advancing clock forces the timeout branch.
    checks = (Check("ci", "SUCCESS", True),)
    backend = PolicyBackend(checks=checks, merge_on="never")
    ticks = iter([0.0, 100.0, 200.0])
    out = GitHubMergeStrategy(backend).publish_and_merge(
        _ctx(policy="auto", timeout=10.0, clock=lambda: next(ticks))
    )
    assert out.outcome == "timeout"


def test_push_race_classification_is_narrow() -> None:
    assert _is_push_race(GitError("! [rejected] HEAD -> main (fetch first)"))
    assert _is_push_race(GitError("! [rejected] HEAD -> main (non-fast-forward)"))
    assert not _is_push_race(GitError("remote: permission denied"))


def test_local_publication_classifies_retry_exhaustion(tmp_path, bare_origin, monkeypatch) -> None:
    origin = bare_origin()
    clone = gitops.clone(origin, tmp_path / "clone")
    feature = gitops.worktree_add(clone, tmp_path / "feature", "feature", base="origin/main")
    (feature / "feature.txt").write_text("change\n", encoding="utf-8")
    gitops.add_all(feature)
    gitops.commit(feature, "feat: add feature")
    gitops.push(feature, "feature")
    attempts = 0

    def reject_push(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise GitError("! [rejected] HEAD -> main (fetch first)")

    monkeypatch.setattr(gitops, "push", reject_push)
    outcome = LocalMergeStrategy().publish_and_merge(
        _ctx(
            clone_dir=clone,
            branch="feature",
            publication_attempts=2,
            verify_command=["true"],
        )
    )

    assert attempts == 2
    assert outcome.outcome == "publication-retries-exhausted"
    assert "all 2 attempts" in outcome.detail
