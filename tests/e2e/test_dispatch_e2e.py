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
import subprocess
import sys
from pathlib import Path

import onejudge_sdk
import pytest
import yaml

from orchestrator import PERSONA_DIR, REPO_ROOT
from orchestrator.config import build_effective_config, load_yaml
from orchestrator.dispatch import DispatchError, dispatch, main, run_onejudge

FAKE_BACKEND = REPO_ROOT / "tests" / "e2e" / "fake_backend.py"
FAKE_HARNESS = REPO_ROOT / "tests" / "e2e" / "fake_harness.py"


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
        "test-engineer",
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
        load_yaml(PERSONA_DIR / "test-engineer.yaml"),
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
    for harness in ("codex", "claude"):
        (bin_dir / harness).symlink_to(FAKE_HARNESS)
    log_path = tmp_path / "harness.jsonl"
    env = {
        **os.environ,
        "ONEHARNESS_BIN_CODEX": str(bin_dir / "missing-codex"),
        "ONEHARNESS_BIN_CLAUDE_CODE": str(bin_dir / "claude"),
        "FAKE_HARNESS_LOG": str(log_path),
    }
    # The surrounding recovery dispatch forces claude-code through the
    # environment; this test must exercise its own configured ordered chain, so
    # both the model and harness overrides are dropped from the inherited env.
    env.pop("ONEHARNESS_MODELS", None)
    env.pop("ONEHARNESS_HARNESSES", None)
    # The default session ("dispatch-<persona>") is a fixed global name: oneharness
    # would resume whatever harness a previous run of it bound, so a stored
    # codex-bound session silently overrides the claude-code model asserted here.
    # Deriving the name from this test's target keeps the run on a session of its
    # own without reaching into the global session store.
    session = f"dispatch-models-{tmp_path.parent.name}-{target.name}"
    proc = subprocess.run(
        [
            "orchestrator-dispatch",
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
    attempts = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert attempts == [
        {"harness": "claude", "model": "claude-opus-4-8"},
    ]
    report = json.loads(proc.stdout)
    assert report["schema_version"] == 5
    assert "telemetry" not in report
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
        env=env,
    )
    effective_config = json.loads(effective.stdout)
    assert effective_config["run_mode"]["value"] == "fallback"
    assert effective_config["harnesses"]["value"][0] == "codex"
    if "claude-code" in effective_config["harnesses"]["value"]:
        assert effective_config["harness"]["claude-code"]["model"]["value"] == "claude-opus-4-8"


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
