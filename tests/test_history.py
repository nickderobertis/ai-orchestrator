from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.history import HistoryError, SessionId, _records, digest, recent_runs, show_run

FIXTURE = Path(__file__).parent / "fixtures" / "history" / "worker.jsonl"


def _fake_oneharness(tmp_path: Path) -> Path:
    script = tmp_path / "oneharness"
    script.write_text(
        """#!/usr/bin/env python3
import json, sys
fixture = sys.argv[1]
args = sys.argv[2:]
worker = {
    "id": "build-history-command-20260714T100000Z-123",
    "name": "build-history-command", "project": "/tmp/example-repo",
    "started": "2026-07-14T10:00:00Z", "path": fixture,
}
judge = {
    "id": "you-are-a-strict-careful-evaluator-1",
    "name": "you-are-a-strict-careful-evaluator", "project": "/tmp/example-repo",
    "started": "2026-07-14T10:02:00Z", "path": fixture,
}
if args[0] == "list": print(json.dumps([judge, worker]))
elif args[0] == "show":
    records = []
    for line in open(fixture):
        try: records.append(json.loads(line))
        except json.JSONDecodeError: pass
    print(json.dumps(records))
""".replace("fixture = sys.argv[1]", f"fixture = {str(FIXTURE)!r}"),
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def test_digest_parses_fixture_defensively() -> None:
    result = digest(_records(FIXTURE), SessionId("worker-id"))
    assert result.turns == 2
    assert (result.input_tokens, result.output_tokens) == (300, 50)
    assert result.commands == ["rg history", "just test"]
    assert result.text == "Tests pass."


def test_history_commands_cross_project_and_default_to_worker(tmp_path: Path) -> None:
    binary = _fake_oneharness(tmp_path)
    listing = recent_runs(15, oneharness_bin=str(binary))
    assert "example-repo" in listing
    assert "build-history-command" in listing
    assert "gpt-5" in listing
    assert "evaluator" not in listing

    output = show_run("history-command", oneharness_bin=str(binary))
    assert "Turns: 2" in output
    assert "300 input / 50 output" in output
    assert "$ just test" in output
    assert "Latest agent text:\nTests pass." in output
    assert (
        "oneharness history show build-history-command-20260714T100000Z-123 --format text" in output
    )


def test_bad_inputs_are_actionable(tmp_path: Path) -> None:
    binary = _fake_oneharness(tmp_path)
    with pytest.raises(HistoryError, match="positive integer"):
        recent_runs(0, oneharness_bin=str(binary))
    with pytest.raises(HistoryError, match="no worker history session"):
        show_run("missing", oneharness_bin=str(binary))
