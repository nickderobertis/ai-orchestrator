"""E2E journeys for the canonical recorded tracked-graph CLI.

The direct-agent journeys invoke ``just run-plan`` / ``just next-round`` and
therefore spawn the adopted real onejudge binary.  Only onejudge's paid model
provider is replaced by the command-provider protocol double from ``conftest``.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pytest

from orchestrator import REPO_ROOT, gitops
from orchestrator.registry import Registry


def _just(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["just", *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("invalid_field", "invalid_value", "message"),
    [
        ("mode", "unknown", "resume 'mode' must be 'pause' or 'retry'"),
        ("source_round", 0, "resume 'source_round' must be a positive integer"),
    ],
)
def test_run_plan_rejects_invalid_retry_resume_contract(
    tmp_path: Path, invalid_field: str, invalid_value: object, message: str
) -> None:
    plan = tmp_path / "invalid-resume.json"
    resume = {
        "branch": "feature/preserved",
        "base_branch": "main",
        "pr_base": "main",
        "checkpoint": "a" * 40,
        "completed_steps": [],
        "pr": None,
        "mode": "retry",
        invalid_field: invalid_value,
    }
    plan.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "retry",
                        "repo": str(tmp_path / "target"),
                        "persona": "engineer",
                        "task": "Retry preserved work.",
                        "resume": resume,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = _just("run-plan", str(plan), "--no-record")

    assert result.returncode == 2
    assert message in result.stderr


def test_direct_human_pause_attestation_and_release_use_real_onejudge(
    tmp_path: Path, command_base, onejudge_bin: str
) -> None:
    """A recorded direct graph pauses, attests, and resumes without replay."""
    runs = tmp_path / "runs"
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "tasks": [
                    {
                        "id": "prepare",
                        "persona": "planner",
                        "task": "complete-now: prepare the release.",
                    },
                    {
                        "id": "approve",
                        "kind": "human",
                        "task": "Approve the prepared release.",
                        "deps": ["prepare"],
                    },
                    {
                        "id": "publish",
                        "persona": "engineer",
                        "task": "complete-now: publish the approved release.",
                        "deps": ["approve"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    common = (
        "--runs-dir",
        str(runs),
        "--base",
        str(command_base()),
        "--onejudge-bin",
        onejudge_bin,
        "--format",
        "json",
    )

    paused = _just("run-plan", str(plan), "--run", "human-direct", *common)

    assert paused.returncode == 1, paused.stderr
    first = json.loads(paused.stdout)
    assert first["schema_version"] == 2 and first["round"] == 1
    assert first["ok"] is False and first["state"] == "waiting"
    assert first["started_order"] == ["prepare", "approve"]
    assert first["results"]["prepare"]["status"] == "done"
    assert first["results"]["approve"] == {
        "kind": "human",
        "status": "waiting",
        "task": "Approve the prepared release.",
        "unblocks": ["publish"],
        "human_actions": [
            {
                "ref": "approve",
                "task": "Approve the prepared release.",
                "unblocks": ["publish"],
                "unblocks_publication": False,
            }
        ],
        "error": "awaiting human action",
    }
    assert first["results"]["publish"]["status"] == "blocked"
    assert first["results"]["publish"]["blocked_by"] == ["approve"]
    assert "Approve the prepared release." in paused.stderr
    assert "--complete-human approve" in paused.stderr

    round_one = runs / "human-direct" / "round-01"
    assert json.loads((round_one / "result.json").read_text(encoding="utf-8")) == first
    events = [
        json.loads(line)
        for line in (runs / "human-direct" / "events.jsonl").read_text().splitlines()
    ]
    assert [event["detail"]["definition"]["id"] for event in events[:3]] == [
        "prepare",
        "approve",
        "publish",
    ]
    assert [(event["detail"]["from"], event["detail"]["to"]) for event in events[3:5]] == [
        ("prepare", "approve"),
        ("approve", "publish"),
    ]
    started = next(event for event in events if event["kind"] == "round-started")
    assert started["detail"]["plan"] == {"schema_version": 2}
    finished = next(event for event in events if event["kind"] == "round-finished")
    assert finished["detail"]["result"] == first
    for invalid in ("missing", "publish", "prepare"):
        rejected = _just(
            "next-round",
            "human-direct",
            "--complete-human",
            invalid,
            *common,
        )
        assert rejected.returncode == 2
        assert "recorded waiting human task" in rejected.stderr
        assert not (runs / "human-direct" / "humans.json").exists()
        assert not (runs / "human-direct" / "round-02").exists()

    resumed = _just(
        "next-round",
        "human-direct",
        "--complete-human",
        "approve",
        *common,
    )

    assert resumed.returncode == 0, resumed.stderr
    second = json.loads(resumed.stdout)
    assert second["ok"] is True and second["state"] == "complete"
    assert second["started_order"] == ["publish"]
    assert list(second["results"]) == ["publish"]
    assert second["results"]["publish"]["status"] == "done"
    second_plan = json.loads(
        (runs / "human-direct" / "round-02" / "plan.json").read_text(encoding="utf-8")
    )
    assert second_plan["schema_version"] == 2
    assert [task["id"] for task in second_plan["tasks"]] == ["publish"]
    assert second_plan["tasks"][0]["deps"] == []

    completions = json.loads((runs / "human-direct" / "humans.json").read_text(encoding="utf-8"))[
        "completions"
    ]
    assert len(completions) == 1
    assert completions[0]["ref"] == "approve"
    assert completions[0]["round"] == 1
    completed_at = datetime.fromisoformat(completions[0]["completed_at"])
    assert completed_at.tzinfo is not None

    repeated = _just(
        "next-round",
        "human-direct",
        "--complete-human",
        "approve",
        *common,
    )
    assert repeated.returncode == 2
    assert "already completed" in repeated.stderr
    assert len(list((runs / "human-direct").glob("round-*"))) == 2

    replay_plan = tmp_path / "replay-plan.json"
    replay_plan.write_text(json.dumps(second_plan))
    round_two = runs / "human-direct" / "round-02"
    for artifact in ("plan.json", "result.json", "status.json"):
        (round_two / artifact).unlink()
    replayed = _just("run-plan", str(replay_plan), "--run", "human-direct", "--recover", *common)
    assert replayed.returncode == 0, replayed.stderr
    assert json.loads((round_two / "plan.json").read_text()) == second_plan
    assert json.loads((round_two / "result.json").read_text()) == second
    round_three = runs / "human-direct" / "round-03"
    assert (round_three / "result.json").exists()
    third = json.loads((round_three / "result.json").read_text())
    (round_three / "result.json").unlink()
    result_only = _just("run-plan", str(replay_plan), "--run", "human-direct", "--recover", *common)
    assert result_only.returncode == 0, result_only.stderr
    assert json.loads((round_three / "result.json").read_text()) == third
    assert (runs / "human-direct" / "round-04" / "result.json").exists()


def test_recover_interrupted_real_cli_does_not_duplicate_node_start(
    tmp_path: Path, command_base, onejudge_bin: str
) -> None:
    runs = tmp_path / "runs"
    plan = tmp_path / "interrupt.json"
    plan.write_text(
        json.dumps({"tasks": [{"id": "work", "persona": "engineer", "task": "complete-now"}]})
    )
    command = [
        "just",
        "run-plan",
        str(plan),
        "--run",
        "interrupted",
        "--runs-dir",
        str(runs),
        "--base",
        str(command_base()),
        "--onejudge-bin",
        onejudge_bin,
        "--format",
        "json",
    ]
    process = subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    events_path = runs / "interrupted" / "events.jsonl"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if events_path.exists() and '"kind": "node-started"' in events_path.read_text():
            break
        time.sleep(0.01)
    else:
        process.kill()
        pytest.fail("run-plan did not durably start its node")
    os.killpg(process.pid, signal.SIGKILL)
    process.wait()
    round_dir = runs / "interrupted" / "round-01"
    (round_dir / "plan.json").unlink()

    recovered = subprocess.run(
        [*command, "--recover"], cwd=REPO_ROOT, text=True, capture_output=True, check=False
    )
    assert recovered.returncode == 0, recovered.stderr
    events = [json.loads(line) for line in events_path.read_text().splitlines()]
    assert sum(event["kind"] == "node-started" for event in events) == 1

    baseline = events_path.read_bytes()
    artifacts = {
        name: (round_dir / name).read_bytes()
        for name in ("plan.json", "result.json", "status.json")
    }
    seq = events[-1]["seq"] + 1
    envelope = {"version": 1, "seq": seq, "at": 0, "run_id": "interrupted", "round": 1}
    corruptions = [
        (b"\n", "is blank"),
        (b"\xff\n", "malformed authoritative event"),
        (b"{broken}\n", "malformed authoritative event"),
        (
            (json.dumps({**envelope, "kind": "node-started"}) + "\n").encode(),
            "invalid authoritative event",
        ),
        (
            (
                json.dumps({**envelope, "kind": "round-started", "run_id": "foreign"}) + "\n"
            ).encode(),
            "belongs to another run",
        ),
        (
            (json.dumps({**envelope, "kind": "round-started", "seq": seq + 1}) + "\n").encode(),
            "sequence must be contiguous",
        ),
        (
            (json.dumps({**envelope, "kind": "node-dropped"}) + "\n").encode(),
            "unknown authoritative event",
        ),
        (
            (
                json.dumps({**envelope, "kind": "node-added", "detail": events[0]["detail"]}) + "\n"
            ).encode(),
            "follows round-finished",
        ),
        (
            (
                json.dumps(
                    {
                        **envelope,
                        "kind": "edge-added",
                        "detail": {"from": "work", "to": "missing"},
                    }
                )
                + "\n"
            ).encode(),
            "follows round-finished",
        ),
        (
            (
                json.dumps(
                    {
                        **envelope,
                        "kind": "node-settled",
                        "node": "work",
                        "detail": {"status": "done"},
                    }
                )
                + "\n"
            ).encode(),
            "follows round-finished",
        ),
        (
            (
                json.dumps(
                    {**envelope, "kind": "round-finished", "detail": {"result": {"ok": "yes"}}}
                )
                + "\n"
            ).encode(),
            "follows round-finished",
        ),
        (
            (
                json.dumps(
                    {
                        **envelope,
                        "kind": "human-attested",
                        "node": "work",
                        "detail": {"ref": "work"},
                    }
                )
                + "\n"
                + json.dumps(
                    {
                        **envelope,
                        "seq": seq + 1,
                        "kind": "human-attested",
                        "node": "work",
                        "detail": {"ref": "work"},
                    }
                )
                + "\n"
            ).encode(),
            "target 'work' is not a projected human action",
        ),
        (
            (
                json.dumps(
                    {
                        **envelope,
                        "kind": "human-attested",
                        "node": "unknown",
                        "detail": {"ref": "unknown"},
                    }
                )
                + "\n"
            ).encode(),
            "references an unknown graph node",
        ),
    ]
    for corruption, diagnostic in corruptions:
        # llmlint: ignore[tests_mirror_real_usage] These bytes deliberately model journal
        # corruption that has no user-facing producer; setup and recovery both use run-plan.
        events_path.write_bytes(baseline + corruption)
        for artifact in artifacts:
            (round_dir / artifact).unlink(missing_ok=True)
        malformed = subprocess.run(
            [*command, "--recover"], cwd=REPO_ROOT, text=True, capture_output=True, check=False
        )
        assert malformed.returncode == 2
        assert "cannot replay authoritative event log" in malformed.stderr
        assert diagnostic in malformed.stderr
        for name, content in artifacts.items():
            (round_dir / name).write_bytes(content)
    pre_finish_events = [event for event in events if event["kind"] != "round-finished"]
    pre_finish = b"".join((json.dumps(event) + "\n").encode() for event in pre_finish_events)
    next_seq = pre_finish_events[-1]["seq"] + 1
    terminal_event = next(event for event in events if event["kind"] == "round-finished")
    disagreement = json.loads(json.dumps(terminal_event))
    disagreement["seq"] = next_seq
    disagreement["detail"]["result"]["results"]["work"]["status"] = "failed"
    unknown_result = json.loads(json.dumps(terminal_event))
    unknown_result["seq"] = next_seq
    unknown_result["detail"]["result"]["results"]["unknown"] = {"status": "done"}
    unknown_started = json.loads(json.dumps(terminal_event))
    unknown_started["seq"] = next_seq
    unknown_started["detail"]["result"]["started_order"].append("unknown")
    never_started_events: list[dict[str, object]] = []
    inserted = False
    for original in events:
        event = json.loads(json.dumps(original))
        if event["kind"] == "round-started" and not inserted:
            never_started_events.append(
                {
                    "version": 1,
                    "seq": event["seq"],
                    "at": 0,
                    "kind": "node-added",
                    "run_id": "interrupted",
                    "round": 1,
                    "detail": {
                        "definition": {
                            "id": "idle",
                            "task": "No diff.",
                            "expects_no_diff": True,
                        }
                    },
                }
            )
            inserted = True
        if inserted:
            event["seq"] += 1
        if event["kind"] == "round-finished":
            event["detail"]["result"]["started_order"].append("idle")
            event["detail"]["result"]["results"]["idle"] = {"status": "skipped"}
        never_started_events.append(event)
    mismatched_attestation_events: list[dict[str, object]] = []
    inserted = False
    for original in events:
        event = json.loads(json.dumps(original))
        if event["kind"] == "round-started" and not inserted:
            mismatched_attestation_events.append(
                {
                    "version": 1,
                    "seq": event["seq"],
                    "at": 0,
                    "kind": "node-added",
                    "run_id": "interrupted",
                    "round": 1,
                    "detail": {
                        "definition": {
                            "id": "approval",
                            "kind": "human",
                            "task": "Approve",
                        }
                    },
                }
            )
            inserted = True
        if inserted:
            event["seq"] += 1
        mismatched_attestation_events.append(event)
    mismatched_attestation_events.append(
        {
            "version": 1,
            "seq": mismatched_attestation_events[-1]["seq"] + 1,
            "at": 0,
            "kind": "human-attested",
            "run_id": "interrupted",
            "round": 1,
            "node": "approval",
            "detail": {"ref": "different"},
        }
    )
    prefix_failures = [
        (
            b"".join(
                (
                    json.dumps(
                        {**event, "detail": {}} if event["kind"] == "round-started" else event
                    )
                    + "\n"
                ).encode()
                for event in pre_finish_events
            ),
            "round-started requires plan metadata",
        ),
        (
            pre_finish
            + (
                json.dumps(
                    {
                        **terminal_event,
                        "seq": next_seq,
                        "detail": {"result": {"ok": "not-a-boolean"}},
                    }
                )
                + "\n"
            ).encode(),
            "round-finished result is invalid",
        ),
        (
            pre_finish + (json.dumps(disagreement) + "\n").encode(),
            "round-finished result disagrees with projected node 'work'",
        ),
        (
            pre_finish + (json.dumps(unknown_result) + "\n").encode(),
            "round-finished result references unknown node(s): unknown",
        ),
        (
            pre_finish + (json.dumps(unknown_started) + "\n").encode(),
            "round-finished started_order references unknown node(s): unknown",
        ),
        (
            b"".join((json.dumps(event) + "\n").encode() for event in never_started_events),
            "round-finished started_order contains node(s) without a start event: idle",
        ),
        (
            b"".join(
                (json.dumps(event) + "\n").encode() for event in mismatched_attestation_events
            ),
            "human-attested ref 'different' does not match locator 'approval'",
        ),
        (
            (json.dumps({**events[0], "unexpected": True}) + "\n").encode()
            + b"".join((json.dumps(event) + "\n").encode() for event in events[1:]),
            "unknown fields",
        ),
        ((json.dumps(events[0]) + "\n").encode(), "no round-started event"),
        (
            (json.dumps(events[0]) + "\n").encode()
            + (
                json.dumps(
                    {
                        **events[0],
                        "seq": 2,
                        "at": 0,
                    }
                )
                + "\n"
            ).encode()
            + (
                json.dumps(
                    {
                        **next(item for item in events if item["kind"] == "round-started"),
                        "seq": 3,
                    }
                )
                + "\n"
            ).encode(),
            "duplicate node-added",
        ),
        (
            pre_finish
            + (
                json.dumps(
                    {
                        **envelope,
                        "seq": next_seq,
                        "kind": "node-started",
                        "node": "missing",
                    }
                )
                + "\n"
            ).encode(),
            "references unknown node",
        ),
        (
            pre_finish
            + (
                json.dumps(
                    {**envelope, "seq": next_seq, "kind": "round-started", "detail": {"plan": {}}}
                )
                + "\n"
            ).encode(),
            "round-started may occur only once",
        ),
        (
            pre_finish
            + (
                json.dumps({**envelope, "seq": next_seq, "kind": "node-started", "node": "work"})
                + "\n"
            ).encode(),
            "started more than once",
        ),
        (
            b"".join(
                (json.dumps(event) + "\n").encode()
                for event in pre_finish_events
                if event["seq"]
                <= next(item["seq"] for item in pre_finish_events if item["kind"] == "node-started")
            )
            + (
                json.dumps(
                    {
                        **envelope,
                        "seq": next(
                            item["seq"]
                            for item in pre_finish_events
                            if item["kind"] == "node-started"
                        )
                        + 1,
                        "kind": "node-settled",
                        "node": "work",
                        "detail": {"status": "bogus"},
                    }
                )
                + "\n"
            ).encode(),
            "requires a terminal status",
        ),
        (
            (json.dumps(events[0]) + "\n").encode()
            + (
                json.dumps(
                    {
                        **envelope,
                        "seq": 2,
                        "kind": "edge-added",
                        "detail": {"from": "work", "to": "work"},
                    }
                )
                + "\n"
            ).encode()
            + (
                json.dumps(
                    {
                        **next(item for item in events if item["kind"] == "round-started"),
                        "seq": 3,
                    }
                )
                + "\n"
            ).encode(),
            "depends on itself",
        ),
        (
            (json.dumps(events[0]) + "\n").encode()
            + (
                json.dumps(
                    {
                        **envelope,
                        "seq": 2,
                        "kind": "edge-added",
                        "detail": {"from": "work", "to": "work"},
                    }
                )
                + "\n"
            ).encode()
            + (
                json.dumps(
                    {
                        **envelope,
                        "seq": 3,
                        "kind": "edge-added",
                        "detail": {"from": "work", "to": "work"},
                    }
                )
                + "\n"
            ).encode()
            + (
                json.dumps(
                    {
                        **next(item for item in events if item["kind"] == "round-started"),
                        "seq": 4,
                    }
                )
                + "\n"
            ).encode(),
            "duplicate edge",
        ),
    ]
    for invalid_log, diagnostic in prefix_failures:
        # llmlint: ignore[tests_mirror_real_usage] These bytes deliberately model journal
        # corruption that has no user-facing producer; setup and recovery both use run-plan.
        events_path.write_bytes(invalid_log)
        for artifact in artifacts:
            (round_dir / artifact).unlink(missing_ok=True)
        malformed = subprocess.run(
            [*command, "--recover"], cwd=REPO_ROOT, text=True, capture_output=True, check=False
        )
        assert malformed.returncode == 2
        assert diagnostic in malformed.stderr
        for name, content in artifacts.items():
            (round_dir / name).write_bytes(content)
    events_path.write_bytes(baseline)

    settled_plan = tmp_path / "settled.json"
    settled_plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "tasks": [
                    {
                        "id": "first",
                        "repo": "o/r",
                        "task": "No lifecycle diff.",
                        "expects_no_diff": True,
                    },
                    {
                        "id": "second",
                        "persona": "engineer",
                        "task": "complete-now",
                        "deps": ["first"],
                    },
                ],
            }
        )
    )
    settled_command = [
        *command[:2],
        str(settled_plan),
        "--run",
        "settled-prefix",
        *command[5:],
    ]
    settled_process = subprocess.Popen(
        settled_command,
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    settled_events = runs / "settled-prefix" / "events.jsonl"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        content = settled_events.read_text() if settled_events.exists() else ""
        if '"kind": "node-settled"' in content and content.count('"kind": "node-started"') >= 2:
            break
        time.sleep(0.01)
    else:
        settled_process.kill()
        pytest.fail("run-plan did not reach the settled-prefix recovery boundary")
    os.killpg(settled_process.pid, signal.SIGKILL)
    settled_process.wait()
    converged = subprocess.run(
        [*settled_command, "--recover"], cwd=REPO_ROOT, text=True, capture_output=True, check=False
    )
    assert converged.returncode == 0, converged.stderr
    settled_records = [json.loads(line) for line in settled_events.read_text().splitlines()]
    for node_id in ("first", "second"):
        assert (
            sum(
                event["kind"] == "node-started" and event.get("node") == node_id
                for event in settled_records
            )
            == 1
        )
        assert (
            sum(
                event["kind"] == "node-settled" and event.get("node") == node_id
                for event in settled_records
            )
            == 1
        )
    assert (
        json.loads((runs / "settled-prefix" / "round-01" / "result.json").read_text())["state"]
        == "complete"
    )


@pytest.mark.parametrize(
    ("event_version", "mutation", "diagnostic"),
    [
        (1, "missing", "legacy settled nodes without terminal payloads: done"),
        (2, "missing", "node-settled requires a serialized node result"),
        (2, "non-mapping", "invalid serialized node result"),
        (2, "status", "invalid serialized node result status"),
        (2, "shape", "invalid human_actions for done"),
    ],
)
# llmlint: ignore[tests_mirror_real_usage] These mutations model corrupt durable prefixes that
# have no valid producer; recovery itself is exercised exclusively through the public CLI.
def test_real_cli_rejects_terminal_prefix_without_required_payload(
    tmp_path: Path, event_version: int, mutation: str, diagnostic: str
) -> None:
    runs = tmp_path / "runs"
    plan = tmp_path / "legacy-prefix.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "tasks": [{"id": "done", "task": "No diff.", "expects_no_diff": True}],
            }
        )
    )
    command = [
        "run-plan",
        str(plan),
        "--run",
        f"bad-payload-v{event_version}-{mutation}",
        "--runs-dir",
        str(runs),
        "--format",
        "json",
    ]
    completed = _just(*command)
    assert completed.returncode == 0, completed.stderr
    round_dir = runs / f"bad-payload-v{event_version}-{mutation}" / "round-01"
    events_path = runs / f"bad-payload-v{event_version}-{mutation}" / "events.jsonl"
    events = [json.loads(line) for line in events_path.read_text().splitlines()]
    prefix = []
    for event in events:
        if event["kind"] == "round-finished":
            continue
        event["version"] = event_version
        if event["kind"] == "node-settled":
            match mutation:
                case "missing":
                    event["detail"].pop("result")
                case "non-mapping":
                    event["detail"]["result"] = "invalid"
                case "status":
                    event["detail"]["result"]["status"] = "failed"
                case _:
                    event["detail"]["result"]["human_actions"] = "invalid"
        prefix.append(event)
    events_path.write_text("".join(json.dumps(event) + "\n" for event in prefix))
    (round_dir / "result.json").unlink()
    (round_dir / "status.json").write_text(
        json.dumps({"status": "running", "pid": 999_999_999, "host": socket.gethostname()})
    )

    recovered = _just(*command, "--recover")
    assert recovered.returncode == 2
    assert diagnostic in recovered.stderr


def test_real_cli_replays_failed_and_waiting_terminal_prefixes(
    tmp_path: Path, command_base, onejudge_bin: str
) -> None:
    runs = tmp_path / "runs"

    def interrupt_and_recover(run_id: str, tasks: list[dict], terminal_kind: str):
        plan = tmp_path / f"{run_id}.json"
        plan.write_text(json.dumps({"tasks": tasks, "concurrency": 2}))
        command = [
            "just",
            "run-plan",
            str(plan),
            "--run",
            run_id,
            "--runs-dir",
            str(runs),
            "--base",
            str(command_base()),
            "--onejudge-bin",
            onejudge_bin,
            "--format",
            "json",
        ]
        process = subprocess.Popen(
            command,
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        events_path = runs / run_id / "events.jsonl"
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            content = events_path.read_text() if events_path.exists() else ""
            if (
                f'"kind": "{terminal_kind}"' in content
                and content.count('"kind": "node-started"') >= 1
            ):
                break
            time.sleep(0.005)
        else:
            process.kill()
            pytest.fail(f"run-plan did not emit {terminal_kind}")
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        recovered = subprocess.run(
            [*command, "--recover"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        return recovered, json.loads((runs / run_id / "round-01" / "result.json").read_text())

    failed, failed_result = interrupt_and_recover(
        "failed-prefix",
        [
            {"id": "failed", "persona": "engineer", "task": "should-fail", "max_turns": 1},
            {"id": "running", "persona": "engineer", "task": "should-fail", "max_turns": 5},
        ],
        "node-failed",
    )
    assert failed.returncode == 1, failed.stderr
    assert failed_result["results"]["failed"]["status"] == "failed"

    waiting, waiting_result = interrupt_and_recover(
        "waiting-prefix",
        [
            {"id": "approval", "kind": "human", "task": "Approve"},
            {"id": "blocked", "persona": "engineer", "task": "complete-now", "deps": ["approval"]},
            {"id": "running", "persona": "engineer", "task": "should-fail", "max_turns": 5},
        ],
        "human-waiting",
    )
    assert waiting.returncode == 1, waiting.stderr
    assert waiting_result["results"]["approval"]["status"] == "waiting"
    assert waiting_result["results"]["blocked"]["status"] == "blocked"


def test_recover_completes_a_partially_emitted_graph_without_duplicates(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    node_count = 2000
    plan = tmp_path / "large-static.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "tasks": [
                    {
                        "id": f"node-{index}",
                        "task": "No diff.",
                        "expects_no_diff": True,
                        **({"deps": ["node-0"]} if index else {}),
                    }
                    for index in range(node_count)
                ],
            }
        )
    )
    command = [
        "just",
        "run-plan",
        str(plan),
        "--run",
        "partial-topology",
        "--runs-dir",
        str(runs),
        "--format",
        "json",
    ]
    process = subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    events_path = runs / "partial-topology" / "events.jsonl"
    deadline = time.monotonic() + 10
    durable = 0
    while time.monotonic() < deadline:
        if events_path.exists():
            durable = events_path.read_text().count('"kind": "node-added"')
            if 0 < durable < node_count:
                break
        time.sleep(0.001)
    else:
        process.kill()
        pytest.fail(f"did not observe a partial graph-definition prefix (saw {durable})")
    os.killpg(process.pid, signal.SIGKILL)
    process.wait()

    prefix = events_path.read_bytes()
    prefix_records = [json.loads(line) for line in prefix.splitlines()]
    status_path = runs / "partial-topology" / "round-01" / "status.json"
    dead_status = status_path.read_bytes()
    malformed_definition = [dict(record) for record in prefix_records]
    malformed_definition[0] = {
        **malformed_definition[0],
        "detail": {"definition": "not-a-mapping"},
    }
    different_definition = [dict(record) for record in prefix_records]
    different_definition[0] = {
        **different_definition[0],
        "detail": {"definition": {"id": "node-0", "task": "Different."}},
    }
    wrong_edge = (
        prefix
        + (
            json.dumps(
                {
                    "version": 1,
                    "seq": prefix_records[-1]["seq"] + 1,
                    "at": 0,
                    "kind": "edge-added",
                    "run_id": "partial-topology",
                    "round": 1,
                    "detail": {"from": "node-9", "to": "node-10"},
                }
            )
            + "\n"
        ).encode()
    )
    comparison_failures = [
        (
            b"".join((json.dumps(item) + "\n").encode() for item in malformed_definition),
            "malformed",
        ),
        (
            b"".join((json.dumps(item) + "\n").encode() for item in different_definition),
            "do not match",
        ),
        (wrong_edge, "graph edges do not match"),
    ]
    invalid_envelopes = [
        (prefix + b"{broken}\n", "malformed authoritative event"),
        (
            b"".join(
                (json.dumps({**item, "unexpected": True} if index == 0 else item) + "\n").encode()
                for index, item in enumerate(prefix_records)
            ),
            "unknown fields",
        ),
        (
            b"".join(
                (json.dumps({**item, "run_id": "foreign"} if index == 0 else item) + "\n").encode()
                for index, item in enumerate(prefix_records)
            ),
            "belongs to another run",
        ),
        (
            b"".join(
                (
                    json.dumps({**item, "seq": item["seq"] + 1} if index == 0 else item) + "\n"
                ).encode()
                for index, item in enumerate(prefix_records)
            ),
            "sequence must be contiguous",
        ),
    ]
    for invalid_events, diagnostic in invalid_envelopes:
        # llmlint: ignore[tests_mirror_real_usage] Envelope corruption has no public
        # producer; the real CLI created the prefix and is the recovery interface under test.
        events_path.write_bytes(invalid_events)
        status_path.write_bytes(dead_status)
        rejected = subprocess.run(
            [*command, "--recover"], cwd=REPO_ROOT, text=True, capture_output=True, check=False
        )
        assert rejected.returncode == 2
        assert diagnostic in rejected.stderr
    for invalid_events, diagnostic in comparison_failures:
        # llmlint: ignore[tests_mirror_real_usage] These records deliberately corrupt the
        # real CLI's durable prefix; recovery itself is exercised only through run-plan.
        events_path.write_bytes(invalid_events)
        status_path.write_bytes(dead_status)
        rejected = subprocess.run(
            [*command, "--recover"], cwd=REPO_ROOT, text=True, capture_output=True, check=False
        )
        assert rejected.returncode == 2
        assert diagnostic in rejected.stderr
    events_path.write_bytes(prefix)
    status_path.write_bytes(dead_status)

    recovered = subprocess.run(
        [*command, "--recover"], cwd=REPO_ROOT, text=True, capture_output=True, check=False
    )
    assert recovered.returncode == 0, recovered.stderr
    events = [json.loads(line) for line in events_path.read_text().splitlines()]
    definitions = [
        event["detail"]["definition"]["id"] for event in events if event["kind"] == "node-added"
    ]
    assert definitions == [f"node-{index}" for index in range(node_count)]
    assert sum(event["kind"] == "round-started" for event in events) == 1


def test_recover_discards_only_a_torn_final_journal_line(
    tmp_path: Path, command_base, onejudge_bin: str
) -> None:
    runs = tmp_path / "runs"
    plan = tmp_path / "torn.json"
    plan.write_text(
        json.dumps({"tasks": [{"id": "work", "persona": "engineer", "task": "complete-now"}]})
    )
    command = [
        "just",
        "run-plan",
        str(plan),
        "--run",
        "torn-tail",
        "--runs-dir",
        str(runs),
        "--base",
        str(command_base()),
        "--onejudge-bin",
        onejudge_bin,
        "--format",
        "json",
    ]
    completed = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True, check=False)
    assert completed.returncode == 0, completed.stderr
    run_dir = runs / "torn-tail"
    events_path = run_dir / "events.jsonl"
    durable = events_path.read_bytes()
    with events_path.open("ab") as handle:
        handle.write(b'{"version":1,"seq":999,"kind":"node-started"')
    round_dir = run_dir / "round-01"
    for artifact in ("plan.json", "result.json", "status.json"):
        (round_dir / artifact).unlink()

    recovered = subprocess.run(
        [*command, "--recover"], cwd=REPO_ROOT, text=True, capture_output=True, check=False
    )
    assert recovered.returncode == 0, recovered.stderr
    assert events_path.read_bytes().startswith(durable)
    assert b'"seq":999' not in events_path.read_bytes()
    assert json.loads((round_dir / "result.json").read_text()) == json.loads(completed.stdout)


def test_legacy_direct_plan_and_recorded_ledger_still_run(
    tmp_path: Path, command_base, onejudge_bin: str
) -> None:
    """Pre-kind direct plans and pre-state ledgers continue through run-plan."""
    base = str(command_base())
    legacy_plan = {
        "concurrency": 1,
        "tasks": [
            {
                "id": "legacy-agent",
                "persona": "engineer",
                "task": "complete-now: run the old direct plan.",
            }
        ],
    }
    plan_path = tmp_path / "legacy-direct.json"
    plan_path.write_text(json.dumps(legacy_plan), encoding="utf-8")

    direct = _just(
        "run-plan",
        str(plan_path),
        "--no-record",
        "--base",
        base,
        "--onejudge-bin",
        onejudge_bin,
        "--format",
        "json",
    )
    assert direct.returncode == 0, direct.stderr
    direct_payload = json.loads(direct.stdout)
    assert direct_payload["schema_version"] == 2 and "round" not in direct_payload
    assert direct_payload["state"] == "complete"
    assert direct_payload["results"]["legacy-agent"]["status"] == "done"

    runs = tmp_path / "runs"
    old_round = runs / "old-ledger" / "round-01"
    old_round.mkdir(parents=True)
    (old_round / "plan.json").write_text(json.dumps(legacy_plan), encoding="utf-8")
    (old_round / "result.json").write_text(
        json.dumps(
            {
                "ok": False,
                "started_order": ["legacy-agent"],
                "results": {
                    "legacy-agent": {
                        "status": "failed",
                        "completed": False,
                        "exit_code": 1,
                        "verdicts": [],
                        "usage": {},
                        "error": "did not complete",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    edits = tmp_path / "legacy-edits.json"
    edits.write_text(
        json.dumps(
            {"retry": {"legacy-agent": {"task": "complete-now: finish the old recorded run."}}}
        ),
        encoding="utf-8",
    )

    continued = _just(
        "next-round",
        "old-ledger",
        str(edits),
        "--runs-dir",
        str(runs),
        "--base",
        base,
        "--onejudge-bin",
        onejudge_bin,
        "--format",
        "json",
    )
    assert continued.returncode == 0, continued.stderr
    continued_payload = json.loads(continued.stdout)
    assert continued_payload["state"] == "complete"
    assert continued_payload["results"]["legacy-agent"]["status"] == "done"
    assert (runs / "old-ledger" / "round-02" / "result.json").is_file()


def test_expects_no_diff_skips_onejudge_while_sibling_uses_real_boundary(
    tmp_path: Path, bare_origin, command_base, onejudge_bin: str
) -> None:
    """The explicit zero-yield node settles without invoking the coding backend."""
    no_op_dir = tmp_path / "no-op-project"
    ordinary_dir = tmp_path / "ordinary-project"
    no_op_dir.mkdir()
    ordinary_dir.mkdir()
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "zero-yield-canonical")
    Registry().register(str(canonical), workflow="local")
    plan = tmp_path / "zero-yield.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "tasks": [
                    {
                        "id": "certify-unchanged",
                        "task": "write-change complete-now: certify the unchanged handoff.",
                        "project_dir": str(no_op_dir),
                        "expects_no_diff": True,
                    },
                    {
                        "id": "ordinary",
                        "persona": "engineer",
                        "task": "write-change complete-now: perform ordinary work.",
                        "project_dir": str(ordinary_dir),
                    },
                    {
                        "id": "lifecycle-no-op",
                        "repo": str(canonical),
                        "task": "write-change complete-now: no lifecycle should start.",
                        "expects_no_diff": True,
                    },
                    {
                        "id": "step-no-op",
                        "repo": str(canonical),
                        "skip_verify": True,
                        "steps": [
                            {
                                "id": "certify",
                                "task": "write-change complete-now: certify unchanged.",
                                "expects_no_diff": True,
                            }
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    completed = _just(
        "run-plan",
        str(plan),
        "--no-record",
        "--base",
        str(command_base()),
        "--onejudge-bin",
        onejudge_bin,
        "--workspace",
        str(tmp_path / "zero-yield-worktrees"),
        "--format",
        "json",
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["results"]["certify-unchanged"] == {
        "outcome": "no-changes",
        "completed": True,
        "kind": "agent",
        "status": "done",
        "task": "write-change complete-now: certify the unchanged handoff.",
        "error": None,
    }
    assert payload["results"]["ordinary"]["status"] == "done"
    assert payload["results"]["lifecycle-no-op"]["outcome"] == "no-changes"
    assert payload["results"]["step-no-op"]["outcome"] == "no-changes"
    assert not (no_op_dir / "CHANGE.txt").exists()
    assert (ordinary_dir / "CHANGE.txt").read_text(encoding="utf-8") == "change from fake agent\n"
    assert (
        subprocess.run(
            ["git", "-C", str(origin), "cat-file", "-e", "main:CHANGE.txt"],
            capture_output=True,
        ).returncode
        != 0
    )


def test_expects_no_diff_contract_is_rejected_at_cli_boundary(tmp_path: Path) -> None:
    invalid_plans = (
        (
            {"schema_version": 1, "tasks": [{"id": "x", "task": "x", "expects_no_diff": True}]},
            "requires schema_version 2",
        ),
        (
            {
                "schema_version": 2,
                "tasks": [
                    {
                        "id": "x",
                        "task": "x",
                        "expects_no_diff": True,
                        "persona": "reviewer",
                        "done_when": "provide review findings",
                    }
                ],
            },
            "cannot set 'persona', 'done_when'",
        ),
        (
            {
                "schema_version": 2,
                "tasks": [{"id": "x", "task": "x", "expects_no_diff": "yes"}],
            },
            "must be a boolean",
        ),
        (
            {"schema_version": 99, "tasks": [{"id": "x", "persona": "p", "task": "x"}]},
            "current version 3",
        ),
        (
            {
                "schema_version": 2,
                "tasks": [
                    {
                        "id": "x",
                        "repo": "owner/repo",
                        "task": "x",
                        "expects_no_diff": True,
                        "persona": "engineer",
                    }
                ],
            },
            "cannot set 'persona'",
        ),
        (
            {
                "schema_version": 2,
                "tasks": [
                    {
                        "id": "x",
                        "repo": "owner/repo",
                        "steps": [
                            {
                                "id": "ready",
                                "task": "x",
                                "expects_no_diff": True,
                                "done_when": "provide findings",
                            }
                        ],
                    }
                ],
            },
            "step 'ready' with expects_no_diff cannot set 'done_when'",
        ),
        (
            {
                "schema_version": 2,
                "tasks": [
                    {
                        "id": "x",
                        "repo": "owner/repo",
                        "task": "x",
                        "expects_no_diff": True,
                        "steps": [{"id": "ready", "task": "x", "expects_no_diff": True}],
                    }
                ],
            },
            "cannot also set 'steps'",
        ),
        (
            {
                "schema_version": 1,
                "tasks": [
                    {"id": "x", "persona": "engineer", "task": "x", "expects_no_diff": False}
                ],
            },
            "requires schema_version 2",
        ),
        ({"schema_version": 2, "tasks": "not-a-list"}, "non-empty 'tasks' list"),
        (
            {
                "schema_version": 2,
                "tasks": [
                    {
                        "id": "x",
                        "repo": "owner/repo",
                        "persona": "engineer",
                        "task": "x",
                        "verify_via_ci": True,
                    }
                ],
            },
            "verify_via_ci' requires schema_version 3",
        ),
    )
    for index, (mapping, message) in enumerate(invalid_plans):
        plan = tmp_path / f"invalid-{index}.json"
        plan.write_text(json.dumps(mapping), encoding="utf-8")
        rejected = _just("run-plan", str(plan), "--no-record")
        assert rejected.returncode == 2
        assert message in rejected.stderr


def test_legacy_repo_plan_runs_through_canonical_and_deprecated_alias(
    tmp_path: Path, bare_origin, command_base, onejudge_bin: str
) -> None:
    """Old lifecycle-only plan mappings work through both command names."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "legacy-repo-canonical")
    Registry().register(str(canonical), workflow="local")
    common = (
        "--no-record",
        "--workspace",
        str(tmp_path / "legacy-repo-worktrees"),
        "--base",
        str(command_base()),
        "--onejudge-bin",
        onejudge_bin,
        "--format",
        "json",
    )

    def write_plan(name: str, task: str) -> Path:
        path = tmp_path / f"{name}.json"
        path.write_text(
            json.dumps(
                {
                    "tasks": [
                        {
                            "id": name,
                            "repo": str(canonical),
                            "persona": "engineer",
                            "task": task,
                            "skip_verify": True,
                            "workflow": "local",
                            "repo_type": "single-owner",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return path

    canonical_run = _just(
        "run-plan",
        str(write_plan("legacy-repo", "complete-now write-change: old repo plan")),
        *common,
    )
    assert canonical_run.returncode == 0, canonical_run.stderr
    canonical_payload = json.loads(canonical_run.stdout)
    assert canonical_payload["state"] == "complete"
    assert canonical_payload["results"]["legacy-repo"]["outcome"] == "merged"

    alias_run = _just(
        "repo-plan",
        str(
            write_plan(
                "legacy-alias",
                "complete-now write-unique-change: deprecated repo plan alias",
            )
        ),
        *common,
    )
    assert alias_run.returncode == 0, alias_run.stderr
    assert "deprecated" in alias_run.stderr
    alias_payload = json.loads(alias_run.stdout)
    assert alias_payload["state"] == "complete"
    assert alias_payload["results"]["legacy-alias"]["outcome"] == "merged"
    assert (
        subprocess.run(
            ["git", "-C", str(origin), "cat-file", "-e", "main:CHANGE.txt"],
            capture_output=True,
        ).returncode
        == 0
    )
