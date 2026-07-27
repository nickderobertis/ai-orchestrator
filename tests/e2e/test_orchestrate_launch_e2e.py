"""Real `just orchestrate` journey: the launched process's harness environment.

The orchestrator is the one dispatch path with no project dir, so nothing else
pins its oneharness binary or its approval mode. This drives the real recipe and
the real oneharness, replacing only the paid harness processes at the seam
oneharness itself exposes (`ONEHARNESS_BIN_<HARNESS>`), then reads back which
harness ran, with which approval flag, and with which environment.
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


def _fake_claude(tmp_path: Path) -> Path:
    """A claude-code stand-in that would succeed — so only routing can exclude it."""
    fake = tmp_path / "claude"
    fake.write_text(
        """#!/usr/bin/env python3
import json
import os
from pathlib import Path

Path(os.environ["ORCHESTRATE_CLAUDE_ARGV"]).write_text("ran", encoding="utf-8")
print(json.dumps({
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "orchestrator turn",
    "session_id": "orchestrate-launch",
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
    wait_deadline = deadline(60)
    while time.monotonic() < wait_deadline:
        if path.is_file() and path.stat().st_size:
            return path.read_text(encoding="utf-8")
        time.sleep(0.02)
    stderr = run_dir / "orchestrator" / "stderr.log"
    detail = stderr.read_text(encoding="utf-8") if stderr.is_file() else "<no stderr>"
    raise AssertionError(f"the launched orchestrator never reached its harness: {detail}")


def _stop(run_dir: Path) -> None:
    """Reap the detached launch; it is its own session leader."""
    status_path = run_dir / "orchestrator" / "status.json"
    if not status_path.is_file():
        return
    pid = json.loads(status_path.read_text(encoding="utf-8"))["pid"]
    with suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGKILL)


def test_orchestrate_launch_carries_bypass_mode_and_orchestrator_routing(
    tmp_path: Path, onejudge_bin: str
) -> None:
    runs = tmp_path / "runs"
    codex_argv = tmp_path / "codex-argv"
    codex_alt_dir = tmp_path / "codex-alt-dir"
    claude_argv = tmp_path / "claude-argv"
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
            "ONEHARNESS_HISTORY": "false",
            "ONEHARNESS_BIN_CODEX": str(_fake_codex(tmp_path)),
            "ONEHARNESS_BIN_CLAUDE_CODE": str(_fake_claude(tmp_path)),
            "ORCHESTRATE_CODEX_ARGV": str(codex_argv),
            "ORCHESTRATE_CODEX_ALT_DIR": str(codex_alt_dir),
            "ORCHESTRATE_CLAUDE_ARGV": str(claude_argv),
        }
    )

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

    # Its own routing: codex carries the role. Under the worker chain the equally
    # available claude-code:alternate would have run first instead.
    assert not claude_argv.exists()
    assert argv[argv.index("--model") + 1] == "gpt-5.6-sol"
    # Its own approval mode: bypass, without the launcher exporting anything.
    assert CODEX_BYPASS_FLAG in argv
    # And the alternate-Claude indirection the fallback variant names is derived
    # from HOME, so nothing dies before the first turn on a fresh shell.
    assert alternate == f"{os.environ['HOME']}/.claude-alt"


def test_orchestrate_rejects_an_unknown_oneharness_mode(tmp_path: Path) -> None:
    rejected = subprocess.run(
        ["just", "orchestrate", str(_plan(tmp_path)), "--oneharness-mode", "unsandboxed"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert rejected.returncode != 0
    assert "--oneharness-mode" in rejected.stderr
    assert "bypass" in rejected.stderr  # the offered choices name the default
