"""Deterministic mechanics around the explicitly paid real-harness smoke."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from harness_records import (
    AUTH_REFUSAL,
    CLAUDE_ALTERNATE2_RECORD,
    CLAUDE_RECORD,
    CODEX_RECORD,
    QUOTA_REFUSAL,
    RATE_LIMITED_RECORD,
    SKIPPED_CANDIDATE,
    reported_usage,
    smoke_history_chain,
    smoke_history_record,
)

import orchestrator.smoke as smoke
from orchestrator.history import HistoryError, HistorySession, SessionId
from orchestrator.telemetry import history_session_launch_failure


def _record(path: Path, shape: dict[str, object] = CLAUDE_RECORD, **overrides: object) -> None:
    """Persist one real provider record, defaulting to the claude-code shape.

    claude-code is what the repository selects first, and its sparse record — a
    measured duration, no ``started_at``, no phase split, a null ``finished_at`` —
    is the one the launch guard used to reject.
    """
    path.write_text(
        json.dumps(smoke_history_record(shape, **overrides)) + "\n",
        encoding="utf-8",
    )


def _chain(path: Path, *shapes: dict[str, object], **overrides: object) -> None:
    """Persist one session recording every candidate a fallback chain attempted."""
    path.write_text(smoke_history_chain(*shapes, **overrides), encoding="utf-8")


def _session(tmp_path: Path, history: Path) -> HistorySession:
    return HistorySession(
        SessionId("smoke-session"),
        "smoke",
        tmp_path,
        "2026-07-25T00:00:00Z",
        history,
        {"role": "agent", "smoke": "smoke-id"},
    )


@pytest.mark.parametrize("shape", [CLAUDE_RECORD, CODEX_RECORD], ids=["claude-code", "codex"])
def test_run_smoke_validates_prompt_and_launch_contract(
    tmp_path, monkeypatch, shape: dict[str, object]
) -> None:
    """Both real shapes are healthy launches, and each reports what it did record."""
    history = tmp_path / "history.jsonl"
    _record(history, shape)

    monkeypatch.setattr(smoke.uuid, "uuid4", lambda: "smoke-id")
    monkeypatch.setattr(smoke, "_run_wrapper", lambda *_args: None)
    monkeypatch.setattr(smoke, "all_sessions", lambda: [_session(tmp_path, history)])
    assert smoke.run_smoke() == smoke.SmokeResult(
        str(shape["harness_id"]), reported_usage(shape)["cost_usd"]
    )


def test_validation_mode_loads_isolated_store_through_public_cli(tmp_path: Path, capsys) -> None:
    project = tmp_path / "project"
    project.mkdir()
    history = project / "smoke.jsonl"
    _record(
        history,
        name="smoke",
        labels={"role": "agent", "smoke": "smoke-id", "ignored": 1},
    )
    (project / "malformed.jsonl").write_text("{bad json\n", encoding="utf-8")
    (project / "events-only.jsonl").write_text('{"type":"event"}\n', encoding="utf-8")

    sessions = smoke._stored_sessions(tmp_path)

    assert len(sessions) == 1
    assert sessions[0].labels == {"role": "agent", "smoke": "smoke-id"}
    assert smoke.main(["--validate-history", str(tmp_path), "--smoke-id", "smoke-id"]) == 0
    assert (
        "smoke: passed via claude-code:alternate (recorded cost: $0.063882)"
        in capsys.readouterr().out
    )


@pytest.mark.parametrize("reported_cost", ["malformed", math.inf, -math.inf, math.nan])
def test_run_smoke_renders_invalid_recorded_cost_as_unreported(
    tmp_path: Path, monkeypatch, capsys, reported_cost: object
) -> None:
    history = tmp_path / "history.jsonl"
    _record(history, usage=reported_usage(CLAUDE_RECORD, cost_usd=reported_cost))
    monkeypatch.setattr(smoke.uuid, "uuid4", lambda: "smoke-id")
    monkeypatch.setattr(smoke, "_run_wrapper", lambda *_args: None)
    monkeypatch.setattr(smoke, "all_sessions", lambda: [_session(tmp_path, history)])

    assert smoke.main([]) == 0
    assert "recorded cost: unreported" in capsys.readouterr().out


def test_wrapper_surfaces_backgrounded_harness_failure(tmp_path, monkeypatch) -> None:
    root = tmp_path / "repo"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    wrapper = scripts / "oneharness-agent.sh"
    wrapper.write_text(
        "#!/bin/sh\n"
        "cat >/dev/null\n"
        'touch "$ORCHESTRATOR_AGENT_STATUS_DIR/agent.failed"\n'
        'echo "provider rejected task" >&2\n'
        "while :; do sleep 1; done\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    status = tmp_path / "status"
    status.mkdir()
    monkeypatch.setattr(smoke, "REPO_ROOT", root)

    with pytest.raises(HistoryError, match="provider rejected task"):
        smoke._run_wrapper(
            tmp_path, status, tmp_path / "history", "smoke-id", smoke.TIMEOUT_SECONDS
        )


@pytest.mark.parametrize(
    ("body", "timeout", "message"),
    [
        ("cat >/dev/null\nexit 3\n", smoke.TIMEOUT_SECONDS, "exit 3"),
        ("cat >/dev/null\nwhile :; do sleep 1; done\n", -10, "timed out"),
    ],
)
def test_wrapper_surfaces_exit_and_timeout(
    tmp_path, monkeypatch, body: str, timeout: int, message: str
) -> None:
    root = tmp_path / "repo"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    wrapper = scripts / "oneharness-agent.sh"
    wrapper.write_text(f"#!/bin/sh\n{body}", encoding="utf-8")
    wrapper.chmod(0o755)
    status = tmp_path / "status"
    status.mkdir()
    monkeypatch.setattr(smoke, "REPO_ROOT", root)
    with pytest.raises(HistoryError, match=message):
        smoke._run_wrapper(tmp_path, status, tmp_path / "history", "smoke-id", timeout)


def test_run_smoke_relaunches_a_failed_turn_and_reports_how_many_it_needed(
    tmp_path: Path, monkeypatch
) -> None:
    """A host that killed one launch is not a launch path that is broken."""
    history = tmp_path / "history.jsonl"
    _record(history)
    stores: list[Path] = []

    def flaky(_target: Path, _status: Path, history_dir: Path, *_rest: object) -> None:
        stores.append(history_dir)
        if len(stores) == 1:
            raise HistoryError("real harness smoke failed: harness ran but did not succeed")

    monkeypatch.setattr(smoke.uuid, "uuid4", lambda: "smoke-id")
    monkeypatch.setattr(smoke, "_run_wrapper", flaky)
    monkeypatch.setattr(smoke.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(smoke, "all_sessions", lambda: [_session(tmp_path, history)])

    result = smoke.run_smoke()

    assert result.attempts == 2
    # Each launch gets a store of its own, so what a killed turn left behind can
    # never be read back as the record the surviving turn wrote.
    assert len(set(stores)) == 2


def test_run_smoke_names_the_attempts_it_spent_before_giving_up(monkeypatch) -> None:
    """A launch path that is genuinely broken still fails, and says how hard it tried."""
    launches: list[object] = []

    def always_fails(*_args: object) -> None:
        launches.append(None)
        raise HistoryError("real harness smoke failed: the harness never started")

    monkeypatch.setattr(smoke, "_run_wrapper", always_fails)
    monkeypatch.setattr(smoke.time, "sleep", lambda _seconds: None)

    with pytest.raises(HistoryError, match=f"after {smoke.LAUNCH_ATTEMPTS} attempts"):
        smoke.run_smoke()

    assert len(launches) == smoke.LAUNCH_ATTEMPTS


def test_run_smoke_pays_for_one_turn_when_the_recorded_contract_is_broken(
    tmp_path: Path, monkeypatch
) -> None:
    """Only the launch is retried: a bad record is the regression, not the weather."""
    history = tmp_path / "history.jsonl"
    _record(history, status="error")
    launches: list[object] = []

    monkeypatch.setattr(smoke.uuid, "uuid4", lambda: "smoke-id")
    monkeypatch.setattr(smoke, "_run_wrapper", lambda *_args: launches.append(None))
    monkeypatch.setattr(smoke, "all_sessions", lambda: [_session(tmp_path, history)])

    with pytest.raises(HistoryError, match="records status 'error'"):
        smoke.run_smoke()

    assert len(launches) == 1


def test_run_smoke_passes_on_the_record_of_the_identity_the_chain_selected(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A refused subscription the chain moved past is the fallback working.

    This is the host as it stands: `claude-code:alternate` is out of weekly quota
    and `claude-code:alternate2` serves the turn. Reading the refusal as launch
    breakage failed this smoke — and with it every push whose diff touches
    `scripts/` — while the launch path was healthy the whole time.
    """
    history = tmp_path / "history.jsonl"
    _chain(history, QUOTA_REFUSAL, CLAUDE_ALTERNATE2_RECORD)
    monkeypatch.setattr(smoke.uuid, "uuid4", lambda: "smoke-id")
    monkeypatch.setattr(smoke, "_run_wrapper", lambda *_args: None)
    monkeypatch.setattr(smoke, "all_sessions", lambda: [_session(tmp_path, history)])

    assert smoke.main([]) == 0

    out = capsys.readouterr().out
    # Named, not swallowed: which subscription is gone is the operator's business
    # even when the verdict is a pass.
    assert "smoke: fell through claude-code:alternate (quota)" in out
    assert "handed the turn to the next identity" in out
    assert "smoke: passed via claude-code:alternate2 (recorded cost: $0.063882)" in out


@pytest.mark.parametrize(
    ("refusal", "reason"),
    [
        (QUOTA_REFUSAL, "quota"),
        (AUTH_REFUSAL, "auth"),
        (SKIPPED_CANDIDATE, "skipped"),
        ({**SKIPPED_CANDIDATE, "usage": None}, "skipped"),
    ],
    ids=["quota", "auth", "skipped", "unaccounted"],
)
def test_run_smoke_accepts_every_refusal_the_chain_moves_past(
    tmp_path: Path, monkeypatch, refusal: dict[str, object], reason: str
) -> None:
    """Each of these candidates declined the task without running it.

    Absent accounting is not evidence of spend: the counters a refusal does report
    have to be zero, but a candidate that reports none at all — as one nobody
    started may — is still one the chain merely stepped past.
    """
    history = tmp_path / "history.jsonl"
    _chain(history, refusal, CODEX_RECORD)
    monkeypatch.setattr(smoke.uuid, "uuid4", lambda: "smoke-id")
    monkeypatch.setattr(smoke, "_run_wrapper", lambda *_args: None)
    monkeypatch.setattr(smoke, "all_sessions", lambda: [_session(tmp_path, history)])

    assert smoke.run_smoke() == smoke.SmokeResult(
        "codex",
        None,
        fell_through=(smoke.FellThrough(str(refusal["harness_id"]), reason),),
    )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"status": "error"}, r"records status 'error' with exit code 0"),
        ({"prompt": "a different task"}, "did not receive the dispatched task"),
        ({"usage": reported_usage(CODEX_RECORD, input_tokens=None)}, "reports no input_tokens"),
    ],
)
def test_run_smoke_holds_the_selected_record_to_the_whole_bar_after_a_fall_through(
    tmp_path: Path, monkeypatch, overrides: dict[str, object], message: str
) -> None:
    """Falling through excuses the candidate, never the identity that then ran."""
    history = tmp_path / "history.jsonl"
    _chain(history, QUOTA_REFUSAL, {**CODEX_RECORD, **overrides})
    monkeypatch.setattr(smoke.uuid, "uuid4", lambda: "smoke-id")
    monkeypatch.setattr(smoke, "_run_wrapper", lambda *_args: None)
    monkeypatch.setattr(smoke, "all_sessions", lambda: [_session(tmp_path, history)])

    with pytest.raises(HistoryError, match=message):
        smoke.run_smoke()


def test_run_smoke_rejects_a_candidate_that_failed_for_an_unclassified_reason(
    tmp_path: Path, monkeypatch
) -> None:
    """`rate_limit` carries billed work, so the chain stops on it rather than moving on.

    A record like this one *ahead* of another therefore describes a chain that did
    something it does not do — which is launch breakage, not fallback.
    """
    history = tmp_path / "history.jsonl"
    _chain(history, RATE_LIMITED_RECORD, CODEX_RECORD)
    monkeypatch.setattr(smoke.uuid, "uuid4", lambda: "smoke-id")
    monkeypatch.setattr(smoke, "_run_wrapper", lambda *_args: None)
    monkeypatch.setattr(smoke, "all_sessions", lambda: [_session(tmp_path, history)])

    with pytest.raises(
        HistoryError,
        match=(
            "real harness candidate claude-code:alternate failed unclassified with "
            "status 'nonzero' and exit code 1"
        ),
    ):
        smoke.run_smoke()


@pytest.mark.parametrize(
    ("candidate", "message"),
    [
        (
            {**QUOTA_REFUSAL, "harness_id": "", "harness": ""},
            "recorded as quota but does not name the identity it was written for",
        ),
        (
            {**QUOTA_REFUSAL, "usage": reported_usage(QUOTA_REFUSAL, output_tokens=340)},
            "recorded as quota but reports output_tokens 340 that was billed for",
        ),
        (
            {**QUOTA_REFUSAL, "usage": reported_usage(QUOTA_REFUSAL, cost_usd=0.21)},
            "recorded as quota but reports cost_usd 0.21 that was billed for",
        ),
        (
            {**QUOTA_REFUSAL, "usage": reported_usage(QUOTA_REFUSAL, input_tokens="many")},
            "recorded as quota but reports malformed input_tokens 'many'",
        ),
        (
            {**QUOTA_REFUSAL, "usage": 12},
            "recorded as quota but reports malformed token accounting 12",
        ),
        (
            {**QUOTA_REFUSAL, "status": "ok", "exit_code": 0},
            "recorded as quota but records a successful turn",
        ),
    ],
    ids=["nameless", "billed-tokens", "billed-cost", "malformed-counter", "malformed-usage", "ran"],
)
def test_run_smoke_rejects_a_candidate_whose_record_does_not_back_its_own_reason(
    tmp_path: Path, monkeypatch, candidate: dict[str, object], message: str
) -> None:
    """A candidate's word for what happened to it is checked, not believed.

    These records come out of a store nothing in this process wrote, and each one
    reaches the verdict and the operator's report alike. A refusal that names no
    identity would be reported as "an unidentified harness fell through", and one
    carrying a billed turn is a candidate that RAN — excusing either as fallback is
    the launch breakage this smoke exists to name.
    """
    history = tmp_path / "history.jsonl"
    _chain(history, candidate, CODEX_RECORD)
    monkeypatch.setattr(smoke.uuid, "uuid4", lambda: "smoke-id")
    monkeypatch.setattr(smoke, "_run_wrapper", lambda *_args: None)
    monkeypatch.setattr(smoke, "all_sessions", lambda: [_session(tmp_path, history)])

    with pytest.raises(HistoryError, match=message):
        smoke.run_smoke()


def test_run_smoke_reads_the_verdict_off_the_last_turn_a_session_recorded(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A store holds more than turns, and only a turn may stand in for the launch.

    The selected candidate is the LAST record, so anything else a store appends —
    an index envelope wrapping a record inside a `record` key, a line the format
    grows later — would take its place and be judged as the identity that ran.
    """
    history = tmp_path / "history.jsonl"
    _chain(history, QUOTA_REFUSAL, CLAUDE_ALTERNATE2_RECORD)
    with history.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"session_path": str(history), "record": {"harness": "codex"}}))
        stream.write("\n")
    monkeypatch.setattr(smoke.uuid, "uuid4", lambda: "smoke-id")
    monkeypatch.setattr(smoke, "_run_wrapper", lambda *_args: None)
    monkeypatch.setattr(smoke, "all_sessions", lambda: [_session(tmp_path, history)])

    assert smoke.main([]) == 0

    assert "smoke: passed via claude-code:alternate2" in capsys.readouterr().out


def test_run_smoke_rejects_a_chain_with_no_candidate_left_to_run_the_task(
    tmp_path: Path, monkeypatch
) -> None:
    """Every identity refusing is the outage this smoke must still report."""
    history = tmp_path / "history.jsonl"
    _chain(history, QUOTA_REFUSAL, AUTH_REFUSAL)
    monkeypatch.setattr(smoke.uuid, "uuid4", lambda: "smoke-id")
    monkeypatch.setattr(smoke, "_run_wrapper", lambda *_args: None)
    monkeypatch.setattr(smoke, "all_sessions", lambda: [_session(tmp_path, history)])

    with pytest.raises(
        HistoryError,
        match=(
            "no candidate left to run the task: claude-code:alternate \\(quota\\), "
            "claude-code:alternate2 \\(auth\\); restore one of these identities"
        ),
    ):
        smoke.run_smoke()


def test_run_smoke_reports_a_session_that_recorded_no_harness_run(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A turn that streamed events and never recorded a run never reached a harness.

    The chain split reads the launch path's outcome off the LAST record, so an empty
    session has to be caught ahead of it: there is no selected candidate to hold to
    the bar, and what the operator needs is that diagnostic rather than a crash.
    """
    history = tmp_path / "history.jsonl"
    history.write_text('{"type": "event", "run_id": "turn-1", "event": {}}\n', encoding="utf-8")
    monkeypatch.setattr(smoke.uuid, "uuid4", lambda: "smoke-id")
    monkeypatch.setattr(smoke, "_run_wrapper", lambda *_args: None)
    monkeypatch.setattr(smoke, "all_sessions", lambda: [_session(tmp_path, history)])

    assert smoke.main([]) == 1

    err = capsys.readouterr().err
    assert "smoke: real harness history recorded no harness run" in err
    assert "rerun 'just smoke'" in err


def test_run_smoke_rejects_missing_matching_history(monkeypatch) -> None:
    monkeypatch.setattr(smoke, "_run_wrapper", lambda *_args: None)
    monkeypatch.setattr(smoke, "all_sessions", lambda: [])
    with pytest.raises(HistoryError, match="found 0"):
        smoke.run_smoke()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"prompt": "a different task"}, "did not receive the dispatched task"),
        ({"harness": ""}, "does not identify the selected harness"),
        ({"status": "error"}, r"records status 'error' with exit code 0"),
        ({"exit_code": 1}, r"records status 'ok' with exit code 1"),
        ({"schema_version": 99}, "records unsupported history schema 99"),
        ({"duration_ms": None}, "records no measured duration"),
        ({"usage": None}, "reports no token accounting"),
        ({"usage": reported_usage(CLAUDE_RECORD, input_tokens=None)}, "reports no input_tokens"),
        (
            {"usage": reported_usage(CLAUDE_RECORD, output_tokens="eight")},
            "malformed output_tokens",
        ),
    ],
)
def test_run_smoke_rejects_broken_record_contracts(
    tmp_path: Path, monkeypatch, overrides: dict[str, object], message: str
) -> None:
    history = tmp_path / "history.jsonl"
    _record(history, **overrides)
    monkeypatch.setattr(smoke.uuid, "uuid4", lambda: "smoke-id")
    monkeypatch.setattr(smoke, "_run_wrapper", lambda *_args: None)
    monkeypatch.setattr(smoke, "all_sessions", lambda: [_session(tmp_path, history)])
    with pytest.raises(HistoryError, match=message):
        smoke.run_smoke()


def test_launch_contract_rejects_a_session_that_never_reached_a_harness(tmp_path: Path) -> None:
    history = tmp_path / "history.jsonl"
    history.write_text('{"type": "event", "run_id": "turn-1", "event": {}}\n', encoding="utf-8")

    assert history_session_launch_failure(_session(tmp_path, history)) == "recorded no harness run"


def test_main_reports_success_and_failure(monkeypatch, capsys) -> None:
    monkeypatch.setattr(smoke, "run_smoke", lambda: smoke.SmokeResult("codex", 0.0123456))
    assert smoke.main([]) == 0
    assert "$0.012346" in capsys.readouterr().out

    def fail() -> smoke.SmokeResult:
        raise HistoryError("broken")

    monkeypatch.setattr(smoke, "run_smoke", fail)
    assert smoke.main([]) == 1
    assert "smoke: broken" in capsys.readouterr().err


def test_main_reports_a_smoke_that_needed_more_than_one_launch(monkeypatch, capsys) -> None:
    """The operator is the only one who can act on a host that killed a launch."""
    monkeypatch.setattr(smoke, "run_smoke", lambda: smoke.SmokeResult("codex", None, attempts=2))

    assert smoke.main([]) == 0
    assert (
        "smoke: passed via codex (recorded cost: unreported) after 2 attempts"
        in capsys.readouterr().out
    )


def test_timeout_override_is_bounded(monkeypatch) -> None:
    monkeypatch.delenv("ORCHESTRATOR_SMOKE_TIMEOUT_SECONDS", raising=False)
    assert smoke._timeout_seconds() == smoke.TIMEOUT_SECONDS
    monkeypatch.setenv("ORCHESTRATOR_SMOKE_TIMEOUT_SECONDS", "7")
    assert smoke._timeout_seconds() == 7
    for invalid in ("bad", "0", str(smoke.TIMEOUT_SECONDS + 1)):
        monkeypatch.setenv("ORCHESTRATOR_SMOKE_TIMEOUT_SECONDS", invalid)
        with pytest.raises(HistoryError, match="must be between"):
            smoke._timeout_seconds()
