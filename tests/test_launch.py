"""Launching-session provenance: id minting, the out-of-repo record, and expiry."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from orchestrator.launch import (
    PROVENANCE_SCHEMA_VERSION,
    UNKNOWN_OWNER,
    DetectedLaunch,
    LaunchError,
    LaunchIdentity,
    RunOwner,
    caller_identity,
    detect_launch,
    generate_launch_id,
    provenance_dir,
    provenance_path,
    read_provenance,
    read_run_owner,
    resolve_launcher_kind,
    select_launch,
    session_fingerprint,
    validate_launch_id,
    validate_session_id,
    write_provenance,
)


@pytest.fixture(autouse=True)
def _state_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


def test_generate_and_validate_launch_id() -> None:
    launch_id = generate_launch_id()
    assert validate_launch_id(launch_id) == launch_id
    assert len(launch_id) == 32
    assert generate_launch_id() != launch_id  # random
    for bad in ("", "xyz", "ABCDEF", launch_id + "0", 123, None):
        assert validate_launch_id(bad) is None


def test_resolve_launcher_kind() -> None:
    assert resolve_launcher_kind(None) == "unknown"
    assert resolve_launcher_kind("") == "unknown"
    assert resolve_launcher_kind("codex") == "codex"
    assert resolve_launcher_kind("claude-code") == "claude-code"
    with pytest.raises(LaunchError, match="launcher kind"):
        resolve_launcher_kind("gpt")


def test_validate_session_id() -> None:
    assert validate_session_id(None) is None
    assert validate_session_id("") is None
    assert validate_session_id("sess-1") == "sess-1"
    # Every control character is a potential second line in a log or history label.
    for crafted in ("two\nlines", "carriage\rreturn", "nul\x00byte", "tab\tsep", "x" * 257):
        with pytest.raises(LaunchError, match="session id"):
            validate_session_id(crafted)


def test_provenance_dir_honours_xdg_state_home(tmp_path: Path) -> None:
    assert provenance_dir() == tmp_path / "state" / "ai-orchestrator" / "launches"


def test_write_and_read_provenance_round_trip() -> None:
    launch_id = generate_launch_id()
    path = write_provenance(
        launch_id=launch_id,
        launcher="codex",
        launcher_session_id="sess-9",
        repository_identity="local/app",
    )
    assert path == provenance_path(launch_id)
    record = read_provenance(launch_id)
    assert record is not None
    assert record["schema_version"] == PROVENANCE_SCHEMA_VERSION
    assert record["launcher"] == "codex"
    assert record["launcher_session_id"] == "sess-9"
    assert record["repository_identity"] == "local/app"


def test_write_provenance_rejects_bad_inputs() -> None:
    with pytest.raises(LaunchError, match="launch id"):
        write_provenance(
            launch_id="not-hex",
            launcher="codex",
            launcher_session_id="s",
            repository_identity="",
        )
    with pytest.raises(LaunchError, match="known launcher"):
        write_provenance(
            launch_id=generate_launch_id(),
            launcher="unknown",
            launcher_session_id="s",
            repository_identity="",
        )


def test_read_provenance_missing_and_invalid_launch_id() -> None:
    assert read_provenance(generate_launch_id()) is None  # nothing written
    assert read_provenance("not-a-launch-id") is None


def test_read_provenance_rejects_malformed_record() -> None:
    launch_id = generate_launch_id()
    path = provenance_path(launch_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A record whose launcher is not a known harness is rejected at the boundary.
    path.write_text(
        json.dumps(
            {
                "schema_version": PROVENANCE_SCHEMA_VERSION,
                "launch_id": launch_id,
                "launcher": "unknown",
                "launcher_session_id": "s",
                "started_at": datetime.now(UTC).isoformat(),
                "repository_identity": "",
            }
        ),
        encoding="utf-8",
    )
    assert read_provenance(launch_id) is None

    # A non-string / unparseable started_at is rejected by the timestamp boundary.
    for bad_started in (12345, "yesterdayZ"):
        path.write_text(
            json.dumps(
                {
                    "schema_version": PROVENANCE_SCHEMA_VERSION,
                    "launch_id": launch_id,
                    "launcher": "codex",
                    "launcher_session_id": "s",
                    "started_at": bad_started,
                    "repository_identity": "",
                }
            ),
            encoding="utf-8",
        )
        assert read_provenance(launch_id) is None

    path.write_text("{ not json", encoding="utf-8")
    assert read_provenance(launch_id) is None


def test_read_provenance_treats_stale_record_as_expired() -> None:
    launch_id = generate_launch_id()
    old = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    write_provenance(
        launch_id=launch_id,
        launcher="codex",
        launcher_session_id="sess-old",
        repository_identity="",
        started_at=old,
    )
    assert read_provenance(launch_id) is None  # older than the default max age
    # A generous max age (or an as-of clock) still reads it.
    fresh = read_provenance(launch_id, max_age_seconds=60 * 24 * 3600)
    assert fresh is not None and fresh["launcher_session_id"] == "sess-old"


def test_write_provenance_rejects_an_unusable_session_id() -> None:
    """The sole writer of the sensitive record re-checks it rather than trusting a caller."""
    for crafted in ("multi\nline", "x" * 257):
        with pytest.raises(LaunchError, match="session id"):
            write_provenance(
                launch_id=generate_launch_id(),
                launcher="codex",
                launcher_session_id=crafted,
                repository_identity="",
            )
    with pytest.raises(LaunchError, match="non-empty launcher session id"):
        write_provenance(
            launch_id=generate_launch_id(),
            launcher="codex",
            launcher_session_id="",
            repository_identity="",
        )


def test_read_provenance_rejects_a_replaced_record_with_an_unusable_session_id() -> None:
    """The record is an out-of-repo file; another writer can have replaced it since."""
    launch_id = generate_launch_id()
    write_provenance(
        launch_id=launch_id,
        launcher="codex",
        launcher_session_id="sess-ok",
        repository_identity="",
    )
    record = json.loads(provenance_path(launch_id).read_text(encoding="utf-8"))
    record["launcher_session_id"] = "smuggled\nsecond-line"
    provenance_path(launch_id).write_text(json.dumps(record), encoding="utf-8")

    assert read_provenance(launch_id) is None


def test_read_provenance_rejects_a_record_dated_far_in_the_future() -> None:
    """A far-future timestamp is not skew; it would outlive every expiry check."""
    launch_id = generate_launch_id()
    write_provenance(
        launch_id=launch_id,
        launcher="codex",
        launcher_session_id="sess-future",
        repository_identity="",
        started_at=(datetime.now(UTC) + timedelta(days=365)).isoformat(),
    )
    assert read_provenance(launch_id) is None

    # Modest skew from a host running slightly ahead stays readable.
    skewed = generate_launch_id()
    write_provenance(
        launch_id=skewed,
        launcher="codex",
        launcher_session_id="sess-skew",
        repository_identity="",
        started_at=(datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
    )
    assert read_provenance(skewed) is not None


def test_write_provenance_rejects_fields_the_reader_would_refuse() -> None:
    """The writer must not persist a record its own reader treats as invalid."""
    with pytest.raises(LaunchError, match="repository identity"):
        write_provenance(
            launch_id=generate_launch_id(),
            launcher="codex",
            launcher_session_id="s",
            repository_identity="local/app\nsecond-line",
        )
    with pytest.raises(LaunchError, match="RFC 3339 UTC"):
        write_provenance(
            launch_id=generate_launch_id(),
            launcher="codex",
            launcher_session_id="s",
            repository_identity="",
            started_at="yesterday",
        )
    # A local-time stamp has no UTC offset, so the reader could never parse it back.
    with pytest.raises(LaunchError, match="RFC 3339 UTC"):
        write_provenance(
            launch_id=generate_launch_id(),
            launcher="codex",
            launcher_session_id="s",
            repository_identity="",
            started_at="2026-07-19T00:00:00",
        )


def test_read_provenance_rejects_a_replaced_record_with_an_unusable_identity() -> None:
    """The identity bound is reapplied on read; the writer's check guards only writes."""
    launch_id = generate_launch_id()
    write_provenance(
        launch_id=launch_id,
        launcher="codex",
        launcher_session_id="sess-ok",
        repository_identity="local/app",
    )
    record = json.loads(provenance_path(launch_id).read_text(encoding="utf-8"))
    record["repository_identity"] = "local/app\nsecond-line"
    provenance_path(launch_id).write_text(json.dumps(record), encoding="utf-8")

    assert read_provenance(launch_id) is None


def test_provenance_dir_ignores_a_relative_xdg_state_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative XDG_STATE_HOME would resolve against the working directory.

    The same launch would then write and read different files depending on where the
    process happened to start, so the spec's default wins instead.
    """
    monkeypatch.setenv("XDG_STATE_HOME", "relative/state")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    assert (
        provenance_dir() == tmp_path / "home" / ".local" / "state" / "ai-orchestrator" / "launches"
    )


def test_provenance_dir_refuses_a_relative_home(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no absolute XDG_STATE_HOME there is nowhere left to fall back to."""
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", "relative/home")

    with pytest.raises(LaunchError, match="absolute state directory"):
        provenance_dir()


def test_read_provenance_degrades_when_the_state_directory_is_unusable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken state environment must not fail a read; the launcher is just unknown."""
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", "relative/home")

    assert read_provenance(generate_launch_id()) is None


def _write_launch_record(run_dir: Path, launch_id: str | None) -> Path:
    """Leave the run-directory half of the scheme, as `just orchestrate` writes it."""
    run_dir.mkdir(parents=True, exist_ok=True)
    record: dict[str, object] = {"schema_version": 2, "run_id": run_dir.name}
    if launch_id is not None:
        record["launch"] = {"launch_id": launch_id}
    (run_dir / "launch.json").write_text(json.dumps(record), encoding="utf-8")
    return run_dir


def test_detect_launch_reads_the_ambient_harness_and_its_session() -> None:
    assert detect_launch({"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "s-1"}) == DetectedLaunch(
        "claude-code", "s-1"
    )
    assert detect_launch({"CODEX_THREAD_ID": "t-1"}) == DetectedLaunch("codex", "t-1")
    # A recognised harness that names no session: the launcher is still known, but
    # there is nothing to join a provenance record to.
    assert detect_launch({"CODEX_SANDBOX": "seatbelt"}) == DetectedLaunch("codex", None)
    # A plain shell stays exactly as unattributable as it has always been.
    assert detect_launch({"SHELL": "/bin/bash"}) == DetectedLaunch("unknown", None)


def test_detect_launch_degrades_an_ambient_session_id_it_cannot_use() -> None:
    """Nobody typed this value, so an unusable one must not fail a launch."""
    detected = detect_launch({"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "two\nlines"})
    assert detected == DetectedLaunch("claude-code", None)


def test_select_launch_prefers_explicit_values_over_the_environment() -> None:
    ambient = {"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "detected"}
    assert select_launch(launcher=None, session_id=None, environ=ambient) == DetectedLaunch(
        "claude-code", "detected"
    )
    assert select_launch(launcher=None, session_id="explicit", environ=ambient) == DetectedLaunch(
        "claude-code", "explicit"
    )
    # A detected session belongs to the harness that was detected. Pairing it with a
    # different one would join a run to a session that never launched it.
    assert select_launch(launcher="codex", session_id=None, environ=ambient) == DetectedLaunch(
        "codex", None
    )
    assert select_launch(launcher="codex", session_id="c-1", environ=ambient) == DetectedLaunch(
        "codex", "c-1"
    )


def test_caller_identity_is_none_without_a_named_session() -> None:
    assert caller_identity({"SHELL": "/bin/sh"}) is None
    assert caller_identity({"CODEX_SANDBOX": "seatbelt"}) is None
    identity = caller_identity({"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "s-1"})
    assert identity == LaunchIdentity("claude-code", "s-1")
    assert identity is not None
    # The label names the session without printing the session id itself.
    assert identity.label == f"claude-code:{session_fingerprint('s-1')}"
    assert "s-1" not in identity.label


def test_read_run_owner_resolves_a_recorded_launch(tmp_path: Path) -> None:
    launch_id = generate_launch_id()
    write_provenance(
        launch_id=launch_id,
        launcher="claude-code",
        launcher_session_id="s-1",
        repository_identity="local/app",
    )
    run_dir = _write_launch_record(tmp_path / "runs" / "owned", launch_id)

    owner = read_run_owner(run_dir)
    caller = LaunchIdentity("claude-code", "s-1")
    assert owner.launcher == "claude-code"
    assert owner.is_(caller)
    assert owner.label(caller) == "mine"
    # Another session of the same harness is still another planner's run.
    other = LaunchIdentity("claude-code", "s-2")
    assert not owner.is_(other)
    assert owner.label(other) == f"claude-code:{session_fingerprint('s-1')}"


@pytest.mark.parametrize("launch_id", [None, "not-a-launch-id"])
def test_a_run_without_a_usable_join_key_belongs_to_nobody(
    tmp_path: Path, launch_id: str | None
) -> None:
    run_dir = _write_launch_record(tmp_path / "runs" / f"run-{launch_id}", launch_id)
    owner = read_run_owner(run_dir)
    assert owner == UNKNOWN_OWNER
    assert owner.label(LaunchIdentity("claude-code", "s-1")) == "unknown"


def test_an_expired_or_missing_record_is_never_attributed_to_the_reader(tmp_path: Path) -> None:
    """Every run that predates provenance lands here, and none of them is mine."""
    launch_id = generate_launch_id()
    write_provenance(
        launch_id=launch_id,
        launcher="claude-code",
        launcher_session_id="s-1",
        repository_identity="local/app",
        started_at=(datetime.now(UTC) - timedelta(days=30)).isoformat(),
    )
    expired = _write_launch_record(tmp_path / "runs" / "expired", launch_id)
    unrecorded = _write_launch_record(tmp_path / "runs" / "unrecorded", generate_launch_id())

    caller = LaunchIdentity("claude-code", "s-1")
    for run_dir in (expired, unrecorded, tmp_path / "runs" / "absent"):
        owner = read_run_owner(run_dir)
        assert owner == UNKNOWN_OWNER
        assert not owner.is_(caller)
        assert owner.label(caller) == "unknown"


def test_ownership_needs_both_halves() -> None:
    """A caller with no session of its own is the owner of nothing."""
    owner = RunOwner("claude-code", LaunchIdentity("claude-code", "s-1"))
    assert not owner.is_(None)
    assert owner.label(None) == f"claude-code:{session_fingerprint('s-1')}"
    assert not UNKNOWN_OWNER.is_(None)
