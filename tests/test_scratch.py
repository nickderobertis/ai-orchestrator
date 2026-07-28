"""Deterministic edge coverage for scratch cleanup and capacity validation."""

from __future__ import annotations

import fcntl
import os
import tempfile
import time
from pathlib import Path

import pytest

import orchestrator.scratch as scratch
from orchestrator.scratch import (
    DEFAULT_MIN_FREE_BYTES,
    MIN_FREE_BYTES_ENV,
    OWNER_LOCK_NAME,
    ScratchCapacityError,
    configured_min_free_bytes,
    main,
    owned_scratch_directory,
    require_scratch_capacity,
    sweep_scratch,
)


def _fabricate_proc_entry(proc_root: Path, pid: int, start_token: int) -> None:
    """Write one procfs stat record with a caller-chosen process start token."""
    entry = proc_root / str(pid)
    entry.mkdir(parents=True)
    fields = ["S", "1", "1", *(["0"] * 16), str(start_token)]
    (entry / "stat").write_text(f"{pid} (fake comm) {' '.join(fields)}\n", encoding="utf-8")


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
    impostor = tmp_path / "orchestrator-watchdog-file"
    impostor.write_text("not a scratch directory", encoding="utf-8")

    result = sweep_scratch(tmp_path, dry_run=True)

    assert result.candidates == (dead,)
    assert result.removed == ()
    assert result.watchdog_retained == (invalid, live)
    assert dead.exists() and invalid.exists() and live.exists() and impostor.exists()


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


def test_owner_probe_is_conservative_and_removal_cli_reports_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    owner = scratch._OwnerIdentity.current(os.getpid())
    assert owner is not None
    monkeypatch.setattr(os, "kill", lambda pid, signal: (_ for _ in ()).throw(PermissionError()))
    assert scratch._OwnerIdentity.parse(owner.render()) == owner
    assert owner.is_live()
    monkeypatch.setattr(
        os,
        "kill",
        lambda pid, signal: (_ for _ in ()).throw(ProcessLookupError()),
    )
    assert not owner.is_live()
    assert scratch._OwnerIdentity.current(os.getpid()) is None
    assert configured_min_free_bytes({}) == DEFAULT_MIN_FREE_BYTES
    dead = tmp_path / "orchestrator-watchdog-dead"
    dead.mkdir()
    (dead / "pid").write_text("123", encoding="utf-8")
    assert main(["--root", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "removed 1 directories" in output
    assert str(dead) not in output


def test_owned_directory_outlives_its_worker_pid_across_a_concurrent_sweep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The dispatcher keeps using its tree after the recorded worker pid is gone."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    with owned_scratch_directory() as directory:
        # Past worker exit: the pid the watchdog recorded no longer exists, while
        # the dispatcher is still reaping descendants and parsing its report.
        (directory / "pid").write_text("999999999\n", encoding="utf-8")
        (directory / "report.json").write_bytes(b"x" * 32)

        result = sweep_scratch(tmp_path)

        assert result.removed == ()
        assert result.reclaimed_bytes == 0
        assert result.watchdog_retained == (directory,)
        assert (directory / "report.json").exists()
        assert main(["--root", str(tmp_path)]) == 0
        assert "retained 1 watchdog directories not proven reclaimable" in capsys.readouterr().out

    assert not directory.exists()


def test_dead_owner_is_reclaimed_while_a_live_record_still_pins_its_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    with owned_scratch_directory() as held:
        record = (held / OWNER_LOCK_NAME).read_text(encoding="utf-8")
        crashed = tmp_path / "orchestrator-watchdog-crashed"
        crashed.mkdir()
        (crashed / OWNER_LOCK_NAME).write_text("999999999 1", encoding="utf-8")
        (crashed / "payload").write_bytes(b"z" * 41)
        unlocked = tmp_path / "orchestrator-watchdog-unlocked"
        unlocked.mkdir()
        (unlocked / OWNER_LOCK_NAME).write_text(record, encoding="utf-8")
        unreadable = tmp_path / "orchestrator-watchdog-unreadable"
        unreadable.mkdir()
        (unreadable / OWNER_LOCK_NAME).write_text("not-an-owner-record", encoding="utf-8")
        symlinked = tmp_path / "orchestrator-watchdog-symlinked"
        symlinked.mkdir()
        (symlinked / OWNER_LOCK_NAME).symlink_to(crashed / OWNER_LOCK_NAME)

        # A record naming a live owner pins its tree even where nothing holds the
        # lock, so a filesystem that ignores flock cannot strand a live dispatch.
        result = sweep_scratch(tmp_path)

    assert result.removed == (crashed, unreadable)
    assert result.reclaimed_bytes == 41 + len("999999999 1") + len("not-an-owner-record")
    # A lock that cannot be opened on its own terms proves nothing about its owner.
    assert set(result.watchdog_retained) == {held, symlinked, unlocked}


def test_recycled_owner_pid_does_not_pin_scratch_forever(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A live process that inherited the recorded pid must not look like the owner."""
    proc_root = tmp_path / "proc"
    monkeypatch.setenv("AI_ORCHESTRATOR_PROC_ROOT", str(proc_root))
    _fabricate_proc_entry(proc_root, os.getpid(), start_token=8888)
    reused = tmp_path / "orchestrator-watchdog-reused"
    reused.mkdir()
    (reused / OWNER_LOCK_NAME).write_text(f"{os.getpid()} 4242", encoding="utf-8")
    legacy = tmp_path / "orchestrator-watchdog-legacy"
    legacy.mkdir()
    (legacy / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    owned = tmp_path / "orchestrator-watchdog-owned"
    owned.mkdir()
    (owned / OWNER_LOCK_NAME).write_text(f"{os.getpid()} 8888", encoding="utf-8")

    result = sweep_scratch(tmp_path)

    # Only the recycled pid loses its claim: the record whose kernel-stamped start
    # token still matches keeps its tree, as does a legacy pid-only record whose
    # process remains identifiable.
    assert result.removed == (reused,)
    assert result.watchdog_retained == (legacy, owned)


def test_final_ownership_check_preserves_a_directory_claimed_mid_sweep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discovery and removal are separate passes; ownership is re-proven at removal."""
    first = tmp_path / "orchestrator-watchdog-a"
    second = tmp_path / "orchestrator-watchdog-b"
    for directory in (first, second):
        directory.mkdir()
        (directory / OWNER_LOCK_NAME).write_text("999999999 1", encoding="utf-8")
    measure = scratch._tree_size
    claimed: list[int] = []

    def claim_second_directory(path: Path) -> int:
        if path == first:
            fd = os.open(second / OWNER_LOCK_NAME, os.O_RDWR)
            fcntl.flock(fd, fcntl.LOCK_EX)
            claimed.append(fd)
        return measure(path)

    monkeypatch.setattr(scratch, "_tree_size", claim_second_directory)
    try:
        result = sweep_scratch(tmp_path)
    finally:
        for fd in claimed:
            os.close(fd)

    assert result.candidates == (first, second)
    assert result.removed == (first,)
    assert result.watchdog_retained == (second,)
    assert second.exists()


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
