"""Collector arithmetic and incomplete-data refusal at the saved telemetry boundary."""

from __future__ import annotations

import json
from pathlib import Path

# pytest's `pythonpath` puts `tests/` on the import path this module is collected under;
# mypy checks the `orchestrator` package alone, so it cannot see that suite helper.
import plan_root_variable  # type: ignore[import-not-found]
import pytest

from orchestrator import spike_brief_budgets


def _in_process(
    measure: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> tuple[int, str, dict[str, object] | None]:
    """The entry point onebudgetspec runs, in this process, read back as onebudgetspec reads it."""
    result = tmp_path / f"{measure}.json"
    result.unlink(missing_ok=True)
    capsys.readouterr()
    with pytest.MonkeyPatch.context() as patched:
        # The SDK's reporter writes the file onebudgetspec names in the environment.
        patched.setenv("ONEBUDGETSPEC_RESULT", str(result))
        status = spike_brief_budgets.main(["--measure", measure])
    written = json.loads(result.read_text()) if result.exists() else None
    return status, capsys.readouterr().err, written


def test_model_collector_counts_failed_attempts_and_refuses_missing_data(tmp_path: Path) -> None:
    history = tmp_path / "history.jsonl"
    history.write_text(
        "\n".join(
            json.dumps(
                {
                    "type": "run",
                    "harness_id": "codex:primary",
                    "status": outcome,
                    "usage": {"input_tokens": 19, "output_tokens": 7},
                }
            )
            for outcome in ["failed", "completed"]
        )
    )
    (tmp_path / "oneharness-sessions.jsonl").write_text(
        json.dumps({"history_file": str(history), "labels": {"role": "agent"}})
    )
    figure, detail = spike_brief_budgets.model(tmp_path)
    assert figure == 52
    assert "attempt 1" in detail and "failed" in detail and "input 19 + output 7" in detail
    history.write_text(
        json.dumps(
            {
                "type": "run",
                "harness_id": "codex:primary",
                "status": "nonzero",
                "usage": {"input_tokens": 19},
            }
        )
    )
    with pytest.raises(ValueError, match="output_tokens"):
        spike_brief_budgets.model(tmp_path)


def test_delivery_collector_reports_phases_and_refuses_partial_data(tmp_path: Path) -> None:
    path = tmp_path / "delivery.json"
    record = {
        "schema_version": 1,
        "eligible": "2026-10-13T00:00:00Z",
        "writer_finished": "2026-10-13T00:00:02Z",
        "copy_finished": "2026-10-13T00:00:05Z",
        "delivered": "2026-10-13T00:00:06Z",
    }
    path.write_text(json.dumps(record))
    figure, detail = spike_brief_budgets.delivery(path)
    assert figure == 6
    assert "writer: 2.0 seconds" in detail and "store publication: 3.0 seconds" in detail
    assert "channel delivery: 1.0 seconds" in detail
    del record["delivered"]
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="incomplete"):
        spike_brief_budgets.delivery(path)


def test_model_collector_refuses_an_empty_attempt_beside_complete_history(tmp_path: Path) -> None:
    complete = tmp_path / "complete.jsonl"
    complete.write_text(
        json.dumps(
            {
                "type": "run",
                "harness_id": "codex:primary",
                "status": "ok",
                "usage": {"input_tokens": 19, "output_tokens": 7},
            }
        )
    )
    incomplete = tmp_path / "incomplete.jsonl"
    incomplete.write_text(json.dumps({"type": "event", "event": "started"}))
    pointers = tmp_path / "oneharness-sessions.jsonl"
    pointers.write_text(
        "\n".join(
            json.dumps({"history_file": str(path), "labels": {"role": "writer"}})
            for path in [complete, incomplete]
        )
    )
    with pytest.raises(
        ValueError, match="incomplete.jsonl: saved history contains no model attempts"
    ):
        spike_brief_budgets.model(tmp_path)


def test_real_budget_cli_reports_incomplete_saved_flow_without_running_a_scenario(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    from orchestrator.root import REPO_ROOT

    monkeypatch.setenv("ONEPIPELINE_RUNS_DIR", str(tmp_path))
    monkeypatch.setenv("SPIKE_BRIEF_FLOWS", "missing")
    result = subprocess.run(
        [
            str(REPO_ROOT / ".venv/bin/onebudgetspec"),
            "check",
            "orchestrator/budgets.yaml",
            "--id",
            spike_brief_budgets.TOKENS,
            "--json",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    row = json.loads(result.stdout)["results"][0]
    assert row["unit"] == "tokens" and row["actual"] is None
    assert "receipt is missing" in row["error"]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "record",
    [
        {},
        {"schema_version": 2, "eligible": "2026-10-13T00:00:00Z"},
        {"schema_version": True, "eligible": "2026-10-13T00:00:00Z"},
        {"schema_version": 1, "eligible": "2026-10-13T00:00:00", "delivered": "bad"},
        {
            "schema_version": 1,
            "eligible": "2026-10-13T00:00:05Z",
            "writer_finished": "2026-10-13T00:00:02Z",
        },
        {
            "schema_version": 1,
            "eligible": "2026-10-13T00:00:00Z",
            "copy_finished": "2026-10-13T00:00:02Z",
        },
    ],
)
def test_delivery_reader_refuses_unusable_history(tmp_path: Path, record: object) -> None:
    path = tmp_path / "delivery.json"
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError):
        spike_brief_budgets.delivery(path)


def test_model_reader_counts_billed_failed_attempt_from_the_real_harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    # pytest's `pythonpath` puts `tests/e2e` on the import path this module is collected
    # under; mypy checks the `orchestrator` package alone, so it cannot see that helper.
    from project_fixtures import helper  # type: ignore[import-not-found]

    from orchestrator.root import REPO_ROOT

    monkeypatch.setenv("ONEHARNESS_BIN_CODEX", str(helper("fake_codex.py")))
    monkeypatch.setenv("FAKE_CODEX_FAIL_AFTER_TURN", "1")
    result = subprocess.run(
        [
            str(REPO_ROOT / ".venv/bin/oneharness"),
            "run",
            "--config",
            "oneharness.spike-brief.toml",
            "--harness",
            "codex:primary",
            "--prompt",
            "Report the local brief.",
            "--history",
            "--history-dir",
            str(tmp_path / "history"),
            "--history-pointer-file",
            str(tmp_path / "oneharness-sessions.jsonl"),
            "--history-label",
            "role=writer",
            "--history-label",
            "onepipeline.attempt=1",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode != 0 and "answered and then failed" in result.stderr
    value, detail = spike_brief_budgets.model(tmp_path)
    assert value == 5
    assert "writer" in detail and "input 4 + output 1" in detail and "attempt 1" in detail
    assert "status nonzero; exit 1" in detail


@pytest.mark.parametrize(
    "selection",
    [
        "automatic",
        "explicit",
        "empty-pointers",
        "malformed-pointer",
        "pointer-no-path",
        "pointer-no-side",
        "empty-side",
        "malformed-record",
        "no-harness",
        "no-status",
        "no-usage",
        "bad-input",
        "bad-output",
        "truncated-history",
        "truncated-pointer",
    ],
)
def test_real_budget_cli_selects_saved_completed_flows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    selection: str,
) -> None:
    import subprocess

    from orchestrator import spike_brief
    from orchestrator.project_store import write_plan_project
    from orchestrator.root import REPO_ROOT

    authoring = tmp_path / "authoring"
    authoring.mkdir()
    monkeypatch.setenv(plan_root_variable.name(), str(authoring))
    monkeypatch.setenv("ONEPIPELINE_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("ONEPIPELINE_LAUNCHER_SESSION", "budget-reader")
    monkeypatch.delenv("SPIKE_BRIEF_FLOWS", raising=False)
    monkeypatch.delenv("PLAN_CHECKLIST_FLOW", raising=False)
    for parent, tokens in [("first", 23), ("second", 58)]:
        writer = parent + "-spike-brief"
        write_plan_project(
            authoring,
            {
                "schema_version": 3,
                "name": writer,
                "goal": {"text": "Complete a collector fixture"},
                "tasks": [{"id": "done", "kind": "human", "task": "Record the ending."}],
            },
            native_id=writer,
        )
        for arguments in [
            ["start", "authoring:" + writer, "--require-rendered", "false"],
            ["attest", writer, "done"],
        ]:
            subprocess.run(
                [str(REPO_ROOT / ".venv/bin/onepipeline"), *arguments],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
        root = tmp_path / "runs" / parent
        root.mkdir()
        spike_brief.write(
            root / spike_brief.RECEIPT,
            spike_brief.validate(
                {
                    "schema_version": 1,
                    "plan_project": "authoring:" + parent,
                    "board_project": None,
                    "parent_run": parent,
                    "spikes_run": parent + "-spikes",
                    "writer_run": writer,
                    "manager_session": "budget-reader",
                    "brief": None,
                    "approval": None,
                }
            ),
        )
        history = tmp_path / (parent + ".jsonl")
        history.write_text(
            json.dumps(
                {
                    "type": "run",
                    "harness_id": "codex:primary",
                    "status": "ok",
                    "usage": {"input_tokens": tokens - 3, "output_tokens": 3},
                }
            )
        )
        (tmp_path / "runs" / writer / "oneharness-sessions.jsonl").write_text(
            json.dumps({"history_file": str(history), "labels": {"role": "writer"}})
        )
    if selection != "automatic":
        monkeypatch.setenv("SPIKE_BRIEF_FLOWS", "first second")
    pointer_file = tmp_path / "runs/first-spike-brief/oneharness-sessions.jsonl"
    first_history = tmp_path / "first.jsonl"
    record = json.loads(first_history.read_text())
    match selection:
        case "empty-pointers":
            pointer_file.write_text("")
        case "malformed-pointer":
            pointer_file.write_text("[]")
        case "pointer-no-path":
            pointer_file.write_text(json.dumps({"labels": {"role": "writer"}}))
        case "pointer-no-side" | "empty-side":
            pointer_file.write_text(
                json.dumps(
                    {
                        "history_file": str(first_history),
                        "labels": {} if selection == "pointer-no-side" else {"role": ""},
                    }
                )
            )
        case "malformed-record":
            first_history.write_text("[]")
        case "no-harness" | "no-status" | "no-usage":
            record.pop(
                {"no-harness": "harness_id", "no-status": "status", "no-usage": "usage"}[selection]
            )
            first_history.write_text(json.dumps(record))
        case "bad-input" | "bad-output":
            record["usage"]["input_tokens" if selection == "bad-input" else "output_tokens"] = (
                True if selection == "bad-input" else 1.2
            )
            first_history.write_text(json.dumps(record))
        case "truncated-history":
            first_history.write_text("{")
        case "truncated-pointer":
            pointer_file.write_text("{")
    result = subprocess.run(
        [
            str(REPO_ROOT / ".venv/bin/onebudgetspec"),
            "check",
            "orchestrator/budgets.yaml",
            "--id",
            spike_brief_budgets.TOKENS,
            "--json",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    row = json.loads(result.stdout)["results"][0]
    assert row["unit"] == "tokens", result.stderr + result.stdout
    if selection in ["automatic", "explicit"]:
        assert row["actual"] == 58
        assert "flow first: 23" in row["detail"] and "flow second: 58" in row["detail"]
        assert "input 20 + output 3" in row["detail"] and "input 55 + output 3" in row["detail"]
    else:
        assert row["actual"] is None
        expected = {
            "empty-pointers": "no saved model history",
            "malformed-pointer": "malformed model-history pointer",
            "pointer-no-path": "malformed model-history pointer",
            "pointer-no-side": "incomplete model side labels",
            "empty-side": "incomplete model side labels",
            "malformed-record": "malformed model history",
            "no-harness": "incomplete harness identity",
            "no-status": "incomplete model status",
            "no-usage": "incomplete usage",
            "bad-input": "invalid input_tokens",
            "bad-output": "invalid output_tokens",
            "truncated-history": "Expecting property name",
            "truncated-pointer": "Expecting property name",
        }
        assert expected[selection] in row["error"], result.stdout
    status, stderr, written = _in_process(spike_brief_budgets.TOKENS, tmp_path, capsys)
    if selection in ["automatic", "explicit"]:
        assert status == 0 and written is not None, stderr
        assert written["value"] == row["actual"] and written["detail"] == row["detail"]
    else:
        assert status == 2 and written is None
        assert expected[selection] in stderr, stderr


@pytest.mark.parametrize(
    "state", ["empty-auto", "unsurfaced", "running", "running-automatic", "absent"]
)
def test_real_budget_cli_refuses_incomplete_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], state: str
) -> None:
    import subprocess

    from orchestrator import spike_brief
    from orchestrator.project_store import write_plan_project
    from orchestrator.root import REPO_ROOT

    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setenv("ONEPIPELINE_RUNS_DIR", str(runs))
    monkeypatch.setenv("ONEPIPELINE_LAUNCHER_SESSION", "budget-reader")
    monkeypatch.delenv("SPIKE_BRIEF_FLOWS", raising=False)
    monkeypatch.delenv("PLAN_CHECKLIST_FLOW", raising=False)
    if state not in ["empty-auto", "absent"]:
        authoring = tmp_path / "authoring"
        authoring.mkdir()
        monkeypatch.setenv(plan_root_variable.name(), str(authoring))
        writer = "preview-spike-brief"
        write_plan_project(
            authoring,
            {
                "schema_version": 3,
                "name": writer,
                "goal": {"text": "Read only completed collector telemetry"},
                "tasks": [{"id": "done", "kind": "human", "task": "Record the ending."}],
            },
            native_id=writer,
        )
        operations = [["start", "authoring:" + writer, "--require-rendered", "false"]]
        if state == "unsurfaced":
            operations += [["attest", writer, "done"], ["adopt", writer, "--attach"]]
        for arguments in operations:
            subprocess.run(
                [str(REPO_ROOT / ".venv/bin/onepipeline"), *arguments],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
        path = runs / "preview" / spike_brief.RECEIPT
        path.parent.mkdir()
        fixture = json.loads((REPO_ROOT / "tests/e2e/spike_brief/receipt-v1.json").read_text())
        spike_brief.write(path, spike_brief.validate(fixture))
    # An automatic selection skips a flow whose writer is still running; a named one does not,
    # and a named flow that never surfaced a brief — a draft with no spikes — has no receipt.
    if state not in ["empty-auto", "running-automatic"]:
        monkeypatch.setenv("SPIKE_BRIEF_FLOWS", "preview")
    measure = spike_brief_budgets.DELIVERY if state == "unsurfaced" else spike_brief_budgets.TOKENS
    result = subprocess.run(
        [
            str(REPO_ROOT / ".venv/bin/onebudgetspec"),
            "check",
            "orchestrator/budgets.yaml",
            "--id",
            measure,
            "--json",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    row = json.loads(result.stdout)["results"][0]
    assert row["actual"] is None and row["unit"] == (
        "seconds" if state == "unsurfaced" else "tokens"
    )
    expected = {
        "empty-auto": "no completed flow telemetry",
        "unsurfaced": "delivery has not completed",
        "running": "model run has not ended",
        "running-automatic": "no completed flow telemetry",
        "absent": "preview: receipt is missing",
    }
    assert expected[state] in row["error"], result.stdout
    status, stderr, written = _in_process(measure, tmp_path, capsys)
    assert status == 2 and written is None and expected[state] in stderr, stderr
