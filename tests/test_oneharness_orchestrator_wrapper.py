"""Regression test for the orchestrator-side oneharness wrapper.

`launch_orchestrator` pins `scripts/oneharness-orchestrator.sh` as the launched
process's oneharness binary. The wrapper is what forces this role's own harness
chain and exports the alternate-Claude config indirection its fallback variant
names — the launch has no project dir, so nothing else does either. These tests
drive the real script with a stub ``oneharness`` on PATH, faking only the
downstream binary, exactly as the agent wrapper's tests do.
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

from orchestrator import REPO_ROOT

WRAPPER = REPO_ROOT / "scripts" / "oneharness-orchestrator.sh"
AGENT_WRAPPER = REPO_ROOT / "scripts" / "oneharness-agent.sh"


def _stub_oneharness(tmp_path: Path) -> Path:
    """Install a stub ``oneharness`` that records its argv and derived environment."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "oneharness"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'printf \'%s\\n\' "$@" > "$ONEHARNESS_ARGS_FILE"\n'
        'printf \'%s\\n\' "$ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR" > "$ONEHARNESS_ENV_FILE"\n',
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir


def _run_wrapper(
    tmp_path: Path,
    argv: list[str],
    *,
    wrapper: Path = WRAPPER,
    home: Path | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[str], str]:
    """Run a wrapper with the stub on PATH; return (proc, recorded argv, alt dir)."""
    bin_dir = _stub_oneharness(tmp_path)
    args_file = tmp_path / "oneharness-argv"
    env_file = tmp_path / "oneharness-env"
    proc = subprocess.run(
        ["bash", str(wrapper), *argv],
        text=True,
        capture_output=True,
        # ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR is deliberately absent: a fresh shell
        # never exports it, and the orchestrator must launch anyway.
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "ONEHARNESS_ARGS_FILE": str(args_file),
            "ONEHARNESS_ENV_FILE": str(env_file),
            "HOME": str(home if home is not None else tmp_path / "home"),
        },
    )
    recorded = args_file.read_text(encoding="utf-8").splitlines() if args_file.exists() else []
    derived = env_file.read_text(encoding="utf-8").strip() if env_file.exists() else ""
    return proc, recorded, derived


def test_orchestrator_run_forces_its_own_config_without_an_exported_alternate_dir(
    tmp_path: Path,
) -> None:
    proc, argv, alternate = _run_wrapper(
        tmp_path,
        ["run", "--compact", "--events", "--system", "role", "--prompt-file", "-"],
    )
    assert proc.returncode == 0, proc.stderr
    assert argv.count("--config") == 1
    assert argv[argv.index("--config") + 1] == f"{REPO_ROOT}/oneharness.orchestrator.toml"
    assert alternate == str(tmp_path / "home" / ".claude-alt")


def test_orchestrator_wrapper_passes_an_explicit_config_through(tmp_path: Path) -> None:
    # oneharness rejects a duplicate --config, so a caller that already chose one
    # must reach it untouched.
    chosen = tmp_path / "oneharness.chosen.toml"
    chosen.write_text('harnesses = ["codex"]\n', encoding="utf-8")
    proc, argv, alternate = _run_wrapper(tmp_path, ["run", "--config", str(chosen)])
    assert proc.returncode == 0, proc.stderr
    assert argv.count("--config") == 1
    assert argv[argv.index("--config") + 1] == str(chosen)
    assert f"{REPO_ROOT}/oneharness.orchestrator.toml" not in argv
    assert alternate == str(tmp_path / "home" / ".claude-alt")


def test_orchestrator_wrapper_rejects_a_non_run_subcommand(tmp_path: Path) -> None:
    proc, argv, _ = _run_wrapper(tmp_path, ["config"])
    assert proc.returncode == 2
    assert "expected the 'run' subcommand" in proc.stderr
    assert argv == []


def test_orchestrator_wrapper_rejects_an_inaccessible_alternate_config_directory(
    tmp_path: Path,
) -> None:
    home = tmp_path / "blocked-home"
    home.mkdir()
    (home / ".claude-alt").write_text("not a directory\n", encoding="utf-8")
    proc, argv, _ = _run_wrapper(tmp_path, ["run", "--prompt", "probe"], home=home)
    assert proc.returncode == 2
    assert "oneharness-orchestrator: alternate Claude config path is not an accessible" in (
        proc.stderr
    )
    assert argv == []


def test_missing_orchestrator_config_is_rejected_before_invoking_oneharness(
    tmp_path: Path,
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    copied = scripts / WRAPPER.name
    copied.write_bytes(WRAPPER.read_bytes())
    copied.chmod(0o755)
    library = REPO_ROOT / "scripts" / "claude-alt-config-dir.sh"
    (scripts / library.name).write_bytes(library.read_bytes())

    proc, argv, _ = _run_wrapper(tmp_path, ["run", "--prompt", "must not run"], wrapper=copied)

    assert proc.returncode == 2
    assert "required orchestrator config is not a readable regular file" in proc.stderr
    assert argv == []


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
    copied = scripts / WRAPPER.name
    copied.write_bytes(WRAPPER.read_bytes())
    copied.chmod(0o755)
    # Deliberately no claude-alt-config-dir.sh beside it, unlike the sibling test.

    proc, argv, _ = _run_wrapper(tmp_path, ["run", "--prompt", "must not run"], wrapper=copied)

    assert proc.returncode == 2
    assert "required helper is not a readable regular file" in proc.stderr
    assert "just bootstrap" in proc.stderr  # the concrete way back
    assert argv == []


def test_both_wrappers_derive_one_shared_alternate_config_default(tmp_path: Path) -> None:
    """The $HOME rule has one source; a second copy would show up as a mismatch."""
    home = tmp_path / "shared-home"
    home.mkdir()
    _, _, orchestrator_default = _run_wrapper(
        tmp_path / "orchestrator", ["run", "--prompt", "probe"], home=home
    )
    _, _, agent_default = _run_wrapper(
        tmp_path / "agent", ["run", "--prompt", "probe"], wrapper=AGENT_WRAPPER, home=home
    )
    assert orchestrator_default == agent_default == str(home / ".claude-alt")
