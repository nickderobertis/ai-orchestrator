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
LIVE_PROCESS_TIMEOUT = 60


def _wait_for(path: Path, predicate, timeout: float = 30) -> None:
    wait_deadline = deadline(timeout)
    while time.monotonic() < wait_deadline:
        if path.is_file() and predicate(path.read_text(encoding="utf-8")):
            return
        time.sleep(0.02)
    raise AssertionError(f"condition did not appear in {path}")


def _wait_for_event(events: Path, kind: str, node: str, timeout: float = 30) -> None:
    """Wait until one journaled event carries both ``kind`` and ``node``.

    A substring search over the whole log cannot say this: `"node": "failed"` is
    already on that node's `node-started` line, so pairing it with a bare
    `"kind": "node-failed"` search is satisfied by *any* node's failure. That
    matters because the batch below retries `failed`, and `retry` on a node still
    running cancels it — the node then settles `cancelled`, contradicting the
    lineage this journey asserts. Which of the two the run reaches first is a race
    the box's load decides, so read one event at a time and match within it.

    Only a trailing fragment is dropped, and only while it is unterminated: the
    journal appends whole newline-terminated records and documents "at worst, one
    torn trailing line", so that is the one thing a poll of a live log must expect
    and the one thing it may skip. Every durable record still has to parse.
    """

    def satisfied(text: str) -> bool:
        lines = text.splitlines()
        if lines and not text.endswith("\n"):
            lines.pop()
        return any(
            record.get("kind") == kind and record.get("node") == node
            for record in (json.loads(line) for line in lines if line.strip())
        )

    _wait_for(events, satisfied, timeout)


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


def _rejected(run_id: str, runs: Path, commands: list[dict[str, object]]) -> str:
    """Submit commands expected to be refused, returning the stated reason."""
    attempt = subprocess.run(
        ["just", "channel-reply", run_id, "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        input=json.dumps({"version": 1, "commands": commands}),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
    )
    assert attempt.returncode != 0, attempt.stdout
    return attempt.stderr


def _next_surface(run_id: str, runs: Path, timeout: float = 10) -> dict:
    surfaced = subprocess.run(
        [
            "just",
            "channel-next",
            run_id,
            "--runs-dir",
            str(runs),
            "--timeout",
            str(e2e_timeout(timeout)),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(surfaced.stdout)


def _continue(run_id: str, runs: Path) -> None:
    subprocess.run(
        ["just", "channel-reply", run_id, "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        input=json.dumps({"completion": False, "message": "continue", "reason": "observed"}),
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
                "schema_version": 5,
                "name": "live-edit",
                "concurrency": 4,
                "tasks": [
                    {
                        "id": "slow_a",
                        "persona": "engineer",
                        "task": (
                            f"slow-branch {tmp_path / 'a.ticks'} live-edit-slow "
                            f"live-edit-ready={tmp_path / 'a.ready'} "
                            f"live-edit-release={tmp_path / 'a.release'}"
                        ),
                    },
                    {
                        "id": "slow_b",
                        "persona": "engineer",
                        "task": (
                            f"slow-branch {tmp_path / 'b.ticks'} live-edit-slow "
                            f"live-edit-ready={tmp_path / 'b.ready'} "
                            f"live-edit-release={tmp_path / 'b.release'}"
                        ),
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
    # Parked, not merely started: `node-started` is journaled before the dispatch
    # launches, so it does not say the slow nodes are somewhere a retry or a drop
    # can still cancel them. Their ready files do — the backend writes one from
    # inside the turn it then holds until a release this test never writes, so from
    # here they provably cannot settle first however starved the box is.
    _wait_for(tmp_path / "a.ready", lambda text: text == "ready\n", LIVE_PROCESS_TIMEOUT)
    _wait_for(tmp_path / "b.ready", lambda text: text == "ready\n", LIVE_PROCESS_TIMEOUT)
    _wait_for_event(events, "human-waiting", "approve")

    # A command that cannot be applied is refused at submission with the reason,
    # never accepted into the channel and silently dropped.
    before = events.read_text(encoding="utf-8").count('"kind": "edit-committed"')
    for command, diagnostic in (
        ({"op": "reparent", "id": "pending", "deps": ["pending"]}, "depends on itself"),
        ({"op": "add", "node": "malformed"}, "node mapping"),
        ({"op": "drop", "id": "pending"}, "define dependents"),
        (
            {"op": "drop", "id": "anchor", "dependents": "detach"},
            "last unresolved publication anchor",
        ),
        ({"op": "retry", "id": "approve", "node": "bad"}, "running, failed, or cancelled"),
    ):
        assert diagnostic in _rejected(run_id, runs, [command])
    assert events.read_text(encoding="utf-8").count('"kind": "edit-committed"') == before
    _wait_for_event(events, "node-failed", "failed")

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
            {
                "op": "add",
                "node": {
                    "id": "external_added",
                    "task": "No diff",
                    "expects_no_diff": True,
                    "deps": ["run:missing-upstream#publish"],
                },
            },
            {
                "op": "reparent",
                "id": "pending",
                "deps": ["run:missing-upstream#publish"],
            },
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
    _wait_for(events, lambda text: text.count('"kind": "edit-committed"') >= before + 8)

    result_path = run_dir / "round-01" / "result.json"
    _wait_for(result_path, lambda text: bool(text.strip()))
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["state"] == "failed"
    assert payload["results"]["added"]["status"] == "done"
    assert payload["results"]["external_added"]["status"] == "blocked"
    assert payload["results"]["pending"]["status"] == "blocked"
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
    # Replaying the event log alone reconstructs the graph that ran: an added node
    # carries the dependencies it was submitted with, and a drop carries the
    # disposition it was submitted with.
    added_node = next(node for node in projection.plan["tasks"] if node["id"] == "added")
    assert added_node["deps"] == ["slow_a_retry"]  # the retry redirected the edge
    external_node = next(
        node for node in projection.plan["tasks"] if node["id"] == "external_added"
    )
    assert external_node["deps"] == ["run:missing-upstream#publish"]
    # Atomic replay preserves the same retry lineage: the original failed node
    # survives the retry-requested no-op while the replacement folds to done.
    assert projection.node_states["failed"] == "failed"
    assert projection.node_states["retry"] == "done"
    after_retry = next(node for node in projection.plan["tasks"] if node["id"] == "after_retry")
    assert after_retry["deps"] == ["retry"]
    committed = [
        line for line in events.read_text().splitlines() if '"kind": "edit-committed"' in line
    ]
    submitted = [json.loads(line)["detail"]["command"] for line in committed]
    assert {
        "op": "add",
        "node": {
            "id": "added",
            "task": "No diff",
            "expects_no_diff": True,
            "deps": ["slow_a"],
        },
    } in submitted
    assert {"op": "drop", "id": "slow_b", "dependents": "detach"} in submitted
    assert {"op": "attest", "ref": "approve"} in submitted
    dropped = next(json.loads(line) for line in committed if '"node-dropped"' in line)
    assert [
        operation
        for operation in dropped["detail"]["operations"]
        if operation["kind"] != "edge-removed"
    ] == [{"kind": "node-dropped", "node": "slow_b", "detail": {"dependents": "detach"}}]
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
    for _ in range(6):
        boundary_payload = _next_surface(run_id, runs)
        if boundary_payload.get("status") == "finished":
            break
        surface = boundary_payload.get("surface")
        if surface is None:
            continue
        if surface["kind"] in {"milestone", "closeout"}:
            subprocess.run(
                ["just", "channel-reply", run_id, "--runs-dir", str(runs)],
                cwd=REPO_ROOT,
                input=json.dumps({"completion": True, "reason": "verified"}),
                text=True,
                capture_output=True,
                check=True,
            )
            break
        _continue(run_id, runs)
    _wait_for(outer_run / "orchestrator" / "report.json", lambda text: bool(text.strip()))


def test_real_cli_attest_and_reparent_reach_the_running_graph(
    tmp_path: Path, onejudge_bin: str
) -> None:
    """A human attestation and a reparent both reach the frontier that is running.

    Both edits only change *eligibility*: nothing settles because of them, so
    nothing else wakes the scheduler. Each is proven by the node it releases
    starting and settling while the round is otherwise held open by one slow
    worker, three of the four concurrency slots free the whole time.
    """
    runs = tmp_path / "runs"
    base = yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8"))
    base["provider"] = {"kind": "command", "command": [sys.executable, str(FAKE_BACKEND)]}
    base_path = tmp_path / "base.yaml"
    base_path.write_text(yaml.safe_dump(base), encoding="utf-8")
    plan = tmp_path / "eligibility-plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 5,
                "name": "eligibility",
                "concurrency": 4,
                "tasks": [
                    {
                        "id": "slow",
                        "persona": "engineer",
                        "task": (
                            f"slow-branch {tmp_path / 'slow.ticks'} live-edit-slow "
                            f"live-edit-ready={tmp_path / 'slow.ready'} "
                            f"live-edit-release={tmp_path / 'slow.release'}"
                        ),
                    },
                    {"id": "approve", "kind": "human", "task": "Approve the release"},
                    {
                        "id": "after_approve",
                        "task": "No diff",
                        "expects_no_diff": True,
                        "deps": ["approve"],
                    },
                    {"id": "hold", "kind": "human", "task": "Hold indefinitely"},
                    {
                        "id": "reparented",
                        "task": "No diff",
                        "expects_no_diff": True,
                        "deps": ["hold"],
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
    run_dir = runs / run_id
    events = run_dir / "events.jsonl"
    _wait_for(tmp_path / "slow.ready", lambda text: text == "ready\n", LIVE_PROCESS_TIMEOUT)
    _wait_for(events, lambda text: text.count('"kind": "human-waiting"') >= 2)

    # `after_approve` and `reparented` are both derived-blocked behind a waiting
    # human before either edit is sent.
    _reply(run_id, runs, [{"op": "attest", "ref": "approve"}])
    # A zero exit from `channel-reply` means applied, not merely queued: the
    # reconciler's commit is already in the log when the command returns.
    assert '"kind": "edit-committed"' in events.read_text(encoding="utf-8")
    assert '"ref": "approve"' in events.read_text(encoding="utf-8")
    _wait_for(
        events,
        lambda text: '"node": "after_approve"' in text and '"kind": "node-settled"' in text,
    )
    _reply(run_id, runs, [{"op": "reparent", "id": "reparented", "deps": []}])
    _wait_for(
        events,
        lambda text: '"node": "reparented"' in text and '"kind": "node-settled"' in text,
    )
    # The slow worker is still held: nothing but the edits themselves moved the
    # frontier, and its three free slots were available the whole time.
    assert not (tmp_path / "slow.release").exists()
    (tmp_path / "slow.release").write_text("release\n", encoding="utf-8")

    result_path = run_dir / "round-01" / "result.json"
    _wait_for(result_path, lambda text: bool(text.strip()), LIVE_PROCESS_TIMEOUT)
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["results"]["approve"]["status"] == "done"
    assert payload["results"]["after_approve"]["status"] == "done"
    assert payload["results"]["reparented"]["status"] == "done"
    assert payload["results"]["hold"]["status"] == "waiting"
    projection = project_run(events, RunId(run_dir.name), 1)
    assert projection.attestations == ("approve",)
    assert projection.node_states["after_approve"] == "done"
    assert projection.node_states["reparented"] == "done"
    reparented = next(node for node in projection.plan["tasks"] if node["id"] == "reparented")
    assert reparented.get("deps", []) == []
    for _ in range(6):
        boundary_payload = _next_surface(run_id, runs)
        if boundary_payload.get("status") == "finished":
            break
        surface = boundary_payload.get("surface")
        if surface is None:
            continue
        if surface["kind"] in {"milestone", "closeout"}:
            subprocess.run(
                ["just", "channel-reply", run_id, "--runs-dir", str(runs)],
                cwd=REPO_ROOT,
                input=json.dumps({"completion": True, "reason": "eligibility verified"}),
                text=True,
                capture_output=True,
                check=True,
            )
            break
        _continue(run_id, runs)
    _wait_for(run_dir / "orchestrator" / "report.json", lambda text: bool(text.strip()))


def test_real_cli_rejects_an_inapplicable_command_at_submission(
    tmp_path: Path, onejudge_bin: str
) -> None:
    """An edit that cannot be applied is refused at submission, never swallowed."""
    runs = tmp_path / "runs"
    base = yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8"))
    base["provider"] = {"kind": "command", "command": [sys.executable, str(FAKE_BACKEND)]}
    base_path = tmp_path / "base.yaml"
    base_path.write_text(yaml.safe_dump(base), encoding="utf-8")
    plan = tmp_path / "rejection-plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 5,
                "name": "rejection",
                "concurrency": 2,
                "tasks": [
                    {
                        "id": "slow",
                        "persona": "engineer",
                        "task": (
                            f"slow-branch {tmp_path / 'slow.ticks'} live-edit-slow "
                            f"live-edit-ready={tmp_path / 'slow.ready'} "
                            f"live-edit-release={tmp_path / 'slow.release'}"
                        ),
                    },
                    {"id": "approve", "kind": "human", "task": "Approve the release"},
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
    run_dir = runs / run_id
    events = run_dir / "events.jsonl"
    _wait_for(tmp_path / "slow.ready", lambda text: text == "ready\n", LIVE_PROCESS_TIMEOUT)
    _wait_for(events, lambda text: '"kind": "human-waiting"' in text)

    before = events.read_text(encoding="utf-8")
    reason = _rejected(run_id, runs, [{"op": "attest", "ref": "not-a-node"}])
    assert "attest requires a currently-ready human action" in reason
    unknown = _rejected(run_id, runs, [{"op": "reparent", "id": "ghost", "deps": ["approve"]}])
    assert "reparent requires an existing node id" in unknown
    # Refused at submission means refused everywhere: nothing was queued, so the
    # authoritative log gained no committed or rejected edit from either attempt.
    assert events.read_text(encoding="utf-8") == before
    assert not (run_dir / "channel" / "commands.jsonl").exists()

    (tmp_path / "slow.release").write_text("release\n", encoding="utf-8")
    _wait_for(
        run_dir / "round-01" / "result.json",
        lambda text: bool(text.strip()),
        LIVE_PROCESS_TIMEOUT,
    )
    for _ in range(6):
        boundary_payload = _next_surface(run_id, runs)
        if boundary_payload.get("status") == "finished":
            break
        surface = boundary_payload.get("surface")
        if surface is None:
            continue
        if surface["kind"] in {"milestone", "closeout"}:
            subprocess.run(
                ["just", "channel-reply", run_id, "--runs-dir", str(runs)],
                cwd=REPO_ROOT,
                input=json.dumps({"completion": True, "reason": "rejection verified"}),
                text=True,
                capture_output=True,
                check=True,
            )
            break
        _continue(run_id, runs)
    _wait_for(run_dir / "orchestrator" / "report.json", lambda text: bool(text.strip()))


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
    provider_ready = tmp_path / "lifecycle.ready"
    provider_release = tmp_path / "lifecycle.release"
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
                        "task": (
                            f"slow-branch {witness} live-edit-slow write-change "
                            f"live-edit-ready={provider_ready} "
                            f"live-edit-release={provider_release}"
                        ),
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
    _wait_for(events, lambda text: '"kind": "node-started"' in text)
    _wait_for(provider_ready, lambda text: text == "ready\n", timeout=LIVE_PROCESS_TIMEOUT)
    _reply(run_id, runs, [{"op": "drop", "id": "lifecycle", "dependents": "drop"}])
    provider_release.write_text("release\n", encoding="utf-8")
    _wait_for(
        events,
        lambda text: '"kind": "node-settled"' in text and '"status": "cancelled"' in text,
        timeout=LIVE_PROCESS_TIMEOUT,
    )
    # The recorded disposition is the one submitted, not the other one.
    committed = next(
        json.loads(line)
        for line in events.read_text(encoding="utf-8").splitlines()
        if '"kind": "edit-committed"' in line
    )
    assert committed["detail"]["command"] == {
        "op": "drop",
        "id": "lifecycle",
        "dependents": "drop",
    }
    assert committed["detail"]["operations"] == [
        {"kind": "node-dropped", "node": "lifecycle", "detail": {"dependents": "drop"}}
    ]

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
        [
            "just",
            "channel-next",
            run_id,
            "--runs-dir",
            str(runs),
            "--timeout",
            str(e2e_timeout(10)),
        ],
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
