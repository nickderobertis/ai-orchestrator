"""Real command-surface journeys for host scratch protection."""

from __future__ import annotations

import json
import multiprocessing
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import install_pre_push_hook

from orchestrator import REPO_ROOT
from orchestrator.lifecycle import run_repo_task
from orchestrator.scratch import (
    MIN_FREE_BYTES_ENV,
    OWNER_LOCK_NAME,
    UNREFERENCED_FAMILIES,
    WATCHDOG_PATTERN,
    sweep_scratch,
)
from orchestrator.workspace import Workspace

_BLOCKING_GATE = """
import os
import sys
import time
from pathlib import Path

candidate, ready, release = map(Path, sys.argv[1:])
candidate.mkdir(exist_ok=True)
(candidate / "payload").write_text("in-flight", encoding="utf-8")
old = time.time() - 48 * 60 * 60
os.utime(candidate, (old, old))
ready.write_text("ready", encoding="utf-8")
while not release.exists():
    time.sleep(0.02)
"""


_LIVE_REFERENCE_HOLDER = """
import ctypes
import json
import os
import sys
import time
from pathlib import Path

PROT_READ, MAP_PRIVATE = 0x1, 0x02

# argv is one of the reference channels the sweep reads; the rest arrive over stdin
# so that this process names each scratch path exactly once.
named = Path(sys.argv[1])
plan = json.loads(sys.stdin.readline())
os.chdir(plan["cwd"])
handle = open(plan["open"], "rb")
# Nx loads its cached native binary with `dlopen`, which maps the file and keeps no
# descriptor, so the mapping is the only place the path still appears. `mmap.mmap`
# would not reproduce that: CPython dups the descriptor and keeps it open. Calling
# mmap(2) directly and closing the descriptor leaves exactly a loader's footprint.
libc = ctypes.CDLL(None, use_errno=True)
libc.mmap.restype = ctypes.c_void_p
libc.mmap.argtypes = [
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_long,
]
mapped = Path(plan["mapped"])
descriptor = os.open(mapped, os.O_RDONLY)
try:
    address = libc.mmap(
        None, mapped.stat().st_size, PROT_READ, MAP_PRIVATE, descriptor, 0
    )
    if address in (None, ctypes.c_void_p(-1).value):
        raise OSError(ctypes.get_errno(), "could not map the cached binary")
finally:
    os.close(descriptor)
Path(plan["pidfile"]).write_text(str(os.getpid()), encoding="utf-8")
release = Path(plan["release"])
while not release.exists():
    time.sleep(0.02)
handle.close()
assert named
"""


def _write_nx_install(path: Path, *, dependencies: dict[str, str]) -> Path:
    """Write the throwaway single-`nx` install shape Nx leaves behind per invocation."""
    (path / "node_modules" / "nx").mkdir(parents=True)
    (path / "package.json").write_text(
        json.dumps({"devDependencies": dependencies}), encoding="utf-8"
    )
    (path / "bun.lock").write_text("{}", encoding="utf-8")
    return path


def _write_nx_native_cache(path: Path, *, entries: tuple[str, ...]) -> Path:
    """Write the shape Nx leaves behind per workspace root: copied native binaries."""
    path.mkdir()
    for name in entries:
        (path / name).write_bytes(b"\x7fELF" + b"\0" * 4096)
    return path


def _age(path: Path) -> None:
    old = time.time() - 48 * 60 * 60
    os.utime(path, (old, old))


def _install_blocking_pre_push_gate(
    checkout: Path, candidate: Path, ready: Path, release: Path
) -> None:
    """Hold the lifecycle open inside its real merge-path gate.

    The repository's `pre-push` hook is where the gate runs now, so that is the
    only point at which a dispatch is genuinely in flight with scratch to protect.
    """
    argv = " ".join(
        shlex.quote(part)
        for part in (
            sys.executable,
            "-c",
            _BLOCKING_GATE,
            str(candidate),
            str(ready),
            str(release),
        )
    )
    install_pre_push_hook(checkout, argv)


def _run_lifecycle_with_blocking_gate(
    origin: str,
    canonical: str,
    workspace_root: str,
    base_path: str,
    persona_dir: str,
    scratch_root: str,
    result_path: str,
) -> None:
    os.environ["TMPDIR"] = scratch_root
    tempfile.tempdir = None
    workspace = Workspace(
        workspace_root,
        resolver=lambda _spec: Path(canonical),
        workflow="local",
    )
    result = run_repo_task(
        origin,
        "complete-now write-unique-change: synchronized scratch sweep",
        "engineer",
        workspace=workspace,
        base_path=base_path,
        persona_dir=persona_dir,
        recorded_gate=["true"],
        repo_type="single-owner",
    )
    Path(result_path).write_text(
        json.dumps({"ok": result.ok, "outcome": result.outcome, "detail": result.detail}),
        encoding="utf-8",
    )


def _kernel_start_token(pid: int) -> str:
    """Read a process's start time (procfs field 22) without the code under test."""
    fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
    return fields[19]


def _worker_pid_is_gone(directory: Path) -> bool:
    """Report whether the pid this dispatch recorded for its worker has exited."""
    try:
        pid = int((directory / "pid").read_text(encoding="utf-8").split()[0])
    except (OSError, IndexError, ValueError):
        return False
    return not Path(f"/proc/{pid}").exists()


def _wait_for_path(path: Path, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() >= deadline:
            pytest.fail(f"timed out waiting for {path}")
        time.sleep(0.02)


def test_sweep_recipe_reclaims_orphans_and_preserves_live_scratch(tmp_path: Path) -> None:
    dead = tmp_path / "orchestrator-watchdog-dead"
    dead.mkdir()
    (dead / "pid").write_text("999999999\n", encoding="utf-8")
    (dead / "payload").write_bytes(b"x" * 17)
    live = tmp_path / "orchestrator-watchdog-live"
    live.mkdir()
    (live / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    malformed = tmp_path / "orchestrator-watchdog-malformed"
    malformed.mkdir()
    (malformed / "pid").write_text("bad\n", encoding="utf-8")
    crashed = tmp_path / "orchestrator-watchdog-crashed"
    crashed.mkdir()
    (crashed / OWNER_LOCK_NAME).write_text("999999999 1", encoding="utf-8")
    recycled = tmp_path / "orchestrator-watchdog-recycled"
    recycled.mkdir()
    recycled_record = f"{os.getpid()} 1"
    (recycled / OWNER_LOCK_NAME).write_text(recycled_record, encoding="utf-8")
    owned = tmp_path / "orchestrator-watchdog-owned"
    owned.mkdir()
    (owned / OWNER_LOCK_NAME).write_text(
        f"{os.getpid()} {_kernel_start_token(os.getpid())}", encoding="utf-8"
    )
    # pid 1 belongs to another user, so the sweep cannot signal it and must decide
    # on its procfs identity alone — the case that once pinned any recycled pid.
    foreign = tmp_path / "orchestrator-watchdog-foreign"
    foreign.mkdir()
    (foreign / OWNER_LOCK_NAME).write_text(f"1 {_kernel_start_token(1)}", encoding="utf-8")
    foreign_recycled = tmp_path / "orchestrator-watchdog-foreign-recycled"
    foreign_recycled.mkdir()
    foreign_record = "1 1"
    (foreign_recycled / OWNER_LOCK_NAME).write_text(foreign_record, encoding="utf-8")
    garbled = tmp_path / "orchestrator-watchdog-garbled"
    garbled.mkdir()
    (garbled / OWNER_LOCK_NAME).write_text("not-an-owner-record", encoding="utf-8")
    symlinked = tmp_path / "orchestrator-watchdog-symlinked"
    symlinked.mkdir()
    (symlinked / OWNER_LOCK_NAME).symlink_to(owned / OWNER_LOCK_NAME)
    old_third_party = tmp_path / "oneharness-sdk-old"
    old_third_party.mkdir()
    (old_third_party / "payload").write_bytes(b"y" * 19)
    old = time.time() - 48 * 60 * 60
    os.utime(old_third_party, (old, old))
    recent = tmp_path / "playwright-active"
    recent.mkdir()

    result = subprocess.run(
        ["just", "sweep-scratch", "--root", str(tmp_path)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    assert not dead.exists()
    assert not crashed.exists()
    assert not recycled.exists()
    assert not garbled.exists()
    assert not foreign_recycled.exists()
    assert not old_third_party.exists()
    assert live.exists()
    assert malformed.exists()
    assert owned.exists()
    assert foreign.exists()
    # A lock reachable only through a symlink is never trusted to name its owner.
    assert symlinked.exists()
    assert recent.exists()
    assert "removed 6 directories" in result.stdout
    reclaimed = (
        46
        + len("999999999 1")
        + len(recycled_record)
        + len(foreign_record)
        + len("not-an-owner-record")
    )
    assert f"reclaimed {reclaimed} bytes" in result.stdout
    assert "retained 5 watchdog directories not proven reclaimable" in result.stdout


def test_third_party_sweep_skips_inflight_lifecycle_then_reclaims(
    tmp_path: Path,
    bare_origin: Callable[..., Path],
    command_base: Callable[..., Path],
    personas_dir: Path,
) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    candidate = scratch / "visual-inflight"
    dead_watchdog = scratch / "orchestrator-watchdog-dead"
    dead_watchdog.mkdir()
    (dead_watchdog / "pid").write_text("999999999\n", encoding="utf-8")
    ready = tmp_path / "gate-ready"
    release = tmp_path / "gate-release"
    result_path = tmp_path / "lifecycle-result.json"
    origin = bare_origin()
    canonical = tmp_path / "canonical"
    subprocess.run(
        ["git", "clone", str(origin), str(canonical)],
        text=True,
        capture_output=True,
        check=True,
    )
    _install_blocking_pre_push_gate(canonical, candidate, ready, release)
    process = multiprocessing.Process(
        target=_run_lifecycle_with_blocking_gate,
        args=(
            str(origin),
            str(canonical),
            str(tmp_path / "worktrees"),
            str(command_base()),
            str(personas_dir),
            str(scratch),
            str(result_path),
        ),
    )
    process.start()
    try:
        _wait_for_path(ready)
        during = subprocess.run(
            ["just", "sweep-scratch", "--root", str(scratch)],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        assert candidate.exists()
        assert not dead_watchdog.exists()
        assert "skipped families: third-party (lifecycle dispatch active)" in during.stdout
    finally:
        release.write_text("release", encoding="utf-8")
        process.join(60)
        if process.is_alive():
            process.terminate()
            process.join(10)

    assert process.exitcode == 0
    lifecycle_result = json.loads(result_path.read_text(encoding="utf-8"))
    assert lifecycle_result["ok"] is True, lifecycle_result
    assert lifecycle_result["outcome"] == "merged"
    after = subprocess.run(
        ["just", "sweep-scratch", "--root", str(scratch)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert not candidate.exists()
    assert "removed 1 directories" in after.stdout


def test_harness_scratch_is_reclaimed_during_an_active_dispatch_but_never_when_referenced(
    tmp_path: Path,
    bare_origin: Callable[..., Path],
    command_base: Callable[..., Path],
    personas_dir: Path,
) -> None:
    """The families that fill this disk are reclaimed while a real dispatch runs.

    Every fixture here is two days old, so age never explains a survival: what keeps
    a directory is a live process still naming it — in argv, in its working directory,
    in an open descriptor, or in a memory mapping with no descriptor left at all —
    Nx's own pid in the name, pytest's own retention and `.lock`, or a shape that is
    not disposable scratch to begin with.
    """
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    third_party = scratch / "visual-inflight"
    argv_named = _write_nx_install(scratch / "tmp-999999999-argv", dependencies={"nx": "^23.1.0"})
    working = _write_nx_install(scratch / "tmp-999999999-cwd", dependencies={"nx": "^23.1.0"})
    opened = _write_nx_install(scratch / "tmp-999999999-open", dependencies={"nx": "^23.1.0"})
    stale = _write_nx_install(scratch / "tmp-999999999-stale", dependencies={"nx": "^23.1.0"})
    lookalike = _write_nx_install(
        scratch / "tmp-999999999-lookalike", dependencies={"nx": "^23.1.0", "eslint": "^9"}
    )
    self_named = _write_nx_install(
        scratch / f"tmp-{os.getpid()}-live", dependencies={"nx": "^23.1.0"}
    )
    onejudge_scratch = scratch / "onejudge-python-stale"
    onejudge_scratch.mkdir()
    # The largest family on this host: one stranded 22 MB copy of Nx's native binary
    # per workspace root, and every lifecycle worktree is a new workspace root.
    binary = "23.1.0-nx.linux-arm64-gnu.node"
    stale_cache = _write_nx_native_cache(
        scratch / "nx-native-file-cache-000d3d4", entries=(binary,)
    )
    mapped_cache = _write_nx_native_cache(
        scratch / "nx-native-file-cache-02c850c", entries=(binary,)
    )
    lookalike_cache = _write_nx_native_cache(
        scratch / "nx-native-file-cache-0123456", entries=(binary, "operator-notes.txt")
    )
    pytest_root = scratch / "pytest-of-e2e"
    pytest_root.mkdir()
    runs = {}
    for number in range(0, 6):
        run = pytest_root / f"pytest-{number}"
        run.mkdir()
        runs[number] = run
    (pytest_root / "pytest-current").symlink_to(runs[5])
    # pytest treats a lock it cannot read as proof the run is not deletable.
    unreadable_lock = runs[0] / ".lock"
    unreadable_lock.write_text("999999999", encoding="utf-8")
    unreadable_lock.chmod(0o000)

    # A path handed to a process in its environment is in use however quiet argv is.
    env_named = scratch / "onejudge-python-env-named"
    env_named.mkdir()
    holder = subprocess.Popen(
        [sys.executable, "-c", _LIVE_REFERENCE_HOLDER, str(argv_named)],
        cwd=tmp_path,
        env={**os.environ, "ONEJUDGE_EFFECTIVE_CONFIG": str(env_named / "effective.json")},
        text=True,
        stdin=subprocess.PIPE,
    )
    # ...and so is a binary running out of scratch. Launched under a different
    # `argv[0]`, this one is named by no argv, no cwd, and no descriptor: the kernel
    # maps its executable, so `exe` and `maps` are what have to keep it.
    executing = scratch / "onejudge-python-executing"
    executing.mkdir()
    relocated = Path(shutil.copy2("/bin/sleep", executing / "held-by-exe"))
    executor = subprocess.Popen(["a-name-that-is-not-a-path", "600"], executable=relocated)
    ready = tmp_path / "gate-ready"
    release = tmp_path / "gate-release"
    holder_pidfile = tmp_path / "holder.pid"
    result_path = tmp_path / "lifecycle-result.json"
    origin = bare_origin()
    canonical = tmp_path / "canonical"
    subprocess.run(
        ["git", "clone", str(origin), str(canonical)],
        text=True,
        capture_output=True,
        check=True,
    )
    _install_blocking_pre_push_gate(canonical, third_party, ready, release)
    process = multiprocessing.Process(
        target=_run_lifecycle_with_blocking_gate,
        args=(
            str(origin),
            str(canonical),
            str(tmp_path / "worktrees"),
            str(command_base()),
            str(personas_dir),
            str(scratch),
            str(result_path),
        ),
    )
    process.start()
    try:
        assert holder.stdin is not None
        holder.stdin.write(
            json.dumps(
                {
                    "cwd": str(working),
                    "open": str(opened / "package.json"),
                    "mapped": str(mapped_cache / binary),
                    "pidfile": str(holder_pidfile),
                    "release": str(release),
                }
            )
            + "\n"
        )
        holder.stdin.flush()
        _wait_for_path(holder_pidfile)
        # The native cache is under test precisely because a loader leaves nothing
        # else behind: assert the descriptors are clean before relying on that.
        holder_proc = Path(f"/proc/{holder.pid}")
        assert not [
            descriptor
            for descriptor in (holder_proc / "fd").iterdir()
            if str(mapped_cache) in os.path.realpath(descriptor)
        ]
        assert str(mapped_cache) in (holder_proc / "maps").read_text(encoding="utf-8")
        # Same isolation for the other two channels: neither directory is named in the
        # argv, cwd, or descriptors of the process that is using it.
        assert str(env_named) not in (holder_proc / "cmdline").read_text(encoding="utf-8")
        executor_proc = Path(f"/proc/{executor.pid}")
        assert str(executing) not in (executor_proc / "cmdline").read_text(encoding="utf-8")
        assert Path(os.path.realpath(executor_proc / "exe")) == relocated
        # pytest keeps the newest three runs itself and marks a live session with a
        # `.lock` holding its pid; both conventions are honored rather than fought.
        (runs[1] / ".lock").write_text(
            holder_pidfile.read_text(encoding="utf-8").strip(), encoding="utf-8"
        )
        for path in (
            *runs.values(),
            pytest_root,
            onejudge_scratch,
            argv_named,
            working,
            opened,
            stale,
            lookalike,
            self_named,
            stale_cache,
            mapped_cache,
            lookalike_cache,
            env_named,
            executing,
        ):
            _age(path)
        _wait_for_path(ready)

        inspected = subprocess.run(
            ["just", "sweep-scratch", "--root", str(scratch), "--dry-run"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        assert str(stale) in inspected.stdout and "would remove" in inspected.stdout
        assert str(stale_cache) in inspected.stdout
        assert "reclaimed 0 bytes" in inspected.stdout
        # A dry run reclaims nothing, so its report has to say which families it
        # examined before that zero can be read as "nothing to reclaim".
        swept, _, skipped = inspected.stdout.partition("; skipped families: ")
        assert "nx-native-file-cache" in swept.split("swept families: ", 1)[1]
        assert skipped.startswith("third-party (lifecycle dispatch active)")
        assert all(path.exists() for path in (stale, stale_cache, onejudge_scratch, runs[2]))

        during = subprocess.run(
            ["just", "sweep-scratch", "--root", str(scratch)],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
    finally:
        release.write_text("release", encoding="utf-8")
        holder.communicate(timeout=60)
        executor.terminate()
        executor.wait(timeout=30)
        process.join(60)
        if process.is_alive():
            process.terminate()
            process.join(10)
        unreadable_lock.chmod(0o600)

    assert not stale.exists()
    assert not stale_cache.exists()
    assert not onejudge_scratch.exists()
    assert not runs[2].exists()
    preserved = [argv_named, working, opened, lookalike, self_named]
    preserved += [mapped_cache / binary, lookalike_cache, env_named, relocated]
    preserved += [runs[number] for number in (0, 1, 3, 4, 5)]
    assert [path for path in preserved if not path.exists()] == []
    referenced = re.search(
        r"retained (\d+) directories referenced by live processes", during.stdout
    )
    assert referenced is not None, during.stdout
    assert int(referenced.group(1)) >= 6
    # The dispatch's own third-party scratch still waits for the exclusive lock.
    assert third_party.exists()
    assert "skipped families: third-party (lifecycle dispatch active)" in during.stdout

    assert process.exitcode == 0
    lifecycle_result = json.loads(result_path.read_text(encoding="utf-8"))
    assert lifecycle_result["ok"] is True, lifecycle_result
    assert lifecycle_result["outcome"] == "merged"


def test_sweep_recipe_leaves_harness_scratch_alone_when_procfs_cannot_answer(
    tmp_path: Path,
) -> None:
    """Without a procfs that can see the sweeper, these families are never removed."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    install = _write_nx_install(
        scratch / "tmp-999999999-unprovable", dependencies={"nx": "^23.1.0"}
    )
    onejudge_scratch = scratch / "onejudge-python-unprovable"
    onejudge_scratch.mkdir()
    pytest_root = scratch / "pytest-of-unprovable"
    pytest_root.mkdir()
    runs = [pytest_root / f"pytest-{number}" for number in range(1, 6)]
    for run in runs:
        run.mkdir()
    dead_watchdog = scratch / "orchestrator-watchdog-dead"
    dead_watchdog.mkdir()
    (dead_watchdog / "pid").write_text("999999999\n", encoding="utf-8")
    for path in (install, onejudge_scratch, pytest_root, *runs):
        _age(path)
    blind = tmp_path / "not-procfs"
    blind.mkdir()

    result = subprocess.run(
        ["just", "sweep-scratch", "--root", str(scratch)],
        cwd=REPO_ROOT,
        env={**os.environ, "AI_ORCHESTRATOR_PROC_ROOT": str(blind)},
        text=True,
        capture_output=True,
        check=True,
    )

    assert install.exists() and onejudge_scratch.exists()
    assert [run for run in runs if not run.exists()] == []
    # The watchdog proof stands on its own lock, so its accounting is unaffected.
    assert not dead_watchdog.exists()
    assert "removed 1 directories" in result.stdout
    assert f"no usable procfs at {blind}" in result.stdout
    # Nothing was reclaimed from these families, so the report has to say they were
    # never examined rather than let one number read as "nothing to reclaim".
    swept, _, skipped = result.stdout.partition("; skipped families: ")
    assert "swept families: watchdog, third-party" in swept
    for family in UNREFERENCED_FAMILIES:
        assert f"{family.name} (no live process could be proven done with it)" in skipped


def test_sweep_cli_keeps_visible_references_when_a_process_hides_its_descriptors(
    tmp_path: Path,
) -> None:
    """Readable references still protect a process whose descriptors are hidden."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    candidate = scratch / "onejudge-python-unprovable"
    candidate.mkdir()
    stale = scratch / "onejudge-python-stale-sibling"
    stale.mkdir()
    for path in (candidate, stale):
        _age(path)
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    holder_script = """
import ctypes
import os
import sys
import time
from pathlib import Path

candidate, ready, release = map(Path, sys.argv[1:])
libc = ctypes.CDLL(None, use_errno=True)
if libc.prctl(4, 0, 0, 0, 0) != 0:
    raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))
ready.write_text("ready", encoding="utf-8")
while not release.exists():
    time.sleep(0.02)
assert candidate
"""
    holder = subprocess.Popen(
        [
            "/usr/bin/python3",
            "-c",
            holder_script,
            str(candidate),
            str(ready),
            str(release),
        ]
    )
    try:
        _wait_for_path(ready)
        with pytest.raises(PermissionError):
            next(Path(f"/proc/{holder.pid}/fd").iterdir())
        result = subprocess.run(
            ["just", "sweep-scratch", "--root", str(scratch)],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
    finally:
        release.touch()
        holder.wait(timeout=30)

    assert candidate.exists()
    assert not stale.exists()
    assert "removed 1 directories" in result.stdout
    assert "retained 1 directories referenced by live processes" in result.stdout


@pytest.mark.parametrize("identifiable", [True, False], ids=["identified", "unidentifiable"])
def test_concurrent_sweep_preserves_a_dispatch_past_its_worker_exit(
    tmp_path: Path, command_base: Callable[..., Path], onejudge_bin: str, identifiable: bool
) -> None:
    """A real dispatch keeps its scratch while a real sweep runs beside it.

    The recorded pid is the onejudge worker, which dies while the dispatcher is
    still reaping its process tree and parsing the report out of the directory.
    Sweeping continuously across the whole dispatch guarantees the sweeper meets
    that window rather than waiting for a lucky interleaving. A dispatcher that
    cannot read its own procfs identity records no token at all, so the lock has
    to hold the tree on its own.
    """
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    dispatch_env = {**os.environ, "TMPDIR": str(scratch)}
    if not identifiable:
        blind = tmp_path / "empty-proc"
        blind.mkdir()
        dispatch_env["AI_ORCHESTRATOR_PROC_ROOT"] = str(blind)
    report_path = tmp_path / "dispatch-report.json"
    report_stream = report_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            "just",
            "dispatch",
            "engineer",
            "complete-now large-dispatch-report: survive a concurrent scratch sweep",
            "--base",
            str(command_base()),
            "--project-dir",
            str(project_dir),
            "--onejudge-bin",
            onejudge_bin,
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        env=dispatch_env,
        text=True,
        stdout=report_stream,
        stderr=subprocess.PIPE,
    )
    swept_past_worker_exit: list[Path] = []
    try:
        while process.poll() is None:
            past_exit = [
                directory
                for directory in scratch.glob(WATCHDOG_PATTERN)
                if _worker_pid_is_gone(directory)
            ]
            # The unattended sweep this test races is the in-process
            # `sweep_scratch()` call every recorded round transition makes
            # (orchestrator/graph.py). The deliberately large real report keeps
            # parsing in flight after waitpid reaps the worker, so the sweep
            # observes that boundary without replacing it.
            # llmlint: ignore[tests_mirror_real_usage] this is the round-transition caller
            result = sweep_scratch(scratch)
            assert result.removed == (), result.removed
            swept_past_worker_exit.extend(
                directory for directory in past_exit if directory in result.watchdog_retained
            )
            time.sleep(0.001)
    finally:
        _, stderr = process.communicate(timeout=120)
        report_stream.close()

    assert process.returncode == 0, stderr
    assert swept_past_worker_exit, "the sweep never observed the post-worker-exit window"
    # The report is parsed out of the swept-past directory, so its survival is the
    # dispatch's own evidence that nothing removed the tree underneath it.
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema_version"] == 5
    assert report["stopped_early"] is False
    assert report["transcript"]["messages"]
    assert list(scratch.glob(WATCHDOG_PATTERN)) == []


def test_dry_run_recipe_and_recorded_round_transition_sweep(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    candidate = scratch / "oneharness-counter-old"
    candidate.mkdir()
    old = time.time() - 48 * 60 * 60
    os.utime(candidate, (old, old))
    inspected = subprocess.run(
        ["just", "sweep-scratch", "--root", str(scratch), "--dry-run"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert str(candidate) in inspected.stdout and candidate.exists()
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "name": "scratch-round",
                "tasks": [
                    {
                        "id": "no-diff",
                        "task": "No dispatch is expected.",
                        "expects_no_diff": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    subprocess.run(
        [
            "just",
            "run-plan",
            str(plan),
            "--run",
            "scratch-round",
            "--runs-dir",
            str(tmp_path / "runs"),
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "TMPDIR": str(scratch)},
        text=True,
        capture_output=True,
        check=True,
    )
    assert not candidate.exists()


def test_lifecycle_cli_refuses_dispatch_when_scratch_capacity_is_insufficient(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "target"
    repo.mkdir()
    result = subprocess.run(
        [
            "just",
            "repo-task",
            str(repo),
            "engineer",
            "must not dispatch",
        ],
        cwd=REPO_ROOT,
        env={**os.environ, MIN_FREE_BYTES_ENV: str(2**63 - 1)},
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert str(Path(os.environ.get("TMPDIR", "/tmp")).resolve()) in result.stderr
    assert "bytes free" in result.stderr
    assert "just sweep-scratch" in result.stderr
    assert MIN_FREE_BYTES_ENV in result.stderr


def test_lifecycle_dispatch_honors_valid_scratch_capacity_override(
    tmp_path: Path,
    bare_origin: Callable[..., Path],
    command_base: Callable[..., Path],
    personas_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    origin = bare_origin()
    canonical = tmp_path / "canonical"
    subprocess.run(
        ["git", "clone", str(origin), str(canonical)],
        text=True,
        capture_output=True,
        check=True,
    )
    install_pre_push_hook(canonical)
    monkeypatch.setenv(MIN_FREE_BYTES_ENV, "0")
    workspace = Workspace(
        tmp_path / "worktrees",
        resolver=lambda _spec: canonical,
        workflow="local",
    )
    result = run_repo_task(
        str(origin),
        "complete-now write-unique-change: capacity override",
        "engineer",
        workspace=workspace,
        base_path=command_base(),
        persona_dir=personas_dir,
        recorded_gate=["true"],
        repo_type="single-owner",
    )

    assert result.ok is True
    assert result.outcome == "merged"


@pytest.mark.parametrize(
    ("value", "message"),
    [("invalid", "integer byte count"), ("-1", "at least zero")],
)
def test_lifecycle_recipe_rejects_invalid_capacity_configuration(
    tmp_path: Path, value: str, message: str
) -> None:
    repo = tmp_path / "target"
    repo.mkdir()
    result = subprocess.run(
        ["just", "repo-task", str(repo), "engineer", "must not dispatch"],
        cwd=REPO_ROOT,
        env={**os.environ, MIN_FREE_BYTES_ENV: value},
        text=True,
        capture_output=True,
    )
    assert result.returncode == 1
    assert MIN_FREE_BYTES_ENV in result.stderr and message in result.stderr


def test_sweep_recipe_rejects_nonfinite_or_negative_age(tmp_path: Path) -> None:
    for value in ("nan", "inf", "-1"):
        result = subprocess.run(
            ["just", "sweep-scratch", "--root", str(tmp_path), "--min-age-hours", value],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
        )
        assert result.returncode != 0
        assert "finite number at least zero" in result.stderr
    missing = subprocess.run(
        ["just", "sweep-scratch", "--root", str(tmp_path / "missing")],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert missing.returncode != 0
    assert "--root must be an existing directory" in missing.stderr


def test_sweep_recipe_custom_age_and_bounded_inspection(tmp_path: Path) -> None:
    eligible = tmp_path / "visual-custom-age"
    eligible.mkdir()
    harness_eligible = tmp_path / "onejudge-python-custom-age"
    harness_eligible.mkdir()
    two_hours_old = time.time() - 2 * 60 * 60
    for path in (eligible, harness_eligible):
        os.utime(path, (two_hours_old, two_hours_old))
    subprocess.run(
        [
            "just",
            "sweep-scratch",
            "--root",
            str(tmp_path),
            "--min-age-hours",
            "1",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert not eligible.exists() and not harness_eligible.exists()

    for index in range(22):
        path = tmp_path / f"playwright-old-{index:02d}"
        path.mkdir()
        os.utime(path, (two_hours_old, two_hours_old))
    inspected = subprocess.run(
        [
            "just",
            "sweep-scratch",
            "--root",
            str(tmp_path),
            "--min-age-hours",
            "1",
            "--dry-run",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert len(inspected.stdout.splitlines()) == 1
    assert "2 more omitted" in inspected.stdout


def test_sweep_recipe_reports_actionable_deletion_failure(tmp_path: Path) -> None:
    candidate = tmp_path / "visual-permission-failure"
    candidate.mkdir()
    (candidate / "payload").write_text("data", encoding="utf-8")
    old = time.time() - 48 * 60 * 60
    os.utime(candidate, (old, old))
    candidate.chmod(0o555)
    try:
        result = subprocess.run(
            ["just", "sweep-scratch", "--root", str(tmp_path)],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
        )
    finally:
        candidate.chmod(0o755)
    assert result.returncode != 0
    assert "check path permissions" in result.stderr
    assert "just sweep-scratch --dry-run" in result.stderr


def test_recorded_round_reports_actionable_sweep_failure(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    candidate = scratch / "screencomp-permission-failure"
    candidate.mkdir()
    (candidate / "payload").write_text("data", encoding="utf-8")
    old = time.time() - 48 * 60 * 60
    os.utime(candidate, (old, old))
    candidate.chmod(0o555)
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "name": "scratch-failure",
                "tasks": [
                    {
                        "id": "no-diff",
                        "task": "No dispatch is expected.",
                        "expects_no_diff": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    try:
        result = subprocess.run(
            [
                "just",
                "run-plan",
                str(plan),
                "--run",
                "scratch-failure",
                "--runs-dir",
                str(tmp_path / "runs"),
            ],
            cwd=REPO_ROOT,
            env={**os.environ, "TMPDIR": str(scratch)},
            text=True,
            capture_output=True,
        )
    finally:
        candidate.chmod(0o755)
    assert result.returncode == 2
    assert "scratch sweep failed before claiming the round" in result.stderr
    assert "just sweep-scratch --dry-run" in result.stderr
    assert "Traceback" not in result.stderr
