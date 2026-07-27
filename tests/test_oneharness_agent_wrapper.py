"""Regression test for the agent-side oneharness wrapper (scripts/oneharness-agent.sh).

onejudge runs BOTH the agent turn and the judge / simulated-user turn through the
same ``provider.bin``. The wrapper forces the orchestrator's agent config, but the
judge side already passes its own ``--config <judge_config>``; the wrapper must not
add a second one (oneharness rejects a duplicate ``--config``). These tests drive
the real script with a stub ``oneharness`` on PATH — only the downstream binary is
faked, mirroring how the e2e suite fakes just the paid harness.
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

from orchestrator import REPO_ROOT

WRAPPER = REPO_ROOT / "scripts" / "oneharness-agent.sh"
ALT_CONFIG_LIBRARY = REPO_ROOT / "scripts" / "claude-alt-config-dir.sh"


def _run_wrapper(
    tmp_path: Path,
    argv: list[str],
    *,
    alternate_config_dir: Path | None = None,
    include_home: bool = True,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run the wrapper with a stub ``oneharness`` on PATH; return (proc, recorded argv)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    args_file = tmp_path / "oneharness-argv"
    stub = bin_dir / "oneharness"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'printf \'%s\\n\' "$@" > "$ONEHARNESS_ARGS_FILE"\n'
        'printf \'%s\\n\' "$ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR" > "$ONEHARNESS_ENV_FILE"\n'
        'printf \'%s\\n\' "${ONEHARNESS_HARNESSES-}" > "$ONEHARNESS_SELECTION_FILE"\n',
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    proc = subprocess.run(
        ["bash", str(WRAPPER), *argv],
        text=True,
        capture_output=True,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "ONEHARNESS_ARGS_FILE": str(args_file),
            "ONEHARNESS_ENV_FILE": str(tmp_path / "oneharness-env"),
            "ONEHARNESS_SELECTION_FILE": str(tmp_path / "oneharness-selection"),
            **({"HOME": str(tmp_path / "home")} if include_home else {}),
            **(
                {"ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR": str(alternate_config_dir)}
                if alternate_config_dir is not None
                else {}
            ),
        },
    )
    recorded = args_file.read_text(encoding="utf-8").splitlines() if args_file.exists() else []
    return proc, recorded


def test_agent_side_forces_the_orchestrator_config(tmp_path: Path) -> None:
    # The agent turn carries no --config, so the wrapper injects exactly the repo's.
    proc, argv = _run_wrapper(
        tmp_path,
        ["run", "--compact", "--events", "--system", "role", "--prompt-file", "-"],
    )
    assert proc.returncode == 0, proc.stderr
    assert argv.count("--config") == 1
    assert argv[argv.index("--config") + 1] == f"{REPO_ROOT}/oneharness.toml"
    assert (tmp_path / "oneharness-env").read_text(encoding="utf-8").strip() == str(
        tmp_path / "home" / ".claude-alt"
    )
    assert (tmp_path / "oneharness-selection").read_text(encoding="utf-8").strip() == "codex"


def test_agent_side_preserves_explicit_alternate_config_dir(tmp_path: Path) -> None:
    explicit = tmp_path / "second-account"
    explicit.mkdir()
    proc, _ = _run_wrapper(
        tmp_path,
        ["run", "--compact", "--prompt", "probe"],
        alternate_config_dir=explicit,
    )
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "oneharness-env").read_text(encoding="utf-8").strip() == str(explicit)
    assert not (tmp_path / "oneharness-selection").read_text(encoding="utf-8").strip()


def test_explicit_alternate_config_dir_does_not_require_home(tmp_path: Path) -> None:
    explicit = tmp_path / "second-account"
    explicit.mkdir()
    proc, _ = _run_wrapper(
        tmp_path,
        ["run", "--compact", "--prompt", "probe"],
        alternate_config_dir=explicit,
        include_home=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "oneharness-env").read_text(encoding="utf-8").strip() == str(explicit)


def test_agent_side_rejects_existing_non_directory_alternate_config(tmp_path: Path) -> None:
    invalid = tmp_path / "not-a-directory"
    invalid.write_text("invalid\n", encoding="utf-8")
    proc, argv = _run_wrapper(
        tmp_path,
        ["run", "--compact", "--prompt", "probe"],
        alternate_config_dir=invalid,
    )
    assert proc.returncode == 2
    assert "not an accessible directory" in proc.stderr
    assert argv == []


def test_agent_side_rejects_unsearchable_alternate_config_directory(tmp_path: Path) -> None:
    inaccessible = tmp_path / "inaccessible"
    inaccessible.mkdir(mode=0o600)
    proc, argv = _run_wrapper(
        tmp_path,
        ["run", "--compact", "--prompt", "probe"],
        alternate_config_dir=inaccessible,
    )
    assert proc.returncode == 2
    assert "not an accessible directory" in proc.stderr
    assert argv == []


def test_judge_side_keeps_its_own_config_and_adds_no_second(tmp_path: Path) -> None:
    # The judge / simulated-user turn already selects --config; the wrapper must not
    # add a second one, which oneharness rejects as a duplicate.
    judge_config = tmp_path / "oneharness.judge.toml"
    judge_config.write_text('harnesses = ["codex"]\n', encoding="utf-8")
    judge_cfg = str(judge_config)
    proc, argv = _run_wrapper(
        tmp_path,
        ["run", "--compact", "--prompt-file", "-", "--config", judge_cfg, "--session", "judge-1"],
    )
    assert proc.returncode == 0, proc.stderr
    assert argv.count("--config") == 1
    assert argv[argv.index("--config") + 1] == judge_cfg
    assert f"{REPO_ROOT}/oneharness.toml" not in argv
    assert (tmp_path / "oneharness-env").read_text(encoding="utf-8").strip() == str(
        tmp_path / "home" / ".claude-alt"
    )


def test_judge_side_accepts_inline_config_path(tmp_path: Path) -> None:
    judge_config = tmp_path / "oneharness.judge.toml"
    judge_config.write_text('harnesses = ["codex"]\n', encoding="utf-8")
    proc, argv = _run_wrapper(tmp_path, ["run", f"--config={judge_config}"])
    assert proc.returncode == 0, proc.stderr
    assert f"--config={judge_config}" in argv
    assert f"{REPO_ROOT}/oneharness.toml" not in argv


def test_judge_side_rejects_config_without_a_path(tmp_path: Path) -> None:
    for suffix in (["--config"], ["--config", ""], ["--config="]):
        proc, argv = _run_wrapper(tmp_path, ["run", "--compact", *suffix])
        assert proc.returncode == 2
        assert "--config requires" in proc.stderr
        assert argv == []


def test_judge_side_rejects_unreadable_config_path(tmp_path: Path) -> None:
    missing = tmp_path / "missing.toml"
    proc, argv = _run_wrapper(tmp_path, ["run", "--config", str(missing)])
    assert proc.returncode == 2
    assert f"caller config is not a readable regular file: {missing}" in proc.stderr
    assert "correct the path and retry" in proc.stderr
    assert argv == []


def test_judge_side_rejects_duplicate_config_paths(tmp_path: Path) -> None:
    first = tmp_path / "first.toml"
    second = tmp_path / "second.toml"
    first.write_text('harnesses = ["codex"]\n', encoding="utf-8")
    second.write_text('harnesses = ["codex"]\n', encoding="utf-8")
    proc, argv = _run_wrapper(
        tmp_path,
        ["run", "--config", str(first), f"--config={second}"],
    )
    assert proc.returncode == 2
    assert "--config may be provided only once" in proc.stderr
    assert argv == []


def test_missing_home_is_rejected_before_invoking_oneharness(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "invoked"
    oneharness = bin_dir / "oneharness"
    oneharness.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    oneharness.chmod(0o755)

    proc = subprocess.run(
        ["bash", str(WRAPPER), "run", "--prompt", "must not run"],
        text=True,
        capture_output=True,
        env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
    )

    assert proc.returncode != 0
    assert "HOME is required to locate the alternate Claude config" in proc.stderr
    assert not marker.exists()


def test_non_run_subcommand_is_rejected(tmp_path: Path) -> None:
    proc, _ = _run_wrapper(tmp_path, ["config"])
    assert proc.returncode == 2
    assert "expected the 'run' subcommand" in proc.stderr


def test_missing_agent_config_is_rejected_before_invoking_oneharness(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    copied_wrapper = scripts / WRAPPER.name
    copied_wrapper.write_bytes(WRAPPER.read_bytes())
    copied_wrapper.chmod(0o755)
    # The wrapper sources its alternate-config derivation from a sibling; copy it so
    # the missing *agent config* guard below is what fails, not the helper lookup.
    (scripts / ALT_CONFIG_LIBRARY.name).write_bytes(ALT_CONFIG_LIBRARY.read_bytes())
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "invoked"
    oneharness = bin_dir / "oneharness"
    oneharness.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    oneharness.chmod(0o755)

    proc = subprocess.run(
        ["bash", str(copied_wrapper), "run", "--prompt", "must not run"],
        text=True,
        capture_output=True,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(tmp_path / "home"),
        },
    )

    assert proc.returncode == 2
    assert "required agent config is not a readable regular file" in proc.stderr
    assert not marker.exists()


def test_watchdog_path_forwards_the_task_on_stdin(tmp_path: Path) -> None:
    """The backgrounded heartbeat path must still deliver the task on stdin."""
    status_dir = tmp_path / "orchestrator-watchdog-1" / "agent"
    status_dir.mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stdin_file = tmp_path / "agent-stdin"
    stub = bin_dir / "oneharness"
    stub.write_text(
        '#!/usr/bin/env bash\ncat > "$ONEHARNESS_STDIN_FILE"\n',
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    task = "## What\nRegenerate the four stale baselines.\n"
    proc = subprocess.run(
        ["bash", str(WRAPPER), "run", "--compact", "--prompt-file", "-"],
        text=True,
        input=task,
        capture_output=True,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "ONEHARNESS_STDIN_FILE": str(stdin_file),
            "ORCHESTRATOR_AGENT_STATUS_DIR": str(status_dir),
            "HOME": str(tmp_path / "home"),
        },
    )
    assert proc.returncode == 0, proc.stderr
    assert stdin_file.read_text(encoding="utf-8") == task
    # The heartbeat path is the one under test: it must have run, not the exec branch.
    assert (status_dir / "agent.done").exists()
