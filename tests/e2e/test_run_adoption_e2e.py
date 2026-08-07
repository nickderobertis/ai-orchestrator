"""Real journey: an orphaned run is adopted and driven to completion on its own ledger.

Two runs were orphaned in one night when the orchestrator's post-round model request
died on provider quota, and an unattended run was unrecoverable by design — `just
orchestrate` refuses a run directory that exists, so the only way forward was a new
run id, which strands the journal, the ledger, and every publication anchor.

Everything here is the production path. `just orchestrate` launches, the run's driver
and its whole dispatch tree are then SIGKILLed mid-round exactly as a dead provider
turn leaves them, and `just orchestrate --adopt` attaches a fresh driver. The
assertions are what a planner gets back: the same run id, the same journal continuing
where it left off, the interrupted round re-claimed rather than replaced, and the dead
driver's own evidence preserved rather than buried.

Both refusals are driven against the same live run, because that is the only way they
can be real: a second planner session asking for a run it did not launch, and this
planner asking for one that is still being driven.
"""

# llmlint: ignore-file[e2e_not_mocked] Only the paid model is a double — the deterministic
# protocol backend this suite already dispatches through (tests/e2e/fake_backend.py). The
# recipes, onejudge, the channel, the ledger, the run journal, and process liveness are real.

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path

import pytest
import yaml
from rendezvous import Rendezvous
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator import BASE_CONFIG, REPO_ROOT
from orchestrator.adopt import RELAUNCH_SCHEMA_VERSION, relaunch_path
from orchestrator.harnesses import JUDGE_HARNESS_ENV, JUDGE_MODEL_ENV
from orchestrator.launch import LAUNCHER_ENVIRONMENT_VARIABLES, read_launch_link
from orchestrator.stop import recorded_owners, run_tree
from orchestrator.watchdog import ProcessId

FAKE_BACKEND = REPO_ROOT / "tests" / "e2e" / "fake_backend.py"

#: The routing this journey launches with, so the adoption has something to replay
#: that no later command line supplies. The model half is the one a replay that kept
#: only the identity would drop — silently back onto the tier `oneharness.judge.toml`
#: pins for it, which is the substitution the per-side seam exists to prevent.
JUDGE_IDENTITY = "claude-code:primary"
JUDGE_MODEL = "claude-opus-5"

#: Where the backend the run's own dispatches spawn appends the routing it was
#: spawned with. That process is the far end of the inheritance a per-side choice
#: travels — driver, round, dispatch, onejudge, provider — so it is where "this run's
#: work actually got the model it was launched on" is a fact rather than a hop.
ROUTING_RECORD = "FAKE_BACKEND_ROUTING"

#: Everything a harness exports to say which session it is, cleared before each launch
#: so the session under test is the one this journey named rather than the developer's.
_LAUNCHER_VARIABLES = (
    *sorted(LAUNCHER_ENVIRONMENT_VARIABLES),
    "CODEX_HOME",
    "ORCHESTRATOR_LAUNCHER",
    "ORCHESTRATOR_LAUNCHER_SESSION",
    "ONEHARNESS_HISTORY_LABELS",
)


def _session_env(tmp_path: Path, session_id: str, routing: Path | None = None) -> dict[str, str]:
    environment = {**os.environ}
    for name in _LAUNCHER_VARIABLES:
        environment.pop(name, None)
    environment["XDG_STATE_HOME"] = str(tmp_path / "state")
    environment["CLAUDECODE"] = "1"
    environment["CLAUDE_CODE_SESSION_ID"] = session_id
    if routing is not None:
        environment[ROUTING_RECORD] = str(routing)
    return environment


def _base(tmp_path: Path) -> Path:
    value = yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8"))
    value["provider"] = {"kind": "command", "command": [sys.executable, str(FAKE_BACKEND)]}
    path = tmp_path / "base.yaml"
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    return path


def _plan(tmp_path: Path, held: Rendezvous) -> Path:
    path = tmp_path / "adoption-plan.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "name": "adoption",
                "tasks": [
                    {
                        "id": "worker",
                        "persona": "engineer",
                        "task": f"complete-now adoption no-assessment{held.sentinels(0)}",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def reaped() -> Iterator[list[Path]]:
    """Kill every launched orchestrator group a journey leaves behind."""
    directories: list[Path] = []
    yield directories
    for run_dir in directories:
        for owner in recorded_owners(run_dir):
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(owner.pid, signal.SIGKILL)


def _orchestrate(
    runs: Path, env: dict[str, str], *arguments: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["just", "orchestrate", *arguments, "--detach", "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
    )


def _launch(
    runs: Path, env: dict[str, str], onejudge_bin: str, *arguments: str
) -> subprocess.CompletedProcess[str]:
    """Start a run, naming the harness binary this suite's venv provides."""
    return _orchestrate(runs, env, *arguments, "--onejudge-bin", onejudge_bin)


def _adopt(runs: Path, env: dict[str, str], run_id: str) -> subprocess.CompletedProcess[str]:
    """Adopt a run, passing nothing the adoption would have to read and drop.

    Deliberately no `--onejudge-bin`: adoption replays the launch parameters the run
    recorded — this suite's venv binary among them — and refuses a launch-only option
    beside `--adopt` rather than silently ignoring it.
    """
    return _orchestrate(runs, env, "--adopt", run_id)


# llmlint: ignore-block[tests_mirror_real_usage] No command produces this state, and
# that is the point of it: `just stop` deliberately lets each owner record its own
# abandonment, while adoption exists for the state *nothing* got to record. So the
# death is synthesized — from the run's own `status.json` records, read through the
# same functions `just stop` reads them with, which is the closest an operator can
# get. Everything either side of it is the real command surface.
def _kill_the_whole_run_tree(run_dir: Path) -> None:
    """End the driver and everything below it the way a provider death leaves them.

    SIGKILL, not `just stop`: a stop lets each owner record its own abandonment, and
    the state adoption exists for is the one nothing got to record — a `running`
    round behind a pid that is simply gone.
    """
    tracked: set[ProcessId] = run_tree(recorded_owners(run_dir))
    assert tracked, "the run recorded no owner to kill"
    for pid in sorted(tracked, reverse=True):
        with suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)
    wait = deadline(120)
    while time.monotonic() < wait:
        if not any(_alive(pid) for pid in tracked):
            return
        time.sleep(0.05)
    raise AssertionError(f"processes outlived the kill: {sorted(tracked)}")


# llmlint: ignore-end[tests_mirror_real_usage]


def _routing(record: Path, *, after: int = 0) -> list[dict[str, str]]:
    """The routing every provider this run spawned since line `after` was given.

    A per-side choice is carried by environment and inherited the whole way down —
    driver, round, dispatch, onejudge, the provider it spawns — so the provider is
    where a replayed value either arrived or did not. Read there rather than at the
    driver, because that is the end of the journey the run's work actually makes: a
    value the record replayed and the spawn dropped would still read correctly at
    every point above it.
    """
    lines = record.read_text(encoding="utf-8").splitlines()[after:]
    return [json.loads(line) for line in lines]


def _await_routing(record: Path, *, after: int = 0) -> list[dict[str, str]]:
    """Block until this run has spawned a provider past line `after`, then read it."""
    wait = deadline(120)
    while True:
        spawned = _routing(record, after=after)
        if spawned:
            return spawned
        assert time.monotonic() < wait, f"no dispatch of this run reached {record}"
        time.sleep(0.05)


def _alive(pid: ProcessId) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return Path(f"/proc/{pid}/stat").is_file()


def _await_round_claimed(run_dir: Path, held: Rendezvous) -> None:
    held.wait(240)
    wait = deadline(240)
    while time.monotonic() < wait:
        if any(owner.source.startswith("round-") for owner in recorded_owners(run_dir)):
            return
        time.sleep(0.05)
    raise AssertionError("the run never claimed a round")


def _await_launched_run(runs: Path) -> Path:
    wait = deadline(120)
    while time.monotonic() < wait:
        found = [item for item in runs.iterdir()] if runs.is_dir() else []
        if found:
            return found[0]
        time.sleep(0.05)
    raise AssertionError(f"no run directory appeared under {runs}")


def _channel(recipe: str, run_id: str, runs: Path, payload: str | None = None) -> str:
    result = subprocess.run(
        ["just", recipe, run_id, "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        input=payload,
        timeout=e2e_timeout(180),
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _answer_and_await_the_report(run_id: str, runs: Path) -> None:
    """Answer the adopted driver's supervisor question, and let the run finish."""
    wait = deadline(300)
    surface = None
    while surface is None:
        assert time.monotonic() < wait, f"{run_id} never asked the planner anything"
        surface = json.loads(_channel("channel-next", run_id, runs) or "{}").get("surface")
    _channel(
        "channel-reply",
        run_id,
        runs,
        json.dumps({"completion": True, "reason": "verified by the adoption journey"}),
    )
    report = runs / run_id / "orchestrator" / "report.json"
    while not (report.is_file() and report.stat().st_size):
        assert time.monotonic() < wait, f"the adopted orchestrator never finished {run_id}"
        time.sleep(0.05)


def test_an_orphaned_run_is_adopted_and_completed_on_its_original_ledger(
    tmp_path: Path, onejudge_bin: str, reaped: list[Path]
) -> None:
    runs = tmp_path / "runs"
    routing = tmp_path / "routing.jsonl"
    routing.touch()
    planner = _session_env(tmp_path, "session-adoption-owner", routing)
    stranger = _session_env(tmp_path, "session-another-planner", routing)
    held = Rendezvous.at(tmp_path, "adoption")

    launched = _launch(
        runs,
        planner,
        onejudge_bin,
        str(_plan(tmp_path, held)),
        "--base",
        str(_base(tmp_path)),
        "--judge-harness",
        JUDGE_IDENTITY,
        "--judge-model",
        JUDGE_MODEL,
        "--skill-command",
        sys.executable,
        str(FAKE_BACKEND),
    )
    assert launched.returncode == 0, launched.stderr
    run_dir = _await_launched_run(runs)
    reaped.append(run_dir)
    run_id = run_dir.name
    _await_round_claimed(run_dir, held)
    # The launch routed as it was told to, at the boundary that decides a turn.
    assert {entry.get(JUDGE_MODEL_ENV) for entry in _await_routing(routing)} == {JUDGE_MODEL}

    # A run another planner launched is refused by name, with no `--force` to get
    # past it: unlike a stop, adopting takes over ongoing work rather than ending it.
    theirs = _adopt(runs, stranger, run_id)
    assert theirs.returncode == 2, theirs.stdout
    assert "another planner" in theirs.stderr, theirs.stderr

    # And this planner's own run is refused while something is still driving it.
    live = _adopt(runs, planner, run_id)
    assert live.returncode == 2, live.stdout
    assert "orchestrator process is still running" in live.stderr, live.stderr

    journal = run_dir / "events.jsonl"
    before = journal.read_text(encoding="utf-8").splitlines()
    link = read_launch_link(run_dir)
    assert link is not None
    _kill_the_whole_run_tree(run_dir)
    held.let_go()
    # Everything recorded past here was spawned by whatever drives the run next, so
    # the dead driver's own turns cannot be mistaken for the replacement's.
    before_adoption = len(_routing(routing))

    # A run launched before adoption existed has no parameters to replay, and is
    # refused rather than started on guessed ones. Moved aside rather than deleted,
    # so the adoption below is the same run in the same state.
    # llmlint: ignore[tests_mirror_real_usage] There is no command that produces this
    # state, and that is what it is: a run this build did not launch. Every run on
    # this host predating adoption has no relaunch record, and the refusal they get
    # is what this asserts — through the real `just orchestrate --adopt`, on a real
    # run, with only the record the older build would not have written withheld.
    record = relaunch_path(run_dir)
    record.replace(record.with_suffix(".withheld"))
    unreplayable = _adopt(runs, planner, run_id)
    assert unreplayable.returncode == 2, unreplayable.stdout
    assert "no relaunch record" in unreplayable.stderr, unreplayable.stderr
    record.with_suffix(".withheld").replace(record)

    adopted = _adopt(runs, planner, run_id)
    assert adopted.returncode == 0, adopted.stderr
    assert json.loads(adopted.stdout)["run_id"] == run_id
    _answer_and_await_the_report(run_id, runs)

    # The routing came back with the driver, all the way to the provider its rounds
    # spawn. `_adopt` passes no side options at all — they are launch-only — so both
    # halves here are the record's, and the model is the half a replay that kept only
    # the identity would have dropped: every round this fresh driver dispatched would
    # have been supervised at the tier the judge config pins instead of the one the
    # run was launched on.
    replayed = _routing(routing, after=before_adoption)
    assert replayed, "the adopted driver dispatched nothing to route"
    assert {entry.get(JUDGE_HARNESS_ENV) for entry in replayed} == {JUDGE_IDENTITY}
    assert {entry.get(JUDGE_MODEL_ENV) for entry in replayed} == {JUDGE_MODEL}

    # One run id, and no second run minted beside it.
    assert sorted(item.name for item in runs.iterdir()) == [run_id]
    # The interrupted round was reclaimed rather than replaced, and it settled.
    result = json.loads((run_dir / "round-01" / "result.json").read_text(encoding="utf-8"))
    assert result["state"] == "complete", result
    # The journal continued: every record written before the kill is still there, in
    # place, with the round the adopted driver ran appended after it. Only a torn
    # trailing line — the one the kill could have caught mid-write — may be repaired
    # away, which is the journal's own documented recovery.
    after = journal.read_text(encoding="utf-8").splitlines()
    assert after[: len(before) - 1] == before[: len(before) - 1]
    assert len(after) > len(before)
    # The run still names the session that launched it: adoption changes the driver,
    # never the provenance.
    assert read_launch_link(run_dir) == link
    # The adopted driver took a conversation of its own rather than resuming the dead
    # one, and its predecessor's report was preserved instead of truncated.
    effective = (run_dir / "orchestrator" / "effective.onejudge.yaml").read_text(encoding="utf-8")
    assert f"session: orchestrator-{run_id}-adopt1" in effective
    assert (run_dir / "orchestrator" / "report.pre-adopt-1.json").is_file()
    # And the record the next adoption would replay still carries both halves, so the
    # choice survives a second driver swap rather than only the first. Read as the
    # file it is — the persisted contract a second adoption re-reads — rather than
    # through the reader, which would prove the reader agrees with itself.
    persisted = json.loads(relaunch_path(run_dir).read_text(encoding="utf-8"))
    assert persisted["judge_harness"] == JUDGE_IDENTITY
    assert persisted["judge_model"] == JUDGE_MODEL
    assert persisted["adoptions"] == 1


def _downgrade_to_schema_v1(run_dir: Path) -> dict[str, object]:
    """Rewrite this run's record as the previous build would have written it.

    A build without the per-side model half wrote schema 1 and no `worker_model` /
    `judge_model` key at all, so that is what the file becomes: the version number
    and the absence together, rather than a v2 record with the number changed. What
    it deliberately does NOT drop is `judge_harness`, which v1 already carried — the
    point of the journey is which halves come back and which are silent.
    """
    path = relaunch_path(run_dir)
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["schema_version"] == RELAUNCH_SCHEMA_VERSION, record
    downgraded = {
        key: value
        for key, value in record.items()
        if key not in {"schema_version", "worker_model", "judge_model"}
    }
    downgraded["schema_version"] = 1
    path.write_text(json.dumps(downgraded), encoding="utf-8")
    return downgraded


def test_a_run_recorded_by_the_previous_build_is_adopted_from_its_v1_record(
    tmp_path: Path, onejudge_bin: str, reaped: list[Path]
) -> None:
    """A run orphaned across an upgrade is rescued, not stranded by its own record.

    Adoption exists for a run whose driver died; a run launched by the build *before*
    the model half existed is exactly such a run, and refusing its record would leave
    the upgrade itself as the thing that stranded it. So a v1 record is replayed: the
    identity it recorded comes back, and its silence about the model is read as the
    same answer a v2 record naming none gives — that side runs the model its config
    pins. Both are observed where a dispatch's routing lands rather than in the file
    that was read, and the record is left normalized to the current version so the
    NEXT adoption of this run replays a record this build wrote.
    """
    runs = tmp_path / "runs"
    routing = tmp_path / "routing.jsonl"
    routing.touch()
    planner = _session_env(tmp_path, "session-v1-record-owner", routing)
    held = Rendezvous.at(tmp_path, "v1-adoption")

    launched = _launch(
        runs,
        planner,
        onejudge_bin,
        str(_plan(tmp_path, held)),
        "--base",
        str(_base(tmp_path)),
        "--judge-harness",
        JUDGE_IDENTITY,
        "--judge-model",
        JUDGE_MODEL,
        "--skill-command",
        sys.executable,
        str(FAKE_BACKEND),
    )
    assert launched.returncode == 0, launched.stderr
    run_dir = _await_launched_run(runs)
    reaped.append(run_dir)
    run_id = run_dir.name
    _await_round_claimed(run_dir, held)
    _await_routing(routing)

    _kill_the_whole_run_tree(run_dir)
    held.let_go()
    before_adoption = len(_routing(routing))
    # llmlint: ignore[tests_mirror_real_usage] No command this build has writes a v1
    # record — the build that did is the one this journey is about — so the record is
    # rewritten into the shape that build left behind. Every other party is real: the
    # run, its ledger, `just orchestrate --adopt`, and the dispatches it then drives.
    downgraded = _downgrade_to_schema_v1(run_dir)
    assert downgraded["judge_harness"] == JUDGE_IDENTITY

    adopted = _adopt(runs, planner, run_id)
    assert adopted.returncode == 0, adopted.stderr
    assert json.loads(adopted.stdout)["run_id"] == run_id
    _answer_and_await_the_report(run_id, runs)

    # The run finished on its own ledger, from a record an older build wrote.
    result = json.loads((run_dir / "round-01" / "result.json").read_text(encoding="utf-8"))
    assert result["state"] == "complete", result
    assert sorted(item.name for item in runs.iterdir()) == [run_id]

    # The identity v1 recorded came back; the model it never recorded did not, so the
    # judge side runs whatever `oneharness.judge.toml` pins for that identity.
    replayed = _routing(routing, after=before_adoption)
    assert replayed, "the adopted driver dispatched nothing to route"
    assert {entry.get(JUDGE_HARNESS_ENV) for entry in replayed} == {JUDGE_IDENTITY}
    assert {entry.get(JUDGE_MODEL_ENV) for entry in replayed} == {None}

    # And the record is normalized on the way back out, so a second adoption of this
    # run replays the current schema rather than the one it was rescued from.
    persisted = json.loads(relaunch_path(run_dir).read_text(encoding="utf-8"))
    assert persisted["schema_version"] == RELAUNCH_SCHEMA_VERSION
    assert persisted["judge_harness"] == JUDGE_IDENTITY
    assert "judge_model" not in persisted
