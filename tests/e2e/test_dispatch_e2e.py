"""E2E: drive the real onejudge CLI through the SDK, faking only the model.

These run the actual `onejudge` binary as a subprocess against a real persona,
with onejudge's `command` provider pointed at tests/e2e/fake_backend.py. Nothing
in our layer (merge, SDK dispatch, report validation) is mocked.
"""

# llmlint: ignore-file[contracts_have_one_source_or_a_drift_gate] The contract assertion
# preserves SDK/CLI equality while accepting only the explicitly bounded 0.3.3->0.3.4
# bootstrap pair; upgrading the shared supervisor binary during this lifecycle would
# terminate the run.

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import onejudge_sdk
import pytest
import yaml

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

    report = dispatch(
        "engineer",
        task,
        base_path=base_path,
        project_dir=str(target),
        onejudge_bin=onejudge_bin,
        env={
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "REAL_ONEHARNESS_BIN": oneharness_bin,
            "MOCK_STDOUT": mock_stdout,
            "ONEHARNESS_HISTORY": "true",
            "XDG_STATE_HOME": str(state_home),
        },
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
    assert len(agent_runs) == 1
    assert agent_runs[0]["prompt"]
    assert agent_runs[0]["prompt"] == task


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
    assert result["results"]["worker"]["error"] == "worker-died"
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
    ready = tmp_path / "ready"
    release = tmp_path / "never-release"
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "wedged",
                        "persona": "engineer",
                        "task": (
                            f"provider-barrier-ready={ready} provider-barrier-release={release}"
                        ),
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
    release.touch()
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


@pytest.mark.parametrize(
    ("config_name", "harness_id", "expected_config"),
    [
        ("oneharness.toml", "claude-code:alternate", "alternate"),
        ("oneharness.judge.toml", "claude-code:primary", "default"),
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
if expected == "alternate":
    assert os.environ["CLAUDE_CONFIG_DIR"] == os.environ["EXPECTED_ALT_DIR"]
else:
    assert "CLAUDE_CONFIG_DIR" not in os.environ
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
    environment = {
        **os.environ,
        "ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR": str(alternate),
        "CLAUDE_CONFIG_DIR": str(alternate),
        "ANTHROPIC_API_KEY": "ambient-api-key",
        "ANTHROPIC_AUTH_TOKEN": "ambient-auth-token",
        "CLAUDE_CODE_OAUTH_TOKEN": "ambient-oauth-token",
        "CLAUDE_CODE_OAUTH_REFRESH_TOKEN": "ambient-refresh-token",
        "EXPECTED_CONFIG": expected_config,
        "EXPECTED_ALT_DIR": str(alternate),
        "ONEHARNESS_HISTORY": "false",
    }

    result = subprocess.run(
        [
            oneharness_bin,
            "run",
            "--config",
            str(REPO_ROOT / config_name),
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
