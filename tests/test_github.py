"""Unit tests for the gh-backed GitHub backend, driven with a fake `run` seam."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import orchestrator.github as gh
from orchestrator.github import (
    AutoMergeUnavailable,
    Check,
    CliGitHubBackend,
    GitHubError,
    PRStatus,
    PullRequest,
    _normalize_check,
)


class RecordingRun:
    """A fake `run` seam: returns queued outputs (or raises) and records argv."""

    def __init__(self, outputs: list) -> None:
        self.outputs = outputs
        self.calls: list[list[str]] = []
        self._i = 0

    def __call__(self, argv: list[str]) -> str:
        self.calls.append(argv)
        out = self.outputs[self._i]
        self._i += 1
        if isinstance(out, Exception):
            raise out
        return out


def _pr() -> PullRequest:
    return PullRequest(number=3, url="u", repo="o/r", head="feat", base="main")


def _queue_payload(entry: dict[str, str] | None = None) -> str:
    return json.dumps({"data": {"repository": {"pullRequest": {"mergeQueueEntry": entry}}}})


def test_default_branch() -> None:
    run = RecordingRun(["main\n"])
    assert CliGitHubBackend(run=run).default_branch("o/r") == "main"
    assert run.calls == [["api", "repos/o/r", "--jq", ".default_branch"]]


def test_default_branch_rejects_empty_github_response() -> None:
    with pytest.raises(GitHubError, match="no default branch"):
        CliGitHubBackend(run=RecordingRun(["\n"])).default_branch("o/r")


def test_required_status_checks_reads_branch_protection_contexts() -> None:
    run = RecordingRun(
        [
            json.dumps(
                {
                    "protected": True,
                    "protection": {"required_status_checks": {"contexts": ["gate", "lint"]}},
                }
            )
        ]
    )
    assert CliGitHubBackend(run=run).required_status_checks("o/r", "release/next") == (
        "gate",
        "lint",
    )
    assert run.calls == [["api", "repos/o/r/branches/release%2Fnext"]]


def test_required_status_checks_reports_unprotected_branch_as_known_empty() -> None:
    run = RecordingRun([json.dumps({"protected": False})])
    assert CliGitHubBackend(run=run).required_status_checks("o/r", "master") == ()


def test_required_status_checks_reports_protection_without_checks_as_known_empty() -> None:
    payload = {"protected": True, "protection": {"required_status_checks": None}}
    assert (
        CliGitHubBackend(run=RecordingRun([json.dumps(payload)])).required_status_checks(
            "o/r", "master"
        )
        == ()
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"protected": "yes"},
        {"protected": True, "protection": []},
        {"protected": True, "protection": {"required_status_checks": []}},
        {"protected": True, "protection": {"required_status_checks": {}}},
        {
            "protected": True,
            "protection": {"required_status_checks": {"contexts": "gate"}},
        },
        {
            "protected": True,
            "protection": {"required_status_checks": {"contexts": [1]}},
        },
    ],
)
def test_required_status_checks_rejects_malformed_response(payload: object) -> None:
    with pytest.raises(GitHubError, match="could not parse required status checks"):
        CliGitHubBackend(run=RecordingRun([json.dumps(payload)])).required_status_checks(
            "o/r", "main"
        )


def test_create_pr_reuses_open_pr_for_head() -> None:
    existing = '[{"number": 41, "url": "https://github.com/o/r/pull/41"}]'
    run = RecordingRun([existing])
    pr = CliGitHubBackend(run=run).create_pr("o/r", head="f", base="main", title="t", body="b")
    assert pr == PullRequest(41, "https://github.com/o/r/pull/41", "o/r", "f", "main")
    assert run.calls == [
        [
            "pr",
            "list",
            "--repo",
            "o/r",
            "--head",
            "f",
            "--base",
            "main",
            "--state",
            "open",
            "--json",
            "number,url",
        ]
    ]


def test_create_pr_creates_when_head_has_no_open_pr() -> None:
    run = RecordingRun(["[]", "Warning: ...\nhttps://github.com/o/r/pull/42\n"])
    pr = CliGitHubBackend(run=run).create_pr("o/r", head="f", base="main", title="t", body="b")
    assert pr.number == 42
    assert pr.repo == "o/r"
    assert run.calls == [
        [
            "pr",
            "list",
            "--repo",
            "o/r",
            "--head",
            "f",
            "--base",
            "main",
            "--state",
            "open",
            "--json",
            "number,url",
        ],
        [
            "pr",
            "create",
            "--repo",
            "o/r",
            "--head",
            "f",
            "--base",
            "main",
            "--title",
            "t",
            "--body",
            "b",
        ],
    ]


def test_create_pr_can_create_draft() -> None:
    run = RecordingRun(["[]", "https://github.com/o/r/pull/43\n"])
    pr = CliGitHubBackend(run=run).create_pr(
        "o/r", head="f", base="main", title="t", body="b", draft=True
    )
    assert pr.number == 43
    assert "--draft" in run.calls[1]


def test_create_pr_bad_output_raises() -> None:
    run = RecordingRun(["[]", "not a url"])
    with pytest.raises(GitHubError, match="could not parse PR number"):
        CliGitHubBackend(run=run).create_pr("o/r", head="f", base="main", title="t", body="b")


def test_create_pr_bad_list_output_raises() -> None:
    run = RecordingRun(['{"number": 42}'])
    with pytest.raises(GitHubError, match="could not parse PR from gh output"):
        CliGitHubBackend(run=run).create_pr("o/r", head="f", base="main", title="t", body="b")


@pytest.mark.parametrize(
    "existing",
    [
        ["not an object"],
        [{"number": True, "url": "https://github.com/o/r/pull/42"}],
        [{"number": 42, "url": ""}],
    ],
)
def test_create_pr_rejects_malformed_existing_pr_fields(existing: list[object]) -> None:
    run = RecordingRun([json.dumps(existing)])
    with pytest.raises(GitHubError, match="could not parse PR from gh output"):
        CliGitHubBackend(run=run).create_pr("o/r", head="f", base="main", title="t", body="b")


def test_enable_auto_merge_ok() -> None:
    run = RecordingRun([""])
    CliGitHubBackend(run=run).enable_auto_merge(_pr(), method="squash")
    assert "--auto" in run.calls[0]


def test_enable_auto_merge_unavailable_maps_to_typed_error() -> None:
    run = RecordingRun([GitHubError("gh pr merge failed: Auto-merge is not enabled for repo")])
    with pytest.raises(AutoMergeUnavailable):
        CliGitHubBackend(run=run).enable_auto_merge(_pr(), method="squash")


def test_enable_auto_merge_other_error_reraised() -> None:
    run = RecordingRun([GitHubError("gh pr merge failed: network is down")])
    with pytest.raises(GitHubError, match="network is down"):
        CliGitHubBackend(run=run).enable_auto_merge(_pr(), method="squash")


def test_merge_calls_gh() -> None:
    run = RecordingRun([""])
    CliGitHubBackend(run=run).merge(_pr(), method="merge")
    assert run.calls[0][0] == "pr" and "--merge" in run.calls[0]


def test_mark_ready_calls_gh() -> None:
    run = RecordingRun([""])
    CliGitHubBackend(run=run).mark_ready(_pr())
    assert run.calls[0] == ["pr", "ready", "3", "--repo", "o/r"]


def test_status_parses_rollup() -> None:
    payload = {
        "number": 3,
        "state": "OPEN",
        "isDraft": True,
        "mergeStateStatus": "BLOCKED",
        "statusCheckRollup": [
            {
                "__typename": "CheckRun",
                "name": "ci",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
                "isRequired": True,
            },
            {
                "__typename": "CheckRun",
                "name": "lint",
                "status": "IN_PROGRESS",
                "isRequired": False,
            },
            {
                "__typename": "StatusContext",
                "context": "legacy",
                "state": "FAILURE",
                "isRequired": True,
            },
        ],
    }
    run = RecordingRun([json.dumps(payload), _queue_payload()])
    status = CliGitHubBackend(run=run).status(_pr())
    assert status.number == 3 and not status.merged
    assert status.draft
    assert not status.merge_in_progress
    assert len(status.blocking) == 2  # ci + legacy
    assert status.blocking_failed  # legacy failed
    assert not status.blocking_green
    assert run.calls[1][:2] == ["api", "graphql"]


def test_status_reports_merge_queue_entry_as_in_progress() -> None:
    payload = {
        "number": 3,
        "state": "OPEN",
        "mergeStateStatus": "CLEAN",
        "statusCheckRollup": [],
    }
    status = CliGitHubBackend(
        run=RecordingRun([json.dumps(payload), _queue_payload({"id": "MQE_1"})])
    ).status(_pr())
    assert status.merge_in_progress


@pytest.mark.parametrize(
    ("entry", "message"),
    [(True, "non-object mergeQueueEntry"), ({}, "invalid mergeQueueEntry id")],
)
def test_status_rejects_malformed_merge_queue_entry(entry: object, message: str) -> None:
    payload = {"number": 3, "state": "OPEN", "statusCheckRollup": []}
    malformed = json.dumps({"data": {"repository": {"pullRequest": {"mergeQueueEntry": entry}}}})
    with pytest.raises(GitHubError, match=message):
        CliGitHubBackend(run=RecordingRun([json.dumps(payload), malformed])).status(_pr())


@pytest.mark.parametrize(
    ("repo", "queue_payload", "message"),
    [
        ("missing-owner", "", "invalid GitHub repository slug"),
        ("o/r", "[]", "invalid JSON payload"),
        ("o/r", json.dumps({"data": {"repository": {}}}), "invalid mergeQueueEntry payload"),
    ],
)
def test_status_rejects_invalid_merge_queue_boundaries(
    repo: str, queue_payload: str, message: str
) -> None:
    payload = {"number": 3, "state": "OPEN", "statusCheckRollup": []}
    pr = PullRequest(number=3, url="u", repo=repo, head="feat", base="main")
    outputs = [json.dumps(payload), queue_payload] if queue_payload else [json.dumps(payload)]
    with pytest.raises(GitHubError, match=message):
        CliGitHubBackend(run=RecordingRun(outputs)).status(pr)


def test_status_merged() -> None:
    payload = {"number": 3, "state": "MERGED", "mergeStateStatus": "CLEAN", "statusCheckRollup": []}
    status = CliGitHubBackend(run=RecordingRun([json.dumps(payload)])).status(_pr())
    assert status.merged and status.blocking_green  # no required checks → vacuously green
    assert not status.merge_in_progress


def test_status_rejects_non_boolean_draft_field() -> None:
    payload = {
        "number": 3,
        "state": "OPEN",
        "isDraft": "false",
        "mergeStateStatus": "CLEAN",
        "statusCheckRollup": [],
    }
    with pytest.raises(GitHubError, match="non-boolean isDraft"):
        CliGitHubBackend(run=RecordingRun([json.dumps(payload)])).status(_pr())


def test_status_rejects_non_object_response_root() -> None:
    with pytest.raises(GitHubError, match="invalid JSON payload"):
        CliGitHubBackend(run=RecordingRun(["[]"])).status(_pr())


def test_normalize_check_variants() -> None:
    pending = _normalize_check({"__typename": "CheckRun", "name": "c", "status": "QUEUED"})
    assert pending.state == "PENDING" and not pending.required
    done = _normalize_check(
        {
            "__typename": "CheckRun",
            "name": "c",
            "status": "COMPLETED",
            "conclusion": "FAILURE",
            "isRequired": True,
        }
    )
    assert done.state == "FAILURE" and done.required and done.red
    ctx = _normalize_check({"__typename": "StatusContext", "context": "x", "state": "success"})
    assert ctx.name == "x" and ctx.state == "SUCCESS" and ctx.green


def test_check_green_red_helpers() -> None:
    assert Check("a", "SUCCESS", True).green
    assert Check("a", "ERROR", True).red
    assert not Check("a", "PENDING", True).green
    assert Check("a", "NEUTRAL", True).settled
    assert not Check("a", "PENDING", True).settled


def test_prstatus_no_required_is_vacuously_green() -> None:
    status = PRStatus(1, "OPEN", False, "CLEAN", (Check("opt", "PENDING", False),))
    assert status.blocking == ()
    assert status.blocking_green and not status.blocking_failed


def test_cli_backend_ci_support_requires_github_coordinates() -> None:
    backend = CliGitHubBackend()
    assert backend.supports_ci("owner/repo")
    assert not backend.supports_ci("local/checkout")


def test_default_run_success_and_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        gh.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="ok", stderr=""),
    )
    assert gh._default_run(["--version"]) == "ok"
    monkeypatch.setattr(
        gh.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="boom"),
    )
    with pytest.raises(GitHubError, match="boom"):
        gh._default_run(["bogus"])
