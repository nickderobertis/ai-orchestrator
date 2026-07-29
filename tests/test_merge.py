"""Unit tests for the GitHub merge strategy's policy logic (no real git/gh)."""

from __future__ import annotations

import subprocess
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


def test_local_merge_relies_on_push_hook_and_journals_the_merge(
    tmp_path: Path, bare_origin
) -> None:
    origin = bare_origin()
    clone = gitops.clone(origin, tmp_path / "clone")
    feature = gitops.worktree_add(clone, tmp_path / "feature", "feature", base="origin/main")
    (feature / "feature.txt").write_text("change\n", encoding="utf-8")
    gitops.add_all(feature)
    gitops.commit(feature, "feat: add feature")
    gitops.push(feature, "feature")
    journal, node = _scope(tmp_path, "run-local")

    out = LocalMergeStrategy().publish_and_merge(
        _ctx(clone_dir=clone, branch="feature", journal=node, gate_command=("just", "gate"))
    )

    assert out.outcome == "merged"
    events = journal.events()
    assert [e.kind for e in events] == [
        "verification-started",
        "verification-finished",
        "publication-finished",
    ]
    # A green publication keeps its evidence too: the settled result has to be able
    # to show that the merge path's gate ran, not only that nothing objected.
    assert events[1].detail["ok"] is True
    assert out.verification is not None and out.verification.ok
    assert "verdict: passed" in Path(str(out.verification.log_path)).read_text(encoding="utf-8")
    assert events[2].detail == {"pr": "local:o/r#feature", "branch": "feature", "base": "main"}


def test_an_ungated_publication_push_claims_no_verdict(tmp_path: Path, bare_origin) -> None:
    """Where required PR checks are the coverage, they decide after this push.

    Recording a bare accepted push as a passed verification would claim a verdict
    the checks have not reached, so an ungated push records none at all.
    """
    origin = bare_origin()
    clone = gitops.clone(origin, tmp_path / "clone-ungated")
    feature = gitops.worktree_add(
        clone, tmp_path / "feature-ungated", "feature", base="origin/main"
    )
    (feature / "feature.txt").write_text("change\n", encoding="utf-8")
    gitops.add_all(feature)
    gitops.commit(feature, "feat: add feature")
    gitops.push(feature, "feature")
    journal, node = _scope(tmp_path, "run-ungated")

    out = LocalMergeStrategy().publish_and_merge(
        _ctx(clone_dir=clone, branch="feature", journal=node)
    )

    assert out.outcome == "merged"
    assert out.verification is None
    assert [event.kind for event in journal.events()] == ["publication-finished"]


def test_local_merge_records_branch_content_already_on_base(tmp_path: Path, bare_origin) -> None:
    origin = bare_origin()
    clone = gitops.clone(origin, tmp_path / "clone-already-integrated")
    feature = gitops.worktree_add(
        clone, tmp_path / "feature-already-integrated", "feature", base="origin/main"
    )
    (feature / "feature.txt").write_text("change\n", encoding="utf-8")
    gitops.add_all(feature)
    gitops.commit(feature, "feat: add feature")
    gitops.push(feature, "feature")
    gitops.push(feature, "HEAD:main", set_upstream=False)
    journal, node = _scope(tmp_path, "run-already-integrated")

    out = LocalMergeStrategy().publish_and_merge(
        _ctx(clone_dir=clone, branch="feature", journal=node)
    )

    assert out.outcome == "already-integrated"
    assert out.pr is not None
    assert "already present on main" in out.detail
    assert [event.kind for event in journal.events()] == ["publication-finished"]
    assert journal.events()[0].detail["outcome"] == "already-integrated"


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
        LocalMergeStrategy().publish_and_merge(_ctx(clone_dir=clone, branch="feature"))


def test_local_merge_surfaces_non_race_push_failure(
    tmp_path: Path, bare_origin, monkeypatch
) -> None:
    origin = bare_origin()
    clone = gitops.clone(origin, tmp_path / "clone-push-failure")
    feature = gitops.worktree_add(
        clone, tmp_path / "feature-push-failure", "feature", base="origin/main"
    )
    (feature / "feature.txt").write_text("change\n", encoding="utf-8")
    gitops.add_all(feature)
    gitops.commit(feature, "feat: add feature")
    gitops.push(feature, "feature")
    monkeypatch.setattr(
        gitops,
        "push",
        lambda *args, **kwargs: (_ for _ in ()).throw(GitError("remote: permission denied")),
    )

    out = LocalMergeStrategy().publish_and_merge(_ctx(clone_dir=clone, branch="feature"))
    assert out.outcome == "error"
    assert "rebuilt local publication push failed" in out.detail
    assert "permission denied" in out.detail


def test_local_merge_records_push_gate_failure_without_claiming_a_merge(
    tmp_path: Path, bare_origin, monkeypatch
) -> None:
    origin = bare_origin()
    clone = gitops.clone(origin, tmp_path / "clone")
    feature = gitops.worktree_add(clone, tmp_path / "feature", "feature", base="origin/main")
    (feature / "feature.txt").write_text("change\n", encoding="utf-8")
    gitops.add_all(feature)
    gitops.commit(feature, "feat: add feature")
    gitops.push(feature, "feature")
    journal, node = _scope(tmp_path, "run-gate")

    monkeypatch.setattr(
        gitops,
        "push",
        lambda *args, **kwargs: (_ for _ in ()).throw(GitError("pre-push: complete gate failed")),
    )
    out = LocalMergeStrategy().publish_and_merge(
        _ctx(clone_dir=clone, branch="feature", journal=node, gate_command=("just", "gate"))
    )

    assert out.outcome == "gate-failed"
    assert "repository pre-push gate rejected" in out.detail
    assert [e.kind for e in journal.events()] == [
        "verification-started",
        "verification-finished",
    ]
    # The rejection is what a settled run has to explain, so its output is kept.
    assert out.verification is not None and not out.verification.ok
    preserved = Path(str(out.verification.log_path)).read_text(encoding="utf-8")
    assert "verdict: FAILED" in preserved
    assert "pre-push: complete gate failed" in preserved
    assert str(out.verification.log_path) in out.detail
    # The rebuilt merge never reached the base branch, so nothing may say it did.
    assert not (gitops.clone(origin, tmp_path / "check") / "feature.txt").exists()


def test_exhausted_publication_retries_preserve_what_each_attempt_saw(
    tmp_path: Path, bare_origin, monkeypatch
) -> None:
    """The failure that killed this node's own previous run preserved nothing.

    A base advancing under the rebuild ends publication in seconds, with no gate
    ever consulted. It used to settle as one sentence asserting a race and keep no
    observation behind it, so a base moved by a sibling run, a genuine concurrent
    publisher, and a synthetic rejection were indistinguishable. The advance here
    is a real push from a real second clone, scheduled at a real seam.
    """
    origin = bare_origin()
    clone = gitops.clone(origin, tmp_path / "clone-exhausted")
    feature = gitops.worktree_add(
        clone, tmp_path / "feature-exhausted", "feature", base="origin/main"
    )
    (feature / "feature.txt").write_text("change\n", encoding="utf-8")
    gitops.add_all(feature)
    gitops.commit(feature, "feat: add feature")
    gitops.push(feature, "feature")
    rival = gitops.clone(origin, tmp_path / "rival")
    advances = 0
    real_merge_squash = gitops.merge_squash

    def advance_base_then_squash(*args: object, **kwargs: object) -> object:
        """Let a sibling publisher land on the base mid-rebuild, for real."""
        nonlocal advances
        advances += 1
        (rival / f"rival-{advances}.txt").write_text("rival\n", encoding="utf-8")
        gitops.add_all(rival)
        gitops.commit(rival, f"chore: rival publication {advances}")
        gitops.push(rival, "HEAD:main", set_upstream=False)
        return real_merge_squash(*args, **kwargs)

    monkeypatch.setattr(gitops, "merge_squash", advance_base_then_squash)
    journal, node = _scope(tmp_path, "run-exhausted")

    out = LocalMergeStrategy().publish_and_merge(
        _ctx(
            clone_dir=clone,
            branch="feature",
            journal=node,
            publication_attempts=2,
            gate_command=("just", "gate"),
        )
    )

    assert out.outcome == "publication-retries-exhausted"
    assert advances == 2
    ((failure,),) = ([e for e in journal.events() if e.kind == "publication-failed"],)
    tail = str(failure.detail["output_tail"])
    assert "attempt 1:" in tail and "attempt 2:" in tail
    assert "is now" in tail  # the observed base sha, not just the expected one
    preserved = Path(str(failure.detail["log_path"])).read_text(encoding="utf-8")
    assert "outcome: publication-retries-exhausted" in preserved
    assert str(failure.detail["log_path"]) in out.detail
    # And the base is untouched: nothing was published without a gate.
    assert not _has_path(origin, "main", "feature.txt")


def _has_path(origin: Path, ref: str, path: str) -> bool:
    return (
        subprocess.run(
            ["git", "-C", str(origin), "cat-file", "-e", f"{ref}:{path}"],
            capture_output=True,
        ).returncode
        == 0
    )


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
        )
    )

    assert attempts == 2
    assert outcome.outcome == "publication-retries-exhausted"
    assert "all 2 attempts" in outcome.detail
