"""Real `just orchestrate` journey: the launched process's harness environment.

The orchestrator is the one dispatch path with no project dir, so nothing else
pins its oneharness binary or its approval mode. This drives the real recipe and
the real oneharness, replacing only the paid harness process at the seam
oneharness exposes for it (`ONEHARNESS_BIN_CODEX`), then reads back which harness
ran, with which approval flag, and with which environment.

Codex is the whole proof here, and deliberately so: it is this role's primary, so
if routing resolved the worker order instead, codex would never record and the
wait below fails. There is no matching negative assertion about claude-code —
oneharness 0.5.10 ignores `ONEHARNESS_BIN_CLAUDE_CODE` (that harness runs through
an SDK rather than a spawned CLI), so a stand-in cannot observe it and a
"claude-code did not run" assertion would pass whether or not it did. The
committed priority itself is held by tests/test_harness_routing.py, and real
fallback selection by tests/e2e/test_dispatch_e2e.py.
"""

from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import time
from contextlib import suppress
from pathlib import Path

import yaml
from waits import deadline

from orchestrator import BASE_CONFIG, REPO_ROOT

# Codex's own no-approval flag: what oneharness maps `ONEHARNESS_MODE=bypass` to.
CODEX_BYPASS_FLAG = "--dangerously-bypass-approvals-and-sandbox"


def _fake_codex(tmp_path: Path) -> Path:
    """A codex stand-in that records its invocation and returns a valid turn."""
    fake = tmp_path / "codex"
    fake.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

Path(os.environ["ORCHESTRATE_CODEX_ARGV"]).write_text("\\n".join(sys.argv[1:]), encoding="utf-8")
Path(os.environ["ORCHESTRATE_CODEX_ALT_DIR"]).write_text(
    os.environ.get("ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR", "<unset>"), encoding="utf-8"
)
print(json.dumps({"type": "thread.started", "thread_id": "orchestrate-launch"}))
print(json.dumps({
    "type": "item.completed",
    "item": {"type": "agent_message", "text": "orchestrator turn"},
}))
print(json.dumps({
    "type": "turn.completed",
    "usage": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1},
}))
""",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return fake


def _oneharness_base(tmp_path: Path) -> Path:
    """A base config whose worker provider is oneharness, as production's is."""
    value = yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8"))
    value["provider"] = {"kind": "oneharness", "bin": "oneharness"}
    path = tmp_path / "oneharness-base.yaml"
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    return path


def _plan(tmp_path: Path) -> Path:
    path = tmp_path / "orchestrate-launch-plan.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "name": "orchestrate-launch",
                "tasks": [{"id": "approval", "kind": "human", "task": "approve the release"}],
            }
        ),
        encoding="utf-8",
    )
    return path


def _wait_text(path: Path, run_dir: Path) -> str:
    """Wait for the codex stand-in to record, or fail with the launch's own stderr.

    A reversed harness order surfaces here rather than at an assert: codex simply
    never records. The stderr tail is what distinguishes that from a launch that
    died before reaching any harness at all.
    """
    wait_deadline = deadline(60)
    while time.monotonic() < wait_deadline:
        if path.is_file() and path.stat().st_size:
            return path.read_text(encoding="utf-8")
        time.sleep(0.02)
    stderr = run_dir / "orchestrator" / "stderr.log"
    detail = stderr.read_text(encoding="utf-8") if stderr.is_file() else "<no stderr>"
    raise AssertionError(f"the launched orchestrator never reached its harness: {detail}")


def _stop(run_dir: Path) -> None:
    """Reap the detached launch; it is its own session leader.

    The launch outlives this process by design, and its channel relay waits a day
    for a planner that never replies — so a leak here is not a stray test artifact
    but a real supervisory process competing with the rest of the suite for the
    host, which is enough to push the timing-sensitive dispatch e2es over their
    heartbeat deadlines. `status.json` is written by the launcher just after the
    fork, so wait for it rather than skipping cleanup when it has not landed yet,
    and confirm the group is actually gone before returning.
    """
    status_path = run_dir / "orchestrator" / "status.json"
    status_deadline = deadline(30)
    while not status_path.is_file() and time.monotonic() < status_deadline:
        time.sleep(0.02)
    if not status_path.is_file():
        return
    pid = json.loads(status_path.read_text(encoding="utf-8"))["pid"]
    with suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGKILL)
    reap_deadline = deadline(30)
    while time.monotonic() < reap_deadline:
        try:
            os.killpg(pid, 0)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(0.02)
    raise AssertionError(f"the launched orchestrator process group {pid} outlived the test")


def _launch_environment(tmp_path: Path, codex_argv: Path, codex_alt_dir: Path) -> dict[str, str]:
    """The environment a fresh shell would hand `just orchestrate`."""
    environment = {**os.environ}
    # A fresh shell exports no alternate-Claude config directory and no mode; the
    # launch must be self-sufficient rather than inheriting what a developer — or an
    # outer orchestrator run — happened to set.
    for inherited in (
        "ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR",
        "ONEHARNESS_MODE",
        "ONEHARNESS_HARNESSES",
        "ONEHARNESS_MODELS",
        "ONEHARNESS_HISTORY_LABELS",
    ):
        environment.pop(inherited, None)
    environment.update(
        {
            # oneharness binds a named session to the harness that created it, and
            # this plan's session name is stable across runs — so without an
            # isolated state home the first run's binding would decide every later
            # run's routing, in the developer's own ~/.local/state/oneharness.
            "XDG_STATE_HOME": str(tmp_path / "state"),
            "ONEHARNESS_HISTORY": "false",
            "ONEHARNESS_BIN_CODEX": str(_fake_codex(tmp_path)),
            "ORCHESTRATE_CODEX_ARGV": str(codex_argv),
            "ORCHESTRATE_CODEX_ALT_DIR": str(codex_alt_dir),
        }
    )
    return environment


def test_orchestrate_launch_carries_bypass_mode_and_orchestrator_routing(
    tmp_path: Path, onejudge_bin: str
) -> None:
    runs = tmp_path / "runs"
    codex_argv = tmp_path / "codex-argv"
    codex_alt_dir = tmp_path / "codex-alt-dir"
    environment = _launch_environment(tmp_path, codex_argv, codex_alt_dir)

    launched = subprocess.run(
        [
            "just",
            "orchestrate",
            str(_plan(tmp_path)),
            "--runs-dir",
            str(runs),
            "--base",
            str(_oneharness_base(tmp_path)),
            "--onejudge-bin",
            onejudge_bin,
        ],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )
    run_dir = runs / json.loads(launched.stdout)["run_id"]
    try:
        argv = _wait_text(codex_argv, run_dir).splitlines()
        alternate = _wait_text(codex_alt_dir, run_dir).strip()
    finally:
        _stop(run_dir)

    # Its own routing: codex carries the role, on this config's own model. Under the
    # worker chain claude-code:alternate would be tried first and codex would never
    # record, so reaching this line at all is the routing assertion.
    assert argv[argv.index("--model") + 1] == "gpt-5.6-sol"
    # Its own approval mode: bypass, without the launcher exporting anything.
    assert CODEX_BYPASS_FLAG in argv
    # And the alternate-Claude indirection the fallback variant names is derived
    # from HOME, so nothing dies before the first turn on a fresh shell.
    assert alternate == f"{os.environ['HOME']}/.claude-alt"


def _view(command: str, runs: Path, history: Path) -> str:
    """One planner-facing read-only view over ``runs``, with no dispatch history."""
    history.mkdir(exist_ok=True)
    viewed = subprocess.run(
        ["just", command, "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        env={**os.environ, "ONEHARNESS_HISTORY_DIR": str(history)},
        text=True,
        capture_output=True,
        check=False,
        timeout=180,
    )
    assert viewed.returncode == 0, viewed.stderr
    return viewed.stdout


def _await_launch_pid(run_dir: Path) -> int:
    status = run_dir / "orchestrator" / "status.json"
    wait_deadline = deadline(60)
    while time.monotonic() < wait_deadline:
        if status.is_file():
            return int(json.loads(status.read_text(encoding="utf-8"))["pid"])
        time.sleep(0.02)
    raise AssertionError(f"the launch never recorded its owner at {status}")


def _orchestrate(tmp_path: Path, runs: Path, onejudge_bin: str, name: str) -> str:
    """Launch one real orchestrator through the recipe and return its run id."""
    plan = tmp_path / f"{name}-plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "name": name,
                "tasks": [{"id": "approval", "kind": "human", "task": "approve the release"}],
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
            str(_oneharness_base(tmp_path)),
            "--onejudge-bin",
            onejudge_bin,
        ],
        cwd=REPO_ROOT,
        env=_launch_environment(tmp_path, tmp_path / f"{name}-argv", tmp_path / f"{name}-alt"),
        text=True,
        capture_output=True,
        check=True,
    )
    return str(json.loads(launched.stdout)["run_id"])


def test_runs_and_status_settle_a_launch_whose_orchestrator_is_gone(
    tmp_path: Path, onejudge_bin: str
) -> None:
    """A real launch reads as live until its process dies, then as settled.

    Nothing ever rewrote the launch record, so a dead orchestrator kept a run looking
    like ordinary finished work while nothing was driving it — which is how runs sat
    stranded for half a day. Both halves come from the same real launch: it must read
    ACTIVE while its process is there, so the detection cannot buy its answer by
    calling live runs dead.

    A second orchestrator, launched the same way and left running throughout, is what
    proves the boundary: the views may say nothing about a run somebody else owns.
    """
    runs = tmp_path / "runs"
    doomed = _orchestrate(tmp_path, runs, onejudge_bin, "doomed")
    neighbour = _orchestrate(tmp_path, runs, onejudge_bin, "neighbour")
    try:
        pid = _await_launch_pid(runs / doomed)
        _await_launch_pid(runs / neighbour)

        live = _view("runs", runs, tmp_path / "history")
        assert f"* {doomed}  ACTIVE" in live
        assert f"* {neighbour}  ACTIVE" in live
        assert "SETTLED" not in live

        _stop(runs / doomed)

        listed = _view("runs", runs, tmp_path / "history")
        reported = _view("status", runs, tmp_path / "history")
    finally:
        _stop(runs / neighbour)

    settled = f"SETTLED (orchestrator pid {pid} is gone before its first round)"
    assert f"! {doomed}  {settled}" in listed
    assert f"{doomed}: {settled}" in reported
    # The other orchestrator was alive throughout, so neither view may call its run
    # settled. It still reports what it is waiting for — that is a live run being
    # reported as live, which is the half the detection must never get wrong.
    assert f"* {neighbour}  ACTIVE" in listed
    assert listed.count("SETTLED") == 1
    assert f"{neighbour}: SETTLED" not in reported
    assert reported.count("SETTLED") == 1
