"""`just watch` is the command AGENTS.md's watch rule names, driven end to end over recorded runs.

The recipe is the engine's own `onepipeline watch` through `scripts/onepipeline.sh`, with
every argument forwarded, so what these journeys hold is that the rule a supervisor reads
is what that command really does: over each checked-in run, the watch ends at the
engine's own exit status — the one its return record names, and the one the rule's table
gives that ending — and its ending line on standard error carries the unread-surface
count and the cursor a later watch resumes from. Nothing is doubled: the real recipe, the
real launcher wrapper and the pinned engine run over a runs root this tree fixes.

**What a recorded run cannot produce is held beside this, by a launch.** A recorded run
is undriven, so every watch over one returns on its first pass — `settled` or
`nothing-driving` — and never waits long enough to write a heartbeat; the engine proves a
run is driven from its live driver process, which only a real launch provides. So the
heartbeat, and the three endings only a live run reaches (`surface-waiting`, `elapsed`,
`node-settled`), are taken by `tests/e2e/test_watch_selector_e2e.py` through this same
recipe over a run it launches and holds live. The rule's requirement that every heartbeat
carry the unread count is asserted there, where heartbeats exist; the helper below that
reads a heartbeat's count is shared so both halves read it one way.

llmlint: ignore-file[shell_test_tiers_stay_split,test_tiers_split_by_project_not_by_marker] This
repository runs one Nx project and splits its test tiers by pytest marker, which is a
settled design rather than an omission. Every test here is `reads_checkouts` — its
subject is the engine wheel installed under `.venv`, which lives outside every `nx.json`
key — so it belongs in the *uncached* `orchestrator:test-checkouts` target, where a memo
over this workspace would replay a green across the very upgrade this module exists to
catch. A project of its own would need a key over the same nothing.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest
from watch_rule import ENDING_LINE, HEARTBEAT_LINE, UNREAD, rule

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.reads_checkouts

#: The checked-in runs this drives the recipe over, so what the engine is asked is fixed
#: by this tree rather than by whatever this host happens to have run.
RECORDED_RUNS = ROOT / "tests" / "fixtures" / "timeline-runs"
#: A recorded run that settled complete.
SETTLED_RUN = "gate-parity-2"
#: A recorded run nothing is driving, whose graph holds several nodes, one of them
#: settled `failed` — which is what makes a condition naming that node answerable.
UNDRIVEN_RUN = "triage-by-root-cause-2"
#: The node of that run a selector is allowed to name, and one it does not hold.
NAMED_NODE = "basis"
ABSENT_NODE = "no-such-node-in-this-graph"
UNKNOWN_CONDITION = "when-the-wind-changes"
#: How long the recipe is given. Every invocation here reads the store once, or is
#: refused before it reads at all, so this is a bound on a wedge rather than on a wait.
BOUND_SECONDS = 240


class Event(NamedTuple):
    """An event the watch relayed: the engine's own kind for it."""

    kind: str


class Heartbeat(NamedTuple):
    """A heartbeat: an interval of silence, and how many planner surfaces are unread."""

    unread: int


class Return(NamedTuple):
    """The ending: the condition, the status it returns, the cursor, and the unread count."""

    condition: str
    exit: int
    cursor: str
    unread: int
    #: The node a `node-settled` ending names; no other ending names one.
    node: str | None


#: One line of the watch's standard output, as the engine's `watch` field names its kind.
Record = Event | Heartbeat | Return


def record_of(line: str) -> Record:
    """Read one machine record, failing by name on a kind or field the engine did not write."""
    match json.loads(line):
        case {"watch": "event", "event": {"kind": str(kind)}}:
            return Event(kind=kind)
        case {"watch": "heartbeat", "unread": {"count": int(count)}}:
            return Heartbeat(unread=count)
        case {
            "watch": "return",
            "condition": str(condition),
            "exit": int(status),
            "cursor": str(cursor),
            "unread": {"count": int(count)},
            **rest,
        } if rest.get("node") is None or isinstance(rest.get("node"), str):
            return Return(
                condition=condition, exit=status, cursor=cursor, unread=count, node=rest.get("node")
            )
        case _:
            raise AssertionError(f"a machine record this module does not read: {line}")


class Watched(NamedTuple):
    """One watch, as its caller receives it."""

    status: int
    #: The machine records, one per line of standard output.
    records: tuple[Record, ...]
    #: The operator's lines, standard error.
    lines: tuple[str, ...]
    #: Both streams whole, for a failure message.
    said: str


def watch(run: str, *arguments: str, environment: dict[str, str] | None = None) -> Watched:
    """The real recipe over the recorded runs root, unless a caller names its own."""
    completed = subprocess.run(
        ["just", "watch", run, *arguments],
        cwd=ROOT,
        env=environment or {**os.environ, "ONEPIPELINE_RUNS_DIR": str(RECORDED_RUNS)},
        text=True,
        capture_output=True,
        timeout=BOUND_SECONDS,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    records = tuple(record_of(line) for line in completed.stdout.splitlines() if line.strip())
    return Watched(
        status=completed.returncode,
        records=records,
        lines=tuple(completed.stderr.splitlines()),
        said=completed.stdout + completed.stderr,
    )


def returned(watched: Watched) -> Return:
    """The one return record a watch that ended on a condition writes, last."""
    ends = [record for record in watched.records if isinstance(record, Return)]
    assert len(ends) == 1 and watched.records[-1] is ends[0], watched.said
    return ends[0]


def ending_line(watched: Watched) -> re.Match[str]:
    """The one ending line the engine wrote for its operator.

    Found rather than assumed last: `just` adds a line of its own naming the recipe when
    the status is not zero, and every ending but `settled` is.
    """
    found = [match for line in watched.lines if (match := ENDING_LINE.match(line))]
    assert len(found) == 1, watched.said
    return found[0]


def unread_count(line: str) -> int | None:
    """The unread-surface count a heartbeat or ending line carries, or `None` for none."""
    counted = UNREAD.search(line)
    return None if counted is None else int(counted.group(1))


def heartbeats(watched: Watched) -> list[str]:
    """Every heartbeat line the watch wrote for its operator."""
    return [line for line in watched.lines if HEARTBEAT_LINE.match(line)]


class RecordedEnding(NamedTuple):
    """One ending a recorded run really produces, and the run that produces it."""

    run: str
    #: The engine's own word for it, which is also what the rule's table calls it.
    condition: str


#: The two endings a run that will not change again can answer.
RECORDED_ENDINGS = (
    RecordedEnding(run=SETTLED_RUN, condition="settled"),
    RecordedEnding(run=UNDRIVEN_RUN, condition="nothing-driving"),
)


@pytest.mark.parametrize("ending", RECORDED_ENDINGS, ids=lambda row: row.condition)
def test_each_ending_is_reported_at_the_engines_own_status_and_the_rules(
    ending: RecordedEnding,
) -> None:
    """The recipe hands back the status the engine chose, and the rule names it the same.

    Three things that have to agree, read from three places: the process's exit status,
    the engine's own return record naming the condition and the status in one breath,
    and the table `AGENTS.md` gives a supervisor to branch on.
    """
    watched = watch(ending.run, "--timeout", "0")

    record = returned(watched)
    assert record.condition == ending.condition, watched.said
    assert watched.status == record.exit, watched.said
    assert watched.status == rule().statuses[ending.condition], watched.said


@pytest.mark.parametrize("ending", RECORDED_ENDINGS, ids=lambda row: row.condition)
def test_the_ending_line_carries_the_unread_count_and_the_cursor(ending: RecordedEnding) -> None:
    """The one signal the watch rule forbids filtering out, and the token it re-arms from.

    A count of zero is asserted as a zero: "nothing is unread" and "this watch stopped
    telling you" are the two states a supervisor most needs told apart, so the clause has
    to be there whatever the number is, and it has to be the engine's own number. The live
    journeys next door read a positive count off a run holding an unanswered question.
    """
    watched = watch(ending.run, "--timeout", "0")

    record = returned(watched)
    ended = ending_line(watched)
    assert ended["run"] == ending.run
    assert ended["ending"] == ending.condition
    assert unread_count(ended["unread"]) == record.unread == 0, ended.group(0)
    assert ended["cursor"] == record.cursor, ended.group(0)
    # A heartbeat is owed only an interval of silence, and a recorded run returns on its
    # first pass; none that did appear may be missing its count.
    for line in heartbeats(watched):
        assert unread_count(line) is not None, line


def test_the_cursor_an_ending_line_carries_is_one_a_later_watch_resumes_from() -> None:
    """The round trip the rule's re-arming advice rests on, against the engine that mints it.

    A caller reads the token off the ending line under the word the rule names, hands it
    straight back, and gets a watch that does not repeat what the first one showed.
    """
    first = watch(SETTLED_RUN, "--timeout", "0")
    word = rule().cursor_word
    cursor = ending_line(first).group(0).rsplit(f" {word} ", 1)[1]
    assert cursor == returned(first).cursor, first.said

    resumed = watch(SETTLED_RUN, "--cursor", cursor, "--timeout", "0")

    assert resumed.status == rule().statuses["settled"], resumed.said
    assert not [record for record in resumed.records if isinstance(record, Event)], (
        f"a watch resumed from {cursor} repeated what the first one showed:\n{resumed.said}"
    )


#: The two node conditions, as a caller spells them: the word and the shape.
NODE_CONDITIONS = ("node-settled", f"node={NAMED_NODE}")


@pytest.mark.parametrize("condition", NODE_CONDITIONS)
def test_a_forwarded_node_condition_is_taken_and_does_not_displace_the_run_level_answer(
    condition: str,
) -> None:
    """The recipe forwards `--until` to the parser, and a run nobody drives still says so.

    Accepted rather than refused — the verb read the run and reported an ending — and the
    ending is the run-level one, because `nothing-driving` is checked before any
    condition a caller named. That is what makes a node condition safe to ask for.
    """
    watched = watch(UNDRIVEN_RUN, "--until", condition, "--timeout", "0")

    assert returned(watched).condition == "nothing-driving", watched.said
    assert watched.status == rule().statuses["nothing-driving"], watched.said


@pytest.mark.parametrize(
    ("condition", "named"),
    [(UNKNOWN_CONDITION, (UNKNOWN_CONDITION,)), (f"node={ABSENT_NODE}", (ABSENT_NODE, NAMED_NODE))],
    ids=["unknown-condition", "absent-node"],
)
def test_a_condition_the_verb_refuses_reports_no_ending(
    condition: str, named: tuple[str, ...]
) -> None:
    """What the engine refuses reaches the caller unchanged, and claims no ending.

    A watch refused before it watched anything must not read as a run that settled, which
    is the silence-as-progress the whole command exists to end: its status is none of the
    rule's, it writes no return record, and the refusal names what to correct.
    """
    watched = watch(UNDRIVEN_RUN, "--until", condition, "--timeout", "0")

    assert watched.status not in rule().statuses.values(), watched.said
    assert not [record for record in watched.records if isinstance(record, Return)]
    for word in named:
        assert word in "\n".join(watched.lines), watched.said


def test_the_unbounded_wait_the_rule_offers_is_forwarded_and_taken() -> None:
    """`--timeout none` is what lets a supervisor write no loop, so it has to parse.

    Driven over a run that has settled, which returns on the first pass whatever the wait
    says, and under this module's own bound so a build that took the value and then
    blocked fails here rather than wedging the tier.
    """
    watched = watch(SETTLED_RUN, "--timeout", rule().unbounded)

    assert watched.status == rule().statuses["settled"], watched.said


def test_the_recorded_run_this_module_names_holds_the_node_it_drives_a_condition_with() -> None:
    """The fixture is read rather than trusted, so a shape is driven with a real id."""
    plan = json.loads((RECORDED_RUNS / UNDRIVEN_RUN / "plan.json").read_text(encoding="utf-8"))
    ids = [task.get("id") for task in plan.get("tasks", [])]

    assert NAMED_NODE in ids, ids
    assert ABSENT_NODE not in ids
