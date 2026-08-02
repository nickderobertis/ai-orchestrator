"""Which provider each side of one dispatch actually ran on.

These journeys drive the real `orchestrator-dispatch` console script, the real
onejudge CLI, the real `scripts/oneharness-agent.sh`, and the real oneharness —
every party that decides a selection. Only the paid providers are replaced, at
oneharness's own boundary: a fake `codex` and a fake `claude` on PATH, each
speaking its harness's native protocol and recording the turn it was handed. What
a test reads back is therefore the process oneharness chose and spawned, not a
flag that parsed.

The judge fake answers with a JSON object because that is what onejudge requires
of a supervisor turn; a bare sentence is rejected with "judge did not return a
JSON object".
"""

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

from orchestrator import REPO_ROOT
from orchestrator.harnesses import JUDGE_HARNESS_ENV, WORKER_HARNESS_ENV

#: onejudge's own framing of a supervisor turn — how a recorded turn says which
#: side of the conversation it belongs to.
JUDGE_MARKER = "evaluator"
#: The shared preamble every dispatched worker is framed with.
WORKER_MARKER = "You are one worker in a larger orchestrated effort"

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
REPLY = (
    json.dumps({{"message": "looks good", "stop": True, "value": True,
                "done": True, "reason": "ok"}})
    if {judge_marker!r} in " ".join(sys.argv[1:])
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
        )
        provider.write_text(f"#!/usr/bin/env python3\n{recorder}{source}", encoding="utf-8")
        provider.chmod(0o755)


def _provider_path(bin_dir: Path, oneharness_bin: str) -> str:
    return f"{bin_dir}{os.pathsep}{Path(oneharness_bin).parent}{os.pathsep}{os.environ['PATH']}"


@dataclass(frozen=True)
class Dispatched:
    """One dispatch's exit status and the provider turns it actually spawned."""

    process: subprocess.CompletedProcess[str]
    turns: list[dict[str, Any]]

    def side(self, marker: str) -> list[dict[str, Any]]:
        """Every recorded turn whose prompt carries this side's marker."""
        return [turn for turn in self.turns if marker in " ".join(turn["argv"])]


def _dispatch(
    tmp_path: Path,
    onejudge_bin: str,
    oneharness_bin: str,
    *,
    extra_args: tuple[str, ...] = (),
    env: Mapping[str, str] | None = None,
) -> Dispatched:
    """Run one real dispatch with both paid providers replaced on PATH."""
    bin_dir = tmp_path / "bin"
    _fake_providers(bin_dir)
    target = tmp_path / "target"
    target.mkdir(exist_ok=True)
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
        **(env or {}),
    }
    environment.pop("ORCHESTRATOR_AGENT_STATUS_DIR", None)
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

    `ONEHARNESS_HARNESSES` is set here to a third identity — the one both sides
    would otherwise land on — so a side that ignored its own value would be caught
    running claude-code's *alternate2* identity, which carries a config directory
    the primary variant masks away.
    """
    dispatched = _dispatch(
        tmp_path,
        onejudge_bin,
        oneharness_bin,
        extra_args=("--worker-harness", "codex", "--judge-harness", "claude-code:primary"),
        env={"ONEHARNESS_HARNESSES": "claude-code:alternate2"},
    )

    assert dispatched.process.returncode == 0, dispatched.process.stderr
    worker_turns = dispatched.side(WORKER_MARKER)
    judge_turns = dispatched.side(JUDGE_MARKER)
    assert worker_turns, dispatched.turns
    assert judge_turns, dispatched.turns
    # The worker ran codex — its own binary, spawned by oneharness.
    assert {turn["bin"] for turn in worker_turns} == {"codex"}
    assert {turn["harnesses"] for turn in worker_turns} == {"codex"}
    # The judge ran claude-code's primary identity: claude's binary with
    # CLAUDE_CONFIG_DIR masked off, which is what makes it the primary account
    # rather than the alternate2 one the ambient value named.
    assert {turn["bin"] for turn in judge_turns} == {"claude"}
    assert {turn["harnesses"] for turn in judge_turns} == {"claude-code:primary"}
    assert {turn["claude_config_dir"] for turn in judge_turns} == {None}


def test_without_either_flag_a_process_wide_selection_still_moves_both_sides(
    tmp_path: Path, onejudge_bin: str, oneharness_bin: str
) -> None:
    """The behaviour the flags displace, proven rather than assumed.

    Same ambient value as the journey above and no flags: both sides land on
    `claude-code:alternate2`, each carrying that subscription's own config
    directory. That is what "the judge followed the worker" looks like.
    """
    dispatched = _dispatch(
        tmp_path,
        onejudge_bin,
        oneharness_bin,
        env={"ONEHARNESS_HARNESSES": "claude-code:alternate2"},
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
    first identity left. The judge's own chain leads with codex anyway. Nothing
    carries a per-side variable.
    """
    dispatched = _dispatch(tmp_path, onejudge_bin, oneharness_bin)

    assert dispatched.process.returncode == 0, dispatched.process.stderr
    worker_turns = dispatched.side(WORKER_MARKER)
    judge_turns = dispatched.side(JUDGE_MARKER)
    assert worker_turns, dispatched.turns
    assert judge_turns, dispatched.turns
    assert {turn["bin"] for turn in dispatched.turns} == {"codex"}
    assert {turn["harnesses"] for turn in worker_turns} == {
        "codex,codex:alternate,claude-code:primary"
    }
    # The judge side is left entirely to its config: no substitution, no variable.
    assert {turn["harnesses"] for turn in judge_turns} == {None}
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
