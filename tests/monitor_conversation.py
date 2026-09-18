"""A monitor conversation a real `onejudge` holds, with a given judge side.

The frames the monitor's judge side reads are onejudge's to write, so every journey about
what that side answers has onejudge write them: the installed `onejudge run`, whose
conversation's judge side is a command — the argv `graphs/dag-scope.yaml` declares, or a
recorder that keeps the frame it was handed. Only the paid model is doubled: a taken turn
is oneharness's own `--mock-harness` responder standing in for codex, and a lost turn is
the real `codex` refused by a loopback endpoint (`tests/lost_turn_producer.py`).

Each conversation runs in a scratch directory standing where a launch stands. That is
forced rather than chosen: the agent side discovers its `oneharness.toml` from the working
directory, and this repository's own names six real identities, so a scratch file naming
codex alone is what keeps a paid provider out of reach. The judge argv names its
configuration launch-relative (`config/onemessagebus.yaml`), so the scratch carries that
path too — this host's committed file, linked, or a copy a journey names.

Owned by the orchestrator project's tiers, the way every helper under `tests/` is: no
project definition names this directory, so a helper belongs to a tier by being inside
its key, and `tests/test_nx_cache_scope.py` holds every memoized tier that imports this
module to that — with this module as the sentinel that the guard reaches something.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orchestrator.root import REPO_ROOT

#: The committed configuration a conversation's judge side reads unless a journey names
#: a copy.
CONFIG = REPO_ROOT / "config" / "onemessagebus.yaml"

#: The one harness a conversation's agent side may select.
HARNESS = "codex"

#: The task, persona and bar the conversation is held to. A monitor's own are long and
#: none of their words reaches a frame the judge side branches on, so these are short.
TASK = "Actively monitor one executing tracked graph and report what drifts from it."
PERSONA = "Act as the live planner reviewing the run's monitor."
DONE_WHEN = "the watch reported every drift it saw as a finding"

#: How long one conversation may take before it is called hung. A lost turn is refused
#: at once and a taken one is a scripted answer, so this only catches a wedge — or a
#: reply window a journey forgot to shorten.
CONVERSATION_SECONDS = 240


@dataclass(frozen=True)
class Taken:
    """An agent side that takes its turns and says `said` on each."""

    said: str


@dataclass(frozen=True)
class Lost:
    """An agent side whose every turn the real producer loses, against `codex_home`."""

    codex_home: Path


@dataclass(frozen=True)
class Conversation:
    """How one conversation is composed: its agent side, its judge command, what it asks."""

    agent: Taken | Lost
    judge: list[str]
    max_turns: int = 2
    # `Any` because an eval is onejudge's config entry, written through to its YAML whole.
    evals: list[dict[str, Any]] = field(default_factory=list)
    assessment: str | None = None
    #: The judge command answers the agent side's own `respond` too — how a journey has
    #: a real onejudge hand the judge side a `respond` frame.
    judge_answers_the_agent: bool = False


@dataclass(frozen=True)
class Held:
    """A conversation onejudge ran, and the report it wrote about it."""

    completed: subprocess.CompletedProcess[str]
    #: `--format json`'s versioned report; `None` when onejudge wrote none. `Any` because
    #: it is onejudge's own wire format, parsed unvalidated: each journey checks the fields
    #: it reads where it reads them.
    report: dict[str, Any] | None

    def judge_errors(self) -> list[str]:
        """Every judge-side decision onejudge recorded as the side failing, by reason."""
        return [
            decision["reason"]
            for turn in (self.report or {}).get("judge_decisions", [])
            for decision in turn["decisions"]
            if decision.get("decision") == "error"
        ]

    def error(self) -> str:
        """The run's own error message, or empty when the run did not fail."""
        return str(((self.report or {}).get("error") or {}).get("message", ""))


def _scratch(scratch: Path, config: Path) -> Path:
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "oneharness.toml").write_text(
        f'run_mode = "fallback"\nharnesses = ["{HARNESS}"]\n', encoding="utf-8"
    )
    (scratch / "config").mkdir(exist_ok=True)
    linked = scratch / "config" / CONFIG.name
    linked.unlink(missing_ok=True)
    linked.symlink_to(config)
    return scratch


# `Any` because this is onejudge's run config, serialized whole for `onejudge run` to read.
def _document(conversation: Conversation) -> dict[str, Any]:
    judge = {"kind": "command", "command": conversation.judge}
    if conversation.judge_answers_the_agent:
        skill: dict[str, Any] = judge
    elif isinstance(conversation.agent, Taken):
        skill = {"kind": "oneharness", "mock_harness": [HARNESS]}
    else:
        skill = {"kind": "oneharness"}
    return {
        "provider": {"kind": "split", "skill": skill, "judge": judge},
        "task": TASK,
        "user": {
            "persona": PERSONA,
            "done_when": DONE_WHEN,
            "max_turns": conversation.max_turns,
            "settle_on_noop": False,
        },
        "session": "dag-scope-1-monitor",
        "evals": conversation.evals,
        "assessment": conversation.assessment,
    }


def start(
    conversation: Conversation,
    environment: dict[str, str],
    scratch: Path,
    *,
    config: Path = CONFIG,
) -> subprocess.Popen[str]:
    """Start the conversation in `scratch`, and return the running `onejudge`."""
    _scratch(scratch, config)
    document = scratch / "onejudge.yaml"
    document.write_text(json.dumps(_document(conversation)), encoding="utf-8")
    spawned = {**environment, "XDG_STATE_HOME": str(scratch / "state")}
    if isinstance(conversation.agent, Taken):
        # llmlint: ignore[e2e_not_mocked] Only the paid model's words are scripted.
        spawned["MOCK_STDOUT"] = json.dumps({"result": conversation.agent.said})
    else:
        spawned["CODEX_HOME"] = str(conversation.agent.codex_home)
    onejudge = shutil.which("onejudge", path=spawned.get("PATH", os.defpath))
    assert onejudge is not None, "onejudge is not installed; run scripts/session-setup.sh"
    return subprocess.Popen(  # noqa: S603 - the installed onejudge, as a dispatch runs it
        [onejudge, "run", str(document), "--format", "json"],
        cwd=scratch,
        env=spawned,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def finish(running: subprocess.Popen[str], seconds: float = CONVERSATION_SECONDS) -> Held:
    """Wait for a started conversation to end, and read its report."""
    try:
        stdout, stderr = running.communicate(timeout=seconds)
    except subprocess.TimeoutExpired as waited:
        running.kill()
        _, stderr = running.communicate()
        raise AssertionError(
            f"the conversation did not end within {seconds}s:\n{stderr}"
        ) from waited
    completed = subprocess.CompletedProcess(running.args, running.returncode, stdout, stderr)
    report = json.loads(stdout) if stdout.strip() else None
    return Held(completed, report)


def hold(
    conversation: Conversation,
    environment: dict[str, str],
    scratch: Path,
    *,
    config: Path = CONFIG,
) -> Held:
    """Hold one conversation to its end."""
    return finish(start(conversation, environment, scratch, config=config))


#: A judge side that keeps the first frame it is handed in the file its one argument
#: names, and answers each op with the smallest reply onejudge accepts for it.
RECORDER = [
    "bash",
    "-c",
    'frame=$(cat); [ -e "$0" ] || printf "%s\\n" "$frame" > "$0"; '
    'case "$frame" in *\'"op":"supervisor"\'*) echo \'{"completion":true,"reason":"recorded"}\';; '
    '*) echo \'{"value":true,"reason":"recorded"}\';; esac',
]


def recorded_frame(op: str, scratch: Path, environment: dict[str, str]) -> str:
    """The first `op` frame a real onejudge writes to its judge side, verbatim.

    `supervisor` is the frame after the monitor's first taken turn; `judge` is the bar a
    one-turn conversation is scored against, which is the first frame it writes.
    """
    kept = scratch / f"{op}.frame.json"
    first = {"supervisor": 2, "judge": 1}[op]
    held = hold(
        Conversation(Taken("read the detailed stream"), [*RECORDER, str(kept)], max_turns=first),
        environment,
        scratch,
    )
    assert held.completed.returncode == 0, held.completed.stderr
    frame = kept.read_text(encoding="utf-8")
    assert json.loads(frame)["op"] == op, frame
    return frame
