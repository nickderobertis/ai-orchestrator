from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from orchestrator.detail_snapshot import SNAPSHOT_VERSION, CommitDetail, PrDetail
from orchestrator.history import (
    HistoryError,
    HistorySession,
    SessionId,
    _persisted_detail,
    _records,
    _render_persisted_detail,
    _session_labels,
    all_sessions,
    digest,
    recent_runs,
    session_duration_ms,
    session_role,
    show_run,
)
from orchestrator.ids import GitId, PrId
from orchestrator.journal import open_journal
from orchestrator.runs import NodeId, RunId, prepare_round, write_result

FIXTURE = Path(__file__).parent / "fixtures" / "history" / "worker.jsonl"

RUN = RunId("watch-me")
FULL_SHA = "0123abcdef4567890123abcdef4567890123abcd"
PR_URL = "https://github.com/acme/app/pull/7"
PLAN: dict[str, Any] = {
    "concurrency": 1,
    "tasks": [{"id": "api", "persona": "engineer", "task": "ship it"}],
}


def _fake_oneharness(tmp_path: Path) -> Path:
    script = tmp_path / "oneharness"
    script.write_text(
        """#!/usr/bin/env python3
import json, sys
fixture = sys.argv[1]
args = sys.argv[2:]
worker = {
    "id": "build-history-command-20260714T100000Z-123",
    "name": "build-history-command", "project": "/tmp/example-repo",
    "started": "2026-07-14T10:00:00Z", "path": fixture,
}
judge = {
    "id": "you-are-a-strict-careful-evaluator-1",
    "name": "you-are-a-strict-careful-evaluator", "project": "/tmp/example-repo",
    "started": "2026-07-14T10:02:00Z", "path": fixture,
}
if args[0] == "list": print(json.dumps([judge, worker]))
elif args[0] == "show":
    records = []
    for line in open(fixture):
        try: records.append(json.loads(line))
        except json.JSONDecodeError: pass
    print(json.dumps(records))
""".replace("fixture = sys.argv[1]", f"fixture = {str(FIXTURE)!r}"),
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def test_digest_parses_fixture_defensively() -> None:
    result = digest(_records(FIXTURE), SessionId("worker-id"))
    assert result.turns == 2
    assert (result.input_tokens, result.output_tokens) == (300, 50)
    assert result.commands == ["rg history", "just test"]
    assert result.duration_ms == 3700
    assert result.text == "Tests pass."


def test_history_commands_cross_project_and_default_to_worker(tmp_path: Path) -> None:
    binary = _fake_oneharness(tmp_path)
    listing = recent_runs(15, oneharness_bin=str(binary))
    assert "example-repo" in listing
    assert "build-history-command" in listing
    assert "gpt-5" in listing
    assert "evaluator" not in listing
    assert {session.name for session in all_sessions(oneharness_bin=str(binary))} == {
        "build-history-command",
        "you-are-a-strict-careful-evaluator",
    }

    output = show_run("history-command", oneharness_bin=str(binary))
    assert "Turns: 2" in output
    assert "300 input / 50 output" in output
    assert "$ just test" in output
    assert "Latest agent text:\nTests pass." in output
    assert (
        "oneharness history show build-history-command-20260714T100000Z-123 --format text" in output
    )


def test_bad_inputs_are_actionable(tmp_path: Path) -> None:
    binary = _fake_oneharness(tmp_path)
    with pytest.raises(HistoryError, match="positive integer"):
        recent_runs(0, oneharness_bin=str(binary))
    with pytest.raises(HistoryError, match="no worker history session"):
        show_run("missing", oneharness_bin=str(binary))


def test_session_identity_role_and_duration_accept_legacy_records_defensively(
    tmp_path: Path,
) -> None:
    assert HistorySession.from_value("bad") is None
    assert HistorySession.from_value({"id": "missing"}) is None
    assert _session_labels("bad") == {}
    assert _session_labels({"role": "judge", "empty": "", 1: "bad", "bad": 1}) == {"role": "judge"}
    labelled = HistorySession(
        SessionId("labelled"),
        "ordinary",
        tmp_path,
        "now",
        tmp_path / "history",
        {"role": "judge"},
    )
    legacy_judge = HistorySession(
        SessionId("legacy"),
        "you-are-roleplaying-the-user-in-a-test",
        tmp_path,
        "now",
        tmp_path / "history",
        {},
    )
    legacy_agent = HistorySession(
        SessionId("agent"),
        "implement",
        tmp_path,
        "now",
        tmp_path / "history",
        {},
    )
    assert session_role(labelled) == session_role(legacy_judge) == "judge"
    assert session_role(legacy_agent) == "agent"
    assert (
        session_duration_ms(
            [{"duration_ms": 5}, {"duration_ms": -1}, {"duration_ms": True}, {"duration_ms": 2.5}]
        )
        == 5
    )


# --- resolving a typed detail id -----------------------------------------------
#
# A `git:`/`pr:`/`graph:` id names a thing in the graph rather than a conversation,
# so the ledger and the journal — both of which outlive the session that produced
# them — are its source, not a oneharness session digest. These build both through
# their real writers.


def _settle(run_dir: Path, results: dict[str, Any], *, ok: bool, state: str) -> None:
    _, round_dir = prepare_round(run_dir, PLAN)
    write_result(
        round_dir,
        {"ok": ok, "state": state, "started_order": sorted(results), "results": results},
    )


def _tracked_run(tmp_path: Path) -> Path:
    """A recorded run whose one node cut a branch, committed, and opened a PR."""
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / RUN
    journal = open_journal(run_dir, RUN, 1)
    journal.append(
        "branch-discovered",
        node=NodeId("api"),
        detail={"branch": "feature-1", "base_branch": "main"},
    )
    journal.append("pr-created", node=NodeId("api"), detail={"pr": PR_URL})
    journal.append("node-settled", node=NodeId("api"), detail={"status": "done", "sha": FULL_SHA})
    _settle(
        run_dir,
        {
            "api": {
                "status": "done",
                "outcome": "merged",
                "repo": "acme/app",
                "branch": "feature-1",
                "base_branch": "main",
                "pr": PR_URL,
                "detail": "merged via auto-merge",
            }
        },
        ok=True,
        state="complete",
    )
    return runs_dir


def test_a_graph_id_resolves_to_the_recorded_node_it_names(tmp_path: Path) -> None:
    output = show_run("graph:watch-me/1/api", runs_dir=_tracked_run(tmp_path))
    assert "Reference: graph:watch-me/1/api" in output
    assert "Graph node: graph:watch-me/1/api" in output
    assert "Status: done (merged)" in output
    assert "Repo: acme/app" in output
    assert "Branch: feature-1" in output
    assert f"PR: {PR_URL}" in output
    assert "Detail: merged via auto-merge" in output
    # The journal says how it got there, which is the half the ledger cannot answer.
    assert "branch-discovered" in output
    assert "node-settled" in output


@pytest.mark.parametrize(
    "query",
    [
        f"git:acme/app@{FULL_SHA}",
        f"git:acme/app@{FULL_SHA[:7]}",  # as a human would copy it out of a log
        "pr:acme/app#7",
    ],
)
def test_a_git_or_pr_id_resolves_through_the_node_that_recorded_it(
    tmp_path: Path, query: str
) -> None:
    output = show_run(query, runs_dir=_tracked_run(tmp_path))
    assert f"Reference: {query}" in output
    assert "Graph node: graph:watch-me/1/api" in output


def test_a_commit_carried_across_rounds_resolves_to_where_it_ended_up(tmp_path: Path) -> None:
    """A branch is legitimately carried across rounds, so one commit can appear in
    several — and the latest round is the one that says where it ended up."""
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / RUN
    first = open_journal(run_dir, RUN, 1)
    first.append("node-settled", node=NodeId("api"), detail={"status": "waiting", "sha": FULL_SHA})
    _settle(
        run_dir,
        {"api": {"status": "waiting", "repo": "acme/app", "branch": "feature-1"}},
        ok=False,
        state="waiting",
    )
    second = open_journal(run_dir, RUN, 2)
    second.append("node-settled", node=NodeId("api"), detail={"status": "done", "sha": FULL_SHA})
    _settle(
        run_dir,
        {"api": {"status": "done", "repo": "acme/app", "branch": "feature-1"}},
        ok=True,
        state="complete",
    )

    output = show_run(f"git:acme/app@{FULL_SHA[:7]}", runs_dir=runs_dir)
    assert "Graph node: graph:watch-me/2/api" in output
    assert "Also recorded in: graph:watch-me/1/api" in output


@pytest.mark.parametrize(
    "query",
    [
        "graph:watch-me/1/nope",  # no such node
        "graph:watch-me/9/api",  # no such round
        "graph:never-ran/1/api",  # no such run
        "pr:acme/app#404",
        "pr:other/app#7",  # the right PR number in the wrong repository
        "git:acme/app@9999999",
    ],
)
def test_a_well_formed_id_naming_nothing_recorded_is_actionable(tmp_path: Path, query: str) -> None:
    with pytest.raises(HistoryError, match="no recorded tracked-graph node matches"):
        show_run(query, runs_dir=_tracked_run(tmp_path))


@pytest.mark.parametrize(
    "query", ["pr:acme/app#abc", "git:acme/app@nothex", "graph:watch-me/0/api"]
)
def test_a_typo_in_a_typed_id_is_reported_rather_than_silently_searched(
    tmp_path: Path, query: str
) -> None:
    """Degrading it to a substring search would answer a question the user did not
    ask — `pr:acme/app#abc` would quietly match on the literal text."""
    with pytest.raises(HistoryError, match="not a valid typed detail id"):
        show_run(query, runs_dir=_tracked_run(tmp_path))


def test_the_oh_namespace_is_the_explicit_spelling_of_the_legacy_query(tmp_path: Path) -> None:
    """Anything else would make the typed form subtly weaker than the string it
    replaces, and the muscle memory for that string is why these are a widening."""
    binary = _fake_oneharness(tmp_path)
    typed = show_run("oh:build-history-command-20260714T100000Z-123", oneharness_bin=str(binary))
    assert typed == show_run("build-history-command", oneharness_bin=str(binary))
    assert "Session: build-history-command-20260714T100000Z-123" in typed


def test_an_oh_uuid_uses_oneharness_exact_history_lookup(tmp_path: Path) -> None:
    binary = _fake_oneharness(tmp_path)
    history_id = "019f6f83-c0f3-7d51-a995-d05011ae2b28"
    output = show_run(f"oh:{history_id}", oneharness_bin=str(binary))
    assert "Session: worker-session" in output
    assert f"oneharness history show {history_id} --format text" in output


def test_an_empty_query_names_nothing_at_all(tmp_path: Path) -> None:
    with pytest.raises(HistoryError, match="must not be empty"):
        show_run("   ", runs_dir=_tracked_run(tmp_path))


def test_git_and_pr_details_fall_back_to_persisted_monitor_snapshots(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    details = runs_dir / "observed" / "monitor" / "details.json"
    details.parent.mkdir(parents=True)
    details.write_text(
        json.dumps(
            {
                "version": SNAPSHOT_VERSION,
                "commits": {
                    f"git:acme/app@{FULL_SHA[:7]}": {
                        "identity": "acme/app",
                        "sha": FULL_SHA[:7],
                        "branch": "feature",
                        "base": "main",
                        "subject": "feat: observed",
                        "detail": "commit detail\n\npatch body",
                    }
                },
                "prs": {
                    "pr:acme/app#7": {
                        "number": 7,
                        "url": PR_URL,
                        "identity": "acme/app",
                        "state": "MERGED",
                        "merged": True,
                        "draft": False,
                        "merge_state_status": "CLEAN",
                        "checks": [{"name": "ci", "state": "SUCCESS", "required": True}],
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    commit = show_run(f"git:acme/app@{FULL_SHA[:7]}", runs_dir=runs_dir)
    assert "Subject: feat: observed" in commit
    assert "patch body" in commit
    pr = show_run("pr:acme/app#7", runs_dir=runs_dir)
    assert "State: MERGED" in pr
    assert '"name": "ci"' in pr


def test_persisted_detail_reader_skips_absent_and_malformed_snapshots(tmp_path: Path) -> None:
    git_ref = GitId("acme/app", FULL_SHA[:7])
    assert _persisted_detail(git_ref, tmp_path / "absent") is None

    malformed = tmp_path / "runs" / "newest" / "monitor" / "details.json"
    malformed.parent.mkdir(parents=True)
    malformed.write_text(json.dumps({"commits": []}), encoding="utf-8")
    assert _persisted_detail(git_ref, tmp_path / "runs") is None
    assert "Commit and diff:\n(unavailable)" in _render_persisted_detail(
        git_ref, CommitDetail(sha=FULL_SHA[:7])
    )
    assert "Checks: []" in _render_persisted_detail(PrId("acme/app", 7), PrDetail(number=7))


def test_recorded_node_includes_its_persisted_remote_detail(tmp_path: Path) -> None:
    runs_dir = _tracked_run(tmp_path)
    details = runs_dir / RUN / "monitor" / "details.json"
    details.parent.mkdir(parents=True)
    details.write_text(
        json.dumps(
            {
                "version": SNAPSHOT_VERSION,
                "commits": {
                    f"git:acme/app@{FULL_SHA}": {
                        "identity": "acme/app",
                        "sha": FULL_SHA,
                        "subject": "feat: full detail",
                        "detail": "stored patch",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    output = show_run(f"git:acme/app@{FULL_SHA[:7]}", runs_dir=runs_dir)
    assert "Graph node: graph:watch-me/1/api" in output
    assert "Persisted remote detail:" in output
    assert "stored patch" in output
