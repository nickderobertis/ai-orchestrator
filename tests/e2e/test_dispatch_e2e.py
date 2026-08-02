"""E2E: drive the real onejudge CLI through the SDK, faking only the model.

These run the actual `onejudge` binary as a subprocess against a real persona,
with onejudge's `command` provider pointed at tests/e2e/fake_backend.py. Nothing
in our layer (merge, SDK dispatch, report validation) is mocked.
"""

# llmlint: ignore-file[contracts_have_one_source_or_a_drift_gate] The contract assertion
# preserves SDK/CLI equality while accepting only the explicitly bounded 0.3.3->0.3.4
# bootstrap pair; upgrading the shared supervisor binary during this lifecycle would
# terminate the run.
# llmlint: ignore-file[e2e_not_mocked] These tests execute the real onejudge and oneharness
# CLIs; only paid Claude/Codex model subprocesses are deterministic protocol doubles, the
# same explicit external-boundary exception documented in AGENTS.md for this e2e suite.

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

import onejudge_sdk
import pytest
import yaml
from mock_oneharness import BARRIER_DEATH_NOTICE
from nx_workspace import requires_workspace_install
from rendezvous import Rendezvous

from orchestrator import PERSONA_DIR, REPO_ROOT
from orchestrator.channel import (
    CHANNEL_DIR_ENV,
    CHANNEL_RUN_ID_ENV,
    create_channel,
    read_message,
    write_message,
)
from orchestrator.config import build_effective_config, load_yaml
from orchestrator.dispatch import DispatchError, dispatch, main, run_onejudge
from orchestrator.watchdog import ProcessId, process_activity

FAKE_BACKEND = REPO_ROOT / "tests" / "e2e" / "fake_backend.py"
MOCK_ONEHARNESS = REPO_ROOT / "tests" / "e2e" / "mock_oneharness.py"


def test_subdir_persona_scaffolding_and_recursive_validation_cli(tmp_path) -> None:
    persona_dir = tmp_path / "personas"
    scaffold = subprocess.run(
        [
            "just",
            "new-persona",
            "repo/specialist",
            "--persona-dir",
            str(persona_dir),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert scaffold.returncode == 0, scaffold.stderr
    assert (persona_dir / "repo" / "specialist.yaml").is_file()
    external = tmp_path / "external.yaml"
    external.write_text("invalid: [unclosed\n", encoding="utf-8")
    (persona_dir / "escaped.yaml").symlink_to(external)
    (persona_dir / "_draft.yaml").write_text("invalid: [unclosed\n", encoding="utf-8")
    (persona_dir / "_private").mkdir()
    (persona_dir / "_private" / "hidden.yaml").write_text("invalid: [unclosed\n", encoding="utf-8")

    validate = subprocess.run(
        [
            "uv",
            "run",
            "orchestrator-validate-personas",
            "--persona-dir",
            str(persona_dir),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert validate.returncode == 0, validate.stderr
    assert "1 persona(s) OK" in validate.stdout


def test_recursive_validation_cli_reports_qualified_persona_name(tmp_path) -> None:
    persona_dir = tmp_path / "personas" / "repo"
    persona_dir.mkdir(parents=True)
    (persona_dir / "broken.yaml").write_text("agent: {}\nuser: {}\n", encoding="utf-8")

    result = subprocess.run(
        [
            "uv",
            "run",
            "orchestrator-validate-personas",
            "--persona-dir",
            str(persona_dir.parent),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "repo/broken: agent.instructions" in result.stderr


@pytest.mark.parametrize("name", ["../escape", "repo/../escape", "repo//name"])
def test_new_persona_cli_rejects_unsafe_names(tmp_path, name) -> None:
    persona_dir = tmp_path / "personas"
    result = subprocess.run(
        ["just", "new-persona", name, "--persona-dir", str(persona_dir)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "invalid persona name" in result.stderr
    assert not persona_dir.exists()


def test_real_onejudge_sdk_and_cli_match_adopted_contract(
    onejudge_bin: str, adopted_onejudge_version: str, installed_onejudge_version: str
) -> None:
    version = subprocess.run(
        [onejudge_bin, "--version"], text=True, capture_output=True, check=True
    )
    schema = subprocess.run([onejudge_bin, "schema"], text=True, capture_output=True, check=True)
    run_help = subprocess.run(
        [onejudge_bin, "run", "--help"], text=True, capture_output=True, check=True
    )

    assert onejudge_sdk.__version__ == installed_onejudge_version
    assert version.stdout.strip() == f"onejudge {installed_onejudge_version}"
    assert (installed_onejudge_version, adopted_onejudge_version) in {
        ("0.3.3", "0.3.4"),
        (adopted_onejudge_version, adopted_onejudge_version),
    }
    assert "system_prompt:" in schema.stdout
    assert "assessment:" in schema.stdout
    assert "--task <TASK>" in run_help.stdout
    assert "--format <FORMAT>" in run_help.stdout


def test_just_dispatch_preserves_metacharacter_laden_arguments(command_base, onejudge_bin) -> None:
    task = 'complete-now: preserve spaces, (parentheses), and "quotes".'
    done_when = 'matches when (a) and (b), including "quoted text"'

    subject = subprocess.run(
        [
            "just",
            "dispatch",
            "engineer",
            task,
            "--done-when",
            done_when,
            "--base",
            str(command_base()),
            "--onejudge-bin",
            onejudge_bin,
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert subject.returncode == 0, subject.stderr
    assert json.loads(subject.stdout)["schema_version"] == 5


def test_dispatch_completes_via_supervisor_loop(command_base, onejudge_bin) -> None:
    report = dispatch(
        "engineer",
        "Add a health-check endpoint.",
        base_path=command_base(),
        persona_dir=PERSONA_DIR,
        onejudge_bin=onejudge_bin,
    )
    assert report.completed
    assert report.exit_code == 0
    assert report.assistant_turns >= 2  # exercised the two-sided loop
    assert report.usage.get("output_tokens", 0) > 0
    assert report.verdicts and report.verdicts[0]["verdict"]["value"] is True
    assert report.assessment == "- Add a regression test for the adjacent edge case."


def test_real_dispatch_delivers_exact_task_to_agent_history(
    tmp_path: Path, onejudge_bin: str, oneharness_bin: str
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    task = "complete-now: preserve this exact dispatched task."
    base = yaml.safe_load((REPO_ROOT / "config" / "onejudge.base.yaml").read_text())
    base["provider"] = {
        "kind": "split",
        "skill": {"kind": "oneharness", "bin": "oneharness"},
        "judge": {"kind": "command", "command": [sys.executable, str(FAKE_BACKEND)]},
    }
    base["user"]["max_turns"] = 1
    base_path = tmp_path / "split.base.yaml"
    base_path.write_text(yaml.safe_dump(base, sort_keys=False), encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "oneharness").symlink_to(MOCK_ONEHARNESS)
    state_home = tmp_path / "state"
    mock_stdout = "\n".join(
        (
            json.dumps({"type": "turn.started"}),
            json.dumps({"type": "thread.started", "thread_id": "prompt-delivery-thread"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "completed"},
                }
            ),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 4, "cached_input_tokens": 0, "output_tokens": 1},
                }
            ),
        )
    )

    dispatch_env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "REAL_ONEHARNESS_BIN": oneharness_bin,
        "MOCK_STDOUT": mock_stdout,
        "ONEHARNESS_HARNESSES": "codex",
        "ONEHARNESS_HISTORY": "true",
        "XDG_STATE_HOME": str(state_home),
    }
    dispatched = (
        ("engineer", task, "worker"),
        ("orchestrator", "complete-now: coordinate this run.", "orchestrator"),
        ("pr-author", "complete-now: draft this pull request.", "pr-author"),
    )
    for persona, persona_task, _agent_role in dispatched:
        report = dispatch(
            persona,
            persona_task,
            base_path=base_path,
            project_dir=str(target),
            onejudge_bin=onejudge_bin,
            env=dispatch_env,
        )
        assert report.completed
    records = [
        json.loads(line)
        for history_file in (state_home / "oneharness" / "history").glob("*/*.jsonl")
        for line in history_file.read_text(encoding="utf-8").splitlines()
    ]
    agent_runs = [
        record
        for record in records
        if record.get("type") == "run" and record.get("labels", {}).get("role") == "agent"
    ]
    assert {record["labels"]["agent_role"] for record in agent_runs} == {
        "worker",
        "orchestrator",
        "pr-author",
    }
    for persona, persona_task, agent_role in dispatched:
        matching = [
            record
            for record in agent_runs
            if record["labels"]["agent_role"] == agent_role and record["prompt"] == persona_task
        ]
        assert len(matching) == 1
        assert matching[0]["labels"]["persona"] == persona


def test_real_dispatch_detects_killed_agent_and_reaps_orphans(
    tmp_path: Path, onejudge_bin: str, oneharness_bin: str
) -> None:
    """Kill the real provider worker while onejudge is awaiting it."""
    target = tmp_path / "target"
    target.mkdir()
    judge = {"kind": "command", "command": [sys.executable, str(FAKE_BACKEND)]}
    base = yaml.safe_load((REPO_ROOT / "config" / "onejudge.base.yaml").read_text())
    base["provider"] = {
        "kind": "split",
        "skill": {"kind": "oneharness", "bin": "oneharness"},
        "judge": judge,
    }
    base_path = tmp_path / "split.base.yaml"
    base_path.write_text(yaml.safe_dump(base, sort_keys=False), encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # llmlint: ignore[e2e_not_mocked] The repository requires faking the paid agent
    # harness; this journey keeps the real wrapper, onejudge, dispatcher, PID kill,
    # process-tree cleanup, and run-plan boundary.
    (bin_dir / "oneharness").symlink_to(MOCK_ONEHARNESS)
    barrier = tmp_path / "agent-descendant.pid"
    existing_status_files = set(Path("/tmp").glob("orchestrator-watchdog-*/agent/agent.child.pid"))
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "worker",
                        "persona": "engineer",
                        "task": "complete-now: agent will be killed",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "REAL_ONEHARNESS_BIN": oneharness_bin,
        "MOCK_AGENT_BARRIER": str(barrier),
        "ORCHESTRATOR_WORKER_HEARTBEAT_TIMEOUT": "2",
    }

    started = time.monotonic()
    process = subprocess.Popen(
        [
            str(Path(onejudge_bin).with_name("orchestrator-run-plan")),
            str(plan),
            "--no-record",
            "--base",
            str(base_path),
            "--project-dir",
            str(target),
            "--onejudge-bin",
            onejudge_bin,
            "--format",
            "json",
        ],
        cwd=target,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = started + 10
    agent_pid = None
    while time.monotonic() < deadline and agent_pid is None:
        descendants = process_activity(ProcessId(process.pid)).pids
        candidates = (
            set(Path("/tmp").glob("orchestrator-watchdog-*/agent/agent.child.pid"))
            - existing_status_files
        )
        if barrier.exists():
            for agent_pid_path in candidates:
                candidate = ProcessId(int(agent_pid_path.read_text(encoding="utf-8")))
                if candidate in descendants:
                    agent_pid = candidate
                    break
        time.sleep(0.02)
    assert agent_pid is not None, "agent worker did not reach the provider barrier"
    orphan_pid = int(barrier.read_text(encoding="utf-8"))
    os.kill(agent_pid, signal.SIGTERM)
    stdout, stderr = process.communicate(timeout=5)

    assert process.returncode == 1, stderr
    result = json.loads(stdout)
    # A death before the first turn leaves no report and no transcript, so this
    # recorded line is the whole account of it. It has to carry the child's fate
    # *and* the child's own words — the stderr the killed harness wrote is what
    # tells a reader why it died, and it has to survive the wrapper, the status
    # directory, the dispatcher, and the graph to reach this JSON. And a killed
    # harness has to read differently from a worker that stopped on its own, or the
    # planner cannot tell retry from escalate.
    error = result["results"]["worker"]["error"]
    assert error.startswith("worker-died"), error
    assert "agent exit status 143" in error, error
    assert "agent harness killed by signal 15" in error, error
    assert BARRIER_DEATH_NOTICE in error, error
    assert time.monotonic() - started < 5
    deadline = time.monotonic() + 2
    while Path(f"/proc/{orphan_pid}").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not Path(f"/proc/{orphan_pid}").exists()
    group_deadline = time.monotonic() + 5
    while time.monotonic() < group_deadline:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)


def test_real_run_plan_round_budget_surfaces_blocking_proposal(
    tmp_path: Path, command_base, onejudge_bin: str
) -> None:
    runs = tmp_path / "runs"
    run_dir = runs / "round-budget"
    channel = create_channel(run_dir)
    # Never released: the round budget, not the agent, is what ends this run.
    wedged = Rendezvous(tmp_path / "wedged.ready", tmp_path / "never-release")
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "wedged",
                        "persona": "engineer",
                        "task": wedged.sentinels().strip(),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        CHANNEL_DIR_ENV: str(channel),
        CHANNEL_RUN_ID_ENV: "round-budget",
    }
    process = subprocess.Popen(
        [
            str(Path(onejudge_bin).with_name("orchestrator-run-plan")),
            str(plan),
            "--run",
            "round-budget",
            "--runs-dir",
            str(runs),
            "--base",
            str(command_base()),
            "--onejudge-bin",
            onejudge_bin,
            "--round-budget",
            "0.2",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    proposal = read_message(channel / "up.fifo", timeout=5)
    assert proposal["surface"] == {
        "kind": "proposal",
        "message": (
            "round-budget: round exceeded its 0.2s liveness budget; "
            "in-flight workers were cancelled and planner intervention is required"
        ),
        "blocking": True,
    }
    wedged.let_go()
    write_message(
        channel / "down.fifo",
        {"completion": False, "message": "stop", "reason": "budget exhausted"},
        timeout=5,
    )
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 1, (stdout, stderr)


def test_dispatch_subdir_qualified_persona_via_real_onejudge(command_base, onejudge_bin) -> None:
    report = dispatch(
        "crozier/crozier-corpus",
        "complete-now: verify subdirectory persona dispatch.",
        base_path=command_base(),
        persona_dir=PERSONA_DIR,
        onejudge_bin=onejudge_bin,
    )

    assert report.completed
    assert report.persona == "crozier/crozier-corpus"


def test_dispatch_forwards_validated_environment_to_real_provider(
    tmp_path, command_base, onejudge_bin
) -> None:
    cache = (tmp_path / "identity-cache").resolve()
    cache.mkdir()

    report = dispatch(
        "engineer",
        "complete-now capture-cache-env",
        base_path=command_base(),
        persona_dir=PERSONA_DIR,
        project_dir=str(tmp_path),
        onejudge_bin=onejudge_bin,
        env={"ORCHESTRATOR_CACHE_DIR": str(cache)},
    )

    assert report.completed
    assert (tmp_path / "CACHE_ENV.txt").read_text(encoding="utf-8") == str(cache)


@pytest.mark.parametrize(
    ("use_llmlint_wrapper", "expected"),
    [
        (True, str(REPO_ROOT / "scripts/llmlint-oneharness.sh")),
        (False, "<absent>"),
    ],
)
def test_bypass_dispatch_scopes_llmlint_wrapper_to_harness_repository(
    tmp_path, command_base, onejudge_bin, use_llmlint_wrapper, expected
) -> None:
    project = tmp_path / ("harness" if use_llmlint_wrapper else "foreign")
    project.mkdir()

    report = dispatch(
        "engineer",
        "complete-now capture-llmlint-env",
        base_path=command_base(),
        persona_dir=PERSONA_DIR,
        project_dir=str(project),
        onejudge_bin=onejudge_bin,
        oneharness_mode="bypass",
        use_llmlint_wrapper=use_llmlint_wrapper,
    )

    assert report.completed
    assert (project / "LLMLINT_ENV.txt").read_text(encoding="utf-8") == expected


@pytest.mark.parametrize(
    "env, message",
    [
        ({"BAD=NAME": "value"}, "name is invalid"),
        ({"": "value"}, "name is invalid"),
        ({"BAD\x00NAME": "value"}, "name is invalid"),
        ({3: "value"}, "name is invalid"),
        ({"BAD_VALUE": "nul\x00value"}, "non-NUL string"),
        ({"BAD_VALUE": 3}, "non-NUL string"),
    ],
)
def test_dispatch_rejects_invalid_process_environment(
    env, message, command_base, onejudge_bin
) -> None:
    with pytest.raises(DispatchError, match=message):
        dispatch(
            "engineer",
            "complete-now",
            base_path=command_base(),
            persona_dir=PERSONA_DIR,
            onejudge_bin=onejudge_bin,
            env=env,
        )


def test_run_onejudge_rejects_invalid_environment_at_its_public_boundary(onejudge_bin) -> None:
    with pytest.raises(DispatchError, match="name is invalid"):
        run_onejudge({}, "task", onejudge_bin=onejudge_bin, env={"": "value"})


def test_dispatch_complete_now_single_turn(command_base, onejudge_bin) -> None:
    report = dispatch(
        "engineer",
        "complete-now: trivial change.",
        base_path=command_base(),
        persona_dir=PERSONA_DIR,
        onejudge_bin=onejudge_bin,
    )
    assert report.completed
    assert report.assistant_turns == 1


def test_dispatch_hits_turn_cap_when_never_done(command_base, onejudge_bin) -> None:
    report = dispatch(
        "engineer",
        "should-fail: this subtask never satisfies the supervisor.",
        base_path=command_base(max_turns=3),
        persona_dir=PERSONA_DIR,
        onejudge_bin=onejudge_bin,
    )
    assert not report.completed
    assert report.exit_code == 1
    assert report.verdicts[0]["verdict"]["value"] is False


def test_run_onejudge_returns_incomplete_report_for_exit_one(command_base, onejudge_bin) -> None:
    config = build_effective_config(
        load_yaml(command_base(max_turns=1)),
        load_yaml(PERSONA_DIR / "engineer.yaml"),
    )
    report = run_onejudge(
        config,
        "should-fail: this subtask never satisfies the supervisor.",
        onejudge_bin=onejudge_bin,
    )

    assert report.exit_code == 1
    assert report.completed is False
    assert report.assistant_turns == 1
    assert report.verdicts[0]["verdict"]["value"] is False


def test_dispatch_unknown_persona_raises(command_base, onejudge_bin) -> None:
    with pytest.raises(DispatchError, match="unknown persona"):
        dispatch("no-such-persona", "x", base_path=command_base(), onejudge_bin=onejudge_bin)


@pytest.mark.parametrize("persona", ["../engineer", "/engineer", "repo/../engineer"])
def test_dispatch_rejects_unsafe_persona_names(persona, command_base, onejudge_bin) -> None:
    with pytest.raises(DispatchError, match="invalid persona name"):
        dispatch(persona, "x", base_path=command_base(), onejudge_bin=onejudge_bin)


def test_dispatch_cli_json_output(command_base, onejudge_bin, capsys) -> None:
    rc = main(
        [
            "reviewer",
            "Review the diff.",
            "--base",
            str(command_base()),
            "--onejudge-bin",
            onejudge_bin,
            "--format",
            "json",
        ]
    )
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["schema_version"] == 5


def test_dispatch_cli_human_reads_task_from_stdin(command_base, onejudge_bin, capsys) -> None:
    import io

    original_stdin = sys.stdin
    sys.stdin = io.StringIO("Document the API.")  # drive the real `--task -` stdin path
    try:
        rc = main(
            ["docs-writer", "-", "--base", str(command_base()), "--onejudge-bin", onejudge_bin]
        )
    finally:
        sys.stdin = original_stdin
    assert rc == 0
    assert "completed" in capsys.readouterr().out


def test_dispatch_provider_override(command_base, onejudge_bin) -> None:
    report = dispatch(
        "reviewer",
        "complete-now: quick review.",
        base_path=command_base(),
        onejudge_bin=onejudge_bin,
        provider="command",  # harmless here (base already command) — exercises the flag
    )
    assert report.completed


class SplitHarnessFixture(NamedTuple):
    target: Path
    base_path: Path
    env: dict[str, str]
    invocation_log: Path


class BindingRejection(NamedTuple):
    process: subprocess.CompletedProcess[str]
    session: str


def test_dispatch_preserves_real_onejudge_failure_status_and_stderr(
    tmp_path: Path, onejudge_bin: str, oneharness_bin: str
) -> None:
    """The binding backend's exact failure must survive watchdog termination."""
    rejection = _run_real_binding_rejection(tmp_path, onejudge_bin, oneharness_bin)

    assert rejection.process.returncode == 2, (
        f"operator stderr={rejection.process.stderr!r}\noperator report={rejection.process.stdout}"
    )
    assert f"`{rejection.session}-skill` was created on harness `codex`" in rejection.process.stderr
    assert "cannot be continued on `claude-code`" in rejection.process.stderr
    assert "exit 255" not in rejection.process.stderr
    assert "<no stderr>" not in rejection.process.stderr


def _split_oneharness_fixture(tmp_path: Path, oneharness_bin: str) -> SplitHarnessFixture:
    target = tmp_path / "target"
    target.mkdir()
    judge = {"kind": "command", "command": [sys.executable, str(FAKE_BACKEND)]}
    base = yaml.safe_load((REPO_ROOT / "config" / "onejudge.base.yaml").read_text())
    base["provider"] = {
        "kind": "split",
        "skill": {"kind": "oneharness", "bin": "oneharness"},
        "judge": judge,
    }
    base["user"]["max_turns"] = 2
    base_path = tmp_path / "split.base.yaml"
    base_path.write_text(yaml.safe_dump(base, sort_keys=False), encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "oneharness").symlink_to(MOCK_ONEHARNESS)
    invocation_log = tmp_path / "invocations.jsonl"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "REAL_ONEHARNESS_BIN": oneharness_bin,
        "MOCK_HARNESSES": "codex",
        "ONEHARNESS_HARNESSES": "codex",
        "MOCK_INVOCATION_LOG": str(invocation_log),
        "XDG_STATE_HOME": str(tmp_path / "state"),
    }
    return SplitHarnessFixture(target, base_path, env, invocation_log)


def _dispatch_command(
    onejudge_bin: str, target: Path, base_path: Path, task: str, *extra: str
) -> list[str]:
    source_override = os.environ.get("DISPATCH_E2E_SOURCE_ROOT")
    executable = [str(Path(onejudge_bin).with_name("orchestrator-dispatch"))]
    if source_override:
        executable = [
            os.environ.get("DISPATCH_E2E_PYTHON", sys.executable),
            "-c",
            (
                "import sys;"
                f"sys.path.insert(0, {source_override!r});"
                "from orchestrator.dispatch import main;"
                "raise SystemExit(main(sys.argv[1:]))"
            ),
        ]
    return [
        *executable,
        "engineer",
        task,
        "--base",
        str(base_path),
        "--cwd",
        str(target),
        "--project-dir",
        str(target),
        "--onejudge-bin",
        onejudge_bin,
        *extra,
    ]


def _recorded_sessions(path: Path) -> list[str]:
    sessions: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        argv = json.loads(line)
        sessions.append(argv[argv.index("--session") + 1])
    return sessions


def test_concurrent_sessionless_dispatches_reach_distinct_real_harness_sessions(
    tmp_path: Path, onejudge_bin: str, oneharness_bin: str
) -> None:
    fixture = _split_oneharness_fixture(tmp_path, oneharness_bin)
    commands = [
        _dispatch_command(
            onejudge_bin,
            fixture.target,
            fixture.base_path,
            f"complete-now: concurrent dispatch {index}",
        )
        for index in range(2)
    ]

    processes = [
        subprocess.Popen(
            command,
            cwd=fixture.target,
            env=fixture.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for command in commands
    ]
    results = [process.communicate(timeout=30) for process in processes]

    assert [process.returncode for process in processes] == [0, 0], results
    sessions = _recorded_sessions(fixture.invocation_log)
    assert len(sessions) == 4
    assert len(set(sessions)) == 2
    assert all(sessions.count(session) == 2 for session in set(sessions))


def test_explicit_session_is_threaded_across_real_harness_turns(
    tmp_path: Path, onejudge_bin: str, oneharness_bin: str
) -> None:
    fixture = _split_oneharness_fixture(tmp_path, oneharness_bin)
    process = subprocess.run(
        _dispatch_command(
            onejudge_bin,
            fixture.target,
            fixture.base_path,
            "finish on the second turn",
            "--session",
            "operator-resume",
        ),
        cwd=fixture.target,
        env=fixture.env,
        text=True,
        capture_output=True,
    )

    assert process.returncode == 0, process.stderr
    sessions = _recorded_sessions(fixture.invocation_log)
    assert len(sessions) == 2
    assert sessions == ["operator-resume-skill", "operator-resume-skill"]


def _run_real_binding_rejection(
    tmp_path: Path, onejudge_bin: str, oneharness_bin: str
) -> BindingRejection:
    fixture = _split_oneharness_fixture(tmp_path, oneharness_bin)
    target, base_path, env = fixture.target, fixture.base_path, fixture.env
    bin_dir = tmp_path / "bin"
    (bin_dir / "oneharness").unlink()
    (bin_dir / "oneharness").symlink_to(oneharness_bin)
    fake_codex = bin_dir / "codex"
    fake_codex.write_text(
        """#!/usr/bin/env python3
import json
print(json.dumps({"type": "thread.started", "thread_id": "bound-codex"}))
print(json.dumps({"type": "item.completed", "item": {
    "type": "agent_message", "text": "bound"
}}))
print(json.dumps({"type": "turn.completed", "usage": {
    "input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1
}}))
""",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    fake_claude = bin_dir / "claude"
    fake_claude.write_text(
        """#!/usr/bin/env python3
import json
print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                  "result": "wrong harness", "session_id": "claude-session"}))
""",
        encoding="utf-8",
    )
    fake_claude.chmod(0o755)
    alternate = tmp_path / "alternate"
    alternate.mkdir()
    env["ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR"] = str(alternate)
    # Pinned rather than inherited: the default derives from the real $HOME, where an
    # authenticated second subscription would change which candidate this runs.
    env["ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR"] = str(alternate)
    env["ORCHESTRATOR_CODEX_ALT_HOME"] = str(tmp_path / "codex-alternate")
    bound_session = "binding-rejection"
    bound_env = {**env, "ONEHARNESS_HARNESSES": "codex"}
    bound = subprocess.run(
        [
            oneharness_bin,
            "run",
            "--config",
            str(REPO_ROOT / "oneharness.toml"),
            "--compact",
            "--prompt",
            "bind this session",
            "--cwd",
            ".",
            "--session",
            f"{bound_session}-skill",
        ],
        text=True,
        capture_output=True,
        cwd=target,
        env=bound_env,
    )
    assert bound.returncode == 0, bound.stderr
    rejecting_env = {
        **env,
        "ONEHARNESS_HARNESSES": "claude-code",
    }

    process = subprocess.run(
        _dispatch_command(
            onejudge_bin,
            target,
            base_path,
            "complete-now: binding must reject",
            "--session",
            bound_session,
            "--format",
            "json",
        ),
        cwd=target,
        env=rejecting_env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    return BindingRejection(process, bound_session)


def test_real_session_harness_binding_rejection_names_session_and_both_harnesses(
    tmp_path: Path, onejudge_bin: str, oneharness_bin: str
) -> None:
    rejection = _run_real_binding_rejection(tmp_path, onejudge_bin, oneharness_bin)

    assert rejection.process.returncode == 2
    detail = rejection.process.stderr
    assert "session/harness binding rejection" in detail
    assert f"`{rejection.session}-skill`" in detail
    assert "`codex`" in detail
    assert "`claude-code`" in detail


def test_dispatch_cli_writes_output_file(command_base, onejudge_bin, tmp_path) -> None:
    out = tmp_path / "report.json"
    rc = main(
        [
            "reviewer",
            "complete-now: quick review.",
            "--base",
            str(command_base()),
            "--onejudge-bin",
            onejudge_bin,
            "--format",
            "json",
            "-o",
            str(out),
        ]
    )
    assert rc == 0
    assert '"schema_version"' in out.read_text(encoding="utf-8")


def test_dispatch_cli_applies_ordered_models_to_real_oneharness(
    tmp_path: Path, onejudge_bin: str, oneharness_bin: str
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "oneharness.toml").write_text(
        'run_mode = "fallback"\n'
        'harnesses = ["codex", "claude-code"]\n'
        '[harness.codex]\nmodel = "gpt-5.5"\n'
        '[harness.claude-code]\nmodel = "claude-sonnet-4-5"\n',
        encoding="utf-8",
    )
    judge = {"kind": "command", "command": [sys.executable, str(FAKE_BACKEND)]}
    base = yaml.safe_load((REPO_ROOT / "config" / "onejudge.base.yaml").read_text())
    base["provider"] = {
        "kind": "split",
        "skill": {"kind": "oneharness", "bin": "oneharness"},
        "judge": judge,
    }
    base["user"]["max_turns"] = 1
    base_path = tmp_path / "split.base.yaml"
    base_path.write_text(yaml.safe_dump(base, sort_keys=False), encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "oneharness").symlink_to(MOCK_ONEHARNESS)
    argv_path = tmp_path / "mock-argv.txt"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "REAL_ONEHARNESS_BIN": oneharness_bin,
        "MOCK_HARNESSES": "claude-code",
        "MOCK_ARGV_FILE": str(argv_path),
        "ONEHARNESS_HARNESSES": "claude-code",
        "ONEHARNESS_MODELS": "claude-opus-4-8",
        "XDG_STATE_HOME": str(tmp_path / "state"),
    }
    # Pin the selected harness and model so every provider invocation crosses the
    # matching shipped mock while still proving dispatch forwards ordered models.
    # The default session ("dispatch-<persona>") is a fixed global name: oneharness
    # would resume whatever harness a previous run of it bound, so a stored
    # codex-bound session silently overrides the claude-code model asserted here.
    # Deriving the name from this test's target keeps the run on a session of its
    # own without reaching into the global session store.
    session = f"dispatch-models-{tmp_path.parent.name}-{target.name}"
    proc = subprocess.run(
        [
            str(Path(onejudge_bin).with_name("orchestrator-dispatch")),
            "engineer",
            "complete-now: prove model fallback",
            "--base",
            str(base_path),
            "--cwd",
            str(target),
            "--project-dir",
            str(target),
            "--session",
            session,
            "--onejudge-bin",
            onejudge_bin,
            "--format",
            "json",
        ],
        cwd=target,
        env=env,
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 0, proc.stderr
    assert argv_path.exists(), (proc.stdout, proc.stderr)
    argv = argv_path.read_text(encoding="utf-8").splitlines()
    assert argv[argv.index("--model") + 1] == "claude-opus-4-8"
    report = json.loads(proc.stdout)
    assert report["schema_version"] == 5
    assert "telemetry" not in report
    config_env = env.copy()
    config_env.pop("ONEHARNESS_HARNESSES")
    config_env.pop("ONEHARNESS_MODELS")
    effective = subprocess.run(
        [
            oneharness_bin,
            "config",
            "--config",
            str(REPO_ROOT / "oneharness.toml"),
            "--compact",
        ],
        text=True,
        capture_output=True,
        check=True,
        env=config_env,
    )
    effective_config = json.loads(effective.stdout)
    assert effective_config["run_mode"]["value"] == "fallback"
    assert effective_config["harnesses"]["value"][0] == "claude-code:alternate"
    assert (
        effective_config["harness"]["claude-code"]["variant"]["alternate"]["model"]["value"]
        == "claude-opus-5"
    )


# Each role's own wrapper is what forces its config; the judge's comes from
# onejudge's `judge_config`, so it is passed here directly.
ROLE_WRAPPERS = {
    "oneharness.toml": REPO_ROOT / "scripts" / "oneharness-agent.sh",
    "oneharness.orchestrator.toml": REPO_ROOT / "scripts" / "oneharness-orchestrator.sh",
    "oneharness.llmlint.toml": REPO_ROOT / "scripts" / "llmlint-oneharness.sh",
}


@pytest.mark.parametrize(
    ("config_name", "harness_id", "expected_config"),
    [
        # Every role now names all three Claude identities, so each config has to
        # route each of them: the two alternate subscriptions into their own config
        # directories, and the primary one into Claude's default with every
        # inherited selector masked off.
        ("oneharness.toml", "claude-code:alternate", "alternate"),
        ("oneharness.toml", "claude-code:alternate2", "alternate2"),
        ("oneharness.toml", "claude-code:primary", "default"),
        ("oneharness.judge.toml", "claude-code:alternate", "alternate"),
        ("oneharness.judge.toml", "claude-code:alternate2", "alternate2"),
        ("oneharness.judge.toml", "claude-code:primary", "default"),
        ("oneharness.llmlint.toml", "claude-code:alternate", "alternate"),
        ("oneharness.llmlint.toml", "claude-code:alternate2", "alternate2"),
        ("oneharness.orchestrator.toml", "claude-code:alternate", "alternate"),
        ("oneharness.orchestrator.toml", "claude-code:alternate2", "alternate2"),
    ],
)
def test_claude_variants_isolate_subscription_environment_at_real_oneharness_boundary(
    tmp_path: Path,
    oneharness_bin: str,
    config_name: str,
    harness_id: str,
    expected_config: str,
) -> None:
    fake_claude = tmp_path / "claude"
    fake_claude.write_text(
        """#!/usr/bin/env python3
import json
import os

expected = os.environ["EXPECTED_CONFIG"]
if expected == "default":
    assert "CLAUDE_CONFIG_DIR" not in os.environ
else:
    assert os.environ["CLAUDE_CONFIG_DIR"] == os.environ["EXPECTED_%s_DIR" % expected.upper()]
for name in (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_REFRESH_TOKEN",
):
    assert name not in os.environ
print(json.dumps({
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "identity isolated",
    "session_id": "variant-boundary",
}))
""",
        encoding="utf-8",
    )
    fake_claude.chmod(0o755)
    alternate = tmp_path / ".claude-alt"
    alternate2 = tmp_path / ".claude-alt2"
    environment = {
        **os.environ,
        "ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR": str(alternate),
        "ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR": str(alternate2),
        # The judge row reaches oneharness without a wrapper, and every config now
        # names a Codex variant whose indirection must be set.
        "ORCHESTRATOR_CODEX_ALT_HOME": str(tmp_path / ".codex-alt"),
        "CLAUDE_CONFIG_DIR": str(alternate),
        "ANTHROPIC_API_KEY": "ambient-api-key",
        "ANTHROPIC_AUTH_TOKEN": "ambient-auth-token",
        "CLAUDE_CODE_OAUTH_TOKEN": "ambient-oauth-token",
        "CLAUDE_CODE_OAUTH_REFRESH_TOKEN": "ambient-refresh-token",
        "EXPECTED_CONFIG": expected_config,
        "EXPECTED_ALTERNATE_DIR": str(alternate),
        "EXPECTED_ALTERNATE2_DIR": str(alternate2),
        "ONEHARNESS_HISTORY": "false",
    }
    environment.pop("ORCHESTRATOR_AGENT_STATUS_DIR", None)

    wrapper = ROLE_WRAPPERS.get(config_name)
    command = (
        [str(wrapper), "run"]
        if wrapper is not None
        else [oneharness_bin, "run", "--config", str(REPO_ROOT / config_name)]
    )
    result = subprocess.run(
        [
            *command,
            "--harness",
            harness_id,
            "--bin",
            f"{harness_id}={fake_claude}",
            "--mode",
            "default",
            "--prompt",
            "prove child environment",
            "--compact",
        ],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["results"][0]["harness_id"] == harness_id
    assert report["results"][0]["status"] == "ok"
    assert report["results"][0]["text"] == "identity isolated"


@pytest.mark.parametrize(
    "config_name",
    [
        "oneharness.toml",
        "oneharness.judge.toml",
        "oneharness.llmlint.toml",
        "oneharness.orchestrator.toml",
    ],
)
def test_alternate_codex_identity_routes_its_home_and_masks_an_ambient_api_key(
    tmp_path: Path, oneharness_bin: str, config_name: str
) -> None:
    """Run the alternate identity for real and read back the child's environment.

    The sibling test above proves this for the Claude variants; the fallthrough
    tests only ever SKIP `codex:alternate` via a missing executable, so nothing
    else exercises the routing that makes a second Codex account a distinct
    identity. Both halves matter: `env_from` must map the portable indirection
    into CODEX_HOME, and `OPENAI_API_KEY` must be gone, because `codex login`
    writes ChatGPT tokens into that home and an ambient key would outrank them
    and silently bill the wrong account.
    """
    codex_alt = tmp_path / "codex-alt"
    codex_alt.mkdir()
    fake_codex = tmp_path / "codex"
    fake_codex.write_text(
        """#!/usr/bin/env python3
import json
import os

assert os.environ["CODEX_HOME"] == os.environ["EXPECTED_CODEX_HOME"], os.environ["CODEX_HOME"]
assert "OPENAI_API_KEY" not in os.environ
print(json.dumps({"type": "thread.started", "thread_id": "codex-variant"}))
print(json.dumps({
    "type": "item.completed",
    "item": {"type": "agent_message", "text": "codex identity isolated"},
}))
print(json.dumps({
    "type": "turn.completed",
    "usage": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1},
}))
""",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)

    result = subprocess.run(
        [
            oneharness_bin,
            "run",
            "--config",
            str(REPO_ROOT / config_name),
            "--harness",
            "codex:alternate",
            "--bin",
            f"codex:alternate={fake_codex}",
            "--mode",
            "default",
            "--prompt",
            "prove codex child environment",
            "--compact",
        ],
        cwd=REPO_ROOT,
        env={
            **{k: v for k, v in os.environ.items() if k != "ONEHARNESS_HARNESSES"},
            "ORCHESTRATOR_CODEX_ALT_HOME": str(codex_alt),
            "EXPECTED_CODEX_HOME": str(codex_alt),
            # The value the variant's `unset_env` must strip from the child.
            "OPENAI_API_KEY": "ambient-openai-key",
            "ONEHARNESS_HISTORY": "false",
        },
        text=True,
        capture_output=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["results"][0]["harness_id"] == "codex:alternate"
    assert report["results"][0]["status"] == "ok"
    assert report["results"][0]["text"] == "codex identity isolated"


@pytest.mark.parametrize(
    ("config_name", "missing_harness", "fallback_harness", "skipped_between"),
    [
        # The worker leads with both alternate Claude subscriptions, so an
        # unusable first one falls through the second before it reaches Codex.
        (
            "oneharness.toml",
            "claude-code:alternate",
            "codex",
            ("claude-code:alternate2",),
        ),
        # `--bin codex=` rebinds only the base id, so the alternate identity still
        # runs the real Codex — against an empty alternate home, which is the `auth`
        # fallthrough the committed chains depend on.
        ("oneharness.judge.toml", "codex", "claude-code:alternate", ("codex:alternate",)),
        # llmlint is no longer Codex-only: past both Codex identities it reaches the
        # same alternate subscriptions the workers use.
        ("oneharness.llmlint.toml", "codex", "claude-code:alternate", ("codex:alternate",)),
        # The orchestrator is the reverse of the worker: codex carries the role so a
        # long-lived supervisor never queues in front of workers for their subscription.
        (
            "oneharness.orchestrator.toml",
            "codex",
            "claude-code:alternate",
            ("codex:alternate",),
        ),
    ],
)
def test_configured_harness_fallbacks_recover_when_preferred_executable_is_unavailable(
    tmp_path: Path,
    oneharness_bin: str,
    config_name: str,
    missing_harness: str,
    fallback_harness: str,
    skipped_between: tuple[str, ...],
) -> None:
    fake_codex = tmp_path / "codex"
    fake_codex.write_text(
        """#!/usr/bin/env python3
import json

print(json.dumps({"type": "thread.started", "thread_id": "fallback-codex"}))
print(json.dumps({
    "type": "item.completed",
    "item": {"type": "agent_message", "text": "fallback recovered"},
}))
print(json.dumps({
    "type": "turn.completed",
    "usage": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1},
}))
""",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    fake_claude = tmp_path / "claude"
    fake_claude.write_text(
        """#!/usr/bin/env python3
import json

print(json.dumps({
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "fallback recovered",
    "session_id": "fallback-claude",
}))
""",
        encoding="utf-8",
    )
    fake_claude.chmod(0o755)
    fallback_bin = fake_codex if fallback_harness == "codex" else fake_claude
    codex_alt = tmp_path / "codex-alt"
    codex_alt.mkdir()
    environment = {
        **{key: value for key, value in os.environ.items() if key != "ONEHARNESS_HARNESSES"},
        "ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR": str(tmp_path / "absent-claude-alt"),
        "ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR": str(tmp_path / "absent-claude-alt2"),
        # Invoked without a wrapper, so nothing else exports the indirections that
        # the codex and claude-code variants name; oneharness refuses to start
        # while one is unset.
        "ORCHESTRATOR_CODEX_ALT_HOME": str(codex_alt),
        "ONEHARNESS_HISTORY": "false",
    }
    # `--bin` binds a base id only, so each intermediate variant needs its own.
    # Without this the alternate identity runs the REAL Codex against a fresh
    # CODEX_HOME, which bootstraps that home by cloning the plugin repository in
    # the background — work that outlives the test and trips the leak guard.
    skipped_bins = [
        argument
        for harness_id in skipped_between
        for argument in ("--bin", f"{harness_id}={tmp_path / 'missing-executable'}")
    ]

    result = subprocess.run(
        [
            oneharness_bin,
            "run",
            "--config",
            str(REPO_ROOT / config_name),
            "--bin",
            f"{missing_harness}={tmp_path / 'missing-executable'}",
            *skipped_bins,
            "--bin",
            f"{fallback_harness}={fallback_bin}",
            "--mode",
            "default",
            "--prompt",
            "prove fallback recovery",
            "--compact",
        ],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert [item["harness_id"] for item in report["results"]] == [
        missing_harness,
        *skipped_between,
        fallback_harness,
    ]
    # Every candidate ahead of the fallback must be classified as a fallthrough, or
    # the chain would stop at it instead of recovering.
    assert all(item["status"] == "skipped" for item in report["results"][:-1])
    assert [entry["harness"] for entry in report["fallback"]["fell_through"]] == [
        missing_harness,
        *skipped_between,
    ]
    assert report["fallback"]["ran"] == fallback_harness
    assert report["results"][-1]["status"] == "ok"
    assert report["results"][-1]["text"] == "fallback recovered"


def test_agent_config_falls_back_to_codex_after_claude_auth_rejection(
    tmp_path: Path, oneharness_bin: str
) -> None:
    fake_claude = tmp_path / "claude"
    fake_claude.write_text(
        "#!/bin/sh\nprintf '%s\\n' 'authentication failed: login required' >&2\nexit 1\n",
        encoding="utf-8",
    )
    fake_claude.chmod(0o755)
    fake_codex = tmp_path / "codex"
    fake_codex.write_text(
        """#!/usr/bin/env python3
import json

print(json.dumps({"type": "thread.started", "thread_id": "auth-fallback-codex"}))
print(json.dumps({
    "type": "item.completed",
    "item": {"type": "agent_message", "text": "auth fallback recovered"},
}))
print(json.dumps({
    "type": "turn.completed",
    "usage": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1},
}))
""",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)

    result = subprocess.run(
        [
            oneharness_bin,
            "run",
            "--config",
            str(REPO_ROOT / "oneharness.toml"),
            "--bin",
            f"claude-code:alternate={fake_claude}",
            # The second alternate subscription sits between them in the worker
            # chain, so it is part of the path under test — and binding it keeps a
            # real `claude` from being spawned against an unauthenticated directory.
            "--bin",
            f"claude-code:alternate2={fake_claude}",
            "--bin",
            f"codex={fake_codex}",
            "--mode",
            "default",
            "--prompt",
            "prove auth fallback recovery",
            "--compact",
        ],
        cwd=REPO_ROOT,
        env={
            **{key: value for key, value in os.environ.items() if key != "ONEHARNESS_HARNESSES"},
            "ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR": str(tmp_path / ".claude-alt"),
            "ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR": str(tmp_path / ".claude-alt2"),
            # No wrapper here, so this invocation must export the alternate-Codex
            # indirection itself; the chain names it too.
            "ORCHESTRATOR_CODEX_ALT_HOME": str(tmp_path / ".codex-alt"),
            "ONEHARNESS_HISTORY": "false",
        },
        text=True,
        capture_output=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert [item["harness_id"] for item in report["results"]] == [
        "claude-code:alternate",
        "claude-code:alternate2",
        "codex",
    ]
    # An unauthenticated Claude identity is classified `auth` and falls through —
    # the property that lets the committed chains name a subscription nobody has
    # logged into yet without hard-failing the whole chain.
    assert [item["failure_kind"] for item in report["results"][:2]] == ["auth", "auth"]
    assert all(item["status"] == "nonzero" for item in report["results"][:2])
    assert report["results"][2]["status"] == "ok"
    assert report["results"][2]["text"] == "auth fallback recovered"


def test_agent_wrapper_validates_alternate_identity_and_recovers_through_real_oneharness(
    tmp_path: Path, oneharness_bin: str
) -> None:
    wrapper = REPO_ROOT / "scripts" / "oneharness-agent.sh"
    environment = {
        **os.environ,
        "PATH": f"{Path(oneharness_bin).parent}:{os.environ['PATH']}",
        "ONEHARNESS_HISTORY": "false",
    }
    environment.pop("ORCHESTRATOR_AGENT_STATUS_DIR", None)

    relative = subprocess.run(
        [str(wrapper), "run", "--prompt", "must not run"],
        cwd=REPO_ROOT,
        env={**environment, "ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR": "relative"},
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert relative.returncode == 2
    assert "alternate Claude config path must be absolute" in relative.stderr

    inaccessible = tmp_path / "inaccessible"
    inaccessible.mkdir(mode=0o600)
    blocked = subprocess.run(
        [str(wrapper), "run", "--prompt", "must not run"],
        cwd=REPO_ROOT,
        env={
            **environment,
            "ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR": str(inaccessible),
        },
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert blocked.returncode == 2
    assert "not an accessible directory" in blocked.stderr

    fake_codex = tmp_path / "codex"
    fake_codex.write_text(
        """#!/usr/bin/env python3
import json

print(json.dumps({"type": "thread.started", "thread_id": "wrapper-fallback"}))
print(json.dumps({
    "type": "item.completed",
    "item": {"type": "agent_message", "text": "wrapper fallback recovered"},
}))
print(json.dumps({
    "type": "turn.completed",
    "usage": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1},
}))
""",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    recovered = subprocess.run(
        [
            str(wrapper),
            "run",
            "--bin",
            f"codex={fake_codex}",
            "--mode",
            "default",
            "--prompt",
            "prove wrapper fallback",
            "--compact",
        ],
        cwd=REPO_ROOT,
        env={
            **environment,
            "ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR": str(tmp_path / "absent"),
            "ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR": str(tmp_path / "absent2"),
        },
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert recovered.returncode == 0, recovered.stderr
    report = json.loads(recovered.stdout)
    # Both alternate subscriptions are absent here, so the wrapper substitutes a
    # chain without them and codex — the next configured candidate — carries the
    # run. Nothing was spawned against a directory nobody has logged into.
    assert [item["harness_id"] for item in report["results"]] == ["codex"]
    assert report["results"][0]["status"] == "ok"
    assert report["results"][0]["text"] == "wrapper fallback recovered"
    assert not (tmp_path / "absent").exists()
    assert not (tmp_path / "absent2").exists()


@requires_workspace_install
@pytest.mark.parametrize(
    ("recipe_args", "expected_args"),
    [
        (["lint-llm", "AGENTS.md"], ["AGENTS.md"]),
        (
            # The diff recipe resolves its base ref before Nx hashes it, and judges
            # exactly the commit it keyed on. `--skip-nx-cache` reaches Nx, not
            # llmlint, and keeps this boundary check off the recorded verdict.
            ["lint-llm-diff", "HEAD", "--skip-nx-cache"],
            ["--diff", "--diff-base", "{base_sha}"],
        ),
    ],
)
def test_just_llmlint_recipes_pin_the_dedicated_harness_boundary(
    tmp_path: Path, recipe_args: list[str], expected_args: list[str]
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "llmlint-record.json"
    fake_llmlint = bin_dir / "llmlint"
    fake_llmlint.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys

if sys.argv[1:2] == ["--version"]:
    print("llmlint 0.0.0-test")
    sys.exit(0)
if sys.argv[1:2] == ["config"]:
    print("{}")
    sys.exit(0)

with open(os.environ["LLMLINT_RECORD"], "w", encoding="utf-8") as stream:
    json.dump({
        "argv": sys.argv[1:],
        "bin": os.environ.get("LLMLINT_ONEHARNESS_BIN"),
        "labels": os.environ["ONEHARNESS_HISTORY_LABELS"],
    }, stream)
""",
        encoding="utf-8",
    )
    fake_llmlint.chmod(0o755)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()

    result = subprocess.run(
        ["just", *recipe_args],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "LLMLINT_RECORD": str(record),
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
        },
        text=True,
        capture_output=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    observed = json.loads(record.read_text(encoding="utf-8"))
    assert observed["argv"] == [item.replace("{base_sha}", base_sha) for item in expected_args]
    assert observed["bin"] == str(REPO_ROOT / "scripts" / "llmlint-oneharness.sh")
    assert "role=llmlint" in observed["labels"].split(",")


def test_run_onejudge_config_error_raises(onejudge_bin) -> None:
    bad = {
        "provider": {"kind": "command", "command": [sys.executable, str(FAKE_BACKEND)]},
        "agent": {"name": "a", "dir": ".", "instructions": "x"},
        "user": {"persona": "p", "done_when": "d", "max_turns": 2},
        "unknown_field": 123,  # onejudge validates deny_unknown_fields → exit 2
    }
    with pytest.raises(DispatchError, match="exit 2") as raised:
        run_onejudge(bad, "task", onejudge_bin=onejudge_bin)
    assert "unknown_field" in str(raised.value)
