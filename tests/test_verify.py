"""Unit tests for local-gate detection and running."""

from __future__ import annotations

import shlex
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from process_tree import await_reaped, await_recorded_pid, is_running, write_orphaning_tree

from orchestrator.journal import NodeJournal, open_journal
from orchestrator.runs import NodeId, RunId
from orchestrator.verify import (
    detect_gate,
    detect_gate_candidates,
    preserve_gate_log,
    preserved_gate_log_dir,
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


# These two recorders are the only ways a publication outcome reaches the journal,
# so the invariant belongs here: neither can record an outcome without the output
# behind it, and an empty output is still a record rather than an empty file.


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


# One durable location per branch, written by whichever driver publishes it. The
# journeys are in `tests/e2e/test_lifecycle_e2e.py`; these are the boundaries a real
# publication cannot be made to sit on — a full directory, a concurrent claim, and the
# single appended file every branch on this host still has from before the split.


def test_each_invocation_claims_a_file_of_its_own_and_retention_bounds_the_rest(
    tmp_path: Path,
) -> None:
    directory = preserved_gate_log_dir(tmp_path, "ai-orchestrator/engineer/deadbeef")
    assert directory == tmp_path / "gate-logs" / "ai-orchestrator-engineer-deadbeef"

    written = [preserve_gate_log(directory, f"attempt {n}", retain=3) for n in range(1, 6)]

    assert [Path(path).name for path in written] == [f"gate-{n:04d}.log" for n in range(1, 6)]
    assert sorted(path.name for path in directory.glob("gate-*.log")) == [
        "gate-0003.log",
        "gate-0004.log",
        "gate-0005.log",
    ]
    assert Path(written[-1]).read_text(encoding="utf-8") == "attempt 5\n"


def test_the_appended_log_written_before_the_split_is_neither_counted_nor_pruned(
    tmp_path: Path,
) -> None:
    """Every branch recovered on this host has one, and it is the whole history."""
    directory = preserved_gate_log_dir(tmp_path, "feature/legacy")
    directory.mkdir(parents=True)
    legacy = directory / "gate.log"
    legacy.write_text("four appended attempts\n", encoding="utf-8")

    for attempt in range(4):
        preserve_gate_log(directory, f"attempt {attempt}", retain=1)

    assert legacy.read_text(encoding="utf-8") == "four appended attempts\n"
    assert sorted(path.name for path in directory.glob("gate-*.log")) == ["gate-0004.log"]


def test_a_number_another_writer_already_claimed_is_never_written_over(
    tmp_path: Path,
) -> None:
    """Two publications of one branch can run at once — a retry beside a recovery."""
    directory = preserved_gate_log_dir(tmp_path, "feature/concurrent")
    directory.mkdir(parents=True)
    (directory / "gate-0001.log").write_text("the other writer's run\n", encoding="utf-8")
    # The name this call would otherwise take next, created between its scan and its
    # claim, which is the only window `O_EXCL` exists to close.
    (directory / "gate-0002.log").write_text("claimed mid-scan\n", encoding="utf-8")

    written = preserve_gate_log(directory, "mine", retain=10)

    assert Path(written).name == "gate-0003.log"
    assert (directory / "gate-0001.log").read_text(encoding="utf-8") == "the other writer's run\n"
    assert (directory / "gate-0002.log").read_text(encoding="utf-8") == "claimed mid-scan\n"


def test_a_preserved_copy_is_named_beside_the_run_scoped_one(tmp_path: Path) -> None:
    """The report keeps pointing into the run; the durable copy is what outlives it."""
    journal, scope = _scope(tmp_path, "durable-evidence")
    durable = preserved_gate_log_dir(tmp_path, "feature/durable")

    result = record_merge_path_verification(
        scope,
        label="branch push",
        command=["just", "gate"],
        ok=True,
        output="gate passed\n",
        preserve_dir=durable,
    )

    assert result is not None and result.preserved_log_path is not None
    (event,) = [e for e in journal.events() if e.kind == "verification-finished"]
    assert event.detail["preserved_log_path"] == result.preserved_log_path
    assert event.detail["log_path"] != result.preserved_log_path
    assert "verdict: passed" in Path(result.preserved_log_path).read_text(encoding="utf-8")
