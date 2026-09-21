"""Regression test for the usage-probe wrapper (scripts/oneharness-usage.sh).

The probe is read-only, but it still has to resolve the same `env_from` indirections
a dispatch does: oneharness refuses to start when a selected variant's indirection is
unset, so a usage call that skipped the helpers would report every claude-code and
codex variant as unavailable rather than as the capacity it has. These tests drive
the real script with a stub `oneharness`, exactly as its three sibling wrappers'
tests do — only the downstream binary is faked.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

from orchestrator.root import REPO_ROOT

WRAPPER = REPO_ROOT / "scripts" / "oneharness-usage.sh"

#: Every Claude identity's indirection, and the directory under the test's own tree each is
#: pinned to. The probe resolves the same four a dispatch does.
CLAUDE_INDIRECTIONS = {
    "ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR": "claude-alt",
    "ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR": "claude-alt2",
    "ORCHESTRATOR_CLAUDE_PRIMARY_BACKUP_CONFIG_DIR": "claude-primary-backup",
    "ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR": "claude-primary",
}


def _stub_oneharness(tmp_path: Path) -> tuple[Path, Path]:
    """A stub `oneharness` that records its argv and the Claude indirections it inherited."""
    recorded = tmp_path / "argv.json"
    binary = tmp_path / "oneharness"
    binary.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$ONEHARNESS_ARGV_LOG"\n'
        f"for name in {' '.join(CLAUDE_INDIRECTIONS)}; do\n"
        '    printf "%s=%s\\n" "$name" "${!name-}" >> "$ONEHARNESS_ENV_LOG"\n'
        'done\nprintf "{}\\n"\n',
        encoding="utf-8",
    )
    binary.chmod(0o755)
    return binary, recorded


def _run_wrapper(
    tmp_path: Path, argv: list[str]
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    binary, recorded = _stub_oneharness(tmp_path)
    proc = subprocess.run(
        [str(WRAPPER), *argv],
        text=True,
        capture_output=True,
        check=False,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path / "home"),
            "ONEHARNESS_BIN": str(binary),
            "ONEHARNESS_ARGV_LOG": str(recorded),
            "ONEHARNESS_ENV_LOG": str(tmp_path / "environment.log"),
            **{name: str(tmp_path / leaf) for name, leaf in CLAUDE_INDIRECTIONS.items()},
            "ORCHESTRATOR_CODEX_ALT_HOME": str(tmp_path / "codex-alt"),
        },
    )
    delivered = recorded.read_text(encoding="utf-8").splitlines() if recorded.exists() else []
    return proc, delivered


def test_the_usage_probe_reaches_oneharness_with_every_indirection_resolved(
    tmp_path: Path,
) -> None:
    """The happy path: `usage` is prepended and both indirections are exported."""
    proc, delivered = _run_wrapper(tmp_path, ["--harness", "codex", "--format", "json"])

    assert proc.returncode == 0, proc.stderr
    assert delivered == ["usage", "--harness", "codex", "--format", "json"]
    assert json.loads(proc.stdout) == {}
    inherited = (tmp_path / "environment.log").read_text(encoding="utf-8").splitlines()
    assert inherited == [f"{name}={tmp_path / leaf}" for name, leaf in CLAUDE_INDIRECTIONS.items()]
    # `ensure_codex_alt_home` really ran: an absent codex home hard-fails oneharness,
    # which is why that helper — unlike the Claude one — creates its directory.
    assert (tmp_path / "codex-alt").is_dir()


def test_the_wrapper_passes_its_arguments_through_unmangled(tmp_path: Path) -> None:
    """Every probe argument reaches oneharness whole, spaces and all."""
    argv = ["--harness", "codex,codex:alternate", "--cwd", str(tmp_path / "a dir"), "--compact"]

    proc, delivered = _run_wrapper(tmp_path, argv)

    assert proc.returncode == 0, proc.stderr
    assert delivered == ["usage", *argv]
    # The recorded argv is the real thing, not a re-split of one joined string.
    assert shlex.join(delivered) != " ".join(delivered)
