"""Real command-surface coverage for the oneharness history views."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from orchestrator import REPO_ROOT

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "history" / "worker.jsonl"


def _run(*args: str, history_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=REPO_ROOT,
        env={**os.environ, "ONEHARNESS_HISTORY_DIR": str(history_dir)},
        text=True,
        capture_output=True,
    )


def _history_store(tmp_path: Path) -> Path:
    """Build the real oneharness on-disk layout with two projects and three sides."""
    history_dir = tmp_path / "history"
    target_project = history_dir / "tmp-target-repo"
    target_project.mkdir(parents=True)

    def add(project: Path, filename: str, name: str, project_path: str, start_hour: int) -> None:
        lines: list[str] = []
        turn = 0
        for line in FIXTURE.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                lines.append(line)
                continue
            record.update(
                name=name,
                project=project_path,
                session=name,
                timestamp=f"2026-07-14T{start_hour:02d}:{turn:02d}:00Z",
            )
            turn += 1
            lines.append(json.dumps(record))
        (project / filename).write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Two non-unique worker names prove substring resolution chooses the newest.
    sessions = (
        (
            "build-history-command-20260714T090000Z-111.jsonl",
            "build-history-command",
            9,
        ),
        (
            "build-history-command-20260714T100000Z-222.jsonl",
            "build-history-command",
            10,
        ),
        (
            "you-are-a-strict-careful-evaluator-20260714T100100Z-333.jsonl",
            "you-are-a-strict-careful-evaluator",
            11,
        ),
        (
            "you-are-roleplaying-the-user-in-20260714T100200Z-444.jsonl",
            "you-are-roleplaying-the-user-in",
            12,
        ),
    )
    for filename, name, start_hour in sessions:
        add(target_project, filename, name, "/tmp/target-repo", start_hour)

    # A second target project proves the recipe is not accidentally scoped to one repo.
    other_project = history_dir / "tmp-other-repo"
    other_project.mkdir()
    add(
        other_project,
        "other-project-task-20260714T080000Z-555.jsonl",
        "other-project-task",
        "/tmp/other-repo",
        8,
    )
    return history_dir


def test_history_recipes_use_real_cli_across_projects(tmp_path: Path) -> None:
    history_dir = _history_store(tmp_path)

    # The underlying CLI's default scope from this repo cannot see the target stores.
    scoped = _run("oneharness", "history", "list", "--format", "json", history_dir=history_dir)
    assert scoped.returncode == 0, scoped.stderr
    assert json.loads(scoped.stdout) == []

    listing = _run("just", "history", "15", history_dir=history_dir)
    assert listing.returncode == 0, listing.stderr
    assert listing.stdout.count("build-history-command") == 2
    assert "other-project-task" in listing.stdout
    assert "strict-careful-evaluator" not in listing.stdout
    assert "roleplaying-the-user" not in listing.stdout

    shown = _run("just", "history-show", "build-history", history_dir=history_dir)
    assert shown.returncode == 0, shown.stderr
    assert "Session: build-history-command-20260714T100000Z-222" in shown.stdout
    assert "Turns: 2" in shown.stdout
    assert "Tokens: 300 input / 50 output" in shown.stdout
    assert "$ just test" in shown.stdout
    assert "Latest agent text:\nTests pass." in shown.stdout
    assert "this malformed line" not in shown.stdout


def test_history_show_bad_or_judge_id_is_actionable(tmp_path: Path) -> None:
    history_dir = _history_store(tmp_path)
    for query in ("missing-session", "strict-careful-evaluator"):
        result = _run("just", "history-show", query, history_dir=history_dir)
        assert result.returncode == 2
        assert f"no worker history session matches {query!r}" in result.stderr
        assert "Traceback" not in result.stderr
