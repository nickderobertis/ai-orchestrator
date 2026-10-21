"""The pre-push hook's three host-impact budgets, driven end to end with stand-in gates.

`.githooks/pre-push` runs `gate_within_budgets` (`scripts/gate-budgets.sh`), whose
`record_local_direct_gate` runs the gate, for the push it names, through the recorder
`python -m orchestrator.gate_impact run`; the installed onebudgetspec then holds the push to
`gate-leftovers`, `gate-peak-temp-disk` and `gate-leaked-processes`, whose commands read
the recorder's record back with `python -m orchestrator.gate_impact report`. These
journeys drive that step as the hook calls it — the real step, the real recorder, the
installed onebudgetspec — over a budgets file registering those three budgets exactly as
the tracked root file does, with short gate commands built from real programs (`bash`,
`setsid`, `sleep`, `dd`, and a real nested `pytest` session) in place of the complete gate.
They assert on onebudgetspec's printed results, the record the recorder wrote, and the
step's exit status, and every journey ends asserting that no recorder root and no
registered scratch is left behind. `orchestrator/gate_impact.py`'s docstring states the
measurement each journey holds the recorder to.

llmlint: ignore-file[e2e_not_mocked] The journeys run short gate commands in place of
the complete `just gate`, because running the real gate inside a test is a second gate
run, which this plan's constraints forbid and ai-orchestrator#1164 already rejected for
gate-time; the plan recorded the same departure under its checklist's
`plan_criteria_require_realistic_tests`, a rule configured for plans only. Everything
under test — the recorder, the hook step, onebudgetspec, a real nested pytest session —
is real, and every publishing push runs the real gate through it.

llmlint: ignore-file[tests_mirror_real_usage] The journeys call `gate_within_budgets`, the
hook's step, rather than `.githooks/pre-push` whole, because the hook's own line runs the
complete gate; the hook line naming the step is what every publication runs.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] What these spend is
seconds of a stand-in gate apiece, most of it the recorder's own ten-second settle wait
for a leaked process — no launch and no paid turn — and what they read is
`scripts/gate-budgets.sh`, `scripts/lock-timeout.sh`, `orchestrator/gate_impact.py` and
`budgets.yaml`, which this project's key already covers.

llmlint: ignore-file[shell_test_tiers_stay_split] For the same cost: a project of their
own would narrow the key of seconds of work at the price of a catalog entry in each place
`tests/nx_inputs.py` and `tests/tree_scans/test_nx_cache_scope.py` hold every project to.
"""

from __future__ import annotations

import contextlib
import os
import re
import shlex
import signal
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from waits import end_carrying
from waits import timeout as e2e_timeout

from orchestrator.gate_impact import Leaked, PeakEntry, Record, StoppedBy
from orchestrator.root import REPO_ROOT

STEP = REPO_ROOT / "scripts" / "gate-budgets.sh"
ROOT_BUDGETS = REPO_ROOT / "budgets.yaml"
PYTHON = REPO_ROOT / ".venv" / "bin" / "python"
CHECKER = REPO_ROOT / ".venv" / "bin" / "onebudgetspec"
#: The budgets these journeys hold a push to, as the tracked root file registers them.
BUDGETS = ("gate-leftovers", "gate-peak-temp-disk", "gate-leaked-processes")
#: A result line as onebudgetspec prints it: the id, the actual value and the verdict.
RESULT = re.compile(r"^budget (?P<id>[\w-]+): actual (?P<value>\S+) .* — (?P<verdict>\w+)")


@pytest.fixture(autouse=True)
def _nothing_outlives_the_test(tmp_path: Path) -> Iterator[None]:
    """End every recorder, gate and stand-in this test started once it is over, however
    it ended — a failure, or a bound that elapsed and killed only its own child — and fail
    a test that left one. Each carries this test's own directory, its cache and temporary
    roots under ``tmp_path``, so nothing else is found."""
    yield
    left = end_carrying(tmp_path)
    assert not left, f"processes {left} this test started were still running when it ended"


def _tracked() -> list[dict[str, Any]]:
    document = yaml.safe_load(ROOT_BUDGETS.read_text(encoding="utf-8"))
    return [budget for budget in document["budgets"] if budget["id"] in BUDGETS]


def _absolute(command: list[str]) -> list[str]:
    """A command from the tracked file, made to run from any directory."""
    first, *rest = command
    return [str(REPO_ROOT / first) if "/" in first else first, *rest]


def _alive(pid: int) -> bool:
    """Whether ``pid`` is a live process, a zombie counting as gone."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return False
    return state not in ("Z", "X")


@dataclass(frozen=True)
class Pushed:
    """What one run of the step answered, and the record its recorder wrote."""

    returncode: int
    stdout: str
    stderr: str
    record: Record

    def value(self, budget: str) -> float:
        return float(self._match(budget)["value"])

    def verdict(self, budget: str) -> str:
        return self._match(budget)["verdict"]

    def detail(self, budget: str) -> str:
        """The detail line onebudgetspec prints under the budget's result line."""
        lines = self.stdout.splitlines()
        for index, line in enumerate(lines):
            if line.startswith(f"budget {budget}:"):
                return lines[index + 1].strip()
        raise AssertionError(f"no result for {budget} in {self.stdout}")

    def _match(self, budget: str) -> re.Match[str]:
        for line in self.stdout.splitlines():
            match = RESULT.match(line)
            if match and match["id"] == budget:
                return match
        raise AssertionError(f"no result for {budget} in {self.stdout}{self.stderr}")

    @property
    def leaked(self) -> dict[int, Leaked]:
        return {process.pid: process for process in self.record.leaked}


@dataclass(frozen=True)
class Host:
    """One journey's own state, cache and runs roots, and where its stand-ins write."""

    root: Path

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def roots(self) -> Path:
        """Where every recorder this journey starts makes its gate root."""
        return self.cache / "aio-gate"

    @property
    def record(self) -> Path:
        return self.state / "ai-orchestrator" / "gate-host-impact-push.json"

    @property
    def notes(self) -> Path:
        """A directory outside every gate root, where a stand-in leaves what it saw."""
        return self.root / "notes"

    def environment(self, **extra: str) -> dict[str, str]:
        environment = {
            key: value
            for key, value in os.environ.items()
            # A push or a recorder the enclosing process runs under is not this journey's.
            if key not in {"ORCHESTRATOR_GATE_PUSH", "ORCHESTRATOR_GATE_SCRATCH"}
        }
        environment.update(
            {
                "XDG_STATE_HOME": str(self.state),
                "XDG_CACHE_HOME": str(self.cache),
                "ONEPIPELINE_RUNS_DIR": str(self.root / "runs"),
                **extra,
            }
        )
        return environment

    def budgets(self, *, peak_threshold: float | None = None) -> Path:
        """The three budgets as the tracked root file registers them, commands absolute."""
        budgets = _tracked()
        for budget in budgets:
            budget["command"] = _absolute(budget["command"])
            if peak_threshold is not None and budget["id"] == "gate-peak-temp-disk":
                budget["threshold"] = peak_threshold
        path = self.root / "budgets.yaml"
        path.write_text(yaml.safe_dump({"schema_version": 1, "budgets": budgets}), encoding="utf-8")
        return path

    def gate(self, body: str, *, name: str = "stand-in-gate") -> Path:
        script = self.root / name
        script.write_text(f"#!/usr/bin/env bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
        script.chmod(0o755)
        return script

    def note(self, name: str) -> str:
        return shlex.quote(str(self.notes / name))

    def read(self, name: str) -> str:
        return (self.notes / name).read_text(encoding="utf-8").strip()

    def assert_clean(self, *registered: Path) -> None:
        """No recorder root and no registered scratch is left on this journey's host."""
        assert not self.roots.exists() or not any(self.roots.iterdir()), list(self.roots.iterdir())
        for path in registered:
            assert not os.path.lexists(path), path


@pytest.fixture
def host(tmp_path: Path) -> Iterator[Host]:
    built = Host(tmp_path)
    for directory in (built.state, built.notes, built.root / "runs"):
        directory.mkdir()
    yield built


def _command(budgets: Path, gate: Path) -> list[str]:
    return [
        "bash",
        "-c",
        f'. "{STEP}"\ngate_within_budgets "$1" "$2"',
        "step",
        str(budgets),
        str(gate),
    ]


def _finished(host: Host, ran: subprocess.CompletedProcess[str]) -> Pushed:
    record = Record.from_json(host.record.read_text(encoding="utf-8"))
    return Pushed(ran.returncode, ran.stdout, ran.stderr, record)


def _push(host: Host, budgets: Path, gate: Path, **extra: str) -> Pushed:
    """Run the hook's step as the hook runs it, with the stand-in gate."""
    ran = subprocess.run(
        _command(budgets, gate),
        cwd=REPO_ROOT,
        env=host.environment(**extra),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        check=False,
    )
    return _finished(host, ran)


def _wait_for(path: Path, *, test: str = "-s") -> str:
    """A shell line waiting, for a bounded time, until ``path`` passes ``test``.

    Bounded so a stand-in whose peer never writes fails at once, naming the file, rather
    than waiting out the journey's own timeout.
    """
    named = shlex.quote(str(path))
    return (
        f"deadline=$((SECONDS + {int(e2e_timeout(15))})); "
        f"until [ {test} {named} ]; do "
        f'[ "$SECONDS" -lt "$deadline" ] || {{ echo "stand-in: {named} never appeared" >&2; '
        "exit 97; }; sleep 0.05; done"
    )


def test_a_real_pytest_session_that_leaks_is_refused_on_both_figures(host: Host) -> None:
    """A gate that is a real nested pytest session, over a planted test that writes
    through `tempfile` and starts a `setsid`-detached child.

    The test passes, so the gate exits 0, and the push is refused on `gate-leftovers` and
    `gate-leaked-processes` with that child named: the figures follow the gate's real tool
    chain, `TMPDIR` reaching `tempfile` through pytest and the child outliving the session.
    """
    planted = host.root / "planted"
    planted.mkdir()
    (planted / "test_planted.py").write_text(
        "import pathlib, subprocess, tempfile\n\n"
        "def test_leaks():\n"
        "    handle, name = tempfile.mkstemp(prefix='planted-')\n"
        "    subprocess.Popen(\n"
        "        ['setsid', 'bash', '-c', 'echo $$ >\"$0\"; exec sleep 3171', "
        f"{str(host.notes / 'child')!r}],\n"
        "        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,\n"
        "    )\n"
        f"    pathlib.Path({str(host.notes / 'tempdir')!r}).write_text(tempfile.gettempdir())\n",
        encoding="utf-8",
    )
    gate = host.gate(
        f"{shlex.quote(str(PYTHON))} -m pytest -q -p no:cacheprovider -p no:xdist "
        f"--rootdir {shlex.quote(str(planted))} -c /dev/null "
        f"{shlex.quote(str(planted / 'test_planted.py'))}\n"
        f"{_wait_for(host.notes / 'child')}"
    )

    pushed = _push(host, host.budgets(), gate)

    assert pushed.returncode == 1, pushed.stdout + pushed.stderr
    assert pushed.record.exit_status == 0, pushed.stderr
    child = int(host.read("child"))
    assert pushed.verdict("gate-leftovers") == "over", pushed.stdout
    assert pushed.value("gate-leftovers") >= 1, pushed.stdout
    assert "planted-" in pushed.detail("gate-leftovers"), pushed.stdout
    assert pushed.verdict("gate-leaked-processes") == "over", pushed.stdout
    assert pushed.value("gate-leaked-processes") == 1, pushed.stdout
    assert f"pid {child} " in pushed.detail("gate-leaked-processes"), pushed.stdout
    assert "sleep 3171" in pushed.leaked[child].command, pushed.record
    assert pushed.verdict("gate-peak-temp-disk") == "within", pushed.stdout
    assert "gate-leftovers, gate-leaked-processes" in pushed.stderr, pushed.stderr
    assert host.read("tempdir").startswith(str(host.roots)), host.read("tempdir")
    assert not _alive(child)
    host.assert_clean()


def test_a_clean_gate_is_admitted_with_all_three_within(host: Host) -> None:
    """A gate that leaves nothing, holds next to nothing and starts nothing that outlives it."""
    pushed = _push(host, host.budgets(), host.gate("echo working >&2"))

    assert pushed.returncode == 0, pushed.stdout + pushed.stderr
    for budget in BUDGETS:
        assert pushed.verdict(budget) == "within", pushed.stdout
    assert pushed.value("gate-leftovers") == 0
    assert pushed.value("gate-leaked-processes") == 0
    assert pushed.record.leaked == []
    assert pushed.record.samples >= 1
    host.assert_clean()


def test_two_entries_left_in_tmpdir_are_refused_as_two(host: Host) -> None:
    pushed = _push(host, host.budgets(), host.gate('touch "$TMPDIR/one" "$TMPDIR/two"'))

    assert pushed.returncode == 1, pushed.stdout + pushed.stderr
    assert pushed.verdict("gate-leftovers") == "over", pushed.stdout
    assert pushed.value("gate-leftovers") == 2, pushed.stdout
    assert {entry.path for entry in pushed.record.leftover_top} == {"one", "two"}
    assert pushed.verdict("gate-leaked-processes") == "within", pushed.stdout
    assert "budget gate-leftovers is over" in pushed.stderr, pushed.stderr
    host.assert_clean()


def test_a_detached_process_is_refused_as_leaked_and_stopped(host: Host) -> None:
    """A `setsid`-detached `sleep` the gate starts and leaves running."""
    gate = host.gate(
        f"setsid bash -c 'echo $$ >{host.note('pid')}; exec sleep 3172' >/dev/null 2>&1 &\n"
        f"{_wait_for(host.notes / 'pid')}"
    )

    pushed = _push(host, host.budgets(), gate)

    pid = int(host.read("pid"))
    assert pushed.returncode == 1, pushed.stdout + pushed.stderr
    assert pushed.verdict("gate-leaked-processes") == "over", pushed.stdout
    assert pushed.value("gate-leaked-processes") == 1, pushed.stdout
    assert pushed.leaked[pid].stopped_by == StoppedBy.TERM, pushed.record
    assert "sleep 3172" in pushed.leaked[pid].command, pushed.record
    assert not _alive(pid)
    host.assert_clean()


def test_a_detached_parent_and_its_live_child_are_both_leaked_and_nothing_else(
    host: Host,
) -> None:
    """A detached parent alive with a live child of its own, beside a process the journey
    started outside the recorder: the descendant tree is walked at every depth, and only it."""
    unrelated = subprocess.Popen(["sleep", "3173"])
    try:
        gate = host.gate(
            "setsid bash -c '"
            f"echo $$ >{host.note('parent')}; sleep 3174 & echo $! >{host.note('child')}; wait"
            "' >/dev/null 2>&1 &\n"
            f"{_wait_for(host.notes / 'child')}"
        )

        pushed = _push(host, host.budgets(), gate)

        parent, child = int(host.read("parent")), int(host.read("child"))
        assert pushed.returncode == 1, pushed.stdout + pushed.stderr
        assert pushed.value("gate-leaked-processes") == 2, pushed.stdout
        assert set(pushed.leaked) == {parent, child}, pushed.record
        assert pushed.leaked[child].ppid == parent, pushed.record
        assert not _alive(parent)
        assert not _alive(child)
        assert unrelated.poll() is None, "the recorder stopped a process outside its tree"
    finally:
        unrelated.kill()
        unrelated.wait()
    host.assert_clean()


def test_a_process_that_exits_within_the_settle_wait_is_still_leaked(host: Host) -> None:
    gate = host.gate(
        f"setsid bash -c 'echo $$ >{host.note('pid')}; exec sleep 2' >/dev/null 2>&1 &\n"
        f"{_wait_for(host.notes / 'pid')}"
    )

    pushed = _push(host, host.budgets(), gate)

    pid = int(host.read("pid"))
    assert pushed.returncode == 1, pushed.stdout + pushed.stderr
    assert pushed.value("gate-leaked-processes") == 1, pushed.stdout
    assert pushed.leaked[pid].stopped_by == StoppedBy.EXITED, pushed.record
    assert "stopped by exited" in pushed.detail("gate-leaked-processes"), pushed.stdout
    host.assert_clean()


def test_files_a_leaked_process_removes_when_stopped_are_still_counted(host: Host) -> None:
    """A detached descendant that removes its own files in `TMPDIR` when sent TERM: the
    leftovers are counted before any signal, so both it and its files are reported."""
    # One process, so the tree it leaves is that process alone: no helper child of its own.
    body = (
        "import os, pathlib, shutil, signal, sys, time\n"
        "mine = pathlib.Path(os.environ['TMPDIR'], 'mine')\n"
        "mine.mkdir()\n"
        "(mine / 'held').touch()\n"
        "def stopped(*_):\n"
        "    shutil.rmtree(mine)\n"
        f"    pathlib.Path({str(host.notes / 'cleaned')!r}).touch()\n"
        "    sys.exit(0)\n"
        "signal.signal(signal.SIGTERM, stopped)\n"
        f"pathlib.Path({str(host.notes / 'pid')!r}).write_text(str(os.getpid()))\n"
        "while True:\n"
        "    time.sleep(1)\n"
    )
    gate = host.gate(
        f"setsid {shlex.quote(str(PYTHON))} -c {shlex.quote(body)} >/dev/null 2>&1 &\n"
        f"{_wait_for(host.notes / 'pid')}"
    )

    pushed = _push(host, host.budgets(), gate)

    pid = int(host.read("pid"))
    assert pushed.returncode == 1, pushed.stdout + pushed.stderr
    assert (host.notes / "cleaned").exists(), "the leaked process never removed its files"
    assert pushed.value("gate-leaked-processes") == 1, pushed.stdout
    assert pushed.leaked[pid].stopped_by == StoppedBy.TERM, pushed.record
    assert pushed.value("gate-leftovers") == 2, pushed.stdout
    assert "mine (2 entries" in pushed.detail("gate-leftovers"), pushed.stdout
    host.assert_clean()


def _held(host: Host, mebibytes: int) -> str:
    """A stand-in holding a planted file across a sample, measuring it with `du` itself."""
    return (
        f'dd if=/dev/zero of="$TMPDIR/held" bs=1M count={mebibytes} status=none\n'
        f'du -s --block-size=1 "$TMPDIR" | cut -f1 >{host.note("du")}\n'
        "sleep 6\n"
        'rm "$TMPDIR/held"'
    )


def test_the_peak_is_no_smaller_than_what_the_gate_itself_measured(host: Host) -> None:
    pushed = _push(host, host.budgets(), host.gate(_held(host, 8)))

    measured = int(host.read("du"))
    assert pushed.returncode == 0, pushed.stdout + pushed.stderr
    assert pushed.verdict("gate-peak-temp-disk") == "within", pushed.stdout
    assert pushed.value("gate-peak-temp-disk") * 1024**3 >= measured, pushed.stdout
    assert pushed.record.peak_bytes >= measured, pushed.record
    assert PeakEntry("held", 8 * 1024**2) in pushed.record.peak_top, pushed.record
    host.assert_clean()


def test_a_peak_over_the_threshold_is_reported_as_measured_and_refused(host: Host) -> None:
    """The same budgets but for the peak's threshold, lowered to 4 MiB so the file stays
    small: the value is the measured peak, not one capped at the threshold."""
    threshold = 4 / 1024
    pushed = _push(host, host.budgets(peak_threshold=threshold), host.gate(_held(host, 8)))

    measured = int(host.read("du"))
    assert pushed.returncode == 1, pushed.stdout + pushed.stderr
    assert pushed.verdict("gate-peak-temp-disk") == "over", pushed.stdout
    assert pushed.value("gate-peak-temp-disk") > threshold, pushed.stdout
    assert pushed.value("gate-peak-temp-disk") * 1024**3 >= measured, pushed.stdout
    assert "budget gate-peak-temp-disk is over" in pushed.stderr, pushed.stderr
    host.assert_clean()


def test_an_allocation_between_two_samples_is_not_seen(host: Host) -> None:
    """The stated sampling limit: a large file created and removed between the first sample
    and the one at exit is absent from the peak."""
    gate = host.gate(
        "sleep 1\n"
        'dd if=/dev/zero of="$TMPDIR/brief" bs=1M count=32 status=none\n'
        f'du -s --block-size=1 "$TMPDIR" | cut -f1 >{host.note("du")}\n'
        'rm "$TMPDIR/brief"'
    )

    pushed = _push(host, host.budgets(), gate)

    assert pushed.returncode == 0, pushed.stdout + pushed.stderr
    assert int(host.read("du")) >= 32 * 1024**2
    assert pushed.record.samples == 2, pushed.record
    assert pushed.record.peak_bytes < 1024**2, pushed.record
    host.assert_clean()


def test_a_zero_byte_uv_lock_is_not_counted_and_a_non_empty_one_is(host: Host) -> None:
    gate = host.gate(
        ': >"$TMPDIR/uv-0123456789abcdef.lock"\necho held >"$TMPDIR/uv-fedcba9876543210.lock"'
    )

    pushed = _push(host, host.budgets(), gate)

    assert pushed.returncode == 1, pushed.stdout + pushed.stderr
    assert pushed.value("gate-leftovers") == 1, pushed.stdout
    assert pushed.record.excluded_uv_locks == 1, pushed.record
    assert [entry.path for entry in pushed.record.leftover_top] == ["uv-fedcba9876543210.lock"]
    assert "1 zero-byte uv lock(s) not counted" in pushed.detail("gate-leftovers"), pushed.stdout
    host.assert_clean()


def test_registered_scratch_outside_tmpdir_is_counted_and_removed(host: Host) -> None:
    outside = host.root / "outside"
    gate = host.gate(
        f"mkdir {shlex.quote(str(outside))}\ntouch {shlex.quote(str(outside / 'kept'))}\n"
        f"{shlex.quote(str(PYTHON))} -m orchestrator.gate_impact register "
        f"{shlex.quote(str(outside))}"
    )

    pushed = _push(host, host.budgets(), gate)

    assert pushed.returncode == 1, pushed.stdout + pushed.stderr
    assert pushed.value("gate-leftovers") == 2, pushed.stdout
    assert pushed.record.leftover_top[0].path == str(outside), pushed.record
    assert f"{outside} (2 entries" in pushed.detail("gate-leftovers"), pushed.stdout
    host.assert_clean(outside)


def test_an_unreadable_directory_left_behind_is_still_removed(host: Host) -> None:
    gate = host.gate(
        'mkdir -p "$TMPDIR/locked/inner"\ntouch "$TMPDIR/locked/inner/file"\n'
        'chmod 000 "$TMPDIR/locked"'
    )

    pushed = _push(host, host.budgets(), gate)

    assert pushed.returncode == 1, pushed.stdout + pushed.stderr
    assert pushed.value("gate-leftovers") >= 1, pushed.stdout
    host.assert_clean()


def test_a_dispatch_s_own_scratch_locations_are_not_handed_to_the_gate(host: Host) -> None:
    """A push from a dispatch inherits two scratch locations writers here prefer over
    `TMPDIR`; the gate sees neither, and sees its own root in all three temp variables."""
    gate = host.gate(
        'echo "${ONEPIPELINE_NODE_SCRATCH_DIR-unset} ${PYTEST_DEBUG_TEMPROOT-unset} '
        f'$TMPDIR $TMP $TEMP" >{host.note("seen")}'
    )

    pushed = _push(
        host,
        host.budgets(),
        gate,
        ONEPIPELINE_NODE_SCRATCH_DIR=str(host.root / "node-scratch"),
        PYTEST_DEBUG_TEMPROOT=str(host.root / "pytest-root"),
    )

    assert pushed.returncode == 0, pushed.stdout + pushed.stderr
    node, pytest_root, *temps = host.read("seen").split()
    assert (node, pytest_root) == ("unset", "unset")
    assert len(set(temps)) == 1 and temps[0].startswith(f"{host.roots}/"), temps
    assert temps[0].endswith("/tmp"), temps
    host.assert_clean()


def test_a_record_for_another_push_is_refused_naming_the_file(host: Host) -> None:
    budgets = host.budgets()
    pushed = _push(host, budgets, host.gate("true"))
    assert pushed.returncode == 0, pushed.stdout + pushed.stderr

    checked = subprocess.run(
        [str(CHECKER), "check", str(budgets)],
        cwd=REPO_ROOT,
        env=host.environment(ORCHESTRATOR_GATE_PUSH="another-push"),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )

    assert checked.returncode == 3, checked.stdout + checked.stderr
    for budget in BUDGETS:
        line = next(
            line for line in checked.stdout.splitlines() if line.startswith(f"budget {budget}:")
        )
        assert "error" in line, checked.stdout
    assert f"the gate record at {host.record} is for an earlier push ({pushed.record.push})" in (
        checked.stdout + checked.stderr
    )
    host.assert_clean()


def test_a_recorder_inside_another_recorder_s_gate_roots_inside_its_tmpdir(host: Host) -> None:
    inner = host.notes / "inner.json"
    gate = host.gate(
        f'echo "$TMPDIR" >{host.note("outer")}\n'
        f"{shlex.quote(str(PYTHON))} -m orchestrator.gate_impact run --summary "
        f"{shlex.quote(str(inner))} -- bash -c "
        + shlex.quote(f'echo "$TMPDIR" >{host.note("inner")}; touch "$TMPDIR/inside"')
    )

    pushed = _push(host, host.budgets(), gate)

    outer_tmp, inner_tmp = host.read("outer"), host.read("inner")
    assert inner_tmp.startswith(f"{outer_tmp}/"), (outer_tmp, inner_tmp)
    assert Record.from_json(inner.read_text(encoding="utf-8")).leftover_entries == 1
    assert pushed.returncode == 0, pushed.stdout + pushed.stderr
    assert pushed.value("gate-leftovers") == 0, pushed.stdout
    host.assert_clean()


def _end_step(process: subprocess.Popen[str], host: Host) -> None:
    """End a step this journey started, and every stand-in process beneath it.

    TERM to the step's whole group first, which the recorder passes to its gate and then
    cleans up after; KILL to the group if it is still there; and each detached process
    the stand-in recorded by pid, which left the group when it was detached.
    """
    for number in (signal.SIGTERM, signal.SIGKILL):
        if process.poll() is not None:
            break
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, number)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.communicate(timeout=e2e_timeout(30))
    recorded = host.notes / "pid"
    if recorded.is_file() and recorded.read_text(encoding="utf-8").strip().isdigit():
        pid = int(recorded.read_text(encoding="utf-8"))
        if _alive(pid):
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)


def test_two_recorders_at_once_each_count_and_stop_only_their_own(tmp_path: Path) -> None:
    """Two pushes gated at once, each over a stand-in that leaves files, registers scratch
    outside its root and detaches a process. The first finishes while the second still
    runs: each record holds only its own, and each recorder removes and stops only its own,
    the other's still present and alive until that one finishes."""
    first, second = Host(tmp_path / "first"), Host(tmp_path / "second")
    for each in (first, second):
        for directory in (each.state, each.notes, each.root / "runs"):
            directory.mkdir(parents=True)
    # One cache, so both recorders make their roots in one directory.
    shared = tmp_path / "cache"
    released = tmp_path / "released"

    def stand_in(each: Host, name: str, until: str) -> Path:
        scratch = each.root / f"scratch-{name}"
        return each.gate(
            f'touch "$TMPDIR/left-{name}"\n'
            f"mkdir {shlex.quote(str(scratch))}\n"
            f"{shlex.quote(str(PYTHON))} -m orchestrator.gate_impact register "
            f"{shlex.quote(str(scratch))}\n"
            f"setsid bash -c 'echo $$ >{each.note('pid')}; exec sleep 3175' >/dev/null 2>&1 &\n"
            f"{_wait_for(each.notes / 'pid')}\n"
            f"echo ready >{each.note('ready')}\n"
            f"{until}"
        )

    first_gate = stand_in(first, "first", _wait_for(second.notes / "ready"))
    second_gate = stand_in(second, "second", _wait_for(released, test="-e"))
    environments = {each: each.environment(XDG_CACHE_HOME=str(shared)) for each in (first, second)}
    processes = {
        each: subprocess.Popen(
            _command(each.budgets(), gate),
            cwd=REPO_ROOT,
            env=environments[each],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # Its own process group, so the clean-up below reaches the recorder and the
            # stand-in beneath the step, not the step's shell alone.
            start_new_session=True,
        )
        for each, gate in ((first, first_gate), (second, second_gate))
    }
    try:
        out, err = processes[first].communicate(timeout=e2e_timeout(180))
        done = _finished(
            first, subprocess.CompletedProcess([], processes[first].returncode, out, err)
        )
        first_pid, second_pid = int(first.read("pid")), int(second.read("pid"))

        assert done.returncode == 1, out + err
        assert set(done.leaked) == {first_pid}, done.record
        assert {entry.path for entry in done.record.leftover_top} == {
            "left-first",
            str(first.root / "scratch-first"),
        }, done.record
        assert not _alive(first_pid)
        assert not (first.root / "scratch-first").exists()
        assert _alive(second_pid), "the first recorder stopped the second one's process"
        assert (second.root / "scratch-second").exists(), "the first removed the second's scratch"
        assert processes[second].poll() is None

        released.touch()
        out, err = processes[second].communicate(timeout=e2e_timeout(180))
        later = _finished(
            second, subprocess.CompletedProcess([], processes[second].returncode, out, err)
        )

        assert later.returncode == 1, out + err
        assert set(later.leaked) == {second_pid}, later.record
        assert {entry.path for entry in later.record.leftover_top} == {
            "left-second",
            str(second.root / "scratch-second"),
        }, later.record
        assert not _alive(second_pid)
    finally:
        released.touch()
        for each, process in processes.items():
            _end_step(process, each)
    for each in (first, second):
        Host(tmp_path).assert_clean(each.root / f"scratch-{each.root.name}")
        assert not _alive(int(each.read("pid"))), "a stand-in process outlived the journey"
    assert not any((shared / "aio-gate").iterdir())


def test_a_leaked_process_s_credentials_stay_out_of_the_record_and_the_result(
    host: Host,
) -> None:
    """A leaked process whose command line carries two credential values: one the push's
    own environment holds, and one only in the leaked process's own environment. Neither
    reaches the record or onebudgetspec's printed result; each is named instead."""
    shared, own = "pushs-own-credential-0123456789", "the-leaked-ones-own-credential-98765"
    gate = host.gate(
        f"setsid env LEAKED_API_TOKEN={own} bash -c "
        + shlex.quote(
            f"echo $$ >{host.note('pid')}; "
            'exec -a "worker $GATE_SERVICE_TOKEN $LEAKED_API_TOKEN" sleep 3177'
        )
        + " >/dev/null 2>&1 &\n"
        + _wait_for(host.notes / "pid")
    )

    pushed = _push(host, host.budgets(), gate, GATE_SERVICE_TOKEN=shared)

    pid = int(host.read("pid"))
    recorded = host.record.read_text(encoding="utf-8")
    assert pushed.returncode == 1, pushed.stdout + pushed.stderr
    assert pushed.value("gate-leaked-processes") == 1, pushed.stdout
    for secret in (shared, own):
        assert secret not in recorded
        assert secret not in pushed.stdout + pushed.stderr
    command = pushed.leaked[pid].command
    assert command.startswith("worker <redacted:GATE_SERVICE_TOKEN> <redacted:LEAKED_API_TOKEN>")
    assert "<redacted:LEAKED_API_TOKEN>" in pushed.detail("gate-leaked-processes")
    host.assert_clean()


def test_a_gate_that_removes_its_own_temporary_directory_is_refused_as_unmeasured(
    host: Host,
) -> None:
    """What such a gate left cannot be counted, so nothing is recorded for the push and
    each budget errors on the missing record: the push is refused, never admitted on a
    partial count."""
    budgets = host.budgets()
    ran = subprocess.run(
        _command(budgets, host.gate('touch "$TMPDIR/left"\nrm -rf "$TMPDIR"')),
        cwd=REPO_ROOT,
        env=host.environment(),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        check=False,
    )

    assert ran.returncode == 1, ran.stdout + ran.stderr
    assert "could not be counted and no record was written" in ran.stderr, ran.stderr
    assert not host.record.exists()
    for budget in BUDGETS:
        (line,) = [one for one in ran.stdout.splitlines() if one.startswith(f"budget {budget}:")]
        assert "error" in line, ran.stdout
    assert f"the budget check of {budgets} errored" in ran.stderr, ran.stderr
    host.assert_clean()
