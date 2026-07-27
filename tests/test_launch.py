"""Launching-session provenance: id minting, the out-of-repo record, and expiry."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from orchestrator.launch import (
    PROVENANCE_SCHEMA_VERSION,
    LaunchError,
    generate_launch_id,
    provenance_dir,
    provenance_path,
    read_provenance,
    resolve_launcher_kind,
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
