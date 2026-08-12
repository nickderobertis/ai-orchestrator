"""Command-boundary tests for llmlint's oneharness mode adapter."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from orchestrator.root import REPO_ROOT

WRAPPER = REPO_ROOT / "scripts" / "llmlint-oneharness.sh"


def _run(tmp_path: Path, *args: str) -> list[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    output = tmp_path / "args"
    oneharness = bin_dir / "oneharness"
    oneharness.write_text(
        '#!/bin/sh\nprintf \'%s\\n\' "$@" >"$WRAPPER_ARGS"\n'
        'printf \'%s\\n\' "${ORCHESTRATOR_CODEX_ALT_HOME-}" >"$WRAPPER_CODEX_HOME"\n'
        'printf \'%s\\n\' "${ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR-}" >"$WRAPPER_CLAUDE_ALT"\n'
        'printf \'%s\\n\' "${ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR-}" >"$WRAPPER_CLAUDE_ALT2"\n',
        encoding="utf-8",
    )
    oneharness.chmod(0o755)
    proc = subprocess.run(
        [WRAPPER, *args],
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "WRAPPER_ARGS": str(output),
            "WRAPPER_CODEX_HOME": str(tmp_path / "codex-home-export"),
            "WRAPPER_CLAUDE_ALT": str(tmp_path / "claude-alt-export"),
            "WRAPPER_CLAUDE_ALT2": str(tmp_path / "claude-alt2-export"),
            # Pin the alternate-Codex home into the test's own tree: the wrapper
            # creates it when absent, and the default derives from the real $HOME.
            "ORCHESTRATOR_CODEX_ALT_HOME": str(tmp_path / "codex-alt"),
            "ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR": str(tmp_path / "claude-alt"),
            "ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR": str(tmp_path / "claude-alt2"),
        },
    )
    assert proc.returncode == 0, proc.stderr
    return output.read_text(encoding="utf-8").splitlines()


def test_read_only_judge_forwards_its_arguments_without_a_harness_passthrough(
    tmp_path: Path,
) -> None:
    """The Codex sandbox grant is no longer a trailing HARNESS_ARG, and must not be.

    oneharness appends `-- <HARNESS_ARG>` to WHICHEVER harness fallback selects, and
    this tier's chain now reaches Claude Code, where `-c` is claude's `--continue`
    boolean — the permission string would be taken as a positional prompt and
    silently replace the lint prompt. It lives in `[harness.codex] args` instead.
    """
    assert _run(tmp_path, "run", "--mode", "read-only", "--compact") == [
        "run",
        "--config",
        str(REPO_ROOT / "oneharness.llmlint.toml"),
        "--mode",
        "read-only",
        "--compact",
    ]


def test_alternate_codex_home_is_exported_and_created_for_the_fallback(tmp_path: Path) -> None:
    """This tier's second candidate is a second Codex identity, so it needs both.

    `[harness.codex.variant.alternate]` maps this indirection into CODEX_HOME, and
    oneharness refuses to run at all when it is unset in the parent. The directory
    must also exist, because the two "not set up yet" states differ: an empty home
    is classified `auth` and falls through to the next candidate, while a missing
    one is an unclassified hard failure that falls through to nothing.
    """
    _run(tmp_path, "run", "--mode", "read-only")
    exported = (tmp_path / "codex-home-export").read_text(encoding="utf-8").strip()
    assert exported == str(tmp_path / "codex-alt")
    assert (tmp_path / "codex-alt").is_dir()


def test_both_alternate_claude_indirections_are_exported(tmp_path: Path) -> None:
    """This tier now names both alternate Claude identities, so it must export both.

    oneharness refuses to start whenever a selected variant's `env_from` source is
    unset in the parent — so a chain that merely NAMES the candidates already
    obliges the wrapper to resolve them, logged in or not. Unlike the Codex home,
    neither directory is created: claude-code classifies an absent one as `auth`
    exactly as it does an empty one, so there is nothing to pre-create.
    """
    _run(tmp_path, "run", "--mode", "read-only")
    assert (tmp_path / "claude-alt-export").read_text(encoding="utf-8").strip() == str(
        tmp_path / "claude-alt"
    )
    assert (tmp_path / "claude-alt2-export").read_text(encoding="utf-8").strip() == str(
        tmp_path / "claude-alt2"
    )
    assert not (tmp_path / "claude-alt").exists()
    assert not (tmp_path / "claude-alt2").exists()


def test_missing_claude_alt_helper_is_rejected_before_invoking_oneharness(
    tmp_path: Path,
) -> None:
    """A partial checkout must name the missing file, not fail through the shell.

    The Codex helper is present here on purpose, so it is specifically the
    claude-alt-config-dir.sh lookup that fails — otherwise this would pass on the
    sibling guard and prove nothing.
    """
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    copied = scripts / WRAPPER.name
    copied.write_bytes(WRAPPER.read_bytes())
    copied.chmod(0o755)
    codex_helper = REPO_ROOT / "scripts" / "codex-alt-home.sh"
    (scripts / codex_helper.name).write_bytes(codex_helper.read_bytes())
    (tmp_path / "oneharness.llmlint.toml").write_bytes(
        (REPO_ROOT / "oneharness.llmlint.toml").read_bytes()
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "invoked"
    oneharness = bin_dir / "oneharness"
    oneharness.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    oneharness.chmod(0o755)

    proc = subprocess.run(
        [copied, "run", "--mode", "read-only"],
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "ORCHESTRATOR_CODEX_ALT_HOME": str(tmp_path / "codex-alt"),
        },
    )

    assert proc.returncode == 2
    assert "claude-alt-config-dir.sh" in proc.stderr
    assert "required helper is not a readable regular file" in proc.stderr
    assert "just bootstrap" in proc.stderr  # the concrete way back
    assert not marker.exists()


def test_real_oneharness_boundary_keeps_the_codex_grant_off_a_claude_candidate(
    tmp_path: Path,
) -> None:
    """Prove at the real boundary that no Codex flag reaches the Claude fallbacks.

    This is the failure the per-harness move exists to prevent: `-c` is claude's
    `--continue`, so the permission string would be consumed as a positional prompt
    and the judge would lint whatever that string happened to mean instead of the
    diff — a wrong verdict, not an error.
    """
    recorded = tmp_path / "claude-argv"
    fake_claude = tmp_path / "claude"
    fake_claude.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys

with open(os.environ["CLAUDE_ARGV"], "w", encoding="utf-8") as stream:
    stream.write("\\n".join(sys.argv[1:]))
print(json.dumps({
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "claude candidate ran",
    "session_id": "llmlint-claude",
}))
""",
        encoding="utf-8",
    )
    fake_claude.chmod(0o755)
    # oneharness 0.6.8 classifies an absent env_from home as auth without spawning.
    # An existing home lets this real boundary test reach its purpose: inspecting
    # the argv handed to the selected custom Claude binary.
    (tmp_path / "claude-alt").mkdir()

    proc = subprocess.run(
        [
            WRAPPER,
            "run",
            "--harness",
            "claude-code:alternate",
            "--bin",
            f"claude-code:alternate={fake_claude}",
            "--mode",
            "read-only",
            "--prompt",
            "judge this diff",
            "--compact",
        ],
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "CLAUDE_ARGV": str(recorded),
            "ORCHESTRATOR_CODEX_ALT_HOME": str(tmp_path / "codex-alt"),
            "ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR": str(tmp_path / "claude-alt"),
            "ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR": str(tmp_path / "claude-alt2"),
            "ONEHARNESS_HISTORY": "false",
        },
    )

    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["results"][0]["harness_id"] == "claude-code:alternate"
    assert report["results"][0]["text"] == "claude candidate ran"
    argv = recorded.read_text(encoding="utf-8").splitlines()
    assert "judge this diff" in argv
    assert not [argument for argument in argv if "sandbox_permissions" in argument]
    assert "-c" not in argv


def test_version_probe_touches_no_filesystem_state(tmp_path: Path) -> None:
    # The probe exits before the run path, so it must not create a Codex home.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    oneharness = bin_dir / "oneharness"
    oneharness.write_text('#!/bin/sh\nprintf "oneharness probe\\n"\n', encoding="utf-8")
    oneharness.chmod(0o755)
    codex_alt = tmp_path / "codex-alt"

    proc = subprocess.run(
        [WRAPPER, "--version"],
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "ORCHESTRATOR_CODEX_ALT_HOME": str(codex_alt),
        },
    )

    assert proc.returncode == 0, proc.stderr
    assert not codex_alt.exists()


def test_other_modes_and_arguments_are_unchanged(tmp_path: Path) -> None:
    assert _run(tmp_path, "run", "--mode", "auto", "--prompt", "read-only") == [
        "run",
        "--config",
        str(REPO_ROOT / "oneharness.llmlint.toml"),
        "--mode",
        "auto",
        "--prompt",
        "read-only",
    ]


def test_real_oneharness_boundary_reads_target_with_read_only_network_grant(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.txt"
    target.write_text("boundary marker\n", encoding="utf-8")
    fake_codex = tmp_path / "codex"
    fake_codex.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
assert args[0] == "exec"
assert args[args.index("--sandbox") + 1] == "read-only"
permission = 'sandbox_permissions=["disk-full-read-access","network-full-access"]'
assert permission in args
with open(os.environ["TARGET_FILE"], encoding="utf-8") as stream:
    marker = stream.read().strip()
print(json.dumps({"type": "thread.started", "thread_id": "boundary"}))
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": marker}}))
""",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)

    proc = subprocess.run(
        [
            WRAPPER,
            "run",
            "--harness",
            "codex",
            "--bin",
            f"codex={fake_codex}",
            "--mode",
            "read-only",
            "--prompt",
            "read the target",
            "--compact",
        ],
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "TARGET_FILE": str(target),
            # Keep the wrapper's alternate-Codex home inside the test's own tree.
            "ORCHESTRATOR_CODEX_ALT_HOME": str(tmp_path / "codex-alt"),
        },
    )

    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["results"][0]["text"] == "boundary marker"
    assert "RTM_NEWADDR" not in proc.stderr
    assert "read no files" not in proc.stdout


def test_oneharness_failure_output_and_status_are_propagated(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    oneharness = bin_dir / "oneharness"
    oneharness.write_text(
        "#!/bin/sh\necho 'provider failed to start' >&2\nexit 42\n", encoding="utf-8"
    )
    oneharness.chmod(0o755)

    proc = subprocess.run(
        [WRAPPER, "run", "--mode", "read-only"],
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "ORCHESTRATOR_CODEX_ALT_HOME": str(tmp_path / "codex-alt"),
        },
    )

    assert proc.returncode == 42
    assert "provider failed to start" in proc.stderr
    assert "run 'oneharness doctor', and retry" in proc.stderr


def test_empty_arguments_are_rejected_before_invoking_oneharness(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "invoked"
    oneharness = bin_dir / "oneharness"
    oneharness.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    oneharness.chmod(0o755)

    proc = subprocess.run(
        [WRAPPER],
        text=True,
        capture_output=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )

    assert proc.returncode == 2
    assert "expected oneharness arguments" in proc.stderr
    assert not marker.exists()


def test_non_run_subcommand_is_rejected_before_invoking_oneharness(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "invoked"
    oneharness = bin_dir / "oneharness"
    oneharness.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    oneharness.chmod(0o755)

    proc = subprocess.run(
        [WRAPPER, "config"],
        text=True,
        capture_output=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )

    assert proc.returncode == 2
    assert "expected the 'run' subcommand" in proc.stderr
    assert not marker.exists()


def test_version_probe_is_forwarded_to_oneharness(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    oneharness = bin_dir / "oneharness"
    oneharness.write_text(
        '#!/bin/sh\n[ "$1" = --version ] && printf "oneharness test-version\\n"\n',
        encoding="utf-8",
    )
    oneharness.chmod(0o755)

    proc = subprocess.run(
        [WRAPPER, "--version"],
        text=True,
        capture_output=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )

    assert proc.returncode == 0
    assert proc.stdout.strip() == "oneharness test-version"


def test_missing_oneharness_reports_recovery_action(tmp_path: Path) -> None:
    proc = subprocess.run(
        ["/bin/bash", WRAPPER, "run", "--mode", "read-only"],
        text=True,
        capture_output=True,
        env={**os.environ, "PATH": str(tmp_path)},
    )

    assert proc.returncode == 127
    assert "required 'oneharness' executable was not found" in proc.stderr
    assert "run 'just bootstrap'" in proc.stderr


def test_missing_dedicated_config_is_rejected_before_invoking_oneharness(
    tmp_path: Path,
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    copied_wrapper = scripts / WRAPPER.name
    copied_wrapper.write_bytes(WRAPPER.read_bytes())
    copied_wrapper.chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "invoked"
    oneharness = bin_dir / "oneharness"
    oneharness.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    oneharness.chmod(0o755)

    proc = subprocess.run(
        [copied_wrapper, "run", "--mode", "read-only"],
        text=True,
        capture_output=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )

    assert proc.returncode == 2
    assert "required config is not a readable regular file" in proc.stderr
    assert not marker.exists()
