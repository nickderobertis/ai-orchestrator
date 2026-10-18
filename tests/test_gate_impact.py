"""`orchestrator/gate_impact.py`: the record, the registry, the report, and every arm of the
recorder's run.

`tests/e2e/budget_gate/test_gate_host_impact_e2e.py` drives the recorder end to end through
the hook's step and onebudgetspec; this tier reaches every line and branch of the module,
because the coverage floor over `orchestrator/` is read from it. The record, the registry
and the report are run **in process**. The recorder's `run` is run as the real command,
`python -m orchestrator.gate_impact run`, under `coverage run --parallel-mode` whenever this
session is measured, because in process it would make this test worker a child subreaper
and walk, count and signal the worker's own children — other tests' processes among them;
the parallel data file it writes beside this session's is combined into it by pytest-cov.

llmlint: ignore-file[shell_test_tiers_stay_split,test_tiers_split_by_project_not_by_marker] This
module stays in `orchestrator:test`: that tier's measurement is the one the 100% floor over
`orchestrator/` is read from, and the journeys are split out into the `budget-gate` project.
"""

from __future__ import annotations

import ctypes
import io
import json
import os
import shlex
import signal
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import coverage
import pytest
from waits import end_carrying
from waits import timeout as e2e_timeout

from orchestrator import gate_impact
from orchestrator.gate_impact import (
    Leaked,
    LeftoverEntry,
    PeakEntry,
    Proc,
    Record,
    StoppedBy,
    Tree,
)
from orchestrator.root import REPO_ROOT

PYTHON = REPO_ROOT / ".venv" / "bin" / "python"
LOCK_TIMEOUT = REPO_ROOT / "scripts" / "lock-timeout.sh"


@pytest.fixture(autouse=True)
def _nothing_outlives_the_test(tmp_path: Path) -> Iterator[None]:
    """End every recorder, gate and stand-in this test started once it is over, however
    it ended — a failure, or a bound that elapsed and killed only its own child — and fail
    a test that left one. Each carries this test's own directory, its cache and temporary
    roots under ``tmp_path``, so nothing else is found."""
    yield
    left = end_carrying(tmp_path)
    assert not left, f"processes {left} this test started were still running when it ended"


def _record(**changes: object) -> Record:
    # Any, because each key's value is checked by the dataclass's own field type.
    values: dict[str, Any] = {
        "schema_version": 1,
        "push": "push-1",
        "exit_status": 0,
        "sample_seconds": 5,
        "samples": 3,
        "longest_walk_seconds": 0.25,
        "peak_bytes": 3 * 1024**3,
        "peak_top": [PeakEntry("big", 3 * 1024**3)],
        "leftover_entries": 2,
        "leftover_bytes": 8192,
        "excluded_uv_locks": 1,
        "leftover_top": [LeftoverEntry("left", 2, 8192)],
        "settle_seconds": 10,
        "leaked": [Leaked(41, 1, "sleep 9", StoppedBy.TERM)],
    }
    values.update(changes)
    return Record(**values)


def _environment(tmp_path: Path, **extra: str) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {gate_impact.SCRATCH_VARIABLE, gate_impact.PUSH_VARIABLE}
    }
    environment.update(
        {
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
            "XDG_STATE_HOME": str(tmp_path / "state"),
            **extra,
        }
    )
    return environment


def _recorder(tmp_path: Path, *arguments: str, **extra: str) -> subprocess.CompletedProcess[str]:
    """The recorder as its own process, measured when this session is."""
    command = [str(PYTHON)]
    if coverage.Coverage.current() is not None:
        command += [
            "-m",
            "coverage",
            "run",
            "--parallel-mode",
            f"--rcfile={REPO_ROOT / 'pyproject.toml'}",
        ]
    return subprocess.run(
        [*command, "-m", "orchestrator.gate_impact", *arguments],
        cwd=REPO_ROOT,
        env=_environment(tmp_path, **extra),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )


def _roots(tmp_path: Path) -> list[Path]:
    parent = tmp_path / "cache" / "ai-orchestrator" / "gate-tmp"
    return list(parent.iterdir()) if parent.exists() else []


def _summary(path: Path) -> Record:
    return Record.from_json(path.read_text(encoding="utf-8"))


def test_a_record_round_trips_through_its_one_declaration() -> None:
    record = _record()
    assert Record.from_json(record.to_json()) == record
    assert list(json.loads(record.to_json())) == [
        "schema_version",
        "push",
        "exit_status",
        "sample_seconds",
        "samples",
        "longest_walk_seconds",
        "peak_bytes",
        "peak_top",
        "leftover_entries",
        "leftover_bytes",
        "excluded_uv_locks",
        "leftover_top",
        "settle_seconds",
        "leaked",
    ]


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("not json", "it is not JSON"),
        ("[]", "the record is not a JSON object"),
        ('{"push": "x"}', "the record lacks"),
    ],
)
def test_a_record_that_is_not_one_is_refused(text: str, why: str) -> None:
    with pytest.raises(ValueError, match=why):
        Record.from_json(text)


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"schema_version": 2}, "it is schema_version 2, not 1"),
        ({"samples": True}, "the record's samples is True, which is not int"),
        ({"push": 3}, "the record's push is 3, which is not PushId"),
        ({"leaked": {}}, "the record's leaked is {}, which is not list"),
        ({"peak_top": [{"path": "a"}]}, "an entry of peak_top lacks"),
        ({"leftover_top": [[]]}, "an entry of leftover_top is not a JSON object"),
        (
            {"leaked": [{"pid": 1, "ppid": 0, "command": "x", "stopped_by": "HUP"}]},
            "an entry of leaked's stopped_by is 'HUP', which is no way to stop",
        ),
        ({"leftover_entries": -1}, "the record's leftover_entries is -1, which is no measurement"),
        ({"exit_status": -15}, "the record's exit_status is -15, which is no measurement"),
        ({"peak_bytes": 0, "longest_walk_seconds": float("nan")}, "which is no measurement"),
        ({"settle_seconds": float("inf")}, "the record's settle_seconds is inf"),
    ],
)
def test_a_record_with_a_wrong_field_is_refused(change: dict[str, object], why: str) -> None:
    document = json.loads(_record().to_json())
    document.update(change)
    with pytest.raises(ValueError, match=why.replace("(", r"\(").replace(")", r"\)")):
        Record.from_json(json.dumps(document))


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({"XDG_STATE_HOME": "/state", "HOME": "/home/u"}, "/state"),
        ({"XDG_STATE_HOME": "relative", "HOME": "/home/u"}, "/home/u/.local/state"),
        ({"HOME": "/home/u"}, "/home/u/.local/state"),
        ({"HOME": ""}, "/.local/state"),
        ({"HOME": "relative"}, None),
        ({}, "/.local/state"),
    ],
)
def test_the_push_file_follows_the_lock_timeout_state_rule(
    environ: dict[str, str], expected: str | None
) -> None:
    found = gate_impact.push_file(environ)
    if expected is None:
        assert found is None
    else:
        assert found == Path(expected) / "ai-orchestrator" / "gate-host-impact-push.json"


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({gate_impact.SCRATCH_VARIABLE: "/r/scratch", "TMPDIR": "/r/tmp", "HOME": "/h"}, "/r/tmp"),
        (
            {gate_impact.SCRATCH_VARIABLE: "/r/scratch", "HOME": "/h"},
            "/h/.cache/ai-orchestrator/gate-tmp",
        ),
        ({"XDG_CACHE_HOME": "/c", "HOME": "/h"}, "/c/ai-orchestrator/gate-tmp"),
        ({"XDG_CACHE_HOME": "c", "HOME": "/h"}, "/h/.cache/ai-orchestrator/gate-tmp"),
    ],
)
def test_the_root_parent_is_the_host_cache_or_an_outer_recorder_s_tmpdir(
    environ: dict[str, str], expected: str
) -> None:
    assert gate_impact.root_parent(environ) == Path(expected)


@pytest.mark.parametrize(
    "environ",
    [
        {"XDG_STATE_HOME": "/state", "HOME": "/home/u"},
        {"XDG_STATE_HOME": "relative", "HOME": "/home/u"},
        {"HOME": "/home/u"},
        {"HOME": ""},
        {"HOME": "relative"},
        {},
    ],
)
def test_the_push_file_sits_where_the_hook_keeps_its_gate_record(
    tmp_path: Path, environ: dict[str, str]
) -> None:
    """One answer with `scripts/lock-timeout.sh`'s `local_direct_gate_state_file`, the rule
    it restates: the same directory, or nowhere for both."""
    asked = subprocess.run(
        ["bash", "-c", '. "$1"; local_direct_gate_state_file', "ask", str(LOCK_TIMEOUT)],
        # The cache is named only for the suite's own guard; the rule reads none of it.
        env={"PATH": os.environ["PATH"], "XDG_CACHE_HOME": str(tmp_path), **environ},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
        check=False,
    )
    found = gate_impact.push_file(environ)
    if asked.returncode != 0:
        assert found is None, (asked.stderr, found)
    else:
        assert found is not None and found.parent == Path(asked.stdout.strip()).parent


@pytest.mark.parametrize(
    "environ", [{"HOME": "relative"}, {"HOME": ""}, {"XDG_CACHE_HOME": "c"}, {}]
)
def test_a_root_parent_with_no_absolute_base_is_refused(environ: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="neither XDG_CACHE_HOME nor HOME is an absolute path"):
        gate_impact.root_parent(environ)


def test_the_defaults_read_this_process_s_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.delenv(gate_impact.SCRATCH_VARIABLE, raising=False)
    assert gate_impact.push_file() == tmp_path / "ai-orchestrator" / "gate-host-impact-push.json"
    assert gate_impact.root_parent() == tmp_path / "ai-orchestrator" / "gate-tmp"


def test_registering_does_nothing_without_a_recorder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(gate_impact.SCRATCH_VARIABLE, raising=False)
    assert gate_impact.register_scratch("/anywhere") is None
    assert gate_impact.main(["register", "/anywhere"]) == 0


def test_registered_paths_are_read_once_each_and_only_when_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = tmp_path / "scratch"
    registry.mkdir()
    monkeypatch.setenv(gate_impact.SCRATCH_VARIABLE, str(registry))
    monkeypatch.chdir(tmp_path)
    gate_impact.register_scratch("relative")
    gate_impact.register_scratch(tmp_path / "relative")
    assert gate_impact.main(["register", f"{tmp_path}/elsewhere"]) == 0
    (registry / "not-absolute").write_text("nowhere\n", encoding="utf-8")
    (registry / "a-directory").mkdir()

    assert gate_impact.registered_paths(registry) == [
        tmp_path / "relative",
        tmp_path / "elsewhere",
    ]
    assert gate_impact.registered_paths(tmp_path / "missing") == []


def test_the_count_once_the_gate_exits_reads_the_registry_strictly(tmp_path: Path) -> None:
    registry = tmp_path / "scratch"
    with pytest.raises(gate_impact.Unmeasured, match="could not be listed"):
        gate_impact.registered_paths(registry, strict=True)
    registry.mkdir()
    (registry / "a-directory").mkdir()
    with pytest.raises(gate_impact.Unmeasured, match="could not be read"):
        gate_impact.registered_paths(registry, strict=True)
    (registry / "a-directory").rmdir()
    (registry / "not-utf-8").write_bytes(b"/tmp/\xff\n")
    with pytest.raises(gate_impact.Unmeasured, match="could not be read"):
        gate_impact.registered_paths(registry, strict=True)
    assert gate_impact.registered_paths(registry) == []
    (registry / "not-utf-8").unlink()
    (registry / "relative").write_text("nowhere\n", encoding="utf-8")
    with pytest.raises(gate_impact.Unmeasured, match="'nowhere', no absolute path"):
        gate_impact.registered_paths(registry, strict=True)


def test_a_registered_path_is_read_back_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A path ending in a space is that path, not its neighbour without one; a path no
    one entry can hold is refused when it is registered."""
    registry = tmp_path / "scratch"
    registry.mkdir()
    monkeypatch.setenv(gate_impact.SCRATCH_VARIABLE, str(registry))
    spaced = tmp_path / "scratch-dir "
    gate_impact.register_scratch(spaced)
    assert gate_impact.registered_paths(registry, strict=True) == [spaced]
    err = io.StringIO()
    assert gate_impact.main(["register", f"{tmp_path}/two\nlines"], err) == 1
    assert "holds a newline or a NUL" in err.getvalue()
    assert len(list(registry.iterdir())) == 1


def test_a_relative_registry_takes_no_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(gate_impact.SCRATCH_VARIABLE, "scratch")
    err = io.StringIO()
    assert gate_impact.main(["register", "/x"], err) == 1
    assert "ORCHESTRATOR_GATE_SCRATCH=scratch is not an absolute path" in err.getvalue()
    assert list(tmp_path.iterdir()) == []


def test_a_registration_with_no_registry_to_write_to_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(gate_impact.SCRATCH_VARIABLE, str(tmp_path / "gone"))
    err = io.StringIO()
    assert gate_impact.main(["register", "/x"], err) == 1
    assert "could not be registered" in err.getvalue()


def test_registrations_that_reach_the_root_or_home_are_never_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, home = tmp_path / "root", tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    home.mkdir()
    (tmp_path / "home-by-link").symlink_to(home)
    paths = [
        root / "tmp" / "inside",
        tmp_path,
        home.parent,
        tmp_path / "kept" / ".." / "..",
        tmp_path / "home-by-link",
        tmp_path / "kept",
        tmp_path / "kept" / "nested",
        tmp_path / "not-yet-made",
    ]
    assert gate_impact._counted(paths, root, root / "tmp") == [
        tmp_path / "kept",
        tmp_path / "not-yet-made",
    ]


def test_a_shared_directory_is_never_one_gate_s_scratch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never counted, never removed, and refused when registered: a directory directly
    under `/`, one every user writes into, and another user's. Judged in process only,
    so nothing here could remove what it names."""
    sticky = tmp_path / "everyone"
    sticky.mkdir()
    sticky.chmod(0o1777)
    shared = [Path("/tmp"), Path("/elsewhere"), sticky, Path("/usr/share")]
    root = tmp_path / "root"
    assert gate_impact._counted(shared, root, root / "tmp") == []
    registry = tmp_path / "scratch"
    registry.mkdir()
    monkeypatch.setenv(gate_impact.SCRATCH_VARIABLE, str(registry))
    for path, why in zip(
        shared,
        ["a top-level directory"] * 2 + ["a directory every user writes into", "another user's"],
        strict=True,
    ):
        err = io.StringIO()
        assert gate_impact.main(["register", str(path)], err) == 1
        assert f"{path} is {why}, never one gate's own scratch" in err.getvalue()
    assert list(registry.iterdir()) == []
    assert gate_impact._shared(tmp_path / "not-yet-made") is None


def test_leftovers_are_counted_by_top_level_entry(tmp_path: Path) -> None:
    tmp = tmp_path / "tmp"
    (tmp / "dir").mkdir(parents=True)
    (tmp / "dir" / "file").write_text("x", encoding="utf-8")
    (tmp / "uv-0123456789abcdef.lock").touch()
    (tmp / "uv-fedcba9876543210.lock").write_text("held", encoding="utf-8")
    (tmp / "uv-0123456789abcdef.lock.d").mkdir()
    registered = tmp_path / "registered"
    registered.write_text("y", encoding="utf-8")

    left = gate_impact.count_leftovers(tmp, [registered, tmp_path / "gone"])

    assert left.excluded_uv_locks == 1
    assert left.entries == 5
    assert {entry.path: entry.entries for entry in left.top} == {
        "dir": 2,
        "uv-fedcba9876543210.lock": 1,
        "uv-0123456789abcdef.lock.d": 1,
        str(registered): 1,
    }
    with pytest.raises(gate_impact.Unmeasured, match="could not be listed"):
        gate_impact.count_leftovers(tmp_path / "missing", [])


def test_removal_opens_up_what_a_test_left_unreadable(tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    (locked / "inner").mkdir(parents=True)
    (locked / "inner" / "file").touch()
    (locked / "inner").chmod(0)
    link = tmp_path / "link"
    link.symlink_to(locked)
    single = tmp_path / "single"
    single.touch()

    assert gate_impact.remove(link)
    assert gate_impact.remove(locked)
    assert gate_impact.remove(single)
    assert gate_impact.remove(tmp_path / "never-there")
    assert list(tmp_path.iterdir()) == []
    gate_impact._open_up(tmp_path / "never-there")


def test_the_walk_counts_the_tree_and_leaves_out_what_the_recorder_excludes() -> None:
    tree = Tree()
    child = tree.start_excluded(["sleep", "30"])
    try:
        assert child.pid not in tree.walk()
        tree.include(child.pid)
        assert child.pid in tree.walk()
        assert gate_impact._command(child.pid) == "sleep 30"
    finally:
        child.kill()
        child.wait()
    assert child.pid not in tree.walk()
    assert gate_impact._stat(child.pid) is None
    assert gate_impact._command(child.pid) == ""
    assert not gate_impact._alive(Proc(child.pid, 1, "S", 0))
    gate_impact._signal({child.pid: Proc(child.pid, 1, "S", 0)}, signal.SIGKILL)


def test_a_walk_over_no_path_samples_nothing(tmp_path: Path) -> None:
    assert gate_impact._du(tmp_path / "missing", [], Tree()) == gate_impact.Walk(0, [])
    assert gate_impact._allocated(tmp_path / "missing") == gate_impact.Allocation(0, 0)


@pytest.fixture
def reporting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """This push's record written, and a result file onebudgetspec would name."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv(gate_impact.PUSH_VARIABLE, "push-1")
    monkeypatch.setenv(gate_impact.RESULT_VARIABLE, str(tmp_path / "result.json"))
    record = gate_impact.push_file()
    assert record is not None
    record.parent.mkdir(parents=True)
    record.write_text(_record().to_json(), encoding="utf-8")
    return tmp_path / "result.json"


@pytest.mark.parametrize(
    ("figure", "value", "detail"),
    [
        (
            "leftovers",
            2,
            "8192 B left; 1 zero-byte uv lock(s) not counted; largest: left (2 entries, 8192 B)",
        ),
        (
            "peak-temp-disk",
            3.0,
            "3221225472 B at the peak of 3 sample(s) every 5s, longest walk 0.25s; "
            "largest at the peak: big (3221225472 B)",
        ),
        (
            "leaked-processes",
            1,
            "alive when the gate exited, waited up to 10s: "
            "pid 41 (parent 1, stopped by TERM): sleep 9",
        ),
    ],
)
def test_each_figure_is_reported_from_the_record(
    reporting: Path, figure: str, value: float, detail: str
) -> None:
    assert gate_impact.main(["report", figure]) == 0
    assert json.loads(reporting.read_text(encoding="utf-8")) == {"value": value, "detail": detail}


def test_an_empty_record_reports_none_in_each_detail(reporting: Path) -> None:
    record = gate_impact.push_file()
    assert record is not None
    record.write_text(_record(peak_top=[], leftover_top=[], leaked=[]).to_json(), encoding="utf-8")
    for figure in gate_impact.Figure:
        assert gate_impact.main(["report", figure]) == 0
        assert json.loads(reporting.read_text(encoding="utf-8"))["detail"].endswith("none")


@pytest.mark.parametrize(
    ("environ", "why"),
    [
        ({gate_impact.PUSH_VARIABLE: ""}, "no push is named in ORCHESTRATOR_GATE_PUSH"),
        ({gate_impact.PUSH_VARIABLE: "a push"}, "is not a push the hook names"),
        ({gate_impact.RESULT_VARIABLE: ""}, "ONEBUDGETSPEC_RESULT names no result file"),
        (
            {"XDG_STATE_HOME": "", "HOME": "relative"},
            "neither XDG_STATE_HOME nor HOME is an absolute path",
        ),
        (
            {gate_impact.PUSH_VARIABLE: "push-2"},
            "is for an earlier push (push-1), not this one (push-2)",
        ),
        (
            {gate_impact.RESULT_VARIABLE: "/nonexistent/result.json"},
            "the result could not be written",
        ),
    ],
)
def test_a_report_that_cannot_answer_is_refused(
    reporting: Path, monkeypatch: pytest.MonkeyPatch, environ: dict[str, str], why: str
) -> None:
    for name, value in environ.items():
        monkeypatch.setenv(name, value)
    err = io.StringIO()
    assert gate_impact.main(["report", "leftovers"], err) == 1
    assert why in err.getvalue(), err.getvalue()


def test_a_missing_or_unusable_record_is_refused_naming_it(reporting: Path) -> None:
    record = gate_impact.push_file()
    assert record is not None
    record.write_text("{}", encoding="utf-8")
    err = io.StringIO()
    assert gate_impact.main(["report", "leftovers"], err) == 1
    assert f"the gate record at {record} is unusable" in err.getvalue()
    record.write_bytes(b"\xff\xfe")
    err = io.StringIO()
    assert gate_impact.main(["report", "leftovers"], err) == 1
    assert f"the gate record at {record} is unusable: it is not UTF-8" in err.getvalue()
    record.unlink()
    err = io.StringIO()
    assert gate_impact.main(["report", "leftovers"], err) == 1
    assert f"no gate record could be read at {record}" in err.getvalue()
    assert "push again" in err.getvalue()


@pytest.mark.parametrize(
    "arguments", [["run"], ["run", "--"], ["run", "--push", "a b", "--", "true"]]
)
def test_a_run_with_nothing_to_run_or_an_invalid_push_name_is_refused(arguments: list[str]) -> None:
    err = io.StringIO()
    assert gate_impact.main(arguments, err) == 2
    assert err.getvalue().startswith("gate-impact: ")


def test_a_gate_that_grows_adopts_and_leaks_is_recorded_whole(tmp_path: Path) -> None:
    """One gate that runs past a sample, leaves an orphan that exits while it runs, and
    leaves three detached processes: one that exits in the settle wait, one TERM stops, and
    one that ignores TERM and is killed. It registers a directory with a child, and a
    second registration naming the same directory."""
    registered = tmp_path / "registered"
    ignoring = (
        "import signal, time, pathlib; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"pathlib.Path({str(tmp_path / 'ignoring')!r}).touch(); time.sleep(60)"
    )
    gate = (
        f"mkdir {registered} && touch {registered}/child\n"
        f"{PYTHON} -m orchestrator.gate_impact register {registered}\n"
        f"{PYTHON} -m orchestrator.gate_impact register {registered}\n"
        "(sleep 0.2 &)\n"
        'dd if=/dev/zero of="$TMPDIR/grown" bs=1M count=2 status=none\n'
        "sleep 6\n"
        'rm "$TMPDIR/grown"; touch "$TMPDIR/left"\n'
        "setsid sleep 1 >/dev/null 2>&1 &\n"
        "setsid sleep 60 >/dev/null 2>&1 &\n"
        f"setsid {PYTHON} -c {json.dumps(ignoring)} >/dev/null 2>&1 &\n"
        f"until [ -e {tmp_path / 'ignoring'} ]; do sleep 0.05; done\n"
        "exit 3"
    )
    summary = tmp_path / "summary.json"

    ran = _recorder(
        tmp_path, "run", "--push", "p-1", "--summary", str(summary), "--", "bash", "-c", gate
    )

    assert ran.returncode == 3, ran.stderr
    record = _summary(summary)
    assert record == _summary(tmp_path / "state" / "ai-orchestrator" / "gate-host-impact-push.json")
    assert record.push == "p-1"
    assert record.exit_status == 3
    assert record.samples >= 3
    assert record.peak_bytes >= 2 * 1024**2
    assert record.peak_top[0] == PeakEntry("grown", 2 * 1024**2)
    assert sorted(process.stopped_by for process in record.leaked) == ["KILL", "TERM", "exited"]
    assert {entry.path: entry.entries for entry in record.leftover_top} == {
        "left": 1,
        str(registered): 2,
    }
    assert not registered.exists()
    assert _roots(tmp_path) == []
    assert "3 leaked process(es); recorded at " in ran.stderr


def test_a_gate_ended_by_a_signal_exits_as_the_shell_would(tmp_path: Path) -> None:
    ran = _recorder(tmp_path, "run", "bash", "-c", "kill -USR1 $$")
    assert ran.returncode == 128 + signal.SIGUSR1, ran.stderr
    assert "exit 138" in ran.stderr
    assert "recorded at" not in ran.stderr
    assert _roots(tmp_path) == []


def test_a_signal_to_the_recorder_reaches_the_gate_and_the_record_is_still_kept(
    tmp_path: Path,
) -> None:
    command = [str(PYTHON)]
    if coverage.Coverage.current() is not None:
        command += [
            "-m",
            "coverage",
            "run",
            "--parallel-mode",
            f"--rcfile={REPO_ROOT / 'pyproject.toml'}",
        ]
    ready = tmp_path / "ready"
    gate = f"trap 'exit 7' TERM; touch {ready}; while :; do sleep 0.1; done"
    recorder = subprocess.Popen(
        [
            *command,
            "-m",
            "orchestrator.gate_impact",
            "run",
            "--summary",
            str(tmp_path / "s.json"),
            "--",
            "bash",
            "-c",
            gate,
        ],
        cwd=REPO_ROOT,
        env=_environment(tmp_path),
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + e2e_timeout(60)
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        recorder.send_signal(signal.SIGTERM)
        _, err = recorder.communicate(timeout=e2e_timeout(60))
    finally:
        if recorder.poll() is None:
            recorder.kill()
            recorder.wait()
    assert recorder.returncode == 7, err
    assert _summary(tmp_path / "s.json").exit_status == 7
    assert _roots(tmp_path) == []


def test_a_root_that_cannot_be_made_runs_no_gate(tmp_path: Path) -> None:
    blocked = tmp_path / "blocked"
    blocked.touch()
    ran = _recorder(
        tmp_path, "run", "--", "touch", str(tmp_path / "ran"), XDG_CACHE_HOME=str(blocked)
    )
    assert ran.returncode == 1
    assert "no root for the gate could be made" in ran.stderr
    assert not (tmp_path / "ran").exists()


def test_a_root_with_nowhere_host_local_to_go_runs_no_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refused before the recorder becomes anything, so it is run in process."""
    monkeypatch.delenv(gate_impact.SCRATCH_VARIABLE, raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", "relative")
    err = io.StringIO()
    ran = tmp_path / "ran"
    assert gate_impact.run(["touch", str(ran)], push=None, summary=None, err=err) == 1
    assert "there is nowhere host-local for the gate's root" in err.getvalue()
    assert not ran.exists()


def test_a_walk_that_cannot_read_everything_says_its_peak_is_a_floor(tmp_path: Path) -> None:
    gate = 'mkdir -p "$TMPDIR/locked/inner" && chmod 000 "$TMPDIR/locked"'
    ran = _recorder(tmp_path, "run", "--", "bash", "-c", gate)
    assert ran.returncode == 0, ran.stderr
    assert "sample walk(s) could not read everything (du said: " in ran.stderr
    assert "so the peak is a floor" in ran.stderr
    assert _roots(tmp_path) == []


@pytest.mark.parametrize(
    ("gate", "why"),
    [
        ('rm -rf "$TMPDIR"', "the gate's temporary directory"),
        ('chmod 000 "$ORCHESTRATOR_GATE_SCRATCH"', "the scratch registry"),
        ('echo relative >"$ORCHESTRATOR_GATE_SCRATCH/by-hand"', "no absolute path"),
        ("printf '/tmp/a\\0b' >\"$ORCHESTRATOR_GATE_SCRATCH/by-hand\"", "no absolute path"),
    ],
)
def test_a_gate_that_spoils_its_own_count_writes_no_record(
    tmp_path: Path, gate: str, why: str
) -> None:
    """Nothing is recorded, so the budgets refuse the push as unmeasured rather than admit
    a partial count; the gate's status and the clean-up stand."""
    summary = tmp_path / "summary.json"
    ran = _recorder(
        tmp_path, "run", "--summary", str(summary), "--", "bash", "-c", f"{gate}; exit 4"
    )
    assert ran.returncode == 4, ran.stderr
    assert why in ran.stderr
    assert "no record was written; its budgets refuse the push as unmeasured" in ran.stderr
    assert "leftovers unmeasured" in ran.stderr
    assert not summary.exists()
    assert _roots(tmp_path) == []


def test_a_gate_that_cannot_start_is_refused_and_leaves_no_root(tmp_path: Path) -> None:
    ran = _recorder(tmp_path, "run", "--", str(tmp_path / "no-such-gate"))
    assert ran.returncode == 127
    assert "could not be started" in ran.stderr
    assert _roots(tmp_path) == []


def test_a_record_with_nowhere_to_go_is_said_so(tmp_path: Path) -> None:
    ran = _recorder(
        tmp_path,
        "run",
        "--push",
        "p-2",
        "--summary",
        "/dev/null/summary.json",
        "--",
        "true",
        XDG_STATE_HOME="",
        HOME="relative",
    )
    assert ran.returncode == 0, ran.stderr
    assert "neither XDG_STATE_HOME nor HOME is an absolute path" in ran.stderr
    assert "the record could not be written to /dev/null/summary.json" in ran.stderr


def test_scratch_and_a_root_that_cannot_be_removed_are_named(tmp_path: Path) -> None:
    """A registered path whose parent is read-only, and a gate that makes its root's own
    parent read-only: both are named rather than silently left."""
    fenced = tmp_path / "fenced"
    (fenced / "inside").mkdir(parents=True)
    gate = (
        f"{PYTHON} -m orchestrator.gate_impact register {fenced / 'inside'}\n"
        f"chmod 555 {fenced}\n"
        'chmod 555 "$TMPDIR/../.."'
    )
    try:
        ran = _recorder(tmp_path, "run", "--", "bash", "-c", gate)
    finally:
        fenced.chmod(0o755)
        for root in _roots(tmp_path):
            root.parent.chmod(0o755)
            gate_impact.remove(root)
    assert ran.returncode == 0, ran.stderr
    assert f"the registered scratch {fenced / 'inside'} could not be removed" in ran.stderr
    assert "the gate's root" in ran.stderr and "could not be removed" in ran.stderr


def test_an_orphan_that_exits_is_reaped_at_once(tmp_path: Path) -> None:
    """An orphan the recorder adopted is reaped the moment it exits, as init would reap it,
    so a gate that kills one and asks at once whether it is gone is answered truly."""
    pid_file = tmp_path / "orphan"
    gate = (
        f"bash -c 'sleep 60 >/dev/null 2>&1 & echo $! >{pid_file}'\n"
        f"pid=$(cat {pid_file})\n"
        'kill -9 "$pid"\n'
        "sleep 0.3\n"
        '[ ! -e "/proc/$pid" ] || { echo "$pid is still a zombie" >&2; exit 9; }'
    )
    ran = _recorder(tmp_path, "run", "--", "bash", "-c", gate)
    assert ran.returncode == 0, ran.stderr
    assert "0 leaked process(es)" in ran.stderr


def test_a_registration_reaching_home_is_left_alone_and_overlaps_counted_once(
    tmp_path: Path,
) -> None:
    """Registrations naming an ancestor of the user's home, by `..` and by a link, are
    neither counted nor removed; one inside another is counted once, with it, and both go."""
    home = tmp_path / "home"
    (home / "kept").mkdir(parents=True)
    (tmp_path / "home-by-link").symlink_to(home)
    scratch = tmp_path / "scratch-dir"
    register = f"{PYTHON} -m orchestrator.gate_impact register"
    gate = (
        f"mkdir -p {scratch}/nested && touch {scratch}/nested/file\n"
        f"{register} {scratch}\n{register} {scratch}/nested\n"
        f"{register} {home}/kept/../..\n{register} {tmp_path}/home-by-link"
    )
    summary = tmp_path / "summary.json"

    ran = _recorder(
        tmp_path, "run", "--summary", str(summary), "--", "bash", "-c", gate, HOME=str(home)
    )

    assert ran.returncode == 0, ran.stderr
    record = _summary(summary)
    assert [(entry.path, entry.entries) for entry in record.leftover_top] == [(str(scratch), 3)]
    assert not scratch.exists()
    assert (home / "kept").is_dir()
    assert (tmp_path / "home-by-link").is_symlink()


def test_descendants_started_after_the_gate_exits_are_stopped_too(tmp_path: Path) -> None:
    """A leaked process that starts a child during the settle wait and another while it
    handles TERM: neither child was alive when the gate exited, so neither is counted,
    and both are stopped — the second only by the re-walk before KILL."""
    notes = tmp_path / "notes"
    notes.mkdir()
    body = (
        "import pathlib, signal, subprocess, sys, time\n"
        f"notes = pathlib.Path({str(notes)!r})\n"
        "def child(name):\n"
        "    started = subprocess.Popen(['sleep', '60'])\n"
        "    (notes / name).write_text(str(started.pid))\n"
        "def stopped(*_):\n"
        "    child('during-term')\n"
        "    sys.exit(0)\n"
        "signal.signal(signal.SIGTERM, stopped)\n"
        "(notes / 'parent').write_text(str(__import__('os').getpid()))\n"
        "time.sleep(1)\n"
        "child('during-settle')\n"
        "while True:\n"
        "    time.sleep(1)\n"
    )
    gate = (
        f"setsid {PYTHON} -c {shlex.quote(body)} >/dev/null 2>&1 &\n"
        f"until [ -s {notes}/parent ]; do sleep 0.05; done"
    )
    summary = tmp_path / "summary.json"

    ran = _recorder(tmp_path, "run", "--summary", str(summary), "--", "bash", "-c", gate)

    assert ran.returncode == 0, ran.stderr
    parent = int((notes / "parent").read_text())
    record = _summary(summary)
    assert [(process.pid, process.stopped_by) for process in record.leaked] == [
        (parent, StoppedBy.TERM)
    ]
    for name in ("during-settle", "during-term"):
        pid = int((notes / name).read_text())
        stat = gate_impact._stat(pid)
        assert stat is None or stat.state == "Z", name


def test_a_signal_before_the_gate_starts_is_passed_on_once_it_has() -> None:
    forward = gate_impact.Forwarding()
    forward(signal.SIGTERM, None)
    assert forward.pending == [signal.SIGTERM]
    gate = subprocess.Popen(["sleep", "30"])
    try:
        forward.attach(gate)
        assert gate.wait(timeout=e2e_timeout(30)) == -signal.SIGTERM
        assert forward.pending == []
        forward(signal.SIGTERM, None)
    finally:
        gate.kill()
        gate.wait()


def test_a_caller_already_gone_stops_the_gate_at_once() -> None:
    """Run in process with a caller that is not this process's parent, which is what the
    recorder sees when its caller exited before it asked to be told; the request it makes
    of the kernel is taken back afterwards, so this worker is not told of its own."""
    forward = gate_impact.Forwarding()
    try:
        gate_impact._watch_caller(os.getppid() + 1, forward)
        assert forward.pending == [signal.SIGTERM]
        gate_impact._watch_caller(os.getppid(), forward)
        assert forward.pending == [signal.SIGTERM]
    finally:
        ctypes.CDLL(None).prctl(gate_impact.PR_SET_PDEATHSIG, 0, 0, 0, 0)


def test_a_recorder_whose_caller_dies_stops_its_gate_and_still_cleans_up(
    tmp_path: Path,
) -> None:
    """The caller — a hook, a push, a test — killed outright mid-gate: the recorder is told,
    passes the stop to its gate, records it and removes its root rather than running on
    re-parented to init."""
    command = [str(PYTHON)]
    if coverage.Coverage.current() is not None:
        command += [
            "-m",
            "coverage",
            "run",
            "--parallel-mode",
            f"--rcfile={REPO_ROOT / 'pyproject.toml'}",
        ]
    ready, recorder_pid = tmp_path / "ready", tmp_path / "recorder"
    summary = tmp_path / "summary.json"
    recorder = shlex.join(
        [
            *command,
            "-m",
            "orchestrator.gate_impact",
            "run",
            "--summary",
            str(summary),
            "--",
            "bash",
            "-c",
            f"touch {ready}; while :; do sleep 0.1; done",
        ]
    )
    caller = subprocess.Popen(
        ["bash", "-c", f"{recorder} & echo $! >{recorder_pid}; wait"],
        cwd=REPO_ROOT,
        env=_environment(tmp_path),
    )
    try:
        deadline = time.monotonic() + e2e_timeout(60)
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready.exists(), "the gate never started"
        caller.kill()
        caller.wait()
        pid = int(recorder_pid.read_text(encoding="utf-8"))
        deadline = time.monotonic() + e2e_timeout(60)
        while gate_impact._stat(pid) is not None and time.monotonic() < deadline:
            time.sleep(0.1)
        assert gate_impact._stat(pid) is None, "the recorder ran on after its caller died"
    finally:
        if caller.poll() is None:
            caller.kill()
            caller.wait()
    assert _summary(summary).exit_status == 128 + signal.SIGTERM
    assert _roots(tmp_path) == []


def test_a_gate_ignoring_its_stop_is_escalated_once_its_caller_dies(tmp_path: Path) -> None:
    """The caller killed outright while the gate ignores TERM and has a detached descendant
    that ignores it too: the recorder escalates against its own whole tree within its
    bound, then records the run and removes its root and registered scratch, rather than
    waiting on a gate that will never stop."""
    command = [str(PYTHON)]
    if coverage.Coverage.current() is not None:
        command += [
            "-m",
            "coverage",
            "run",
            "--parallel-mode",
            f"--rcfile={REPO_ROOT / 'pyproject.toml'}",
        ]
    notes = tmp_path / "notes"
    notes.mkdir()
    registered, summary = tmp_path / "registered", tmp_path / "summary.json"
    gate = (
        "trap '' TERM\n"
        f"echo $$ >{notes}/gate\n"
        f"mkdir {registered} && {PYTHON} -m orchestrator.gate_impact register {registered}\n"
        f"setsid bash -c 'echo $$ >{notes}/detached; exec sleep 600' >/dev/null 2>&1 &\n"
        f"until [ -s {notes}/detached ]; do sleep 0.05; done\n"
        f"touch {notes}/ready\n"
        "while :; do sleep 0.1; done"
    )
    recorder = shlex.join(
        [
            *command,
            "-m",
            "orchestrator.gate_impact",
            "run",
            "--summary",
            str(summary),
            "--",
            "bash",
            "-c",
            gate,
        ]
    )
    caller = subprocess.Popen(
        ["bash", "-c", f"{recorder} & echo $! >{notes}/recorder; wait"],
        cwd=REPO_ROOT,
        env=_environment(tmp_path),
    )
    try:
        deadline = time.monotonic() + e2e_timeout(60)
        while not (notes / "ready").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert (notes / "ready").exists(), "the gate never started"
        caller.kill()
        caller.wait()
        pids = {name: int((notes / name).read_text()) for name in ("recorder", "gate", "detached")}
        deadline = time.monotonic() + e2e_timeout(30)
        while gate_impact._stat(pids["recorder"]) is not None and time.monotonic() < deadline:
            time.sleep(0.2)
        assert gate_impact._stat(pids["recorder"]) is None, "the recorder ran on"
    finally:
        if caller.poll() is None:
            caller.kill()
            caller.wait()
    for name in ("gate", "detached"):
        left = gate_impact._stat(pids[name])
        assert left is None or left.state in ("Z", "X"), f"the {name} process is still running"
    record = _summary(summary)
    assert record.exit_status == 128 + signal.SIGKILL, record
    assert not registered.exists()
    assert _roots(tmp_path) == []
