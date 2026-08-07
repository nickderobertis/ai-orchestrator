"""Regression test for the orchestrator-side oneharness wrapper.

`launch_orchestrator` pins `scripts/oneharness-orchestrator.sh` as the launched
process's oneharness binary. The wrapper is what forces this role's own harness
chain and exports the alternate-Claude config indirection its fallback variant
names — the launch has no project dir, so nothing else does either. These tests
drive the real script with a stub ``oneharness`` on PATH, faking only the
downstream binary, exactly as the agent wrapper's tests do.
"""

from __future__ import annotations

import json
import re
import stat
import subprocess
import time
from pathlib import Path

import pytest

from orchestrator import REPO_ROOT
from orchestrator.boundary import (
    BACKOFF_FACTOR,
    DEFAULT_ATTEMPTS,
    DEFAULT_BACKOFF_SECONDS,
    MAX_ATTEMPTS,
    MAX_BACKOFF_SECONDS,
)

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
        'printf \'%s\\n\' "$ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR" > "$ONEHARNESS_ENV_FILE"\n'
        'printf \'%s\\n\' "$ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR" > "$ONEHARNESS_ENV2_FILE"\n'
        'printf \'%s\\n\' "${ORCHESTRATOR_CODEX_ALT_HOME-}" > "$ONEHARNESS_CODEX_ENV_FILE"\n',
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
            "ONEHARNESS_ENV2_FILE": str(tmp_path / "oneharness-env2"),
            "ONEHARNESS_CODEX_ENV_FILE": str(tmp_path / "oneharness-codex-env"),
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
    for library in (
        REPO_ROOT / "scripts" / "claude-alt-config-dir.sh",
        REPO_ROOT / "scripts" / "codex-alt-home.sh",
    ):
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


def test_missing_codex_alt_helper_is_rejected_before_invoking_oneharness(
    tmp_path: Path,
) -> None:
    """The second helper needs the same guard as the first, and its own diagnostic.

    A partial checkout can leave either helper behind. The Claude one is present
    here on purpose, so it is specifically the codex-alt-home.sh lookup that fails
    — otherwise this would pass on the sibling guard and prove nothing.
    """
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    copied = scripts / WRAPPER.name
    copied.write_bytes(WRAPPER.read_bytes())
    copied.chmod(0o755)
    library = REPO_ROOT / "scripts" / "claude-alt-config-dir.sh"
    (scripts / library.name).write_bytes(library.read_bytes())
    # Deliberately no codex-alt-home.sh beside it.

    proc, argv, _ = _run_wrapper(tmp_path, ["run", "--prompt", "must not run"], wrapper=copied)

    assert proc.returncode == 2
    assert "codex-alt-home.sh" in proc.stderr
    assert "required helper is not a readable regular file" in proc.stderr
    assert "just bootstrap" in proc.stderr  # the concrete way back
    assert argv == []


def test_both_wrappers_derive_one_shared_alternate_config_default(tmp_path: Path) -> None:
    """The $HOME rule has one source; a second copy would show up as a mismatch."""
    home = tmp_path / "shared-home"
    home.mkdir()
    orchestrator_dir = tmp_path / "orchestrator"
    agent_dir = tmp_path / "agent"
    _, _, orchestrator_default = _run_wrapper(
        orchestrator_dir, ["run", "--prompt", "probe"], home=home
    )
    _, _, agent_default = _run_wrapper(
        agent_dir, ["run", "--prompt", "probe"], wrapper=AGENT_WRAPPER, home=home
    )
    assert orchestrator_default == agent_default == str(home / ".claude-alt")
    # The SECOND alternate subscription is derived by the same one helper, so it is
    # shared the same way; a wrapper keeping its own copy would show up here.
    second_defaults = {
        (run_dir / "oneharness-env2").read_text(encoding="utf-8").strip()
        for run_dir in (orchestrator_dir, agent_dir)
    }
    assert second_defaults == {str(home / ".claude-alt2")}
    # The alternate-Codex rule is shared the same way, and by a third wrapper too.
    codex_defaults = {
        (run_dir / "oneharness-codex-env").read_text(encoding="utf-8").strip()
        for run_dir in (orchestrator_dir, agent_dir)
    }
    assert codex_defaults == {str(home / ".codex-alt")}


def test_orchestrator_wrapper_rejects_an_inaccessible_alternate_codex_home(
    tmp_path: Path,
) -> None:
    home = tmp_path / "blocked-codex-home"
    home.mkdir()
    (home / ".codex-alt").write_text("not a directory\n", encoding="utf-8")
    proc, argv, _ = _run_wrapper(tmp_path, ["run", "--prompt", "probe"], home=home)
    assert proc.returncode == 2
    assert "oneharness-orchestrator: alternate Codex home is not an accessible" in proc.stderr
    assert argv == []


def _retrying_wrapper(
    tmp_path: Path,
    *,
    body: str,
    attempts: str = "3",
    argv: list[str] | None = None,
    attempts_log: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Drive the real wrapper with a stub oneharness whose behaviour ``body`` decides.

    The stub counts its own invocations in a file, which is what makes "asked
    again" observable at the only boundary that can show it: the wrapper is what
    onejudge runs, and a retry is a second child process, not a return value.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "oneharness"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'count=$(( $(cat "$STUB_COUNT" 2>/dev/null || echo 0) + 1 ))\n'
        'printf \'%s\' "$count" > "$STUB_COUNT"\n' + body,
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    environment = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "STUB_COUNT": str(tmp_path / "stub-count"),
        "ORCHESTRATOR_BOUNDARY_ATTEMPTS": attempts,
        # Nothing here waits for real seconds: the policy under test is "how many
        # times", and a real backoff would only make the suite slower.
        "ORCHESTRATOR_BOUNDARY_BACKOFF_SECONDS": "0",
    }
    if attempts_log is not None:
        environment["ORCHESTRATOR_BOUNDARY_ATTEMPTS_LOG"] = str(attempts_log)
    return subprocess.run(
        ["bash", str(WRAPPER), *(argv or ["run", "--prompt", "probe"])],
        text=True,
        capture_output=True,
        env=environment,
    )


def _stub_invocations(tmp_path: Path) -> int:
    counter = tmp_path / "stub-count"
    return int(counter.read_text(encoding="utf-8")) if counter.is_file() else 0


def test_a_post_round_request_that_produced_nothing_is_asked_again(tmp_path: Path) -> None:
    """The refusal that orphaned two runs in one night is retried, not fatal.

    The first attempt exits non-zero with an empty stdout — a harness that never
    reached the turn — and the second answers. What the caller receives is the
    successful answer and a zero status, so onejudge sees one turn rather than a
    dead process.
    """
    log = tmp_path / "boundary-attempts.jsonl"
    proc = _retrying_wrapper(
        tmp_path,
        body=(
            'if [ "$count" -eq 1 ]; then echo "quota exhausted" >&2; exit 1; fi\n'
            "printf '{\"ok\":true}'\n"
        ),
        attempts_log=log,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == '{"ok":true}'
    assert _stub_invocations(tmp_path) == 2
    # And the retry is recorded where the next round folds it into the journal.
    recorded = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(recorded) == 1
    assert (recorded[0]["role"], recorded[0]["attempt"], recorded[0]["attempts"]) == (
        "orchestrator",
        1,
        3,
    )


def test_an_attempt_that_answered_is_never_asked_twice(tmp_path: Path) -> None:
    """A failing turn that produced output has already answered onejudge.

    onejudge parses this process's stdout as exactly one document, so a second
    attempt appended to a partial answer would corrupt it. Non-zero is therefore
    not on its own a reason to retry — producing nothing is.
    """
    log = tmp_path / "boundary-attempts.jsonl"
    proc = _retrying_wrapper(
        tmp_path,
        body="printf '{\"partial\":true}'\nexit 1\n",
        attempts_log=log,
    )

    assert proc.returncode == 1
    assert proc.stdout == '{"partial":true}'
    assert _stub_invocations(tmp_path) == 1
    assert not log.exists()


def test_the_retry_budget_is_bounded_and_the_last_failure_stands(tmp_path: Path) -> None:
    """A genuinely broken launch path fails every attempt and still fails."""
    log = tmp_path / "boundary-attempts.jsonl"
    proc = _retrying_wrapper(
        tmp_path,
        body='echo "harness cannot start" >&2\nexit 2\n',
        attempts="2",
        attempts_log=log,
    )

    assert proc.returncode == 2
    assert proc.stdout == ""
    assert _stub_invocations(tmp_path) == 2
    assert [json.loads(line)["attempt"] for line in log.read_text().splitlines()] == [1]


def test_a_streamed_turn_is_never_buffered_by_the_retry(tmp_path: Path) -> None:
    """A caller that streams owns its stdout shape, so it passes straight through."""
    proc = _retrying_wrapper(
        tmp_path,
        body='echo "died" >&2\nexit 1\n',
        argv=["run", "--stream", "--prompt", "probe"],
    )

    assert proc.returncode == 1
    assert _stub_invocations(tmp_path) == 1


def test_the_wrapper_restates_the_boundary_policy_the_python_side_owns() -> None:
    """The drift gate for the five numbers the shell cannot import.

    `orchestrator/boundary.py` is the one source of the retry policy, but this
    wrapper runs before any interpreter and has to restate it. Restating without a
    gate is how the two halves of one policy end up disagreeing — the shell riding
    out an outage for two minutes while the journal says five seconds. This reads
    both sides and fails when they part.
    """
    declared = dict(
        re.findall(r"^(BOUNDARY_[A-Z_]+)=(\d+)$", WRAPPER.read_text(encoding="utf-8"), re.M)
    )

    assert declared == {
        "BOUNDARY_DEFAULT_ATTEMPTS": str(DEFAULT_ATTEMPTS),
        "BOUNDARY_DEFAULT_BACKOFF_SECONDS": str(int(DEFAULT_BACKOFF_SECONDS)),
        "BOUNDARY_BACKOFF_FACTOR": str(int(BACKOFF_FACTOR)),
        "BOUNDARY_MAX_BACKOFF_SECONDS": str(int(MAX_BACKOFF_SECONDS)),
        "BOUNDARY_MAX_ATTEMPTS": str(MAX_ATTEMPTS),
    }


def test_a_retry_that_succeeded_says_nothing_and_a_failure_says_everything(
    tmp_path: Path,
) -> None:
    """A recovery that worked is not news; one that did not is the whole story.

    Two alarming lines on a planner's terminal for an outcome nothing needs to act
    on is exactly the noise that makes real diagnostics get skimmed. The durable
    record is the attempts log either way.
    """
    log = tmp_path / "boundary-attempts.jsonl"
    recovered = _retrying_wrapper(
        tmp_path,
        body=(
            'if [ "$count" -eq 1 ]; then echo "quota exhausted" >&2; exit 1; fi\n'
            "printf '{\"ok\":true}'\n"
        ),
        attempts_log=log,
    )

    assert recovered.returncode == 0, recovered.stderr
    assert "retried in" not in recovered.stderr, recovered.stderr
    # The child's own words still reach the operator; only the wrapper's narration
    # of a recovery that worked is withheld.
    assert "quota exhausted" in recovered.stderr
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1

    exhausted = _retrying_wrapper(
        tmp_path / "exhausted",
        body='echo "harness cannot start" >&2\nexit 2\n',
        attempts="2",
    )

    assert exhausted.returncode == 2
    assert "retried in" in exhausted.stderr, exhausted.stderr


@pytest.mark.parametrize("configured", ["", ".", "-1", "0", "not-a-number", "1e9"])
def test_an_unusable_backoff_falls_back_rather_than_disabling_the_recovery(
    tmp_path: Path, configured: str
) -> None:
    """Both settings arrive from the environment, so every shape reaches the sleep.

    A value this wrapper could not use would otherwise reach `sleep` directly: `.`
    and `not-a-number` fail it, `-1` and `0` remove the wait the backoff exists for,
    and `1e9` is a run asleep past any planner's patience. The recovery still has to
    happen, so an unusable value takes the default rather than the turn.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "oneharness"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'count=$(( $(cat "$STUB_COUNT" 2>/dev/null || echo 0) + 1 ))\n'
        'printf \'%s\' "$count" > "$STUB_COUNT"\n'
        'if [ "$count" -eq 1 ]; then exit 1; fi\n'
        "printf '{\"ok\":true}'\n",
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    started = time.monotonic()
    proc = subprocess.run(
        ["bash", str(WRAPPER), "run", "--prompt", "probe"],
        text=True,
        capture_output=True,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(tmp_path / "home"),
            "STUB_COUNT": str(tmp_path / "stub-count"),
            "ORCHESTRATOR_BOUNDARY_ATTEMPTS": "2",
            "ORCHESTRATOR_BOUNDARY_BACKOFF_SECONDS": configured,
        },
    )
    elapsed = time.monotonic() - started

    assert proc.returncode == 0, proc.stderr
    assert _stub_invocations(tmp_path) == 2
    # The default wait, not the unusable one: long enough to prove a wait happened,
    # far short of the ceiling a `1e9` would otherwise have asked for.
    assert DEFAULT_BACKOFF_SECONDS <= elapsed < MAX_BACKOFF_SECONDS
