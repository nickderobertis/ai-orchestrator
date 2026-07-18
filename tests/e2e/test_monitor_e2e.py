"""E2E: the monitor's sources against the real things they read, and the real command.

Git is real throughout — a real repository with real commits, read through the same
`gitops.log_delta` the lifecycle uses. GitHub's PR/CI decisioning is faked at the
sanctioned `GitHubBackend` seam (the double the lifecycle e2e already uses), and the
oneharness history source is driven through a real subprocess, because a subprocess
is the boundary that source actually crosses. Nothing inside the monitor is patched:
every test calls the same functions `just monitor` calls, or `just monitor` itself.
"""

# llmlint: ignore-file[e2e_not_mocked] the two faked seams are the repo's sanctioned
# ones (GitHub's PR/CI decisioning via the shared FakeGitHub, and the paid harness —
# here the dispatched sessions oneharness would have recorded, served by
# fake_oneharness.py over the real `oneharness history list` subprocess boundary).
# Git, the journal, the ledger, the snapshot, and the monitor itself are all real.

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fakes import FakeGitHub

from orchestrator import REPO_ROOT, gitops
from orchestrator.github import Check, GitHubError, PRStatus, PullRequest
from orchestrator.journal import open_journal
from orchestrator.monitor import (
    HEADER,
    BranchRef,
    DetailSnapshot,
    Monitor,
    PrRef,
    RoundLedger,
    git_events,
    history_events,
    known_branches,
    known_prs,
    load_snapshot,
    pr_events,
    save_snapshot,
    snapshot_path,
)
from orchestrator.registry import Registry
from orchestrator.runs import NodeId, RunId, prepare_round, write_result

FAKE_ONEHARNESS = REPO_ROOT / "tests" / "e2e" / "fake_oneharness.py"

RUN = RunId("watch-me")
AT = datetime(2026, 7, 14, 12, 0, 0, tzinfo=UTC).timestamp()
BRANCH = BranchRef("local/app", "work", "main")
PLAN: dict[str, Any] = {
    "concurrency": 1,
    "tasks": [{"id": "api", "persona": "engineer", "task": "ship it"}],
}


def _git(*args: str, cwd: str | Path | None = None) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd) if cwd else None, text=True, capture_output=True
    )
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr or proc.stdout}")
    return proc.stdout


def _repo(tmp_path: Path, bare_origin: Callable[..., Path]) -> Path:
    """A real clone whose `work` branch carries two real commits over `main`.

    Cloned rather than `git init`-ed because a checkout the lifecycle works in always
    has an origin, and that origin is what the registry identifies it by.
    """
    repo = tmp_path / "app"
    _git("clone", str(bare_origin()), str(repo))
    _git("checkout", "-b", "work", cwd=repo)
    for name, subject in (("a.txt", "feat: add a"), ("b.txt", "fix: add b")):
        (repo / name).write_text(f"{name}\n", encoding="utf-8")
        _git("add", "-A", cwd=repo)
        _git("commit", "-m", subject, cwd=repo)
    return repo


def _settle(run_dir: Path, results: dict[str, Any], *, ok: bool, state: str) -> Path:
    """Record one finished round through the real ledger writer."""
    _, round_dir = prepare_round(run_dir, PLAN)
    write_result(
        round_dir,
        {"ok": ok, "state": state, "started_order": sorted(results), "results": results},
    )
    return round_dir


class _Offline:
    """A GitHub that cannot be reached: no `gh`, no auth, or no network."""

    def __init__(self) -> None:
        self.asked: list[int] = []

    def status(self, pr: PullRequest) -> PRStatus:
        self.asked.append(pr.number)
        raise GitHubError("gh: command not found")


class _MutableChecks:
    """The sanctioned GitHub seam with a rollup a live poll can observe changing."""

    def __init__(self, *checks: Check) -> None:
        self.checks = list(checks)

    def status(self, pr: PullRequest) -> PRStatus:
        return PRStatus(
            number=pr.number,
            state="OPEN",
            merged=False,
            merge_state_status="CLEAN",
            checks=tuple(self.checks),
        )


# --- the git source, and what outlives the branch ------------------------------


def test_the_git_source_reports_each_real_commit_under_a_stable_id(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    """A commit is immutable, so its sha *is* its durable identity — which is what
    lets the dedup fire it exactly once however many passes re-read the branch."""
    repo = _repo(tmp_path, bare_origin)
    snapshot = DetailSnapshot()

    events = git_events([BRANCH], {"local/app": repo}, snapshot, now=AT)
    assert [event.summary for event in events] == [
        "commit work fix: add b",
        "commit work feat: add a",
    ]
    assert {event.source for event in events} == {"git"}

    shas = [commit.sha for commit in gitops.log_delta(repo, "main", "work")]
    assert [str(event.stream_id) for event in events] == [f"git:local/app@{sha}" for sha in shas]

    # Re-reading the branch yields the same keys, so a poll loop reports nothing new.
    again = git_events([BRANCH], {"local/app": repo}, snapshot, now=AT)
    assert [event.key for event in again] == [event.key for event in events]


def test_a_replay_reports_the_commits_git_can_no_longer_show(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    """A branch is deleted once its PR merges and a clone is thrown away, so a replay
    that only re-derived commits from git would show *fewer* of them the longer ago
    the run was — the opposite of a durable record."""
    repo = _repo(tmp_path, bare_origin)
    run_dir = tmp_path / "runs" / RUN
    checkouts = {"local/app": repo}

    snapshot = DetailSnapshot()
    live = git_events([BRANCH], checkouts, snapshot, now=AT)
    save_snapshot(run_dir, snapshot)
    assert len(live) == 2

    _git("checkout", "main", cwd=repo)
    _git("branch", "-D", "work", cwd=repo)

    # Nothing persisted means nothing to replay: the snapshot is what carries these.
    assert git_events([BRANCH], checkouts, DetailSnapshot(), now=AT) == []

    # Keyed by durable identity, so a replay names the same commits the live pass did.
    # Order within one pass is not part of that contract: every git event in a pass
    # shares its timestamp, and the snapshot is a mapping keyed by id.
    replayed = git_events([BRANCH], checkouts, load_snapshot(run_dir), now=AT)
    assert sorted(event.key for event in replayed) == sorted(event.key for event in live)
    assert sorted(event.summary for event in replayed) == sorted(event.summary for event in live)

    # The same holds once the clone itself is gone.
    thrown_away = git_events([BRANCH], {}, load_snapshot(run_dir), now=AT)
    assert sorted(event.key for event in thrown_away) == sorted(event.key for event in live)


def test_a_replay_reports_the_commits_that_are_now_in_the_base(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    """`base..branch` legitimately empties as the base catches up. The run's commits
    did not stop being the run's commits when the merge landed."""
    repo = _repo(tmp_path, bare_origin)
    run_dir = tmp_path / "runs" / RUN
    checkouts = {"local/app": repo}

    snapshot = DetailSnapshot()
    live = git_events([BRANCH], checkouts, snapshot, now=AT)
    save_snapshot(run_dir, snapshot)

    _git("checkout", "main", cwd=repo)
    _git("merge", "--ff-only", "work", cwd=repo)
    assert gitops.log_delta(repo, "main", "work") == []

    replayed = git_events([BRANCH], checkouts, load_snapshot(run_dir), now=AT)
    assert sorted(event.key for event in replayed) == sorted(event.key for event in live)


def test_the_snapshot_widens_what_git_shows_rather_than_replacing_it(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    """Git is the only source for a commit pushed since the last pass."""
    repo = _repo(tmp_path, bare_origin)
    run_dir = tmp_path / "runs" / RUN
    checkouts = {"local/app": repo}

    snapshot = DetailSnapshot()
    git_events([BRANCH], checkouts, snapshot, now=AT)
    save_snapshot(run_dir, snapshot)

    (repo / "c.txt").write_text("c\n", encoding="utf-8")
    _git("add", "-A", cwd=repo)
    _git("commit", "-m", "docs: add c", cwd=repo)

    events = git_events([BRANCH], checkouts, load_snapshot(run_dir), now=AT)
    assert sorted(event.summary for event in events) == [
        "commit work docs: add c",  # only git knows this one...
        "commit work feat: add a",  # ...and the snapshot agrees about the rest
        "commit work fix: add b",
    ]


# --- the PR source -------------------------------------------------------------


def test_every_pr_check_transition_says_whether_it_is_required() -> None:
    """Optional checks are observable too; the classification precedes an unbounded name."""
    pull = PullRequest(1, "https://github.com/acme/app/pull/1", "acme/app", "work", "main")
    refs = [PrRef("acme/app", pull.number, pull.url, "main")]
    snapshot = DetailSnapshot()
    github = _MutableChecks(
        Check("ci", "FAILURE", True),
        Check("lint", "FAILURE", False),
    )
    seen: set[str] = set()

    def fresh() -> list[Any]:
        events = pr_events(refs, snapshot, now=AT, github=github, replay=not seen)
        result = [event for event in events if event.key not in seen]
        seen.update(event.key for event in result)
        return result

    initial = fresh()
    assert [(event.kind, event.summary) for event in initial] == [
        ("pr-state", "PR #1 open clean"),
        ("pr-check", "PR #1 required check failure ci"),
        ("pr-check", "PR #1 optional check failure lint"),
    ]
    assert fresh() == []

    github.checks[1] = Check("lint", "SUCCESS", False)
    assert [event.summary for event in fresh()] == ["PR #1 optional check success lint"]

    github.checks[0] = Check("ci", "SUCCESS", True)
    assert [event.summary for event in fresh()] == ["PR #1 required check success ci"]

    github.checks[0] = Check("ci", "SUCCESS", False)
    assert [event.summary for event in fresh()] == ["PR #1 optional check success ci"]

    github.checks[0] = Check("ci", "FAILURE", False)
    assert [event.summary for event in fresh()] == ["PR #1 optional check failure ci"]
    github.checks[0] = Check("ci", "SUCCESS", False)
    assert [event.summary for event in fresh()] == ["PR #1 optional check success ci"]

    github.checks[1] = Check("style", "SUCCESS", False)
    assert [event.summary for event in fresh()] == [
        "PR #1 optional check success style",
        "PR #1 optional check removed lint",
    ]

    github.checks.append(Check("x" * 120 + "\nrenamed", "ERROR", False))
    long_event = fresh()
    assert len(long_event) == 1
    assert long_event[0].summary.startswith("PR #1 optional check error ")
    assert len(long_event[0].summary) == 96
    assert "\n" not in long_event[0].summary


def test_monitor_poll_deduplicates_checks_but_emits_an_optional_only_change(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / RUN
    open_journal(run_dir, RUN, 1).append(
        "pr-created",
        node=NodeId("api"),
        detail={
            "repo": "acme/app",
            "pr": "https://github.com/acme/app/pull/1",
            "base": "main",
        },
    )
    github = _MutableChecks(
        Check("ci", "SUCCESS", True),
        Check("lint", "PENDING", False),
    )
    monitor = Monitor(
        run_id=RUN,
        run_dir=run_dir,
        oneharness_bin=str(tmp_path / "absent"),
        github=github,
        clock=lambda: AT,
    )

    assert [event.summary for event in monitor.poll() if event.source == "pr"] == [
        "PR #1 open clean",
        "PR #1 required check success ci",
        "PR #1 optional check pending lint",
    ]
    assert monitor.snapshot.check_rollup.last_completed_check == "ci"
    assert monitor.snapshot.check_rollup.current_blocker == ""
    assert monitor.poll() == []

    github.checks[0] = Check("ci", "PENDING", True)
    assert [event.summary for event in monitor.poll()] == ["PR #1 required check pending ci"]
    assert monitor.snapshot.check_rollup.current_blocker == "ci: pending"
    save_snapshot(run_dir, monitor.snapshot)
    _settle(run_dir, {"api": {"status": "waiting"}}, ok=False, state="waiting")
    reported = _monitor_cli(
        "--runs-dir", str(tmp_path / "runs"), "--once", "--format", "jsonl", RUN
    )
    assert reported.returncode == 0, reported.stderr
    assert json.loads(reported.stdout.splitlines()[-1])["current_blocker"] == "ci: pending"
    github.checks[0] = Check("ci", "SUCCESS", True)
    assert [event.summary for event in monitor.poll()] == ["PR #1 required check success ci"]
    assert monitor.snapshot.check_rollup.current_blocker == ""

    github.checks[1] = Check("lint", "FAILURE", False)
    assert [event.summary for event in monitor.poll()] == ["PR #1 optional check failure lint"]
    assert monitor.poll() == []

    github.checks[1] = Check("lint", "PENDING", False)
    assert [event.summary for event in monitor.poll()] == ["PR #1 optional check pending lint"]
    assert monitor.poll() == []


def test_monitor_command_reports_one_rollup_across_multiple_prs(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / RUN
    journal = open_journal(run_dir, RUN, 1)
    for number in (1, 2):
        journal.append(
            "pr-created",
            node=NodeId(f"api-{number}"),
            detail={
                "repo": "acme/app",
                "pr": f"https://github.com/acme/app/pull/{number}",
                "base": "main",
            },
        )

    class PerPrChecks:
        def status(self, pr: PullRequest) -> PRStatus:
            check = Check("ci", "PENDING" if pr.number == 1 else "SUCCESS", True)
            return PRStatus(pr.number, "OPEN", False, "CLEAN", (check,))

    monitor = Monitor(
        run_id=RUN,
        run_dir=run_dir,
        oneharness_bin=str(tmp_path / "absent"),
        github=PerPrChecks(),
        clock=lambda: AT,
    )
    # llmlint: ignore[tests_mirror_real_usage] GitHub's sanctioned backend seam produces
    # the external transition; the assertions below consume it through `just monitor`.
    monitor.poll()
    _settle(
        run_dir,
        {"api-1": {"status": "waiting"}, "api-2": {"status": "done"}},
        ok=False,
        state="waiting",
    )
    reported = _monitor_cli(
        "--runs-dir", str(tmp_path / "runs"), "--once", "--format", "jsonl", RUN
    )
    assert reported.returncode == 0, reported.stderr
    rollup = json.loads(reported.stdout.splitlines()[-1])
    assert rollup["last_completed_check"] == "ci"
    assert rollup["current_blocker"] == "PR #1 ci: pending"


def test_monitor_command_ignores_an_old_snapshot_contract(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / RUN
    details = snapshot_path(run_dir)
    details.parent.mkdir(parents=True)
    details.write_text(
        json.dumps(
            {
                "version": 1,
                "commits": {},
                "prs": {},
                "check_rollup": {"current_blocker": "stale: pending"},
            }
        ),
        encoding="utf-8",
    )
    _settle(run_dir, {"api": {"status": "waiting"}}, ok=False, state="waiting")
    reported = _monitor_cli("--runs-dir", str(runs_dir), "--once", "--format", "jsonl", RUN)
    assert reported.returncode == 0, reported.stderr
    heartbeat = json.loads(reported.stdout.splitlines()[-1])
    assert "current_blocker" not in heartbeat


def test_a_replay_reports_the_pr_state_gh_can_no_longer_be_asked_for(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    """GitHub outlives the round but is not reproducible from the run directory, and a
    replay may have no `gh`, no auth, and no network."""
    github = FakeGitHub(bare_origin())
    pull = github.create_pr("acme/app", head="work", base="main", title="t", body="b")
    refs = [PrRef("acme/app", pull.number, pull.url, "main")]
    run_dir = tmp_path / "runs" / RUN

    snapshot = DetailSnapshot()
    live = pr_events(refs, snapshot, now=AT, github=github)
    save_snapshot(run_dir, snapshot)
    assert [event.summary for event in live] == [
        "PR #1 open clean",
        "PR #1 required check success ci",
    ]

    offline = _Offline()
    assert pr_events(refs, DetailSnapshot(), now=AT, github=offline) == []

    replayed = pr_events(refs, load_snapshot(run_dir), now=AT, github=offline)
    assert [event.key for event in replayed] == [event.key for event in live]
    assert [event.summary for event in replayed] == [event.summary for event in live]
    # It asked both times, and fell back only because it was refused an answer.
    assert offline.asked == [1, 1]


@pytest.mark.parametrize(
    ("identity", "number", "why"),
    [
        ("local/app", 1, "a local direct merge synthesizes a PR so the result has a ref"),
        ("acme/app", 0, "and numbers it 0, which the typed-id grammar never admits"),
    ],
)
def test_a_pr_that_was_never_opened_is_never_asked_about(
    identity: str, number: int, why: str
) -> None:
    """Asking `gh` about one would be a guaranteed error rather than a degradation."""
    offline = _Offline()
    refs = [PrRef(identity, number, f"https://github.com/{identity}/pull/{number}", "main")]
    assert pr_events(refs, DetailSnapshot(), now=AT, github=offline) == []
    assert offline.asked == []


# --- what the sources are pointed at -------------------------------------------


def _ledger(**fields: Any) -> RoundLedger:
    return RoundLedger(
        1,
        {
            "ok": True,
            "state": "complete",
            "started_order": ["api"],
            "results": {"api": {"status": "done", **fields}},
        },
    )


def test_a_branch_is_streamable_from_the_journal_before_the_round_records_it(
    tmp_path: Path,
) -> None:
    """The journal records `branch-discovered` the moment a worktree is cut; the ledger
    only says so once the round has *finished*. A monitor that waited for the ledger
    could not report a branch until there was nothing left to report."""
    run_dir = tmp_path / "runs" / RUN
    journal = open_journal(run_dir, RUN, 1)
    journal.append(
        "branch-discovered",
        node=NodeId("api"),
        detail={"branch": "feature-2", "base_branch": "main"},
    )
    ledgers = [_ledger(repo="local/app", branch="feature-1", base_branch="main")]

    assert known_branches(ledgers, journal.events()) == [
        BranchRef("local/app", "feature-1", "main"),
        BranchRef("local/app", "feature-2", "main"),
    ]


def test_a_branch_with_no_confirmed_repository_is_not_guessed_at(tmp_path: Path) -> None:
    """A journal event names the branch but not the repository, so a branch is only
    adopted for an identity the ledger has confirmed for this run."""
    run_dir = tmp_path / "runs" / RUN
    journal = open_journal(run_dir, RUN, 1)
    journal.append(
        "branch-discovered", node=NodeId("api"), detail={"branch": "orphan", "base_branch": "main"}
    )
    assert known_branches([], journal.events()) == []


def test_a_current_branch_is_streamable_from_its_journal_identity(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / RUN
    journal = open_journal(run_dir, RUN, 1)
    journal.append(
        "branch-discovered",
        node=NodeId("api"),
        detail={"repo": "acme/app", "branch": "live", "base_branch": "main"},
    )
    assert known_branches([], journal.events()) == [BranchRef("acme/app", "live", "main")]


def test_prs_are_collected_from_the_ledger_and_the_journal(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / RUN
    journal = open_journal(run_dir, RUN, 1)
    journal.append(
        "pr-created",
        node=NodeId("api"),
        detail={"pr": "https://github.com/acme/app/pull/2", "base": "main"},
    )
    ledgers = [_ledger(repo="acme/app", pr="https://github.com/acme/app/pull/1", pr_base="main")]

    assert known_prs(ledgers, journal.events()) == [
        PrRef("acme/app", 1, "https://github.com/acme/app/pull/1", "main"),
        PrRef("acme/app", 2, "https://github.com/acme/app/pull/2", "main"),
    ]


def test_a_current_pr_is_streamable_from_its_journal_identity(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / RUN
    journal = open_journal(run_dir, RUN, 1)
    journal.append(
        "pr-created",
        node=NodeId("api"),
        detail={
            "repo": "acme/app",
            "pr": "https://github.com/acme/app/pull/7",
            "base": "main",
        },
    )
    assert known_prs([], journal.events()) == [
        PrRef("acme/app", 7, "https://github.com/acme/app/pull/7", "main")
    ]


# --- the oneharness history source ---------------------------------------------


def _oneharness(tmp_path: Path, sessions: list[dict[str, Any]]) -> str:
    """A real `oneharness`-shaped executable serving the recorded store this test wrote."""
    store = tmp_path / "history-store.json"
    store.write_text(json.dumps({"sessions": sessions}), encoding="utf-8")
    launcher = tmp_path / "oneharness"
    launcher.write_text(
        f'#!/bin/sh\nFAKE_ONEHARNESS_STORE={store} exec {sys.executable} {FAKE_ONEHARNESS} "$@"\n',
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    return str(launcher)


def _session(
    tmp_path: Path,
    session_id: str,
    name: str,
    statuses: list[str],
    *,
    history_id: str | None = None,
    **labels: str,
) -> dict[str, Any]:
    records = tmp_path / f"{session_id}.jsonl"
    records.write_text(
        "\n".join(
            json.dumps(
                {
                    "status": status,
                    "harness": "codex",
                    **({"history_id": history_id} if history_id else {}),
                }
            )
            for status in statuses
        )
        + "\n",
        encoding="utf-8",
    )
    session: dict[str, Any] = {
        "id": session_id,
        "name": name,
        "project": "/tmp/app",
        "started": "2026-07-14T10:00:00Z",
        "path": str(records),
    }
    if labels:
        session["labels"] = labels
    return session


def test_the_history_source_selects_exactly_the_sessions_this_run_labelled(
    tmp_path: Path,
) -> None:
    """The label filter is what the whole source rests on: no path heuristics, and no
    matching against a task string a persona happened to choose."""
    sessions = [
        _session(
            tmp_path,
            "api-20260714T100000Z-1",
            "engineer",
            ["running", "completed"],
            history_id="019f6f83-c0f3-7d51-a995-d05011ae2b28",
            run_id="watch-me",
            round="1",
            node="api",
        ),
        _session(tmp_path, "other-20260714T100000Z-2", "engineer", ["completed"], run_id="other"),
        _session(tmp_path, "bare-20260714T100000Z-3", "engineer", ["completed"]),
        _session(
            tmp_path,
            "judge-20260714T100000Z-4",
            "you-are-a-strict-careful-evaluator",
            ["completed"],
            run_id="watch-me",
        ),
    ]
    binary = _oneharness(tmp_path, sessions)

    events = history_events(RUN, now=AT, oneharness_bin=binary)
    assert [str(event.stream_id) for event in events] == ["oh:019f6f83-c0f3-7d51-a995-d05011ae2b28"]
    assert events[0].summary == "session api completed turns=2 engineer"
    assert events[0].source == "history"


def test_a_session_is_reported_again_when_its_own_state_moves(tmp_path: Path) -> None:
    """The key is over the values the summary is derived from, so it reports every
    change and nothing else."""
    session = _session(
        tmp_path, "api-20260714T100000Z-1", "engineer", ["running"], run_id="watch-me"
    )
    binary = _oneharness(tmp_path, [session])
    first = history_events(RUN, now=AT, oneharness_bin=binary)
    assert first[0].summary == "session ? running turns=1 engineer"

    Path(session["path"]).write_text(
        '{"status": "running"}\n{"status": "completed"}\n', encoding="utf-8"
    )
    later = history_events(RUN, now=AT, oneharness_bin=binary)
    assert later[0].key != first[0].key
    assert later[0].summary == "session ? completed turns=2 engineer"

    assert [e.key for e in history_events(RUN, now=AT, oneharness_bin=binary)] == [later[0].key]


def test_an_absent_history_store_degrades_to_silence(tmp_path: Path) -> None:
    """The journal already reports the node that dispatched the session, so losing
    this source costs detail rather than the transition itself."""
    assert history_events(RUN, now=AT, oneharness_bin=str(tmp_path / "absent")) == []


# --- the whole monitor, over a real run ----------------------------------------


def test_a_run_folds_its_journal_and_its_real_branch_into_one_stream(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    """The monitor discovers the branch from the run's own ledger, reads the real
    commits on it, and persists them — so the replay survives the branch itself."""
    repo = _repo(tmp_path, bare_origin)
    # A local origin is not GitHub, so the type cannot be inferred from it.
    Registry().register(str(repo), repo_type="single-owner")
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / RUN

    journal = open_journal(run_dir, RUN, 1)
    journal.append("node-started", node=NodeId("api"), detail={"persona": "engineer"})
    journal.append("node-settled", node=NodeId("api"), detail={"status": "done"})
    _settle(
        run_dir,
        {"api": {"status": "done", "repo": "local/app", "branch": "work", "base_branch": "main"}},
        ok=True,
        state="complete",
    )

    monitor = Monitor(
        run_id=RUN, run_dir=run_dir, oneharness_bin=str(tmp_path / "absent"), clock=lambda: AT
    )
    events = monitor.poll()
    assert [str(event.stream_id) for event in events if event.source == "journal"] == [
        "graph:watch-me/1/api",
        "graph:watch-me/1/api",
    ]
    shas = [commit.sha for commit in gitops.log_delta(repo, "main", "work")]
    git_ids = [str(event.stream_id) for event in events if event.source == "git"]
    assert git_ids == [f"git:local/app@{sha}" for sha in shas]
    assert monitor.poll() == []

    persisted = json.loads((run_dir / "monitor" / "details.json").read_text(encoding="utf-8"))
    assert sorted(persisted["commits"]) == sorted(git_ids)

    # The branch is deleted, as it is once its PR merges. A monitor started fresh
    # against the persisted snapshot still replays the exact same stream.
    _git("checkout", "main", cwd=repo)
    _git("branch", "-D", "work", cwd=repo)
    replay = Monitor(
        run_id=RUN,
        run_dir=run_dir,
        oneharness_bin=str(tmp_path / "absent"),
        clock=lambda: AT,
        snapshot=load_snapshot(run_dir),
    )
    replayed = [str(e.stream_id) for e in replay.poll() if e.source == "git"]
    assert sorted(replayed) == sorted(git_ids)


# --- the real command ----------------------------------------------------------


def _monitor_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["just", "monitor", *args], cwd=REPO_ROOT, text=True, capture_output=True, timeout=180
    )


def test_the_monitor_command_streams_a_run_and_only_success_exits_zero(tmp_path: Path) -> None:
    """The header is a contract, not a banner: it names the one command that turns any
    id in the stream into the full record."""
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / RUN
    journal = open_journal(run_dir, RUN, 1)
    journal.append("node-started", node=NodeId("api"), detail={"persona": "engineer"})
    journal.append("node-settled", node=NodeId("api"), detail={"status": "done"})
    _settle(run_dir, {"api": {"status": "done"}}, ok=True, state="complete")

    text = _monitor_cli("--runs-dir", str(runs_dir))
    assert text.returncode == 0, text.stderr
    lines = text.stdout.splitlines()
    assert lines[0] == HEADER
    assert "just history-show" in lines[0]
    assert lines[1].endswith("graph:watch-me/1/api  node-started persona=engineer")
    assert lines[-1].endswith("watch-me round-01 complete: graph complete")

    streamed = _monitor_cli("--runs-dir", str(runs_dir), "--format", "jsonl", RUN)
    assert streamed.returncode == 0, streamed.stderr
    records = [json.loads(line) for line in streamed.stdout.splitlines()]
    assert records[0]["type"] == "event"
    assert records[0]["id"] == "graph:watch-me/1/api"
    assert records[-1]["type"] == "heartbeat"
    assert (records[-1]["state"], records[-1]["detail"]) == ("complete", "graph complete")
    assert "id" not in records[-1]


def test_the_monitor_command_reports_a_run_it_cannot_watch_actionably(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    _settle(runs_dir / RUN, {"api": {"status": "done"}}, ok=True, state="complete")

    unknown = _monitor_cli("--runs-dir", str(runs_dir), "never-ran")
    assert unknown.returncode == 2
    assert "no recorded run 'never-ran'" in unknown.stderr
    assert "Traceback" not in unknown.stderr

    empty = _monitor_cli("--runs-dir", str(tmp_path / "elsewhere"))
    assert empty.returncode == 2
    assert "pass --runs-dir" in empty.stderr

    bad = _monitor_cli("--runs-dir", str(runs_dir), "--heartbeat", "0")
    assert bad.returncode == 2
    assert "--heartbeat must be a positive number of seconds" in bad.stderr
    bad_bound = _monitor_cli(
        "--runs-dir", str(runs_dir), "--poll-interval", "2", "--max-poll-interval", "1"
    )
    assert bad_bound.returncode == 2
    assert "--max-poll-interval must be at least --poll-interval" in bad_bound.stderr


def test_monitor_command_backs_off_to_its_bounded_interval(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / RUN
    open_journal(run_dir, RUN, 1).append("node-started", node=NodeId("api"))
    _settle(run_dir, {"api": {"status": "waiting"}}, ok=False, state="waiting")
    process = subprocess.Popen(
        [
            "just",
            "monitor",
            "--runs-dir",
            str(runs_dir),
            "--format",
            "jsonl",
            "--heartbeat",
            "0.001",
            "--poll-interval",
            "0.01",
            "--max-poll-interval",
            "0.04",
            RUN,
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert process.stdout is not None
        records = [json.loads(process.stdout.readline()) for _ in range(5)]
    finally:
        process.terminate()
        process.wait(timeout=5)
    intervals = [record["next_poll_seconds"] for record in records if record["type"] == "heartbeat"]
    assert 0.02 in intervals
    assert intervals[-1] == 0.04
