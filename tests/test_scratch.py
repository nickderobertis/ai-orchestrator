"""Deterministic edge coverage for scratch cleanup and capacity validation."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

import orchestrator.scratch as scratch
from orchestrator.scratch import (
    DEFAULT_MIN_FREE_BYTES,
    MIN_FREE_BYTES_ENV,
    ScratchCapacityError,
    _pid_is_live,
    configured_min_free_bytes,
    main,
    require_scratch_capacity,
    sweep_scratch,
)


def test_dry_run_is_inspectable_and_invalid_pid_is_left_alone(tmp_path: Path) -> None:
    dead = tmp_path / "orchestrator-watchdog-dead"
    dead.mkdir()
    (dead / "pid").write_text("999999999", encoding="utf-8")
    invalid = tmp_path / "orchestrator-watchdog-invalid"
    invalid.mkdir()
    (invalid / "pid").write_text("bad", encoding="utf-8")
    live = tmp_path / "orchestrator-watchdog-live"
    live.mkdir()
    (live / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")

    result = sweep_scratch(tmp_path, dry_run=True)

    assert result.candidates == (dead,)
    assert result.removed == ()
    assert dead.exists() and invalid.exists() and live.exists()


def test_known_patterns_are_extensible_and_unknown_old_scratch_is_preserved(
    tmp_path: Path,
) -> None:
    known = tmp_path / "screencomp-old"
    unknown = tmp_path / "other-old"
    known.mkdir()
    unknown.mkdir()
    old = time.time() - 100
    os.utime(known, (old, old))
    os.utime(unknown, (old, old))

    result = sweep_scratch(tmp_path, min_age_seconds=50)

    assert result.removed == (known,)
    assert unknown.exists()


def test_active_dispatch_lock_skips_third_party_but_removes_dead_watchdog(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stale = tmp_path / "visual-old"
    stale.mkdir()
    old = time.time() - 100
    os.utime(stale, (old, old))
    dead = tmp_path / "orchestrator-watchdog-dead"
    dead.mkdir()
    (dead / "pid").write_text("999999999", encoding="utf-8")

    with scratch._scratch_lock(tmp_path, exclusive=False):
        assert main(["--root", str(tmp_path), "--min-age-hours", "0"]) == 0

    output = capsys.readouterr().out
    assert "third-party sweep skipped: lifecycle dispatch active" in output
    assert stale.exists()
    assert not dead.exists()
    assert sweep_scratch(tmp_path, min_age_seconds=0).removed == (stale,)


def test_disappearing_candidate_is_ignored_during_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale = tmp_path / "screencomp-gone"
    stale.mkdir()
    old = time.time() - 100
    os.utime(stale, (old, old))
    monkeypatch.setattr(
        scratch.shutil,
        "rmtree",
        lambda path: (_ for _ in ()).throw(FileNotFoundError()),
    )

    result = sweep_scratch(tmp_path, min_age_seconds=0)

    assert result.removed == ()


def test_capacity_configuration_and_cli_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert configured_min_free_bytes({MIN_FREE_BYTES_ENV: "0"}) == 0
    with pytest.raises(ScratchCapacityError, match="integer byte count"):
        configured_min_free_bytes({MIN_FREE_BYTES_ENV: "nope"})
    with pytest.raises(ScratchCapacityError, match="at least zero"):
        configured_min_free_bytes({MIN_FREE_BYTES_ENV: "-1"})
    require_scratch_capacity(tmp_path, min_free_bytes=0)
    with pytest.raises(ScratchCapacityError, match="just sweep-scratch"):
        require_scratch_capacity(tmp_path, min_free_bytes=2**63 - 1)
    monkeypatch.setenv(MIN_FREE_BYTES_ENV, "0")
    assert main(["--root", str(tmp_path), "--dry-run"]) == 0
    assert "would remove 0 directories" in capsys.readouterr().out
    for value in ("-1", "nan", "inf"):
        with pytest.raises(SystemExit):
            main(["--root", str(tmp_path), "--min-age-hours", value])
    with pytest.raises(SystemExit):
        main(["--root", str(tmp_path / "missing")])


def test_pid_probe_is_conservative_and_removal_cli_reports_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert not _pid_is_live(0)
    assert _pid_is_live(os.getpid())
    monkeypatch.setattr(os, "kill", lambda pid, signal: (_ for _ in ()).throw(PermissionError()))
    assert _pid_is_live(123)
    monkeypatch.setattr(
        os,
        "kill",
        lambda pid, signal: (_ for _ in ()).throw(ProcessLookupError()),
    )
    assert not _pid_is_live(123)
    assert configured_min_free_bytes({}) == DEFAULT_MIN_FREE_BYTES
    dead = tmp_path / "orchestrator-watchdog-dead"
    dead.mkdir()
    (dead / "pid").write_text("123", encoding="utf-8")
    assert main(["--root", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "removed 1 directories" in output
    assert str(dead) not in output


def test_final_watchdog_liveness_check_preserves_active_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    watchdog = tmp_path / "orchestrator-watchdog-race"
    watchdog.mkdir()
    (watchdog / "pid").write_text("123", encoding="utf-8")
    monkeypatch.setattr(scratch, "_watchdog_is_orphaned", lambda path: False)
    result = sweep_scratch(tmp_path)
    assert result.removed == () and watchdog.exists()

    monkeypatch.setattr(scratch, "_watchdog_is_orphaned", lambda path: True)
    assert main(["--root", str(tmp_path), "--dry-run"]) == 0
    assert str(watchdog) in capsys.readouterr().out


def test_cli_translates_unexpected_filesystem_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        scratch,
        "sweep_scratch",
        lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError("denied")),
    )
    assert main(["--root", str(tmp_path)]) == 1
    assert "check path permissions" in capsys.readouterr().err
