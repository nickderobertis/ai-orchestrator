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
import tomllib
from pathlib import Path

from orchestrator import REPO_ROOT
from orchestrator.dispatch import AGENT_STATUS_NAMES, agent_failure_reason

WRAPPER = REPO_ROOT / "scripts" / "oneharness-agent.sh"
ALT_CONFIG_LIBRARY = REPO_ROOT / "scripts" / "claude-alt-config-dir.sh"
CODEX_ALT_LIBRARY = REPO_ROOT / "scripts" / "codex-alt-home.sh"


def _run_wrapper(
    tmp_path: Path,
    argv: list[str],
    *,
    alternate_config_dir: Path | None = None,
    alternate2_config_dir: Path | None = None,
    codex_alt_home: Path | None = None,
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
        'printf \'%s\\n\' "$ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR" > "$ONEHARNESS_ENV2_FILE"\n'
        'printf \'%s\\n\' "${ONEHARNESS_HARNESSES-}" > "$ONEHARNESS_SELECTION_FILE"\n'
        'printf \'%s\\n\' "${ORCHESTRATOR_CODEX_ALT_HOME-}" > "$ONEHARNESS_CODEX_ENV_FILE"\n',
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
            "ONEHARNESS_ENV2_FILE": str(tmp_path / "oneharness-env2"),
            "ONEHARNESS_SELECTION_FILE": str(tmp_path / "oneharness-selection"),
            "ONEHARNESS_CODEX_ENV_FILE": str(tmp_path / "oneharness-codex-env"),
            **({"HOME": str(tmp_path / "home")} if include_home else {}),
            **(
                {"ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR": str(alternate_config_dir)}
                if alternate_config_dir is not None
                else {}
            ),
            **(
                {"ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR": str(alternate2_config_dir)}
                if alternate2_config_dir is not None
                else {}
            ),
            **(
                {"ORCHESTRATOR_CODEX_ALT_HOME": str(codex_alt_home)}
                if codex_alt_home is not None
                else {}
            ),
        },
    )
    recorded = args_file.read_text(encoding="utf-8").splitlines() if args_file.exists() else []
    return proc, recorded


def _selection(tmp_path: Path) -> str:
    return (tmp_path / "oneharness-selection").read_text(encoding="utf-8").strip()


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
    assert (tmp_path / "oneharness-env2").read_text(encoding="utf-8").strip() == str(
        tmp_path / "home" / ".claude-alt2"
    )
    # Neither alternate Claude directory exists in this fixture, so both are dropped
    # — and nothing else is: both Codex identities and the primary Claude one keep
    # their configured relative order, since dropping any of them would cost the
    # worker a candidate it can still authenticate.
    assert _selection(tmp_path) == "codex,codex:alternate,claude-code:primary"
    assert (tmp_path / "oneharness-codex-env").read_text(encoding="utf-8").strip() == str(
        tmp_path / "home" / ".codex-alt"
    )


def test_agent_side_records_its_tool_transcript_exactly_once(tmp_path: Path) -> None:
    """`--events` is a run flag, so this wrapper is where the agent side adopts it.

    The views already read what it records — `just status`'s "Commands:" line and
    `just history-show`'s detail are built from each history record's `events` — but
    claude-code, the workers' primary harness, carries no tool transcript in its
    default output format, so those lines were empty for every claude-code dispatch.

    Exactly once is the other half: oneharness refuses a repeated `--events`, so a
    caller that already asked for it must not have a second one appended.
    """
    proc, argv = _run_wrapper(tmp_path, ["run", "--compact", "--prompt", "probe"])
    assert proc.returncode == 0, proc.stderr
    assert argv.count("--events") == 1

    already, asked = _run_wrapper(tmp_path, ["run", "--compact", "--events", "--prompt", "probe"])
    assert already.returncode == 0, already.stderr
    assert asked.count("--events") == 1

    # The judge side passes its own config and is executed untouched: it supervises
    # a transcript rather than producing one, and its own config decides its format.
    judge_config = tmp_path / "judge.toml"
    judge_config.write_text("history = true\n", encoding="utf-8")
    judged, judge_argv = _run_wrapper(
        tmp_path, ["run", "--config", str(judge_config), "--prompt", "supervise"]
    )
    assert judged.returncode == 0, judged.stderr
    assert "--events" not in judge_argv


def test_agent_side_keeps_the_second_alternate_when_only_the_first_is_absent(
    tmp_path: Path,
) -> None:
    """One absent subscription must not cost the worker the other one.

    These are two separate Claude accounts: a host that has logged into only the
    second must still dispatch through it, in the position the config gives it.
    """
    present = tmp_path / "second-account"
    present.mkdir()
    proc, _ = _run_wrapper(
        tmp_path, ["run", "--compact", "--prompt", "probe"], alternate2_config_dir=present
    )
    assert proc.returncode == 0, proc.stderr
    assert _selection(tmp_path) == (
        "claude-code:alternate2,codex,codex:alternate,claude-code:primary"
    )


def test_agent_side_keeps_the_first_alternate_when_only_the_second_is_absent(
    tmp_path: Path,
) -> None:
    present = tmp_path / "first-account"
    present.mkdir()
    proc, _ = _run_wrapper(
        tmp_path, ["run", "--compact", "--prompt", "probe"], alternate_config_dir=present
    )
    assert proc.returncode == 0, proc.stderr
    assert _selection(tmp_path) == (
        "claude-code:alternate,codex,codex:alternate,claude-code:primary"
    )


def test_agent_side_substitutes_no_chain_when_both_alternates_are_present(
    tmp_path: Path,
) -> None:
    # Nothing to drop, so the configured chain must reach oneharness untouched
    # rather than through a restatement that could drift from it.
    first = tmp_path / "first-account"
    second = tmp_path / "second-account"
    first.mkdir()
    second.mkdir()
    proc, _ = _run_wrapper(
        tmp_path,
        ["run", "--compact", "--prompt", "probe"],
        alternate_config_dir=first,
        alternate2_config_dir=second,
    )
    assert proc.returncode == 0, proc.stderr
    assert _selection(tmp_path) == ""


def test_agent_side_substituted_chain_is_a_subsequence_of_the_committed_one(
    tmp_path: Path,
) -> None:
    """The degraded chain is read from the config, so it can only ever be a subset.

    A chain restated in the wrapper would silently stop matching oneharness.toml
    the next time the committed order changed — and the substitution overrides that
    order for every dispatch on a host with one Claude login.
    """
    configured = tomllib.loads((REPO_ROOT / "oneharness.toml").read_text(encoding="utf-8"))
    proc, _ = _run_wrapper(tmp_path, ["run", "--compact", "--prompt", "probe"])
    assert proc.returncode == 0, proc.stderr
    substituted = _selection(tmp_path).split(",")
    remaining = list(configured["harnesses"])
    for candidate in substituted:
        assert candidate in remaining, f"{candidate} is not in the committed chain"
        remaining = remaining[remaining.index(candidate) + 1 :]
    assert set(configured["harnesses"]) - set(substituted) == {
        "claude-code:alternate",
        "claude-code:alternate2",
    }


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
    # An override selects the directory; it must not also change the default the
    # OTHER identity derives from HOME.
    assert (tmp_path / "oneharness-env2").read_text(encoding="utf-8").strip() == str(
        tmp_path / "home" / ".claude-alt2"
    )


def test_explicit_alternate_identities_do_not_require_home(tmp_path: Path) -> None:
    # HOME is only needed to DERIVE the defaults; a caller that named every alternate
    # identity outright must dispatch on a shell that never exported it.
    explicit = tmp_path / "second-account"
    explicit.mkdir()
    explicit2 = tmp_path / "third-account"
    explicit2.mkdir()
    codex_alt = tmp_path / "second-codex"
    codex_alt.mkdir()
    proc, _ = _run_wrapper(
        tmp_path,
        ["run", "--compact", "--prompt", "probe"],
        alternate_config_dir=explicit,
        alternate2_config_dir=explicit2,
        codex_alt_home=codex_alt,
        include_home=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "oneharness-env").read_text(encoding="utf-8").strip() == str(explicit)
    assert (tmp_path / "oneharness-env2").read_text(encoding="utf-8").strip() == str(explicit2)
    assert (tmp_path / "oneharness-codex-env").read_text(encoding="utf-8").strip() == str(codex_alt)


def test_agent_side_rejects_an_inaccessible_second_alternate_config_directory(
    tmp_path: Path,
) -> None:
    """The second subscription's directory gets the first one's guards, by name.

    Both come from one helper, so a diagnostic that named only "alternate Claude
    config" would send the operator to the wrong directory.
    """
    invalid = tmp_path / "not-a-directory"
    invalid.write_text("invalid\n", encoding="utf-8")
    proc, argv = _run_wrapper(
        tmp_path,
        ["run", "--compact", "--prompt", "probe"],
        alternate2_config_dir=invalid,
    )
    assert proc.returncode == 2
    assert "second alternate Claude config path is not an accessible directory" in proc.stderr
    assert argv == []


def test_agent_side_rejects_a_relative_second_alternate_config_directory(
    tmp_path: Path,
) -> None:
    # env_from hands this straight to the child as CLAUDE_CONFIG_DIR, where a
    # relative path would resolve against whatever directory that child started in.
    proc, argv = _run_wrapper(
        tmp_path,
        ["run", "--compact", "--prompt", "probe"],
        alternate2_config_dir=Path("relative/config"),
    )
    assert proc.returncode == 2
    assert "second alternate Claude config path must be absolute" in proc.stderr
    assert "ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR" in proc.stderr  # the concrete way back
    assert argv == []


def test_agent_side_creates_neither_alternate_claude_config_directory(tmp_path: Path) -> None:
    """The Claude helper must stay side-effect free, unlike the Codex one.

    claude-code classifies an absent CLAUDE_CONFIG_DIR exactly as it classifies an
    empty one — `auth`, which falls through — and creates the directory itself when
    it runs, so there is nothing for the helper to pre-create. Creating one anyway
    would put a config directory on disk for an account nobody has logged into.
    """
    proc, _ = _run_wrapper(tmp_path, ["run", "--compact", "--prompt", "probe"])
    assert proc.returncode == 0, proc.stderr
    assert not (tmp_path / "home" / ".claude-alt").exists()
    assert not (tmp_path / "home" / ".claude-alt2").exists()


def test_agent_side_creates_an_absent_alternate_codex_home(tmp_path: Path) -> None:
    """An empty home is the state that falls through; an absent one hard-fails.

    oneharness classifies a `CODEX_HOME` holding no credentials as `auth` and moves
    to the next candidate, but a `CODEX_HOME` that does not exist is an unclassified
    failure that falls through to nothing. Creating it is what makes the committed
    chain safe on a host with only one Codex login.
    """
    proc, _ = _run_wrapper(tmp_path, ["run", "--compact", "--prompt", "probe"])
    assert proc.returncode == 0, proc.stderr
    created = tmp_path / "home" / ".codex-alt"
    assert created.is_dir()
    assert (tmp_path / "oneharness-codex-env").read_text(encoding="utf-8").strip() == str(created)


def test_agent_side_rejects_an_existing_non_directory_codex_home(tmp_path: Path) -> None:
    invalid = tmp_path / "codex-not-a-directory"
    invalid.write_text("invalid\n", encoding="utf-8")
    proc, argv = _run_wrapper(
        tmp_path,
        ["run", "--compact", "--prompt", "probe"],
        codex_alt_home=invalid,
    )
    assert proc.returncode == 2
    assert "alternate Codex home is not an accessible writable directory" in proc.stderr
    assert argv == []


def test_agent_side_rejects_a_read_only_alternate_codex_home(tmp_path: Path) -> None:
    """A readable home codex cannot write to must fail here, not inside the child.

    Codex initializes state in CODEX_HOME — auth tokens, logs, its own tmp — so a
    directory that satisfies the read checks but denies writes would pass this
    boundary and fail deep in the harness, where the message names neither the
    directory nor the variable that selects it.
    """
    read_only = tmp_path / "read-only-codex-home"
    read_only.mkdir(mode=0o500)
    try:
        proc, argv = _run_wrapper(
            tmp_path,
            ["run", "--compact", "--prompt", "probe"],
            codex_alt_home=read_only,
        )
        assert proc.returncode == 2
        assert "alternate Codex home is not an accessible writable directory" in proc.stderr
        assert argv == []
    finally:
        read_only.chmod(0o700)


def test_agent_side_reports_an_uncreatable_alternate_codex_home(tmp_path: Path) -> None:
    """An unwritable parent must name the fix rather than dispatch into a hard failure.

    The helper creates the home precisely so an unauthenticated one degrades as
    `auth`; if it cannot, dispatching anyway would leave the chain in the missing
    state that falls through to nothing. Fail loudly with the variable to set.
    """
    blocked = tmp_path / "unwritable"
    blocked.mkdir(mode=0o500)
    try:
        proc, argv = _run_wrapper(
            tmp_path,
            ["run", "--compact", "--prompt", "probe"],
            codex_alt_home=blocked / "codex-alt",
        )
        assert proc.returncode == 2
        assert "cannot create the alternate Codex home" in proc.stderr
        assert "ORCHESTRATOR_CODEX_ALT_HOME" in proc.stderr  # the concrete way back
        assert argv == []
    finally:
        # Restore write permission so pytest can clean the directory up.
        blocked.chmod(0o700)


def test_agent_side_rejects_a_relative_codex_home(tmp_path: Path) -> None:
    # env_from hands this straight to the child as CODEX_HOME, where a relative path
    # would resolve against whatever directory that child happened to start in.
    proc, argv = _run_wrapper(
        tmp_path,
        ["run", "--compact", "--prompt", "probe"],
        codex_alt_home=Path("relative/codex-home"),
    )
    assert proc.returncode == 2
    assert "alternate Codex home must be absolute" in proc.stderr
    assert argv == []


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
    # The wrapper sources both alternate-identity derivations from siblings; copy
    # them so the missing *agent config* guard below is what fails, not a helper
    # lookup.
    (scripts / ALT_CONFIG_LIBRARY.name).write_bytes(ALT_CONFIG_LIBRARY.read_bytes())
    (scripts / CODEX_ALT_LIBRARY.name).write_bytes(CODEX_ALT_LIBRARY.read_bytes())
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


def test_the_pid_a_turn_advertises_outlives_the_marker_that_closes_it(tmp_path: Path) -> None:
    """`agent.pid` is the wrapper's own pid, and `agent.done` names it before it exits.

    The dispatcher's liveness rule reads exactly this ordering: a pid that has left
    the process tree unnamed by `agent.done` died mid-turn. That is only sound
    because the advertised pid outlives the marker, so "gone" implies "already
    marked". `test_dispatch_unit.py`'s onejudge double models the same ordering to
    exercise the rule; this is the gate that keeps the two from drifting apart.
    """
    status_dir = tmp_path / "orchestrator-watchdog-ordering" / "agent"
    status_dir.mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "oneharness"
    stub.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    proc = subprocess.Popen(
        ["bash", str(WRAPPER), "run", "--prompt", "task"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "ORCHESTRATOR_AGENT_STATUS_DIR": str(status_dir),
            "HOME": str(tmp_path / "home"),
        },
    )
    _, stderr = proc.communicate(timeout=60)
    assert proc.returncode == 0, stderr

    advertised = (status_dir / "agent.pid").read_text(encoding="utf-8").strip()
    assert advertised == str(proc.pid), "the wrapper advertised a pid that is not its own"
    # Read after the process is gone: the marker was therefore already on disk.
    assert (status_dir / "agent.done").read_text(encoding="utf-8").strip() == advertised
    child = (status_dir / "agent.child.pid").read_text(encoding="utf-8").strip()
    assert child != advertised, "the harness child must be recorded apart from the turn's pid"


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


def test_quota_diagnostic_harness_matches_worker_primary() -> None:
    """DRIFT-GATE the diagnostic identity against the configured first candidate."""
    config = tomllib.loads((REPO_ROOT / "oneharness.toml").read_text(encoding="utf-8"))
    primary = config["harnesses"][0]
    script = WRAPPER.read_text(encoding="utf-8")

    assert primary == "claude-code:alternate"
    assert f"alternate_harness={primary}" in script


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
    stub = bin_dir / "oneharness"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'echo "claude: no conversation found with session id 0dd" >&2\nexit 7\n',
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    proc = subprocess.Popen(
        ["bash", str(WRAPPER), "run", "--prompt", "task"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "ORCHESTRATOR_AGENT_STATUS_DIR": str(status_dir),
            "HOME": str(tmp_path / "home"),
        },
    )
    try:
        failed = status_dir / "agent.failed"
        deadline = time.monotonic() + 30
        while not failed.exists() and time.monotonic() < deadline:
            assert proc.poll() is None, "wrapper exited instead of parking for the dispatcher"
            time.sleep(0.02)
        assert failed.exists(), "the wrapper never recorded the failure"
        assert (status_dir / "agent.exit_code").read_text(encoding="utf-8").strip() == "7"
        assert "no conversation found" in (status_dir / "agent.stderr").read_text(encoding="utf-8")
        assert not (status_dir / "agent.done").exists()
    finally:
        proc.kill()
        proc.communicate(timeout=10)


def test_a_failing_agent_harness_records_why_before_awaiting_recovery(tmp_path: Path) -> None:
    """The dispatcher can only name a provider failure if the wrapper writes it down.

    The exit status alone does not say whether to retry or escalate. The wrapper
    parks after a failed turn so the supervisor can reap it, so this drives the real
    script and reads the markers while it is still parked.
    """
    status_dir = tmp_path / "orchestrator-watchdog-3" / "agent"
    status_dir.mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "oneharness"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'echo "You\'ve hit your session limit · resets 7pm (UTC)"\n'
        "echo 'oneharness: fallback harness `claude-code:alternate` ran but did not succeed' >&2\n"
        "exit 7\n",
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    with subprocess.Popen(
        ["bash", str(WRAPPER), "run", "--compact", "--prompt-file", "-"],
        text=True,
        stdin=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "ORCHESTRATOR_AGENT_STATUS_DIR": str(status_dir),
            "HOME": str(tmp_path / "home"),
        },
    ) as process:
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if (status_dir / "agent.failed").exists():
                    break
                time.sleep(0.05)
            else:  # pragma: no cover - only reached when the wrapper never marks failure
                raise AssertionError("the wrapper never recorded the failed turn")
            reason = (status_dir / "agent.failure").read_text(encoding="utf-8").strip()
            recorded = (status_dir / "agent.stderr").read_text(encoding="utf-8")
        finally:
            process.kill()

    assert reason == "agent harness exited 7"
    assert "out of quota" in recorded
    assert "resets 7pm (UTC)" in recorded
    assert agent_failure_reason(status_dir) == (
        "agent harness exited 7: oneharness: fallback harness `claude-code:alternate` "
        "ran but did not succeed oneharness-agent: dispatch failure: harness "
        "claude-code:alternate is out of quota; You've hit your session limit · "
        "resets 7pm (UTC); configure a usable fallback or retry after the stated reset time"
    )


def test_a_signal_killed_agent_harness_is_recorded_as_a_signal(tmp_path: Path) -> None:
    """An OOM kill and an ordinary non-zero exit must not read the same."""
    status_dir = tmp_path / "orchestrator-watchdog-4" / "agent"
    status_dir.mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "oneharness"
    stub.write_text("#!/usr/bin/env bash\nkill -KILL $$\n", encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    with subprocess.Popen(
        ["bash", str(WRAPPER), "run", "--compact", "--prompt-file", "-"],
        text=True,
        stdin=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "ORCHESTRATOR_AGENT_STATUS_DIR": str(status_dir),
            "HOME": str(tmp_path / "home"),
        },
    ) as process:
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if (status_dir / "agent.failed").exists():
                    break
                time.sleep(0.05)
            else:  # pragma: no cover - only reached when the wrapper never marks failure
                raise AssertionError("the wrapper never recorded the killed turn")
            reason = (status_dir / "agent.failure").read_text(encoding="utf-8").strip()
        finally:
            process.kill()

    assert reason == "agent harness killed by signal 9"


def test_an_unusable_stderr_capture_is_reported_not_assumed_away(tmp_path: Path) -> None:
    """A capture that stopped working must not read as "the harness said nothing"."""
    status_dir = tmp_path / "orchestrator-watchdog-5" / "agent"
    status_dir.mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "oneharness"
    # Replacing the file with a directory makes it unwritable regardless of
    # privilege, while the copy already holding the old handle keeps running.
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'echo "provider error: 429 rate_limit_error" >&2\n'
        'rm -f "$ORCHESTRATOR_AGENT_STATUS_DIR/agent.stderr"\n'
        'mkdir "$ORCHESTRATOR_AGENT_STATUS_DIR/agent.stderr"\n'
        "exit 7\n",
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    with subprocess.Popen(
        ["bash", str(WRAPPER), "run", "--compact", "--prompt-file", "-"],
        text=True,
        stdin=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "ORCHESTRATOR_AGENT_STATUS_DIR": str(status_dir),
            "HOME": str(tmp_path / "home"),
        },
    ) as process:
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if (status_dir / "agent.failed").exists():
                    break
                time.sleep(0.05)
            else:  # pragma: no cover - only reached when the wrapper never marks failure
                raise AssertionError("the wrapper never recorded the failed turn")
            reason = (status_dir / "agent.failure").read_text(encoding="utf-8").strip()
        finally:
            process.kill()

    assert reason == (
        "agent harness exited 7; agent stderr capture became unwritable, "
        "so its tail may be incomplete"
    )


def test_an_unopenable_stderr_capture_stops_the_turn_before_it_starts(tmp_path: Path) -> None:
    """A capture that never opened would leave a death with no reason at all."""
    status_dir = tmp_path / "orchestrator-watchdog-6" / "agent"
    status_dir.mkdir(parents=True)
    (status_dir / "agent.stderr").mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "oneharness"
    stub.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

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


def test_a_failed_stdout_capture_cannot_publish_success(tmp_path: Path) -> None:
    """The wrapper joins its real stdout recorder before publishing a terminal marker."""
    status_dir = tmp_path / "orchestrator-watchdog-stdout" / "agent"
    status_dir.mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "oneharness"
    stub.write_text(
        "#!/usr/bin/env bash\nprintf '%4096s\\n' output\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    with subprocess.Popen(
        [
            "bash",
            "-c",
            'ulimit -f 1; exec bash "$1" run --prompt task',
            "capture-limit",
            str(WRAPPER),
        ],
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "ORCHESTRATOR_AGENT_STATUS_DIR": str(status_dir),
            "HOME": str(tmp_path / "home"),
        },
    ) as process:
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if (status_dir / "agent.failed").exists():
                    break
                assert not (status_dir / "agent.done").exists()
                time.sleep(0.05)
            else:
                raise AssertionError("the wrapper never recorded the capture failure")
            recorded = (status_dir / "agent.stderr").read_text(encoding="utf-8")
            exit_code = (status_dir / "agent.exit_code").read_text(encoding="utf-8").strip()
        finally:
            process.kill()

    assert exit_code == "2"
    assert "agent stdout capture failed" in recorded
