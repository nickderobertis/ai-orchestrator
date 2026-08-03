"""Which provider each side of one dispatch actually ran on.

These journeys drive the real `orchestrator-dispatch` and `orchestrator-run-plan`
console scripts, the real onejudge CLI, the real `scripts/oneharness-agent.sh`,
and the real oneharness — every party that decides a selection, plus real git for
the lifecycle node.

The one thing they replace is the paid provider itself, this repository's
designated external seam: a fake `codex` and a fake `claude` earlier on PATH than
the real ones, each speaking its harness's native protocol and recording the turn
it was handed. oneharness still selects the identity, resolves its variant
environment, spawns that binary by name, and parses its stream — which is what
makes "the worker ran codex" a fact about the real selection rather than about a
stub standing in for it. The fakes answer a supervisor turn with a JSON object
because that is what onejudge requires of one ("judge did not return a JSON
object"), and either fake may be selected for either side.
"""

# llmlint: ignore-file[e2e_not_mocked] only the paid provider binaries are replaced,
# at oneharness's own spawn boundary — the seam AGENTS.md designates — while
# onejudge, the wrapper, oneharness, git, and the lifecycle all run for real.

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from waits import timeout as e2e_timeout

from orchestrator import BASE_CONFIG, REPO_ROOT, gitops
from orchestrator.harnesses import (
    JUDGE_HARNESS_ENV,
    PROCESS_WIDE_HARNESS_ENV,
    WORKER_HARNESS_ENV,
)

#: onejudge's own framing of a supervisor turn — how a recorded turn says which
#: side of the conversation it belongs to.
JUDGE_MARKER = "evaluator"
#: The shared preamble every dispatched worker is framed with.
WORKER_MARKER = "You are one worker in a larger orchestrated effort"

#: The identity each side is told to run, so the sentinel below can be asserted
#: against them rather than merely chosen to differ by inspection.
WORKER_CHOICE = "codex"
JUDGE_CHOICE = "claude-code:primary"
#: The process-wide value the two journeys below state for themselves: a third
#: identity, which is what makes "the side ignored its own selection" observable.
#: It is only a sentinel while it is a value nobody else could have supplied, so
#: `_provider_environment` drops whatever the *enclosing* process exported before
#: either journey states this — otherwise a dispatch that happened to be routed to
#: the same identity would make the control and the treatment one value, and the
#: journey that states no process-wide selection at all would not be stating one.
AMBIENT_SENTINEL = "claude-code:alternate2"

#: Record the turn, then answer it as whichever side handed it over: onejudge
#: requires a JSON object from a supervisor turn ("judge did not return a JSON
#: object") and takes a worker turn's text as the agent's message. Either provider
#: can be selected for either side, so both must answer both.
_RECORDER = """
import json, os, sys
with open(os.environ["SELECTION_RECORD"], "a") as record:
    record.write(json.dumps({{
        "bin": {name!r},
        "claude_config_dir": os.environ.get("CLAUDE_CONFIG_DIR"),
        "harnesses": os.environ.get("ONEHARNESS_HARNESSES"),
        "worker_override": os.environ.get({worker_env!r}),
        "judge_override": os.environ.get({judge_env!r}),
        "argv": sys.argv[1:],
    }}) + "\\n")
PROMPT = " ".join(sys.argv[1:])
JUDGING = {judge_marker!r} in PROMPT
if {worker_marker!r} in PROMPT:
    # A worker turn does real work in its own cwd, which for a lifecycle node is the
    # isolated worktree: a step that changed nothing settles as `no-changes` and
    # never reaches the gate or the merge these journeys drive. Only a turn carrying
    # the worker preamble writes, because every other turn — a supervisor's, an
    # assessment — runs in the cwd of the command under test, which is this
    # repository itself.
    with open("worked-on-by-{name}.txt", "a") as change:
        change.write("worked\\n")
REPLY = (
    json.dumps({{"message": "looks good", "stop": True, "value": True,
                "done": True, "reason": "ok"}})
    if JUDGING
    else "I finished the subtask."
)
"""

#: One complete turn in codex's own JSON-lines shape.
_FAKE_CODEX = """
print(json.dumps({"type": "thread.started", "thread_id": "side-selection"}))
print(json.dumps({"type": "item.completed", "item": {
    "type": "agent_message", "text": REPLY}}))
print(json.dumps({"type": "turn.completed", "usage": {
    "input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1}}))
"""

#: One complete turn in claude-code's own result shape.
_FAKE_CLAUDE = """
print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                  "result": REPLY, "session_id": "side-selection"}))
"""


def _fake_providers(bin_dir: Path) -> None:
    """Replace both paid provider binaries, and only those, inside `bin_dir`."""
    bin_dir.mkdir(exist_ok=True)
    for name, source in (("codex", _FAKE_CODEX), ("claude", _FAKE_CLAUDE)):
        provider = bin_dir / name
        recorder = _RECORDER.format(
            name=name,
            worker_env=WORKER_HARNESS_ENV,
            judge_env=JUDGE_HARNESS_ENV,
            judge_marker=JUDGE_MARKER,
            worker_marker=WORKER_MARKER,
        )
        provider.write_text(f"#!/usr/bin/env python3\n{recorder}{source}", encoding="utf-8")
        provider.chmod(0o755)


def _provider_path(bin_dir: Path, oneharness_bin: str) -> str:
    return f"{bin_dir}{os.pathsep}{Path(oneharness_bin).parent}{os.pathsep}{os.environ['PATH']}"


def _provider_environment(tmp_path: Path, oneharness_bin: str) -> tuple[Path, dict[str, str]]:
    """The record file and the environment a command under test is run with."""
    bin_dir = tmp_path / "bin"
    _fake_providers(bin_dir)
    record = tmp_path / "selection.jsonl"
    record.touch()
    environment = {
        **os.environ,
        "PATH": _provider_path(bin_dir, oneharness_bin),
        "SELECTION_RECORD": str(record),
        # Pinned rather than derived from the real $HOME: an authenticated
        # alternate subscription on this host would otherwise change which
        # candidate the default chain reaches.
        "ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR": str(tmp_path / "absent-alternate"),
        "ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR": str(tmp_path / "absent-alternate2"),
        "ORCHESTRATOR_CODEX_ALT_HOME": str(tmp_path / "codex-alternate"),
        "ONEHARNESS_HISTORY": "false",
        "XDG_STATE_HOME": str(tmp_path / "state"),
    }
    environment.pop("ORCHESTRATOR_AGENT_STATUS_DIR", None)
    # Whatever process-wide selection reached this process is not part of any
    # journey's claim, so no journey inherits one: each states its own value, and a
    # journey that states none genuinely runs with none. Dropped here, before the
    # per-journey `env` is layered on, so a stated value is the only one there is.
    environment.pop(PROCESS_WIDE_HARNESS_ENV, None)
    return record, environment


def _registered_local_checkout(tmp_path: Path, origin: Path, onejudge_bin: str) -> Path:
    """Clone the origin and register it the way an operator would, gate and all."""
    checkout = gitops.clone(origin, tmp_path / "checkout")
    subprocess.run(
        [
            str(Path(onejudge_bin).with_name("orchestrator-register-repo")),
            str(checkout),
            "--workflow",
            "local",
            "--repo-type",
            "single-owner",
            "--gate",
            "true",
        ],
        cwd=REPO_ROOT,
        env=os.environ,
        text=True,
        capture_output=True,
        check=True,
        timeout=e2e_timeout(60),
    )
    return checkout


@dataclass(frozen=True)
class Dispatched:
    """One dispatch's exit status and the provider turns it actually spawned."""

    process: subprocess.CompletedProcess[str]
    turns: list[dict[str, Any]]

    def side(self, marker: str) -> list[dict[str, Any]]:
        """Every recorded turn whose prompt carries this side's marker."""
        return [turn for turn in self.turns if marker in " ".join(turn["argv"])]


def _recorded_turns(record: Path, process: subprocess.CompletedProcess[str]) -> Dispatched:
    turns = [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]
    return Dispatched(process, turns)


def _dispatch(
    tmp_path: Path,
    onejudge_bin: str,
    oneharness_bin: str,
    *,
    extra_args: tuple[str, ...] = (),
    env: Mapping[str, str] | None = None,
) -> Dispatched:
    """Run one real dispatch with both paid providers replaced on PATH."""
    target = tmp_path / "target"
    target.mkdir(exist_ok=True)
    record, environment = _provider_environment(tmp_path, oneharness_bin)
    environment.update(env or {})
    process = subprocess.run(
        [
            str(Path(onejudge_bin).with_name("orchestrator-dispatch")),
            "engineer",
            "record which provider ran this side",
            "--project-dir",
            str(target),
            "--cwd",
            str(target),
            "--max-turns",
            "1",
            # oneharness resumes whatever harness a stored session was bound to, so
            # a name of this test's own keeps the selection under test the only one.
            "--session",
            f"side-selection-{tmp_path.name}",
            "--onejudge-bin",
            onejudge_bin,
            *extra_args,
        ],
        cwd=target,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
    )
    turns = [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]
    return Dispatched(process, turns)


def test_each_side_runs_the_provider_it_was_given_over_a_process_wide_selection(
    tmp_path: Path, onejudge_bin: str, oneharness_bin: str
) -> None:
    """The pairing this seam exists for: one author, a different reviewer.

    The process-wide selection is stated here as a third identity — the one both
    sides would otherwise land on — so a side that ignored its own value would be
    caught running claude-code's *alternate2* identity, which carries a config
    directory the primary variant masks away. The sentinel only says that while it
    is neither side's own choice, so the journey asserts that here rather than
    leaving it to whoever next edits the three constants.
    """
    assert AMBIENT_SENTINEL not in {WORKER_CHOICE, JUDGE_CHOICE}
    dispatched = _dispatch(
        tmp_path,
        onejudge_bin,
        oneharness_bin,
        extra_args=("--worker-harness", WORKER_CHOICE, "--judge-harness", JUDGE_CHOICE),
        env={PROCESS_WIDE_HARNESS_ENV: AMBIENT_SENTINEL},
    )

    assert dispatched.process.returncode == 0, dispatched.process.stderr
    worker_turns = dispatched.side(WORKER_MARKER)
    judge_turns = dispatched.side(JUDGE_MARKER)
    assert worker_turns, dispatched.turns
    assert judge_turns, dispatched.turns
    assert {turn["bin"] for turn in worker_turns} == {"codex"}
    assert {turn["harnesses"] for turn in worker_turns} == {WORKER_CHOICE}
    assert {turn["bin"] for turn in judge_turns} == {"claude"}
    assert {turn["harnesses"] for turn in judge_turns} == {JUDGE_CHOICE}
    # A masked CLAUDE_CONFIG_DIR is what distinguishes the primary account from the
    # alternate2 one the ambient value named; both run the same binary.
    assert {turn["claude_config_dir"] for turn in judge_turns} == {None}


def test_without_either_flag_a_process_wide_selection_still_moves_both_sides(
    tmp_path: Path, onejudge_bin: str, oneharness_bin: str
) -> None:
    """The behaviour the flags displace, proven rather than assumed.

    Same stated value as the journey above and no flags: both sides land on
    `claude-code:alternate2`, each carrying that subscription's own config
    directory. That is what "the judge followed the worker" looks like.
    """
    dispatched = _dispatch(
        tmp_path,
        onejudge_bin,
        oneharness_bin,
        env={PROCESS_WIDE_HARNESS_ENV: AMBIENT_SENTINEL},
    )

    assert dispatched.process.returncode == 0, dispatched.process.stderr
    assert dispatched.side(WORKER_MARKER), dispatched.turns
    assert dispatched.side(JUDGE_MARKER), dispatched.turns
    assert {turn["bin"] for turn in dispatched.turns} == {"claude"}
    assert {turn["claude_config_dir"] for turn in dispatched.turns} == {
        str(tmp_path / "absent-alternate2")
    }
    assert {turn["worker_override"] for turn in dispatched.turns} == {None}
    assert {turn["judge_override"] for turn in dispatched.turns} == {None}


def test_with_no_selection_at_all_both_sides_resolve_their_configured_chains(
    tmp_path: Path, onejudge_bin: str, oneharness_bin: str
) -> None:
    """The unchanged default path, including the absent-alternate substitution.

    Neither alternate Claude config directory exists in this fixture, so the agent
    branch substitutes a chain without them and the worker lands on codex — the
    first identity left. oneharness narrows its process-wide selection to that
    candidate before spawning the provider. The judge's own chain leads with codex
    anyway. Nothing carries a per-side variable.
    """
    dispatched = _dispatch(tmp_path, onejudge_bin, oneharness_bin)

    assert dispatched.process.returncode == 0, dispatched.process.stderr
    worker_turns = dispatched.side(WORKER_MARKER)
    judge_turns = dispatched.side(JUDGE_MARKER)
    assert worker_turns, dispatched.turns
    assert judge_turns, dispatched.turns
    assert {turn["bin"] for turn in dispatched.turns} == {"codex"}
    assert {turn["harnesses"] for turn in worker_turns} == {"codex"}
    # The judge side is left entirely to its config; oneharness scopes the spawned
    # provider to the candidate that config selected just as it does for the worker.
    assert {turn["harnesses"] for turn in judge_turns} == {"codex"}
    assert {turn["worker_override"] for turn in dispatched.turns} == {None}
    assert {turn["judge_override"] for turn in dispatched.turns} == {None}


@pytest.mark.parametrize(
    ("option", "value", "config"),
    [
        ("--worker-harness", "opencode", "oneharness.toml"),
        ("--judge-harness", "claude-code:alternate3", "oneharness.judge.toml"),
    ],
)
def test_an_unconfigured_identity_refuses_the_dispatch_before_it_starts(
    tmp_path: Path, onejudge_bin: str, oneharness_bin: str, option: str, value: str, config: str
) -> None:
    """A run that quietly used another provider is worse than one that refused."""
    dispatched = _dispatch(tmp_path, onejudge_bin, oneharness_bin, extra_args=(option, value))

    assert dispatched.process.returncode == 2, dispatched.process.stdout
    stderr = dispatched.process.stderr
    assert option in stderr
    assert repr(value) in stderr
    assert config in stderr
    # Every identity that side does configure, so the message is correctable.
    assert "codex:alternate" in stderr
    # Nothing was spawned: the refusal happens before any provider is selected.
    assert dispatched.turns == []


def test_run_plan_carries_the_selection_into_direct_and_lifecycle_nodes(
    tmp_path: Path,
    bare_origin,
    onejudge_bin: str,
    oneharness_bin: str,
) -> None:
    """One round, both node kinds, the providers each side was told to use.

    `just run-plan` builds its own runners for direct agents and for lifecycle
    workstreams, so a selection that reached only one of them would leave half a
    graph supervised by a provider nobody chose. This drives the real recorded
    executor over a real git checkout: the direct node dispatches, the lifecycle
    node clones, works in its worktree, passes its gate and merges — every turn of
    both through the real wrapper and the real oneharness.
    """
    origin = bare_origin()
    checkout = _registered_local_checkout(tmp_path, origin, onejudge_bin)
    record, environment = _provider_environment(tmp_path, oneharness_bin)
    target = tmp_path / "target"
    target.mkdir()

    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "tasks": [
                    {
                        "id": "direct",
                        "persona": "engineer",
                        "task": "record which provider ran this direct node",
                        "project_dir": str(target),
                        "max_turns": 1,
                    },
                    {
                        "id": "workstream",
                        "repo": str(checkout),
                        "persona": "engineer",
                        "task": "record which provider ran this lifecycle node",
                        "recorded_gate": ["true"],
                        "max_turns": 1,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    process = subprocess.run(
        [
            str(Path(onejudge_bin).with_name("orchestrator-run-plan")),
            str(plan),
            "--no-record",
            "--base",
            str(BASE_CONFIG),
            "--workspace",
            str(tmp_path / "worktrees"),
            "--worker-harness",
            "codex",
            "--judge-harness",
            "claude-code:primary",
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
    )
    dispatched = _recorded_turns(record, process)
    payload = json.loads(process.stdout)

    assert process.returncode == 0, process.stderr
    assert payload["results"]["workstream"]["outcome"] == "merged"
    # Both node kinds dispatched, and every side of every one of them ran the
    # provider the round was given.
    assert len(dispatched.side(WORKER_MARKER)) >= 2
    assert {turn["bin"] for turn in dispatched.side(WORKER_MARKER)} == {"codex"}
    assert {turn["bin"] for turn in dispatched.side(JUDGE_MARKER)} == {"claude"}
    assert {turn["harnesses"] for turn in dispatched.side(JUDGE_MARKER)} == {"claude-code:primary"}


def test_repo_task_runs_a_whole_workstream_on_the_providers_it_was_given(
    tmp_path: Path, bare_origin, onejudge_bin: str, oneharness_bin: str
) -> None:
    """The lifecycle entry point an operator reaches for one change, end to end.

    `just repo-task` clones, works in an isolated worktree, verifies with the
    identity's own gate and merges — several dispatches on one branch. All of them
    have to run the pair this command was given, and the merge is what proves the
    selection did not just parse but carried a real workstream through.
    """
    origin = bare_origin()
    checkout = _registered_local_checkout(tmp_path, origin, onejudge_bin)
    record, environment = _provider_environment(tmp_path, oneharness_bin)

    process = subprocess.run(
        [
            str(Path(onejudge_bin).with_name("orchestrator-repo-task")),
            str(checkout),
            "engineer",
            "record which provider ran this workstream",
            "--workspace",
            str(tmp_path / "worktrees"),
            "--max-turns",
            "1",
            "--worker-harness",
            "codex",
            "--judge-harness",
            "claude-code:primary",
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
    )
    dispatched = _recorded_turns(record, process)

    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout)["outcome"] == "merged"
    assert dispatched.side(WORKER_MARKER), dispatched.turns
    assert {turn["bin"] for turn in dispatched.side(WORKER_MARKER)} == {"codex"}
    assert {turn["bin"] for turn in dispatched.side(JUDGE_MARKER)} == {"claude"}
    assert {turn["harnesses"] for turn in dispatched.side(JUDGE_MARKER)} == {"claude-code:primary"}


def test_the_orchestrator_role_is_outside_this_seam(tmp_path: Path, oneharness_bin: str) -> None:
    """The boundary: a dispatch's per-side choice must not move the third role.

    Both variables are read only by the worker/judge wrapper, so an orchestrator
    process launched inside an environment carrying them still resolves its own
    config's chain — which leads with codex, not the claude-code identity named
    here.
    """
    bin_dir = tmp_path / "bin"
    _fake_providers(bin_dir)
    record = tmp_path / "selection.jsonl"
    record.touch()

    result = subprocess.run(
        [
            str(REPO_ROOT / "scripts" / "oneharness-orchestrator.sh"),
            "run",
            "--prompt",
            "prove the orchestrator role is unmoved",
            "--compact",
        ],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "PATH": _provider_path(bin_dir, oneharness_bin),
            "SELECTION_RECORD": str(record),
            "ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR": str(tmp_path / "absent-alternate"),
            "ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR": str(tmp_path / "absent-alternate2"),
            "ORCHESTRATOR_CODEX_ALT_HOME": str(tmp_path / "codex-alternate"),
            "ONEHARNESS_HISTORY": "false",
            WORKER_HARNESS_ENV: "claude-code:primary",
            JUDGE_HARNESS_ENV: "claude-code:primary",
        },
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["results"][0]["harness_id"] == "codex"
    spawned = {json.loads(line)["bin"] for line in record.read_text(encoding="utf-8").splitlines()}
    assert spawned == {"codex"}
