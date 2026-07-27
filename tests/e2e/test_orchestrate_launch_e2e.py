"""Real `just orchestrate` journey: the launched process's harness environment.

The orchestrator is the one dispatch path with no project dir, so nothing else
pins its oneharness binary or its approval mode. This drives the actual recipe and
lets the launched onejudge process reach a recording stand-in for the paid
`oneharness` binary — the same seam the rest of the suite fakes — then reads back
what that process would have run the harness with.
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

import pytest
import yaml
from waits import deadline

from orchestrator import BASE_CONFIG, REPO_ROOT

ORCHESTRATOR_CONFIG = REPO_ROOT / "oneharness.orchestrator.toml"


def _oneharness_recorder(tmp_path: Path) -> Path:
    """Put a recording `oneharness` first on PATH; return its bin directory.

    It records the invocation the orchestrator wrapper built, then refuses to speak
    the provider protocol so the launched run ends immediately: the launch
    environment is what is under test, not another agent turn.
    """
    bin_dir = tmp_path / "recorder-bin"
    bin_dir.mkdir()
    recorder = bin_dir / "oneharness"
    recorder.write_text(
        "#!/usr/bin/env bash\n"
        'printf \'%s\\n\' "$@" > "$ORCHESTRATE_RECORD_ARGV"\n'
        "printf '%s\\n' \"${ONEHARNESS_MODE-<unset>}\" "
        '"${ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR-<unset>}" > "$ORCHESTRATE_RECORD_ENV"\n'
        "exit 3\n",
        encoding="utf-8",
    )
    recorder.chmod(recorder.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir


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


def _wait_lines(path: Path) -> list[str]:
    wait_deadline = deadline(30)
    while time.monotonic() < wait_deadline:
        if path.is_file() and path.stat().st_size:
            return path.read_text(encoding="utf-8").splitlines()
        time.sleep(0.02)
    raise AssertionError(f"the launched orchestrator never invoked oneharness: {path}")


def _stop(run_dir: Path) -> None:
    """Reap the detached launch; it is its own session leader."""
    status_path = run_dir / "orchestrator" / "status.json"
    if not status_path.is_file():
        return
    pid = json.loads(status_path.read_text(encoding="utf-8"))["pid"]
    with suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGKILL)


@pytest.mark.parametrize(
    ("mode_args", "expected_mode"),
    [([], "bypass"), (["--oneharness-mode", "read-only"], "read-only")],
)
def test_orchestrate_launch_carries_bypass_mode_and_orchestrator_routing(
    tmp_path: Path, onejudge_bin: str, mode_args: list[str], expected_mode: str
) -> None:
    runs = tmp_path / "runs"
    argv_record = tmp_path / "oneharness-argv"
    env_record = tmp_path / "oneharness-env"
    environment = {
        **os.environ,
        "PATH": f"{_oneharness_recorder(tmp_path)}{os.pathsep}{os.environ['PATH']}",
        "ORCHESTRATE_RECORD_ARGV": str(argv_record),
        "ORCHESTRATE_RECORD_ENV": str(env_record),
    }
    # A fresh shell exports no alternate-Claude config directory; the launch must be
    # self-sufficient rather than inheriting one a developer set by hand.
    environment.pop("ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR", None)
    environment.pop("ONEHARNESS_MODE", None)

    # The console script `just orchestrate` delegates to, invoked directly: `uv run`
    # would re-prepend the project venv and shadow the recorder with the real
    # oneharness wheel. tests/test_orchestrator_launch.py holds the recipe and this
    # entry point together.
    launched = subprocess.run(
        [
            "orchestrator-orchestrate",
            str(_plan(tmp_path)),
            "--runs-dir",
            str(runs),
            "--base",
            str(_oneharness_base(tmp_path)),
            "--onejudge-bin",
            onejudge_bin,
            *mode_args,
        ],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )
    run_dir = runs / json.loads(launched.stdout)["run_id"]
    try:
        argv = _wait_lines(argv_record)
        recorded_mode, recorded_alternate = _wait_lines(env_record)
    finally:
        _stop(run_dir)

    # Its own routing: the orchestrator's committed config, never the worker chain
    # oneharness would otherwise discover from the repo root.
    assert argv[0] == "run"
    assert argv.count("--config") == 1
    assert argv[argv.index("--config") + 1] == str(ORCHESTRATOR_CONFIG)
    assert recorded_mode == expected_mode
    assert recorded_alternate == f"{os.environ['HOME']}/.claude-alt"


def test_orchestrate_rejects_an_unknown_oneharness_mode(tmp_path: Path) -> None:
    rejected = subprocess.run(
        ["orchestrator-orchestrate", str(_plan(tmp_path)), "--oneharness-mode", "unsandboxed"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert rejected.returncode == 2
    assert "--oneharness-mode" in rejected.stderr
    assert "bypass" in rejected.stderr  # the offered choices name the default
