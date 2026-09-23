"""The sixth identity reaches every entry point, and a host that never heard of it keeps working.

Every chain now names `claude-code:primary-backup` just before `claude-code:primary`, and
both read their `CLAUDE_CONFIG_DIR` through an indirection oneharness refuses to start
without. So three things have to hold at once, and each is driven here rather than read
off a config:

* the real `oneharness` still refuses loudly when one of the new indirections is missing,
  so a wrapper that forgot to export it fails at once instead of routing somewhere quiet;
* a host set up before this identity existed — exporting nothing, keeping no identities
  file, never logged into the primary-backup account — resolves every indirection at
  every entry point, and its primary-backup candidate falls through as `auth`;
* a host identities file the helper refuses stops every entry point before the binary it
  was about to run.

Only what a paid provider or a whole launch would spend is substituted: a recording
`claude`/`codex` stands in for the provider, and a recording `uv`/`oneharness` for the
binary an entry point hands off to where the journey is about what that entry point
exported rather than about the turn. Every script, helper and config is the real one,
under a real bash; the real `oneharness` CLI runs wherever a turn is the subject. The
draft-pr-body entry point is driven the same way by `tests/e2e/test_draft_pr_body_e2e.py`,
and session setup by `tests/test_session_setup.py`.

Every environment here is built from nothing but a `PATH`, a `HOME` this test owns, and
what each journey names, so `XDG_CONFIG_HOME` is always absent and a host's own
identities file never reaches a verdict.
"""

# llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] The reason is below.
# llmlint: ignore-file[shell_test_tiers_stay_split] The reason is below.
# llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] The reason is below.
# Nothing here is expensive: the paid providers and every launch are doubled, so each
# test's slowest phase measured 0.08s under `pytest --durations`, and the real
# `oneharness` CLI it drives is the one tests/e2e/test_oneharness_timeout_e2e.py already
# drives over the same configs in this tier, so a project of its own would isolate no cost.

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest
import short_state
from project_fixtures import project_from_plan
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: Every provider binary a chain can reach, each a refusal, first on `PATH` wherever a
#: real turn could otherwise reach somebody's subscription.
PAID_PROVIDER_GUARD = Path(__file__).resolve().parent / "no-paid-provider"

CLAUDE_INDIRECTIONS = (
    "ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR",
    "ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR",
    "ORCHESTRATOR_CLAUDE_PRIMARY_BACKUP_CONFIG_DIR",
    "ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR",
)
NEW_INDIRECTIONS = (
    "ORCHESTRATOR_CLAUDE_PRIMARY_BACKUP_CONFIG_DIR",
    "ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR",
)

#: The directory each indirection resolves to on a host that exports nothing.
DEFAULT_LEAVES = {
    "ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR": ".claude-alt",
    "ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR": ".claude-alt2",
    "ORCHESTRATOR_CLAUDE_PRIMARY_BACKUP_CONFIG_DIR": ".claude-primary-backup",
    "ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR": ".claude",
}

#: Every role config this repository ships.
ROLE_CONFIGS = tuple(sorted(path.name for path in REPO_ROOT.glob("oneharness*.toml")))

DISPATCH_ENVIRONMENT = REPO_ROOT / "scripts" / "dispatch-env.sh"
LLMLINT_WRAPPER = REPO_ROOT / "scripts" / "llmlint-oneharness.sh"

#: An envelope carrying a command, which is what the envelope validator judges.
ENVELOPE = json.dumps(
    {"version": 2, "commands": [{"op": "amend", "id": "work", "text": "Report."}]}
)

#: A binary that records the Claude-identity environment it was handed and answers. As a
#: provider it answers the way claude-code does; as `uv` or `oneharness` it answers an
#: empty object and exits 0, which ends every entry point's hand-off at once.
RECORDER = """#!/usr/bin/env python3
import json, os, sys

if {"--version", "-v"}.intersection(sys.argv[1:]):
    print("0.0.0 (identity recorder)")
    raise SystemExit(0)
name = os.path.basename(sys.argv[0])
seen = {
    key: value
    for key, value in os.environ.items()
    if key.startswith("ORCHESTRATOR_CLAUDE_") or key == "CLAUDE_CONFIG_DIR"
}
with open(os.environ["IDENTITY_RECORDINGS"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps({"binary": name, "argv": sys.argv[1:], "environment": seen}) + "\\n")
if name == "claude":
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                      "result": "recorded", "session_id": "identity-recorder"}))
else:
    print("{}")
"""

LOGIN_REFUSAL = """#!/usr/bin/env python3
import json, sys
if "--version" in sys.argv[1:]:
    print("1.0.0 (login-refusal)")
    raise SystemExit(0)
print(json.dumps({
    "type": "result", "subtype": "success", "is_error": True,
    "duration_ms": 429, "num_turns": 1,
    "result": "Not logged in · Please run /login",
    "session_id": "host-login-refusal", "total_cost_usd": 0,
    "usage": {"input_tokens": 0, "output_tokens": 0}, "modelUsage": {},
}))
raise SystemExit(1)
"""


class Host(NamedTuple):
    """A host set up before the primary-backup identity existed."""

    home: Path
    environment: dict[str, str]
    recordings: Path


def _recorders(directory: Path, *names: str) -> Path:
    """A `PATH` front holding one recorder per name."""
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        binary = directory / name
        binary.write_text(RECORDER, encoding="utf-8")
        binary.chmod(0o755)
    return directory


def _existing_host(
    tmp_path: Path,
    *front: Path,
    logged_in: tuple[str, ...] = (".claude-alt", ".claude-alt2", ".claude"),
) -> Host:
    """A `HOME` holding the logged-in directories named, and an environment exporting none.

    Built from nothing, so none of the four indirections, no `XDG_CONFIG_HOME`, and no
    `ONEHARNESS_*` override the suite's own process carries can reach the entry point.
    """
    home = tmp_path / "home"
    home.mkdir()
    for leaf in logged_in:
        (home / leaf).mkdir()
    recordings = tmp_path / "recordings.jsonl"
    search = [*map(str, front), str(REPO_ROOT / ".venv" / "bin"), os.environ["PATH"]]
    environment = {
        "PATH": os.pathsep.join(search),
        "HOME": str(home),
        "IDENTITY_RECORDINGS": str(recordings),
        # What the engine hands a dispatch; this host's scratch rather than the suite's.
        "ONEPIPELINE_NODE_SCRATCH_DIR": str(tmp_path / "node-scratch"),
        # Keeps each journey's harness history out of the host's.
        "XDG_STATE_HOME": str(short_state.state_home(tmp_path)),
        "ONEHARNESS_HISTORY": "false",
    }
    (tmp_path / "node-scratch").mkdir()
    return Host(home, environment, recordings)


def _recorded(host: Host) -> list[dict[str, object]]:
    if not host.recordings.exists():
        return []
    return [json.loads(line) for line in host.recordings.read_text(encoding="utf-8").splitlines()]


def _run(
    command: list[str], host: Host, *, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=host.environment,
        input=stdin,
        stdin=None if stdin is not None else subprocess.DEVNULL,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )


@pytest.mark.parametrize("config", ROLE_CONFIGS)
@pytest.mark.parametrize("missing", NEW_INDIRECTIONS)
def test_the_real_cli_refuses_a_run_whose_new_claude_indirection_is_unset(
    tmp_path: Path, oneharness_bin: str, config: str, missing: str
) -> None:
    """The refusal a wrapper that forgot an export would meet, from the real CLI.

    Every other indirection is set, to a directory that exists, so the missing one is the
    only thing that can refuse the run — and the guard is first on `PATH`, so a refusal
    that regressed into a routed turn reaches no paid provider.
    """
    host = _existing_host(tmp_path, PAID_PROVIDER_GUARD)
    for indirection in CLAUDE_INDIRECTIONS:
        (tmp_path / indirection).mkdir()
        host.environment[indirection] = str(tmp_path / indirection)
    host.environment["ORCHESTRATOR_CODEX_ALT_HOME"] = str(tmp_path / "codex-alt")
    del host.environment[missing]

    refused = _run(
        [oneharness_bin, "run", "--config", str(REPO_ROOT / config), "--prompt", "x", "--compact"],
        host,
    )

    assert refused.returncode == 2, f"{refused.stdout}\n{refused.stderr}"
    assert f"`{missing}` is not set in the parent process" in refused.stderr, refused.stderr


# Only the paid provider executables (`claude`, `codex`) are substituted; the real
# dispatch-environment resolver, config and `oneharness` CLI run above them, as in
# tests/e2e/test_dispatch_environment_e2e.py.
# llmlint: ignore[e2e_not_mocked, tests_mirror_real_usage] see the reason above
def test_on_an_existing_host_the_agent_config_starts_a_real_turn(tmp_path: Path) -> None:
    """Nothing exported and no identities file, and the real CLI runs the first candidate.

    The environment is the one `scripts/dispatch-env.sh` establishes for a launch, and the
    turn is `oneharness run` under the agent config, as the engine starts a worker's.
    """
    front = _recorders(tmp_path / "providers", "claude", "codex")
    host = _existing_host(tmp_path, front)

    turn = _run(
        [
            "bash",
            "-c",
            '. "$1"; export_dispatch_environment probe; '
            'exec oneharness run --config "$2" --prompt record',
            "probe",
            str(DISPATCH_ENVIRONMENT),
            str(REPO_ROOT / "oneharness.toml"),
        ],
        host,
    )

    assert turn.returncode == 0, turn.stderr
    assert "is not set in the parent process" not in turn.stderr
    recorded = _recorded(host)
    assert [entry["binary"] for entry in recorded] == ["claude"], recorded
    handed = recorded[0]["environment"]
    assert isinstance(handed, dict)
    assert handed["CLAUDE_CONFIG_DIR"] == str(host.home / ".claude-alt")
    assert {name: handed[name] for name in CLAUDE_INDIRECTIONS} == {
        name: str(host.home / leaf) for name, leaf in DEFAULT_LEAVES.items()
    }


# Only the paid provider executables (`claude`, `codex`) are substituted; the real llmlint
# wrapper, config and `oneharness` CLI classify the candidate and fall through it above them.
# llmlint: ignore[e2e_not_mocked, tests_mirror_real_usage] see the reason above
def test_a_never_logged_in_primary_backup_falls_through_as_auth_where_nothing_filters_it(
    tmp_path: Path,
) -> None:
    """An entry point that filters nothing still reaches the primary, and says why it skipped.

    The llmlint wrapper forwards its selection untouched, so the candidate whose directory
    was never created reaches the real CLI, which classifies it `auth` without spawning
    anything and continues to the primary under `$HOME/.claude`.
    """
    front = _recorders(tmp_path / "providers", "claude", "codex")
    host = _existing_host(tmp_path, front)

    turn = _run(
        [
            str(LLMLINT_WRAPPER),
            "run",
            "--harness",
            "claude-code:primary-backup,claude-code:primary",
            "--mode",
            "read-only",
            "--prompt",
            "record",
            "--compact",
        ],
        host,
    )

    assert turn.returncode == 0, turn.stderr
    fallback = json.loads(turn.stdout)["fallback"]
    assert [(skipped["harness"], skipped["reason"]) for skipped in fallback["fell_through"]] == [
        ("claude-code:primary-backup", "auth")
    ], fallback
    assert fallback["ran"] == "claude-code:primary", fallback
    recorded = _recorded(host)
    assert [entry["binary"] for entry in recorded] == ["claude"], recorded
    handed = recorded[0]["environment"]
    assert isinstance(handed, dict)
    assert handed["CLAUDE_CONFIG_DIR"] == str(host.home / ".claude")
    assert not (host.home / ".claude-primary-backup").exists()


def _assert_login_refusal_is_authentication(rendered: str) -> None:
    """The operator verdict whose legacy failure this adoption must expose."""
    assert "fell through 'claude-code' (auth)" in rendered, rendered
    assert "rate-limit" not in rendered, rendered


def test_the_login_refusal_assertion_rejects_the_legacy_rate_limit_classification() -> None:
    """The regression assertion fails specifically when the old classification returns."""
    legacy = "provider: fell through 'claude-code' (rate-limit)"
    with pytest.raises(AssertionError, match="fell through 'claude-code' \\(auth\\)"):
        _assert_login_refusal_is_authentication(legacy)


# Only the paid Claude process is substituted. A single-sided node graph keeps the turn
# inside the oneagentgraph and oneharness-core libraries linked into the installed engine.
# llmlint: ignore[e2e_not_mocked, tests_mirror_real_usage] see the reason above
def test_the_adopted_engine_reports_a_claude_login_refusal_as_authentication(
    tmp_path: Path,
) -> None:
    provider = tmp_path / "claude"
    provider.write_text(LOGIN_REFUSAL, encoding="utf-8")
    provider.chmod(0o755)
    harness = tmp_path / "oneharness.toml"
    harness.write_text('run_mode = "fallback"\nharnesses = ["claude-code"]\n', encoding="utf-8")
    graph = tmp_path / "node-scope.yaml"
    graph.write_text(
        "version: 1\nname: node-scope\nmembers:\n  worker:\n"
        "    kind: oneharness\n    oneharness_config: ./oneharness.toml\n",
        encoding="utf-8",
    )
    run = f"login-refusal-{os.getpid()}"
    plan = tmp_path / "login-refusal.plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "goal": {"text": "Observe the login refusal."},
                "name": run,
                "tasks": [
                    {
                        "id": "work",
                        "persona": "engineer",
                        "task": "Report without changing files.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment.pop("ONEAGENTGRAPH_ONEHARNESS_BIN", None)
    environment.update(
        {
            "CLAUDE_CODE_SESSION_ID": "e2e-claude-login-refusal",
            "ONEPIPELINE_RUNS_DIR": str(tmp_path / "runs"),
            "ONEPIPELINE_NODE_GRAPH": str(graph),
            "ONEAGENTGRAPH_STATE_DIR": str(tmp_path.parent / "login-refusal-graphs"),
            "ONEHARNESS_BIN_CLAUDE_CODE": str(provider),
            "XDG_STATE_HOME": str(short_state.state_home(tmp_path)),
        }
    )
    project = project_from_plan(plan, run)
    launched = subprocess.run(  # noqa: S603 - this checkout's installed-engine recipe
        [
            "just",
            "orchestrate",
            project,
            "--dag-graph",
            "off",
            "--success-hook=",
            "--failure-hook=",
        ],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
        check=False,
    )
    assert launched.returncode != 0, launched.stdout + launched.stderr
    results = subprocess.run(  # noqa: S603 - this checkout's installed-engine results recipe
        ["just", "results", run],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert results.returncode == 0, results.stderr
    _assert_login_refusal_is_authentication(results.stdout)


class EntryPoint(NamedTuple):
    """One script that resolves the Claude indirections before it hands off."""

    script: str
    arguments: tuple[str, ...]
    stdin: str | None = None


#: Every entry point that calls `resolve_claude_alt_config_dir` and then runs `uv` or
#: `oneharness`, with arguments that carry it to that hand-off.
ENTRY_POINTS = (
    EntryPoint("llmlint-oneharness.sh", ("run", "--mode", "read-only", "--prompt", "record")),
    EntryPoint("oneharness-usage.sh", ("--harness", "claude-code:primary")),
    EntryPoint("onepipeline.sh", ("start", "plans:identity-routing-probe")),
    EntryPoint("review-plan.sh", ("plans:identity-routing-probe",)),
    EntryPoint("envelope-review.sh", (), ENVELOPE),
    EntryPoint("smoke.sh", ()),
)


@pytest.mark.parametrize("entry", ENTRY_POINTS, ids=[entry.script for entry in ENTRY_POINTS])
# The hand-off environment is the only observable: past `uv`/`oneharness` these entry points
# launch a real run or a paid review, and the real `oneharness` path is proven by the two
# journeys above.
# llmlint: ignore[e2e_not_mocked, tests_mirror_real_usage] see the reason above
def test_on_an_existing_host_every_entry_point_hands_off_with_every_indirection_resolved(
    tmp_path: Path, entry: EntryPoint
) -> None:
    front = _recorders(tmp_path / "hand-off", "uv", "oneharness")
    host = _existing_host(tmp_path, front)

    completed = _run(
        ["bash", str(REPO_ROOT / "scripts" / entry.script), *entry.arguments],
        host,
        stdin=entry.stdin,
    )

    recorded = _recorded(host)
    assert recorded, (
        f"{entry.script} never reached its hand-off (exit {completed.returncode}):\n"
        f"{completed.stderr}"
    )
    expected = {name: str(host.home / leaf) for name, leaf in DEFAULT_LEAVES.items()}
    for entry_recorded in recorded:
        assert entry_recorded["environment"] == expected, (
            f"{entry.script} handed {entry_recorded['binary']} {entry_recorded['environment']}"
        )
    assert "Claude config" not in completed.stderr, completed.stderr
    assert not (host.home / ".claude-primary-backup").exists()


class RefusedIdentitiesFile(NamedTuple):
    """One way a host identities file is refused."""

    #: What the file holds, or None where the path is a directory instead of a file.
    contents: str | None
    #: The words in the refusal that say which way it was refused.
    reported: str


REFUSED_IDENTITIES_FILES = {
    "a-name-it-does-not-admit": RefusedIdentitiesFile(
        "NOT_A_CLAUDE_IDENTITY=a-value-that-must-not-appear\n",
        "names NOT_A_CLAUDE_IDENTITY",
    ),
    "a-malformed-line": RefusedIdentitiesFile(
        "ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR=/a-value-that-must-not-appear\nnot assigned\n",
        "malformed Claude identities line 2",
    ),
    "a-value-it-does-not-expand": RefusedIdentitiesFile(
        "ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR=$HOME/a-value-that-must-not-appear\n",
        "primary Claude config path must be absolute",
    ),
    "an-empty-value": RefusedIdentitiesFile(
        "ORCHESTRATOR_CLAUDE_PRIMARY_BACKUP_CONFIG_DIR=\n",
        "primary-backup Claude config path must be absolute",
    ),
    "not-a-regular-file": RefusedIdentitiesFile(None, "is not a readable regular file"),
}


@pytest.mark.parametrize("refusal", list(REFUSED_IDENTITIES_FILES))
@pytest.mark.parametrize("entry", ENTRY_POINTS, ids=[entry.script for entry in ENTRY_POINTS])
def test_a_refused_identities_file_stops_every_entry_point_before_its_hand_off(
    tmp_path: Path, entry: EntryPoint, refusal: str
) -> None:
    front = _recorders(tmp_path / "hand-off", "uv", "oneharness")
    host = _existing_host(tmp_path, front)
    contents, reported = REFUSED_IDENTITIES_FILES[refusal]
    identities = host.home / ".config" / "ai-orchestrator" / "claude-identities.env"
    identities.parent.mkdir(parents=True)
    if contents is None:
        identities.mkdir()
    else:
        identities.write_text(contents, encoding="utf-8")

    refused = _run(
        ["bash", str(REPO_ROOT / "scripts" / entry.script), *entry.arguments],
        host,
        stdin=entry.stdin,
    )

    assert refused.returncode != 0, refused.stderr
    assert reported in refused.stderr, refused.stderr
    assert "a-value-that-must-not-appear" not in refused.stderr
    assert _recorded(host) == [], f"{entry.script} handed off past a refused identities file"
