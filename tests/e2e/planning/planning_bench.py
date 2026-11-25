"""The throwaway host every planning journey runs on.

Each journey runs in a **copy of this checkout** — `nx_workspace.copy_working_tree` over this
checkout's install — because the planning road's draft is a direct node that works in the
checkout it was launched from, and one journey changes `graphs/planner.yaml` to move the
review bar; neither may touch the checkout the suite runs in. Everything in the copy is
real: the `just` recipes, `templates/planning/`, `scripts/review-plan-item.sh`,
`scripts/plan-attestation-check.sh`, `scripts/run-ended.sh`, the installed `onepipeline`,
`onetaskgraph`, `oneagentgraph`, `onejudge` and `llmlint`.

**Only the paid model is doubled**, at the two seams it is reached through:
`tests/e2e/fake_backend.py` as `oneagentgraph`'s harness binary, answering every dispatched
worker and its supervisor (and running the commands a worker whose task carries a marker
runs: the real store and engine verbs, in the dispatch's own directory), and
`tests/e2e/planning/judging_codex.py` as codex's provider binary, answering every judge a
review reaches and recording every input it is handed. The planner's `reviewer` is answered at
that second seam rather than the first (`FAKE_BACKEND_PANEL_JUDGE_AT_PROVIDER`): its turn runs
the real chain down to the Codex binary, which refuses a directory git places in no work tree
as the real provider does.

Every store a journey writes is its own: the runs root, the harness history, the authoring
and follow-up-runs roots (the checkout copy's `.plans` and `.follow-ups/runs`, stated over
the ones `tests/conftest.py` gives the whole test process), a private `board` local Markdown
source standing in for the plans board, a `humans` local source the engine's human items
are filed in, an empty follow-up drafts root, and — for a journey that preserves a spike
branch — a scratch `onevcs` registry holding a seeded identity. No tracker credential
reaches any process.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import NamedTuple

import follow_up_variables
import plan_root_variable
import short_state
from fake_backend import PANEL_JUDGE_AT_PROVIDER_ENV, PROMPT_LOG_ENV, RUN_ON_MARKER_ENV
from harness_indirections import established_indirections
from judging_codex import JudgeInput
from no_paid_provider import REFUSAL as PAID_PROVIDER_REFUSAL
from nx_workspace import (
    answering_this_checkouts_origin,
    copy_working_tree,
    share_this_checkouts_install,
)
from project_fixtures import helper
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

FAKE_BACKEND = helper("fake_backend.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")
JUDGING_CODEX = Path(__file__).resolve().parent / "judging_codex.py"

#: The judge's answer to a reviewer turn: accept.
ACCEPTS = json.dumps({"completion": True, "reason": "the stand-in judge accepts"})

#: The destination standing in for the plans board.
BOARD = "board"

#: Variables an enclosing dispatch or launch would otherwise decide for these launches.
INHERITED = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "ONEPIPELINE_HOOK",
    "ONEPIPELINE_RUN_ID",
    "ONEPIPELINE_RUN_ROOT",
    "ONEPIPELINE_NODE_ID",
    "ONEPIPELINE_NODE_SCRATCH_DIR",
    "ONEPIPELINE_FLOW",
    "ONEPIPELINE_CHANNEL_ASKER",
    "ONEPIPELINE_PLAN",
    "ONEVCS_SESSION",
    "ORCHESTRATOR_ASK_MANAGER",
    "ORCHESTRATOR_DECLINE_RULING",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)


def store_setting(source: str, path: str) -> str:
    """The variable the plan store reads `sources.<source>.<path>` from, at its environment
    layer: each dotted part upper-cased behind the store's prefix, a dash in the source's
    name read as an underscore."""
    name = source.upper().replace("-", "_")
    return "__".join(["ONETASKGRAPH_SOURCES", name, *path.upper().split(".")])


def local_source(source: str, root: Path) -> dict[str, str]:
    """The settings declaring ``source`` a private local Markdown source over ``root``."""
    return {
        store_setting(source, "plugin"): "local-md",
        store_setting(source, "config.root"): str(root),
        store_setting(source, "visibility"): "private",
    }


class Bench(NamedTuple):
    """One planning host: the checkout copy, its environment and where it records."""

    checkout: Path
    environment: dict[str, str]
    tmp: Path
    runs: Path
    board: Path
    judge_log: Path
    turn_log: Path
    markers: Path


def checkout_copy(destination: Path) -> Path:
    """A copy of this checkout, sharing its install and answering its origin."""
    destination.mkdir(parents=True)
    copy_working_tree(destination)
    share_this_checkouts_install(destination)
    answering_this_checkouts_origin(destination)
    for root in (".plans", ".follow-ups/runs", ".test-fixtures"):
        (destination / root).mkdir(parents=True, exist_ok=True)
    return destination


#: The engine's variable beneath `--require-rendered`, which `tests/conftest.py` exports
#: `false` for the session's hand-written fixture plans.
REQUIRE_RENDERED_ENV = "ONEPIPELINE_REQUIRE_RENDERED"


def bench(
    tmp: Path,
    oneharness_bin: str,
    session: str,
    *,
    rendered_only_as_configured: bool = False,
    **extra: str,
) -> Bench:
    """A bench in ``tmp``: a checkout copy and an environment launching into it.

    ``rendered_only_as_configured`` removes the session's `ONEPIPELINE_REQUIRE_RENDERED`
    opt-out from the bench, so every plan check and launch on it reads the rendered-only
    answer this host configures — `scripts/onepipeline.sh`'s per-project one for a `plan
    check`, the launch config's for a `start` — as a journey creating or checking a spikes
    project must.
    """
    checkout = checkout_copy(tmp / "checkout")
    environment = dict(os.environ)
    for name in INHERITED:
        environment.pop(name, None)
    if rendered_only_as_configured:
        environment.pop(REQUIRE_RENDERED_ENV, None)
    runs, board = tmp / "runs", tmp / BOARD
    board.mkdir()
    environment["CLAUDE_CODE_SESSION_ID"] = session
    environment["ONEPIPELINE_RUNS_DIR"] = str(runs)
    environment["XDG_STATE_HOME"] = str(short_state.state_home(tmp))
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(JUDGING_CODEX)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment["FAKE_CODEX_ANSWERS"] = json.dumps([ACCEPTS])
    # The planner's `reviewer` runs the real chain down to the Codex binary above, so a
    # review directory that binary refuses is refused here as the real provider refuses it.
    environment[PANEL_JUDGE_AT_PROVIDER_ENV] = "1"
    environment["FAKE_PLANNING_JUDGE_LOG"] = str(tmp / "judge.jsonl")
    environment[PROMPT_LOG_ENV] = str(tmp / "turns.jsonl")
    environment[RUN_ON_MARKER_ENV] = str(tmp / "markers.json")
    environment.update(local_source(BOARD, board))
    # The plan roots are the checkout copy's own. `tests/conftest.py` roots `authoring` and
    # `follow-up-runs` once per test process, so without this every journey a process runs
    # stores into one root, and the loopback board numbers its issues afresh per journey: a
    # refresh would then remap a board-only task's edge onto another journey's task copied
    # under the same issue id.
    environment[plan_root_variable.name()] = str(checkout / ".plans")
    environment[store_setting("follow-up-runs", "config.root")] = str(
        checkout / ".follow-ups" / "runs"
    )
    # Human items stay on, as this host runs them, but their tasks go to a local source of the
    # bench's, through the host-local layer `ONEPIPELINE_CONFIG` names, and no tracker
    # credential reaches any process here.
    for name in ("GH_PROJECTS_TOKEN", "GITHUB_TOKEN", "GH_TOKEN", "HELLOPATIENT_LINEAR_API_KEY"):
        environment.pop(name, None)
    humans = tmp / "humans"
    humans.mkdir()
    host = tmp / "onepipeline-host.yaml"
    host.write_text("human_items:\n  enabled: true\n  task_source: humans\n", encoding="utf-8")
    environment["ONEPIPELINE_CONFIG"] = str(host)
    environment.update(local_source("humans", humans))
    # Follow-up drafts — a success hook verifies a run's — go to an empty root of the bench's,
    # never to the drafts root an enclosing dispatch exported.
    drafts = tmp / "drafts"
    drafts.mkdir()
    root_name, plugin_name, command_name = follow_up_variables.all_names()
    environment.pop(command_name, None)
    environment.pop(plugin_name, None)
    environment[root_name] = str(drafts)
    environment.update(dict(established_indirections("tests/e2e/planning")))
    environment.update(extra)
    (tmp / "markers.json").write_text("{}", encoding="utf-8")
    return Bench(
        checkout,
        environment,
        tmp,
        runs,
        board,
        tmp / "judge.jsonl",
        tmp / "turns.jsonl",
        tmp / "markers.json",
    )


def run(
    bench: Bench, *argv: str, seconds: float = 600, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    """One command in the bench's checkout copy, with its environment."""
    return subprocess.run(  # noqa: S603 - this checkout's own recipes and the installed tools
        list(argv),
        cwd=bench.checkout,
        env=bench.environment,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
    )


def just(bench: Bench, *arguments: str, seconds: float = 900) -> subprocess.CompletedProcess[str]:
    """One of the copy's own `just` recipes."""
    return run(bench, "just", *arguments, seconds=seconds)


def store(bench: Bench, *arguments: str) -> subprocess.CompletedProcess[str]:
    """The copy's own pinned plan store."""
    binary = str(bench.checkout / ".venv" / "bin" / "onetaskgraph")
    return run(bench, binary, *arguments, "--no-interactive")


def store_json(bench: Bench, *arguments: str) -> dict[str, object]:
    """The plan store's JSON answer, refused by name when the verb failed."""
    answered = store(bench, *arguments, "--json")
    assert answered.returncode in (0, 3), answered.stdout + answered.stderr
    found = json.loads(answered.stdout)
    assert isinstance(found, dict), found
    return found


def mark(bench: Bench, marker: str, *commands: str) -> None:
    """Have the dispatched worker whose task carries ``marker`` run ``commands`` once, in order.

    Once per run: a worker is reached by more than one turn, and a real one does not create
    its records twice, so the commands claim a directory of the bench's, named for the run
    the worker was dispatched in, before they run.
    """
    held = json.loads(bench.markers.read_text(encoding="utf-8"))
    # A marker scripted again keeps its own claim slot, so it never shares another's in a run.
    slot = list(held).index(marker) if marker in held else len(held)
    claim = bench.tmp / "claims" / str(slot)
    claim.parent.mkdir(exist_ok=True)
    script = f'mkdir "{claim}-$ONEPIPELINE_RUN_ID" 2>/dev/null || exit 0; set -e; ' + "; ".join(
        commands
    )
    held[marker] = [["bash", "-c", script]]
    bench.markers.write_text(json.dumps(held), encoding="utf-8")


def judge_inputs(bench: Bench) -> list[JudgeInput]:
    """Every input the doubled judge provider was handed, in order."""
    if not bench.judge_log.is_file():
        return []
    lines = bench.judge_log.read_text(encoding="utf-8").splitlines()
    inputs: list[JudgeInput] = [json.loads(line) for line in lines]
    return inputs


#: What onejudge hands a judge as the worker's turn in a judge-only run given no `--task`.
JUDGE_ONLY_TURN = "Produce the material under review."
#: The task `scripts/review-plan-item.sh` hands every judge-only run it makes, which onejudge
#: puts in that turn's place: a phrase of its framing, which no other task carries.
REVIEW_FRAMING = "reviewed under the plan-review bar before or during its delivery"


def judge_only_turns(bench: Bench) -> list[dict[str, object]]:
    """Every judge-only reviewer turn the doubled model answered: one per judge-only run."""
    if not bench.turn_log.is_file():
        return []
    turns = [json.loads(line) for line in bench.turn_log.read_text(encoding="utf-8").splitlines()]
    return [
        turn
        for turn in turns
        if JUDGE_ONLY_TURN in str(turn.get("prompt")) or REVIEW_FRAMING in str(turn.get("prompt"))
    ]


def reviewer_codex_turns(inputs: list[JudgeInput]) -> list[JudgeInput]:
    """Each launch of a judge-only reviewer turn the Codex double was handed, refused or not:
    an `exec` carrying the reviewer's prompt, never an llmlint batch."""
    return [
        entry
        for entry in inputs
        if not entry["llmlint"]
        and entry["argv"][:1] == ["exec"]
        and (JUDGE_ONLY_TURN in str(entry["prompt"]) or REVIEW_FRAMING in str(entry["prompt"]))
    ]


def assert_reviewed_on_codex(
    inputs: list[JudgeInput], review: subprocess.CompletedProcess[str]
) -> None:
    """The review's `reviewer` ran on Codex in its non-repository scratch directory.

    Every reviewer launch the Codex double saw ran in a `review-plan-item.*` directory git
    placed in no work tree, and none was refused as untrusted — a refusal is what sends the
    chain to Claude — so the double answered each, and oneharness, which stops a chain at
    its first answer, started no Claude identity behind it. The review's own output names no
    `untrusted-directory` fallback and no turn the paid-provider guard refused, which is
    where any Claude identity of the chain would have been sent.
    """
    turns = reviewer_codex_turns(inputs)
    assert turns, "no reviewer turn reached the Codex provider binary"
    where = [turn["cwd"] for turn in turns]
    assert all(Path(one).name.startswith("review-plan-item.") for one in where), where
    assert not [turn for turn in turns if turn["repository"]], where
    refused = [turn["cwd"] for turn in turns if turn["refused"]]
    assert not refused, (
        "the Codex double refused the reviewer as an untrusted directory, as the real "
        f"provider would, in {refused}: oneharness.judge.toml's [harness.codex] must state "
        "skip_git_repo_check = true"
    )
    output = review.stdout + review.stderr
    assert "untrusted-directory" not in output, review.stderr
    assert PAID_PROVIDER_REFUSAL not in output, review.stderr


def status(bench: Bench, run_id: str) -> dict[str, object]:
    """`onepipeline status <run> --json`, through the copy's wrapper."""
    answered = run(bench, "scripts/onepipeline.sh", "status", run_id, "--json", seconds=120)
    assert answered.returncode == 0, answered.stdout + answered.stderr
    found = json.loads(answered.stdout)
    assert isinstance(found, dict), found
    return found


def runs_of(bench: Bench) -> list[str]:
    """Every run id recorded under the bench's runs root."""
    if not bench.runs.is_dir():
        return []
    return sorted(path.name for path in bench.runs.iterdir() if path.is_dir())


ROOT = REPO_ROOT
