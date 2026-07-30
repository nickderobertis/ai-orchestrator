"""Serve the real read-only telemetry API over a recorded run fixture.

The browser journeys in ``apps/dag-ui/e2e`` drive the shipped UI against the actual
``orchestrator/server.py`` process, so the app's fetch/SSE paths, the telemetry
client, and the read model are all exercised for real. What this script fabricates
is only what a browser test cannot afford to earn: the recorded run directory an
orchestration would have written, and the paid harness' history store, which is
served through the same ``tests/e2e/fake_oneharness.py`` subprocess the Python e2e
suite uses. The run directory is written with the executor's own public writers
(``prepare_round``, ``open_journal(...).append``, ``write_result``,
``save_snapshot``), so the projection the UI renders is the real one.

Everything lands in a fresh temporary workspace that is discarded when the server
exits, so a browser run never reads or writes the operator's own ``runs/``.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sys
import tempfile
from pathlib import Path
from typing import Any, NamedTuple

from orchestrator import REPO_ROOT

# llmlint: ignore-file[modern_domain_modeling] The plain mappings here are not a domain
# model: they are the exact on-disk JSON a plan file, a journal event detail, and an
# `oneharness history list` record already are. `orchestrator` owns those shapes and
# validates them at the boundaries this fixture feeds; restating them as local types
# would create a second declaration that drifts from the one under test. The one shape
# this file does own — its recorded session list — is a typed `DashboardSession`.

FAKE_ONEHARNESS = REPO_ROOT / "tests" / "e2e" / "fake_oneharness.py"

#: Launch ids are 32 lowercase hex characters; these join each run to its launcher.
CODEX_LAUNCH = "c0de" * 8
CLAUDE_LAUNCH = "c1a0" * 8

LIVE_RUN = "dag-ui-live"
HISTORY_RUN = "dag-ui-history"
#: A second run of the *same* launch as `LIVE_RUN`: one planner session often drives
#: several graphs, and the navigation has to gather them under that one session.
SIBLING_RUN = "dag-ui-sibling"
UNATTRIBUTED_RUN = "dag-ui-unattributed"
#: A run whose round is prepared but which has journalled nothing yet — what every
#: run looks like for its first moments, and what the served `repo-plan*` runs on an
#: operator's machine look like permanently. Its `last_event` is null.
EVENTLESS_RUN = "dag-ui-eventless"

_LIVE_TASKS: list[dict[str, Any]] = [
    {
        "id": "foundation",
        "persona": "engineer",
        "task": "Prepare shared contracts",
        "done_when": "Contract tests pass",
        "repo": "local/example",
    },
    {
        "id": "dashboard",
        "persona": "engineer",
        # The second dependency is a cross-DAG reference: a prerequisite in another
        # run, which the projection accepts and this graph has no node to draw to.
        "deps": ["foundation", f"run:{HISTORY_RUN}#archive"],
        "task": "Build the live dashboard",
        "done_when": "Users can inspect transcripts",
    },
    {
        "id": "publish",
        "persona": "engineer",
        "deps": ["dashboard"],
        "task": "Publish the dashboard",
        "done_when": "The release is reachable",
    },
    {
        "id": "approval",
        "kind": "human",
        "deps": ["publish"],
        "task": "Wait for release approval",
    },
    {
        "id": "queued",
        "persona": "engineer",
        "deps": ["approval"],
        "task": "Start queued follow-up",
        "done_when": "Follow-up starts",
    },
    {
        "id": "obsolete",
        "persona": "engineer",
        "task": "Retire obsolete work",
        "done_when": "Work is cancelled",
    },
]

_HISTORY_TASKS: list[dict[str, Any]] = [
    {
        "id": "archive",
        "persona": "engineer",
        "task": "Archive the release",
        "done_when": "Archive exists",
    }
]

_SIBLING_TASKS: list[dict[str, Any]] = [
    {
        "id": "sibling",
        "persona": "engineer",
        "task": "Run beside the dashboard work",
        "done_when": "The sibling settles",
    }
]

_UNATTRIBUTED_TASKS: list[dict[str, Any]] = [
    {
        "id": "orphan",
        "persona": "engineer",
        "task": "Continue unattributed work",
        "done_when": "The work continues",
    }
]

#: Typed like the task lists above: `Any` here is plan-file JSON whose shape
#: `orchestrator` owns and validates, per this module's `modern_domain_modeling` note.
_EVENTLESS_TASKS: list[dict[str, Any]] = [
    {
        "id": "unstarted",
        "persona": "engineer",
        "task": "Wait for the round to begin",
        "done_when": "The round starts",
    }
]


def _record_launch(run_dir: Path, run_id: str, launch_id: str) -> None:
    """Persist the run->launch join exactly as the executor's launch record does."""
    (run_dir / "launch.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "run_id": run_id,
                "channel_id": run_id,
                "plan_name": run_id,
                "commands": {},
                "launch": {"launch_id": launch_id},
            }
        ),
        encoding="utf-8",
    )


def _write_live_run(runs_dir: Path) -> None:
    """One in-flight run covering every renderable node state."""
    from orchestrator.journal import NodeId, RunId, open_journal
    from orchestrator.runs import prepare_round

    run_dir = runs_dir / LIVE_RUN
    # Schema 5 is the first that admits the cross-DAG dependency this plan declares.
    plan = {"schema_version": 5, "concurrency": 3}
    prepare_round(run_dir, {**plan, "tasks": _LIVE_TASKS})
    journal = open_journal(run_dir, RunId(LIVE_RUN), 1)
    for task in _LIVE_TASKS:
        # A definition's own `deps` are the edges; adding them again would duplicate.
        journal.append("node-added", detail={"definition": task})
    journal.append("round-started", detail={"plan": plan})

    journal.append("node-started", node=NodeId("foundation"), detail={"persona": "engineer"})
    journal.append(
        "verification-finished",
        node=NodeId("foundation"),
        detail={
            "ok": True,
            "command": ["just", "gate"],
            "reused": False,
            "gate_attestation": {
                "commit": "1" * 40,
                "comparison_remote": "origin",
                "comparison_base": "origin/main",
                "comparison_commit": "2" * 40,
                "command": ["just", "gate"],
                "environment_sha256": "3" * 64,
            },
        },
    )
    journal.append(
        "node-settled",
        node=NodeId("foundation"),
        detail={
            "status": "done",
            "result": {
                "status": "done",
                "ok": True,
                "task": "Prepare shared contracts",
                "repo": "local/example",
                "branch": "ai-orchestrator/engineer/foundation",
                "pr": "https://github.com/example/repo/pull/12",
                "detail": "Gate completed successfully",
                "telemetry": {"checks": {"unit": "passed"}},
                "artifacts": {"gate_log": "round-01/foundation/gate.log"},
            },
        },
    )
    journal.append("node-started", node=NodeId("dashboard"), detail={"persona": "engineer"})
    journal.append("node-started", node=NodeId("publish"), detail={"persona": "engineer"})
    journal.append(
        "node-failed",
        node=NodeId("publish"),
        detail={
            "status": "failed",
            "result": {
                "status": "failed",
                "ok": False,
                "error": "Deploy failed",
                "detail": "Deploy failed",
            },
        },
    )
    journal.append(
        "human-waiting",
        node=NodeId("approval"),
        detail={
            "status": "waiting",
            "result": {
                "status": "waiting",
                "kind": "human",
                "task": "Wait for release approval",
                "unblocks": ["queued"],
            },
        },
    )
    journal.append("node-started", node=NodeId("obsolete"), detail={"persona": "engineer"})
    journal.append(
        "node-settled",
        node=NodeId("obsolete"),
        detail={"status": "cancelled", "result": {"status": "cancelled", "ok": False}},
    )
    _record_launch(run_dir, LIVE_RUN, CODEX_LAUNCH)


def _write_history_run(runs_dir: Path) -> None:
    """One settled run, so the navigation has a second launching session to group."""
    from orchestrator.journal import NodeId, RunId, open_journal
    from orchestrator.runs import prepare_round, write_result

    run_dir = runs_dir / HISTORY_RUN
    _, round_dir = prepare_round(run_dir, {"tasks": _HISTORY_TASKS})
    journal = open_journal(run_dir, RunId(HISTORY_RUN), 1)
    journal.append("node-added", detail={"definition": _HISTORY_TASKS[0]})
    journal.append("round-started", detail={"plan": {"schema_version": 3, "concurrency": 1}})
    journal.append("node-started", node=NodeId("archive"), detail={"persona": "engineer"})
    settled = {"status": "done", "ok": True, "task": "Archive the release"}
    journal.append(
        "node-settled", node=NodeId("archive"), detail={"status": "done", "result": settled}
    )
    result = {
        "ok": True,
        "state": "complete",
        "started_order": ["archive"],
        "results": {"archive": settled},
    }
    journal.append("round-finished", detail={"result": result})
    write_result(round_dir, result)
    _record_launch(run_dir, HISTORY_RUN, CLAUDE_LAUNCH)


def _write_sibling_run(runs_dir: Path) -> None:
    """A second run under the live run's launch id, whose executor then stopped.

    Its round is claimed and then abandoned with no result recorded, which is what an
    executor that took a signal leaves behind and what the read model reports as
    ``stopped``. That makes this the one run here whose state falls outside the
    vocabulary the UI gives a meaning to, so the navigation has to render it plainly
    rather than borrow an outcome it does not have. The abandonment is written by the
    same private helper the executor's own teardown uses, so the recorded shape stays
    the one the reader was built against.
    """
    from orchestrator.journal import NodeId, RunId, open_journal
    from orchestrator.runs import _abandon_if_running, prepare_round

    run_dir = runs_dir / SIBLING_RUN
    prepare_round(run_dir, {"tasks": _SIBLING_TASKS})
    journal = open_journal(run_dir, RunId(SIBLING_RUN), 1)
    journal.append("node-added", detail={"definition": _SIBLING_TASKS[0]})
    journal.append("round-started", detail={"plan": {"schema_version": 3, "concurrency": 1}})
    journal.append("node-started", node=NodeId("sibling"), detail={"persona": "engineer"})
    _abandon_if_running(run_dir / "round-01", "owner took SIGTERM")
    _record_launch(run_dir, SIBLING_RUN, CODEX_LAUNCH)


def _write_unattributed_run(runs_dir: Path) -> None:
    """One run with no launch record and no recorded transcripts.

    Runs predating launch provenance, and runs whose history store has been swept,
    both read this way: the read API serves them with no launch join at all, and the
    navigation has to group them under an unknown session rather than hide them.
    """
    from orchestrator.journal import NodeId, RunId, open_journal
    from orchestrator.runs import prepare_round

    run_dir = runs_dir / UNATTRIBUTED_RUN
    prepare_round(run_dir, {"tasks": _UNATTRIBUTED_TASKS})
    journal = open_journal(run_dir, RunId(UNATTRIBUTED_RUN), 1)
    journal.append("node-added", detail={"definition": _UNATTRIBUTED_TASKS[0]})
    journal.append("round-started", detail={"plan": {"schema_version": 3, "concurrency": 1}})
    journal.append("node-started", node=NodeId("orphan"), detail={"persona": "engineer"})


def _write_eventless_run(runs_dir: Path) -> None:
    """One run whose round is prepared and whose journal is still empty.

    The read API serves it with a null ``last_event`` and no rounds at all. It has to
    stay in the navigation beside the runs that do have events: the client validates
    the run list in one parse, so a run this shape either renders with the rest or
    takes every one of them down with it.
    """
    from orchestrator.runs import prepare_round

    prepare_round(runs_dir / EVENTLESS_RUN, {"tasks": _EVENTLESS_TASKS})


def _session(
    workspace: Path,
    *,
    session_id: str,
    name: str,
    run_id: str,
    node: str | None,
    role: str,
    agent_role: str,
    launcher: str,
    launch_id: str,
    prompt: str,
    text: str,
    started: str,
) -> dict[str, Any]:
    """One recorded harness session plus the JSONL record `oneharness history` serves."""
    record = workspace / f"{session_id}.jsonl"
    record.write_text(
        json.dumps(
            {
                "session": session_id,
                "name": name,
                "harness": "codex",
                "model": "gpt-5",
                "timestamp": started,
                "prompt": prompt,
                "text": text,
                "status": "ok",
                "session_id": session_id,
                "usage": {"input_tokens": 1200, "output_tokens": 340},
                "events": [
                    {
                        "kind": "tool_call",
                        "name": "command_execution",
                        "input": {"command": "just gate"},
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    labels = {
        "run_id": run_id,
        "role": role,
        "agent_role": agent_role,
        "persona": "engineer",
        "launcher": launcher,
        "launch_id": launch_id,
        "round": "1",
    }
    # The orchestrator's own session acts on the whole graph, so it carries no node.
    if node is not None:
        labels["node"] = node
    return {
        "id": session_id,
        "name": name,
        "project": str(workspace),
        "started": started,
        "path": str(record),
        "labels": labels,
    }


class DashboardSession(NamedTuple):
    """One recorded transcript of the live run's dashboard node."""

    session_id: str
    name: str
    transport_role: str
    agent_role: str
    text: str


#: One session per attributed role the detail view labels separately.
_DASHBOARD_SESSIONS = (
    DashboardSession(
        "worker-session",
        "engineer-dashboard",
        "agent",
        "worker",
        "Implementing the dashboard now",
    ),
    DashboardSession(
        "judge-session",
        "you-are-a-strict-careful-evaluator",
        "judge",
        "judge",
        "The transcript is accessible",
    ),
    DashboardSession(
        "check-in-session",
        "check-in-dashboard",
        "agent",
        "check-in",
        "Progress update sent",
    ),
    DashboardSession(
        "pr-author-session",
        "pr-author-dashboard",
        "agent",
        "pr-author",
        "Drafted the pull request",
    ),
    DashboardSession(
        "llmlint-session",
        "llmlint-dashboard",
        "llmlint",
        "worker",
        "Reviewed the changed behavior",
    ),
)


def _history_store(workspace: Path) -> Path:
    """A recorded oneharness store covering every attributed role of both runs."""
    sessions = [
        _session(
            workspace,
            session_id=recorded.session_id,
            name=recorded.name,
            run_id=LIVE_RUN,
            node="dashboard",
            role=recorded.transport_role,
            agent_role=recorded.agent_role,
            launcher="codex",
            launch_id=CODEX_LAUNCH,
            prompt=f"Act as {recorded.agent_role}",
            text=recorded.text,
            started=f"2026-07-26T11:0{index}:00Z",
        )
        for index, recorded in enumerate(_DASHBOARD_SESSIONS)
    ]
    sessions.append(
        _session(
            workspace,
            session_id="orchestrator-session",
            name=f"orchestrator-{LIVE_RUN}",
            run_id=LIVE_RUN,
            node=None,
            role="agent",
            agent_role="orchestrator",
            launcher="codex",
            launch_id=CODEX_LAUNCH,
            prompt="Drive the graph",
            text="Coordinating the execution frontier",
            started="2026-07-26T10:00:00Z",
        )
    )
    sessions.append(
        _session(
            workspace,
            session_id="sibling-session",
            name="engineer-sibling",
            run_id=SIBLING_RUN,
            node="sibling",
            role="agent",
            agent_role="worker",
            # Same launch id as the live run's sessions: one planner session, two runs.
            launcher="codex",
            launch_id=CODEX_LAUNCH,
            prompt="Act as worker",
            text="Working beside the dashboard run",
            started="2026-07-26T09:30:00Z",
        )
    )
    sessions.append(
        _session(
            workspace,
            session_id="archive-session",
            name="engineer-archive",
            run_id=HISTORY_RUN,
            node="archive",
            role="agent",
            agent_role="worker",
            launcher="claude-code",
            launch_id=CLAUDE_LAUNCH,
            prompt="Act as worker",
            text="Archived the release",
            started="2026-07-25T09:00:00Z",
        )
    )
    store = workspace / "history-store.json"
    store.write_text(json.dumps({"sessions": sessions}), encoding="utf-8")
    return store


def _oneharness_bin(workspace: Path) -> Path:
    binary = workspace / "oneharness"
    binary.write_text(
        "#!/usr/bin/env python3\n" + FAKE_ONEHARNESS.read_text(encoding="utf-8"), encoding="utf-8"
    )
    binary.chmod(0o755)
    return binary


def build_fixture(workspace: Path) -> tuple[Path, Path]:
    """Write the runs root and history store; return ``(runs_dir, oneharness_bin)``."""
    from orchestrator.launch import write_provenance

    runs_dir = workspace / "runs"
    runs_dir.mkdir(parents=True)
    # Written oldest first: the list view orders by most recent progress, so the live
    # run ends up at the top and is what an operator sees on arrival.
    _write_eventless_run(runs_dir)
    _write_unattributed_run(runs_dir)
    _write_history_run(runs_dir)
    _write_sibling_run(runs_dir)
    _write_live_run(runs_dir)
    os.environ["FAKE_ONEHARNESS_STORE"] = str(_history_store(workspace))
    for launch_id, launcher in ((CODEX_LAUNCH, "codex"), (CLAUDE_LAUNCH, "claude-code")):
        write_provenance(
            launch_id=launch_id,
            launcher=launcher,
            launcher_session_id=f"{launcher}-top-session",
            repository_identity="local/ai-orchestrator",
        )
    return runs_dir, _oneharness_bin(workspace)


def settle_dashboard(workspace: Path) -> int:
    """Record real progress on the served live run, so the stream invalidates it.

    A browser journey calls this to change the state the server projects, exactly as
    a running executor would: one appended authoritative event, no reaching into the
    server or the client.
    """
    from orchestrator.journal import NodeId, RunId, open_journal

    journal = open_journal(workspace / "runs" / LIVE_RUN, RunId(LIVE_RUN), 1)
    journal.append(
        "node-settled",
        node=NodeId("dashboard"),
        detail={
            "status": "done",
            "result": {"status": "done", "ok": True, "detail": "Dashboard shipped"},
        },
    )
    return 0


def remove_run(workspace: Path, run_id: str) -> int:
    """Take one recorded run out of the served root, as a sweep or an operator does.

    Runs are directories, and a removed run is a removed directory; the server
    notices on its next poll and invalidates it. Nothing else about a run's storage
    is public, so this stays beside the code that wrote it.

    The identifier reaches a recursive delete, so it is validated exactly as the read
    API validates one and the resolved target must still sit beneath the runs root —
    a command line is an untrusted boundary even in a fixture.
    """
    from orchestrator.config import ConfigError
    from orchestrator.runs import validate_run_id

    runs_dir = workspace / "runs"
    try:
        validated = validate_run_id(run_id)
    except ConfigError as exc:
        print(f"serve-fixture: {exc}", file=sys.stderr)
        return 2
    target = (runs_dir / validated).resolve()
    if not target.is_dir() or target.parent != runs_dir.resolve():
        print(f"serve-fixture: no run {validated!r} beneath {runs_dir}", file=sys.stderr)
        return 2
    shutil.rmtree(target)
    return 0


def stall(port: int) -> int:
    """Accept connections on ``port`` and never answer them.

    A read that is in flight is the only way to observe a loading view, and a browser
    reaches that state only while a real request is outstanding. This is a network
    condition, not a stand-in for the API: it serves nothing and answers nothing, so
    the app's own request stays pending exactly as it would against a wedged server.
    """
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(16)
    held = []
    while True:
        connection, _ = listener.accept()
        # Held open, never written to and never closed: closing would let the client
        # fail fast, which is the opposite of what this proves.
        held.append(connection)


#: Published beside the runs root so the browser spec names the runs this module
#: wrote rather than keeping its own copy of them.
RUN_IDS_NAME = "run-ids.json"


def serve(workspace: Path, port: int) -> int:
    """Rebuild the fixture in ``workspace`` and serve it on a loopback port."""
    shutil.rmtree(workspace, ignore_errors=True)
    workspace.mkdir(parents=True)
    runs_dir, oneharness_bin = build_fixture(workspace)
    (workspace / RUN_IDS_NAME).write_text(
        json.dumps(
            {
                "live": LIVE_RUN,
                "history": HISTORY_RUN,
                "sibling": SIBLING_RUN,
                "unattributed": UNATTRIBUTED_RUN,
                "eventless": EVENTLESS_RUN,
            }
        ),
        encoding="utf-8",
    )
    from orchestrator.server import main as serve_api

    return serve_api(
        [
            "--runs-dir",
            str(runs_dir),
            "--port",
            str(port),
            "--oneharness-bin",
            str(oneharness_bin),
        ]
    )


def main(argv: list[str] | None = None) -> int:
    """Serve the fixture, or mutate the one a running server is already serving."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace",
        type=Path,
        help="fixture directory to build and serve; a throwaway temporary one by default",
    )
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument(
        "--settle-dashboard",
        action="store_true",
        help="append real progress to an already-served fixture instead of serving",
    )
    parser.add_argument(
        "--remove-run",
        help="take one run out of an already-served fixture instead of serving",
    )
    parser.add_argument(
        "--stall",
        action="store_true",
        help="accept connections and never answer, so a read stays in flight",
    )
    args = parser.parse_args(argv)

    if args.stall:
        return stall(args.port)

    workspace = args.workspace or Path(tempfile.mkdtemp(prefix="dag-ui-e2e-"))
    # The provenance records this fixture writes are throwaway too, so they must not
    # land in the operator's own state directory.
    os.environ["XDG_STATE_HOME"] = str(workspace / "state")
    if args.settle_dashboard:
        return settle_dashboard(workspace)
    if args.remove_run is not None:
        return remove_run(workspace, args.remove_run)
    try:
        return serve(workspace, args.port)
    finally:
        if args.workspace is None:
            shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
