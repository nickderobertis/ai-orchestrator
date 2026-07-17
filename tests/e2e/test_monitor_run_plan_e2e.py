"""Real oneharness history/watch acceptance for graph-labelled monitor inputs."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from orchestrator import REPO_ROOT

FAKE_HARNESS = REPO_ROOT / "tests" / "e2e" / "fake_harness.py"
RUN_ID = "real-monitor-e2e"
GRAPH_LABELS = (
    "run_id=real-monitor-e2e",
    "round=1",
    "node=history-turns",
    "step=record",
)


def _just(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["just", *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=180,
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
    deadline = time.monotonic() + 15
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
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _run_record(
    oneharness_bin: str,
    *,
    config: Path,
    name: str,
    environment: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            oneharness_bin,
            "run",
            "--config",
            str(config),
            "--prompt",
            f"record {name}",
            "--history",
            "--history-name",
            name,
            "--compact",
            *_graph_label_args(),
        ],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
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


def test_real_history_labels_and_cursor_watch(oneharness_bin: str, tmp_path: Path) -> None:
    history_dir = tmp_path / "history"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "codex").symlink_to(FAKE_HARNESS)
    (bin_dir / "claude").symlink_to(FAKE_HARNESS)

    environment = os.environ.copy()
    for inherited in (
        "ONEHARNESS_HARNESSES",
        "ONEHARNESS_MODELS",
        "ONEHARNESS_HISTORY_LABELS",
    ):
        environment.pop(inherited, None)
    environment.update(
        {
            "ONEHARNESS_BIN_CODEX": str(bin_dir / "codex"),
            "ONEHARNESS_BIN_CLAUDE_CODE": str(bin_dir / "claude"),
            "ONEHARNESS_HISTORY_DIR": str(history_dir),
            "FAKE_HARNESS_LOG": str(tmp_path / "fake-harness.jsonl"),
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
        timeout=60,
    )
    assert listed.returncode == 0, listed.stderr
    records = json.loads(listed.stdout)
    assert isinstance(records, list) and len(records) == 3
    by_role = {record["labels"]["role"]: record for record in records}
    assert set(by_role) == {"agent", "judge", "llmlint"}
    for record in records:
        assert record["labels"] == {
            "node": "history-turns",
            "role": record["labels"]["role"],
            "round": "1",
            "run_id": RUN_ID,
            "step": "record",
        }

    watcher = _watch(oneharness_bin, history_dir)
    try:
        envelopes = _drain_jsonl(watcher, 3)
    finally:
        _terminate(watcher)
    assert len(envelopes) == 3, envelopes
    assert {envelope["type"] for envelope in envelopes} == {"record"}
    watched_ids = [envelope["record"]["history_id"] for envelope in envelopes]
    assert len(watched_ids) == len(set(watched_ids)) == 3

    resumed_watcher = _watch(oneharness_bin, history_dir, after=watched_ids[0])
    try:
        resumed = _drain_jsonl(resumed_watcher, 2)
    finally:
        _terminate(resumed_watcher)
    resumed_ids = [envelope["record"]["history_id"] for envelope in resumed]
    assert watched_ids[0] not in resumed_ids
    assert resumed_ids == watched_ids[1:]


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
                        "persona": "backend-engineer",
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
    }
    assert (runs_dir / RUN_ID / "round-01" / "result.json").is_file()
    assert (runs_dir / RUN_ID / "round-02" / "result.json").is_file()
    events = [
        json.loads(line)
        for line in (runs_dir / RUN_ID / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(event["kind"] == "human-attested" for event in events)
