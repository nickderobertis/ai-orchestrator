"""Real session-setup journeys kept behind their own host-tool target."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeIs, get_args

from nx_workspace import shares_workspace_install
from published_tools import ONETASKGRAPH_BIN
from waits import until

from orchestrator.root import REPO_ROOT

#: The variable session setup's last step reads its checkout list from, in place of the
#: tracked one. Named here so a run of the real setup provisions the stand-ins below and
#: never this host's registered siblings — the one thing a journey may not do to them.
REGISTERED_CHECKOUTS_OVERRIDE = "ORCHESTRATOR_REPOS_BOOTSTRAP_CHECKOUTS"
#: The file a held stand-in's bootstrap waits for before it finishes, named through this
#: variable, which the detached job inherits from the session setup that started it.
RELEASE_VARIABLE = "STAND_IN_RELEASE"
#: How long a held stand-in's bootstrap waits for its release: longer than the three
#: session starts it has to outlast, and bounded so a journey that never releases it
#: leaves nothing running for long.
HELD_BOOTSTRAP_SECONDS = 600
#: Every status a job's state records, as the script writes it;
#: `tests/test_repos_bootstrap_docs.py` holds this to the script's own vocabulary.
JobStatus = Literal["running", "done", "failed", "stale"]
JOB_ENDINGS: frozenset[JobStatus] = frozenset({"done", "failed", "stale"})


def _is_job_status(value: str) -> TypeIs[JobStatus]:
    return value in get_args(JobStatus)


@dataclass(frozen=True)
class JobState:
    """What one checkout's `state` file records: how its job stands, and its process."""

    status: JobStatus | None = None
    pid: int | None = None

    @classmethod
    def read(cls, memo_dir: Path) -> JobState:
        state = memo_dir / "state"
        if not state.exists():
            return cls()
        fields = dict(
            row.split(" ", 1) for row in state.read_text(encoding="utf-8").splitlines() if row
        )
        status = fields.get("status")
        assert status is None or _is_job_status(status), fields
        return cls(
            status=status,
            pid=int(fields["pid"]) if "pid" in fields else None,
        )

    @property
    def ended(self) -> bool:
        return self.status in JOB_ENDINGS

    @property
    def alive(self) -> bool:
        """Whether the process the job recorded of itself still exists: a read, not a signal."""
        return self.pid is not None and Path(f"/proc/{self.pid}").exists()

    @property
    def stopped(self) -> bool:
        """Whether nothing is left running for this state, however it ended.

        Every job records its process before it bootstraps, so a state with no `pid` is
        one whose job has not yet begun — unless it has ended, when no job wrote it.
        """
        if self.pid is None:
            return self.ended
        return not self.alive


def _stand_in(root: Path, *, marks: Path, body: str = "") -> Path:
    """A real checkout whose `just bootstrap` leaves a mark and then runs `body`."""
    root.mkdir()
    (root / "justfile").write_text(
        f'bootstrap:\n    @echo "{root.name}" >> "{marks}"\n{body}',
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "init", "-q", "--initial-branch", "main"], cwd=root, check=True, capture_output=True
    )
    _commit(root, "seed")
    return root


def _commit(root: Path, subject: str) -> None:
    for command in (
        ["git", "add", "-A"],
        [
            "git",
            "-c",
            "user.name=Journey",
            "-c",
            "user.email=j@example.invalid",
            "commit",
            "-q",
            "-m",
            subject,
        ],
    ):
        subprocess.run(command, cwd=root, check=True, capture_output=True)


def _held_body() -> str:
    return (
        f"    @for _ in $(seq 1 {HELD_BOOTSTRAP_SECONDS * 10}); do"
        f' [ -f "${RELEASE_VARIABLE}" ] && break; sleep 0.1; done\n'
    )


@dataclass(frozen=True)
class ReportLine:
    """One checkout's line of the recipe's report: what happened to it, and its log."""

    outcome: str
    text: str
    log: Path | None

    @classmethod
    def of(cls, stderr: str, checkout: Path) -> ReportLine:
        """The one line naming `checkout`, which has to be there exactly once."""
        lines = [line for line in stderr.splitlines() if str(checkout) in line.split()]
        assert len(lines) == 1, stderr
        text = lines[0].strip()
        _, _, log = text.partition("(log: ")
        return cls(outcome=text.split()[0], text=text, log=Path(log.rstrip(")")) if log else None)

    @property
    def memo_dir(self) -> Path:
        assert self.log is not None, self.text
        return self.log.parent


def _await_ending(memo_dir: Path) -> JobState:
    """Wait on the sentinel a job writes — the ending in its state — and return it."""
    until(
        f"the job for {memo_dir.name} to record how it ended",
        lambda: JobState.read(memo_dir).ended,
        seconds=60,
        state=lambda: f"state {JobState.read(memo_dir)}",
    )
    return JobState.read(memo_dir)


def _await_stopped(memo_dir: Path) -> None:
    """Wait until the job for `memo_dir` has ended, or its recorded process is gone."""
    until(
        f"the job for {memo_dir.name} to stop",
        lambda: JobState.read(memo_dir).stopped,
        seconds=60,
        state=lambda: f"state {JobState.read(memo_dir)}",
    )


def _await_mark(marks: Path, name: str) -> None:
    """Wait until the stand-in `name`'s bootstrap has left its mark."""
    until(
        f"the job for {name} to be inside its bootstrap",
        lambda: name in _marks(marks),
        seconds=30,
        state=lambda: f"marks {_marks(marks)}",
    )


def _marks(marks: Path) -> list[str]:
    return sorted(marks.read_text(encoding="utf-8").split()) if marks.exists() else []


@shares_workspace_install
def test_session_setup_keeps_the_locked_plan_store_and_plan_root_in_force(
    tmp_path: Path,
) -> None:
    """The real setup verifies the locked CLI, ensures the plan root, and detaches the siblings.

    Its own exit status stays the one its toolchain earned whatever the sibling jobs do.
    """
    plans = REPO_ROOT / ".plans"
    marks = tmp_path / "marks"
    release = tmp_path / "release"
    held = _stand_in(tmp_path / "held-sibling", marks=marks, body=_held_body())
    moving = _stand_in(tmp_path / "moving-sibling", marks=marks, body=_held_body())
    failing = _stand_in(
        tmp_path / "failing-sibling",
        marks=marks,
        body='    @echo "no cargo-deny here" >&2\n    @exit 3\n',
    )
    listing = tmp_path / "checkouts"
    listing.write_text(
        f"{held}\n{moving}\n{failing}\n{tmp_path / 'absent-sibling'}\n", encoding="utf-8"
    )
    cache = tmp_path / "cache"

    def session_start() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(REPO_ROOT / "scripts" / "session-setup.sh")],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
            env={
                **os.environ,
                REGISTERED_CHECKOUTS_OVERRIDE: str(listing),
                RELEASE_VARIABLE: str(release),
                "XDG_CACHE_HOME": str(cache),
            },
        )

    try:
        first = session_start()

        assert first.returncode == 0, first.stdout + first.stderr
        adopted = (REPO_ROOT / "config" / "onetaskgraph.version").read_text().strip()
        assert f"ready (onetaskgraph: {adopted} at {ONETASKGRAPH_BIN})" in first.stderr
        assert (plans / "tasks").is_dir()
        assert (plans / "projects").is_dir()

        # Each stand-in's job was started and the absent one skipped the way the
        # registration recipe skips it; the setup returned with the held jobs still
        # inside their bootstraps, holding no memo that claims they finished.
        memo = {}
        for stand_in in (held, moving, failing):
            line = ReportLine.of(first.stderr, stand_in)
            assert line.outcome == "started", line
            memo[stand_in] = line.memo_dir
        assert ReportLine.of(first.stderr, tmp_path / "absent-sibling").outcome == "skip"
        assert "3 started, 0 running, 0 stale, 0 unchanged, 1 skipped, 0 refused, 0 failed" in (
            first.stderr
        )
        for stand_in in (held, moving):
            _await_mark(marks, stand_in.name)
            state = JobState.read(memo[stand_in])
            assert state.status == "running", state
            assert state.alive, state
            assert not (memo[stand_in] / "stamp").exists()
        assert _await_ending(memo[failing]).status == "failed"

        (moving / "README").write_text("moved under a running job\n", encoding="utf-8")
        _commit(moving, "move HEAD under the job")

        second = session_start()

        assert second.returncode == 0, second.stdout + second.stderr
        assert ReportLine.of(second.stderr, held).outcome == "running"
        stale = ReportLine.of(second.stderr, moving)
        assert stale.outcome == "stale" and "still running" in stale.text, stale
        failed = ReportLine.of(second.stderr, failing)
        assert failed.outcome == "failed" and "exit 3; started again" in failed.text, failed
        assert failed.log is not None
        assert "no cargo-deny here" in failed.log.read_text(encoding="utf-8")
        assert (
            "session-setup: a registered sibling checkout's bootstrap failed or was refused"
            in second.stderr
        )
        # The failed one was started again; neither held checkout was, while its lock
        # was held.
        assert _await_ending(memo[failing]).status == "failed"
        assert _marks(marks) == sorted([held.name, moving.name, failing.name, failing.name])

        release.write_text("", encoding="utf-8")
        assert _await_ending(memo[held]).status == "done"
        assert (memo[held] / "stamp").exists()
        # The moved checkout's job succeeded at a tree it no longer is: never trusted.
        assert _await_ending(memo[moving]).status == "stale"
        assert not (memo[moving] / "stamp").exists()

        third = session_start()

        assert third.returncode == 0, third.stdout + third.stderr
        complete = ReportLine.of(third.stderr, held)
        assert complete.outcome == "unchanged" and "completed" in complete.text, complete
        assert ReportLine.of(third.stderr, moving).outcome == "stale"
        assert _marks(marks).count(held.name) == 1
    finally:
        release.write_text("", encoding="utf-8")
        for memo_dir in (state.parent for state in cache.rglob("state")):
            _await_stopped(memo_dir)
