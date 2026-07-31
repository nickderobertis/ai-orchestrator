"""Real-CLI journeys for run ownership: attribution, the view, and stopping.

Every boundary here is the production one. `just orchestrate` is launched from an
environment shaped like a planner's own session, so the provenance it records is
the one the ordinary path records — no flags, no fixture written by hand. `just
runs` and `just stop` are then driven as a second planner would drive them, from a
*different* session, which is the only way the refusal being tested can be real.

Only the paid harness is faked, at the seam onejudge exposes for it: both the
orchestrator agent and its worker run the deterministic `fake_backend`, and the
worker parks at a release barrier so a stop lands on a run with live work below it
rather than on an idle process. That worker is a real dispatch tree — round owner,
watchdog, onejudge, provider — which is what makes "no orphaned worker" mean
anything.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml
from process_tree import is_running
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator import BASE_CONFIG, REPO_ROOT
from orchestrator.launch import provenance_dir, read_launch_info, session_fingerprint
from orchestrator.stop import RecordedOwner, recorded_owners, run_tree
from orchestrator.watchdog import ProcessId

FAKE_BACKEND = REPO_ROOT / "tests" / "e2e" / "fake_backend.py"

#: Everything a harness exports to say which session it is. Cleared before each
#: launch so a test's session is the one under test, not the one running the suite.
_LAUNCHER_VARIABLES = (
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
    "CODEX_SANDBOX",
    "CODEX_HOME",
    "ORCHESTRATOR_LAUNCHER",
    "ORCHESTRATOR_LAUNCHER_SESSION",
    "ONEHARNESS_HISTORY_LABELS",
)


def _session_env(
    tmp_path: Path, session_id: str | None, launcher: str = "claude-code"
) -> dict[str, str]:
    """The environment one planner session hands the commands it runs.

    ``None`` is a plain shell: no harness names it, so everything it launches is
    unattributable — the state every run on this host was in before provenance was
    populated, and the one a stop must still refuse.
    """
    environment = {**os.environ}
    for name in _LAUNCHER_VARIABLES:
        environment.pop(name, None)
    # The protected record belongs to this test, not to the developer's own state.
    environment["XDG_STATE_HOME"] = str(tmp_path / "state")
    if session_id is not None and launcher == "claude-code":
        environment["CLAUDECODE"] = "1"
        environment["CLAUDE_CODE_SESSION_ID"] = session_id
    elif session_id is not None:
        environment["CODEX_THREAD_ID"] = session_id
    return environment


def _base(tmp_path: Path) -> Path:
    value = yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8"))
    value["provider"] = {"kind": "command", "command": [sys.executable, str(FAKE_BACKEND)]}
    path = tmp_path / "base.yaml"
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    return path


def _plan(tmp_path: Path, name: str) -> Path:
    """A one-worker plan whose worker parks until the test releases it."""
    path = tmp_path / f"{name}-plan.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "name": name,
                "tasks": [
                    {
                        "id": "worker",
                        "persona": "engineer",
                        "task": (
                            f"slow-branch {tmp_path / f'{name}.ticks'} live-edit-slow "
                            f"live-edit-ready={tmp_path / f'{name}.ready'} "
                            f"live-edit-release={tmp_path / f'{name}.release'}"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


@dataclass(frozen=True)
class Launch:
    """One launched run and the session that launched it."""

    run_id: str
    runs: Path
    env: dict[str, str]
    plan: Path

    @property
    def run_dir(self) -> Path:
        return self.runs / self.run_id


@pytest.fixture
def launches(tmp_path: Path) -> Iterator[list[Launch]]:
    """Force-stop every run a test launched, however the test ended."""
    started: list[Launch] = []
    yield started
    for launch in started:
        subprocess.run(
            ["just", "stop", launch.run_id, "--runs-dir", str(launch.runs), "--force"],
            cwd=REPO_ROOT,
            env=launch.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=e2e_timeout(120),
        )


def _orchestrate(
    tmp_path: Path,
    runs: Path,
    onejudge_bin: str,
    name: str,
    env: dict[str, str],
    started: list[Launch],
) -> Launch:
    plan = _plan(tmp_path, name)
    launched = subprocess.run(
        [
            "just",
            "orchestrate",
            str(plan),
            "--runs-dir",
            str(runs),
            "--base",
            str(_base(tmp_path)),
            "--onejudge-bin",
            onejudge_bin,
            "--skill-command",
            sys.executable,
            str(FAKE_BACKEND),
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    launch = Launch(str(json.loads(launched.stdout)["run_id"]), runs, env, plan)
    started.append(launch)
    return launch


def _runs_view(runs: Path, env: dict[str, str], *extra: str) -> str:
    viewed = subprocess.run(
        ["just", "runs", "--runs-dir", str(runs), *extra],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=e2e_timeout(180),
    )
    assert viewed.returncode == 0, viewed.stderr
    return viewed.stdout


def _stop(launch: Launch, env: dict[str, str], *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["just", "stop", launch.run_id, "--runs-dir", str(launch.runs), *extra],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=e2e_timeout(180),
    )


def _await_parked_worker(launch: Launch, name: str, tmp_path: Path) -> tuple[RecordedOwner, ...]:
    """Wait until the run has a real dispatch tree sitting at the worker's barrier."""
    ready = tmp_path / f"{name}.ready"
    wait = deadline(180)
    while time.monotonic() < wait:
        owners = recorded_owners(launch.run_dir)
        if ready.is_file() and any(item.source.startswith("round-") for item in owners):
            return owners
        time.sleep(0.05)
    stderr = launch.run_dir / "orchestrator" / "stderr.log"
    detail = stderr.read_text(encoding="utf-8") if stderr.is_file() else "<no stderr>"
    raise AssertionError(f"the run never reached its worker barrier: {detail}")


def _await_gone(pids: set[ProcessId]) -> set[ProcessId]:
    wait = deadline(60)
    while time.monotonic() < wait:
        alive = {pid for pid in pids if is_running(pid)}
        if not alive:
            return set()
        time.sleep(0.05)
    return {pid for pid in pids if is_running(pid)}


def test_orchestrate_records_this_session_and_runs_shows_who_owns_each_run(
    tmp_path: Path, onejudge_bin: str, launches: list[Launch]
) -> None:
    """The ordinary launch is attributable, and the view every planner reads says so."""
    runs = tmp_path / "runs"
    planner = _session_env(tmp_path, "session-alpha")
    mine = _orchestrate(tmp_path, runs, onejudge_bin, "owned", planner, launches)

    # No flags were passed: the run recorded the session it was launched from.
    launch_id = read_launch_info(mine.run_dir)
    assert launch_id is not None
    record = json.loads(
        (
            Path(planner["XDG_STATE_HOME"]) / "ai-orchestrator" / "launches" / f"{launch_id}.json"
        ).read_text(encoding="utf-8")
    )
    assert record["launcher"] == "claude-code"
    assert record["launcher_session_id"] == "session-alpha"

    # And the sensitive half stayed out of the repository: the run directory holds
    # only the join key, exactly as the scheme requires.
    assert provenance_dir() != mine.run_dir
    leaked = [
        str(path)
        for path in mine.run_dir.rglob("*")
        if path.is_file() and b"session-alpha" in path.read_bytes()
    ]
    assert leaked == []

    # A second, unattributable launch: the state every run on this host was already in.
    stranger = _session_env(tmp_path, None)
    theirs = _orchestrate(tmp_path, runs, onejudge_bin, "unowned", stranger, launches)
    assert read_launch_info(theirs.run_dir) is not None

    owner_view = _runs_view(runs, planner)
    assert f"{mine.run_id}  [mine]" in owner_view
    # A run with no provenance reads as explicitly unknown, never as the caller's.
    assert f"{theirs.run_id}  [unknown]" in owner_view

    # The same runs, read by another planner: neither is theirs, and the owned one
    # is named by its launching session rather than by a session id nobody may print.
    other = _session_env(tmp_path, "session-beta")
    other_view = _runs_view(runs, other)
    assert f"{mine.run_id}  [claude-code:{session_fingerprint('session-alpha')}]" in other_view
    assert "session-alpha" not in other_view
    assert f"{theirs.run_id}  [unknown]" in other_view

    assert mine.run_id in _runs_view(runs, planner, "--mine")
    assert theirs.run_id not in _runs_view(runs, planner, "--mine")
    assert _runs_view(runs, other, "--mine").strip() == "No runs launched by this session."


def test_stop_refuses_another_planners_run_and_an_unattributable_one(
    tmp_path: Path, onejudge_bin: str, launches: list[Launch]
) -> None:
    """The guard that would have prevented the incident, and the override that reports."""
    runs = tmp_path / "runs"
    planner = _session_env(tmp_path, "session-alpha")
    other = _session_env(tmp_path, "session-beta")
    theirs = _orchestrate(tmp_path, runs, onejudge_bin, "guarded", planner, launches)
    owners = _await_parked_worker(theirs, "guarded", tmp_path)
    tree = run_tree(owners)

    refused = _stop(theirs, other)
    assert refused.returncode == 2, refused.stdout
    assert f"claude-code:{session_fingerprint('session-alpha')}" in refused.stderr
    assert "--force" in refused.stderr
    assert "session-alpha" not in refused.stderr
    # A refusal is a refusal: the run it declined to stop is still working.
    assert {pid for pid in tree if is_running(pid)}

    unattributed = _orchestrate(
        tmp_path, runs, onejudge_bin, "nameless", _session_env(tmp_path, None), launches
    )
    anonymous = _stop(unattributed, other)
    assert anonymous.returncode == 2, anonymous.stdout
    assert "no recorded launcher" in anonymous.stderr

    forced = _stop(theirs, other, "--force")
    assert forced.returncode == 0, forced.stderr
    # --force proceeds only after naming whose work it is ending.
    assert f"claude-code:{session_fingerprint('session-alpha')}" in forced.stdout
    assert _await_gone(tree) == set()


def test_stopping_a_run_leaves_no_worker_behind_and_the_round_reclaimable(
    tmp_path: Path, onejudge_bin: str, launches: list[Launch]
) -> None:
    """A stop ends the whole dispatch tree and gives the round back, unfinished."""
    runs = tmp_path / "runs"
    planner = _session_env(tmp_path, "session-alpha")
    mine = _orchestrate(tmp_path, runs, onejudge_bin, "reclaimed", planner, launches)
    owners = _await_parked_worker(mine, "reclaimed", tmp_path)
    tree = run_tree(owners)
    # The tree is a real dispatch: the launched orchestrator, the round owner it
    # started, and the worker process below them — not one idle process.
    assert len(tree) > len(owners)

    stopped = _stop(mine, planner)
    assert stopped.returncode == 0, stopped.stderr
    assert _await_gone(tree) == set()

    # The stopped round is reported exactly as an interrupted one, and names its own
    # reclaiming command rather than leaving the planner to reconstruct it.
    view = _runs_view(runs, planner)
    assert "ABANDONED" in view
    assert "--recover" in view

    # And the reclaim is real: released from its barrier, the same round finishes.
    (tmp_path / "reclaimed.release").write_text("go\n", encoding="utf-8")
    reclaimed = subprocess.run(
        [
            "just",
            "run-plan",
            str(mine.run_dir / "round-01" / "plan.json"),
            "--run",
            mine.run_id,
            "--runs-dir",
            str(runs),
            "--base",
            str(_base(tmp_path)),
            "--provider",
            "command",
            "--recover",
        ],
        cwd=REPO_ROOT,
        env=planner,
        text=True,
        capture_output=True,
        check=False,
        timeout=e2e_timeout(600),
    )
    assert reclaimed.returncode == 0, reclaimed.stdout + reclaimed.stderr
    result = json.loads((mine.run_dir / "round-01" / "result.json").read_text(encoding="utf-8"))
    assert result["results"]["worker"]["status"] == "done"


def test_stop_answers_for_a_run_it_cannot_address_or_has_nothing_left_to_stop(
    tmp_path: Path, onejudge_bin: str, launches: list[Launch]
) -> None:
    """The refusals a planner meets by mistyping, and the second stop that is a no-op.

    Every record read here was written by a real launch and a real stop; nothing is
    fabricated. Stopping an already-stopped run is the ordinary way a planner reaches
    the no-live-process path — after an interrupted round, or after a stop it is not
    sure landed — so it must be a plain success rather than an error.
    """
    runs = tmp_path / "runs"
    planner = _session_env(tmp_path, "session-alpha")
    mine = _orchestrate(tmp_path, runs, onejudge_bin, "addressed", planner, launches)
    _await_parked_worker(mine, "addressed", tmp_path)

    def stop(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["just", "stop", *args, "--runs-dir", str(runs)],
            cwd=REPO_ROOT,
            env=planner,
            text=True,
            capture_output=True,
            check=False,
            timeout=e2e_timeout(180),
        )

    # A run id that could name a path, and one that names nothing, are both refused
    # before anything is signalled — the live run above is still running afterwards.
    escaped = stop("../escape")
    assert escaped.returncode == 2, escaped.stdout
    assert "run id" in escaped.stderr
    absent = stop("no-such-run")
    assert absent.returncode == 2, absent.stdout
    assert "no recorded run" in absent.stderr
    # A grace period that is not a finite number would never expire into escalation.
    unbounded = stop(mine.run_id, "--grace", "nan")
    assert unbounded.returncode == 2, unbounded.stdout
    assert "finite" in unbounded.stderr
    assert recorded_owners(mine.run_dir), "the refusals must not have stopped the run"

    first = stop(mine.run_id)
    assert first.returncode == 0, first.stderr
    assert "signalled" in first.stdout

    # Stopping it again is a success that says there was nothing left, and still
    # points at the run's own review command.
    again = stop(mine.run_id)
    assert again.returncode == 0, again.stderr
    assert "nothing to stop" in again.stdout
    assert f"just results {mine.run_id}" in again.stdout


def _recorded_provenance(launch: Launch) -> dict[str, object]:
    """The out-of-repo record this launch joined to, read where the scheme puts it."""
    launch_id = read_launch_info(launch.run_dir)
    assert launch_id is not None
    record = (
        Path(launch.env["XDG_STATE_HOME"]) / "ai-orchestrator" / "launches" / f"{launch_id}.json"
    )
    return dict(json.loads(record.read_text(encoding="utf-8")))


def test_orchestrate_records_a_codex_session_and_honours_explicit_overrides(
    tmp_path: Path, onejudge_bin: str, launches: list[Launch]
) -> None:
    """Detection is not hardcoded to one harness, and a typed flag still wins."""
    runs = tmp_path / "runs"
    codex = _session_env(tmp_path, "thread-77", launcher="codex")
    detected = _orchestrate(tmp_path, runs, onejudge_bin, "codex-detected", codex, launches)
    record = _recorded_provenance(detected)
    assert record["launcher"] == "codex"
    assert record["launcher_session_id"] == "thread-77"

    # An explicit pair overrides the ambient session it was launched from, which is
    # what lets a wrapper attribute a run it starts on someone else's behalf.
    plan = _plan(tmp_path, "explicit")
    launched = subprocess.run(
        [
            "just",
            "orchestrate",
            str(plan),
            "--runs-dir",
            str(runs),
            "--base",
            str(_base(tmp_path)),
            "--onejudge-bin",
            onejudge_bin,
            "--launcher",
            "claude-code",
            "--launcher-session",
            "declared-session",
            "--skill-command",
            sys.executable,
            str(FAKE_BACKEND),
        ],
        cwd=REPO_ROOT,
        env=codex,
        text=True,
        capture_output=True,
        check=True,
    )
    override = Launch(str(json.loads(launched.stdout)["run_id"]), runs, codex, plan)
    launches.append(override)
    overridden = _recorded_provenance(override)
    assert overridden["launcher"] == "claude-code"
    assert overridden["launcher_session_id"] == "declared-session"

    # Each run reads as its own session's, and neither as the other's.
    codex_view = _runs_view(runs, codex)
    assert f"{detected.run_id}  [mine]" in codex_view
    assert (
        f"{override.run_id}  [claude-code:{session_fingerprint('declared-session')}]" in codex_view
    )
    assert _runs_view(runs, codex, "--mine").count(override.run_id) == 0

    # A launcher named without a session cannot join anything, and neither can a
    # session id the scheme cannot carry: both degrade to `unknown` rather than
    # attaching the run to a session that did not launch it.
    for name, extra, environment in (
        ("half-override", ["--launcher", "claude-code"], codex),
        ("unusable-session", [], _session_env(tmp_path, "two\nlines")),
    ):
        partial = _plan(tmp_path, name)
        started = subprocess.run(
            [
                "just",
                "orchestrate",
                str(partial),
                "--runs-dir",
                str(runs),
                "--base",
                str(_base(tmp_path)),
                "--onejudge-bin",
                onejudge_bin,
                *extra,
                "--skill-command",
                sys.executable,
                str(FAKE_BACKEND),
            ],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=True,
        )
        degraded = Launch(str(json.loads(started.stdout)["run_id"]), runs, environment, partial)
        launches.append(degraded)
        launch_id = read_launch_info(degraded.run_dir)
        assert launch_id is not None, "the run still records its own join key"
        assert not (
            Path(environment["XDG_STATE_HOME"])
            / "ai-orchestrator"
            / "launches"
            / f"{launch_id}.json"
        ).exists()
        assert f"{degraded.run_id}  [unknown]" in _runs_view(runs, environment)
