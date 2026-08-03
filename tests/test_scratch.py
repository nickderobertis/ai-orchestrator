"""Deterministic edge coverage for scratch cleanup and capacity validation."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import tempfile
import time
import tomllib
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path

import pytest
from process_tree import await_orphaned, await_reaped, await_recorded_pid, write_reparented_leaving

import orchestrator.scratch as scratch
from orchestrator import REPO_ROOT
from orchestrator.scratch import (
    AGENT_STATUS_DIR_ENV,
    DEFAULT_MIN_FREE_BYTES,
    MIN_FREE_BYTES_ENV,
    ORPHAN_FAMILY,
    OWNER_LOCK_NAME,
    PYTEST_RETAINED_RUNS,
    UNREFERENCED_FAMILIES,
    UNREFERENCED_MIN_AGE_SECONDS,
    ScratchCapacityError,
    configured_min_free_bytes,
    main,
    orphaned_dispatch_processes,
    owned_scratch_directory,
    require_scratch_capacity,
    sweep_scratch,
)

_NX_MANIFEST = {"devDependencies": {"nx": "^23.1.0"}}


def test_pytest_retention_matches_the_configured_producer_contract() -> None:
    """The sweeper's retention window must move with pytest's explicit setting."""
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert (
        config["tool"]["pytest"]["ini_options"]["tmp_path_retention_count"] == PYTEST_RETAINED_RUNS
    )


def _fabricate_proc_entry(proc_root: Path, pid: int, start_token: int) -> None:
    """Write one procfs stat record with a caller-chosen process start token."""
    entry = proc_root / str(pid)
    entry.mkdir(parents=True)
    fields = ["S", "1", "1", *(["0"] * 16), str(start_token)]
    (entry / "stat").write_text(f"{pid} (fake comm) {' '.join(fields)}\n", encoding="utf-8")


def _fabricate_proc_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point procfs reads at a fabricated root that can still see this process.

    A root that cannot show the sweeping process is not a procfs the reference proof
    can be built on, so every fabricated one has to carry that entry.
    """
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    monkeypatch.setenv("AI_ORCHESTRATOR_PROC_ROOT", str(proc_root))
    _fabricate_proc_entry(proc_root, os.getpid(), start_token=7)
    (proc_root / str(os.getpid()) / "fd").mkdir()
    return proc_root


def _fabricate_proc_process(
    proc_root: Path,
    pid: int,
    *,
    cmdline: Sequence[str] = (),
    cwd: Path | None = None,
    open_files: Sequence[Path] = (),
    environ: Sequence[str] = (),
    executable: Path | None = None,
    mapped: Sequence[Path] = (),
) -> None:
    """Write one procfs entry naming the paths a live process is using."""
    _fabricate_proc_entry(proc_root, pid, start_token=1)
    entry = proc_root / str(pid)
    (entry / "cmdline").write_bytes("\0".join(cmdline).encode("utf-8"))
    (entry / "environ").write_bytes("\0".join(environ).encode("utf-8"))
    # One file-backed mapping per line, in the kernel's own column layout: a `dlopen`ed
    # binary shows up here and nowhere else, with no descriptor left open.
    (entry / "maps").write_text(
        "".join(
            f"e6{index:04x}000-e6{index:04x}fff r-xp 00000000 fd:01 {index}    {path}\n"
            for index, path in enumerate(mapped, start=1)
        ),
        encoding="utf-8",
    )
    if cwd is not None:
        (entry / "cwd").symlink_to(cwd)
    if executable is not None:
        (entry / "exe").symlink_to(executable)
    descriptors = entry / "fd"
    descriptors.mkdir()
    for number, target in enumerate(open_files):
        (descriptors / str(number)).symlink_to(target)


def _age(path: Path, seconds: float) -> None:
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


def _make_nx_install(
    path: Path,
    *,
    manifest: object = _NX_MANIFEST,
    node_modules: bool = True,
    extra: Sequence[str] = (),
    age_seconds: float = 60 * 60,
) -> Path:
    """Write the throwaway single-`nx` install shape Nx leaves behind per invocation."""
    path.mkdir()
    (path / "package.json").write_text(
        manifest if isinstance(manifest, str) else json.dumps(manifest), encoding="utf-8"
    )
    (path / "bun.lock").write_text("{}", encoding="utf-8")
    if node_modules:
        (path / "node_modules" / "nx").mkdir(parents=True)
    for name in extra:
        (path / name).mkdir()
    _age(path, age_seconds)
    return path


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
    assert "skipped families: third-party (lifecycle dispatch active)" in output
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


def test_nx_temp_installs_are_identified_by_shape_and_lookalikes_are_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`tmp-*` is far too generic to sweep on, so only the install shape decides."""
    _fabricate_proc_root(tmp_path, monkeypatch)
    root = tmp_path / "scratch"
    root.mkdir()
    disposable = _make_nx_install(root / "tmp-999999999-disposable")
    lookalikes = {
        "extra-dependency": _make_nx_install(
            root / "tmp-999999999-extra-dependency",
            manifest={"devDependencies": {"nx": "^23.1.0", "eslint": "^9"}},
        ),
        "named-project": _make_nx_install(
            root / "tmp-999999999-named-project",
            manifest={"name": "app", "devDependencies": {"nx": "^23.1.0"}},
        ),
        "own-sources": _make_nx_install(root / "tmp-999999999-own-sources", extra=("src",)),
        "no-install": _make_nx_install(root / "tmp-999999999-no-install", node_modules=False),
        "unparsable": _make_nx_install(root / "tmp-999999999-unparsable", manifest="{not json"),
        "manifest-list": _make_nx_install(root / "tmp-999999999-manifest-list", manifest=[]),
        "dependency-list": _make_nx_install(
            root / "tmp-999999999-dependency-list", manifest={"devDependencies": ["nx"]}
        ),
        # The pid Nx stamps into the name is a second signal: a node process can
        # finish installing and then require modules with nothing left open.
        "live-owner": _make_nx_install(root / f"tmp-{os.getpid()}-live-owner"),
        # The shape is only trusted where the glob already narrowed the scan.
        "unmatched-name": _make_nx_install(root / "workspace-999999999-unmatched"),
    }
    unreadable = _make_nx_install(root / "tmp-999999999-unreadable")
    unreadable.chmod(0o000)
    # A symlink is never a candidate, whatever the shape at the other end of it.
    linked = root / "tmp-999999999-linked"
    linked.symlink_to(disposable)

    try:
        result = sweep_scratch(root)
    finally:
        unreadable.chmod(0o755)

    assert result.removed == (disposable,)
    assert all(path.exists() for path in lookalikes.values()), lookalikes
    assert unreadable.exists() and linked.is_symlink()


def _make_nx_native_cache(
    path: Path, *, entries: Sequence[str] = ("23.1.0-nx.linux-arm64-gnu.node",)
) -> Path:
    """Write the shape Nx leaves behind: copied native binaries and nothing else."""
    path.mkdir()
    for name in entries:
        (path / name).write_bytes(b"\x7fELF")
    _age(path, 60 * 60)
    return path


def test_nx_native_caches_are_swept_by_shape_and_lookalikes_are_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stranded copy of Nx's 22 MB native binary is the largest family on this host.

    Its key is a digest of a workspace root that no longer exists, so nothing will ever
    reuse it; only the exact name shape and an all-`.node` content decide.
    """
    _fabricate_proc_root(tmp_path, monkeypatch)
    root = tmp_path / "scratch"
    root.mkdir()
    stranded = _make_nx_native_cache(root / "nx-native-file-cache-000d3d4")
    emptied = _make_nx_native_cache(root / "nx-native-file-cache-abcdef0", entries=())
    lookalikes = {
        "short-key": _make_nx_native_cache(root / "nx-native-file-cache-000d3d"),
        "long-key": _make_nx_native_cache(root / "nx-native-file-cache-000d3d4a"),
        "non-hex-key": _make_nx_native_cache(root / "nx-native-file-cache-zzzzzzz"),
        "other-content": _make_nx_native_cache(
            root / "nx-native-file-cache-0123456",
            entries=("23.1.0-nx.linux-arm64-gnu.node", "notes.txt"),
        ),
    }
    nested = _make_nx_native_cache(root / "nx-native-file-cache-1234567")
    (nested / "subdirectory").mkdir()
    lookalikes["nested-directory"] = nested
    unreadable = _make_nx_native_cache(root / "nx-native-file-cache-7654321")
    unreadable.chmod(0o000)
    linked_binary = _make_nx_native_cache(root / "nx-native-file-cache-89abcde", entries=())
    (linked_binary / "23.1.0-nx.linux-arm64-gnu.node").symlink_to(
        stranded / "23.1.0-nx.linux-arm64-gnu.node"
    )
    lookalikes["symlinked-binary"] = linked_binary
    impostor = root / "nx-native-file-cache-fedcba9"
    impostor.write_text("not a cache directory", encoding="utf-8")
    linked = root / "nx-native-file-cache-0000000"
    linked.symlink_to(stranded)

    try:
        result = sweep_scratch(root)
    finally:
        unreadable.chmod(0o755)

    assert set(result.removed) == {stranded, emptied}
    assert all(path.exists() for path in lookalikes.values()), lookalikes
    assert unreadable.exists() and impostor.exists() and linked.is_symlink()


def test_a_memory_mapped_native_cache_is_never_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Nx `dlopen`s its cached binary, so the mapping is the only reference there is.

    The mapped cache below is named in no argv, no environment, no link, and no open
    descriptor — exactly what a running `nx` looks like. Without the mapping channel
    the sweep would delete the binary out from under it.
    """
    proc_root = _fabricate_proc_root(tmp_path, monkeypatch)
    root = tmp_path / "scratch"
    root.mkdir()
    mapped = _make_nx_native_cache(root / "nx-native-file-cache-02c850c")
    stranded = _make_nx_native_cache(root / "nx-native-file-cache-000d3d4")
    _fabricate_proc_process(proc_root, 4242, mapped=[mapped / "23.1.0-nx.linux-arm64-gnu.node"])

    assert main(["--root", str(root)]) == 0

    output = capsys.readouterr().out
    assert mapped.exists()
    assert not stranded.exists()
    assert "removed 1 directories" in output
    assert "retained 1 directories referenced by live processes" in output


def test_environment_and_executable_references_protect_scratch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A path a process was handed or is executing is in use whatever argv says."""
    proc_root = _fabricate_proc_root(tmp_path, monkeypatch)
    root = tmp_path / "scratch"
    root.mkdir()
    configured, running, stale = (
        root / "onejudge-python-configured",
        root / "onejudge-python-running",
        root / "onejudge-python-stale",
    )
    for directory in (configured, running, stale):
        directory.mkdir()
        _age(directory, 60 * 60)
    (running / "harness").write_text("#!/bin/sh\n", encoding="utf-8")
    _fabricate_proc_process(
        proc_root,
        4242,
        environ=[f"ONEJUDGE_CONFIG={configured}/effective.onejudge.json", "HOME=/home/agent"],
        executable=running / "harness",
    )

    result = sweep_scratch(root)

    assert result.removed == (stale,)
    assert set(result.referenced_retained) == {configured, running}


def test_report_names_every_family_it_swept_and_every_one_it_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`reclaimed 0 bytes` must never be able to mean a family was left unswept."""
    _fabricate_proc_root(tmp_path, monkeypatch)
    root = tmp_path / "scratch"
    root.mkdir()

    assert main(["--root", str(root)]) == 0
    quiescent = capsys.readouterr().out
    assert "reclaimed 0 bytes" in quiescent
    for family in ("watchdog", "third-party", *(family.name for family in UNREFERENCED_FAMILIES)):
        assert family in quiescent.split("swept families: ", 1)[1]
    assert (
        f"skipped families: {scratch.RUN_WORKTREE_FAMILY} ({scratch.RUN_WORKTREE_SKIP_REASON})"
    ) in quiescent

    with scratch._scratch_lock(root, exclusive=False):
        assert main(["--root", str(root)]) == 0
    during_dispatch = capsys.readouterr().out
    swept, _, skipped = during_dispatch.partition("; skipped families: ")
    assert "nx-native-file-cache" in swept
    assert skipped.startswith("third-party (lifecycle dispatch active)")
    assert f"{scratch.RUN_WORKTREE_FAMILY} ({scratch.RUN_WORKTREE_SKIP_REASON})" in skipped

    blind = tmp_path / "not-procfs"
    blind.mkdir()
    monkeypatch.setenv("AI_ORCHESTRATOR_PROC_ROOT", str(blind))
    assert main(["--root", str(root)]) == 0
    unprovable = capsys.readouterr().out
    _, _, unprovable_skipped = unprovable.partition("; skipped families: ")
    for family in UNREFERENCED_FAMILIES:
        assert f"{family.name} (no live process could be proven done with it)" in unprovable_skipped
    assert (
        f"{scratch.RUN_WORKTREE_FAMILY} ({scratch.RUN_WORKTREE_SKIP_REASON})" in unprovable_skipped
    )


def test_pytest_runs_are_swept_below_pytest_s_own_retention_and_live_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The nested `pytest-of-*/pytest-<n>` layout, judged by pytest's own conventions."""
    _fabricate_proc_root(tmp_path, monkeypatch)
    root = tmp_path / "scratch"
    root.mkdir()
    parent = root / "pytest-of-alice"
    parent.mkdir()
    runs = {}
    for number in range(1, 8):
        run = parent / f"pytest-{number}"
        run.mkdir()
        runs[number] = run
    (parent / "pytest-current").symlink_to(runs[7])
    # A numbered name that is really a symlink must not shift the retention window.
    (parent / "pytest-9").symlink_to(runs[7])
    (runs[1] / ".lock").write_text("999999999", encoding="utf-8")
    (runs[2] / ".lock").write_text(str(os.getpid()), encoding="utf-8")
    (runs[3] / ".lock").write_text("not-a-pid", encoding="utf-8")
    (runs[5] / ".lock").write_text("999999999", encoding="utf-8")
    for run in runs.values():
        _age(run, 60 * 60)
    unrelated = parent / "garbage-6f0a"
    unrelated.mkdir()
    _age(unrelated, 60 * 60)
    unnumbered = parent / "pytest-scratchpad"
    unnumbered.mkdir()
    _age(unnumbered, 60 * 60)
    # A session whose newer runs were deleted by hand still owns `pytest-current`.
    other = root / "pytest-of-bob"
    other.mkdir()
    orphaned = {}
    for number in range(0, 6):
        run = other / f"pytest-{number}"
        run.mkdir()
        orphaned[number] = run
    # pytest treats a lock it cannot read as proof the run is not deletable.
    unreadable_lock = orphaned[0] / ".lock"
    unreadable_lock.write_text("999999999", encoding="utf-8")
    unreadable_lock.chmod(0o000)
    for run in orphaned.values():
        _age(run, 60 * 60)
    (other / "pytest-current").symlink_to(orphaned[1])
    linked_parent = root / "pytest-of-link"
    linked_parent.symlink_to(parent)
    sealed_parent = root / "pytest-of-sealed"
    sealed_parent.mkdir()
    (sealed_parent / "pytest-1").mkdir()
    sealed_parent.chmod(0o000)
    impostor = root / "pytest-of-file"
    impostor.write_text("not a pytest root", encoding="utf-8")

    try:
        result = sweep_scratch(root)
    finally:
        sealed_parent.chmod(0o755)
        unreadable_lock.chmod(0o600)

    # pytest keeps the newest three itself, so the sweep starts below them; the runs
    # holding a live `.lock` pid, an unreadable lock, and a lock that is not a pid are
    # kept, as is the one an operator's `pytest-current` still points at.
    assert result.removed == (runs[1], runs[4], orphaned[2])
    assert [number for number in runs if runs[number].exists()] == [2, 3, 5, 6, 7]
    assert [number for number in orphaned if orphaned[number].exists()] == [0, 1, 3, 4, 5]
    assert (parent / "pytest-current").resolve() == runs[7]
    assert unrelated.exists() and unnumbered.exists() and impostor.exists()
    assert (sealed_parent / "pytest-1").exists() and linked_parent.is_symlink()
    assert PYTEST_RETAINED_RUNS == 3


def test_live_process_references_are_protected_while_a_dispatch_holds_the_scratch_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Non-reference, not quiescence, is what makes removal safe during a dispatch."""
    proc_root = _fabricate_proc_root(tmp_path, monkeypatch)
    root = tmp_path / "scratch"
    root.mkdir()
    named, working, opened, stale = (
        root / "onejudge-python-named",
        root / "onejudge-python-working",
        root / "onejudge-python-opened",
        root / "onejudge-python-stale",
    )
    for directory in (named, working, opened, stale):
        directory.mkdir()
        (directory / "effective.onejudge.json").write_text("{}", encoding="utf-8")
        _age(directory, 60 * 60)
    third_party = root / "visual-old"
    third_party.mkdir()
    _age(third_party, 60 * 60)
    impostor = root / "onejudge-python-file"
    impostor.write_text("not a scratch directory", encoding="utf-8")
    linked = root / "onejudge-python-linked"
    linked.symlink_to(working)
    _fabricate_proc_process(
        proc_root,
        4242,
        cmdline=["onejudge", "run", f"{named}/effective.onejudge.json", "--format", "json"],
        cwd=working,
        open_files=[opened / "effective.onejudge.json"],
    )
    (proc_root / "self").mkdir()

    with scratch._scratch_lock(root, exclusive=False):
        assert main(["--root", str(root)]) == 0

    output = capsys.readouterr().out
    assert not stale.exists()
    assert named.exists() and working.exists() and opened.exists()
    assert impostor.exists() and linked.is_symlink()
    # The 24h third-party rule and its dispatch-active skip are untouched.
    assert third_party.exists()
    assert "skipped families: third-party (lifecycle dispatch active)" in output
    assert "removed 1 directories" in output
    assert "retained 3 directories referenced by live processes" in output


def test_reference_proof_is_retaken_before_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory a process starts using between discovery and removal is kept.

    Discovery precedes the whole third-party pass, so the proof is retaken against
    the freshest procfs state. No external process can open that in-process window on
    demand, so this drives the sweep through it directly.
    """
    # llmlint: ignore[changed_behavior_has_e2e] in-process interleaving only
    proc_root = _fabricate_proc_root(tmp_path, monkeypatch)
    root = tmp_path / "scratch"
    root.mkdir()
    claimed = root / "onejudge-python-claimed"
    touched = root / "onejudge-python-touched"
    vanished = root / "onejudge-python-vanished"
    doomed = root / "onejudge-python-doomed"
    for directory in (claimed, touched, vanished, doomed):
        directory.mkdir()
        _age(directory, 60 * 60)
    original = scratch._referenced_scratch_paths
    proofs = 0

    def claim_before_the_second_proof(scratch_root: Path) -> frozenset[str]:
        nonlocal proofs
        proofs += 1
        if proofs == 2:
            _fabricate_proc_process(proc_root, 4242, cmdline=[str(claimed)])
            _age(touched, 0)
            vanished.rmdir()
        return original(scratch_root)

    monkeypatch.setattr(scratch, "_referenced_scratch_paths", claim_before_the_second_proof)

    result = sweep_scratch(root)

    assert result.candidates == (claimed, doomed, touched, vanished)
    assert result.removed == (doomed,)
    assert result.referenced_retained == (claimed,)
    assert claimed.exists() and touched.exists() and not vanished.exists()


def test_unreferenced_families_use_their_own_minimum_age(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """These families are eligible in minutes; the 24h third-party default is unchanged."""
    _fabricate_proc_root(tmp_path, monkeypatch)
    root = tmp_path / "scratch"
    root.mkdir()
    eligible = _make_nx_install(
        root / "tmp-999999999-eligible", age_seconds=UNREFERENCED_MIN_AGE_SECONDS + 60
    )
    just_made = _make_nx_install(
        root / "tmp-999999999-just-made", age_seconds=UNREFERENCED_MIN_AGE_SECONDS - 60
    )
    third_party = root / "visual-recent"
    third_party.mkdir()
    _age(third_party, UNREFERENCED_MIN_AGE_SECONDS + 60)

    assert sweep_scratch(root).removed == (eligible,)
    assert just_made.exists() and third_party.exists()
    # An explicit shorter age still means "now"; the third-party rule follows it too.
    assert set(sweep_scratch(root, min_age_seconds=0).removed) == {just_made, third_party}


def test_a_procfs_that_cannot_see_this_process_withdraws_the_whole_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """ "Cannot ask" is not "nothing is referenced", and must not authorize removal."""
    blind = tmp_path / "not-procfs"
    blind.mkdir()
    monkeypatch.setenv("AI_ORCHESTRATOR_PROC_ROOT", str(blind))
    root = tmp_path / "scratch"
    root.mkdir()
    install = _make_nx_install(root / "tmp-999999999-unprovable")
    onejudge_scratch = root / "onejudge-python-unprovable"
    onejudge_scratch.mkdir()
    _age(onejudge_scratch, 60 * 60)
    dead_watchdog = root / "orchestrator-watchdog-dead"
    dead_watchdog.mkdir()
    (dead_watchdog / "pid").write_text("999999999", encoding="utf-8")

    assert main(["--root", str(root)]) == 0

    output = capsys.readouterr().out
    assert install.exists() and onejudge_scratch.exists()
    # The watchdog proof stands on its own lock, so its accounting is unaffected.
    assert not dead_watchdog.exists()
    assert "removed 1 directories" in output
    assert f"no usable procfs at {blind}" in output


def test_a_proof_withdrawn_between_discovery_and_removal_keeps_every_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Removal re-asks, and an unanswerable second question retains rather than deletes."""
    # llmlint: ignore[changed_behavior_has_e2e] in-process interleaving only
    proc_root = _fabricate_proc_root(tmp_path, monkeypatch)
    root = tmp_path / "scratch"
    root.mkdir()
    candidate = root / "onejudge-python-candidate"
    candidate.mkdir()
    _age(candidate, 60 * 60)
    original = scratch._referenced_scratch_paths
    proofs = 0

    def blind_the_second_proof(scratch_root: Path) -> frozenset[str] | None:
        nonlocal proofs
        proofs += 1
        if proofs == 2:
            (proc_root / str(os.getpid()) / "stat").unlink()
            (proc_root / str(os.getpid()) / "fd").rmdir()
            (proc_root / str(os.getpid())).rmdir()
        return original(scratch_root)

    monkeypatch.setattr(scratch, "_referenced_scratch_paths", blind_the_second_proof)

    result = sweep_scratch(root)

    assert result.candidates == (candidate,)
    assert result.removed == ()
    assert result.reference_proof_unavailable is True
    assert candidate.exists()


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


def _fabricate_stamped_process(proc_root: Path, pid: int, status_dir: Path | None) -> None:
    """Write a procfs entry for a process carrying (or lacking) a dispatch stamp."""
    stamp = () if status_dir is None else (f"{AGENT_STATUS_DIR_ENV}={status_dir}",)
    _fabricate_proc_process(proc_root, pid, environ=("PATH=/usr/bin", *stamp, "TERM=dumb"))


def test_only_a_stamp_naming_a_finished_dispatch_claims_a_reparented_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every way the stamp can fail to prove ownership, decided without signalling anyone.

    `orphaned_dispatch_processes` is asked directly rather than through `sweep_scratch`
    so these fabricated pid numbers are only ever *classified*. A unit test that let
    the sweep signal them would be aiming real signals at whatever process on this host
    happened to hold the number.
    """
    proc_root = _fabricate_proc_root(tmp_path, monkeypatch)
    root = tmp_path / "scratch"
    root.mkdir()
    finished = root / "orchestrator-watchdog-finished"
    (finished / "agent").mkdir(parents=True)
    (finished / OWNER_LOCK_NAME).write_text("999999999 1", encoding="utf-8")
    owned = root / "orchestrator-watchdog-owned"
    (owned / "agent").mkdir(parents=True)
    (owned / OWNER_LOCK_NAME).write_text(f"{os.getpid()} 7", encoding="utf-8")
    linked = root / "orchestrator-watchdog-linked"
    linked.symlink_to(finished)
    outside = tmp_path / "elsewhere" / "orchestrator-watchdog-other-root"
    outside.mkdir(parents=True)

    _fabricate_stamped_process(proc_root, 4242, finished / "agent")
    # A dispatch whose whole scratch tree is gone ran to completion.
    _fabricate_stamped_process(proc_root, 4243, root / "orchestrator-watchdog-vanished" / "agent")
    _fabricate_stamped_process(proc_root, 4244, owned / "agent")
    _fabricate_stamped_process(proc_root, 4245, None)
    _fabricate_stamped_process(proc_root, 4246, outside / "agent")
    # A name under the swept root that no `owned_scratch_directory` could have made.
    _fabricate_stamped_process(proc_root, 4247, root / "someone-elses-tree" / "agent")
    # The prefix alone is not a directory this harness created.
    _fabricate_stamped_process(proc_root, 4248, root / "orchestrator-watchdog-" / "agent")
    # Neither is a path that merely lands somewhere inside a watchdog tree.
    _fabricate_stamped_process(proc_root, 4251, finished / "agent" / "deeper")
    _fabricate_stamped_process(proc_root, 4252, finished)
    # A lock reachable only through a symlink never authorizes anything.
    _fabricate_stamped_process(proc_root, 4249, linked / "agent")
    # The stamp is read as a whole entry, so a value that merely contains its name is not it.
    _fabricate_proc_process(proc_root, 4250, environ=(f"NOTES=see {AGENT_STATUS_DIR_ENV}=x",))

    assert orphaned_dispatch_processes(root) == (4242, 4243)


def test_the_sweeping_process_and_its_own_ancestry_are_never_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sweep running inside a dispatch cannot reach the run it is part of."""
    proc_root = _fabricate_proc_root(tmp_path, monkeypatch)
    root = tmp_path / "scratch"
    root.mkdir()
    finished = root / "orchestrator-watchdog-finished"
    (finished / "agent").mkdir(parents=True)
    (finished / OWNER_LOCK_NAME).write_text("999999999 1", encoding="utf-8")
    # Give this process a parent that is itself stamped for that finished dispatch,
    # then stamp this process too: neither may be claimed however the proof reads.
    parent = 4300
    _fabricate_stamped_process(proc_root, parent, finished / "agent")
    entry = proc_root / str(os.getpid())
    (entry / "environ").write_bytes(f"{AGENT_STATUS_DIR_ENV}={finished / 'agent'}".encode())
    (entry / "stat").write_text(
        f"{os.getpid()} (self) S {parent} 1 " + " ".join(["0"] * 16) + " 7\n", encoding="utf-8"
    )

    assert orphaned_dispatch_processes(root) == ()


def test_an_ancestry_walk_stops_at_whatever_procfs_can_no_longer_tell_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A vanished or unparseable stat record ends the walk rather than the sweep."""
    proc_root = _fabricate_proc_root(tmp_path, monkeypatch)
    root = tmp_path / "scratch"
    root.mkdir()
    entry = proc_root / str(os.getpid())
    (entry / "stat").unlink()
    assert orphaned_dispatch_processes(root) == ()
    (entry / "stat").write_text("1 (truncated)\n", encoding="utf-8")
    assert orphaned_dispatch_processes(root) == ()


def test_an_unreadable_environment_leaves_a_process_unclaimed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another user's process answers nothing, so it is nothing this sweep may act on."""
    proc_root = _fabricate_proc_root(tmp_path, monkeypatch)
    root = tmp_path / "scratch"
    root.mkdir()
    _fabricate_proc_entry(proc_root, 4400, start_token=1)

    assert orphaned_dispatch_processes(root) == ()


def test_the_orphan_family_is_reported_swept_or_skipped_with_its_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`reaped 0` must never be able to mean the question was never asked."""
    _fabricate_proc_root(tmp_path, monkeypatch)
    root = tmp_path / "scratch"
    root.mkdir()

    assert main(["--root", str(root)]) == 0
    assert ORPHAN_FAMILY in capsys.readouterr().out.partition("swept families: ")[2]

    blind = tmp_path / "not-procfs"
    blind.mkdir()
    monkeypatch.setenv("AI_ORCHESTRATOR_PROC_ROOT", str(blind))
    assert main(["--root", str(root)]) == 0
    skipped = capsys.readouterr().out.partition("; skipped families: ")[2]
    assert f"{ORPHAN_FAMILY} (no usable procfs" in skipped


def test_a_finished_dispatchs_leaving_is_terminated_where_no_parentage_remains(
    tmp_path: Path,
) -> None:
    """The reap itself, against a process init has already adopted.

    Real procfs and a real process rather than a fabricated root: the point of the
    whole mechanism is that it acts on something the kernel actually reports, and a
    fabricated pid number is one this host may have handed to a stranger.
    """
    root = tmp_path / "scratch"
    root.mkdir()
    finished = root / "orchestrator-watchdog-finished"
    (finished / "agent").mkdir(parents=True)
    (finished / OWNER_LOCK_NAME).write_text("999999999 1", encoding="utf-8")
    marker = tmp_path / "leaving.pid"
    intermediate = subprocess.Popen(
        [sys.executable, str(write_reparented_leaving(tmp_path)), str(marker)],
        env={**os.environ, AGENT_STATUS_DIR_ENV: os.fspath(finished / "agent")},
    )
    assert intermediate.wait(timeout=60) == 0
    leaving = await_recorded_pid(marker)
    assert await_orphaned(leaving)

    try:
        result = sweep_scratch(root)
    finally:
        with suppress(ProcessLookupError):
            os.kill(leaving, 9)

    assert result.reaped_processes == (leaving,)
    assert ORPHAN_FAMILY in result.swept_families
    assert await_reaped(leaving)
