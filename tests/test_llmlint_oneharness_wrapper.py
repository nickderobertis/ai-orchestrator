"""Command-boundary tests for llmlint's oneharness mode adapter."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from orchestrator import REPO_ROOT

WRAPPER = REPO_ROOT / "scripts" / "llmlint-oneharness.sh"


def _run(tmp_path: Path, *args: str) -> list[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    output = tmp_path / "args"
    oneharness = bin_dir / "oneharness"
    oneharness.write_text(
        '#!/bin/sh\nprintf \'%s\\n\' "$@" >"$WRAPPER_ARGS"\n'
        'printf \'%s\\n\' "${ORCHESTRATOR_CODEX_ALT_HOME-}" >"$WRAPPER_CODEX_HOME"\n',
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
            # Pin the alternate-Codex home into the test's own tree: the wrapper
            # creates it when absent, and the default derives from the real $HOME.
            "ORCHESTRATOR_CODEX_ALT_HOME": str(tmp_path / "codex-alt"),
        },
    )
    assert proc.returncode == 0, proc.stderr
    return output.read_text(encoding="utf-8").splitlines()


def test_read_only_judge_keeps_filesystem_sandbox_and_grants_network(tmp_path: Path) -> None:
    assert _run(tmp_path, "run", "--mode", "read-only", "--compact") == [
        "run",
        "--config",
        str(REPO_ROOT / "oneharness.llmlint.toml"),
        "--mode",
        "read-only",
        "--compact",
        "--",
        "-c",
        'sandbox_permissions=["disk-full-read-access","network-full-access"]',
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
