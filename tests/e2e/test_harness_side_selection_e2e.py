"""Which provider each side of one dispatch actually ran on, and on what model.

These journeys drive the real `orchestrator-run-plan` console script — the one
executor, over plans holding a single direct node, a single lifecycle node, or
both — the real onejudge CLI, the real `scripts/oneharness-agent.sh`, and the real
oneharness: every party that decides a selection, plus real git for the lifecycle
node. The model is read the same way the provider is: from the `--model` on the
argv oneharness built, which is the last word on what the turn ran.

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
from orchestrator.labels import parse_labels

#: onejudge's own framing of a supervisor turn — how a recorded turn says which
#: side of the conversation it belongs to.
JUDGE_MARKER = "evaluator"
#: The shared preamble every dispatched worker is framed with.
WORKER_MARKER = "You are one worker in a larger orchestrated effort"

#: The identity each side is told to run, so the sentinel below can be asserted
#: against them rather than merely chosen to differ by inspection.
WORKER_CHOICE = "codex"
JUDGE_CHOICE = "claude-code:primary"
#: The model each side is told to run on, each one within its own side's chosen
#: harness family and each deliberately *not* what that identity's config pins —
#: an override that named the configured model would prove only that the config
#: was read. The configured values are asserted below rather than assumed.
WORKER_MODEL_CHOICE = "gpt-5.6-luna"
JUDGE_MODEL_CHOICE = "claude-opus-5"
#: What each side's config pins for the identity these journeys route it to, and
#: therefore what a side given no model of its own must still run on. Pinned here
#: so a config change fails as a drift gate rather than as a silent no-op override.
CONFIGURED_WORKER_MODEL = "gpt-5.6-sol"
CONFIGURED_JUDGE_MODEL = "gpt-5.6-sol"
#: The supervisor tier `oneharness.judge.toml` keeps every Claude identity on — the
#: one a judge-side model override exists to lift a consequential run off.
CONFIGURED_JUDGE_CLAUDE_MODEL = "claude-sonnet-5"
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
        # What oneharness would stamp on the session this turn becomes, read where
        # oneharness reads it: the environment of the provider it spawned.
        "history_labels": os.environ.get("ONEHARNESS_HISTORY_LABELS"),
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

    def models(self, marker: str) -> set[str | None]:
        """The model oneharness put on this side's provider invocations.

        Read from the provider's own argv, which is where a model stops being a
        variable and becomes the run: both harnesses take it as `--model <name>`.
        """
        return {_invoked_model(turn) for turn in self.side(marker)}


def _invoked_model(turn: Mapping[str, Any]) -> str | None:
    argv = list(turn["argv"])
    return argv[argv.index("--model") + 1] if "--model" in argv else None


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
    """Run one real dispatch with both paid providers replaced on PATH.

    A single dispatch is a one-node plan: the tracked graph is the only executor,
    so `orchestrator-run-plan` over a plan holding one direct agent node is what an
    operator runs for one subtask, and what these journeys drive.
    """
    target = tmp_path / "target"
    target.mkdir(exist_ok=True)
    record, environment = _provider_environment(tmp_path, oneharness_bin)
    environment.update(env or {})
    plan = tmp_path / "one-node.plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 6,
                "tasks": [
                    {
                        "id": "side-selection",
                        "persona": "engineer",
                        "task": "record which provider ran this side",
                        "project_dir": str(target),
                        "max_turns": 1,
                        # oneharness resumes whatever harness a stored session was
                        # bound to, so a name of this test's own keeps the selection
                        # under test the only one.
                        "session": f"side-selection-{tmp_path.name}",
                    }
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
            "--cwd",
            str(target),
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


def test_a_dispatch_never_stamps_the_worker_role_on_its_own_supervisor(
    tmp_path: Path, onejudge_bin: str, oneharness_bin: str
) -> None:
    """The semantic role a real dispatch stamps reaches only the side it describes.

    A dispatch exports one `ONEHARNESS_HISTORY_LABELS` for the whole conversation,
    and oneharness merges labels CLI > env > project file — so the worker's
    `agent_role` outranked `oneharness.judge.toml`'s own `agent_role = "judge"` and
    every supervisor session in the store was recorded as its worker's role. Read at
    the provider oneharness spawned, which is where the recording is decided: this
    is the real dispatch, the real wrapper, and the real oneharness label merge.
    """
    dispatched = _dispatch(tmp_path, onejudge_bin, oneharness_bin)

    assert dispatched.process.returncode == 0, dispatched.process.stderr
    worker_labels = [_labels(turn) for turn in dispatched.side(WORKER_MARKER)]
    judge_labels = [_labels(turn) for turn in dispatched.side(JUDGE_MARKER)]
    assert worker_labels and judge_labels, dispatched.turns
    assert all(labels.get("agent_role") == "worker" for labels in worker_labels)
    assert all("agent_role" not in labels for labels in judge_labels)
    # Only that one key: a judge session must still name the persona and the dispatch
    # it supervised, or nothing could group the two sides of one conversation.
    assert all(labels.get("persona") == "engineer" for labels in judge_labels)


def _labels(turn: Mapping[str, Any]) -> dict[str, str]:
    recorded = turn.get("history_labels")
    return parse_labels(recorded) if isinstance(recorded, str) else {}


def test_without_either_flag_a_process_wide_selection_still_moves_both_sides(
    tmp_path: Path, onejudge_bin: str, oneharness_bin: str
) -> None:
    """The behaviour the flags displace, proven rather than assumed.

    Same stated value as the journey above and no flags: both sides land on
    `claude-code:alternate2`, each carrying that subscription's own config
    directory. That is what "the judge followed the worker" looks like.

    This is also the legitimate half of the pair the isolation seam has to keep
    apart. A selection oneharness carries into a provider it spawns is real
    behaviour a journey may observe — asserted below — and it is told apart from an
    inherited one by *provenance, not by value*: `_provider_environment` drops
    whatever selection reached this process before any journey states its own, so a
    value a turn records here is one this journey put there.
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
    # Handed to the spawned provider verbatim: neither dropped, nor narrowed to the
    # candidate that ran. Pinned here because the sibling journey's assertion that
    # *no* selection arrives is only meaningful while a stated one demonstrably does.
    assert {turn["harnesses"] for turn in dispatched.turns} == {AMBIENT_SENTINEL}
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

    Both recorded values are also this suite's drift gate on what oneharness leaves
    a provider it spawns: the whole substituted *chain* on the worker side, and no
    variable at all on the judge side, which exports none. oneharness does not
    narrow a selection to the candidate it actually ran — see
    `test_without_either_flag_a_process_wide_selection_still_moves_both_sides` for
    the pass-through this journey is the absence of — so a reading of the single
    identity `codex` on both sides is the signature of a selection *inherited* from
    an enclosing dispatch reaching this journey, never of anything oneharness did.
    It was once read the other way and the assertions adapted to it, which deleted
    this gate; the chain below is what makes a real narrowing fail loudly here.
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
    # No model was chosen either, so each side runs the one its own config pins for
    # the identity it landed on — the no-op the model half must keep intact.
    assert dispatched.models(WORKER_MARKER) == {CONFIGURED_WORKER_MODEL}
    assert dispatched.models(JUDGE_MARKER) == {CONFIGURED_JUDGE_MODEL}


def test_each_side_runs_the_model_it_was_given(
    tmp_path: Path, onejudge_bin: str, oneharness_bin: str
) -> None:
    """The pairing the model half exists for: one author's tier, a different judge's.

    `oneharness.judge.toml` pins its Claude variants to the cheaper supervisor tier
    by design, and the only lever that beats a config's model is oneharness's
    process-wide `ONEHARNESS_MODEL` — which would also reach the orchestrator role's
    codex-first chain. So this is read where it stops being a variable: the `--model`
    each provider was actually invoked with, by the real wrapper and the real
    oneharness.
    """
    dispatched = _dispatch(
        tmp_path,
        onejudge_bin,
        oneharness_bin,
        extra_args=(
            "--worker-harness",
            WORKER_CHOICE,
            "--worker-model",
            WORKER_MODEL_CHOICE,
            "--judge-harness",
            JUDGE_CHOICE,
            "--judge-model",
            JUDGE_MODEL_CHOICE,
        ),
    )

    assert dispatched.process.returncode == 0, dispatched.process.stderr
    assert dispatched.side(WORKER_MARKER), dispatched.turns
    assert dispatched.side(JUDGE_MARKER), dispatched.turns
    assert dispatched.models(WORKER_MARKER) == {WORKER_MODEL_CHOICE}
    assert dispatched.models(JUDGE_MARKER) == {JUDGE_MODEL_CHOICE}
    # Neither value reached the other side, and neither side fell back to the model
    # its config pins for the identity it is running.
    assert JUDGE_MODEL_CHOICE not in dispatched.models(WORKER_MARKER)
    assert WORKER_MODEL_CHOICE not in dispatched.models(JUDGE_MARKER)
    assert CONFIGURED_WORKER_MODEL not in dispatched.models(WORKER_MARKER)
    assert CONFIGURED_JUDGE_CLAUDE_MODEL not in dispatched.models(JUDGE_MARKER)


@pytest.mark.parametrize(
    ("extra_args", "worker_model", "judge_model"),
    [
        pytest.param(
            ("--worker-harness", WORKER_CHOICE, "--worker-model", WORKER_MODEL_CHOICE),
            WORKER_MODEL_CHOICE,
            CONFIGURED_JUDGE_MODEL,
            id="worker-model-does-not-reach-the-judge-branch",
        ),
        pytest.param(
            ("--judge-harness", JUDGE_CHOICE, "--judge-model", JUDGE_MODEL_CHOICE),
            CONFIGURED_WORKER_MODEL,
            JUDGE_MODEL_CHOICE,
            id="judge-model-does-not-reach-the-agent-branch",
        ),
    ],
)
def test_a_model_on_one_side_leaves_the_other_on_its_configured_model(
    tmp_path: Path,
    onejudge_bin: str,
    oneharness_bin: str,
    extra_args: tuple[str, ...],
    worker_model: str,
    judge_model: str,
) -> None:
    """Both directions of the leak the per-side variables exist to prevent.

    `ONEHARNESS_MODEL` is process-wide, so a value set for one side would move both
    unless the wrapper applies it on that side's branch alone. The unchosen side is
    asserted on its config's own model rather than merely on "not the other value":
    an override that silently blanked it would satisfy the weaker claim.
    """
    dispatched = _dispatch(tmp_path, onejudge_bin, oneharness_bin, extra_args=extra_args)

    assert dispatched.process.returncode == 0, dispatched.process.stderr
    assert dispatched.side(WORKER_MARKER), dispatched.turns
    assert dispatched.side(JUDGE_MARKER), dispatched.turns
    assert dispatched.models(WORKER_MARKER) == {worker_model}
    assert dispatched.models(JUDGE_MARKER) == {judge_model}


@pytest.mark.parametrize(
    ("extra_args", "expected"),
    [
        pytest.param(
            ("--judge-model", JUDGE_MODEL_CHOICE),
            ("--judge-model", "--judge-harness", "oneharness.judge.toml"),
            id="a-model-with-no-identity-beside-it",
        ),
        pytest.param(
            ("--worker-harness", "claude-code:primary,codex", "--worker-model", "claude-opus-5"),
            ("--worker-model", "--worker-harness", "claude-code", "codex"),
            id="a-chain-spanning-two-harness-families",
        ),
    ],
)
def test_an_unpairable_model_refuses_the_dispatch_before_it_starts(
    tmp_path: Path,
    onejudge_bin: str,
    oneharness_bin: str,
    extra_args: tuple[str, ...],
    expected: tuple[str, ...],
) -> None:
    """A model pushed onto the wrong provider kills a run; refuse it at the boundary.

    oneharness's `fallback` mode falls through a candidate that cannot run at all,
    not a task the provider rejected — so an unpaired model does not degrade, it
    ends the dispatch mid-run. The refusal has to name the option and the reason.
    """
    dispatched = _dispatch(tmp_path, onejudge_bin, oneharness_bin, extra_args=extra_args)

    assert dispatched.process.returncode == 2, dispatched.process.stdout
    for fragment in expected:
        assert fragment in dispatched.process.stderr
    # Nothing was spawned: the refusal happens before any provider is selected.
    assert dispatched.turns == []


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


def test_run_plan_carries_both_side_choices_into_direct_and_lifecycle_nodes(
    tmp_path: Path,
    bare_origin,
    onejudge_bin: str,
    oneharness_bin: str,
) -> None:
    """One round, both node kinds, the provider and model each side was told to use.

    `just run-plan` builds its own runners for direct agents and for lifecycle
    workstreams, so a choice that reached only one of them would leave half a graph
    supervised by a provider — or at a tier — nobody chose. This drives the real
    recorded executor over a real git checkout: the direct node dispatches, the
    lifecycle node clones, works in its worktree, passes its gate and merges — every
    turn of both through the real wrapper and the real oneharness.
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
            "--worker-model",
            WORKER_MODEL_CHOICE,
            "--judge-harness",
            "claude-code:primary",
            "--judge-model",
            JUDGE_MODEL_CHOICE,
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
    assert dispatched.models(WORKER_MARKER) == {WORKER_MODEL_CHOICE}
    assert dispatched.models(JUDGE_MARKER) == {JUDGE_MODEL_CHOICE}


def test_a_one_node_plan_runs_a_whole_workstream_on_the_providers_it_was_given(
    tmp_path: Path, bare_origin, onejudge_bin: str, oneharness_bin: str
) -> None:
    """The lifecycle plan an operator reaches for one change, end to end.

    One lifecycle node clones, works in an isolated worktree, verifies with the
    identity's own gate and merges — several dispatches on one branch. All of them
    have to run the pair this run was given, and the merge is what proves the
    selection did not just parse but carried a real workstream through.
    """
    origin = bare_origin()
    checkout = _registered_local_checkout(tmp_path, origin, onejudge_bin)
    record, environment = _provider_environment(tmp_path, oneharness_bin)
    plan = tmp_path / "workstream.plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 6,
                "tasks": [
                    {
                        "id": "workstream",
                        "repo": str(checkout),
                        "persona": "engineer",
                        "task": "record which provider ran this workstream",
                        "max_turns": 1,
                    }
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

    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout)["results"]["workstream"]["outcome"] == "merged"
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
