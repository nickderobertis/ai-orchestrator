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
HELPERS = (
    REPO_ROOT / "scripts" / "claude-alt-config-dir.sh",
    REPO_ROOT / "scripts" / "codex-alt-home.sh",
)


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
    tmp_path: Path, argv: list[str], *, wrapper: Path | None = None
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    binary, recorded = _stub_oneharness(tmp_path)
    proc = subprocess.run(
        [str(wrapper or WRAPPER), *argv],
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


def _wrapper_without(tmp_path: Path, *present: Path) -> Path:
    """A copy of the wrapper beside only the helpers named, mimicking a partial checkout."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    copied = scripts / WRAPPER.name
    copied.write_bytes(WRAPPER.read_bytes())
    copied.chmod(0o755)
    for helper in present:
        (scripts / helper.name).write_bytes(helper.read_bytes())
    return copied


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


def test_missing_alternate_config_helper_is_rejected_before_invoking_oneharness(
    tmp_path: Path,
) -> None:
    """A wrapper without its shared helper must name the file and the way back.

    The helper is a separate file on disk, so a partial checkout can leave the
    wrapper without it. Sourcing it unguarded would fail through the shell's own
    "No such file" line, which names neither the contract nor the recovery.
    """
    copied = _wrapper_without(tmp_path)

    proc, delivered = _run_wrapper(tmp_path, ["--harness", "codex"], wrapper=copied)

    assert proc.returncode == 2
    assert "oneharness-usage: required helper is not a readable regular file" in proc.stderr
    assert "claude-alt-config-dir.sh" in proc.stderr
    assert "just bootstrap" in proc.stderr  # the concrete way back
    assert delivered == []


def test_missing_codex_alt_helper_is_rejected_before_invoking_oneharness(
    tmp_path: Path,
) -> None:
    """The second helper needs the same guard as the first, and its own diagnostic.

    The Claude helper is present here on purpose, so it is specifically the
    codex-alt-home.sh lookup that fails — otherwise this would pass on the sibling
    guard above and prove nothing.
    """
    copied = _wrapper_without(tmp_path, HELPERS[0])

    proc, delivered = _run_wrapper(tmp_path, ["--harness", "codex"], wrapper=copied)

    assert proc.returncode == 2
    assert "oneharness-usage: required helper is not a readable regular file" in proc.stderr
    assert "codex-alt-home.sh" in proc.stderr
    assert "just bootstrap" in proc.stderr  # the concrete way back
    assert delivered == []


def test_an_unreadable_helper_is_rejected_like_an_absent_one(tmp_path: Path) -> None:
    """The guard tests readability, not just presence, so a mode-0 file fails here too."""
    copied = _wrapper_without(tmp_path, *HELPERS)
    unreadable = copied.parent / HELPERS[0].name
    unreadable.chmod(0o000)

    proc, delivered = _run_wrapper(tmp_path, ["--harness", "codex"], wrapper=copied)

    try:
        assert proc.returncode == 2
        assert "oneharness-usage: required helper is not a readable regular file" in proc.stderr
        assert delivered == []
    finally:
        # Restored so the temporary tree can be cleaned up.
        unreadable.chmod(0o644)


def test_the_wrapper_passes_its_arguments_through_unmangled(tmp_path: Path) -> None:
    """Every probe argument reaches oneharness whole, spaces and all."""
    argv = ["--harness", "codex,codex:alternate", "--cwd", str(tmp_path / "a dir"), "--compact"]

    proc, delivered = _run_wrapper(tmp_path, argv)

    assert proc.returncode == 0, proc.stderr
    assert delivered == ["usage", *argv]
    # The recorded argv is the real thing, not a re-split of one joined string.
    assert shlex.join(delivered) != " ".join(delivered)
