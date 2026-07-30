"""Regression test for the agent-side oneharness wrapper (scripts/oneharness-agent.sh).

onejudge runs BOTH the agent turn and the judge / simulated-user turn through the
same ``provider.bin``. The wrapper forces the orchestrator's agent config, but the
judge side already passes its own ``--config <judge_config>``; the wrapper must not
add a second one (oneharness rejects a duplicate ``--config``). These tests drive
the real script with a stub ``oneharness`` on PATH — only the downstream binary is
faked, mirroring how the e2e suite fakes just the paid harness.
"""

from __future__ import annotations

import re
import stat
import subprocess
import time
from pathlib import Path

from orchestrator import REPO_ROOT
from orchestrator.dispatch import AGENT_STATUS_NAMES

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


def test_missing_alternate_config_helper_is_rejected_before_invoking_oneharness(
    tmp_path: Path,
) -> None:
    """A wrapper without its shared helper must name the file and the way back.

    The helper is a separate file on disk, so a partial checkout can leave the
    wrapper without it. Sourcing it unguarded would fail through the shell's own
    "No such file" line, which names neither the contract nor the recovery.
    """
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    copied_wrapper = scripts / WRAPPER.name
    copied_wrapper.write_bytes(WRAPPER.read_bytes())
    copied_wrapper.chmod(0o755)
    # Deliberately no claude-alt-config-dir.sh beside it.
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
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path / "home")},
    )

    assert proc.returncode == 2
    assert "required helper is not a readable regular file" in proc.stderr
    assert "just bootstrap" in proc.stderr  # the concrete way back
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
        '#!/usr/bin/env bash\ncat > "$ONEHARNESS_STDIN_FILE"\n'
        'echo "harness: startup chatter nobody asked for" >&2\n',
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
    assert (status_dir / "agent.exit_code").read_text(encoding="utf-8").strip() == "0"
    # A turn that succeeded reports through the protocol on stdout, so the harness
    # chatter it happened to print stays in the record and out of the caller's face.
    assert proc.stderr == "", proc.stderr
    assert "startup chatter" in (status_dir / "agent.stderr").read_text(encoding="utf-8")
    # Nothing died, so nothing claims a reason for a death that did not happen.
    assert not (status_dir / "agent.failure").exists()


def test_status_file_contract_has_one_source_the_wrapper_honors() -> None:
    """The dispatcher's status-file names and the wrapper's must not drift apart.

    The status directory is the whole IPC contract between this shell wrapper and
    the Python dispatcher, and each side spells the filenames itself. Renaming one
    of them on either side alone would not fail to compile or parse; it would just
    make the dispatcher stop seeing a marker, which reads as a healthy worker that
    never finishes.
    """
    script = WRAPPER.read_text(encoding="utf-8")
    for name in AGENT_STATUS_NAMES:
        assert name in script, f"{WRAPPER.name} does not write the {name!r} status file"
    written = set(re.findall(r"\$status_dir/(agent\.[a-z_.]+?)(?:\.tmp)?[\"\s]", script))
    written |= set(re.findall(r"write_status (agent\.[a-z_.]+)", script))
    assert written <= set(AGENT_STATUS_NAMES), (
        f"{WRAPPER.name} writes status files the dispatcher does not know: "
        f"{sorted(written - set(AGENT_STATUS_NAMES))}"
    )


def _park_until_failed(status_dir: Path, bin_dir: Path, tmp_path: Path) -> subprocess.Popen[str]:
    """Start the wrapper and return it parked, having recorded a failed turn."""
    process = subprocess.Popen(
        ["bash", str(WRAPPER), "run", "--compact", "--prompt-file", "-"],
        text=True,
        stdin=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "ORCHESTRATOR_AGENT_STATUS_DIR": str(status_dir),
            "HOME": str(tmp_path / "home"),
        },
    )
    deadline = time.monotonic() + 30
    while not (status_dir / "agent.failed").exists() and time.monotonic() < deadline:
        assert process.poll() is None, "wrapper exited instead of parking for the dispatcher"
        time.sleep(0.02)
    assert (status_dir / "agent.failed").exists(), "the wrapper never recorded the failure"
    return process


def _stub_harness(bin_dir: Path, body: str) -> None:
    stub = bin_dir / "oneharness"
    stub.write_text(f"#!/usr/bin/env bash\n{body}", encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def test_dead_agent_records_its_exit_status_and_stderr_before_parking(tmp_path: Path) -> None:
    """A failed agent leaves the dispatcher an account of why it died.

    The wrapper parks after a failure so the dispatcher can observe the marker, and
    the child's process tree is torn down right after — so anything not written to
    the status directory here is simply lost, and the dispatch reports a bare
    outcome name for a failure nobody can diagnose.
    """
    status_dir = tmp_path / "orchestrator-watchdog-2" / "agent"
    status_dir.mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _stub_harness(
        bin_dir,
        'echo "provider error: 429 rate_limit_error quota exhausted" >&2\nexit 7\n',
    )
    process = _park_until_failed(status_dir, bin_dir, tmp_path)
    try:
        assert (status_dir / "agent.exit_code").read_text(encoding="utf-8").strip() == "7"
        assert (status_dir / "agent.failure").read_text(
            encoding="utf-8"
        ).strip() == "agent harness exited 7"
        captured = (status_dir / "agent.stderr").read_text(encoding="utf-8")
        assert "429 rate_limit_error quota exhausted" in captured
        assert not (status_dir / "agent.done").exists()
    finally:
        process.kill()
        process.communicate(timeout=10)


def test_a_signal_killed_agent_harness_is_recorded_as_a_signal(tmp_path: Path) -> None:
    """An OOM kill and an ordinary non-zero exit must not read the same."""
    status_dir = tmp_path / "orchestrator-watchdog-3" / "agent"
    status_dir.mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _stub_harness(bin_dir, "kill -KILL $$\n")
    process = _park_until_failed(status_dir, bin_dir, tmp_path)
    try:
        reason = (status_dir / "agent.failure").read_text(encoding="utf-8").strip()
    finally:
        process.kill()
        process.communicate(timeout=10)

    assert reason == "agent harness killed by signal 9"


def test_an_unusable_stderr_capture_is_reported_not_assumed_away(tmp_path: Path) -> None:
    """A capture that stopped working must not read as "the harness said nothing"."""
    status_dir = tmp_path / "orchestrator-watchdog-4" / "agent"
    status_dir.mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    # Replacing the file with a directory makes it unwritable regardless of
    # privilege, while the copy already holding the old handle keeps running.
    _stub_harness(
        bin_dir,
        'echo "provider error: 429 rate_limit_error" >&2\n'
        'rm -f "$ORCHESTRATOR_AGENT_STATUS_DIR/agent.stderr"\n'
        'mkdir "$ORCHESTRATOR_AGENT_STATUS_DIR/agent.stderr"\n'
        "exit 7\n",
    )
    process = _park_until_failed(status_dir, bin_dir, tmp_path)
    try:
        reason = (status_dir / "agent.failure").read_text(encoding="utf-8").strip()
    finally:
        process.kill()
        process.communicate(timeout=10)

    assert reason == (
        "agent harness exited 7; agent stderr capture became unwritable, "
        "so its tail may be incomplete"
    )


def test_an_unopenable_stderr_capture_stops_the_turn_before_it_starts(tmp_path: Path) -> None:
    """A capture that never opened would leave a death with no reason at all."""
    status_dir = tmp_path / "orchestrator-watchdog-5" / "agent"
    status_dir.mkdir(parents=True)
    (status_dir / "agent.stderr").mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _stub_harness(bin_dir, "exit 0\n")

    proc = subprocess.run(
        ["bash", str(WRAPPER), "run", "--compact", "--prompt-file", "-"],
        text=True,
        input="",
        capture_output=True,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "ORCHESTRATOR_AGENT_STATUS_DIR": str(status_dir),
            "HOME": str(tmp_path / "home"),
        },
    )

    assert proc.returncode == 2
    assert "cannot open the agent stderr record" in proc.stderr
    assert not (status_dir / "agent.done").exists()
