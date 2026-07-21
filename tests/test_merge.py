"""Unit tests for the GitHub merge strategy's policy logic (no real git/gh)."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from orchestrator import gitops
from orchestrator.github import AutoMergeUnavailable, Check, PRStatus, PullRequest
from orchestrator.gitops import GitError
from orchestrator.journal import Journal, NodeJournal, open_journal
from orchestrator.merge import (
    GitHubMergeStrategy,
    LocalMergeStrategy,
    MergeContext,
    _is_push_race,
)
from orchestrator.runs import NodeId, RunId


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


def test_only_single_owner_automated_github_publication_uses_queue(monkeypatch) -> None:
    turns: list[str] = []

    @contextmanager
    def record_turn(identity):
        turns.append(str(identity))
        yield

    monkeypatch.setattr("orchestrator.merge.merge_queue_turn", record_turn)
    checks = (Check("ci", "SUCCESS", True),)
    GitHubMergeStrategy(PolicyBackend(checks=checks, merge_on="auto")).publish_and_merge(
        _ctx(policy="auto")
    )
    GitHubMergeStrategy(PolicyBackend(checks=checks, merge_on="auto")).publish_and_merge(
        _ctx(policy="auto", repository_type="team")
    )
    GitHubMergeStrategy(PolicyBackend(checks=())).publish_and_merge(_ctx(policy="none"))

    assert len(turns) == 1
    assert turns[0].startswith("git:")


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
        return PRStatus(
            1,
            "MERGED" if merged else "OPEN",
            merged,
            "CLEAN",
            checks,
            merge_in_progress=not merged,
        )


def test_direct_merge_observed_after_a_later_poll() -> None:
    slept = []
    backend = DelayedDirectBackend()
    out = GitHubMergeStrategy(backend).publish_and_merge(
        _ctx(policy="direct", timeout=1000.0, sleep=slept.append)
    )
    assert out.outcome == "merged"
    assert slept  # it looped (slept) before observing the merge


def test_check_poll_backoff_is_bounded() -> None:
    slept: list[float] = []
    backend = DelayedDirectBackend()
    backend.polls = -3
    out = GitHubMergeStrategy(backend).publish_and_merge(
        _ctx(
            policy="direct",
            timeout=1000.0,
            poll_interval=2.0,
            max_poll_interval=5.0,
            sleep=slept.append,
        )
    )
    assert out.outcome == "merged"
    assert slept == [2.0, 4.0, 5.0, 5.0]


def test_settled_green_checks_fail_when_auto_merge_never_merges() -> None:
    checks = (Check("ci", "SUCCESS", True),)
    backend = PolicyBackend(checks=checks, merge_on="never")
    out = GitHubMergeStrategy(backend).publish_and_merge(
        _ctx(policy="auto", timeout=10_000.0, sleep=lambda _: (_ for _ in ()).throw(AssertionError))
    )
    assert out.outcome == "error"
    assert "ci=SUCCESS" in out.detail


def test_optional_pending_check_does_not_prevent_settlement() -> None:
    checks = (Check("ci", "SUCCESS", True), Check("preview", "PENDING", False))
    backend = PolicyBackend(checks=checks, merge_on="never")
    out = GitHubMergeStrategy(backend).publish_and_merge(_ctx(policy="auto"))
    assert out.outcome == "error"
    assert "ci=SUCCESS" in out.detail
    assert "preview" not in out.detail


def test_timeout_remains_a_backstop_for_a_required_check_that_never_settles() -> None:
    backend = PolicyBackend(checks=(Check("ci", "PENDING", True),), merge_on="never")
    ticks = iter([0.0, 100.0])
    out = GitHubMergeStrategy(backend).publish_and_merge(
        _ctx(policy="auto", timeout=10.0, clock=lambda: next(ticks))
    )
    assert out.outcome == "timeout"


# --- journaled publication transitions -------------------------------------
#
# Wired the way the lifecycle wires it: a real `Journal` on disk, narrowed to the
# node the merge is running for. A strategy is handed that scope and never names
# a node itself, so these prove both the transitions and the binding.


def _scope(tmp_path: Path, run: str) -> tuple[Journal, NodeJournal]:
    journal = open_journal(tmp_path / run, RunId(run), 1)
    return journal, NodeJournal(sink=journal, node=NodeId("api"), run_id=RunId(run), round=1)


def test_github_merge_journals_the_publication_it_drove(tmp_path: Path) -> None:
    journal, node = _scope(tmp_path, "run-gh")
    backend = PolicyBackend(checks=(Check("ci", "SUCCESS", True),), merge_on="auto")

    out = GitHubMergeStrategy(backend).publish_and_merge(_ctx(policy="auto", journal=node))

    assert out.outcome == "merged"
    events = journal.events()
    assert [e.kind for e in events] == [
        "pr-created",
        "pr-checks-observed",
        "pr-merged",
        "publication-finished",
    ]
    # Every transition is attributed to the node the merge ran for, though the
    # strategy never names one.
    assert {e.node for e in events} == {"api"}
    assert events[0].detail == {
        "repo": "o/r",
        "pr": "u",
        "number": 1,
        "base": "main",
        "draft": False,
    }
    assert events[1].detail["blocking"] == [{"name": "ci", "state": "SUCCESS"}]


def test_github_merge_journals_the_ready_transition_for_a_draft(tmp_path: Path) -> None:
    journal, node = _scope(tmp_path, "run-draft")

    out = GitHubMergeStrategy(DraftBackend()).publish_and_merge(_ctx(policy="direct", journal=node))

    assert out.outcome == "merged"
    assert [e.kind for e in journal.events()] == [
        "pr-created",
        "pr-ready",
        "pr-checks-observed",
        "pr-merged",
        "publication-finished",
    ]


def test_github_merge_does_not_journal_a_merge_that_did_not_happen(tmp_path: Path) -> None:
    """A red required check settles as checks-failed, and nothing claims a merge."""
    journal, node = _scope(tmp_path, "run-red")
    backend = PolicyBackend(checks=(Check("ci", "FAILURE", True),), merge_on="never")

    out = GitHubMergeStrategy(backend).publish_and_merge(_ctx(policy="auto", journal=node))

    assert out.outcome == "checks-failed"
    kinds = [e.kind for e in journal.events()]
    assert kinds == ["pr-created", "pr-checks-observed"]
    assert "pr-merged" not in kinds


def test_local_merge_journals_verification_and_the_merge(tmp_path: Path, bare_origin) -> None:
    origin = bare_origin()
    clone = gitops.clone(origin, tmp_path / "clone")
    feature = gitops.worktree_add(clone, tmp_path / "feature", "feature", base="origin/main")
    (feature / "feature.txt").write_text("change\n", encoding="utf-8")
    gitops.add_all(feature)
    gitops.commit(feature, "feat: add feature")
    gitops.push(feature, "feature")
    journal, node = _scope(tmp_path, "run-local")

    out = LocalMergeStrategy().publish_and_merge(
        _ctx(clone_dir=clone, branch="feature", verify_command=["true"], journal=node)
    )

    assert out.outcome == "merged"
    events = journal.events()
    assert [e.kind for e in events] == [
        "verification-started",
        "verification-finished",
        "publication-finished",
    ]
    assert events[0].detail == {"command": ["true"], "attempt": 1}
    assert events[1].detail == {"ok": True, "command": ["true"], "attempt": 1}
    assert events[2].detail == {"pr": "local:o/r#feature", "branch": "feature", "base": "main"}


def test_local_merge_rejects_zero_publication_attempts(tmp_path: Path, bare_origin) -> None:
    clone = gitops.clone(bare_origin(), tmp_path / "clone-zero-attempts")

    with pytest.raises(ValueError, match="at least 1"):
        LocalMergeStrategy().publish_and_merge(_ctx(clone_dir=clone, publication_attempts=0))


def test_local_merge_cleans_scratch_worktree_after_content_conflict(
    tmp_path: Path, bare_origin
) -> None:
    origin = bare_origin({"shared.txt": "original\n"})
    clone = gitops.clone(origin, tmp_path / "clone-conflict")
    feature = gitops.worktree_add(
        clone, tmp_path / "feature-conflict", "feature", base="origin/main"
    )
    (feature / "shared.txt").write_text("feature\n", encoding="utf-8")
    gitops.add_all(feature)
    gitops.commit(feature, "feat: edit shared file")
    gitops.push(feature, "feature")
    updater = gitops.clone(origin, tmp_path / "updater-conflict")
    (updater / "shared.txt").write_text("base\n", encoding="utf-8")
    gitops.add_all(updater)
    gitops.commit(updater, "feat: advance base")
    gitops.push(updater, "main")

    with pytest.raises(GitError):
        LocalMergeStrategy().publish_and_merge(
            _ctx(clone_dir=clone, branch="feature", verify_command=["true"])
        )


def test_local_merge_journals_a_gate_failure_without_claiming_a_merge(
    tmp_path: Path, bare_origin
) -> None:
    origin = bare_origin()
    clone = gitops.clone(origin, tmp_path / "clone")
    feature = gitops.worktree_add(clone, tmp_path / "feature", "feature", base="origin/main")
    (feature / "feature.txt").write_text("change\n", encoding="utf-8")
    gitops.add_all(feature)
    gitops.commit(feature, "feat: add feature")
    gitops.push(feature, "feature")
    journal, node = _scope(tmp_path, "run-gate")

    out = LocalMergeStrategy().publish_and_merge(
        _ctx(clone_dir=clone, branch="feature", verify_command=["false"], journal=node)
    )

    assert out.outcome == "gate-failed"
    events = journal.events()
    assert [e.kind for e in events] == ["verification-started", "verification-finished"]
    assert events[1].detail["ok"] is False
    # The rebuilt merge never reached the base branch, so nothing may say it did.
    assert not (gitops.clone(origin, tmp_path / "check") / "feature.txt").exists()


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
