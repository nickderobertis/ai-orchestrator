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
from rendezvous import Rendezvous
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


def _records(text: str) -> list[dict]:
    """Every whole journal record in ``text``, tolerating one torn trailing line.

    The journal appends whole newline-terminated records and documents "at worst,
    one torn trailing line", so that is the one thing a poll of a live log may skip
    and the one thing it must not mistake for a record that is not there.
    """
    lines = text.splitlines()
    if lines and not text.endswith("\n"):
        lines.pop()
    return [json.loads(line) for line in lines if line.strip()]


def _started_in(events: Path, round_number: int) -> list[str]:
    """Every node one round dispatched, in journalled order.

    A node the harness never launched leaves no `node-started`, which is the only
    honest way to say a parked node was *not* redispatched: its result payload looks
    the same either way.
    """
    return [
        record["node"]
        for record in _records(events.read_text(encoding="utf-8"))
        if record.get("round") == round_number and record.get("kind") == "node-started"
    ]


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
                            f"slow-branch {tmp_path / 'a.ticks'}"
                            f"{Rendezvous.at(tmp_path, 'a').sentinels(1)}"
                        ),
                    },
                    {
                        "id": "slow_b",
                        "persona": "engineer",
                        "task": (
                            f"slow-branch {tmp_path / 'b.ticks'}"
                            f"{Rendezvous.at(tmp_path, 'b').sentinels(1)}"
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
            "--detach",
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
    Rendezvous.at(tmp_path, "a").wait(LIVE_PROCESS_TIMEOUT)
    Rendezvous.at(tmp_path, "b").wait(LIVE_PROCESS_TIMEOUT)
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
                            f"slow-branch {tmp_path / 'slow.ticks'}"
                            f"{Rendezvous.at(tmp_path, 'slow').sentinels(1)}"
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
            "--detach",
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
    Rendezvous.at(tmp_path, "slow").wait(LIVE_PROCESS_TIMEOUT)
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
    assert not Rendezvous.at(tmp_path, "slow").release.exists()
    Rendezvous.at(tmp_path, "slow").let_go()

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
                            f"slow-branch {tmp_path / 'slow.ticks'}"
                            f"{Rendezvous.at(tmp_path, 'slow').sentinels(1)}"
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
            "--detach",
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
    Rendezvous.at(tmp_path, "slow").wait(LIVE_PROCESS_TIMEOUT)
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

    Rendezvous.at(tmp_path, "slow").let_go()
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
    provider = Rendezvous.at(tmp_path, "lifecycle")
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
                        "task": f"slow-branch {witness} write-change{provider.sentinels(1)}",
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
            "--detach",
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
    provider.wait(LIVE_PROCESS_TIMEOUT)
    _reply(run_id, runs, [{"op": "drop", "id": "lifecycle", "dependents": "drop"}])
    provider.let_go()
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


def test_planner_context_attached_mid_round_reaches_the_next_round_dispatch(
    tmp_path: Path, onejudge_bin: str
) -> None:
    """What the planner learns during a round reaches the node's next dispatch.

    The transition used to restore the plan the round was launched with, so a node
    carried forward was re-briefed with prose that predated everything the round had
    just proven — and the worker set about redoing finished work. Every step here is
    the operator's own: `just orchestrate` launches and owns the channel, the note is
    submitted through `just channel-reply` while the round runs, the orchestrator
    drives the transition after the continuing verdict, and the assertion is on the
    prompt the agent side actually received rather than on the plan alone.
    """
    runs = tmp_path / "runs"
    base = yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8"))
    base["provider"] = {"kind": "command", "command": [sys.executable, str(FAKE_BACKEND)]}
    base_path = tmp_path / "context-base.yaml"
    base_path.write_text(yaml.safe_dump(base), encoding="utf-8")
    prompts = tmp_path / "prompts.jsonl"
    unstarted = tmp_path / "unstarted-prompts.jsonl"
    note = "41 commits are on the branch and the gate is green; only llmlint remains."
    pending_note = "the fixture landed upstream; take it from there rather than rebuilding it."
    plan = tmp_path / "planner-context-plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 6,
                "name": "planner-context",
                "concurrency": 4,
                "tasks": [
                    {
                        "id": "work",
                        "persona": "engineer",
                        "task": (
                            "## What\nSweep the harness debt.\n\n"
                            f"should-fail no-assessment record-task={prompts}"
                        ),
                        "max_turns": 1,
                    },
                    {"id": "settled", "task": "No diff", "expects_no_diff": True},
                    {
                        "id": "hold",
                        "persona": "engineer",
                        "task": (
                            f"slow-branch {tmp_path / 'hold.ticks'}"
                            f"{Rendezvous.at(tmp_path, 'hold').sentinels(1)}"
                        ),
                    },
                    {
                        "id": "later",
                        "persona": "engineer",
                        "task": f"complete-now record-task={unstarted}",
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
            "--detach",
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
    _wait_for(run_dir / "events.jsonl", lambda text: bool(text.strip()), LIVE_PROCESS_TIMEOUT)
    events = run_dir / "events.jsonl"
    # The round has to still be executing when the note is submitted — that is the
    # case the incident was — so one worker is held inside its turn while the node
    # the note is about has already failed and a third node has already settled.
    Rendezvous.at(tmp_path, "hold").wait(LIVE_PROCESS_TIMEOUT)
    _wait_for_event(events, "node-failed", "work", LIVE_PROCESS_TIMEOUT)
    _wait_for_event(events, "node-settled", "settled", LIVE_PROCESS_TIMEOUT)

    # A note aimed at a node that already settled `done` could reach no dispatch, so
    # the planner is told that at submission rather than left believing it landed.
    assert "can still be dispatched" in _rejected(
        run_id, runs, [{"op": "context", "id": "settled", "note": "too late"}]
    )
    _reply(
        run_id,
        runs,
        [
            {"op": "context", "id": "work", "note": note},
            {"op": "context", "id": "later", "note": pending_note},
        ],
    )

    Rendezvous.at(tmp_path, "hold").let_go()
    _wait_for(run_dir / "round-01" / "result.json", lambda text: bool(text.strip()))
    round_one = json.loads((run_dir / "round-01" / "result.json").read_text(encoding="utf-8"))
    assert round_one["results"]["work"]["status"] == "failed"
    # `later` was still blocked behind the held node when its note was committed, so
    # the reconciler installing the edited graph is what put the note in front of it:
    # this round dispatched it with the note, without waiting for a transition.
    assert round_one["results"]["later"]["status"] == "done"
    pending = json.loads(unstarted.read_text(encoding="utf-8").splitlines()[0])
    assert pending_note in pending and "## Planner context" in pending

    # The planner's continuing verdict is what sends the orchestrator into the
    # transition; the carried node runs again there and fails again, by design.
    for _ in range(6):
        boundary = _next_surface(run_id, runs, LIVE_PROCESS_TIMEOUT)
        if boundary.get("status") == "finished":
            break
        surface = boundary.get("surface")
        if surface is None:
            continue
        if surface["kind"] in {"milestone", "closeout"}:
            _continue(run_id, runs)
            break
        _continue(run_id, runs)
    _wait_for(run_dir / "round-02" / "plan.json", lambda text: bool(text.strip()), 120)
    _wait_for(run_dir / "round-02" / "result.json", lambda text: bool(text.strip()), 120)

    carried = json.loads((run_dir / "round-02" / "plan.json").read_text(encoding="utf-8"))
    work = next(task for task in carried["tasks"] if task["id"] == "work")
    assert work["context"] == [note]
    delivered = [json.loads(line) for line in prompts.read_text(encoding="utf-8").splitlines()]
    assert len(delivered) >= 2
    # The opening brief was delivered without the note, and the next round's dispatch
    # received the same brief *plus* it, under the section that says what it is.
    assert note not in delivered[0]
    assert note in delivered[-1]
    assert "## What\nSweep the harness debt." in delivered[-1]
    assert "## Planner context" in delivered[-1]

    subprocess.run(
        ["just", "stop", run_id, "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_edits_committed_during_a_round_are_what_the_next_round_is_derived_from(
    tmp_path: Path, onejudge_bin: str
) -> None:
    """A transition derived from `round-NN/plan.json` discarded every live edit.

    The launch record is never rewritten, so a retry's replacement id, its branch
    pin, and its amended controls all evaporated at the boundary — after
    `channel-reply` had told the planner they were applied. All four are asserted on
    the plan the transition wrote, because they are carried by one mechanism: the
    next round is derived from the node the executed graph holds, whatever keys the
    planner put on it.

    The journey is the operator's own throughout: `just orchestrate` launches, the
    edits go through `just channel-reply` while nodes run, and the assertion is on
    the plan the orchestrator's own transition wrote.
    """
    runs = tmp_path / "runs"
    base = yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8"))
    base["provider"] = {"kind": "command", "command": [sys.executable, str(FAKE_BACKEND)]}
    base_path = tmp_path / "carried-base.yaml"
    base_path.write_text(yaml.safe_dump(base), encoding="utf-8")
    retried_prompts = tmp_path / "retried-prompts.jsonl"
    amended = (
        "## What\nRe-run the sweep against the corrected fixture.\n\n"
        "## Acceptance criteria\nThe corrected fixture is the one under test."
    )
    plan = tmp_path / "carried-edit-plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 6,
                "name": "carried-edits",
                "concurrency": 4,
                "tasks": [
                    # Fails, is retried live, and its replacement fails too — so the
                    # replacement is what the next round has to carry, with the
                    # amended brief the planner attached to it.
                    {"id": "sweep", "persona": "engineer", "task": "should-fail", "max_turns": 1},
                    # Fails, is retried live, and its replacement *succeeds*. Neither
                    # id may appear in the next round: the replacement did the work,
                    # and re-running the original is the incident.
                    {
                        "id": "verdicts",
                        "persona": "engineer",
                        "task": "should-fail",
                        "max_turns": 1,
                    },
                    {
                        "id": "hold",
                        "persona": "engineer",
                        "task": (
                            f"slow-branch {tmp_path / 'hold.ticks'}"
                            f"{Rendezvous.at(tmp_path, 'carried').sentinels(1)}"
                        ),
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
            "--detach",
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
    _wait_for(events, lambda text: bool(text.strip()), LIVE_PROCESS_TIMEOUT)
    # The round has to still be executing when the edits are submitted, which is the
    # case the incident was, so one worker is held inside its turn.
    Rendezvous.at(tmp_path, "carried").wait(LIVE_PROCESS_TIMEOUT)
    _wait_for_event(events, "node-failed", "sweep", LIVE_PROCESS_TIMEOUT)
    _wait_for_event(events, "node-failed", "verdicts", LIVE_PROCESS_TIMEOUT)

    _reply(
        run_id,
        runs,
        [
            {
                "op": "retry",
                "id": "sweep",
                "node": {
                    "id": "sweep-corrected",
                    "persona": "engineer",
                    "task": f"{amended}\n\nshould-fail record-task={retried_prompts}",
                    "done_when": "the corrected fixture is exercised and the gate is green",
                    "max_turns": 2,
                    # The pin is the edit with the worst failure mode: lost at the
                    # boundary, the retry cuts a fresh branch from the base and the
                    # verified work on the preserved one is orphaned. It is carried
                    # here as one more key on the node the transition derives from,
                    # which is why proving it beside the others is the whole test.
                    "branch": "ai-orchestrator/engineer/preserved-sweep",
                },
            },
            {
                "op": "retry",
                "id": "verdicts",
                "node": {
                    "id": "verdicts-corrected",
                    "persona": "engineer",
                    "task": "complete-now verdicts-corrected",
                },
            },
            {
                "op": "add",
                "node": {
                    "id": "followup",
                    "persona": "engineer",
                    "task": "should-fail",
                    "max_turns": 1,
                },
            },
        ],
    )
    _wait_for_event(events, "node-failed", "sweep-corrected", LIVE_PROCESS_TIMEOUT)

    Rendezvous.at(tmp_path, "carried").let_go()
    _wait_for(run_dir / "round-01" / "result.json", lambda text: bool(text.strip()), 120)
    round_one = json.loads((run_dir / "round-01" / "result.json").read_text(encoding="utf-8"))
    assert round_one["results"]["verdicts-corrected"]["status"] == "done"
    assert round_one["results"]["sweep-corrected"]["status"] == "failed"
    # The launch record itself is untouched — that is exactly why reading it back at
    # the boundary lost everything the planner had committed.
    launch_plan = json.loads((run_dir / "round-01" / "plan.json").read_text(encoding="utf-8"))
    assert {task["id"] for task in launch_plan["tasks"]} == {"sweep", "verdicts", "hold"}

    # Every settled node leaves a proposal ahead of the boundary, so the planner
    # answers whatever arrives until the orchestrator's own transition has written
    # the next round. That transition is the subject of this test.
    next_plan = run_dir / "round-02" / "plan.json"
    transition_deadline = deadline(300)
    while time.monotonic() < transition_deadline and not next_plan.is_file():
        boundary = _next_surface(run_id, runs, 10)
        if boundary.get("status") == "finished":
            break
        if boundary.get("surface") is None:
            continue
        # A run that settled between the read and the reply refuses it, and that is a
        # finished run rather than a failure of this journey — round-02's plan below
        # is the evidence either way.
        subprocess.run(
            ["just", "channel-reply", run_id, "--runs-dir", str(runs)],
            cwd=REPO_ROOT,
            input=json.dumps({"completion": False, "message": "continue", "reason": "observed"}),
            text=True,
            capture_output=True,
            check=False,
            timeout=e2e_timeout(30),
        )
    _wait_for(next_plan, lambda text: bool(text.strip()), 120)

    carried = json.loads((run_dir / "round-02" / "plan.json").read_text(encoding="utf-8"))
    by_id = {task["id"]: task for task in carried["tasks"]}
    # The retry's own identity survived, with the brief, the judge bar and the turn
    # budget the planner attached to it.
    assert by_id["sweep-corrected"]["done_when"].startswith("the corrected fixture")
    assert by_id["sweep-corrected"]["max_turns"] == 2
    assert amended in by_id["sweep-corrected"]["task"]
    assert by_id["sweep-corrected"]["branch"] == "ai-orchestrator/engineer/preserved-sweep"
    # The node it replaced is gone rather than carried forward beside it, and the
    # replacement that finished its work is carried *out* rather than redone.
    assert "sweep" not in by_id, sorted(by_id)
    assert "verdicts" not in by_id, sorted(by_id)
    assert "verdicts-corrected" not in by_id, sorted(by_id)
    # An added node is the round's work too, and reaches the next round like any other.
    assert "followup" in by_id, sorted(by_id)

    _wait_for(run_dir / "round-02" / "result.json", lambda text: bool(text.strip()), 180)
    delivered = [
        json.loads(line) for line in retried_prompts.read_text(encoding="utf-8").splitlines()
    ]
    # The next round dispatched the amended brief, not the one the run was launched with.
    assert len(delivered) >= 2, delivered
    assert "Re-run the sweep against the corrected fixture." in delivered[-1]

    subprocess.run(
        ["just", "stop", run_id, "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _drive_to_round_two(run_id: str, runs: Path, next_plan: Path) -> None:
    """Answer surfaces until the orchestrator's own transition wrote the next round."""
    transition_deadline = deadline(300)
    while time.monotonic() < transition_deadline and not next_plan.is_file():
        boundary = _next_surface(run_id, runs, 10)
        if boundary.get("status") == "finished":
            break
        if boundary.get("surface") is None:
            continue
        subprocess.run(
            ["just", "channel-reply", run_id, "--runs-dir", str(runs)],
            cwd=REPO_ROOT,
            input=json.dumps({"completion": False, "message": "continue", "reason": "observed"}),
            text=True,
            capture_output=True,
            check=False,
            timeout=e2e_timeout(30),
        )
    _wait_for(next_plan, lambda text: bool(text.strip()), 120)


def _read_only_view(command: list[str]) -> str:
    """Run one read-only planner view CLI and return what it printed."""
    completed = subprocess.run(
        command, cwd=REPO_ROOT, text=True, capture_output=True, timeout=e2e_timeout(120)
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


# Parks and requeues against the live process registry other workers mutate.
@pytest.mark.single_threaded
def test_real_cli_cancel_parks_a_running_lifecycle_and_a_later_round_requeues_it(
    tmp_path: Path, onejudge_bin: str, bare_origin
) -> None:
    """The arc `drop` and `retry` could not express: stop for now, resume later.

    `drop` refuses to remove the last unresolved publication anchor and `retry`
    demands an immediate successor, so a planner with a live node it merely wanted
    idle had no edit to send — which is how a duplicate recovery path came to run
    beside it. `cancel` parks the node instead: the dispatch stops cooperatively and
    its branch is preserved exactly as a drop preserves one, the anchor stays in the
    graph, and the node is carried across the round boundary without being
    relaunched until a `requeue` puts it back on the frontier.
    """
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-park")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")
    runs = tmp_path / "park-runs"
    base = yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8"))
    base["provider"] = {"kind": "command", "command": [sys.executable, str(FAKE_BACKEND)]}
    base_path = tmp_path / "park-base.yaml"
    base_path.write_text(yaml.safe_dump(base), encoding="utf-8")
    provider = Rendezvous.at(tmp_path, "park")
    # The note is prose to the harness and a rendezvous to the deterministic backend,
    # which parses the whole task it receives. That is what holds round *two* open:
    # a release path is one-shot, so the carried node's second dispatch needs a
    # rendezvous the first one never named, and planner context is the one thing
    # that reaches a carried node's task without changing what the round did.
    second_round = Rendezvous.at(tmp_path, "second-round")
    plan = tmp_path / "park-plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 7,
                "name": "park-and-resume",
                "tasks": [
                    {
                        "id": "lifecycle",
                        "repo": str(canonical),
                        "execution_checkout": str(canonical),
                        "persona": "engineer",
                        # Held on its *second* turn, after the first has written and
                        # pushed the change: a cancel before that has nothing to
                        # preserve, and the preserved branch is the subject here.
                        "task": (
                            f"slow-branch {tmp_path / 'park.ticks'} write-change"
                            f"{provider.sentinels(1)}"
                        ),
                        # Deliberately unpinned: the branch a `requeue` resumes has to
                        # be the one the park preserved and the transition carried,
                        # not one the plan named up front.
                        "title": "feat: write the change this journey parks and resumes",
                        "verify_cmd": ["test", "-f", "CHANGE.txt"],
                    },
                    {
                        "id": "stacked",
                        "repo": str(canonical),
                        "task": "No diff",
                        "expects_no_diff": True,
                        "deps": ["lifecycle"],
                    },
                    {
                        "id": "carried",
                        "persona": "engineer",
                        "task": "should-fail no-assessment",
                        "max_turns": 1,
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
            "--detach",
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
    _wait_for(run_dir / "events.jsonl", lambda text: bool(text.strip()), LIVE_PROCESS_TIMEOUT)
    events = run_dir / "events.jsonl"
    provider.wait(LIVE_PROCESS_TIMEOUT)
    _wait_for_event(events, "node-failed", "carried", LIVE_PROCESS_TIMEOUT)

    # Unchanged: the node is the only publication anchor its stacked dependent has,
    # so `drop` still refuses it — which is exactly the corner `cancel` exists for.
    assert "last unresolved publication anchor" in _rejected(
        run_id, runs, [{"op": "drop", "id": "lifecycle", "dependents": "detach"}]
    )
    assert "pending or running" in _rejected(run_id, runs, [{"op": "cancel", "id": "carried"}])
    assert "existing node id" in _rejected(run_id, runs, [{"op": "cancel", "id": "ghost"}])
    assert "requires a parked node" in _rejected(
        run_id, runs, [{"op": "requeue", "id": "lifecycle"}]
    )

    # Both while the lifecycle is still held: once it settles, this round has nothing
    # running and closes, and a note submitted after that has no live round to reach.
    _reply(
        run_id,
        runs,
        [
            {"op": "cancel", "id": "lifecycle"},
            {
                "op": "context",
                "id": "carried",
                "note": f"hold the next attempt here:{second_round.sentinels(0)}",
            },
        ],
    )
    provider.let_go()
    _wait_for(
        events,
        lambda text: '"kind": "node-settled"' in text and '"status": "parked"' in text,
        timeout=LIVE_PROCESS_TIMEOUT,
    )

    _wait_for(run_dir / "round-01" / "result.json", lambda text: bool(text.strip()), 120)
    round_one = json.loads((run_dir / "round-01" / "result.json").read_text(encoding="utf-8"))
    parked_result = round_one["results"]["lifecycle"]
    assert parked_result["status"] == "parked"
    branch = parked_result["branch"]
    checkpoint = parked_result["resume"]["checkpoint"]
    # Held, not lost: the stacked dependent is blocked rather than skipped, which is
    # what a `cancelled` node would have made it. (This round's own state is `failed`
    # because `carried` failed; a park alone leaves a round `waiting`, which the
    # pending-park journey below asserts.)
    assert round_one["results"]["stacked"]["status"] == "blocked"
    # The branch the cancelled dispatch preserved is real, survives in the registered
    # checkout, and carries the incomplete provenance a resume has to clear.
    assert gitops.is_ancestor(canonical, checkpoint, branch)
    assert incomplete_commits(canonical, "origin/main", branch)
    projection = project_run(events, RunId(run_dir.name), 1)
    assert projection.node_states["lifecycle"] == "parked"
    parked_task = next(node for node in projection.plan["tasks"] if node["id"] == "lifecycle")
    assert parked_task["parked"] is True

    # The two read-only views a planner reaches for after a park, driven as real CLIs
    # against this recorded round. Both render the park from the same result, and both
    # would silently omit it if `parked` fell out of the status vocabulary they count
    # over — the branch a `requeue` resumes is the one thing a parked row must name.
    results_view = _read_only_view(["just", "results", run_id, "--runs-dir", str(runs)])
    assert "lifecycle  parked" in results_view, results_view
    assert f"Preserved branch: {branch}" in results_view, results_view
    listed = _read_only_view(["just", "runs", "--runs-dir", str(runs)])
    assert "1 parked" in listed, listed

    # The transition folds the executed graph, so the park reaches the next round.
    next_plan = run_dir / "round-02" / "plan.json"
    _drive_to_round_two(run_id, runs, next_plan)
    carried_plan = json.loads(next_plan.read_text(encoding="utf-8"))
    carried_by_id = {task["id"]: task for task in carried_plan["tasks"]}
    # Still parked, and still on the branch its cancelled dispatch preserved: the
    # checkpoint is what a requeue has to adopt rather than cut a fresh branch from.
    assert carried_by_id["lifecycle"]["parked"] is True
    assert carried_by_id["lifecycle"]["resume"]["checkpoint"] == checkpoint
    assert carried_by_id["lifecycle"]["resume"]["branch"] == branch

    # Round two is executing — the carried node is held at the rendezvous the note
    # delivered — and the parked node has still not been dispatched a second time.
    second_round.wait(LIVE_PROCESS_TIMEOUT)
    assert _started_in(events, 2) == ["carried"]

    # `requeue` puts it back on the frontier, amended, and a normal dispatch takes it
    # from there — adopting the carried checkpoint rather than starting over.
    _reply(run_id, runs, [{"op": "requeue", "id": "lifecycle", "amend": {"max_turns": 8}}])
    # Released only once the requeued node is in flight: the round stays open on it
    # from here, and letting the held node go any earlier could settle the round
    # between the reconciler's commit and its next scheduling pass.
    _wait_for(events, lambda _text: "lifecycle" in _started_in(events, 2), LIVE_PROCESS_TIMEOUT)
    second_round.let_go()
    _wait_for(run_dir / "round-02" / "result.json", lambda text: bool(text.strip()), 180)
    round_two = json.loads((run_dir / "round-02" / "result.json").read_text(encoding="utf-8"))
    requeued = round_two["results"]["lifecycle"]
    assert requeued["status"] == "done", requeued
    assert requeued["outcome"] == "merged"
    assert requeued["branch"] == branch
    # The preserved work is what got published. A resume that could not adopt the
    # named branch settles `resume-failed`, so `merged` on that pin is the proof, and
    # the change the cancelled dispatch had already made is on the base.
    assert (canonical / "CHANGE.txt").read_text(encoding="utf-8") == "change from fake agent\n"
    # The requeue released the stacked dependent too: it was blocked behind the park
    # rather than skipped, so the anchor finishing is what let it run.
    assert sorted(_started_in(events, 2)) == ["carried", "lifecycle", "stacked"]
    assert round_two["results"]["stacked"]["status"] == "done"
    resumed = [
        record
        for record in _records(events.read_text(encoding="utf-8"))
        if record.get("round") == 2 and record.get("kind") == "branch-discovered"
    ]
    assert [record["detail"]["resumed"] for record in resumed] == [True]

    subprocess.run(
        ["just", "stop", run_id, "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_real_cli_cancel_parks_a_node_before_it_is_ever_dispatched(
    tmp_path: Path, onejudge_bin: str
) -> None:
    """A node parked while pending is never dispatched, and settles `parked` anyway."""
    runs = tmp_path / "runs"
    base = yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8"))
    base["provider"] = {"kind": "command", "command": [sys.executable, str(FAKE_BACKEND)]}
    base_path = tmp_path / "pending-park-base.yaml"
    base_path.write_text(yaml.safe_dump(base), encoding="utf-8")
    prompts = tmp_path / "never-dispatched.jsonl"
    plan = tmp_path / "pending-park-plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 7,
                "name": "pending-park",
                "concurrency": 4,
                "tasks": [
                    {
                        "id": "hold",
                        "persona": "engineer",
                        "task": (
                            f"slow-branch {tmp_path / 'hold.ticks'}"
                            f"{Rendezvous.at(tmp_path, 'pending-park').sentinels(0)}"
                        ),
                    },
                    {"id": "approve", "kind": "human", "task": "Approve the release"},
                    {
                        "id": "unstarted",
                        "persona": "engineer",
                        "task": f"complete-now record-task={prompts}",
                        "deps": ["approve"],
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
            "--detach",
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
    Rendezvous.at(tmp_path, "pending-park").wait(LIVE_PROCESS_TIMEOUT)
    _wait_for(events, lambda text: '"kind": "human-waiting"' in text)

    _reply(run_id, runs, [{"op": "cancel", "id": "unstarted"}])
    # Parked before it was dispatched, so attesting the human action that gated it
    # releases nothing: the node stays held rather than starting.
    _reply(run_id, runs, [{"op": "attest", "ref": "approve"}])
    assert "already parked" in _rejected(run_id, runs, [{"op": "cancel", "id": "unstarted"}])

    Rendezvous.at(tmp_path, "pending-park").let_go()
    _wait_for(run_dir / "round-01" / "result.json", lambda text: bool(text.strip()), 120)
    payload = json.loads((run_dir / "round-01" / "result.json").read_text(encoding="utf-8"))
    assert payload["results"]["unstarted"]["status"] == "parked"
    assert payload["results"]["hold"]["status"] == "done"
    assert payload["state"] == "waiting"
    assert not prompts.exists()
    assert _started_in(events, 1) == ["hold"]

    subprocess.run(
        ["just", "stop", run_id, "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
