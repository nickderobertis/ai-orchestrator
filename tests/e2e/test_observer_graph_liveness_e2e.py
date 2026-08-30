"""What holds `graphs/dag-scope.yaml`'s monitor to being a conversation.

The finding itself — why a scheduled monitor is unavailable on the pinned reader, what
it costs, and the two upstream changes that would lift it — is written up for a manager
in `docs/orchestration.md`. This module is what keeps that write-up honest, by driving
the reader rather than describing it:

* the shipped document validates, which is what every run's watching depends on;
* the same document with its monitor converted to the pacemaker's shape does not;
* the remedy that refusal names settles the whole observer graph after one turn each;
* the write-up quotes a refusal the installed reader still prints.

Only the paid provider is substituted, at both seams a member can reach one through.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

import pytest
from persona_probe import probe_environment
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The observer graph every `just orchestrate` attaches, and the two members it names.
DAG_SCOPE_GRAPH = "graphs/dag-scope.yaml"
MONITOR_MEMBER = "monitor"
PACEMAKER_MEMBER = "check-in"

#: Where the finding is written up for a manager, and what the last journey reads back.
FINDING_DOCUMENT = "docs/orchestration.md"

#: Who `harness_indirections` attributes an unresolvable alternate identity to.
INDIRECTION_CALLER = "tests/e2e/test_observer_graph_liveness_e2e.py"

#: The monitor as the repair would have it: the pacemaker's shape, the monitor's own
#: harness config, and the schedule that repair asked for. Written out here rather than
#: derived, because what is under test is whether the reader accepts *this* member.
SCHEDULED_MONITOR = """  monitor:
    kind: oneharness
    oneharness_config: ../oneharness.orchestrator.toml
    task: |
      {task}

      Watch that run and report what you find.
    schedule: {every: 600, start_after: 180, resettable: false}
"""

#: The same member with the first turn the refusal's own remedy demands. Substituted
#: into both members, because the reader asks it of every one of them.
IMMEDIATE_FIRST_TURN = re.compile(r"start_after: \d+")
IMMEDIATE = "start_after: 0"

#: What the pinned reader says when no member is left outside the schedules. Split into
#: the three claims it makes, so a reword that keeps the refusal fails on the wording
#: rather than on the behaviour: it names the quiescing, the members whose first turn
#: never comes due, and the two ways out.
REFUSAL_NAMES_THE_QUIESCE = "the run quiesces as soon as its clocks tick"
REFUSAL_NAMES_THE_DEFERRED = "never comes due"
REFUSAL_NAMES_THE_REMEDIES = ("`start_after: 0`", "a member outside the schedules")

#: How long a one-tick observer graph may take and still prove the point. Both shipped
#: schedules are minutes apart (600 and 1800 seconds), so a graph that has settled
#: inside this has settled without ever waiting for a second tick.
SETTLED_WITHOUT_A_SECOND_TICK_SECONDS = 120

#: The task a probe run composes. Distinctive, so a member's own `{task}` interpolation
#: is visibly this run's rather than something a stale scratch directory held.
PROBE_TASK = "onepipeline run `observer-liveness-probe`."


class Answered(NamedTuple):
    """What the pinned reader answered about one document, accepting it or refusing it."""

    status: int
    #: Both streams, because a refusal is written to stderr and an acceptance to stdout
    #: and these journeys assert on each.
    said: str


def _validated(graph: Path) -> Answered:
    """Ask the pinned reader about one document, the way the launch path asks."""
    ran = subprocess.run(
        ["oneagentgraph", "validate", str(graph)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    return Answered(status=ran.returncode, said=f"{ran.stdout}\n{ran.stderr}")


def _observer_graph_with_a_scheduled_monitor(written_to: Path, *, immediate: bool) -> Path:
    """The shipped document with its monitor converted to a scheduled single-sided member.

    Taken from the shipped file rather than retyped, so what is refused below is this
    repository's own observer graph with one member's shape changed and nothing else —
    the pacemaker, the schema version and every comment travel verbatim. Its relative
    refs are rewritten to absolute, which is what they already resolve to: a ref is
    resolved against the directory the document was read from, and this copy is read
    from a temporary one.

    `immediate` drives the remedy the refusal itself names onto every member, which is
    the only other shape an all-scheduled observer graph can have.
    """
    lines = (REPO_ROOT / DAG_SCOPE_GRAPH).read_text(encoding="utf-8").splitlines()
    opened = next(
        (index for index, line in enumerate(lines) if line.strip() == f"{MONITOR_MEMBER}:"),
        None,
    )
    assert opened is not None, (
        f"{DAG_SCOPE_GRAPH} declares no `{MONITOR_MEMBER}` member, so this journey is "
        "reading a document that has moved on without it"
    )
    closed = next(
        index for index, line in enumerate(lines) if line.strip() == f"{PACEMAKER_MEMBER}:"
    )
    # The pacemaker's own comment block belongs to the pacemaker, not to the member
    # being replaced, so the replacement stops where that block opens.
    while lines[closed - 1].strip().startswith("#"):
        closed -= 1

    document = "\n".join([*lines[:opened], *SCHEDULED_MONITOR.splitlines(), *lines[closed:]])
    if immediate:
        document = IMMEDIATE_FIRST_TURN.sub(IMMEDIATE, document)
        document = document.replace(
            "schedule: {every: 1800,", f"schedule: {{every: 1800, {IMMEDIATE},"
        )
    written_to.write_text(document.replace("../", f"{REPO_ROOT}/") + "\n", encoding="utf-8")
    return written_to


class Envelope(NamedTuple):
    """One event a probe run streamed, narrowed at the seam to what is read below.

    Parsed rather than cast, and parsed once here rather than at each read: a field the
    producer has moved arrives as its empty default, so a journey fails on the claim it
    was making instead of on a `KeyError` three frames away from the claim.
    """

    kind: str
    ts: str
    #: Who produced it — `member` is the one read here.
    labels: dict[str, str]
    #: Its kind's own body: a turn's `role`, a settlement's `members`.
    payload: dict[str, object]


def _envelope(streamed: dict[str, object]) -> Envelope:
    """One decoded stream line, narrowed by checking rather than by asserting."""
    labels = streamed.get("labels")
    payload = streamed.get("payload")
    return Envelope(
        kind=_text(streamed, "kind"),
        ts=_text(streamed, "ts"),
        labels={
            name: value
            for name, value in (labels if isinstance(labels, dict) else {}).items()
            if isinstance(value, str)
        },
        payload=payload if isinstance(payload, dict) else {},
    )


def _text(streamed: dict[str, object], field: str) -> str:
    """One string field of a decoded line, empty where it is absent or not a string."""
    value = streamed.get(field)
    return value if isinstance(value, str) else ""


class Ran(NamedTuple):
    """One probe run of a graph, narrowed to what these journeys read."""

    envelopes: list[Envelope]
    #: Everything it printed, for a failure message that can be acted on.
    said: str


def _ran(graph: Path, environment: dict[str, str], directory: Path) -> Ran:
    """Run one probe graph for real, in a directory of its own.

    `oneagentgraph run` is the verb `onepipeline` starts an observer graph with, so a
    member firing, settling, or never coming due is observable here exactly as it is on
    a launched run.
    """
    ran = subprocess.run(
        ["oneagentgraph", "run", str(graph), "--task", PROBE_TASK, "--dir", str(directory)],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
        check=False,
    )
    decoded = (json.loads(line) for line in ran.stdout.splitlines() if line.startswith("{"))
    return Ran(
        envelopes=[_envelope(line) for line in decoded if isinstance(line, dict)],
        said=f"{ran.stdout}\n{ran.stderr}",
    )


def _of_kind(ran: Ran, kind: str) -> list[Envelope]:
    """Every envelope of one kind, in the order the run streamed them."""
    return [envelope for envelope in ran.envelopes if envelope.kind == kind]


def _agent_turns(ran: Ran, member: str) -> list[Envelope]:
    """Every turn one member actually took, which is what a schedule firing looks like."""
    return [
        envelope
        for envelope in _of_kind(ran, "turn-started")
        if envelope.labels.get("member") == member and envelope.payload.get("role") == "assistant"
    ]


def test_the_shipped_observer_graph_is_one_the_pinned_reader_accepts() -> None:
    """`graphs/dag-scope.yaml` still loads, which is what every run's watching depends on.

    A document the reader refuses attaches no observer at all: the run is driven, reports
    plain `ACTIVE`, and nothing watches it. That is the failure the whole
    `kind: onejudge` monitor exists to avoid, and it is why the two journeys below —
    which show what refuses — are worth nothing without this one beside them.
    """
    validated = _validated(REPO_ROOT / DAG_SCOPE_GRAPH)

    assert validated.status == 0, (
        f"{DAG_SCOPE_GRAPH} is not a document the pinned reader will run, so every "
        f"launch from this checkout attaches no observer:\n{validated.said}"
    )
    for member in (MONITOR_MEMBER, PACEMAKER_MEMBER):
        assert member in (REPO_ROOT / DAG_SCOPE_GRAPH).read_text(encoding="utf-8"), (
            f"{DAG_SCOPE_GRAPH} no longer declares its `{member}` member"
        )


def test_the_pinned_reader_refuses_an_observer_graph_whose_members_are_all_scheduled(
    tmp_path: Path,
) -> None:
    """Converting the monitor to the pacemaker's shape leaves nothing to pace the graph.

    The reader is explicit about why, and about the only two ways out — every member
    taking an immediate first turn, or a member outside the schedules. Neither gives the
    monitor the deferred first turn a scheduled repair would want, which is the whole of
    why that repair is unavailable on this release.
    """
    refused = _validated(
        _observer_graph_with_a_scheduled_monitor(tmp_path / "all-scheduled.yaml", immediate=False)
    )

    assert refused.status != 0, (
        "the pinned reader now accepts an observer graph whose members are all "
        "scheduled, so the constraint this journey and `docs/orchestration.md` record "
        f"has been lifted upstream and the write-up is due a re-measurement:\n{refused.said}"
    )
    assert REFUSAL_NAMES_THE_QUIESCE in refused.said, refused.said
    assert REFUSAL_NAMES_THE_DEFERRED in refused.said, refused.said
    for remedy in REFUSAL_NAMES_THE_REMEDIES:
        assert remedy in refused.said, (
            f"the refusal no longer names {remedy!r} as a way out, so the write-up's "
            f"account of what it offers is out of date:\n{refused.said}"
        )
    assert MONITOR_MEMBER in refused.said, (
        "the refusal no longer names the member whose first turn never comes due, which "
        f"is what says the monitor is the one being refused:\n{refused.said}"
    )


# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] `xdist_group` selects
# an xdist worker under this suite's `--dist loadgroup`, not a test tier; the tiers here
# split by what a test reads, which is what each one's Nx cache key has to cover.
@pytest.mark.xdist_group("observer-graph-liveness")
def test_the_remedy_that_refusal_names_settles_the_observer_after_one_turn_each(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The all-scheduled graph that *does* load stops watching almost immediately.

    This is the shape a repair would be left with once the reader has had its way, and
    running it is what shows the cost: both members fire once, both settle, and the graph
    settles with them — minutes before either schedule comes round again. Nothing
    relaunches it, so a run wired this way is watched for one turn and driven, unwatched,
    for the rest of its life.
    """
    graph = _observer_graph_with_a_scheduled_monitor(tmp_path / "immediate.yaml", immediate=True)
    accepted = _validated(graph)
    assert accepted.status == 0, (
        "the remedy the refusal names does not even load, so this journey cannot show "
        f"what it costs:\n{accepted.said}"
    )

    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    # llmlint: ignore[live_tier_compiles_and_requires_credential] Same: the boundary under
    # test is the graph reader, and a credentialed turn would prove nothing more about it.
    environment = probe_environment(tmp_path, oneharness_bin, INDIRECTION_CALLER)
    ran = _ran(graph, environment, tmp_path)

    settled = _of_kind(ran, "graph-settled")
    assert settled, f"the probe observer graph never settled at all:\n{ran.said}"
    for member in (MONITOR_MEMBER, PACEMAKER_MEMBER):
        turns = _agent_turns(ran, member)
        assert len(turns) == 1, (
            f"the `{member}` member took {len(turns)} turn(s) in a graph that settled "
            "immediately; if it now takes more, a scheduled member is being paced by "
            f"something and this journey's account of the cost is wrong:\n{ran.said}"
        )
    started = _of_kind(ran, "graph-started")[0].ts
    finished = settled[0].ts
    watched_for = _seconds_between(started, finished)
    assert watched_for < SETTLED_WITHOUT_A_SECOND_TICK_SECONDS, (
        f"the probe graph watched for {watched_for:.1f}s, which is long enough that it "
        "may have waited for a second tick; this journey can no longer tell a one-shot "
        f"observer from a paced one:\n{ran.said}"
    )
    members = settled[0].payload.get("members")
    assert isinstance(members, dict), settled[0].payload
    assert set(members) == {MONITOR_MEMBER, PACEMAKER_MEMBER}, members


# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]


def _seconds_between(started: str, finished: str) -> float:
    """How long the run lasted, from the two timestamps its own envelopes carry."""
    read = "%Y-%m-%dT%H:%M:%S.%f%z"
    opened = datetime.strptime(started.replace("Z", "+0000"), read)
    closed = datetime.strptime(finished.replace("Z", "+0000"), read)
    return (closed - opened).total_seconds()


@pytest.mark.reads_docs
def test_the_write_up_quotes_the_refusal_the_reader_actually_prints(tmp_path: Path) -> None:
    """The documented finding is held to the tool, not to the memory of having run it.

    `docs/orchestration.md` quotes the reader's refusal as the evidence that the monitor
    cannot be a scheduled member here. A quote is exactly the kind of claim that outlives
    the release it was taken from, so it is compared against what the reader says today —
    which makes a reword upstream a failing check rather than a paragraph quietly
    describing a message nothing produces.
    """
    refused = _validated(
        _observer_graph_with_a_scheduled_monitor(tmp_path / "all-scheduled.yaml", immediate=False)
    )
    written = (REPO_ROOT / FINDING_DOCUMENT).read_text(encoding="utf-8")

    quoted = REFUSAL_NAMES_THE_QUIESCE
    assert quoted in " ".join(written.split()), (
        f"{FINDING_DOCUMENT} no longer quotes the refusal it records, so a reader has "
        "nothing to compare against the reader's own words"
    )
    assert quoted in refused.said, (
        f"{FINDING_DOCUMENT} quotes a refusal the pinned reader no longer prints:\n{refused.said}"
    )
