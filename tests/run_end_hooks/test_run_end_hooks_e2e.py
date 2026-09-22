"""The run-end hooks `just orchestrate` wires, fired by the installed engine as a run ends.

A launch through `just orchestrate` names `scripts/run-ended.sh` as both its success hook
and its failure hook. Whether that does anything is decided three layers down — by the
recipe adding the flags, by the engine this host installed judging the run's ending and
spawning the hook, and by the script launching the follow-up run through `just follow-ups`
— so this module launches real plans through the real recipe and reads what each layer
left: the hook's log under the run, `just results`, `just runs`, and the launch records.

Everything between the recipe and the model is real: `just orchestrate`,
`scripts/orchestrate.sh`, `scripts/onepipeline.sh` and its design-approval gate, the
installed `onepipeline` driver, `scripts/run-ended.sh`, `just follow-ups` and the detached
follow-up run it launches under `graphs/follow-up.yaml`, and the drafting command the
drafts are written with. **The paid model alone is doubled**: `tests/e2e/fake_backend.py`
at the `oneagentgraph` seam for the main run's two-party worker, and
`tests/e2e/fake_codex.py` at the provider binary for the follow-up agent's single-sided
member. Every store a launch writes to — the runs root, the drafts root, the authoring
root — is this module's own, so nothing lands in this checkout's.

A hook never changes how a run settled, so each hooked launch has a twin launched with
both hooks named blank — the recipe keeps a caller's own value, and the engine reads a
blank one as none — and the two are held to the same exit status, settlement line, and
node statuses.

The refusals and the no-drafts ending are driven by running the script itself with the
environment the engine hands it, because no launch can put the engine's variables into a
shape the engine never produces.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple, TypedDict

import follow_up_variables
import plan_root_variable
import pytest
from harness_indirections import established_indirections
from nx_workspace import SHARED_TOOLCHAIN_GROUP
from project_fixtures import helper, project_from_plan
from waits import timeout as e2e_timeout

from orchestrator.plan_store import WRITABLE_PLUGIN
from orchestrator.root import REPO_ROOT

#: A real launch holds this checkout's toolchain for as long as it runs, so this module is
#: scheduled with every other journey that does, which also keeps its fixtures on one worker.
pytestmark = pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP)

#: The paid model's stand-ins, and the guard covering the identities neither seam reaches.
FAKE_BACKEND = helper("fake_backend.py")
FAKE_CODEX = helper("fake_codex.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")

#: The script under test, spawned directly for the shapes no launch produces.
RUN_ENDED = REPO_ROOT / "scripts" / "run-ended.sh"

#: The launching session this module states, and everything an enclosing dispatch would
#: otherwise decide for these launches — ownership, the run a hook is told about, and the
#: stores a launch resolves.
LAUNCHING_SESSION = "e2e-run-end-hooks"
INHERITED = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "ONEPIPELINE_HOOK",
    "ONEPIPELINE_RUN_ID",
    "ONEPIPELINE_RUN_ROOT",
    "ONEPIPELINE_NODE_SCRATCH_DIR",
    "ONEVCS_SESSION",
    "ORCHESTRATOR_ASK_MANAGER",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
    "FAKE_CODEX_HOLD_SECONDS",
    *follow_up_variables.all_names(),
    plan_root_variable.name(),
)

#: Who the indirection helpers attribute their diagnostics to.
INDIRECTION_CALLER = "tests/run_end_hooks/test_run_end_hooks_e2e.py"

#: The one node every plan here holds.
NODE = "report"

#: A persona name no built-in role claims and no file under `graphs/` answers to.
UNCLAIMED_PERSONA = "no-role-claims-this-name"

#: What `just follow-ups` names the run that follows up another.
FOLLOW_UPS_SUFFIX = "-follow-ups"

#: The line the success hook prints once it has launched the follow-up run.
LAUNCHED = re.compile(
    r"^main work of run (?P<run>\S+) is complete; follow-ups are being verified in run "
    r"(?P<follow_up>\S+); watch it with: just watch (?P<watched>\S+)$",
    re.MULTILINE,
)

#: How long a follow-up run with one stand-in turn is given to settle.
FOLLOW_UP_SETTLE_SECONDS = 300


class Bench(NamedTuple):
    """The throwaway host every launch here runs on: its environment and its stores."""

    environment: dict[str, str]
    tmp: Path
    runs: Path
    drafts_root: Path


class Launch(NamedTuple):
    """One `just orchestrate` of a one-node plan, and the run it made."""

    result: subprocess.CompletedProcess[str]
    run: str


class Ended(NamedTuple):
    """A hooked launch and its hookless twin, read back before anything is torn down."""

    bench: Bench
    hooked: Launch
    twin: Launch
    log: str
    results: subprocess.CompletedProcess[str]


class Succeeded(NamedTuple):
    """The success journey: the ending, then the follow-up run the hook launched."""

    ended: Ended
    follow_up: str
    watched: subprocess.CompletedProcess[str]
    mine: subprocess.CompletedProcess[str]


# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] These are typed views
# of the public wire input this journey sends to the installed engine; its real reply
# validator is the drift gate, so a field removed or changed by the authority refuses
# the journey rather than letting a parallel local implementation accept it.
class RetryNode(TypedDict):
    """The replacement node sent through the public retry envelope."""

    id: str
    task: str
    expects_no_diff: bool


class RetryCommand(TypedDict):
    """One retry command in that envelope."""

    op: str
    id: str
    node: RetryNode


class RetryEnvelope(TypedDict):
    """The manager reply that reopens the failed run."""

    version: int
    completion: bool
    message: str
    reason: str
    commands: list[RetryCommand]


# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]


def _bench(tmp: Path, oneharness_bin: str) -> Bench:
    runs, drafts_root, plans = tmp / "runs", tmp / "follow-ups", tmp / "plans"
    environment = dict(os.environ)
    for name in INHERITED:
        environment.pop(name, None)
    root_name, plugin_name, _ = follow_up_variables.all_names()
    environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    environment["ONEPIPELINE_RUNS_DIR"] = str(runs)
    environment[root_name] = str(drafts_root)
    environment[plugin_name] = WRITABLE_PLUGIN
    environment[plan_root_variable.name()] = str(plans)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment.update(established_indirections(INDIRECTION_CALLER))
    environment["XDG_STATE_HOME"] = str(tmp / "state")
    return Bench(environment, tmp, runs, drafts_root)


def _just(
    bench: Bench,
    *arguments: str,
    seconds: float = 600,
    environment: dict[str, str] | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - this checkout's own recipes
        ["just", *arguments],  # noqa: S607
        cwd=REPO_ROOT,
        env=environment or bench.environment,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
    )


def _project(bench: Bench, name: str, **node: object) -> str:
    """A launchable one-node project whose run id is `name`."""
    plan = bench.tmp / f"{name}.plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "goal": {"text": "Report once, so the run ends"},
                "name": name,
                "tasks": [
                    {
                        "id": NODE,
                        "persona": "engineer",
                        "task": "Report without changing files.",
                        **node,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return project_from_plan(plan, name)


def _draft(bench: Bench, run: str) -> None:
    """One follow-up drafted against `run` through the real drafting command, as a manager."""
    drafted = subprocess.run(  # noqa: S603 - the drafting command every party drafts with
        [str(REPO_ROOT / "scripts" / "follow-up-draft.sh"), "--as", "manager", "--run", run]
        + ["--title", "The listing cursor skips the last page"]
        + ["--repository", "github.com/nickderobertis/some-service", "--path", "src/cursor.py"],
        cwd=bench.tmp,
        env=bench.environment,
        input=(
            "## What happened\nThe cursor skipped a page.\n\n## Where\n`src/cursor.py`.\n\n"
            "## Why it is out of scope\nThe run was about something else.\n\n"
            "## Evidence\nPage 9 of 9 never rendered.\n"
        ),
        text=True,
        capture_output=True,
        check=False,
    )
    assert drafted.returncode == 0, drafted.stdout + drafted.stderr


def _launch(bench: Bench, run: str, *flags: str, **node: object) -> Launch:
    project = _project(bench, run, **node)
    return Launch(_just(bench, "orchestrate", project, "--dag-graph", "off", *flags), run)


def _settlement(launch: Launch) -> dict[str, object]:
    """The `{"run_id", "settlement"}` line an attached launch ends its standard output with."""
    lines = launch.result.stdout.strip().splitlines()
    assert lines, f"the launch printed no settlement:\n{launch.result.stderr}"
    settled: object = json.loads(lines[-1])
    assert isinstance(settled, dict), settled
    return settled


def _statuses(bench: Bench, run: str) -> dict[str, tuple[object, object]]:
    """Each node's status and outcome in the run's `result.json`, which a hook must not change."""
    result: object = json.loads((bench.runs / run / "result.json").read_text("utf-8"))
    assert isinstance(result, dict), result
    nodes = result["nodes"]
    assert isinstance(nodes, list), result
    return {
        str(node["id"]): (node["status"], node.get("outcome"))
        for node in nodes
        if isinstance(node, dict)
    }


def _ended(bench: Bench, hooked: str, hook: str, **node: object) -> Ended:
    """Launch `hooked` through the recipe's defaults, then its twin with both hooks blank."""
    _draft(bench, hooked)
    launched = _launch(bench, hooked, **node)
    twin = _launch(bench, f"{hooked}-twin", "--success-hook=", "--failure-hook=", **node)
    log_path = bench.runs / hooked / "hooks" / f"{hook}.log"
    log = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
    results = _just(bench, "results", hooked, seconds=60)
    return Ended(bench, launched, twin, log, results)


def _launch_record(bench: Bench, run: str) -> dict[str, object]:
    record: object = json.loads((bench.runs / run / "launch.json").read_text("utf-8"))
    assert isinstance(record, dict), record
    return record


def _stop(bench: Bench, *runs: str) -> None:
    for run in runs:
        if (bench.runs / run).exists():
            _just(bench, "stop", run, seconds=60)


@pytest.fixture(scope="module")
def succeeded(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Succeeded:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    bench = _bench(tmp_path_factory.mktemp("run-end-success"), oneharness_bin)
    run = f"hooks-done-{os.getpid()}"
    follow_up = ""
    try:
        ended = _ended(bench, run, "success")
        matched = LAUNCHED.search(ended.log)
        follow_up = matched["follow_up"] if matched else ""
        watched = (
            _just(
                bench,
                "watch",
                follow_up,
                "--timeout",
                str(FOLLOW_UP_SETTLE_SECONDS),
                seconds=FOLLOW_UP_SETTLE_SECONDS + 60,
            )
            if follow_up
            else subprocess.CompletedProcess([], 1, "", "no follow-up run was named")
        )
        mine = _just(bench, "runs", "--mine", seconds=60)
        return Succeeded(ended, follow_up, watched, mine)
    finally:
        _stop(bench, run, f"{run}-twin", *([follow_up] if follow_up else []))


@pytest.fixture(scope="module")
def failed(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Ended:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    bench = _bench(tmp_path_factory.mktemp("run-end-failure"), oneharness_bin)
    # A persona no built-in role claims is read as a path under `graphs/`, which does not
    # exist, so the dispatch dies in config validation and the node settles `failed` without
    # a turn — `personas/README.md` describes that path. Nothing here is doubled to fail it.
    run = f"hooks-failed-{os.getpid()}"
    try:
        return _ended(bench, run, "failure", persona=UNCLAIMED_PERSONA)
    finally:
        _stop(bench, run, f"{run}-twin")


def test_a_run_whose_nodes_all_end_done_launches_its_follow_up_run_detached(
    succeeded: Succeeded,
) -> None:
    ended, run = succeeded.ended, succeeded.ended.hooked.run

    assert _settlement(ended.hooked) == {"run_id": run, "settlement": "complete"}, (
        ended.hooked.result.stderr
    )
    matched = LAUNCHED.search(ended.log)
    assert matched is not None, f"the success hook did not report a follow-up run:\n{ended.log}"
    assert matched["run"] == run
    assert matched["follow_up"] == matched["watched"] == f"{run}{FOLLOW_UPS_SUFFIX}"
    launched = _launch_record(ended.bench, succeeded.follow_up)
    assert launched["project"] == f"authoring:{run}{FOLLOW_UPS_SUFFIX}", launched
    # Detached, so the hook returned at the launch record rather than awaiting the run.
    assert launched.get("mode", "detached") != "attached", launched


def test_the_hooks_output_reaches_the_attached_launch_and_the_results_view(
    succeeded: Succeeded,
) -> None:
    ended = succeeded.ended
    announced = (
        f"follow-ups are being verified in run {succeeded.follow_up}; "
        f"watch it with: just watch {succeeded.follow_up}"
    )

    assert announced in ended.hooked.result.stderr, ended.hooked.result.stderr
    assert ended.results.returncode == 0, ended.results.stderr
    assert announced in ended.results.stdout, ended.results.stdout


def test_the_follow_up_run_is_owned_by_the_session_that_owns_the_main_run(
    succeeded: Succeeded,
) -> None:
    bench, run, follow_up = succeeded.ended.bench, succeeded.ended.hooked.run, succeeded.follow_up

    assert _launch_record(bench, run)["session"] == LAUNCHING_SESSION
    assert _launch_record(bench, follow_up)["session"] == LAUNCHING_SESSION
    assert succeeded.mine.returncode == 0, succeeded.mine.stderr
    for owned in (run, follow_up):
        assert re.search(
            rf"^\*?\s*{re.escape(owned)}\s+\[mine\]", succeeded.mine.stdout, re.MULTILINE
        ), succeeded.mine.stdout


#: The hook the engine runs before every dispatch rather than at a run's end. From
#: onepipeline 0.36.0 a launch naming it records it beside the run-end hooks and logs it
#: under the same `hooks/` directory, so what these journeys hold about run-end hooks is
#: read with it left out.
DISPATCH_ENV_HOOK = "dispatch_env_hook"
RUN_END_HOOK_LOGS = ("success.log", "failure.log")


def _run_end_hooks_named(launched: dict[str, object]) -> set[str]:
    """The run-end hook keys a launch record carries, the dispatch-environment hook aside."""
    return {key for key in launched if "hook" in key and not key.startswith(DISPATCH_ENV_HOOK)}


def _run_end_hook_logs(run_root: Path) -> list[Path]:
    """The run-end hook logs a run's root holds."""
    return [
        run_root / "hooks" / log for log in RUN_END_HOOK_LOGS if (run_root / "hooks" / log).exists()
    ]


def test_a_follow_up_run_names_no_hook_and_launches_no_follow_up_run_of_its_own(
    succeeded: Succeeded,
) -> None:
    bench, follow_up = succeeded.ended.bench, succeeded.follow_up

    assert succeeded.watched.returncode == 0, succeeded.watched.stdout + succeeded.watched.stderr
    launched = _launch_record(bench, follow_up)
    assert not _run_end_hooks_named(launched), launched
    assert not _run_end_hook_logs(bench.runs / follow_up)
    assert not list(bench.runs.glob(f"{follow_up}{FOLLOW_UPS_SUFFIX}*"))


def test_a_hook_leaves_a_completed_runs_settlement_and_exit_status_as_they_were(
    succeeded: Succeeded,
) -> None:
    ended = succeeded.ended

    _assert_the_twins_settled_alike(ended)
    assert ended.hooked.result.returncode == 0, ended.hooked.result.stderr
    assert not _run_end_hook_logs(ended.bench.runs / ended.twin.run)


def test_a_run_that_ends_with_a_failed_node_launches_nothing_and_says_how_to_verify_by_hand(
    failed: Ended,
) -> None:
    run = failed.hooked.run

    assert _settlement(failed.hooked)["settlement"] != "complete", failed.hooked.result.stdout
    assert (
        f"run-ended: run {run} ended without every node done (reason nodes: {NODE} failed); "
        "no follow-up run was launched; verify its drafts by hand with: "
        f"just follow-ups {run}"
    ) in failed.log, failed.log
    assert f"just follow-ups {run}" in failed.hooked.result.stderr, failed.hooked.result.stderr
    assert not list(failed.bench.runs.glob(f"{run}{FOLLOW_UPS_SUFFIX}*"))
    assert not (failed.bench.runs / run / "hooks" / "success.log").exists()


def test_a_hook_leaves_a_failed_runs_settlement_and_exit_status_as_they_were(
    failed: Ended,
) -> None:
    _assert_the_twins_settled_alike(failed)


def test_a_failed_run_retried_to_completion_launches_follow_up_verification(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """A later successful ending gets its own hook epoch on this host's real recipes."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    bench = _bench(tmp_path, oneharness_bin)
    run = f"hooks-recovered-{os.getpid()}"
    follow_up = ""
    try:
        _draft(bench, run)
        failed = _launch(bench, run, persona=UNCLAIMED_PERSONA)
        assert _settlement(failed)["settlement"] != "complete", failed.result.stdout
        assert (bench.runs / run / "hooks" / "failure.log").is_file()

        envelope: RetryEnvelope = {
            "version": 3,
            "completion": False,
            "message": "Retry the configuration failure with the normal worker role.",
            "reason": "The corrected node can now complete.",
            "commands": [
                {
                    "op": "retry",
                    "id": NODE,
                    "node": {
                        "id": f"{NODE}-2",
                        "task": "Report without changing files.",
                        "expects_no_diff": True,
                    },
                }
            ],
        }
        replied = subprocess.run(  # noqa: S603 - this checkout's real engine wrapper
            [str(REPO_ROOT / "scripts" / "onepipeline.sh"), "reply", run],
            cwd=REPO_ROOT,
            env=bench.environment,
            input=json.dumps(envelope),
            text=True,
            capture_output=True,
            timeout=e2e_timeout(60),
            check=False,
        )
        assert replied.returncode == 0, replied.stdout + replied.stderr
        adopted = _just(bench, "orchestrate", "--adopt", run)
        assert adopted.returncode == 0, adopted.stdout + adopted.stderr
        assert _settlement(Launch(adopted, run)) == {"run_id": run, "settlement": "complete"}

        success_log = (bench.runs / run / "hooks" / "success.log").read_text(encoding="utf-8")
        matched = LAUNCHED.search(success_log)
        assert matched is not None, success_log
        follow_up = matched["follow_up"]
        assert follow_up == f"{run}{FOLLOW_UPS_SUFFIX}"
        results = _just(bench, "results", run, seconds=60)
        assert "failure hook fired" in results.stdout, results.stdout
        assert "success hook fired" in results.stdout, results.stdout
        # Each record against the epoch it belongs to: the failure hook's reason names a
        # node the retry replaced and its output is instructions for a run that is not
        # there any more, so the adopted engine labels it superseded and names the edit
        # that reopened the run, while the success hook — the current epoch's — stands.
        # Before that landing both rendered alike, and the stale instructions read as
        # where the run was.
        failure_line = next(
            line for line in results.stdout.splitlines() if "failure hook fired" in line
        )
        success_line = next(
            line for line in results.stdout.splitlines() if "success hook fired" in line
        )
        assert "superseded:" in failure_line and "reopened the run after it" in failure_line, (
            failure_line
        )
        assert "retry" in failure_line, failure_line
        assert "superseded" not in success_line, success_line
    finally:
        _stop(bench, run, *([follow_up] if follow_up else []))


# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] A typed view of the
# public wire input this journey sends through `just channel-reply`; the installed
# engine's reply validator and reconciler are the drift gate, so a field the authority
# removed or renamed refuses the journey rather than a local copy accepting it.
class SettleCommand(TypedDict):
    """One settle command: a node's recorded state moved from evidence the run never saw."""

    op: str
    id: str
    outcome: str
    evidence: str


class SettleEnvelope(TypedDict):
    """A commands-only envelope, which `just channel-reply` sends to the run's replies."""

    version: int
    commands: list[SettleCommand]


# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]


def test_a_failed_run_whose_failed_node_is_settled_done_fires_its_success_hook_once(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """A settle that carries an ended run to a different ending is an epoch of its own.

    The rescue this host's manager performs when a node's work landed another way: the
    run ended failed and its failure hook fired, the manager settles the failed node
    `done` through `just channel-reply`, and the adopting driver applies it. Nothing is
    made live again — the settle moves a recorded state and dispatches nothing — so an
    engine that opened an epoch only on an edit that reopened work left the run complete
    with its follow-up run never launched. On the adopted engine the success hook fires
    for the complete ending the settle carried the run to, exactly once, and the failure
    hook's record is labelled superseded by the settle.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    bench = _bench(tmp_path, oneharness_bin)
    run = f"hooks-settled-{os.getpid()}"
    follow_up = ""
    try:
        _draft(bench, run)
        failed = _launch(bench, run, persona=UNCLAIMED_PERSONA)
        assert _settlement(failed)["settlement"] != "complete", failed.result.stdout
        assert (bench.runs / run / "hooks" / "failure.log").is_file()
        assert not (bench.runs / run / "hooks" / "success.log").exists()

        envelope: SettleEnvelope = {
            "version": 3,
            "commands": [
                {
                    "op": "settle",
                    "id": NODE,
                    "outcome": "done",
                    "evidence": "The report this node owed was delivered by hand.",
                }
            ],
        }
        replied = _just(bench, "channel-reply", run, seconds=120, input_text=json.dumps(envelope))
        assert replied.returncode == 0, replied.stdout + replied.stderr
        adopted = _just(bench, "orchestrate", "--adopt", run)
        assert adopted.returncode == 0, adopted.stdout + adopted.stderr
        assert _settlement(Launch(adopted, run)) == {"run_id": run, "settlement": "complete"}
        assert _statuses(bench, run)[NODE][0] == "done", _statuses(bench, run)

        success_log = (bench.runs / run / "hooks" / "success.log").read_text(encoding="utf-8")
        matched = LAUNCHED.search(success_log)
        assert matched is not None, success_log
        follow_up = matched["follow_up"]
        assert follow_up == f"{run}{FOLLOW_UPS_SUFFIX}"
        assert _launch_record(bench, follow_up)["project"] == f"authoring:{follow_up}"

        # Once: a second adoption of the ended run finds the success hook already fired
        # for this ending and fires nothing, so `results` still carries one record of it.
        again = _just(bench, "orchestrate", "--adopt", run)
        assert again.returncode == 0, again.stdout + again.stderr
        assert _settlement(Launch(again, run)) == {"run_id": run, "settlement": "complete"}
        results = _just(bench, "results", run, seconds=60)
        assert results.returncode == 0, results.stderr
        fired = [line for line in results.stdout.splitlines() if "success hook fired" in line]
        assert len(fired) == 1, (again.stdout + again.stderr, results.stdout)
        assert "superseded" not in fired[0], fired[0]
        failure_line = next(
            line for line in results.stdout.splitlines() if "failure hook fired" in line
        )
        assert "superseded:" in failure_line and "settle" in failure_line, failure_line
        assert sorted(path.name for path in bench.runs.glob(f"{run}{FOLLOW_UPS_SUFFIX}*")) == [
            follow_up
        ]
    finally:
        _stop(bench, run, *([follow_up] if follow_up else []))


def _assert_the_twins_settled_alike(ended: Ended) -> None:
    hooked, twin = ended.hooked, ended.twin

    assert hooked.result.returncode == twin.result.returncode, (
        hooked.result.stderr + twin.result.stderr
    )
    assert _settlement(hooked)["settlement"] == _settlement(twin)["settlement"]
    assert _statuses(ended.bench, hooked.run) == _statuses(ended.bench, twin.run)


class Refused(NamedTuple):
    """An environment the engine never hands a hook, and what the script says to it."""

    environment: dict[str, str]
    status: int
    says: str


REFUSALS = (
    Refused(
        {
            "ONEPIPELINE_HOOK": "success",
            "ONEPIPELINE_RUN_ID": "demo",
            "ONEPIPELINE_RUN_ROOT": "/unexpected/../demo",
        },
        2,
        "ONEPIPELINE_RUN_ROOT '/unexpected/../demo' is not the canonical absolute directory "
        "of run demo",
    ),
    Refused({"ONEPIPELINE_HOOK": "success"}, 2, "ONEPIPELINE_RUN_ID is not set"),
    Refused(
        {"ONEPIPELINE_HOOK": "failure", "ONEPIPELINE_RUN_ID": "not a run id"},
        2,
        "ONEPIPELINE_RUN_ID 'not a run id' is not a run id",
    ),
    Refused(
        {
            "ONEPIPELINE_HOOK": "success",
            "ONEPIPELINE_RUN_ID": "demo",
            "ONEPIPELINE_RUN_ROOT": "runs/demo",
        },
        2,
        "ONEPIPELINE_RUN_ROOT 'runs/demo' is not the canonical absolute directory of run demo",
    ),
    Refused(
        {
            "ONEPIPELINE_HOOK": "success",
            "ONEPIPELINE_RUN_ID": "demo",
            "ONEPIPELINE_RUN_ROOT": "/runs/another-run",
        },
        2,
        "ONEPIPELINE_RUN_ROOT '/runs/another-run' is not the canonical absolute directory "
        "of run demo",
    ),
    Refused(
        {"ONEPIPELINE_HOOK": "success", "ONEPIPELINE_RUN_ID": "demo"},
        2,
        "ONEPIPELINE_RUN_ROOT is not set",
    ),
    Refused(
        {"ONEPIPELINE_HOOK": "settled", "ONEPIPELINE_RUN_ID": "demo"},
        2,
        "ONEPIPELINE_HOOK is 'settled', not 'success' or 'failure'",
    ),
)


def _hook(
    tmp: Path, environment: dict[str, str], stdin: str = ""
) -> subprocess.CompletedProcess[str]:
    """Spawn the script the way the engine does: no arguments, one document on stdin."""
    bench = _bench(tmp, "")
    return subprocess.run(  # noqa: S603 - the script under test
        [str(RUN_ENDED)],
        cwd=REPO_ROOT,
        env=bench.environment | environment,
        input=stdin,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )


@pytest.mark.parametrize("refused", REFUSALS, ids=lambda row: row.says)
def test_the_hook_refuses_an_environment_that_names_no_run_or_no_mode(
    tmp_path: Path, refused: Refused
) -> None:
    ran = _hook(tmp_path, refused.environment)

    assert ran.returncode == refused.status, ran.stdout + ran.stderr
    assert refused.says in ran.stderr, ran.stderr


def test_the_success_hook_relays_the_recipes_answer_when_there_is_nothing_to_verify(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    environment = {
        "ONEPIPELINE_HOOK": "success",
        "ONEPIPELINE_RUN_ID": "nothing-drafted",
        "ONEPIPELINE_RUN_ROOT": str(runs / "nothing-drafted"),
    }

    ran = _hook(tmp_path, environment)

    assert ran.returncode == 0, ran.stdout + ran.stderr
    # One line, the recipe's own, which already says nothing was launched.
    lines = ran.stdout.splitlines()
    assert len(lines) == 1, ran.stdout
    assert lines[0].startswith("follow-ups: run nothing-drafted holds no follow-up drafts"), (
        ran.stdout
    )
    assert lines[0].endswith("no follow-up run was launched"), ran.stdout
    assert not runs.exists() or not list(runs.iterdir())


def test_the_success_hook_exits_with_the_recipes_status_when_the_recipe_refuses(
    tmp_path: Path,
) -> None:
    # A drafts root that is a file rather than a directory, which the recipe refuses
    # before it counts anything.
    not_a_directory = tmp_path / "drafts-root-is-a-file"
    not_a_directory.write_text("", encoding="utf-8")
    root_name, _, _ = follow_up_variables.all_names()
    environment = {
        "ONEPIPELINE_HOOK": "success",
        "ONEPIPELINE_RUN_ID": "refused",
        "ONEPIPELINE_RUN_ROOT": str(tmp_path / "runs" / "refused"),
        root_name: str(not_a_directory),
    }

    ran = _hook(tmp_path, environment)

    assert ran.returncode == 2, ran.stdout + ran.stderr
    assert "could not be resolved to a writable root" in ran.stderr, ran.stderr
    assert (
        "but 'just follow-ups refused --detach' exited 2, so no follow-up run was launched"
    ) in ran.stdout, ran.stdout


class Reason(NamedTuple):
    """A failure document the engine may hand the hook, and the reason the line carries."""

    stdin: str
    says: str


REASONS = (
    Reason(
        json.dumps(
            {
                "version": 1,
                "hook": "failure",
                "run_id": "demo",
                "reason": {"kind": "nodes", "nodes": [{"id": "build\nforged", "status": "failed"}]},
            }
        ),
        "(the reason could not be read from the hook input: its nodes are not a list of nodes "
        "with a printable id and status)",
    ),
    Reason(
        json.dumps(
            {
                "version": 1,
                "hook": "failure",
                "run_id": "demo",
                "run_root": "/runs/demo",
                "reason": {
                    "kind": "unfinished",
                    "nodes": [{"id": "build", "status": "parked", "outcome": None}],
                },
            }
        ),
        "(reason unfinished: build parked)",
    ),
    Reason(
        json.dumps(
            {
                "version": 1,
                "hook": "failure",
                "run_id": "demo",
                "run_root": "/runs/demo",
                "reason": {"kind": "stopped", "nodes": []},
            }
        ),
        "(reason stopped: the run was stopped)",
    ),
    Reason("not a document", "(the reason could not be read from the hook input: "),
    Reason(
        json.dumps({"version": 2, "hook": "failure", "run_id": "demo", "reason": None}),
        "(the reason could not be read from the hook input: it is not a version 1 hook document)",
    ),
    Reason(
        json.dumps({"version": 1, "hook": "failure", "run_id": "another-run", "reason": None}),
        "(the reason could not be read from the hook input: "
        "it is not the failure hook of run demo)",
    ),
    Reason(
        json.dumps({"version": 1, "hook": "failure", "run_id": "demo", "reason": {"kind": "odd"}}),
        "(the reason could not be read from the hook input: its reason kind is not one of "
        "nodes, unfinished, stopped)",
    ),
    Reason(
        json.dumps(
            {
                "version": 1,
                "hook": "failure",
                "run_id": "demo",
                "reason": {"kind": "nodes", "nodes": [{"id": 3}]},
            }
        ),
        "(the reason could not be read from the hook input: its nodes are not a list of nodes "
        "with a printable id and status)",
    ),
)


@pytest.mark.parametrize("reason", REASONS, ids=lambda row: row.says)
def test_the_failure_hook_names_every_reason_kind_and_always_exits_zero(
    tmp_path: Path, reason: Reason
) -> None:
    environment = {"ONEPIPELINE_HOOK": "failure", "ONEPIPELINE_RUN_ID": "demo"}

    ran = _hook(tmp_path, environment, reason.stdin)

    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert ran.stdout.startswith("run-ended: run demo ended without every node done "), ran.stdout
    assert reason.says in ran.stdout, ran.stdout
    assert ran.stdout.rstrip().endswith(
        "no follow-up run was launched; verify its drafts by hand with: just follow-ups demo"
    ), ran.stdout


#: What the failure hook's line ends with whatever reason it could read.
BY_HAND = "no follow-up run was launched; verify its drafts by hand with: just follow-ups demo"


def _copied_hook(tmp_path: Path) -> Path:
    """The hook and the helper it sources, in a checkout that carries no `.venv`."""
    scripts = tmp_path / "bare-checkout" / "scripts"
    scripts.mkdir(parents=True)
    for name in ("run-ended.sh", "plan-brief.sh"):
        copied = scripts / name
        shutil.copy2(REPO_ROOT / "scripts" / name, copied)
    return scripts / "run-ended.sh"


def _failure_document() -> str:
    return json.dumps(
        {
            "version": 1,
            "hook": "failure",
            "run_id": "demo",
            "run_root": "/runs/demo",
            "reason": {"kind": "nodes", "nodes": [{"id": "build", "status": "failed"}]},
        }
    )


def test_the_failure_hook_reads_its_reason_with_the_hosts_python_when_the_checkout_has_none(
    tmp_path: Path,
) -> None:
    hook = _copied_hook(tmp_path)

    ran = subprocess.run(  # noqa: S603 - the script under test, from a checkout with no .venv
        [str(hook)],
        cwd=tmp_path,
        env=_bench(tmp_path, "").environment
        | {"ONEPIPELINE_HOOK": "failure", "ONEPIPELINE_RUN_ID": "demo"},
        input=_failure_document(),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )

    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "(reason nodes: build failed)" in ran.stdout, ran.stdout
    assert ran.stdout.rstrip().endswith(BY_HAND), ran.stdout


def test_the_failure_hook_still_says_how_to_verify_by_hand_when_no_python_can_run(
    tmp_path: Path,
) -> None:
    hook = _copied_hook(tmp_path)
    # A search path holding only what the hook itself runs outside Python, so neither the
    # checkout's interpreter (there is none) nor a host one can be found.
    bare = tmp_path / "bare-bin"
    bare.mkdir()
    (bare / "dirname").symlink_to(shutil.which("dirname") or "/usr/bin/dirname")

    ran = subprocess.run(  # noqa: S603 - the script under test, with no interpreter on PATH
        [shutil.which("bash") or "/bin/bash", str(hook)],
        cwd=tmp_path,
        env={"PATH": str(bare), "ONEPIPELINE_HOOK": "failure", "ONEPIPELINE_RUN_ID": "demo"},
        input=_failure_document(),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )

    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert (
        "(the reason could not be read, because no Python interpreter could run)" in ran.stdout
    ), ran.stdout
    assert ran.stdout.rstrip().endswith(BY_HAND), ran.stdout


def test_the_hook_refuses_to_run_from_a_checkout_missing_the_run_id_grammar(tmp_path: Path) -> None:
    hook = _copied_hook(tmp_path)
    (hook.parent / "plan-brief.sh").unlink()

    ran = subprocess.run(  # noqa: S603 - the script under test, from a checkout missing its helper
        [str(hook)],
        cwd=tmp_path,
        env=_bench(tmp_path, "").environment
        | {"ONEPIPELINE_HOOK": "failure", "ONEPIPELINE_RUN_ID": "demo"},
        input=_failure_document(),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )

    assert ran.returncode == 2, ran.stdout + ran.stderr
    assert "required helper is not a readable regular file" in ran.stderr, ran.stderr
    assert "plan-brief.sh" in ran.stderr, ran.stderr
