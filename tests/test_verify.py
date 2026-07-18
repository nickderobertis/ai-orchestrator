"""Unit tests for local-gate detection and running."""

from __future__ import annotations

import subprocess
from pathlib import Path

from orchestrator.verify import detect_gate, run_gate


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


def test_run_gate_missing_command(tmp_path) -> None:
    result = run_gate(tmp_path, ["definitely-not-a-real-command-xyz"])
    assert not result.ok and "not found" in result.output


def test_verify_result_tail() -> None:
    result = run_gate(Path("."), ["true"])
    assert result.tail(10) == result.output[-10:]
