"""Oneharness history/watch acceptance for graph-labelled monitor inputs."""

# llmlint: ignore-file[e2e_not_mocked] the smoke test crosses the real oneharness
# and Codex boundary. Deterministic history behavior uses oneharness's shipped mock
# harness at that paid-provider seam. The lifecycle slice uses the repository's
# sanctioned make_writing_dispatch paid-harness seam and FakeGitHub PR/CI decision
# seam while driving real git, lifecycle commits/merge, journal, snapshots, and
# public CLIs; the real dispatch boundary is exercised here and in test_dispatch_e2e.py.

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fakes import FakeGitHub, make_writing_dispatch
from mock_oneharness import run_mock_oneharness
from waits import deadline as e2e_deadline
from waits import timeout as e2e_timeout

from orchestrator import REPO_ROOT, gitops
from orchestrator.github import Check, GitHubError, PRStatus, PullRequest
from orchestrator.journal import NodeJournal, open_journal
from orchestrator.lifecycle import result_payload, run_repo_task
from orchestrator.merge import GitHubMergeStrategy
from orchestrator.monitor import Monitor, load_snapshot
from orchestrator.runs import NodeId, RunId, prepare_round, write_result
from orchestrator.workspace import Workspace

RUN_ID = "real-monitor-e2e"
LIFECYCLE_RUN_ID = "real-lifecycle-monitor-e2e"
GRAPH_LABELS = (
    "run_id=real-monitor-e2e",
    "round=1",
    "node=history-turns",
    "step=record",
)
MOCK_CODEX_STDOUT = "\n".join(
    (
        json.dumps({"type": "turn.started"}),
        json.dumps({"type": "thread.started", "thread_id": "mock-codex-thread"}),
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "done"}}),
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 4, "cached_input_tokens": 0, "output_tokens": 1},
            }
        ),
    )
)


def _just(
    *args: str, environment: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["just", *args],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
    )


def _graph_label_args() -> list[str]:
    return [item for label in GRAPH_LABELS for item in ("--history-label", label)]


def _drain_jsonl(process: subprocess.Popen[str], minimum: int) -> list[dict[str, Any]]:
    assert process.stdout is not None
    lines: queue.Queue[str | None] = queue.Queue()

    def pump() -> None:
        for line in process.stdout:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()
    records: list[dict[str, Any]] = []
    deadline = e2e_deadline(15)
    while len(records) < minimum:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            line = lines.get(timeout=remaining)
        except queue.Empty:
            break
        if line is None:
            break
        records.append(json.loads(line))
    return records


def _terminate(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=e2e_timeout(5))
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=e2e_timeout(5))


def _run_record(
    oneharness_bin: str,
    *,
    config: Path,
    name: str,
    environment: dict[str, str],
    mock_harness: bool = True,
    prompt: str | None = None,
) -> subprocess.CompletedProcess[str]:
    args = [
        "--config",
        str(config),
        "--prompt",
        prompt or f"record {name}",
        "--history",
        "--history-name",
        name,
        "--compact",
        *_graph_label_args(),
    ]
    if mock_harness:
        return run_mock_oneharness(oneharness_bin, *args, cwd=REPO_ROOT, env=environment)
    return subprocess.run(
        [oneharness_bin, "run", *args],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
    )


def _watch(
    oneharness_bin: str,
    history_dir: Path,
    *,
    after: str | None = None,
) -> subprocess.Popen[str]:
    command = [
        oneharness_bin,
        "history",
        "watch",
        "--all-projects",
        "--history-dir",
        str(history_dir),
        "--label",
        f"run_id={RUN_ID}",
        "--format",
        "jsonl",
    ]
    if after is not None:
        command += ["--after", after]
    return subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_real_oneharness_codex_smoke(oneharness_bin: str, tmp_path: Path) -> None:
    """Intentionally cross the real oneharness-to-Codex provider boundary once."""
    environment = os.environ.copy()
    for inherited in (
        "MOCK_STDOUT",
        "ONEHARNESS_BIN_CODEX",
        "ONEHARNESS_HARNESSES",
        "ONEHARNESS_HISTORY_LABELS",
    ):
        environment.pop(inherited, None)
    environment.update(
        {
            "ONEHARNESS_HARNESSES": "codex",
            "ONEHARNESS_HISTORY_DIR": str(tmp_path / "history"),
        }
    )

    completed = _run_record(
        oneharness_bin,
        config=REPO_ROOT / "oneharness.toml",
        name="real-oneharness-codex-smoke",
        environment=environment,
        mock_harness=False,
        prompt="Reply with exactly oneharness-codex-smoke and do nothing else.",
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)["results"][0]
    assert result["harness"] == "codex"
    assert result["status"] == "ok"
    assert "oneharness-codex-smoke" in result["text"]


def test_history_labels_and_cursor_watch(oneharness_bin: str, tmp_path: Path) -> None:
    history_dir = tmp_path / "history"

    environment = os.environ.copy()
    for inherited in (
        "ONEHARNESS_BIN_CODEX",
        "ONEHARNESS_BIN_CLAUDE_CODE",
        "ONEHARNESS_MODELS",
        "ONEHARNESS_HISTORY_LABELS",
    ):
        environment.pop(inherited, None)
    environment.update(
        {
            "ONEHARNESS_HARNESSES": "codex",
            "ONEHARNESS_HISTORY_DIR": str(history_dir),
            "MOCK_STDOUT": MOCK_CODEX_STDOUT,
        }
    )

    agent = _run_record(
        oneharness_bin,
        config=REPO_ROOT / "oneharness.toml",
        name="agent-invocation",
        environment=environment,
    )
    assert agent.returncode == 0, agent.stderr
    judge = _run_record(
        oneharness_bin,
        config=REPO_ROOT / "oneharness.judge.toml",
        name="judge-invocation",
        environment=environment,
    )
    assert judge.returncode == 0, judge.stderr
    llmlint = _run_record(
        oneharness_bin,
        config=REPO_ROOT / "oneharness.toml",
        name="llmlint-invocation",
        environment={**environment, "ONEHARNESS_HISTORY_LABELS": "role=llmlint"},
    )
    assert llmlint.returncode == 0, llmlint.stderr
    legacy_config = tmp_path / "oneharness-legacy.toml"
    legacy_config.write_text(
        (REPO_ROOT / "oneharness.toml")
        .read_text(encoding="utf-8")
        .replace('history_labels = { role = "agent" }', ""),
        encoding="utf-8",
    )
    correction = _run_record(
        oneharness_bin,
        config=legacy_config,
        name="ordinary-history-session",
        environment=environment,
        prompt=(
            "Your previous verdict reported rule violations in files that those rules do not cover"
        ),
    )
    assert correction.returncode == 0, correction.stderr

    listed = subprocess.run(
        [
            oneharness_bin,
            "history",
            "list",
            "--all-projects",
            "--history-dir",
            str(history_dir),
            "--format",
            "json",
            "--compact",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
    )
    assert listed.returncode == 0, listed.stderr
    records = json.loads(listed.stdout)
    assert isinstance(records, list) and len(records) == 4
    by_role = {record["labels"]["role"]: record for record in records if "role" in record["labels"]}
    assert set(by_role) == {"agent", "judge", "llmlint"}
    for record in records:
        expected_labels = {
            "node": "history-turns",
            "round": "1",
            "run_id": RUN_ID,
            "step": "record",
        }
        if "role" in record["labels"]:
            expected_labels["role"] = record["labels"]["role"]
        assert record["labels"] == expected_labels

    recent = _just("history", environment=environment)
    assert recent.returncode == 0, recent.stderr
    assert "agent-invocation" in recent.stdout
    assert "judge-invocation" not in recent.stdout
    assert "llmlint-invocation" not in recent.stdout
    assert "ordinary-history-session" not in recent.stdout

    watcher = _watch(oneharness_bin, history_dir)
    try:
        envelopes = _drain_jsonl(watcher, 4)
    finally:
        _terminate(watcher)
    assert len(envelopes) == 4, envelopes
    assert {envelope["type"] for envelope in envelopes} == {"record"}
    watched_ids = [envelope["record"]["history_id"] for envelope in envelopes]
    assert len(watched_ids) == len(set(watched_ids)) == 4

    resumed_watcher = _watch(oneharness_bin, history_dir, after=watched_ids[0])
    try:
        resumed = _drain_jsonl(resumed_watcher, 3)
    finally:
        _terminate(resumed_watcher)
    resumed_ids = [envelope["record"]["history_id"] for envelope in resumed]
    assert watched_ids[0] not in resumed_ids
    assert resumed_ids == watched_ids[1:]

    detail = _just(
        "history-show",
        f"oh:{watched_ids[0]}",
        "--runs-dir",
        str(tmp_path / "runs"),
        environment=environment,
    )
    assert detail.returncode == 0, detail.stderr
    assert f"oneharness history show {watched_ids[0]} --format text" in detail.stdout
    assert "Latest agent text:" in detail.stdout

    runs_dir = tmp_path / "runs"
    plan_path = tmp_path / "provider-telemetry-plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "history-turns",
                        "kind": "human",
                        "task": "Approve the history-backed telemetry run.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    planned = _just(
        "run-plan",
        str(plan_path),
        "--run",
        RUN_ID,
        "--runs-dir",
        str(runs_dir),
        "--format",
        "json",
        environment=environment,
    )
    assert planned.returncode == 1, planned.stderr
    assert json.loads(planned.stdout)["state"] == "waiting"
    indexed = _just(
        "telemetry",
        "--runs-dir",
        str(runs_dir),
        "--oneharness-bin",
        oneharness_bin,
        environment=environment,
    )
    assert indexed.returncode == 0, indexed.stderr
    run = json.loads(indexed.stdout)["runs"][0]
    assert json.loads(indexed.stdout)["schema_version"] == 5
    native_records = {
        role: [json.loads(line) for line in Path(record["path"]).read_text().splitlines()]
        for role, record in by_role.items()
    }
    expected_providers = {
        (
            str(items[-1].get("provider", "oneharness")),
            str(items[-1].get("harness", "")),
            str(items[-1].get("model", "")),
        )
        for role, items in native_records.items()
        if role != "llmlint"
    }
    assert {
        (provider["provider"], provider.get("harness", ""), provider.get("model", ""))
        for provider in run["providers"]
    } == expected_providers
    expected_agent_ms = sum(
        int(item["duration_ms"])
        for role in ("agent",)
        for item in native_records[role]
        if isinstance(item.get("duration_ms"), int)
        and not isinstance(item["duration_ms"], bool)
        and item["duration_ms"] >= 0
    )
    assert round(run["timing"]["agent_seconds"] * 1000) == expected_agent_ms
    expected_judge_ms = sum(
        int(item["duration_ms"])
        for item in native_records["judge"]
        if isinstance(item.get("duration_ms"), int)
        and not isinstance(item["duration_ms"], bool)
        and item["duration_ms"] >= 0
    )
    assert round(run["timing"]["judge_seconds"] * 1000) == expected_judge_ms
    assert run["turns"] == sum(
        len(items) for role, items in native_records.items() if role != "llmlint"
    )
    breakdown = _just(
        "telemetry",
        "--runs-dir",
        str(runs_dir),
        "--oneharness-bin",
        oneharness_bin,
        "--breakdown",
        environment=environment,
    )
    assert breakdown.returncode == 0, breakdown.stderr
    assert "history-turns" in breakdown.stdout
    assert "Turn histogram:" in breakdown.stdout


def test_real_run_plan_waits_then_monitor_exits_only_after_attestation(
    tmp_path: Path, command_base: Any, onejudge_bin: str
) -> None:
    runs_dir = tmp_path / "runs"
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "prepare",
                        "persona": "engineer",
                        "task": "complete-now: real monitored turn",
                    },
                    {
                        "id": "approve",
                        "kind": "human",
                        "task": "Approve the monitored turn.",
                        "deps": ["prepare"],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    common = (
        "--runs-dir",
        str(runs_dir),
        "--base",
        str(command_base()),
        "--onejudge-bin",
        onejudge_bin,
        "--format",
        "json",
    )

    paused = _just("run-plan", str(plan_path), "--run", RUN_ID, *common)
    assert paused.returncode == 1, paused.stderr
    first = json.loads(paused.stdout)
    assert first["state"] == "waiting" and first["ok"] is False
    assert first["results"]["prepare"]["status"] == "done"
    assert first["results"]["approve"]["status"] == "waiting"
    indexed = _just("telemetry", "--runs-dir", str(runs_dir), "--oneharness-bin", "absent")
    assert indexed.returncode == 0, indexed.stderr
    prepare_node = next(
        node for node in json.loads(indexed.stdout)["runs"][0]["nodes"] if node["node"] == "prepare"
    )
    assert prepare_node["timing"]["wall_ms"] > 0
    assert prepare_node["timing"]["scheduling_seconds"] > 0
    assert prepare_node["timing"]["fractions"]["idle_orchestration"] < 1.0

    waiting = _just(
        "monitor",
        RUN_ID,
        "--once",
        "--runs-dir",
        str(runs_dir),
        "--format",
        "jsonl",
    )
    assert waiting.returncode == 0, waiting.stderr
    waiting_records = [json.loads(line) for line in waiting.stdout.splitlines()]
    assert any(record.get("id") == f"graph:{RUN_ID}/1/prepare" for record in waiting_records)
    assert waiting_records[-1]["type"] == "heartbeat"
    assert waiting_records[-1]["state"] == "waiting"
    assert waiting_records[-1]["round"] == 1
    assert waiting_records[-1]["detail"] == "1 done, 1 waiting"

    resumed = _just("next-round", RUN_ID, "--complete-human", "approve", *common)
    assert resumed.returncode == 0, resumed.stderr
    assert "nothing to iterate" in resumed.stdout
    second = json.loads(
        (runs_dir / RUN_ID / "round-02" / "result.json").read_text(encoding="utf-8")
    )
    assert second["ok"] is True and second["state"] == "complete"
    assert second["results"]["approve"]["status"] == "done"

    completed = _just(
        "monitor",
        RUN_ID,
        "--runs-dir",
        str(runs_dir),
        "--format",
        "jsonl",
        "--heartbeat",
        "0.01",
        "--poll-interval",
        "0.01",
    )
    assert completed.returncode == 0, completed.stderr
    completed_records = [json.loads(line) for line in completed.stdout.splitlines()]
    assert any(
        record.get("id") == f"graph:{RUN_ID}/1/approve"
        and record.get("summary", "").startswith("human-attested")
        for record in completed_records
    )
    assert completed_records[-1] == {
        "type": "heartbeat",
        "at": completed_records[-1]["at"],
        "run_id": RUN_ID,
        "round": 2,
        "state": "complete",
        "detail": "graph complete",
        "next_poll_seconds": 0.01,
    }
    assert (runs_dir / RUN_ID / "round-01" / "result.json").is_file()
    assert (runs_dir / RUN_ID / "round-02" / "result.json").is_file()
    events = [
        json.loads(line)
        for line in (runs_dir / RUN_ID / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(event["kind"] == "human-attested" for event in events)


def test_real_failed_run_telemetry_stops_wall_time_at_settlement(
    tmp_path: Path, command_base: Any, onejudge_bin: str
) -> None:
    runs_dir = tmp_path / "runs"
    common = (
        "--runs-dir",
        str(runs_dir),
        "--base",
        str(command_base()),
        "--onejudge-bin",
        onejudge_bin,
        "--format",
        "json",
    )

    def run_failure(run_id: str, task: str) -> None:
        plan_path = tmp_path / f"{run_id}.json"
        plan_path.write_text(
            json.dumps(
                {
                    "tasks": [
                        {
                            "id": "fail",
                            "persona": "engineer",
                            "task": task,
                            "max_turns": 1,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        failed = _just("run-plan", str(plan_path), "--run", run_id, *common)
        assert failed.returncode == 1, failed.stderr
        assert json.loads(failed.stdout)["state"] == "failed"

    run_failure("failed-wall", "should-fail: prove failed wall telemetry")
    run_failure("provider", "provider-errors: prove provider classification")
    run_failure("configuration", "configuration-errors: prove configuration classification")
    run_failure("unknown", "unknown-errors: prove unknown classification")

    first = _just("telemetry", "--runs-dir", str(runs_dir), "--all")
    assert first.returncode == 0, first.stderr
    first_runs = {run["run_id"]: run for run in json.loads(first.stdout)["runs"]}
    first_run = first_runs["failed-wall"]
    assert first_run["failure"]["class"] == "agent"
    assert first_runs["provider"]["failure"]["class"] == "provider"
    assert first_runs["configuration"]["failure"]["class"] == "configuration"
    assert first_runs["unknown"]["failure"]["class"] == "unknown"
    time.sleep(0.05)
    second = _just("telemetry", "--runs-dir", str(runs_dir), "--all")
    assert second.returncode == 0, second.stderr
    second_run = next(
        run for run in json.loads(second.stdout)["runs"] if run["run_id"] == "failed-wall"
    )
    assert second_run["state"] == "failed"
    assert second_run["timing"]["wall_seconds"] == first_run["timing"]["wall_seconds"]
    active_only = _just("telemetry", "--runs-dir", str(runs_dir))
    assert active_only.returncode == 0, active_only.stderr
    assert json.loads(active_only.stdout)["runs"] == []
    missing = _just("telemetry", "--runs-dir", str(tmp_path / "missing-runs"))
    assert missing.returncode == 0, missing.stderr
    assert json.loads(missing.stdout)["runs"] == []


def test_monitor_backoff_resets_after_real_human_attestation(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    plan_path = tmp_path / "human-plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {"id": "approve", "kind": "human", "task": "Approve the monitored run."},
                    {
                        "id": "release",
                        "kind": "human",
                        "task": "Release the monitored run.",
                        "deps": ["approve"],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    started = _just(
        "run-plan",
        str(plan_path),
        "--run",
        "backoff-reset",
        "--runs-dir",
        str(runs_dir),
        "--format",
        "json",
    )
    assert started.returncode == 1, started.stderr
    process = subprocess.Popen(
        [
            "just",
            "monitor",
            "backoff-reset",
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
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    before: list[dict[str, Any]] = []
    try:
        assert process.stdout is not None
        while not any(record.get("next_poll_seconds") == 0.04 for record in before):
            before.append(json.loads(process.stdout.readline()))
        resumed = _just(
            "next-round",
            "backoff-reset",
            "--complete-human",
            "approve",
            "--runs-dir",
            str(runs_dir),
            "--format",
            "json",
        )
        assert resumed.returncode == 1, resumed.stderr
        transition: list[dict[str, Any]] = []
        while not any(record.get("kind") == "human-attested" for record in transition):
            transition.append(json.loads(process.stdout.readline()))
        attested = next(record for record in transition if record.get("kind") == "human-attested")
        assert attested["next_poll_seconds"] == 0.01
        released = _just(
            "next-round",
            "backoff-reset",
            "--complete-human",
            "release",
            "--runs-dir",
            str(runs_dir),
            "--format",
            "json",
        )
        assert released.returncode == 0, released.stderr
        remainder, stderr = process.communicate(timeout=e2e_timeout(30))
        assert process.returncode == 0, stderr
    finally:
        _terminate(process)
    after = [json.loads(line) for line in remainder.splitlines()]
    assert after[-1]["state"] == "complete"
    events = [
        json.loads(line)
        for line in (runs_dir / "backoff-reset" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert sum(event["kind"] == "human-attested" for event in events) == 2


class _PausingGitHub(FakeGitHub):
    """Hold native auto-merge open long enough for the live monitor pass."""

    def __init__(self, origin: Path) -> None:
        super().__init__(origin)
        self.ready = threading.Event()
        self.release = threading.Event()
        self.status_calls = 0

    def status(self, pr: PullRequest) -> PRStatus:
        state = self._prs[pr.number]
        self.status_calls += 1
        # GitHubMergeStrategy reads once to detect a draft, then begins its merge
        # loop. Pause that second read before either direct or native auto-merge.
        if self.status_calls == 2 and not state.merged:
            self.ready.set()
            if not self.release.wait(e2e_timeout(15)):
                raise AssertionError("test did not release the paused auto-merge")
        return super().status(pr)


class _MonitorGitHub:
    """Read the fake GitHub state without causing its simulated auto-merge."""

    def __init__(self, lifecycle: _PausingGitHub) -> None:
        self.lifecycle = lifecycle

    def status(self, pr: PullRequest) -> PRStatus:
        state = self.lifecycle._prs[pr.number]
        return PRStatus(
            number=pr.number,
            state="MERGED" if state.merged else "OPEN",
            merged=state.merged,
            merge_state_status="CLEAN",
            checks=(Check("ci", "SUCCESS", True),),
            draft=state.draft,
        )


class _OfflineGitHub:
    def status(self, pr: PullRequest) -> PRStatus:
        raise GitHubError("offline after lifecycle completion")


def _lifecycle_workspace(tmp_path: Path, origin: Path) -> tuple[Workspace, Path]:
    canonical = gitops.clone(origin, tmp_path / "canonical")
    workspace = Workspace(
        tmp_path / "worktrees",
        resolver=lambda _spec: canonical,
        workflow="local",
    )
    return workspace, canonical


def test_real_lifecycle_commit_and_pr_survive_live_state(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    """The tracked journal, real git lifecycle, and GitHub seam form one stream.

    The preceding tests in this module exercise real onejudge and oneharness. This
    slice composes the remaining sanctioned boundaries: only the paid agent is
    replaced by its writing seam and only GitHub decisioning is faked; git, the
    lifecycle commit/merge, journal, monitor, snapshots, and detail CLI are real.
    """
    origin = bare_origin()
    workspace, canonical = _lifecycle_workspace(tmp_path, origin)
    runs_dir = tmp_path / "runs"
    run_id = RunId(LIFECYCLE_RUN_ID)
    run_dir = runs_dir / run_id
    node_id = NodeId("ship")
    branch = "orchestrator/monitor-lifecycle-e2e"
    plan = {
        "tasks": [
            {
                "id": node_id,
                "repo": "acme/widget",
                "persona": "engineer",
                "task": "write the monitored lifecycle change",
                "branch": branch,
            }
        ]
    }

    # A stopped executor is nonterminal: the next recorded round is allowed to
    # continue the same run, and the monitor must replay both rounds.
    _, first_dir = prepare_round(run_dir, plan)
    first_journal = open_journal(run_dir, run_id, 1)
    first_journal.append("round-started", detail={"nodes": 1, "concurrency": 1})
    first_node = NodeJournal(first_journal, node_id, run_id, 1)
    first_node.append("node-started", detail={"node_kind": "lifecycle"})
    first_node.append("node-failed", detail={"detail": "executor stopped"})
    first_journal.append("round-finished", detail={"state": "failed", "ok": False})
    write_result(
        first_dir,
        {
            "ok": False,
            "state": "failed",
            "started_order": [node_id],
            "results": {
                node_id: {
                    "kind": "agent",
                    "status": "failed",
                    "task": "write the monitored lifecycle change",
                    "error": "executor stopped",
                }
            },
        },
    )
    failed_telemetry = _just("telemetry", "--runs-dir", str(runs_dir), "--all")
    assert failed_telemetry.returncode == 0, failed_telemetry.stderr
    failed_record = json.loads(failed_telemetry.stdout)["runs"][0]
    assert failed_record["failure"]["class"] == "agent"

    _, second_dir = prepare_round(run_dir, plan)
    second_journal = open_journal(run_dir, run_id, 2)
    second_journal.append("round-started", detail={"nodes": 1, "concurrency": 1})
    second_node = NodeJournal(second_journal, node_id, run_id, 2)
    second_node.append("node-started", detail={"node_kind": "lifecycle"})
    lifecycle_github = _PausingGitHub(origin)
    monitor_github = _MonitorGitHub(lifecycle_github)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            run_repo_task,
            "acme/widget",
            "write the monitored lifecycle change",
            "engineer",
            workspace=workspace,
            merge=GitHubMergeStrategy(lifecycle_github),
            url=str(origin),
            branch=branch,
            repo_type="single-owner",
            dispatch_fn=make_writing_dispatch(filename="monitored.txt"),
            verify_cmd=["sh", "-c", "test -f monitored.txt"],
            sleep=lambda _seconds: None,
            journal=second_node,
        )
        if not lifecycle_github.ready.wait(e2e_timeout(15)):
            early = future.result(timeout=e2e_timeout(1))
            raise AssertionError(
                f"lifecycle did not reach its PR merge wait: {early.outcome}: {early.detail}"
            )
        monitor = Monitor(
            run_id=run_id,
            run_dir=run_dir,
            oneharness_bin=str(tmp_path / "absent-oneharness"),
            github=monitor_github,
            _checkout_cache={"acme/widget": canonical},
        )
        try:
            live = monitor.poll()
            time.sleep(0.01)
            active_telemetry = _just(
                "telemetry",
                "--runs-dir",
                str(runs_dir),
                "--oneharness-bin",
                str(tmp_path / "absent-oneharness"),
            )
            assert active_telemetry.returncode == 0, active_telemetry.stderr
            active_record = json.loads(active_telemetry.stdout)["runs"][0]
            assert active_record["timing"]["publication_wait_seconds"] > 0
        finally:
            lifecycle_github.release.set()
        result = future.result(timeout=e2e_timeout(30))

    assert result.ok and result.outcome == "merged"
    assert result.pr is not None and result.pr.number == 1
    live_ids = {str(event.stream_id) for event in live}
    git_ids = sorted(item for item in live_ids if item.startswith("git:acme/widget@"))
    assert len(git_ids) == 1
    assert "pr:acme/widget#1" in live_ids
    assert f"graph:{run_id}/2/{node_id}" in live_ids

    second_node.append("node-settled", detail={"status": "done", "outcome": result.outcome})
    second_journal.append("round-finished", detail={"state": "complete", "ok": True})
    lifecycle_item = result_payload(result)
    lifecycle_item.update(
        {
            "kind": "agent",
            "status": "done",
            "task": "write the monitored lifecycle change",
            "error": None,
        }
    )
    write_result(
        second_dir,
        {
            "ok": True,
            "state": "complete",
            "started_order": [node_id],
            "results": {node_id: lifecycle_item},
        },
    )
    settled = monitor.poll()
    assert any(
        str(event.stream_id) == "pr:acme/widget#1" and "merged" in event.summary
        for event in settled
    )
    assert gitops.ref_sha(origin, "main") == gitops.ref_sha(origin, branch)

    telemetry_command = _just(
        "telemetry",
        "--runs-dir",
        str(runs_dir),
        "--all",
        "--oneharness-bin",
        str(tmp_path / "absent-oneharness"),
    )
    assert telemetry_command.returncode == 0, telemetry_command.stderr
    record = json.loads(telemetry_command.stdout)["runs"][0]
    assert record["phase"] == "complete"
    assert record["last_event"] == "round-finished"
    timing = record["timing"]
    assert isinstance(timing, dict) and timing["gate_seconds"] >= 0
    node = record["nodes"][0]
    assert isinstance(node, dict)
    assert (node["comparison_remote"], node["comparison_base"]) == ("origin", "main")
    attestation = node["gate_attestation"]
    assert isinstance(attestation, dict)
    assert attestation["comparison_base"] == "main"
    assert attestation["commit"]
    assert json.loads(telemetry_command.stdout)["metrics"]["green_to_publication_seconds"]
    active_only = _just("telemetry", "--runs-dir", str(runs_dir))
    assert active_only.returncode == 0, active_only.stderr
    assert json.loads(active_only.stdout)["runs"] == []

    for detail_id in (
        git_ids[0],
        "pr:acme/widget#1",
        f"graph:{run_id}/2/{node_id}",
    ):
        shown = _just("history-show", detail_id, "--runs-dir", str(runs_dir))
        assert shown.returncode == 0, shown.stderr
        assert f"Reference: {detail_id}" in shown.stdout

    replay = Monitor(
        run_id=run_id,
        run_dir=run_dir,
        oneharness_bin=str(tmp_path / "absent-oneharness"),
        github=_OfflineGitHub(),
        snapshot=load_snapshot(run_dir),
        _checkout_cache={},
    )
    replay_ids = {str(event.stream_id) for event in replay.poll()}
    assert git_ids[0] in replay_ids
    assert "pr:acme/widget#1" in replay_ids

    completed = _just(
        "monitor",
        str(run_id),
        "--runs-dir",
        str(runs_dir),
        "--format",
        "jsonl",
        "--heartbeat",
        "0.01",
        "--poll-interval",
        "0.01",
    )
    assert completed.returncode == 0, completed.stderr
    records = [json.loads(line) for line in completed.stdout.splitlines()]
    assert any(record.get("summary", "").endswith("executor stopped") for record in records)
    assert records[-1]["state"] == "complete"
    assert records[-1]["detail"] == "graph complete"
