"""The operational notes a dispatched worker is really handed, read off its own turn.

`config/dispatch-appendix.md` is this host's one source for that text, and every plan
node carries a copy of it inside its own `task`. So the file being right is not the same
claim as the rules arriving: a task is composed by a plan builder, rendered by
`onepipeline`, and handed to `oneagentgraph`, and each of those is a place a rule can be
dropped, reordered, or truncated before the model reads it.
`tests/test_dispatch_appendix.py` holds the file; this holds the prompt, which is the
artifact a worker actually obeys.

Everything between the recipe and the model is real: the `just orchestrate` recipe, the
`onepipeline` driver and its reconciler, the `graphs/` agent-graph configs,
`oneagentgraph`, and the onejudge conversation those compose.
`tests/e2e/fake_backend.py` stands in for the paid model alone, at the `oneharness` seam
a dispatch reaches it through, and records the prompt each turn was given.

What is asserted is what this repository most recently paid for, in both directions.

A worker is told to run only the checks that exercise what it changed, is told that the
repository's full bar runs downstream on the merge path, and is handed no complete gate
to run — the instruction this host removed after a gate run cost twenty to forty minutes
of every dispatch to learn what the merge path reports anyway, and after six of fourteen
nodes in one workstream settled `task-failed` with complete, green work. Removing it
from the file is a different claim from a worker no longer being handed it: this text is
copied into every task, rendered by `onepipeline`, and handed to `oneagentgraph`, and a
stale builder's copy travels through all three unchanged. It is also told, as a property
rather than as an ordering of its own outputs, that every claim it makes about the
finished work is true of the tree as it finally stands — and is no longer told that its
report must be its last output, which is the demand that failed six further nodes with
complete committed work and no acceptance criterion unmet.

A worker is told never to signal a process it did not identify by PID — stated *before*
any reasoning about how a pattern matches, because the old text argued only from `pgrep
-f`'s self-match and `pkill -x`, which does not self-match, is the more dangerous form;
two workers reached that place in one day while another manager's dispatch was live. And
a worker is no longer told to avoid running two gates concurrently: that rested on this
repository's e2e configs binding fixed ports, which they no longer do.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import NamedTuple, cast

import pytest
from fake_backend import JUDGE_CONFIG_NAME, PROMPT_LOG_ENV
from harness_indirections import established_indirections
from project_fixtures import project_from_plan
from test_dispatch_appendix import (
    A_DELTA_SATISFIES_IT,
    A_FALSE_CLAIM_STILL_FAILS,
    A_RUN_FROM_BEFORE_THE_CHANGE_IS_EVIDENCE,
    AMBIGUOUS_IN_FLIGHT,
    AN_INDUCED_FAILURE_IS_EVIDENCE,
    BACKGROUNDED,
    CAPTURED_AT_LAUNCH,
    CHAINED_INVOCATION,
    CLAIMS_TRUE_OF_THE_FINAL_TREE,
    CONCURRENT_GATE_INSTRUCTION,
    CONCURRENT_RUNS_ALLOWED,
    DEFINITION,
    DOWNSTREAM,
    EXCLUDES_ONLY_ITSELF,
    FIXED_PORT_REASON,
    GATE_FUNCTION,
    MATCHES_COMMAND_LINES,
    NARROWEST_SCOPE,
    NEVER_PATTERN_KILL,
    PER_INVOCATION_PATH,
    PID_FROM_A_PATTERN,
    POLLING,
    REPORT_IS_LAST,
    REPORTED_AFRESH,
    RERUN_THAT_CHECK,
    RERUN_WHAT_A_CHANGE_COULD_HAVE_BROKEN,
    SENTINEL_PATH_OWNERSHIP,
    SIGNAL_ONLY_BY_PID,
    SILENCE_DOES_NOT_SATISFY_IT,
    TARGETED_CHECKS,
    THE_CITATION_SAYS_WHICH_IT_IS,
    UNRECHECKED_CLAIMS_ARE_NAMED,
    WAITED_ON,
    WIDE_BAR,
    sentence_around,
)
from waits import timeout as e2e_timeout

from orchestrator.criteria_guard import APPENDIX
from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_docs

#: The stand-in for the paid model at the seam a two-party member spawns it through,
#: the stand-in one layer lower for the provider a single-sided member runs in library,
#: and the directory holding a `claude` that refuses the turn — the identities
#: `ONEHARNESS_BIN_*` cannot reach, since that seam keys on a harness id.
FAKE_BACKEND = Path(__file__).resolve().parent / "fake_backend.py"
FAKE_CODEX = Path(__file__).resolve().parent / "fake_codex.py"
PAID_PROVIDER_GUARD = Path(__file__).resolve().parent / "no-paid-provider"

#: Who `harness_indirections` attributes an unresolvable alternate identity to.
INDIRECTION_CALLER = "tests/e2e/test_dispatched_operational_notes_e2e.py"

#: The run this journey launches, and the node inside it. One direct node, because what
#: is under test is the text a dispatch carries rather than anything the node does.
RUN_NAME = "dispatched-operational-notes"
NODE_ID = "notes"

#: The member `oneagentgraph` pins a dispatched worker's turns to, and how it names that
#: member in the scratch path it writes each turn's config into.
WORKER_MEMBER = "worker"
MEMBER_OF_CONFIG = re.compile(r"/members/([^/]+)/")

#: A sentence of the node's own task, so the dispatched prompt can be told from every
#: other turn of the run — the monitor's, the pacemaker's, and each supervisor's.
TASK_MARKER = "Report which scheduler picks the next runnable node."


class Recorded(NamedTuple):
    """One turn as the stand-in recorded it, narrowed to what this journey reads.

    The two fields are the whole of the attribution: `config` is the scratch path
    `oneagentgraph` pinned the turn to, which names both the member and the side, and
    `prompt` is what the model was given.
    """

    config: str
    prompt: str


class Dispatched(NamedTuple):
    """One launched run, and the turns its stand-in model recorded."""

    launch: subprocess.CompletedProcess[str]
    turns: list[Recorded]


def _task() -> str:
    """A node's task in the shape every plan this host writes has, appendix included.

    Built the way a plan builder builds one — the appendix appended verbatim, which is
    what `orchestrator.criteria_guard.check_appendix` requires of every node — so what is
    read below is the text a real dispatch carries rather than a paraphrase of it.
    """
    return (
        f"## What\n\n{TASK_MARKER}\n\n"
        "## Why\n\nThe next change to the scheduler needs one written account of it.\n\n"
        "## Acceptance criteria\n\n"
        "- The note names the selection rule and the code that implements it.\n"
        "- The dispatch closes with a completion report naming the evidence it verified.\n\n"
        f"{(REPO_ROOT / APPENDIX).read_text(encoding='utf-8').strip()}\n"
    )


def _plan(root: Path) -> Path:
    written = root / "plan.json"
    written.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "goal": {"text": "Read the operational notes a dispatch carries"},
                "name": RUN_NAME,
                "tasks": [{"id": NODE_ID, "persona": "engineer", "task": _task()}],
            }
        ),
        encoding="utf-8",
    )
    return written


def _environment(tmp_path: Path, oneharness_bin: str) -> dict[str, str]:
    """The environment the launch gets, with the paid provider substituted at both seams."""
    environment = dict(os.environ)
    for name in ("ONEPIPELINE_LAUNCHER", "ONEPIPELINE_LAUNCHER_SESSION", "CODEX_THREAD_ID"):
        environment.pop(name, None)
    environment["CLAUDE_CODE_SESSION_ID"] = "dispatched-operational-notes-e2e"
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment.update(established_indirections(INDIRECTION_CALLER))
    # Keeps this run's graph scratch and history out of the host's, so it never reads or
    # reclaims a live dispatch's.
    environment["XDG_STATE_HOME"] = str(tmp_path / "state")
    return environment


@pytest.fixture(scope="module")
def dispatched(
    tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str
) -> Iterator[Dispatched]:
    """Launch one node carrying the real appendix and hand back what its worker read."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp_path = tmp_path_factory.mktemp("dispatched-operational-notes")
    environment = _environment(tmp_path, oneharness_bin)
    prompt_log = tmp_path / "prompts.jsonl"
    environment[PROMPT_LOG_ENV] = str(prompt_log)
    launch = subprocess.run(
        ["just", "orchestrate", project_from_plan(_plan(tmp_path))],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
        check=False,
    )
    try:
        assert launch.returncode == 0, launch.stdout + launch.stderr
        # The read that reaches past the operator-facing interface is this one, so it is
        # the line the directive below sits on: a line-scoped ignore covers the line after
        # it, and the block that decodes what was read needs no licence of its own.
        # llmlint: ignore[tests_mirror_real_usage] No view carries a dispatch's own prompt.
        recorded = prompt_log.read_text(encoding="utf-8")
        # The stand-in writes this JSONL itself, one object per turn; it is test-owned on
        # both ends, so this states its schema rather than validating somebody else's.
        written = [
            cast(Mapping[str, str], json.loads(line))
            for line in recorded.splitlines()
            if line.strip()
        ]
        yield Dispatched(
            launch=launch,
            turns=[
                Recorded(config=turn.get("config") or "", prompt=turn.get("prompt") or "")
                for turn in written
            ],
        )
    finally:
        subprocess.run(
            ["just", "stop", RUN_NAME],
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            timeout=e2e_timeout(60),
            check=False,
        )


@pytest.fixture(scope="module")
def dispatched_notes(dispatched: Dispatched) -> str:
    """The operational text the dispatched worker itself was handed, off its own turn."""
    working = []
    for turn in dispatched.turns:
        member = MEMBER_OF_CONFIG.search(turn.config)
        if member is None or member.group(1) != WORKER_MEMBER:
            continue
        # The agent side, by the one property that separates the two: the judge side is
        # the turn pinned to the judge config.
        if Path(turn.config).name == JUDGE_CONFIG_NAME:
            continue
        if TASK_MARKER in turn.prompt:
            working.append(turn.prompt)
    assert working, (
        "no dispatched worker of this run was handed its own task, so nothing here reads "
        f"the operational notes a dispatch carries:\n{dispatched.launch.stdout}\n"
        f"{dispatched.launch.stderr}"
    )
    return working[0]


@pytest.mark.xdist_group("dispatched-operational-notes")
def test_a_dispatched_worker_is_told_to_run_only_the_checks_that_exercise_its_change(
    dispatched_notes: str,
) -> None:
    """The instruction that replaced the gate, read off the prompt a worker was handed.

    The file saying it is not the same claim as the worker reading it: the appendix is
    copied into a task by a plan builder, rendered by `onepipeline`, and handed to
    `oneagentgraph`, and each of those is a place a rule can be dropped or truncated.
    The wide bar has to arrive too, named as something that runs downstream — a worker
    told only to run less, and never told who runs the rest, reinstates the wider run on
    its own judgment.
    """
    assert TARGETED_CHECKS.search(dispatched_notes), (
        "a dispatched worker was handed operational notes that never tell it to run only "
        "the checks that exercise what it changed, which is the whole of what replaced "
        "the complete gate"
    )
    assert NARROWEST_SCOPE.search(dispatched_notes), (
        "the notes a dispatch carries never tell it to take the narrowest scope each "
        "check supports, so 'the checks that exercise what you changed' arrives satisfied "
        "by the widest tier that touches the change"
    )
    assert RERUN_THAT_CHECK.search(dispatched_notes), (
        "the notes a dispatch carries never say that the check which reported something "
        "is the check to run again, so a worker confirms a fix by rerunning that check's "
        "neighbours — the round this instruction replaced"
    )
    mentions = list(WIDE_BAR.finditer(dispatched_notes))
    assert mentions, (
        "the notes a dispatch carries no longer say a repository-wide bar exists at all, "
        "so a worker told to run less is not told who runs the rest"
    )
    for mention in mentions:
        sentence = sentence_around(dispatched_notes, mention.start())
        assert any(marker in sentence.lower() for marker in DOWNSTREAM), (
            f"a dispatched worker is handed {mention.group(0)!r} in a sentence that does "
            f"not say it runs downstream on the merge path ({sentence!r})"
        )


@pytest.mark.xdist_group("dispatched-operational-notes")
def test_a_dispatched_worker_is_handed_no_complete_gate_to_run(
    dispatched_notes: str,
) -> None:
    """The removed instruction, absent from the prompt rather than from a file.

    A task rebuilt from a builder cloned before this change carries the old appendix
    verbatim — `just check-plan` refuses that plan, but nothing between the plan and the
    model would — so the absence is asserted where a worker actually reads it: no
    `complete_gate` definition, no handle to call one by, and no chained `just … && just
    …` for a worker to run instead.
    """
    defined = DEFINITION.search(dispatched_notes)
    assert defined is None, (
        f"a dispatched worker is handed a complete gate to run ({defined.group(0)!r}); "
        "this host stopped asking a dispatch for its repository's whole bar"
    )
    assert GATE_FUNCTION not in dispatched_notes, (
        f"a dispatched worker is handed `{GATE_FUNCTION}`, the handle the removed "
        "instruction called its chain by"
    )
    chained = CHAINED_INVOCATION.search(dispatched_notes)
    assert chained is None, (
        f"a dispatched worker is handed {chained.group(0)!r} — recipes chained into one "
        "command is what the complete gate was, under whatever name"
    )


@pytest.mark.xdist_group("dispatched-operational-notes")
def test_a_dispatched_worker_is_told_its_claims_must_hold_of_the_finished_tree(
    dispatched_notes: str,
) -> None:
    """The residue of the removed gate, arriving as a property of the finished dispatch.

    Six of fourteen nodes in one workstream settled `task-failed` on the ordering of
    their completion reports rather than on a missed criterion; the ordering demand that
    answered it then failed six more, each with complete committed work, a green
    deterministic tier, and no criterion found unmet. So what a dispatch is handed is the
    property that ordering was serving, and it is asserted here rather than in the file
    alone because a builder cloned before this change carries the old wording through
    `onepipeline` and `oneagentgraph` unaltered. The clause exempting evidence produced on
    purpose about an earlier or induced state travels with it, because a node was failed
    for citing the failure its own criteria required it to observe.
    """
    for stated, missing in (
        (
            CLAIMS_TRUE_OF_THE_FINAL_TREE,
            "that every claim it makes about the finished work is true of the tree as it "
            "finally stands",
        ),
        (A_DELTA_SATISFIES_IT, "that a correct delta satisfies that property"),
        (SILENCE_DOES_NOT_SATISFY_IT, "that silence after a later change satisfies nothing"),
        (
            A_FALSE_CLAIM_STILL_FAILS,
            "that a claim about a check or a commit that was never run or made is false",
        ),
        (
            RERUN_WHAT_A_CHANGE_COULD_HAVE_BROKEN,
            "that a change which could have invalidated a claim is re-run against",
        ),
        (
            UNRECHECKED_CLAIMS_ARE_NAMED,
            "that the claims left un-re-checked are named instead",
        ),
        (
            A_RUN_FROM_BEFORE_THE_CHANGE_IS_EVIDENCE,
            "that citing a run taken before a change is correct evidence",
        ),
        (
            AN_INDUCED_FAILURE_IS_EVIDENCE,
            "that citing a failure induced on purpose is correct evidence",
        ),
        (
            THE_CITATION_SAYS_WHICH_IT_IS,
            "that such a citation carries which of the two it is",
        ),
    ):
        assert stated.search(dispatched_notes), (
            f"the notes a dispatch carries no longer say {missing}"
        )


@pytest.mark.xdist_group("dispatched-operational-notes")
def test_a_dispatched_worker_is_not_told_its_report_must_be_its_last_output(
    dispatched_notes: str,
) -> None:
    """The withdrawn ordering, absent from the prompt rather than from a file.

    A plan builder cloned before this change composes a task carrying the old appendix
    verbatim, and nothing between that plan and the model would notice — so the absence
    is asserted where a worker actually reads it. The demand is unsatisfiable by a
    dispatch whose supervisor keeps asking after the report, and it failed six nodes with
    complete, green work and no acceptance criterion unmet.
    """
    for withdrawn, what in (
        (REPORT_IS_LAST, "that its completion report is the last thing it produces"),
        (REPORTED_AFRESH, "that what it finds afterwards is fixed and then reported afresh"),
    ):
        found = withdrawn.search(dispatched_notes)
        assert found is None, (
            f"a dispatched worker is told {what} again ({found.group(0)!r}); that demand "
            "cannot be met by a worker that answers its supervisor after reporting"
        )


@pytest.mark.xdist_group("dispatched-operational-notes")
def test_a_dispatched_worker_is_told_to_signal_only_what_it_identified_by_pid(
    dispatched_notes: str,
) -> None:
    """The rule, and its order, read out of the prompt rather than out of the file.

    Order is half of what is proven. The passage used to argue only from `pgrep -f`'s
    self-match, and `-x` does not self-match — so a reader who followed that reasoning to
    `pkill -x` followed it where it leads, and `pkill -x just` takes every `just` on this
    host where `pkill -f 'just gate'` at least needs a narrowing pattern. Two workers
    arrived there from different directions in one day while another manager's dispatch
    had been live over an hour, one of them by reading the process table and piping the
    pids into `kill` — which obeys every other sentence of this text.
    """
    signalling = SIGNAL_ONLY_BY_PID.search(dispatched_notes)
    assert signalling is not None, (
        "a dispatched worker was handed operational notes that never say a process is "
        "signalled only when it was identified by its own PID"
    )
    assert PID_FROM_A_PATTERN.search(dispatched_notes), (
        "the notes a dispatch carries do not say that a PID obtained from a pattern is "
        "still a pattern kill, which is the form one of the two workers used"
    )
    assert NEVER_PATTERN_KILL.search(dispatched_notes), (
        "the notes a dispatch carries do not refuse `pkill`, which knows nothing about "
        "whose process it matched on a host several managers share"
    )
    assert CAPTURED_AT_LAUNCH.search(dispatched_notes), (
        "the notes a dispatch carries show no way to hold a pid that was never matched; "
        "a rule that only forbids leaves a worker reaching for a pattern to get one"
    )
    matching = POLLING.search(dispatched_notes)
    assert matching is not None and signalling.start() < matching.start(), (
        "the notes a dispatch carries reason about how a pattern matches before they "
        "state whose process may be signalled, which is the order that licensed `pkill -x`"
    )


@pytest.mark.xdist_group("dispatched-operational-notes")
def test_a_dispatched_worker_is_still_told_why_a_pattern_wait_never_ends(
    dispatched_notes: str,
) -> None:
    """The demoted half, still arriving: it explains a wedged wait, not a kill.

    Seven workers wedged on this defect, so demoting it must not drop it. It is a fact
    about the polling mechanism — a pattern naming what you wait for is inside the
    command line of the shell doing the waiting — and it transfers to every wait, which
    is why it is asserted as the mechanism rather than as advice about gates.
    """
    assert MATCHES_COMMAND_LINES.search(dispatched_notes), (
        "the notes a dispatch carries no longer say a pattern is matched against full "
        "command lines, which is the whole reason a wait on one matches its own poll"
    )
    assert EXCLUDES_ONLY_ITSELF.search(dispatched_notes), (
        "the notes a dispatch carries no longer say the match excludes only the asking "
        "process itself — the shell doing the waiting is its parent, and nothing excludes "
        "that"
    )


@pytest.mark.xdist_group("dispatched-operational-notes")
def test_a_dispatched_worker_is_not_told_to_avoid_a_concurrent_gate(
    dispatched_notes: str,
) -> None:
    """The deleted rule, absent from what a worker is handed rather than from a file.

    It rested on this repository's e2e configs binding fixed ports; live e2e code
    allocates through `_free_port()` in `tests/dag_ui/test_dag_ui_serving_e2e.py` instead,
    and on 2026-08-24 two managers' judged tiers ran concurrently and both completed. The
    reason is asserted gone beside the instruction, because a reason left in the prompt is
    an instruction a worker reconstructs from it.
    """
    instruction = CONCURRENT_GATE_INSTRUCTION.search(dispatched_notes)
    assert instruction is None, (
        f"a dispatched worker is still told not to run two gates at once "
        f"({instruction.group(0)!r}); nothing on this host makes that true, and this text "
        "constrains every dispatch it is handed to"
    )
    reason = FIXED_PORT_REASON.search(dispatched_notes)
    assert reason is None, (
        f"a dispatched worker is still handed the fixed-port conflict ({reason.group(0)!r}) "
        "as a reason to serialize its gates"
    )


@pytest.mark.xdist_group("dispatched-operational-notes")
def test_a_dispatched_worker_is_shown_a_wait_on_one_command_and_its_own_sentinel(
    dispatched_notes: str,
) -> None:
    """The worked wait, read off the prompt rather than off the file it was copied from.

    Its predecessor backgrounded two of the complete gate's three parts and polled a
    shared `/tmp/gate.exit`, so a worker that obeyed it both ran the parts separately and
    could end its wait on another dispatch's result — on a host where three workers
    collided on shared names in one day. The example a worker is handed now has to be the
    repaired one: one command in the background, and the sentinel it polls named after
    the invocation that writes it.
    """
    backgrounded = BACKGROUNDED.findall(dispatched_notes)
    assert backgrounded, (
        "the notes a dispatch carries show it no way to wait on a long command at all, so "
        "a worker that needs one reaches for a pattern match instead"
    )
    waited = WAITED_ON.search(dispatched_notes)
    assert waited is not None, (
        "the notes a dispatch carries background a command and never poll for its "
        "sentinel, which is the half that makes the wait end"
    )
    sentinel = waited["sentinel"]
    assert PER_INVOCATION_PATH.search(sentinel), (
        f"a dispatched worker is shown a wait on {sentinel!r}, a path every dispatch on "
        "this host would write, so its wait can end on somebody else's result"
    )
    for command in backgrounded:
        assert sentinel in command, (
            f"a dispatched worker is shown {command!r} backgrounded against a wait on "
            f"{sentinel!r}, which that command does not write"
        )


@pytest.mark.xdist_group("dispatched-operational-notes")
def test_a_dispatched_worker_checks_its_sentinel_not_other_gates(
    dispatched_notes: str,
) -> None:
    """Read the repaired pre-launch check from the worker's real prompt.

    This is the delivery seam where the ambiguity produced two supervisory findings:
    the worker must receive a path-ownership check and explicit permission for
    concurrent gates and judged tiers, not the objectless ``one`` that was read as a
    gate.
    """
    assert SENTINEL_PATH_OWNERSHIP.search(dispatched_notes), (
        "a dispatched worker is not told that the pre-launch check concerns its sentinel path"
    )
    assert CONCURRENT_RUNS_ALLOWED.search(dispatched_notes), (
        "a dispatched worker is not told that sentinel ownership does not prohibit "
        "concurrent gates or judged tiers"
    )
    ambiguous = AMBIGUOUS_IN_FLIGHT.search(dispatched_notes)
    assert ambiguous is None, (
        "a dispatched worker still receives the ambiguous pre-launch instruction "
        f"{ambiguous.group(0)!r}"
    )
