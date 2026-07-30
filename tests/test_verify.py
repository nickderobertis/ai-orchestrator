"""Unit tests for local-gate detection and running."""

from __future__ import annotations

import shlex
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from process_tree import await_reaped, await_recorded_pid, is_running, write_orphaning_tree

from orchestrator.verify import detect_gate, detect_gate_candidates, run_gate


def _seed(tmp_path: Path, files: dict[str, str]) -> Path:
    d = tmp_path / "repo"
    d.mkdir()
    for rel, content in files.items():
        (d / rel).write_text(content, encoding="utf-8")
    return d


def test_detect_prefers_just_check(tmp_path) -> None:
    d = _seed(tmp_path, {"justfile": "check:\n\techo ok\n", "Makefile": "check:\n\ttrue\n"})
    assert detect_gate(d) == ["just", "check"]


def test_detect_justfile_variant_capitalized(tmp_path) -> None:
    d = _seed(tmp_path, {"Justfile": "check arg:\n\ttrue\n"})
    assert detect_gate(d) == ["just", "check"]


def test_detect_justfile_without_check_falls_through(tmp_path) -> None:
    d = _seed(tmp_path, {"justfile": "build:\n\ttrue\n", "Cargo.toml": "[package]\n"})
    assert detect_gate(d) == ["cargo", "test"]


def test_detect_makefile_check(tmp_path) -> None:
    assert detect_gate(_seed(tmp_path, {"Makefile": "check:\n\ttrue\n"})) == ["make", "check"]


def test_detect_makefile_without_check_falls_through(tmp_path) -> None:
    d = _seed(tmp_path, {"Makefile": "build:\n\ttrue\n"})
    assert detect_gate(d) is None


def test_detect_npm_test(tmp_path) -> None:
    d = _seed(tmp_path, {"package.json": '{"scripts": {"test": "jest"}}'})
    assert detect_gate(d) == ["npm", "test"]


def test_detect_npm_without_test_script(tmp_path) -> None:
    d = _seed(tmp_path, {"package.json": '{"scripts": {"build": "x"}}'})
    assert detect_gate(d) is None


def test_detect_npm_invalid_json(tmp_path) -> None:
    d = _seed(tmp_path, {"package.json": "{not json"})
    assert detect_gate(d) is None


def test_detect_pytest(tmp_path) -> None:
    assert detect_gate(_seed(tmp_path, {"pyproject.toml": "[project]\n"})) == [
        "python",
        "-m",
        "pytest",
    ]


def test_detect_none_when_unrecognized(tmp_path) -> None:
    assert detect_gate(_seed(tmp_path, {"README.md": "hi"})) is None


def test_detect_candidates_rank_all_monorepo_markers(tmp_path: Path) -> None:
    d = _seed(
        tmp_path,
        {
            "turbo.json": "{}",
            "WORKSPACE.bazel": "",
            "pnpm-workspace.yaml": "packages: []\n",
            "lerna.json": "{}",
            "package.json": '{"scripts":{"test":"true"}}',
        },
    )
    candidates = detect_gate_candidates(d)
    assert candidates[-1] == "npm test"
    assert [candidate.split()[0] for candidate in candidates[:-1]] == [
        "npx",
        "sh",
        "pnpm",
        "npx",
    ]


def test_run_gate_pass_and_fail(tmp_path) -> None:
    ok = run_gate(tmp_path, ["true"])
    assert ok.ok and ok.command == ["true"]
    bad = run_gate(tmp_path, ["false"])
    assert not bad.ok


def test_run_gate_passes_comparison_context(tmp_path) -> None:
    result = run_gate(
        tmp_path,
        [
            "sh",
            "-c",
            'test "$ORCHESTRATOR_COMPARISON_REMOTE/'
            '$ORCHESTRATOR_COMPARISON_BASE" = upstream/master',
        ],
        env={
            "ORCHESTRATOR_COMPARISON_REMOTE": "upstream",
            "ORCHESTRATOR_COMPARISON_BASE": "master",
        },
    )
    assert result.ok


def test_run_gate_scrubs_parent_orchestrator_channel(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AI_ORCHESTRATOR_CHANNEL_DIR", "/live/channel")
    monkeypatch.setenv("AI_ORCHESTRATOR_CHANNEL_RUN_ID", "outer-run")
    monkeypatch.setenv("AI_ORCHESTRATOR_CHANNEL_FUTURE", "private")

    result = run_gate(
        tmp_path,
        ["sh", "-c", "test -z \"$(env | grep '^AI_ORCHESTRATOR_CHANNEL_')\""],
    )

    assert result.ok, result.output


def test_run_gate_reuses_only_exact_commit_and_comparison(tmp_path) -> None:
    subprocess.run(["git", "init", "-b", "main", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    (tmp_path / "tracked").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "initial"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "update-ref", "refs/remotes/origin/main", "HEAD"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "update-ref", "refs/remotes/origin/release", "HEAD"],
        check=True,
    )
    log = tmp_path.with_name(f"{tmp_path.name}-gate.log")
    command = ["sh", "-c", f"echo run >> {log}"]
    env = {
        "ORCHESTRATOR_COMPARISON_REMOTE": "origin",
        "ORCHESTRATOR_COMPARISON_BASE": "main",
    }

    assert run_gate(tmp_path, command, env=env).ok
    reused = run_gate(tmp_path, command, env=env)
    assert reused.ok and reused.reused
    assert log.read_text(encoding="utf-8").splitlines() == ["run"]

    changed_base = {**env, "ORCHESTRATOR_COMPARISON_BASE": "release"}
    assert run_gate(tmp_path, command, env=changed_base).ok
    (tmp_path / "tracked").write_text("two\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-am", "change"], check=True)
    assert run_gate(tmp_path, command, env=env).ok
    assert log.read_text(encoding="utf-8").splitlines() == ["run", "run", "run"]


def test_run_gate_timeout_reaps_the_whole_gate_tree(tmp_path) -> None:
    """A gate that overruns takes every process it started down with it."""
    marker = tmp_path / "worker.pid"
    command = [sys.executable, str(write_orphaning_tree(tmp_path)), str(marker)]

    result = run_gate(tmp_path, command, timeout=0.5)
    worker = await_recorded_pid(marker)

    assert not result.ok
    assert "gate timed out after 0.5s" in result.output
    assert await_reaped(worker), "the gate's reparented worker outlived its own gate"


def test_run_gate_honours_a_deadline_shorter_than_its_supervision_interval(tmp_path) -> None:
    """A gate that finishes after its deadline is timed out, not credited with a verdict.

    A timeout the lifecycle asks for is the longest it is willing to wait, so a gate
    still running at that moment has already failed to answer in time. Supervising the
    gate on a coarser interval than the deadline must not quietly extend it: this gate
    finishes well inside the supervision interval but well outside its own deadline,
    and the verdict it produces there is one nothing was waiting for any more.
    """
    log = tmp_path / "finished"
    command = ["sh", "-c", f"sleep 0.1; echo done > {log}; echo done"]

    result = run_gate(tmp_path, command, timeout=0.01)

    assert not result.ok
    assert "gate timed out after 0.01s" in result.output
    assert result.output.strip() != "done"
    assert not log.exists(), "the gate outlived the deadline instead of being stopped at it"


def test_run_gate_cancellation_reaps_the_whole_gate_tree(tmp_path) -> None:
    """A cancelled gate stops paying for a verdict nothing is left to read."""
    marker = tmp_path / "worker.pid"
    command = [sys.executable, str(write_orphaning_tree(tmp_path)), str(marker)]
    cancel = threading.Event()

    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(run_gate, tmp_path, command, cancel=cancel)
        worker = await_recorded_pid(marker)
        assert is_running(worker)
        cancel.set()
        result = running.result(timeout=30)

    assert not result.ok
    assert "gate cancelled" in result.output
    assert await_reaped(worker), "the cancelled gate's reparented worker survived"


def test_run_gate_leaves_an_uncancelled_gate_alone(tmp_path) -> None:
    """An event that never fires must not disturb a gate that is doing its job."""
    result = run_gate(tmp_path, ["sh", "-c", "sleep 0.3; echo done"], cancel=threading.Event())

    assert result.ok and result.output.strip() == "done"


def test_run_gate_missing_command(tmp_path) -> None:
    result = run_gate(tmp_path, ["definitely-not-a-real-command-xyz"])
    assert not result.ok and "not found" in result.output


def test_verify_result_tail() -> None:
    result = run_gate(Path("."), ["true"])
    assert result.tail(10) == result.output[-10:]


def test_a_gate_that_names_no_command_reports_a_failed_gate(tmp_path) -> None:
    """`--gate " "` splits to nothing, and must not surface as a harness crash."""
    result = run_gate(tmp_path, shlex.split("   "))

    assert result.ok is False
    assert "gate command is empty" in result.output
