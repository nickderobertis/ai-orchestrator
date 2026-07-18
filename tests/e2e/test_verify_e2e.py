"""E2E journeys for durable complete-gate attestations against real Git."""

from __future__ import annotations

import subprocess

from orchestrator.verify import run_gate


def test_gate_attestation_reuses_only_exact_commit_and_comparison(tmp_path) -> None:
    subprocess.run(["git", "init", "-b", "main", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    (tmp_path / "tracked").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "initial"], check=True)
    gate_log = tmp_path / "gate.log"
    command = ["sh", "-c", f"echo run >> {gate_log}"]
    comparison = {
        "ORCHESTRATOR_COMPARISON_REMOTE": "origin",
        "ORCHESTRATOR_COMPARISON_BASE": "main",
    }

    assert run_gate(tmp_path, command, env=comparison).ok
    reused = run_gate(tmp_path, command, env=comparison)
    assert reused.ok and reused.reused
    assert gate_log.read_text(encoding="utf-8").splitlines() == ["run"]

    changed_base = {**comparison, "ORCHESTRATOR_COMPARISON_BASE": "release"}
    assert run_gate(tmp_path, command, env=changed_base).ok
    (tmp_path / "tracked").write_text("two\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-am", "change"], check=True)
    assert run_gate(tmp_path, command, env=comparison).ok
    assert gate_log.read_text(encoding="utf-8").splitlines() == ["run", "run", "run"]
