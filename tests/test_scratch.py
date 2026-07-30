"""Deterministic edge coverage for scratch cleanup and capacity validation."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

import pytest

import orchestrator.scratch as scratch
from orchestrator.scratch import (
    DEFAULT_MIN_FREE_BYTES,
    MIN_FREE_BYTES_ENV,
    OWNER_LOCK_NAME,
    PYTEST_RETAINED_RUNS,
    UNREFERENCED_MIN_AGE_SECONDS,
    ScratchCapacityError,
    configured_min_free_bytes,
    main,
    owned_scratch_directory,
    require_scratch_capacity,
    sweep_scratch,
)

_NX_MANIFEST = {"devDependencies": {"nx": "^23.1.0"}}


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
) -> None:
    """Write one procfs entry naming the paths a live process is using."""
    _fabricate_proc_entry(proc_root, pid, start_token=1)
    entry = proc_root / str(pid)
    (entry / "cmdline").write_bytes("\0".join(cmdline).encode("utf-8"))
    if cwd is not None:
        (entry / "cwd").symlink_to(cwd)
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
    assert "third-party sweep skipped: lifecycle dispatch active" in output
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
