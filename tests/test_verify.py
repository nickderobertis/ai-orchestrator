"""Unit tests for local-gate detection and running."""

from __future__ import annotations

import subprocess
from pathlib import Path

from orchestrator.journal import NodeJournal, open_journal
from orchestrator.runs import NodeId, RunId
from orchestrator.verify import (
    detect_gate,
    detect_gate_candidates,
    record_merge_path_failure,
    record_merge_path_verification,
    run_gate,
)


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


def test_run_gate_missing_command(tmp_path) -> None:
    result = run_gate(tmp_path, ["definitely-not-a-real-command-xyz"])
    assert not result.ok and "not found" in result.output


def test_verify_result_tail() -> None:
    result = run_gate(Path("."), ["true"])
    assert result.tail(10) == result.output[-10:]


# --- every settled publication leaves something to read ------------------------
#
# The failure that killed this node's own run recorded `ok=false` with no output
# tail and wrote nothing to any log. Both recorders below are the only ways a
# publication outcome reaches the journal, so the invariant is stated here: a
# verdict cannot be recorded without the output behind it, and an empty output is
# still a record rather than a zero-byte file.


def _scope(tmp_path: Path, run: str) -> tuple[object, NodeJournal]:
    journal = open_journal(tmp_path / run, RunId(run), 1)
    return journal, NodeJournal(journal, NodeId("publish"), RunId(run), 1)


def test_a_recorded_verdict_always_carries_the_output_behind_it(tmp_path: Path) -> None:
    journal, scope = _scope(tmp_path, "verdict-evidence")

    result = record_merge_path_verification(
        scope, label="publication push", command=["just", "gate"], ok=False, output=""
    )

    assert result is not None
    (event,) = [e for e in journal.events() if e.kind == "verification-finished"]
    assert str(event.detail["output_tail"]).strip()
    preserved = Path(str(event.detail["log_path"])).read_text(encoding="utf-8")
    # The round-1 observation was a *zero-byte* gate.log beside a real verdict.
    assert preserved.strip()
    assert "<no output>" in preserved and "verdict: FAILED" in preserved


def test_a_publication_that_never_reached_a_gate_still_records_its_error(
    tmp_path: Path,
) -> None:
    journal, scope = _scope(tmp_path, "failure-evidence")

    log_path = record_merge_path_failure(
        scope,
        label="publication of feature",
        outcome="GitError",
        output="fatal: could not read from remote repository\n",
    )

    assert log_path is not None
    (event,) = [e for e in journal.events() if e.kind == "publication-failed"]
    assert "could not read from remote repository" in str(event.detail["output_tail"])
    assert "could not read from remote repository" in Path(log_path).read_text(encoding="utf-8")
