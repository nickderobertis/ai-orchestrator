"""Regression test for the agent-side oneharness wrapper (scripts/oneharness-agent.sh).

onejudge runs BOTH the agent turn and the judge / simulated-user turn through the
same ``provider.bin``. The wrapper forces the orchestrator's agent config, but the
judge side already passes its own ``--config <judge_config>``; the wrapper must not
add a second one (oneharness rejects a duplicate ``--config``). These tests drive
the real script with a stub ``oneharness`` on PATH — only the downstream binary is
faked, mirroring how the e2e suite fakes just the paid harness.
"""

from __future__ import annotations

import json
import re
import shlex
import stat
import subprocess
import time
import tomllib
from collections.abc import Mapping
from pathlib import Path

import pytest
from process_tree import consumed_cpu_seconds

from orchestrator import REPO_ROOT
from orchestrator.dispatch import AGENT_STATUS_NAMES, agent_failure_reason
from orchestrator.harnesses import JUDGE_HARNESS_ENV, WORKER_HARNESS_ENV
from orchestrator.labels import LABEL_ENV, format_labels, parse_labels

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
    env: Mapping[str, str] | None = None,
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
        'printf \'%s\\n\' "${ORCHESTRATOR_CODEX_ALT_HOME-}" > "$ONEHARNESS_CODEX_ENV_FILE"\n'
        # The labels oneharness would stamp on the session this turn becomes, read
        # where oneharness reads them: the environment of the process it is spawned
        # in. Derived from the argv path so every existing caller records them too.
        'printf \'%s\\n\' "${ONEHARNESS_HISTORY_LABELS-}" > "$ONEHARNESS_ARGS_FILE.labels"\n',
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
            **(env or {}),
        },
    )
    recorded = args_file.read_text(encoding="utf-8").splitlines() if args_file.exists() else []
    return proc, recorded


def _selection(tmp_path: Path) -> str:
    return (tmp_path / "oneharness-selection").read_text(encoding="utf-8").strip()


def _raw_labels(tmp_path: Path) -> str:
    """The exact ONEHARNESS_HISTORY_LABELS value the spawned oneharness was given."""
    return (tmp_path / "oneharness-argv.labels").read_text(encoding="utf-8").strip()


def _stamped_labels(tmp_path: Path) -> dict[str, str]:
    """The history labels the spawned oneharness would have recorded, parsed."""
    raw = _raw_labels(tmp_path)
    return parse_labels(raw) if raw else {}


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


def _judge_argv(
    tmp_path: Path, chain: tuple[str, ...] = ("codex", "claude-code:primary")
) -> list[str]:
    """The argv shape onejudge gives the judge turn: its own --config, chosen already."""
    judge_config = tmp_path / "oneharness.judge.toml"
    rendered = ", ".join(f'"{identity}"' for identity in chain)
    judge_config.write_text(f"harnesses = [{rendered}]\n", encoding="utf-8")
    return ["run", "--compact", "--prompt-file", "-", "--config", str(judge_config)]


def test_agent_side_runs_the_worker_selection_it_was_given(tmp_path: Path) -> None:
    """An explicit worker choice reaches oneharness verbatim.

    Verbatim matters: the absent-alternate substitution narrows a chain nobody
    chose, but silently dropping an identity an operator named would run a
    provider they did not ask for.
    """
    proc, _ = _run_wrapper(
        tmp_path,
        ["run", "--compact", "--prompt", "probe"],
        env={WORKER_HARNESS_ENV: "claude-code:alternate2,codex"},
    )

    assert proc.returncode == 0, proc.stderr
    assert _selection(tmp_path) == "claude-code:alternate2,codex"


def test_judge_side_runs_the_judge_selection_it_was_given(tmp_path: Path) -> None:
    proc, argv = _run_wrapper(
        tmp_path, _judge_argv(tmp_path), env={JUDGE_HARNESS_ENV: "claude-code:primary"}
    )

    assert proc.returncode == 0, proc.stderr
    assert _selection(tmp_path) == "claude-code:primary"
    # Still exactly the caller's own config: choosing a side's harness must not
    # change which config that side is judged from.
    assert argv.count("--config") == 1
    assert f"{REPO_ROOT}/oneharness.toml" not in argv


def test_neither_side_leaks_its_selection_into_the_other(tmp_path: Path) -> None:
    """The bug this seam exists for: one value moving both sides at once."""
    both = {WORKER_HARNESS_ENV: "codex", JUDGE_HARNESS_ENV: "claude-code:primary"}

    agent, _ = _run_wrapper(tmp_path, ["run", "--compact", "--prompt", "probe"], env=both)
    assert agent.returncode == 0, agent.stderr
    assert _selection(tmp_path) == "codex"

    judge, _ = _run_wrapper(tmp_path, _judge_argv(tmp_path), env=both)
    assert judge.returncode == 0, judge.stderr
    assert _selection(tmp_path) == "claude-code:primary"


def test_a_side_with_its_own_selection_ignores_a_process_wide_one(tmp_path: Path) -> None:
    """`ONEHARNESS_HARNESSES` is process-wide and beats config; a per-side value beats it."""
    ambient = {"ONEHARNESS_HARNESSES": "codex:alternate"}

    agent, _ = _run_wrapper(
        tmp_path,
        ["run", "--compact", "--prompt", "probe"],
        env={**ambient, WORKER_HARNESS_ENV: "codex"},
    )
    assert agent.returncode == 0, agent.stderr
    assert _selection(tmp_path) == "codex"

    judge, _ = _run_wrapper(
        tmp_path, _judge_argv(tmp_path), env={**ambient, JUDGE_HARNESS_ENV: "claude-code:primary"}
    )
    assert judge.returncode == 0, judge.stderr
    assert _selection(tmp_path) == "claude-code:primary"


def test_a_side_without_its_own_selection_resolves_exactly_as_before(tmp_path: Path) -> None:
    """Overriding one side must leave the other side's default path untouched."""
    # The agent branch still substitutes a chain without the absent alternates...
    agent, _ = _run_wrapper(
        tmp_path,
        ["run", "--compact", "--prompt", "probe"],
        env={JUDGE_HARNESS_ENV: "claude-code:primary"},
    )
    assert agent.returncode == 0, agent.stderr
    assert _selection(tmp_path) == "codex,codex:alternate,claude-code:primary"

    # ...and the judge branch still leaves the selection to its own config.
    judge, _ = _run_wrapper(tmp_path, _judge_argv(tmp_path), env={WORKER_HARNESS_ENV: "codex"})
    assert judge.returncode == 0, judge.stderr
    assert _selection(tmp_path) == ""


def test_a_selection_the_side_cannot_honor_stops_the_turn_here(tmp_path: Path) -> None:
    """The variables are the boundary a hand-set value arrives at, so check them.

    The dispatch layer validates the same way before anything starts, but nothing
    stops a caller exporting one of these directly — and oneharness would take an
    identity its config does not configure and run something else for it.
    """
    agent, argv = _run_wrapper(
        tmp_path,
        ["run", "--compact", "--prompt", "probe"],
        env={WORKER_HARNESS_ENV: "codex,opencode"},
    )
    assert agent.returncode == 2
    assert "'opencode' is not a harness" in agent.stderr
    assert f"{REPO_ROOT}/oneharness.toml configures" in agent.stderr
    # The chain it could have named, so the value is correctable from the message.
    assert "claude-code:alternate" in agent.stderr
    assert argv == []

    judge, argv = _run_wrapper(
        tmp_path, _judge_argv(tmp_path), env={JUDGE_HARNESS_ENV: "claude-code:alternate2"}
    )
    assert judge.returncode == 2
    # Judged against the caller's own config, which is the one this turn would run
    # from — not the agent chain, which does name that identity.
    assert "'claude-code:alternate2' is not a harness" in judge.stderr
    assert "select from codex claude-code:primary" in judge.stderr
    assert argv == []


@pytest.mark.parametrize(
    "rendered",
    [
        'harnesses = ["codex", "claude-code:primary"]',
        # The committed shape: one identity per line, with a trailing comma.
        'harnesses = [\n    "codex",\n    "claude-code:primary",\n]',
        # TOML's other quote style, which a regenerated config could well use.
        "harnesses = ['codex', 'claude-code:primary']",
        'harnesses  =  [ "codex","claude-code:primary" ]\nhistory = true\n',
        # A comment naming an identity in quotes — text a file scanner would take
        # for a configured one, authorizing a selection the config never made.
        'harnesses = ["codex", "claude-code:primary"] # not "opencode"\n',
        # ...and the same trap on the line the chain opens on.
        'harnesses = [ # never "opencode"\n    "codex",\n    "claude-code:primary",\n]',
    ],
)
def test_the_wrappers_chain_reader_agrees_with_the_toml_parser(
    tmp_path: Path, rendered: str
) -> None:
    """One contract, two readers, held to the same answer.

    `orchestrator.harnesses` validates a selection with tomllib before dispatch and
    this wrapper validates it again at the variable's own boundary. Both read the
    key with tomllib for exactly this reason, and these shapes are the gate on that
    staying true: a scanner reintroduced here would understand a narrower TOML than
    the parser does — refusing a value the dispatch layer accepted, or accepting an
    identity that appears in the file only inside a comment.
    """
    config = tmp_path / "oneharness.judge.toml"
    config.write_text(rendered, encoding="utf-8")
    chain = tomllib.loads(rendered)["harnesses"]
    argv = ["run", "--compact", "--prompt-file", "-", "--config", str(config)]

    accepted, _ = _run_wrapper(tmp_path, argv, env={JUDGE_HARNESS_ENV: ",".join(chain)})
    assert accepted.returncode == 0, accepted.stderr
    assert _selection(tmp_path) == ",".join(chain)

    # Clear what the accepted run recorded, so "nothing reached oneharness" below is
    # about the refused run rather than about a file that was never rewritten.
    (tmp_path / "oneharness-argv").unlink()
    refused, recorded = _run_wrapper(tmp_path, argv, env={JUDGE_HARNESS_ENV: "codex:alternate"})
    assert refused.returncode == 2
    assert "is not a harness" in refused.stderr
    assert recorded == []


def test_a_malformed_chain_is_refused_rather_than_filtered_to_its_usable_members(
    tmp_path: Path,
) -> None:
    """Half of a broken declaration is not the selection the file meant to make."""
    config = tmp_path / "oneharness.judge.toml"
    config.write_text('harnesses = ["codex", 3]\n', encoding="utf-8")

    proc, argv = _run_wrapper(
        tmp_path,
        ["run", "--compact", "--prompt-file", "-", "--config", str(config)],
        env={JUDGE_HARNESS_ENV: "codex"},
    )

    assert proc.returncode == 2
    assert "malformed harnesses chain" in proc.stderr
    assert argv == []


def test_a_config_without_a_chain_cannot_honor_a_selection_either(tmp_path: Path) -> None:
    proc, argv = _run_wrapper(
        tmp_path,
        _judge_argv(tmp_path, chain=()),
        env={JUDGE_HARNESS_ENV: "codex"},
    )

    assert proc.returncode == 2
    assert "declares no 'harnesses' chain" in proc.stderr
    assert argv == []


def test_an_ambient_selection_still_reaches_a_side_that_was_given_none(tmp_path: Path) -> None:
    """Nothing about oneharness's own override changes for a side nobody chose for."""
    agent, _ = _run_wrapper(
        tmp_path,
        ["run", "--compact", "--prompt", "probe"],
        env={"ONEHARNESS_HARNESSES": "codex:alternate"},
    )
    assert agent.returncode == 0, agent.stderr
    assert _selection(tmp_path) == "codex:alternate"

    judge, _ = _run_wrapper(
        tmp_path, _judge_argv(tmp_path), env={"ONEHARNESS_HARNESSES": "codex:alternate"}
    )
    assert judge.returncode == 0, judge.stderr
    assert _selection(tmp_path) == "codex:alternate"


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


def test_the_judge_side_is_not_stamped_with_the_worker_semantic_role(tmp_path: Path) -> None:
    """Only the judge side drops `agent_role`, and it drops nothing else.

    A dispatch exports one `ONEHARNESS_HISTORY_LABELS` for the whole conversation,
    and oneharness merges labels CLI > env > config — so the worker's `agent_role`
    outranked oneharness.judge.toml's own `agent_role = "judge"` and every supervisor
    session was recorded as a worker. This is the boundary that knows which side it
    is, so it is the boundary that must not stamp one side with the other's role.
    """
    judge_config = tmp_path / "oneharness.judge.toml"
    judge_config.write_text('harnesses = ["codex"]\n', encoding="utf-8")
    dispatched = {LABEL_ENV: "run_id=demo,node=api,step=main,agent_role=worker,persona=engineer"}

    judge, _ = _run_wrapper(tmp_path, ["run", "--config", str(judge_config)], env=dispatched)
    assert judge.returncode == 0, judge.stderr
    assert _stamped_labels(tmp_path) == {
        "run_id": "demo",
        "node": "api",
        "step": "main",
        "persona": "engineer",
    }

    agent, _ = _run_wrapper(tmp_path, ["run", "--compact", "--prompt", "work"], env=dispatched)
    assert agent.returncode == 0, agent.stderr
    assert _stamped_labels(tmp_path)["agent_role"] == "worker"


def test_the_judge_side_rewrite_hands_oneharness_only_contract_shaped_labels(
    tmp_path: Path,
) -> None:
    """What this branch rewrites is checked against the same contract Python writes.

    The inherited value comes from whatever invoked the dispatch, so a pair that
    violates oneharness's label contract must not survive a rewrite this side made —
    it would turn a value oneharness refuses into one this wrapper produced. The
    Python parser is the authority on which pairs those are, so the two are compared
    rather than the shell's answer being restated here.
    """
    judge_config = tmp_path / "oneharness.judge.toml"
    judge_config.write_text('harnesses = ["codex"]\n', encoding="utf-8")
    inherited = (
        "run_id=demo,agent_role=worker,-leading=hyphen,empty=,"
        "no-separator,node=api,over" + "long" * 80 + "=value"
    )

    judge, _ = _run_wrapper(
        tmp_path, ["run", "--config", str(judge_config)], env={LABEL_ENV: inherited}
    )

    assert judge.returncode == 0, judge.stderr
    expected = parse_labels(inherited)
    expected.pop("agent_role")
    assert expected == {"run_id": "demo", "node": "api"}
    # The exported *string*, not what a lenient reader can still parse out of it:
    # dropping a malformed pair is the whole claim, and every reader here drops one.
    assert _raw_labels(tmp_path) == format_labels(expected)


def test_the_judge_side_leaves_a_dispatch_with_no_labels_unlabelled(tmp_path: Path) -> None:
    """An empty label list is not a value oneharness accepts, so none is exported."""
    judge_config = tmp_path / "oneharness.judge.toml"
    judge_config.write_text('harnesses = ["codex"]\n', encoding="utf-8")

    for labels in ({}, {LABEL_ENV: "agent_role=worker"}):
        judge, _ = _run_wrapper(tmp_path, ["run", "--config", str(judge_config)], env=labels)
        assert judge.returncode == 0, judge.stderr
        assert _raw_labels(tmp_path) == ""


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


#: Long enough that a core pinned at 100% is unmistakable against the threshold
#: below, and short enough to keep this a couple of seconds of the suite. This is a
#: measurement window rather than a rendezvous: what is being observed is a rate.
_PARKED_SAMPLE_SECONDS = 1.5
#: A parked wrapper wakes once an hour. Anything approaching this is a spin.
_PARKED_CPU_BUDGET_SECONDS = 0.1


def test_a_wrapper_awaiting_recovery_consumes_no_cpu_while_it_waits(tmp_path: Path) -> None:
    """Staying alive for the dispatcher must not cost a core for the whole window.

    The contract is unchanged — the wrapper writes its markers and then stays in the
    tree so the dispatcher can observe and recover it. What it must not do is spend
    that window in an empty loop: this host runs every workstream's dispatches on the
    same cores, and a failed turn used to hold one of them at 100% until the tree came
    down.
    """
    status_dir = tmp_path / "orchestrator-watchdog-parked" / "agent"
    status_dir.mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "oneharness"
    stub.write_text("#!/usr/bin/env bash\necho 'harness refused' >&2\nexit 7\n", encoding="utf-8")
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
            while not (status_dir / "agent.failed").exists():
                assert process.poll() is None, "the wrapper exited instead of awaiting recovery"
                assert time.monotonic() < deadline, "the wrapper never recorded the failed turn"
                time.sleep(0.02)
            parked = consumed_cpu_seconds(process.pid)
            time.sleep(_PARKED_SAMPLE_SECONDS)
            burned = consumed_cpu_seconds(process.pid) - parked
            still_waiting = process.poll() is None
            recorded = {
                name: (status_dir / name).read_text(encoding="utf-8").strip()
                for name in ("agent.failed", "agent.failure", "agent.exit_code")
            }
        finally:
            process.kill()

    assert still_waiting, "the wrapper left before the dispatcher could recover it"
    assert burned < _PARKED_CPU_BUDGET_SECONDS, (
        f"the parked wrapper burned {burned:.2f}s of CPU in {_PARKED_SAMPLE_SECONDS:g}s of waiting"
    )
    assert recorded["agent.exit_code"] == "7"
    assert recorded["agent.failure"] == "agent harness exited 7"
    assert recorded["agent.failed"].isdigit()
    assert not (status_dir / "agent.done").exists()


#: What the parked wrapper asks for in one nap, so a nap that came from the park is
#: told apart from the 0.5s ones its pre-park poll asks for through the same stub.
_PARKED_NAP_SECONDS = 3600
#: What the stub waits instead, compressing an hour by a factor of 360000 while
#: still costing a fork. Returning outright compressed it further and turned this
#: test into the busy loop the wrapper was fixed to stop being: a few thousand
#: forks a second, on a host whose every other dispatch wants the same cores.
_STUBBED_NAP_SECONDS = 0.01
#: Naps watched, each one a re-entry the wrapper had no obligation to make.
_PARKED_NAPS_WATCHED = 25
#: Real time watched alongside them, which the stub's compressed clock cannot supply.
_PARKED_CLOCK_WINDOW_SECONDS = 1.5


def test_a_parked_wrapper_stays_parked_across_repeated_naps_and_a_clock_window(
    tmp_path: Path,
) -> None:
    """The real script, driven end to end, parks and keeps re-entering its wait.

    A single long nap costs no CPU either, so the CPU measurement above cannot tell
    it from a loop of them, and a wrapper that fell out of its wait would hand the
    dispatcher a turn reporting success markers it never wrote. Stubbing `sleep` to
    return almost at once makes each next iteration observable but compresses the
    clock, so a bound on how many times it sleeps and a bound on the clock surface
    differently: the first as the wrapper leaving mid-nap, the second only by being
    outlasted in real time. Both are watched, each reaching the bounds below its own
    size — the sizes are small deliberately, because the test below rules out a
    bound of any size by reading the loop, and this one need only prove the shape it
    reads is what the wrapper really does.
    """
    status_dir = tmp_path / "orchestrator-watchdog-parked-loop" / "agent"
    status_dir.mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "oneharness"
    stub.write_text("#!/usr/bin/env bash\nexit 7\n", encoding="utf-8")
    # This process is the only one that runs these stubs, so user execute is the
    # whole grant they need.
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    naps = tmp_path / "naps"
    sleep_stub = bin_dir / "sleep"
    # Recorded before waiting, so a nap counts the moment the wrapper asks for it.
    # `command -p` reaches the real sleep rather than this stub, which is first on
    # the wrapper's PATH and would otherwise call itself.
    sleep_stub.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$1" >>{shlex.quote(str(naps))}\n'
        f"command -p sleep {_STUBBED_NAP_SECONDS}\n",
        encoding="utf-8",
    )
    sleep_stub.chmod(sleep_stub.stat().st_mode | stat.S_IXUSR)

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
            patience = time.monotonic() + 30
            parked_naps: list[str] = []
            parked_since: float | None = None
            while True:
                parked_for = 0.0 if parked_since is None else time.monotonic() - parked_since
                assert process.poll() is None, (
                    f"the wrapper left its wait after {len(parked_naps)} naps and "
                    f"{parked_for:.1f}s parked, instead of awaiting recovery; the parked "
                    "wait has an exit condition of its own"
                )
                assert time.monotonic() < patience, (
                    f"the parked wrapper napped only {len(parked_naps)} of "
                    f"{_PARKED_NAPS_WATCHED} times in 30s; it is "
                    "not re-entering its wait"
                )
                time.sleep(0.02)
                recorded = naps.read_text(encoding="utf-8") if naps.exists() else ""
                parked_naps = [
                    line for line in recorded.splitlines() if line == str(_PARKED_NAP_SECONDS)
                ]
                if parked_since is None:
                    if not parked_naps:
                        continue
                    parked_since = time.monotonic()
                elif (
                    len(parked_naps) >= _PARKED_NAPS_WATCHED
                    and parked_for >= _PARKED_CLOCK_WINDOW_SECONDS
                ):
                    break
            still_waiting = process.poll() is None
        finally:
            process.kill()

    assert still_waiting, "the wrapper left its wait once the observations were over"
    assert (status_dir / "agent.failed").exists()
    assert not (status_dir / "agent.done").exists()


#: The loop headers that cannot decide to stop. Anything else — `while [ ... ]`,
#: `until`, `for` over a finite list — is a termination condition by construction.
_UNCONDITIONAL_LOOP_HEADERS = ("while :; do", "while true; do")


def _parked_wait_source() -> list[str]:
    """What the wrapper runs after recording a failed turn: code lines, no comments.

    The failure branch's last act is the park, so the block is everything between
    the marker write and the branch's own `fi`. Both anchors are unique and are
    asserted to be, because a scan that silently matched nothing would pass.
    """
    lines = WRAPPER.read_text(encoding="utf-8").splitlines()
    marks = [
        i for i, line in enumerate(lines) if line.strip().startswith("write_status agent.failed")
    ]
    assert len(marks) == 1, f"expected one agent.failed write to anchor the park, got {len(marks)}"
    closes = [i for i in range(marks[0], len(lines)) if lines[i] == "fi"]
    assert closes, "the failure branch that parks the wrapper is never closed"
    return [
        stripped
        for line in lines[marks[0] + 1 : closes[0]]
        if (stripped := line.strip()) and not stripped.startswith("#")
    ]


def test_the_parked_wait_is_written_as_a_loop_with_no_termination_condition() -> None:
    """No bound at all, which is the half no finite observation can reach.

    The behavioural test above outlasts a park that counts its naps or watches the
    clock, but only up to its own sizes: an hour-long deadline still passes it, and
    waiting one out is not something a suite can do. So the loop is also read. The
    shape required is the narrowest one that cannot stop on its own — an
    unconditional header, one `sleep` inside it, and nothing after it in the branch
    — which is what rejects a clock check, a `timeout`, a counter, or a `break`
    without needing to enumerate them.

    This is a source assertion, so it constrains how the park is written and not
    only what it does: a legitimate rewrite into another shape fails here and should
    update this test alongside it.
    """
    # llmlint: ignore[tests_assert_real_behavior] The property is the absence of a
    # termination condition at any size, and no finite observation reaches an
    # hour-long bound — the loop's own source is the only evidence there is. The
    # behavioural test above asserts everything about this park that can be observed.
    block = _parked_wait_source()

    assert block[0] in _UNCONDITIONAL_LOOP_HEADERS, (
        f"the parked wait opens with {block[0]!r}; it must be one of "
        f"{list(_UNCONDITIONAL_LOOP_HEADERS)}, because every other header is a "
        "condition the wait can stop on"
    )
    assert block[-1] == "done", (
        f"the parked wait ends with {block[-1]!r} rather than closing its loop; "
        "nothing may follow the park in the failure branch, or the wrapper has a "
        "way back out to the turn's own exit"
    )
    body = block[1:-1]
    assert len(body) == 1 and re.fullmatch(r"sleep \S+", body[0]), (
        f"the parked wait's body is {body}; it must be a single sleep, so that no "
        "clock check, counter, or break can end the wait the dispatcher is meant to"
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
        # The wrapper asks whether this invocation can stream before it runs the
        # turn, and the real CLI answers `--print-command` by rendering what it
        # would spawn and spawning nothing. A stub that ran its whole body for that
        # question would model a binary oneharness is not.
        'for arg in "$@"; do [ "$arg" = "--print-command" ] && exit 0; done\n'
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


#: The one report shape onejudge accepts: a single bare JSON document on stdout.
#: `oneharness run --stream` writes something else entirely, so these tests pin what
#: the wrapper hands onejudge in each of the three selections below.
_REPORT = '{"schema_version":"0.3","results":[{"harness_id":"claude-code","status":"ok"}]}'
_STREAMED = (
    '{"type":"event","event":{"kind":"tool_call","name":"Bash",'
    '"input":{"command":"just check"}}}\n'
    '{"type":"result","report":' + _REPORT + "}"
)


def _stream_capable_stub(bin_dir: Path, *, streamable: bool) -> Path:
    """A stub oneharness that answers the capability probe and then the run.

    ``--print-command`` is the wrapper's probe: the real CLI renders what it would
    spawn, validates `--stream` against the config and this invocation's flags, and
    spawns nothing. ``streamable`` is that answer. The run itself then emits whichever
    shape the flags it was actually given call for, exactly as the real CLI does.
    """
    stub = bin_dir / "oneharness"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$ONEHARNESS_ARGS_FILE"\n'
        "probe=false; streaming=false\n"
        'for arg in "$@"; do\n'
        '    [ "$arg" = "--print-command" ] && probe=true\n'
        '    [ "$arg" = "--stream" ] && streaming=true\n'
        "done\n"
        f"[ {str(streamable).lower()} = false ] && [ $streaming = true ] && exit 2\n"
        "[ $probe = true ] && exit 0\n"
        f'if [ $streaming = true ]; then printf "%s\\n" {shlex.quote(_STREAMED)};\n'
        f'else printf "%s\\n" {shlex.quote(_REPORT)}; fi\n',
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return stub


def _run_streaming_wrapper(
    tmp_path: Path, argv: list[str], *, streamable: bool = True, name: str = "stream"
) -> tuple[subprocess.CompletedProcess[str], list[str], Path]:
    """Drive the wrapper's dispatched path; return (proc, every recorded argv, status dir)."""
    status_dir = tmp_path / f"orchestrator-watchdog-{name}" / "agent"
    status_dir.mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _stream_capable_stub(bin_dir, streamable=streamable)
    args_file = tmp_path / f"argv-{name}"
    proc = subprocess.run(
        ["bash", str(WRAPPER), *argv],
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(tmp_path / "home"),
            "ONEHARNESS_ARGS_FILE": str(args_file),
            "ORCHESTRATOR_AGENT_STATUS_DIR": str(status_dir),
        },
    )
    recorded = args_file.read_text(encoding="utf-8").splitlines() if args_file.exists() else []
    return proc, recorded, status_dir


def test_a_dispatched_turn_streams_and_still_hands_onejudge_one_report(tmp_path: Path) -> None:
    """The whole point, and the constraint it had to be reconciled with.

    `--events` only guarantees the transcript is in the report at the *end* of a
    turn, so a node was invisible for the 600-2000 seconds one takes here. `--stream`
    delivers the same normalized events as they occur — but onejudge parses this
    process's stdout as exactly one JSON document, so the stream cannot simply be
    handed through.
    """
    proc, recorded, status_dir = _run_streaming_wrapper(
        tmp_path, ["run", "--compact", "--events", "--prompt", "probe"]
    )
    assert proc.returncode == 0, proc.stderr

    probe, run = recorded
    # The probe asks about this exact invocation rather than a simplified stand-in.
    assert "--print-command" in probe and "--stream" in probe and "--compact" in probe
    # oneharness refuses a repeated flag, so each appears exactly once on the run.
    assert run.split().count("--stream") == 1
    assert run.split().count("--events") == 1

    assert proc.stdout.strip() == _REPORT
    # The raw record keeps every line the child wrote, as `tee` did before.
    assert (status_dir / "agent.stdout").read_text(encoding="utf-8").strip() == _STREAMED
    published = json.loads((status_dir / "agent.activity").read_text(encoding="utf-8"))
    assert (published["name"], published["detail"], published["events"]) == (
        "Bash",
        "just check",
        1,
    )


def test_an_invocation_that_cannot_stream_falls_back_to_events(tmp_path: Path) -> None:
    """Degrading is never a dispatch failure, and never costs the transcript.

    The probe is one question — can *this* config's chain, with *these* flags, be
    streamed — and an older CLI that does not know `--stream` at all answers it the
    same way a mode outside the supported set does.
    """
    proc, recorded, status_dir = _run_streaming_wrapper(
        tmp_path, ["run", "--compact", "--prompt", "probe"], streamable=False, name="degraded"
    )
    assert proc.returncode == 0, proc.stderr

    probe, run = recorded
    assert "--stream" in probe
    assert "--stream" not in run.split()
    # `--events` is the degrade path: the transcript still reaches the report.
    assert run.split().count("--events") == 1
    assert proc.stdout.strip() == _REPORT
    assert not (status_dir / "agent.activity").exists()


def test_a_caller_that_streams_already_keeps_its_own_stream(tmp_path: Path) -> None:
    """Nothing is added to, or rewritten for, a caller that asked for the stream.

    oneharness refuses a repeated `--stream`, and a consumer that asked for events as
    they occur is not asking for them to be buffered back into one report.
    """
    proc, recorded, status_dir = _run_streaming_wrapper(
        tmp_path, ["run", "--compact", "--stream", "--prompt", "probe"], name="caller"
    )
    assert proc.returncode == 0, proc.stderr

    # No probe: there is nothing to decide.
    assert len(recorded) == 1
    assert recorded[0].split().count("--stream") == 1
    assert proc.stdout.strip() == _STREAMED
    assert not (status_dir / "agent.activity").exists()


def test_an_undispatched_turn_keeps_events_because_nothing_is_watching(tmp_path: Path) -> None:
    """No status directory means no dispatch, so a stream would have no consumer."""
    proc, recorded = _run_wrapper(tmp_path, ["run", "--compact", "--prompt", "probe"])

    assert proc.returncode == 0, proc.stderr
    assert "--stream" not in recorded
    assert recorded.count("--events") == 1


def test_a_stream_capture_that_refuses_its_paths_records_its_own_reason(tmp_path: Path) -> None:
    """The reader's account of a failed capture has to survive the process tree.

    A capture that stops working must never read as "the harness said nothing", and
    the wrapper's failure line says where the reason is — so the reader's stderr goes
    into that same durable record rather than onto a wrapper stderr that vanishes
    with the tree the dispatcher tears down.

    The refusal itself is the filter resolving its paths rather than reading their
    spelling: this status directory has the shape the wrapper requires, and resolves
    somewhere with no dispatch behind it at all.
    """
    actual = tmp_path / "elsewhere" / "agent"
    actual.mkdir(parents=True)
    (tmp_path / "orchestrator-watchdog-sneaky").symlink_to(tmp_path / "elsewhere")
    status_dir = tmp_path / "orchestrator-watchdog-sneaky" / "agent"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _stream_capable_stub(bin_dir, streamable=True)

    with subprocess.Popen(
        ["bash", str(WRAPPER), "run", "--compact", "--prompt", "probe"],
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(tmp_path / "home"),
            "ONEHARNESS_ARGS_FILE": str(tmp_path / "argv-sneaky"),
            "ORCHESTRATOR_AGENT_STATUS_DIR": str(status_dir),
        },
    ) as process:
        try:
            limit = time.monotonic() + 30
            while time.monotonic() < limit and not (status_dir / "agent.failed").exists():
                time.sleep(0.05)
            assert (status_dir / "agent.failed").exists(), "the wrapper never recorded the failure"
            recorded = (status_dir / "agent.stderr").read_text(encoding="utf-8")
        finally:
            process.kill()

    # The reader's own reason, in the record the wrapper's failure line points at.
    assert "in one dispatch status directory" in recorded
    assert "agent stdout capture failed" in recorded
    assert str(status_dir / "agent.stderr") in recorded


def test_a_streamed_turn_forwards_output_the_filter_cannot_read(tmp_path: Path) -> None:
    """A turn survives output this build does not model, and onejudge still gets one.

    The stream filter sits between oneharness and onejudge on the one channel a
    turn's answer travels, and onejudge parses that channel as a *single* JSON
    document — so passing an unmodelled line along would fail a turn that otherwise
    succeeded, which is the failure this whole arrangement exists to avoid. A later
    protocol's envelope, an event envelope carrying no event, and a harness that
    printed over the protocol are all kept in the raw record and kept out of the
    document.
    """
    unmodelled = (
        '{"type":"notice","message":"a shape from a later protocol"}',
        '{"type":"event"}',
        "oneharness: warning: something happened",
        '{"type":"event","event":{"kind":"tool_call","name":"Bash",'
        '"input":{"command":"just check"}}}',
        '{"type":"result","report":' + _REPORT + "}",
    )
    status_dir = tmp_path / "orchestrator-watchdog-mixed" / "agent"
    status_dir.mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = _stream_capable_stub(bin_dir, streamable=True)
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'for arg in "$@"; do [ "$arg" = "--print-command" ] && exit 0; done\n'
        f"printf '%s\\n' {' '.join(shlex.quote(line) for line in unmodelled)}\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(WRAPPER), "run", "--compact", "--prompt", "probe"],
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(tmp_path / "home"),
            "ORCHESTRATOR_AGENT_STATUS_DIR": str(status_dir),
        },
    )

    assert proc.returncode == 0, proc.stderr
    # Exactly the report, and nothing in front of it: onejudge parses this as one
    # document, so a surviving notice or warning would be the thing that broke it.
    assert proc.stdout.strip().splitlines() == [_REPORT]
    assert json.loads(proc.stdout) == json.loads(_REPORT)
    # The raw record keeps every line the child wrote, recognized or not — nothing
    # was dropped, only kept out of a channel that could not carry it.
    assert (status_dir / "agent.stdout").read_text(encoding="utf-8").splitlines() == list(
        unmodelled
    )
    # And the one real event still published, counted as the only one.
    published = json.loads((status_dir / "agent.activity").read_text(encoding="utf-8"))
    assert published["events"] == 1
    assert published["detail"] == "just check"


def test_a_streamed_turn_still_carries_a_bare_report_through(tmp_path: Path) -> None:
    """The one unmodelled line that IS onejudge's document, and must reach it.

    A report with no stream discriminator is what a run that did not stream answers
    with. Dropping it alongside the noise would turn a turn that produced an answer
    into a turn that produced nothing — the degrade path failing exactly where it is
    supposed to hold.
    """
    status_dir = tmp_path / "orchestrator-watchdog-bare" / "agent"
    status_dir.mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = _stream_capable_stub(bin_dir, streamable=True)
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'for arg in "$@"; do [ "$arg" = "--print-command" ] && exit 0; done\n'
        f"printf '%s\\n' {shlex.quote(_REPORT)}\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(WRAPPER), "run", "--compact", "--prompt", "probe"],
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(tmp_path / "home"),
            "ORCHESTRATOR_AGENT_STATUS_DIR": str(status_dir),
        },
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == _REPORT
