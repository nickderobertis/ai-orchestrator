"""Real-CLI journeys: the planner views read live process truth, not `ps` patterns.

Every boundary here is the production one. The dispatch scratch is created by
`orchestrator.scratch.owned_scratch_directory`, which is what `run_onejudge` enters
for every dispatch and what holds the owner lock the views require as ownership
proof. The process carrying that dispatch's stamp is the real
`scripts/oneharness-agent.sh` driving the real `oneharness` CLI. The run ledger is
written by the real journal writer. The views are real `just status`, `just runs`,
and `just host` subprocesses.

Only the paid model is replaced, and at the narrowest seam this repository has for
it: `ONEHARNESS_BIN_CLAUDE_CODE` points the `claude-code` harness at a stand-in
executable, so oneharness spawns a real provider process, over the real
``stream-json`` protocol, from this repository's own `oneharness.toml`. That
narrowness is load-bearing here rather than incidental — the harness identity these
views report is read from the provider process itself, so a journey that replaced
oneharness's selection would be asserting on evidence production never produces.
"""

# llmlint: ignore-file[e2e_not_mocked] the repository requires faking the paid agent
# harness and does it here at its designated seam (a stand-in provider binary behind
# oneharness's own bin override). The ownership registry, the status directory, the
# agent wrapper, the oneharness CLI and its fallback selection, the stream protocol,
# the journal, and the three view CLIs are all real.

from __future__ import annotations

import json
import os
import shlex
import stat
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path

import pytest
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator import REPO_ROOT
from orchestrator.journal import open_journal
from orchestrator.runs import NodeId, RunId, write_result
from orchestrator.scratch import AGENT_STATUS_DIR_NAME, owned_scratch_directory
from orchestrator.watchdog import ProcessId, terminate_tree

#: The stand-in provider: a real executable named `claude`, spawned by real
#: oneharness over the real ``stream-json`` protocol. It publishes one tool call and
#: then stays in its turn, because every claim here is about a turn that is still
#: running — a finished one is what history already reported.
_TOOL_CALL = json.dumps(
    {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "tool_use", "id": "c1", "name": "Bash", "input": {"command": "just check"}}
            ]
        },
    }
)
_RESULT = json.dumps({"type": "result", "result": "done"})
_CLAUDE = f"""#!/usr/bin/env bash
printf '%s\\n' {shlex.quote(_TOOL_CALL)}
sleep 300
printf '%s\\n' {shlex.quote(_RESULT)}
"""

#: A process that spawns a child and idles, so the run's recorded launch owner has a
#: live descendant. That is what separates "this node lost its dispatch" from "the
#: whole run stopped" — the second is `orchestrator.liveness`' answer, one level up.
_BUSY = (
    "import subprocess, sys, time\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'])\n"
    "open(sys.argv[1], 'w').write('ready\\n')\n"
    "time.sleep(600)\n"
)


@pytest.fixture
def scratch_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point this process's `tempfile` at a private root, as the stream journeys do.

    A dispatch and the view reading it have to agree on one scratch root, and on a
    shared host that root is `TMPDIR`. Giving this journey its own is what keeps it
    from seeing — or being seen by — the real dispatches running beside it.
    """
    root = tmp_path / "scratch"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    return root


@pytest.fixture
def processes() -> Iterator[list[subprocess.Popen[bytes]]]:
    """Every process this journey starts, torn down by group.

    Each is started in its own session, so the whole tree below it — the wrapper, the
    oneharness CLI, and the provider it spawned — goes with it. Killing only the
    recorded pid leaves the provider running, which is exactly the orphaning
    `orchestrator.scratch` reaps in production and a leak the suite refuses here.
    """
    started: list[subprocess.Popen[bytes]] = []
    yield started
    for process in started:
        _kill_tree(process)


def _kill_tree(process: subprocess.Popen[bytes]) -> None:
    """End one started process and everything it spawned, then collect it.

    Through the harness's own teardown, because that is the one that works here: a
    provider oneharness put in a group of its own outlives a `killpg` of the
    wrapper's, and every descendant reparents away the instant the wrapper dies. The
    tree is therefore sampled while the root is still alive and terminated as a set,
    which is exactly what `orchestrator.dispatch` does when a dispatch ends.
    """
    terminate_tree(ProcessId(process.pid))
    process.kill()
    process.wait(timeout=e2e_timeout(15))


def _bin_dir(tmp_path: Path, oneharness_bin: str) -> Path:
    """A PATH holding the real `oneharness` and the stand-in `claude` it will spawn."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    oneharness = bin_dir / "oneharness"
    if not oneharness.exists():
        oneharness.symlink_to(oneharness_bin)
    provider = bin_dir / "claude"
    if not provider.exists():
        provider.write_text(_CLAUDE, encoding="utf-8")
        provider.chmod(provider.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir


def _dispatch(
    tmp_path: Path,
    oneharness_bin: str,
    stack: ExitStack,
    processes: list[subprocess.Popen[bytes]],
    *,
    run_id: str,
    node: str,
) -> Path:
    """Start one real dispatch for ``node`` and return its owned status directory."""
    status_dir = stack.enter_context(owned_scratch_directory()) / AGENT_STATUS_DIR_NAME
    status_dir.mkdir()
    bin_dir = _bin_dir(tmp_path, oneharness_bin)
    environment = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path / "home"),
        "ORCHESTRATOR_AGENT_STATUS_DIR": str(status_dir),
        "ONEHARNESS_HARNESSES": "claude-code",
        "ONEHARNESS_BIN_CLAUDE_CODE": str(bin_dir / "claude"),
        # The environment `oneharness.toml`'s `alternate2` variant hands its provider:
        # the identity a fallback chain selected is observable from outside the
        # process only as the credential directory it was given.
        "CLAUDE_CONFIG_DIR": str(Path(tmp_path / "home") / ".claude-alt2"),
        "ONEHARNESS_HISTORY_LABELS": (
            f"run_id={run_id},round=1,node={node},persona=engineer,agent_role=worker"
        ),
    }
    process = subprocess.Popen(
        [
            "bash",
            str(REPO_ROOT / "scripts" / "oneharness-agent.sh"),
            "run",
            "--compact",
            "--prompt",
            f"work on {node}",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
        start_new_session=True,
    )
    processes.append(process)
    # Torn down on the same stack that owns this dispatch's scratch, and pushed after
    # it, so the callback runs *before* the directory is released: the wrapper exits on
    # its own the moment its status directory disappears, and once it has, its provider
    # has reparented to init and the tree walk cannot reach it any more.
    stack.callback(_kill_tree, process)
    return status_dir


def _await_live(scratch_root: Path, node: str) -> None:
    """Wait until the ownership registry can see this dispatch, or fail saying so."""
    from orchestrator.dispatches import live_dispatches

    limit = deadline(90)
    while time.monotonic() < limit:
        observed = live_dispatches(root=scratch_root) or []
        if any(dispatch.node == node for dispatch in observed):
            return
        time.sleep(0.1)
    raise AssertionError(f"no live dispatch for node {node!r} ever became observable")


def _record_round(run_dir: Path) -> None:
    """Give this run one completed round, through the ledger's own result writer.

    Without it `just runs` renders the run through its unrecorded-launch branch. A run
    a planner is actually supervising has finished a round, so the row that carries the
    live-dispatch line for a real supervision session is the recorded one.
    """
    round_dir = run_dir / "round-01"
    round_dir.mkdir(exist_ok=True)
    write_result(
        round_dir,
        {
            "ok": True,
            "state": "complete",
            "started_order": ["seed"],
            "results": {"seed": {"kind": "agent", "status": "done", "ok": True}},
        },
    )


def _launch(run_dir: Path, pid: int, nodes: tuple[str, ...]) -> None:
    """Record a live launch and journal each node as started, through real writers."""
    journal = open_journal(run_dir, RunId(run_dir.name), 1)
    journal.append("round-started", detail={"nodes": len(nodes), "concurrency": 2, "plan": {}})
    for node in nodes:
        journal.append("node-started", node=NodeId(node), detail={"persona": "engineer"})
    (run_dir / "orchestrator").mkdir()
    (run_dir / "launch.json").write_text(
        json.dumps({"schema_version": 2, "run_id": run_dir.name, "channel_id": run_dir.name}),
        encoding="utf-8",
    )
    (run_dir / "orchestrator" / "status.json").write_text(
        json.dumps({"status": "running", "pid": pid}), encoding="utf-8"
    )


def _status_command(run_dir: Path, runs_dir: Path) -> list[str]:
    """`just status` for this run, with the undriven grace collapsed to nothing.

    That grace covers one gap in production — a dispatcher that journalled a start
    and has not yet `exec`ed the tree carrying its stamp — and this journey has
    already waited for the stamp to be observable, so waiting it out again would only
    add a minute to the suite.
    """
    return [
        "just",
        "status",
        run_dir.name,
        "--runs-dir",
        str(runs_dir),
        "--undriven-after",
        "0",
    ]


def _view(command: list[str], scratch_root: Path) -> str:
    """Run one real view CLI against the same scratch root the dispatches wrote into."""
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        env={**os.environ, "TMPDIR": str(scratch_root)},
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def test_the_views_report_the_role_harness_and_turn_age_of_a_live_dispatch(
    tmp_path: Path,
    oneharness_bin: str,
    scratch_root: Path,
    processes: list[subprocess.Popen[bytes]],
) -> None:
    """`just status`, `just runs` and `just host` all name what is actually running.

    Before this, every one of them stopped at the node: the ledger said a dispatch
    started and nothing could say which turn was in flight, on what, or for how long.
    """
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "live-truth"
    run_dir.mkdir(parents=True)
    _launch(run_dir, os.getpid(), ("build",))
    _record_round(run_dir)
    with ExitStack() as stack:
        _dispatch(tmp_path, oneharness_bin, stack, processes, run_id=run_dir.name, node="build")
        _await_live(scratch_root, "build")

        status = _view(["just", "status", run_dir.name, "--runs-dir", str(runs_dir)], scratch_root)
        host = _view(["just", "host", "--runs-dir", str(runs_dir)], scratch_root)
        host_json = json.loads(
            _view(
                ["just", "host", "--runs-dir", str(runs_dir), "--format", "json"],
                scratch_root,
            )
        )
        listed = _view(["just", "runs", "--runs-dir", str(runs_dir)], scratch_root)

    # The role is the turn's, read from the wrapper the dispatch is running; the
    # harness identity is the provider's, read from the credential directory the
    # selected variant was given; the age is that turn's, not the node's.
    assert "worker on claude-code:alternate2" in status, status
    assert "turn running " in status, status
    assert "build" in status, status
    # The whole-host view names the same dispatch, its owning session column, and the
    # load it is contributing.
    assert "worker on claude-code:alternate2" in host, host
    assert "live-truth round-01 build" in host, host
    assert "Load:" in host, host
    # The run listing surfaces the live/undriven distinction from the same
    # observation, on the recorded round-01 row a supervised run actually renders.
    recorded_row = next(line for line in listed.splitlines() if line.startswith("* "))
    assert run_dir.name in recorded_row and "round-01" in recorded_row, listed
    assert "1 live dispatch(es): live-truth round-01 build worker on" in listed, listed
    # The header attributes this host's load to the runs and nodes producing it.
    assert "Load:" in status, status
    # The machine-readable form of the same picture, which a tool joins on.
    row = next(item for item in host_json["dispatches"] if item["node"] == "build")
    assert host_json["observable"] is True
    assert (row["role"], row["harness"]) == ("worker", "claude-code:alternate2")
    assert row["run_id"] == run_dir.name and row["round"] == "1"
    assert row["turn_age_seconds"] >= 0 and row["turn_is_outlier"] is False
    assert row["processes"] >= 1
    assert "ANOMALOUS" not in host, host


def test_an_overrunning_turn_is_flagged_anomalous_and_a_quiet_host_says_so(
    tmp_path: Path,
    oneharness_bin: str,
    scratch_root: Path,
    processes: list[subprocess.Popen[bytes]],
) -> None:
    """The wedged-turn flag, and the two things `just host` says when it sees nothing.

    The anomalous flag is the reason these views exist — a judge turn ran for 1h54m
    unnoticed — so it is driven against a turn that is genuinely running rather than
    left to be believed. Waiting out a real multiple of a worker's typical duration is
    not a test, so the threshold is moved instead, through the same kind of documented
    knob `--parked-after` and `--undriven-after` already are.
    """
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "overrunning"
    run_dir.mkdir(parents=True)
    _launch(run_dir, os.getpid(), ("slow",))
    with ExitStack() as stack:
        _dispatch(tmp_path, oneharness_bin, stack, processes, run_id=run_dir.name, node="slow")
        _await_live(scratch_root, "slow")

        flagged = _view(
            ["just", "host", "--runs-dir", str(runs_dir), "--outlier-multiple", "0"],
            scratch_root,
        )
        flagged_json = json.loads(
            _view(
                [
                    "just",
                    "host",
                    "--runs-dir",
                    str(runs_dir),
                    "--outlier-multiple",
                    "0",
                    "--format",
                    "json",
                ],
                scratch_root,
            )
        )
        # A scratch root no dispatch ever wrote into: the registry answered, and the
        # answer is that nothing is running.
        (tmp_path / "quiet").mkdir()
        quiet = _view(
            [
                "just",
                "host",
                "--runs-dir",
                str(runs_dir),
                "--scratch-root",
                str(tmp_path / "quiet"),
            ],
            scratch_root,
        )

    assert "ANOMALOUS, past 0x the" in flagged, flagged
    assert next(item for item in flagged_json["dispatches"] if item["node"] == "slow")[
        "turn_is_outlier"
    ], flagged_json
    assert "Live dispatches: none." in quiet, quiet
    assert "ownership stamp whose scratch directory a dispatcher still holds" in quiet, quiet


def test_an_unreadable_process_table_is_reported_as_unknown_not_as_nothing(
    tmp_path: Path, scratch_root: Path
) -> None:
    """`just host` never turns "I could not look" into "nothing is running".

    Driven through `AI_ORCHESTRATOR_PROC_ROOT`, the procfs seam every liveness probe
    in this harness already reads, pointed at a directory that cannot show this
    process. That is the one condition under which no ownership proof is available at
    all, and reporting it as an empty host is the confident wrong answer these views
    exist to stop giving.
    """
    completed = subprocess.run(
        ["just", "host", "--runs-dir", str(tmp_path / "runs")],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        env={
            **os.environ,
            "TMPDIR": str(scratch_root),
            "AI_ORCHESTRATOR_PROC_ROOT": str(tmp_path / "no-procfs"),
        },
    )

    assert completed.returncode == 0, completed.stderr
    assert "Live dispatches: unknown" in completed.stdout, completed.stdout
    assert "procfs could not be read" in completed.stdout, completed.stdout
    assert "Live dispatches: none" not in completed.stdout, completed.stdout


def test_a_node_whose_dispatch_died_is_flagged_while_its_sibling_keeps_running(
    tmp_path: Path,
    oneharness_bin: str,
    scratch_root: Path,
    processes: list[subprocess.Popen[bytes]],
) -> None:
    """Killing one node's dispatch flags that node, and only that node.

    This is the failure the views could not see: the ledger records a node running,
    the run is plainly alive, and nothing is driving that node. The distinction is
    what makes the flag actionable rather than noise — a run that lost *everything*
    is already reported one level up, by `orchestrator.liveness`.
    """
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "one-died"
    run_dir.mkdir(parents=True)
    ready = tmp_path / "launch-ready"
    launch = subprocess.Popen(
        ["python3", "-c", _BUSY, str(ready)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    processes.append(launch)
    limit = deadline(60)
    while time.monotonic() < limit and not ready.is_file():
        time.sleep(0.02)
    assert ready.is_file(), "the recorded launch owner never spawned its child"
    _launch(run_dir, launch.pid, ("alive", "dying"))

    with ExitStack() as stack:
        _dispatch(tmp_path, oneharness_bin, stack, processes, run_id=run_dir.name, node="alive")
        with ExitStack() as doomed:
            _dispatch(
                tmp_path, oneharness_bin, doomed, processes, run_id=run_dir.name, node="dying"
            )
            _await_live(scratch_root, "alive")
            _await_live(scratch_root, "dying")
            both = _view(_status_command(run_dir, runs_dir), scratch_root)
            assert "UNDRIVEN" not in both, both
            # Kill the dispatch and release its ownership lock, which together are
            # what a dispatcher's death leaves behind.
            _kill_tree(processes[-1])

            after = _view(_status_command(run_dir, runs_dir), scratch_root)
            listed = _view(["just", "runs", "--runs-dir", str(runs_dir)], scratch_root)
            assert "1 live dispatch(es)" in listed, listed

    # Both of this run's dispatches are gone now, while another run's is still live.
    # That is the other half of the per-row distinction: the registry can see live
    # dispatches, and none of them is this run's.
    with ExitStack() as elsewhere:
        _dispatch(tmp_path, oneharness_bin, elsewhere, processes, run_id="other-run", node="x")
        _await_live(scratch_root, "x")
        emptied = _view(["just", "runs", "--runs-dir", str(runs_dir)], scratch_root)
    assert "no live dispatch carries this run's ownership stamp" in emptied, emptied

    dying_line = next(line for line in after.splitlines() if " dying " in line)
    alive_line = next(line for line in after.splitlines() if " alive " in line)
    # `UNDRIVEN`, deliberately not `parked`: that word is already the node state a
    # planner's own `cancel` produces, and it means the opposite of this.
    assert "UNDRIVEN" in dying_line, after
    assert "no live dispatch carries its ownership stamp" in dying_line, after
    assert "parked" not in dying_line.lower().replace("undriven", ""), after
    assert "UNDRIVEN" not in alive_line, after
    assert "worker on claude-code:alternate2" in alive_line, after
