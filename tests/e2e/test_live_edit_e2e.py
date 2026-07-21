"""Real-CLI journey for versioned live graph edits and atomic replay."""

# llmlint: ignore-file[live_tier_compiles_and_requires_credential] the onejudge_bin fixture fails
# fast unless the real adopted onejudge CLI is on PATH, and this journey drives it as a real
# subprocess; per the documented suite invariant only the paid model backend is faked via
# onejudge's own command provider (fake_backend.py) — the one external dependency the free gate
# cannot run — so no model credential is required by design.

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import yaml
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator import BASE_CONFIG, REPO_ROOT, gitops
from orchestrator.projection import project_run
from orchestrator.provenance import incomplete_commits
from orchestrator.registry import Registry
from orchestrator.runs import RunId

FAKE_BACKEND = REPO_ROOT / "tests" / "e2e" / "fake_backend.py"


def _wait_for(path: Path, predicate, timeout: float = 30) -> None:
    wait_deadline = deadline(timeout)
    while time.monotonic() < wait_deadline:
        if path.is_file() and predicate(path.read_text(encoding="utf-8")):
            return
        time.sleep(0.02)
    raise AssertionError(f"condition did not appear in {path}")


def _reply(run_id: str, runs: Path, commands: list[dict[str, object]]) -> None:
    subprocess.run(
        ["just", "channel-reply", run_id, "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        input=json.dumps({"version": 1, "commands": commands}),
        text=True,
        capture_output=True,
        check=True,
        timeout=e2e_timeout(30),
    )


def test_real_cli_mutates_live_frontier_and_replays_atomic_edits(
    tmp_path: Path, onejudge_bin: str
) -> None:
    runs = tmp_path / "runs"
    base = yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8"))
    base["provider"] = {"kind": "command", "command": [sys.executable, str(FAKE_BACKEND)]}
    base_path = tmp_path / "base.yaml"
    base_path.write_text(yaml.safe_dump(base), encoding="utf-8")
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "name": "live-edit",
                "concurrency": 4,
                "tasks": [
                    {
                        "id": "slow_a",
                        "persona": "engineer",
                        "task": f"slow-branch {tmp_path / 'a.ticks'} live-edit-slow",
                    },
                    {
                        "id": "slow_b",
                        "persona": "engineer",
                        "task": f"slow-branch {tmp_path / 'b.ticks'} live-edit-slow",
                    },
                    {
                        "id": "failed",
                        "persona": "engineer",
                        "task": "should-fail no-assessment",
                        "max_turns": 1,
                    },
                    {
                        "id": "after_retry",
                        "task": "No diff",
                        "expects_no_diff": True,
                        "deps": ["failed"],
                    },
                    {"id": "approve", "kind": "human", "task": "Approve"},
                    {
                        "id": "pending",
                        "task": "No diff",
                        "expects_no_diff": True,
                        "deps": ["slow_a"],
                    },
                    {
                        "id": "anchor",
                        "repo": "acme/widget",
                        "task": "No diff",
                        "expects_no_diff": True,
                        "deps": ["approve"],
                    },
                    {
                        "id": "stacked",
                        "repo": "acme/widget",
                        "task": "No diff",
                        "expects_no_diff": True,
                        "deps": ["anchor"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    launched = subprocess.run(
        [
            "just",
            "orchestrate",
            str(plan),
            "--runs-dir",
            str(runs),
            "--base",
            str(base_path),
            "--onejudge-bin",
            onejudge_bin,
            "--skill-command",
            sys.executable,
            str(FAKE_BACKEND),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    run_id = str(json.loads(launched.stdout)["run_id"])
    outer_run = runs / run_id
    wait_deadline = deadline(15)
    run_dir: Path | None = None
    while time.monotonic() < wait_deadline:
        if (outer_run / "events.jsonl").is_file():
            run_dir = outer_run
            break
        time.sleep(0.02)
    assert run_dir is not None
    events = run_dir / "events.jsonl"
    _wait_for(
        events,
        lambda text: (
            text.count('"kind": "node-started"') >= 3 and '"kind": "human-waiting"' in text
        ),
    )

    _reply(run_id, runs, [{"op": "reparent", "id": "pending", "deps": ["pending"]}])
    messages: list[str] = []
    for _ in range(4):
        rejected = subprocess.run(
            ["just", "channel-next", run_id, "--runs-dir", str(runs), "--timeout", "10"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        message = json.loads(rejected.stdout)["surface"]["message"]
        messages.append(message)
        if any("depends on itself" in message for message in messages):
            break
        subprocess.run(
            ["just", "channel-reply", run_id, "--runs-dir", str(runs)],
            cwd=REPO_ROOT,
            input=json.dumps(
                {"completion": False, "message": "continue", "reason": "proposal observed"}
            ),
            text=True,
            capture_output=True,
            check=True,
        )
    assert any("depends on itself" in message for message in messages)
    for command, diagnostic in (
        ({"op": "add", "node": "malformed"}, "node mapping"),
        ({"op": "drop", "id": "pending"}, "define dependents"),
        (
            {"op": "drop", "id": "anchor", "dependents": "detach"},
            "last unresolved publication anchor",
        ),
        ({"op": "retry", "id": "approve", "node": "bad"}, "running, failed, or cancelled"),
    ):
        _reply(run_id, runs, [command])
        rejected = subprocess.run(
            ["just", "channel-next", run_id, "--runs-dir", str(runs), "--timeout", "10"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        assert diagnostic in json.loads(rejected.stdout)["surface"]["message"]
    before = events.read_text(encoding="utf-8").count('"kind": "edit-committed"')
    _wait_for(events, lambda text: '"kind": "node-failed"' in text and '"node": "failed"' in text)

    _reply(
        run_id,
        runs,
        [
            {
                "op": "add",
                "node": {
                    "id": "added",
                    "task": "No diff",
                    "expects_no_diff": True,
                    "deps": ["slow_a"],
                },
            },
            {"op": "reparent", "id": "pending", "deps": ["slow_b"]},
            {"op": "drop", "id": "slow_b", "dependents": "detach"},
            {"op": "attest", "ref": "approve"},
            {
                "op": "retry",
                "id": "slow_a",
                "node": {"id": "slow_a_retry", "task": "No diff", "expects_no_diff": True},
            },
            {
                "op": "retry",
                "id": "failed",
                "node": {"id": "retry", "task": "No diff", "expects_no_diff": True},
            },
            {"op": "complete", "reason": "planner verified publication anchors"},
        ],
    )
    _wait_for(events, lambda text: text.count('"kind": "edit-committed"') >= before + 7)

    result_path = run_dir / "round-01" / "result.json"
    _wait_for(result_path, lambda text: bool(text.strip()), timeout=25)
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["state"] == "failed"
    assert payload["results"]["added"]["status"] == "done"
    assert payload["results"]["pending"]["status"] == "done"
    assert payload["results"]["retry"]["status"] == "done"
    assert payload["results"]["after_retry"]["status"] == "done"
    assert payload["results"]["slow_a_retry"]["status"] == "done"
    assert payload["results"]["slow_a"]["status"] == "cancelled"
    # Retry schedules a fresh replacement without mutating the settled node in
    # place: the failed original stays on the frontier as its own failed result.
    assert payload["results"]["failed"]["status"] == "failed"
    assert "slow_b" not in payload["results"]
    projection = project_run(events, RunId(run_dir.name), 1)
    assert {node["id"] for node in projection.plan["tasks"]} == set(payload["results"])
    # Atomic replay preserves the same retry lineage: the original failed node
    # survives the retry-requested no-op while the replacement folds to done.
    assert projection.node_states["failed"] == "failed"
    assert projection.node_states["retry"] == "done"
    after_retry = next(node for node in projection.plan["tasks"] if node["id"] == "after_retry")
    assert after_retry["deps"] == ["retry"]
    committed = [
        line for line in events.read_text().splitlines() if '"kind": "edit-committed"' in line
    ]
    reparent = next(json.loads(line) for line in committed if '"reparent"' in line)
    assert [operation["kind"] for operation in reparent["detail"]["operations"]] == [
        "edge-removed",
        "edge-added",
        "reparent",
    ]
    running_retry = next(
        json.loads(line)
        for line in committed
        if '"retry-requested"' in line and '"node": "slow_a"' in line
    )
    assert running_retry["detail"]["operations"][-2:] == [
        {"kind": "edge-removed", "detail": {"from": "slow_a", "to": "added"}},
        {"kind": "edge-added", "detail": {"from": "slow_a_retry", "to": "added"}},
    ]
    retry = next(
        json.loads(line)
        for line in committed
        if '"retry-requested"' in line and '"node": "failed"' in line
    )
    assert retry["detail"]["operations"][0] == {
        "kind": "retry-requested",
        "node": "failed",
        "detail": {"replacement": "retry", "reset": ["after_retry"]},
    }
    assert retry["detail"]["operations"][-2:] == [
        {"kind": "edge-removed", "detail": {"from": "failed", "to": "after_retry"}},
        {"kind": "edge-added", "detail": {"from": "retry", "to": "after_retry"}},
    ]
    # The planner's completion command is committed through the reconciler as a
    # completion-requested operation rather than being silently dropped.
    assert any('"completion-requested"' in line for line in committed)
    boundary = subprocess.run(
        ["just", "channel-next", run_id, "--runs-dir", str(runs), "--timeout", "10"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    boundary_payload = json.loads(boundary.stdout)
    if boundary_payload.get("status") != "finished":
        assert boundary_payload["surface"]["kind"] in {"milestone", "closeout"}
        subprocess.run(
            ["just", "channel-reply", run_id, "--runs-dir", str(runs)],
            cwd=REPO_ROOT,
            input=json.dumps({"completion": True, "reason": "verified"}),
            text=True,
            capture_output=True,
            check=True,
        )
    _wait_for(outer_run / "orchestrator" / "report.json", lambda text: bool(text.strip()))


def test_real_cli_live_drop_preserves_and_recovers_running_lifecycle(
    tmp_path: Path, onejudge_bin: str, bare_origin
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-live-cancel")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")
    runs = tmp_path / "lifecycle-runs"
    base = yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8"))
    base["provider"] = {"kind": "command", "command": [sys.executable, str(FAKE_BACKEND)]}
    base_path = tmp_path / "lifecycle-base.yaml"
    base_path.write_text(yaml.safe_dump(base), encoding="utf-8")
    witness = tmp_path / "lifecycle.ticks"
    branch = "feature/live-cancel"
    plan = tmp_path / "lifecycle-plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "name": "live-edit",
                "tasks": [
                    {
                        "id": "lifecycle",
                        "repo": str(canonical),
                        "execution_checkout": str(canonical),
                        "persona": "engineer",
                        "task": f"slow-branch {witness} live-edit-slow write-change",
                        "branch": branch,
                        "verify_cmd": ["test", "-f", "CHANGE.txt"],
                    },
                    {"id": "keep", "kind": "human", "task": "Keep cancellation observable"},
                ],
            }
        ),
        encoding="utf-8",
    )
    launched = subprocess.run(
        [
            "just",
            "orchestrate",
            str(plan),
            "--runs-dir",
            str(runs),
            "--base",
            str(base_path),
            "--onejudge-bin",
            onejudge_bin,
            "--skill-command",
            sys.executable,
            str(FAKE_BACKEND),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    run_id = str(json.loads(launched.stdout)["run_id"])
    outer_run = runs / run_id
    nested: Path | None = None
    wait_deadline = deadline(15)
    while time.monotonic() < wait_deadline:
        if (outer_run / "events.jsonl").is_file():
            nested = outer_run
            break
        time.sleep(0.02)
    assert nested is not None
    events = nested / "events.jsonl"
    _wait_for(
        witness,
        lambda text: text.count("tick") >= 3,
        timeout=e2e_timeout(20),
    )
    _wait_for(events, lambda text: '"kind": "node-started"' in text)
    _reply(run_id, runs, [{"op": "drop", "id": "lifecycle", "dependents": "drop"}])
    _wait_for(
        events,
        lambda text: '"kind": "node-settled"' in text and '"status": "cancelled"' in text,
        timeout=e2e_timeout(20),
    )

    checkpoint = gitops.ref_sha(canonical, branch)
    assert incomplete_commits(canonical, "origin/main", branch)
    recovered = subprocess.run(
        [
            "just",
            "repo-recover",
            branch,
            "--repo",
            str(canonical),
            "--workspace",
            str(tmp_path / "recovery-worktrees"),
            "--gate",
            "test -f CHANGE.txt",
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    recovery = json.loads(recovered.stdout)
    assert recovery["outcome"] == "merged"
    assert not gitops.is_ancestor(canonical, checkpoint, "origin/main")
    assert (canonical / "CHANGE.txt").read_text(encoding="utf-8") == "change from fake agent\n"

    boundary = subprocess.run(
        ["just", "channel-next", run_id, "--runs-dir", str(runs), "--timeout", "10"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(boundary.stdout)["surface"]["kind"] in {"milestone", "closeout"}
    subprocess.run(
        ["just", "channel-reply", run_id, "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        input=json.dumps({"completion": True, "reason": "cancelled branch recovered"}),
        text=True,
        capture_output=True,
        check=True,
    )
    _wait_for(outer_run / "orchestrator" / "report.json", lambda text: bool(text.strip()))
