"""Continuous, aggregated event stream for one tracked-graph run.

`just runs` says where a run *ended*; `just history-show` says everything about
one thing in it. Neither answers the question you actually have while a graph is
running — "what is happening right now, across all of it?" — because a round's
activity is scattered across four stores that settle at different times:

* the **run journal** (`runs/<run-id>/events.jsonl`) — every node transition;
* **oneharness history** — the dispatched sessions, found by the run labels the
  journal's own scopes stamped on them;
* **git** — the commits sitting on each lifecycle branch;
* **GitHub** — the state of each PR the lifecycle linked.

This module folds those into one ordered stream. Two properties make it usable
against a live run rather than only a finished one:

* **Dedup by durable source identity.** Every source is *polled*, so each pass
  re-reads things it has already reported. An observation is therefore keyed by
  something the source itself guarantees is stable — a journal sequence, a commit
  sha, a PR's state signature — rather than by its position in a pass. A commit is
  reported once, ever; a PR is reported again exactly when its state changes.
* **Never lose the executor's own evidence.** The monitor reads; it never writes
  to the ledger or journal, takes no lock a writer needs, and treats every source
  as optional. A missing `gh`, an unfetched branch, or an absent history store
  degrades that source to silence instead of ending the stream.

Only the journal is authoritative for *what happened*. Git and GitHub are remote
state that outlives the round but is not reproducible from the run directory, so
what they report is persisted as a **detail snapshot** (`runs/<run-id>/monitor/`)
— that is what makes a replay of a finished run show the same commits and PRs as
the live session that first observed them, without re-reaching the network.

Every emitted line carries exactly one strict typed id from `ids`, which is
precisely the argument `just history-show` resolves. That is the whole contract
between this concise view and the full one: the monitor never tries to *be* the
detail, it tells you the id to ask for.

A third property makes it usable by the planner rather than only by a person:
**it returns.** Following is a terminal affordance — flushed line by line, ended
with Ctrl-C — so off a terminal this command makes one bounded pass and exits
(`follows`), and every external source it crosses to carries a deadline
(`SOURCE_TIMEOUT_SECONDS`). Without both, the command `launch.json` advertises
was one an automated supervisor could not use at all.

`--until-settled` is the third way it can end, and the one a foreground `just
orchestrate` waits on: attach, stream, and return **when the run settles** — see
`SETTLEMENTS` for what that word means here and why it is neither "the round
finished" nor "the orchestrator exited".
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import re
import sys
import time
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, TextIO

from . import gitops
from .channel import ChannelError, due_indicator
from .config import ConfigError
from .coordination import advisory_lock, atomic_json
from .detail_snapshot import SNAPSHOT_VERSION, CheckRollup, CommitDetail, PrDetail
from .github import Check, CliGitHubBackend, GitHubBackend, GitHubError, PRStatus, PullRequest
from .history import HistoryError, HistorySession, session_records, worker_sessions
from .ids import DetailId, DetailIdError, GitId, GraphId, OneharnessId, PrId
from .journal import (
    JOURNAL_NAME,
    ROUND_EVENT_KINDS,
    DetailValue,
    Event,
    read_events,
)
from .liveness import PARKED_AFTER_SECONDS, parked_indicator
from .registry import Registry, RegistryError
from .runs import (
    GraphPayload,
    GraphResultItem,
    RunId,
    abandoned_launch,
    abandoned_round,
    as_result_payload,
    latest_round,
    launch_claims_a_live_owner,
    load_mapping,
    resolve_supervision_run,
    result_state,
    round_appears_in_flight,
    rounds,
    validate_run_id,
)

# The first line of the text stream, verbatim. It is a contract rather than a
# banner: it names the one command that turns any id in the stream into the full
# record, which is what lets every following line stay one capped line.
HEADER = "Concise graph events; run just history-show <stream-id> for full detail."

DEFAULT_RUNS_DIR = Path("runs")
DEFAULT_HEARTBEAT = 60.0
DEFAULT_POLL_INTERVAL = 2.0
DEFAULT_MAX_POLL_INTERVAL = 30.0

#: How long one poll may wait on a single external source before giving up on it.
#: The two that are not local files — oneharness history, a subprocess, and `gh`,
#: the network — had no deadline, so either could hold a pass open indefinitely.
#: Both already degrade to silence when absent; an expired read is that silence.
SOURCE_TIMEOUT_SECONDS = 30.0

#: What a non-following invocation is held to end to end, derived from the deadlines
#: above rather than picked: history once, `gh` at worst an in-flight call plus its
#: whole-source budget, and slack for the local reads. Nothing here enforces it —
#: `tests/e2e/test_monitor_e2e.py` does, on a runs root of realistic size — so it
#: lives beside the deadlines it is derived from and moves only when they do.
RETURN_BOUND_SECONDS = 3 * SOURCE_TIMEOUT_SECONDS + 30.0

# A summary is a *scan target*, not prose: it sits beside a typed id that already
# leads to the full record, so it is capped hard enough to stay one terminal line
# next to that id.
SUMMARY_LIMIT = 96
_ELLIPSIS = "..."

MONITOR_DIR = "monitor"
SNAPSHOT_NAME = "details.json"
# The run-label key a dispatched session is stamped with. `NodeJournal.labels`
# renders it through `graph_labels`, so filtering history on it here selects
# exactly the sessions this run's own scopes labelled.
RUN_LABEL = "run_id"

_PR_URL_NUMBER = re.compile(r"/pull/(\d+)/?\Z")

# A local direct-merge repo has no GitHub PR to poll: `LocalMergeStrategy`
# synthesizes one so the result has a stable ref, and asking `gh` about it would
# be a guaranteed error rather than a missing-network degradation.
LOCAL_IDENTITY_PREFIX = "local/"

Source = Literal["journal", "history", "git", "pr"]

# The graph states that mean "this run is finished and nothing is left to watch".
# Everything else — waiting on a human, failed, or an executor that stopped
# without recording anything — keeps the stream open, because all of them are
# states a person acts on and then the run continues.
COMPLETE_STATE = "complete"

# A settle-terminating attach has to end at exactly the right moment, and the
# obvious readings are all wrong in a way that costs a planner the run:
#
# * *the round finished* returns while the orchestrator is still scheduling — the
#   caller walks away from a graph that is about to start round two;
# * *the orchestrator exited* never returns on the ordinary case, because the
#   process stays alive holding a question nobody is answering.
#
# So settled is a property of the **run**, not of a round or a process: the run is
# no longer advancing on its own, and the next move belongs to the planner. Three
# conditions say that, and nothing else does.

Settlement = Literal["complete", "awaiting-planner", "unattended"]

#: The graph completed successfully. Nothing is left to watch.
SETTLED_COMPLETE: Settlement = "complete"

#: A *blocking* planner surface is pending: the orchestrator has asked a question
#: and will not move until `just channel-reply` answers it. A non-blocking surface
#: is deliberately not this — the orchestrator continues without waiting for a
#: reply to a heartbeat, so returning there would abandon a working run.
SETTLED_AWAITING_PLANNER: Settlement = "awaiting-planner"

#: Nothing is driving the run any more: its launch is parked, its round was
#: abandoned, or its executor is gone with the graph unfinished. All of them are
#: states a planner must act on, and all of them look like a stream that has simply
#: gone quiet — which is the picture this whole mode exists to replace.
SETTLED_UNATTENDED: Settlement = "unattended"

#: Every settlement, and the exit status the attach reports it with. Zero for the
#: two a planner *expects* to reach; non-zero for the one that means intervene,
#: so a scripted launch fails loudly rather than reporting a run nobody is driving
#: as a clean finish.
SETTLEMENTS: Mapping[Settlement, int] = {
    SETTLED_COMPLETE: 0,
    SETTLED_AWAITING_PLANNER: 0,
    SETTLED_UNATTENDED: 3,
}


class MonitorError(Exception):
    """A run cannot be monitored, or an emitted event violates its contract."""


def _strip_controls(value: str) -> str:
    """Replace every Cc control character with a space.

    Substituted rather than dropped because a newline or a tab is a *separator*.
    Deleting one welds the last word of a line onto the first word of the next and
    yields a token that appeared in neither — a gate's ``failed\\nassert x == 1``
    would reach the reader as ``failedassert x == 1``, which is worse than useless
    on a line whose whole job is to be scanned. `summarize` collapses the runs this
    leaves behind, so one event is still one line.
    """
    return "".join(" " if unicodedata.category(ch) == "Cc" else ch for ch in value)


def summarize(value: str) -> str:
    """Render one control-stripped, single-line summary capped at `SUMMARY_LIMIT`.

    Summaries are built from recorded status/result values, which are themselves
    untrusted-ish: a gate's stderr, an agent's own text, and a git subject all
    reach here and any of them can carry a newline or an escape sequence. One
    event must stay one line, so the stripping happens here rather than being
    remembered at each of the four sources.
    """
    line = " ".join(_strip_controls(value).split())
    if len(line) <= SUMMARY_LIMIT:
        return line
    return line[: SUMMARY_LIMIT - len(_ELLIPSIS)] + _ELLIPSIS


@dataclass(frozen=True)
class MonitorEvent:
    """One observation, addressed by exactly one strict typed id.

    ``key`` is the **durable source identity** the dedup runs on, and it is the
    source's own stable name for this observation — never a counter of what this
    process has seen. That is what makes the stream idempotent across restarts and
    across the overlapping passes of a poll loop: re-reading a journal, a branch,
    or a PR produces the same keys, so nothing is reported twice.
    """

    at: float
    source: Source
    kind: str
    stream_id: DetailId
    summary: str
    key: str

    def __post_init__(self) -> None:
        if len(self.summary) > SUMMARY_LIMIT:
            raise MonitorError(f"summary exceeds {SUMMARY_LIMIT} characters: {self.summary!r}")
        if self.summary != _strip_controls(self.summary):
            raise MonitorError(f"summary contains a control character: {self.summary!r}")
        if not self.key:
            raise MonitorError("monitor event key must be a non-empty string")

    def text(self) -> str:
        stamp = datetime.fromtimestamp(self.at, UTC).strftime("%H:%M:%S")
        return f"{stamp}  {self.stream_id}  {self.summary}"

    def record(self, *, next_poll_seconds: float | None = None) -> dict[str, DetailValue]:
        record: dict[str, DetailValue] = {
            "type": "event",
            "at": self.at,
            "source": self.source,
            "kind": self.kind,
            "id": str(self.stream_id),
            "summary": self.summary,
        }
        if next_poll_seconds is not None:
            record["next_poll_seconds"] = next_poll_seconds
        return record


@dataclass(frozen=True)
class Heartbeat:
    """A liveness line for a run that is open but has produced nothing new.

    It carries no typed id on purpose: a heartbeat is the *absence* of a graph
    event, and inventing an id for it would put a value in the stream that
    `just history-show` cannot resolve — breaking the header's promise.
    """

    at: float
    run_id: RunId
    round: int | None
    state: str
    detail: str
    last_completed_check: str = ""
    current_blocker: str = ""
    next_poll_seconds: float = 0.0
    #: The settlement this beat reports, when it is the last one of a settle-
    #: terminating attach. Absent on every other beat, so a consumer can tell "this
    #: is where it stands" from "this is where it stopped".
    settlement: Settlement | None = None

    def text(self) -> str:
        stamp = datetime.fromtimestamp(self.at, UTC).strftime("%H:%M:%S")
        where = f"round-{self.round:02d}" if self.round is not None else "no round"
        return f"{stamp}  --  {self.run_id} {where} {self.state}: {self.detail}"

    def record(self) -> dict[str, DetailValue]:
        record: dict[str, DetailValue] = {
            "type": "heartbeat",
            "at": self.at,
            "run_id": self.run_id,
            "round": self.round,
            "state": self.state,
            "detail": self.detail,
        }
        if self.settlement is not None:
            record["settlement"] = self.settlement
        if self.last_completed_check:
            record["last_completed_check"] = self.last_completed_check
        if self.current_blocker:
            record["current_blocker"] = self.current_blocker
        if self.next_poll_seconds:
            record["next_poll_seconds"] = self.next_poll_seconds
        return record


# --- persisted detail snapshots ------------------------------------------------


@dataclass
class DetailSnapshot:
    """Commit and PR details this run has observed, keyed by their typed id.

    The journal is reproducible from the run directory forever; git and GitHub are
    not. A branch is deleted after its PR merges and a clone is thrown away, so a
    replay that re-derived commits from git would show *fewer* of them the longer
    ago the run was — the opposite of a durable record. Persisting what was seen,
    keyed by the same typed id the stream reports, makes replay stable.
    """

    commits: dict[str, dict[str, DetailValue]] = field(default_factory=dict)
    prs: dict[str, dict[str, DetailValue]] = field(default_factory=dict)
    check_rollup: CheckRollup = field(default_factory=CheckRollup)

    def to_record(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "version": SNAPSHOT_VERSION,
            "commits": self.commits,
            "prs": self.prs,
        }
        if rollup := self.check_rollup.to_record():
            record["check_rollup"] = rollup
        return record


def _detail_map(
    value: object, parser: Callable[[object], CommitDetail | PrDetail | None]
) -> dict[str, dict[str, DetailValue]]:
    """Read one persisted section, skipping anything that is not a detail mapping."""
    if not isinstance(value, dict):
        return {}
    return {
        key: parsed.to_record()
        for key, item in value.items()
        if isinstance(key, str) and key and (parsed := parser(item)) is not None
    }


def snapshot_path(run_dir: Path) -> Path:
    return run_dir / MONITOR_DIR / SNAPSHOT_NAME


def load_snapshot(run_dir: Path) -> DetailSnapshot:
    """Read the persisted snapshot; an unreadable one is treated as empty.

    A snapshot is an *optimization over observation*, never evidence a decision is
    made on, so a corrupt or future-versioned file degrades to "nothing observed
    yet" rather than failing a monitor of a live run.
    """
    path = snapshot_path(run_dir)
    if not path.exists():
        return DetailSnapshot()
    try:
        raw = load_mapping(path)
    except (ConfigError, OSError):
        return DetailSnapshot()
    if raw.get("version") != SNAPSHOT_VERSION:
        return DetailSnapshot()
    return DetailSnapshot(
        commits=_detail_map(raw.get("commits"), CommitDetail.from_value),
        prs=_detail_map(raw.get("prs"), PrDetail.from_value),
        check_rollup=CheckRollup.from_value(raw.get("check_rollup")) or CheckRollup(),
    )


def save_snapshot(run_dir: Path, snapshot: DetailSnapshot) -> None:
    """Persist the snapshot atomically; a write failure never ends the stream."""
    path = snapshot_path(run_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with advisory_lock(f"monitor:{path}"):
            atomic_json(path, snapshot.to_record())
    except OSError:
        return


# --- the ledger a run's sources are discovered from ----------------------------


@dataclass(frozen=True)
class RoundLedger:
    """One recorded round's payload, as the ledger persisted it."""

    number: int
    payload: GraphPayload


def _round_ledgers(run_dir: Path) -> list[RoundLedger]:
    """Every round of a run that has recorded a result, oldest first.

    A round that is still running has no ``result.json``; its activity reaches the
    stream through the journal instead, which is exactly why the journal is the
    authoritative source and this one only widens it.
    """
    found: list[RoundLedger] = []
    for number, round_dir in rounds(run_dir):
        path = round_dir / "result.json"
        if not path.exists():
            continue
        try:
            found.append(RoundLedger(number, as_result_payload(load_mapping(path))))
        except (ConfigError, OSError):
            continue
    return found


def _pr_number(value: object) -> int | None:
    if not isinstance(value, str) or (match := _PR_URL_NUMBER.search(value)) is None:
        return None
    return int(match.group(1))


def _text(item: GraphResultItem, key: str) -> str | None:
    value = item.get(key)
    return value if isinstance(value, str) and value else None


@dataclass(frozen=True)
class BranchRef:
    """One lifecycle branch, qualified by the repository it lives in."""

    identity: str
    branch: str
    base: str


def known_branches(ledgers: Iterable[RoundLedger], events: Iterable[Event]) -> list[BranchRef]:
    """Every lifecycle branch this run recorded, from the ledger and the journal.

    Both are read because they become true at different times: the journal records
    ``branch-discovered`` the moment a worktree is cut, while the ledger only says
    so once the round has *finished*. A monitor that waited for the ledger could
    not report a branch until there was nothing left to report.
    """
    found: dict[tuple[str, str], BranchRef] = {}
    for ledger in ledgers:
        for item in ledger.payload["results"].values():
            identity = _text(item, "repo")
            branch = _text(item, "branch")
            base = _text(item, "base_branch") or _text(item, "pr_base")
            if identity and branch and base:
                found[(identity, branch)] = BranchRef(identity, branch, base)
    identities = {ref.identity for ref in found.values()}
    for event in events:
        if event.kind != "branch-discovered":
            continue
        detail_branch = event.detail.get("branch")
        detail_base = event.detail.get("base_branch") or event.detail.get("pr_base")
        if (
            not isinstance(detail_branch, str)
            or not isinstance(detail_base, str)
            or not detail_branch
            or not detail_base
        ):
            continue
        detail_identity = event.detail.get("repo")
        candidates = (
            [detail_identity]
            if isinstance(detail_identity, str) and detail_identity
            else sorted(identities)
        )
        # Current lifecycle writers carry the normalized repository directly, so
        # the branch is observable before a running round has a result ledger. The
        # ledger fallback keeps older journals replayable.
        for identity in candidates:
            found.setdefault(
                (identity, detail_branch), BranchRef(identity, detail_branch, detail_base)
            )
    return sorted(found.values(), key=lambda ref: (ref.identity, ref.branch))


@dataclass(frozen=True)
class PrRef:
    """One PR the lifecycle linked, qualified by the repository it was opened in."""

    identity: str
    number: int
    url: str
    base: str


def known_prs(ledgers: Iterable[RoundLedger], events: Iterable[Event]) -> list[PrRef]:
    """Every PR this run linked, from the ledger and the journal.

    A synthesized local PR (number 0) is excluded by `PrId`'s own grammar, which
    only admits a positive number — the same rule that keeps ``pr:owner/name#0``
    out of `history-show`.
    """
    found: dict[tuple[str, int], PrRef] = {}
    for ledger in ledgers:
        for item in ledger.payload["results"].values():
            identity = _text(item, "repo")
            url = _text(item, "pr")
            number = _pr_number(url)
            base = _text(item, "pr_base") or _text(item, "base_branch") or ""
            if identity and url and number is not None and number >= 1:
                found[(identity, number)] = PrRef(identity, number, url, base)
    identities = {ref.identity for ref in found.values()}
    for event in events:
        detail_url = event.detail.get("pr")
        detail_number = _pr_number(detail_url)
        if detail_number is None or not isinstance(detail_url, str):
            continue
        detail_base = event.detail.get("base")
        detail_identity = event.detail.get("repo")
        candidates = (
            [detail_identity]
            if isinstance(detail_identity, str) and detail_identity
            else sorted(identities)
        )
        for identity in candidates:
            found.setdefault(
                (identity, detail_number),
                PrRef(
                    identity,
                    detail_number,
                    detail_url,
                    detail_base if isinstance(detail_base, str) else "",
                ),
            )
    return sorted(found.values(), key=lambda ref: (ref.identity, ref.number))


def ledger_checkouts(ledgers: Iterable[RoundLedger]) -> dict[str, Path]:
    """Where each repository's work actually happened, as the ledger recorded it.

    The registry names a repository's *publication* checkout, which is not where a
    lifecycle branch's commits live when the round ran against a separate execution
    checkout — the self-dispatch case, where the two deliberately differ. Reading the
    recorded path is the only way to point git at the clone that has the branch.
    """
    found: dict[str, Path] = {}
    for ledger in ledgers:
        for item in ledger.payload["results"].values():
            identity = _text(item, "repo")
            checkout = _text(item, "execution_checkout")
            if identity and checkout:
                found[identity] = Path(checkout)
    return found


# --- sources -------------------------------------------------------------------

# The recorded values a summary is derived from, in the order they are rendered.
# This is an allowlist rather than "whatever is in the detail": a payload is open
# at the leaves by design, so rendering it whole would put an unbounded blob on a
# line that must stay scannable.
_SUMMARY_KEYS = (
    "status",
    "outcome",
    "state",
    "ok",
    "node_kind",
    "step_kind",
    "persona",
    "branch",
    "base_branch",
    "number",
    "draft",
    "turns",
    "preserved",
    "resumed",
    "error",
    "command",
    "task",
    "detail",
)


def _scalar(value: DetailValue) -> str | None:
    """Render a detail leaf, skipping the containers a summary cannot hold."""
    match value:
        case bool():
            return "yes" if value else "no"
        case str():
            return value or None
        case int() | float():
            return str(value)
        case _:
            return None


def _journal_summary(event: Event) -> str:
    parts: list[str] = [event.kind]
    if event.step:
        parts.append(f"[{event.step}]")
    parts.extend(
        f"{key}={rendered}"
        for key in _SUMMARY_KEYS
        if (value := event.detail.get(key)) is not None and (rendered := _scalar(value))
    )
    return summarize(" ".join(parts))


def journal_events(run_id: RunId, run_dir: Path) -> list[MonitorEvent]:
    """The run's own recorded node transitions — the authoritative source.

    Round transitions are deliberately not events here. The journal guarantees a
    node locator for every *other* kind, and this stream's contract is that each
    line resolves through one typed id; a round has no node, so it has no
    `graph:` id, and it reaches the reader as run state (a heartbeat) instead of
    as a line pointing at an id that cannot exist.
    """
    found: list[MonitorEvent] = []
    for event in read_events(run_dir / JOURNAL_NAME):
        if event.run_id != run_id or event.kind in ROUND_EVENT_KINDS or event.node is None:
            continue
        try:
            ref = GraphId(run_id=event.run_id, round=event.round, node=event.node)
        except DetailIdError:
            # A node id the typed-id grammar cannot render is un-addressable, so a
            # line for it could not honour the header's promise. Skip it rather
            # than emit a `history-show` argument that would not resolve.
            continue
        found.append(
            MonitorEvent(
                at=event.at,
                source="journal",
                kind=event.kind,
                stream_id=ref,
                summary=_journal_summary(event),
                # The journal's own sequence is unique per run and stable on disk,
                # which is exactly the durability the dedup needs.
                key=f"journal:{event.run_id}:{event.seq}",
            )
        )
    return found


def run_sessions(
    run_id: RunId, *, oneharness_bin: str = "oneharness", timeout: float | None = None
) -> list[HistorySession]:
    """The dispatched worker sessions labelled as belonging to ``run_id``.

    This is the label filter the whole history source rests on: a dispatch is
    stamped with its run/round/node by `NodeJournal.labels`, so selecting on the
    run label returns this run's sessions and nothing else — no path heuristics,
    no name matching against a task string a persona chose.
    """
    return [
        session
        for session in worker_sessions(oneharness_bin=oneharness_bin, timeout=timeout)
        if session.labels.get(RUN_LABEL) == run_id
    ]


def history_events(
    run_id: RunId,
    *,
    now: float,
    oneharness_bin: str = "oneharness",
    timeout: float | None = None,
) -> list[MonitorEvent]:
    """Watch oneharness history for sessions labelled with this run.

    A missing, failing, or too-slow history store degrades to silence: the journal
    already reports the node that dispatched the session, so losing this source
    costs detail rather than the transition itself.
    """
    try:
        sessions = run_sessions(run_id, oneharness_bin=oneharness_bin, timeout=timeout)
    except HistoryError:
        return []
    found: list[MonitorEvent] = []
    for session in sessions:
        try:
            records = session_records(session)
        except HistoryError:
            continue
        latest = records[-1] if records else {}
        # v0.2 records own a stable UUIDv7 identity.  The list envelope keeps its
        # filename-derived ``id`` for backwards compatibility, so take the typed
        # detail identity from the record itself.  Labelled graph history is
        # necessarily v0.2; the fallback only keeps hand-written/legacy fixtures
        # and old stores inspectable.
        history_id = latest.get("history_id", session.session_id)
        if not isinstance(history_id, str):
            continue
        try:
            ref = OneharnessId(history_id=history_id)
        except DetailIdError:
            continue
        status = str(latest.get("status", "unknown"))
        node = session.labels.get("node", "?")
        found.append(
            MonitorEvent(
                at=now,
                source="history",
                kind="session",
                stream_id=ref,
                summary=summarize(f"session {node} {status} turns={len(records)} {session.name}"),
                # Re-emit exactly when the session's own status or turn count moves:
                # those are the values the summary is derived from, so a key over
                # them reports every change and nothing else.
                key=f"history:{history_id}:{status}:{len(records)}",
            )
        )
    return found


def _checkouts(registry: Registry) -> dict[str, Path]:
    """Map each registered repository slug to its checkout path.

    Read-only on purpose: `Registry.resolve` would *clone* an unregistered repo,
    and a viewing command must never make a network call the user did not ask for
    to answer "what is happening right now".
    """
    return {str(slug): Path(entry.path) for slug, entry in registry.entries.items()}


def _persisted_commits(snapshot: DetailSnapshot, ref: BranchRef) -> list[gitops.Commit]:
    """The commits a previous pass already recorded for this branch."""
    found: list[gitops.Commit] = []
    for record in snapshot.commits.values():
        sha = record.get("sha")
        subject = record.get("subject")
        if (
            record.get("identity") == ref.identity
            and record.get("branch") == ref.branch
            and isinstance(sha, str)
            and isinstance(subject, str)
        ):
            found.append(gitops.Commit(sha, subject))
    return found


def branch_commits(
    ref: BranchRef, checkouts: Mapping[str, Path], snapshot: DetailSnapshot
) -> list[gitops.Commit]:
    """Every commit known for one branch: what was persisted, plus what git shows now.

    The two are *unioned* rather than one being preferred, because each is
    incomplete in exactly the way the other covers. Git is the only source for a
    commit pushed since the last pass. The snapshot is the only source for one whose
    branch has since been deleted after its merge, or whose commits ``base..branch``
    no longer lists precisely because they are now *in* the base — so re-deriving
    from git alone would report fewer commits the longer ago the run was, which is
    the opposite of a durable record.

    Deduping by sha is what makes the union safe: a commit is immutable, so the two
    sources naming it agree, and the reader sees it once.
    """
    found = {commit.sha: commit for commit in _persisted_commits(snapshot, ref)}
    checkout = checkouts.get(ref.identity)
    if checkout is None or not checkout.is_dir():
        return list(found.values())
    try:
        live = gitops.log_delta(checkout, ref.base, ref.branch)
    except gitops.GitError:
        # An unfetched base, a branch already deleted after its merge, or a clone
        # that has moved. All are normal, and none is worth ending a stream whose
        # other three sources still work — the snapshot still carries what was seen.
        return list(found.values())
    for commit in live:
        found.setdefault(commit.sha, commit)
    return list(found.values())


def git_events(
    branches: Iterable[BranchRef],
    checkouts: Mapping[str, Path],
    snapshot: DetailSnapshot,
    *,
    now: float,
) -> list[MonitorEvent]:
    """The commits sitting on each known lifecycle branch.

    A commit is immutable, so its sha *is* its durable identity: the key needs no
    state signature and the event fires exactly once, on the pass that first sees
    it, no matter how many passes re-read the branch.
    """
    found: list[MonitorEvent] = []
    for ref in branches:
        for commit in branch_commits(ref, checkouts, snapshot):
            try:
                git_ref = GitId(identity=ref.identity, sha=commit.sha)
            except DetailIdError:
                continue
            key = str(git_ref)
            checkout = checkouts.get(ref.identity)
            detail = ""
            if checkout is not None:
                with contextlib.suppress(gitops.GitError):
                    detail = gitops.commit_detail(checkout, commit.sha)
            snapshot.commits[key] = CommitDetail(
                sha=commit.sha,
                subject=commit.subject,
                branch=ref.branch,
                base=ref.base,
                identity=ref.identity,
                detail=detail,
            ).to_record()
            found.append(
                MonitorEvent(
                    at=now,
                    source="git",
                    kind="commit",
                    stream_id=git_ref,
                    summary=summarize(f"commit {ref.branch} {commit.subject}"),
                    key=f"git:{key}",
                )
            )
    return found


def _pr_signature(status_state: str, merged: bool, draft: bool, merge_state: str) -> str:
    return f"{status_state}:{merged}:{draft}:{merge_state}"


def _ordered_checks(checks: Iterable[Check]) -> list[Check]:
    """Give check observations a stable order independent of GitHub's rollup order."""
    return sorted(
        checks,
        key=lambda check: (
            check.name.casefold(),
            check.name,
            not check.required,
            check.state,
        ),
    )


def _check_key(ref: PrId, check: Check, revision: int, state: str | None = None) -> str:
    """A collision-safe identity for one check observation under a PR detail id."""
    observation = json.dumps(
        {
            "name": check.name,
            "required": check.required,
            "revision": revision,
            "state": state or check.state,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"pr-check:{ref}:{observation}"


def _check_event(
    ref: PrId,
    number: int,
    check: Check,
    *,
    now: float,
    revision: int,
    removed: bool = False,
) -> MonitorEvent:
    classification = "required" if check.required else "optional"
    state = "removed" if removed else check.state.lower()
    return MonitorEvent(
        at=now,
        source="pr",
        kind="pr-check",
        stream_id=ref,
        summary=summarize(f"PR #{number} {classification} check {state} {check.name}"),
        key=_check_key(ref, check, revision, "REMOVED" if removed else None),
    )


def persisted_status(snapshot: DetailSnapshot, ref: PrId) -> PRStatus | None:
    """The PR state a previous pass persisted, for a replay `gh` cannot serve.

    Returned as a `PRStatus` — the same type the live backend returns — rather than
    as a rendered line, so a replayed PR runs through exactly the same signature and
    summary code as a live one. A replay that rendered itself would be free to drift
    from the thing it is replaying.
    """
    detail = PrDetail.from_value(snapshot.prs.get(str(ref)))
    return detail.status() if detail is not None else None


def pr_events(
    prs: Iterable[PrRef],
    snapshot: DetailSnapshot,
    *,
    now: float,
    github: GitHubBackend | None = None,
    replay: bool = True,
    timeout: float | None = None,
) -> list[MonitorEvent]:
    """The current state of each PR the lifecycle linked.

    PR lifecycle state and individual checks have separate durable signatures. That
    keeps an optional-only check transition visible and lets every check summary say
    whether it gates the merge, without making the PR-state line unbounded.

    ``timeout`` bounds this whole source rather than only each call: it caps each
    `gh` call *and* stops asking once the source has spent that budget, because a
    run with many linked PRs would otherwise cost one timeout per PR. Either way
    the answer is the persisted state, exactly as an absent `gh` already gives.
    """
    backend = github if github is not None else CliGitHubBackend(timeout=timeout)
    deadline = None if timeout is None else time.monotonic() + timeout
    found: list[MonitorEvent] = []
    for ref in prs:
        if ref.identity.startswith(LOCAL_IDENTITY_PREFIX):
            continue
        try:
            pr_ref = PrId(identity=ref.identity, number=ref.number)
        except DetailIdError:
            continue
        key = str(pr_ref)
        previous = PrDetail.from_value(snapshot.prs.get(key))
        pull = PullRequest(
            number=ref.number, url=ref.url, repo=ref.identity, head="", base=ref.base
        )
        try:
            # A spent budget is raised rather than branched on, because "GitHub did
            # not answer for this PR" is one outcome with one handling however it
            # came about, and splitting it would let the two drift.
            if deadline is not None and time.monotonic() >= deadline:
                raise GitHubError(f"pr source exhausted its {timeout:g}s budget")
            status = backend.status(pull)
        except (GitHubError, json.JSONDecodeError, OSError):
            # No `gh`, no auth, no network, or no time left. Fall back to the state a
            # previous pass persisted, so replaying a finished run still shows the PR
            # it reported live. With nothing persisted either, this source degrades to
            # silence rather than ending a stream whose journal still has the
            # transitions.
            fallback = persisted_status(snapshot, pr_ref)
            if fallback is None:
                continue
            status = fallback
        current = _ordered_checks(status.checks)
        signature = _pr_signature(
            status.state, status.merged, status.draft, status.merge_state_status
        )
        previous_signature = (
            _pr_signature(
                previous.state,
                previous.merged,
                previous.draft,
                previous.merge_state_status,
            )
            if previous is not None
            else None
        )
        previous_checks = _ordered_checks(previous.checks) if previous is not None else []
        previous_by_name = {check.name: check for check in previous_checks}
        newly_completed = [
            check.name
            for check in current
            if check.settled
            and not check.red
            and (
                previous_by_name.get(check.name) is None or not previous_by_name[check.name].settled
            )
        ]
        last_completed = (
            newly_completed[-1] if newly_completed else snapshot.check_rollup.last_completed_check
        )
        changed = previous is None or signature != previous_signature or current != previous_checks
        revision = 1 if previous is None else previous.revision + int(changed)
        snapshot.prs[key] = PrDetail.from_status(
            status, url=ref.url, identity=ref.identity, revision=revision
        ).to_record()
        persisted_prs = [
            detail
            for value in snapshot.prs.values()
            if (detail := PrDetail.from_value(value)) is not None
        ]
        blockers = [
            (detail.number, check)
            for detail in persisted_prs
            for check in detail.checks
            if check.required and not (check.settled and not check.red)
        ]
        multiple_prs = len(persisted_prs) > 1
        snapshot.check_rollup = CheckRollup(
            last_completed_check=last_completed,
            current_blocker=(
                (f"PR #{blockers[0][0]} " if multiple_prs else "")
                + f"{blockers[0][1].name}: {blockers[0][1].state.lower()}"
                if blockers
                else ""
            ),
        )
        # A draft PR is OPEN to `gh`, so drafts and ready PRs would render identically
        # and a draft going ready would read as no change at all.
        state = "draft" if status.draft and not status.merged else status.state.lower()
        if replay or previous is None or signature != previous_signature:
            found.append(
                MonitorEvent(
                    at=now,
                    source="pr",
                    kind="pr-state",
                    stream_id=pr_ref,
                    summary=summarize(
                        f"PR #{status.number} {state} {status.merge_state_status.lower()}"
                    ),
                    key=f"pr:{key}:{revision}:{signature}",
                )
            )
        if replay or previous is None:
            observed = current
        else:
            observed = [check for check in current if previous_by_name.get(check.name) != check]
        found.extend(
            _check_event(
                pr_ref,
                status.number,
                check,
                now=now,
                revision=revision,
            )
            for check in observed
        )
        if previous is not None and not replay:
            current_names = {check.name for check in current}
            found.extend(
                _check_event(
                    pr_ref,
                    status.number,
                    check,
                    now=now,
                    revision=revision,
                    removed=True,
                )
                for check in previous_checks
                if check.name not in current_names
            )
    return found


# --- run selection and state ---------------------------------------------------


@dataclass(frozen=True)
class RunState:
    """Where a run stands right now, as the ledger records it."""

    run_id: RunId
    round: int | None
    state: str
    ok: bool
    executor_live: bool
    detail: str
    #: Which of `SETTLEMENTS` this run has reached, or ``None`` while it is still
    #: advancing on its own. Carried on the state rather than recomputed per caller
    #: so the foreground `orchestrate` and `just monitor --until-settled` cannot
    #: disagree about when a run is done with them.
    settlement: Settlement | None = None

    @property
    def finished(self) -> bool:
        """Whether the graph completed successfully and nothing is left to watch."""
        return self.state == COMPLETE_STATE and self.ok

    @property
    def settled(self) -> bool:
        """Whether the run has stopped advancing on its own; see `SETTLEMENTS`."""
        return self.settlement is not None


def nothing_is_driving(run_dir: Path) -> bool:
    """Whether no process is left that could advance this run without a planner.

    The three recorded shapes of "gone" are asked in the order that keeps a working
    run working: a self-recorded abandonment or an owner this host proved dead
    settle it outright, and otherwise a launch that still claims a live owner is
    taken at its word — it may simply be between rounds, which is the moment a
    naive "the round finished" reading would have returned on.

    Past that, the answer is the executor's: a run whose newest round is not in
    flight — or which has no round at all — has nothing running that could record
    the next one. That covers the `run-plan` case too, where there is no launch to
    claim anything and the round *is* the executor.
    """
    if abandoned_round(run_dir) is not None or abandoned_launch(run_dir) is not None:
        return True
    if launch_claims_a_live_owner(run_dir):
        return False
    latest = latest_round(run_dir)
    if latest is None:
        # No round was ever claimed, so only a launch could still claim one. Without
        # a launch record this is a run directory nothing has started yet — a state
        # a reader must not call abandoned.
        return (run_dir / "launch.json").is_file()
    return not round_appears_in_flight(latest[1])


# llmlint: ignore[changed_behavior_has_e2e] real blocking and informational pending surfaces run
# through just monitor e2e; malformed-file tolerance is a deterministic reader boundary.
def run_state(
    run_dir: Path, run_id: RunId, *, parked_after: float = PARKED_AFTER_SECONDS
) -> RunState:
    """Read the run's current state from its newest round.

    A parked launch is reported before anything else the run recorded. Both the
    persisted planner surface and the round's ``running`` status outlive the work
    that wrote them, so a launch that is alive and doing nothing would otherwise be
    rendered as the last thing it *was* doing.
    """
    if (parked := parked_indicator(run_dir, parked_after=parked_after)) is not None:
        parked_round = latest_round(run_dir)
        return RunState(
            run_id,
            parked_round[0] if parked_round else None,
            "parked",
            False,
            False,
            parked,
            SETTLED_UNATTENDED,
        )
    pending = run_dir / "channel" / "planner-pending.json"
    if pending.is_file():
        try:
            surface = load_mapping(pending)
            kind = surface.get("kind", "decision")
            message = surface.get("message", "planner reply required")
            if not isinstance(kind, str) or not isinstance(message, str):
                raise ConfigError("persisted planner surface kind and message must be strings")
            # llmlint: ignore[boundary_inputs_validated] the `isinstance` on the next
            # line is this field's validation, and it runs before the value is used:
            # anything that is not a boolean raises and the run falls back to its
            # recorded round state rather than settling on an unvalidated flag.
            blocking = surface.get("blocking", True)
            if not isinstance(blocking, bool):
                raise ConfigError("persisted planner surface blocking must be boolean")
            required = "ACK REQUIRED" if blocking else "REPLY REQUESTED"
            pending_round = latest_round(run_dir)
            return RunState(
                run_id,
                pending_round[0] if pending_round else None,
                "blocked",
                False,
                True,
                summarize(f"{required}: {kind}: {message}"),
                SETTLED_AWAITING_PLANNER if blocking else None,
            )
        except (ConfigError, OSError):
            pass
    unattended = SETTLED_UNATTENDED if nothing_is_driving(run_dir) else None
    latest = latest_round(run_dir)
    if latest is None:
        return RunState(run_id, None, "unknown", False, False, "no recorded rounds yet", unattended)
    number, round_dir = latest
    result_path = round_dir / "result.json"
    if not result_path.exists():
        live = round_appears_in_flight(round_dir)
        return RunState(
            run_id,
            number,
            "running" if live else "stopped",
            False,
            live,
            "round in progress" if live else "executor stopped without recording a result",
            unattended,
        )
    try:
        payload = as_result_payload(load_mapping(result_path))
    except (ConfigError, OSError) as exc:
        return RunState(
            run_id, number, "unknown", False, False, f"unreadable result: {exc}", unattended
        )
    state = result_state(payload)
    ok = bool(payload.get("ok"))
    counts = ", ".join(
        f"{sum(1 for item in payload['results'].values() if item.get('status') == status)} {status}"
        for status in ("done", "waiting", "blocked", "failed", "skipped")
        if any(item.get("status") == status for item in payload["results"].values())
    )
    complete = state == COMPLETE_STATE and ok
    return RunState(
        run_id,
        number,
        state,
        ok,
        False,
        counts or "no nodes recorded",
        SETTLED_COMPLETE if complete else unattended,
    )


def active_runs(runs_dir: Path) -> list[RunId]:
    """Runs that still have something to watch, newest activity first.

    "Active" is a property of the *run*, not of a live process: a round waiting on
    a human has no executor at all, and it is the single most important thing to
    be watching. So anything that has not completed successfully is active.
    """
    if not runs_dir.is_dir():
        return []
    scored: list[tuple[float, RunId]] = []
    for run_dir in sorted(entry for entry in runs_dir.iterdir() if entry.is_dir()):
        latest = latest_round(run_dir)
        if latest is None:
            continue
        try:
            run_id = validate_run_id(run_dir.name)
        except ConfigError:
            continue
        if run_state(run_dir, run_id).finished:
            continue
        try:
            stamp = latest[1].stat().st_mtime
        except OSError:
            continue
        scored.append((stamp, run_id))
    return [run_id for _, run_id in sorted(scored, key=lambda item: item[0], reverse=True)]


def newest_run(runs_dir: Path) -> RunId | None:
    """The newest run of any state, used only when nothing is active."""
    if not runs_dir.is_dir():
        return None
    scored: list[tuple[float, RunId]] = []
    for run_dir in sorted(entry for entry in runs_dir.iterdir() if entry.is_dir()):
        latest = latest_round(run_dir)
        if latest is None:
            continue
        try:
            scored.append((latest[1].stat().st_mtime, validate_run_id(run_dir.name)))
        except (ConfigError, OSError):
            continue
    return max(scored, key=lambda item: item[0])[1] if scored else None


def resolve_run(runs_dir: Path, run_id: str | None) -> RunId:
    """Resolve the run to watch: the named one, else the newest active one."""
    if run_id is not None:
        try:
            resolved = resolve_supervision_run(runs_dir, run_id)
        except ConfigError as exc:
            raise MonitorError(str(exc)) from exc
        return resolved
    if active := active_runs(runs_dir):
        return active[0]
    if (newest := newest_run(runs_dir)) is not None:
        return newest
    raise MonitorError(
        f"no recorded runs under {runs_dir}/ (pass --runs-dir if they are recorded elsewhere)"
    )


# --- the stream ----------------------------------------------------------------


@dataclass
class Monitor:
    """One run's aggregated stream, with the dedup state a poll loop needs.

    The seen-key set and the detail snapshot are the only state carried between
    passes, and both are keyed by durable source identities, so a monitor restarted
    mid-run re-emits the replay and then continues exactly where the last one left
    off rather than double-reporting the round so far.
    """

    run_id: RunId
    run_dir: Path
    oneharness_bin: str = "oneharness"
    github: GitHubBackend | None = None
    source_timeout: float | None = SOURCE_TIMEOUT_SECONDS
    parked_after: float = PARKED_AFTER_SECONDS
    clock: Callable[[], float] = time.time
    seen: set[str] = field(default_factory=set)
    snapshot: DetailSnapshot = field(default_factory=DetailSnapshot)
    _checkout_cache: dict[str, Path] | None = None
    _polled: bool = False

    def checkouts(self) -> dict[str, Path]:
        """Resolve registered checkouts once per process; the registry rarely moves."""
        if self._checkout_cache is None:
            try:
                self._checkout_cache = _checkouts(Registry())
            except (RegistryError, ConfigError, OSError):
                self._checkout_cache = {}
        return self._checkout_cache

    def poll(self) -> list[MonitorEvent]:
        """Read every source once and return only what has not been reported.

        Ordered by observation time so the four sources interleave into one
        chronological stream rather than four blocks per pass.
        """
        now = self.clock()
        events = read_events(self.run_dir / JOURNAL_NAME)
        mine = [event for event in events if event.run_id == self.run_id]
        ledgers = _round_ledgers(self.run_dir)
        found = journal_events(self.run_id, self.run_dir)
        found += history_events(
            self.run_id,
            now=now,
            oneharness_bin=self.oneharness_bin,
            timeout=self.source_timeout,
        )
        # The registry is the fallback; a recorded execution checkout wins, because it
        # is where the branch's commits are when the two differ.
        checkouts = {**self.checkouts(), **ledger_checkouts(ledgers)}
        found += git_events(known_branches(ledgers, mine), checkouts, self.snapshot, now=now)
        found += pr_events(
            known_prs(ledgers, mine),
            self.snapshot,
            now=now,
            github=self.github,
            replay=not self._polled,
            timeout=self.source_timeout,
        )
        self._polled = True
        fresh = [event for event in found if event.key not in self.seen]
        self.seen.update(event.key for event in fresh)
        if fresh:
            save_snapshot(self.run_dir, self.snapshot)
        return sorted(fresh, key=lambda event: event.at)

    def state(self) -> RunState:
        return run_state(self.run_dir, self.run_id, parked_after=self.parked_after)


class Writer:
    """Renders the stream in the selected format onto one stream."""

    def __init__(self, fmt: str, out: Any) -> None:
        self._json = fmt == "jsonl"
        self._out = out

    def header(self) -> None:
        # Only the text stream carries the header: a jsonl consumer parses every
        # line as a record, and a prose banner would be the one line that is not.
        if not self._json:
            print(HEADER, file=self._out, flush=True)

    def event(self, event: MonitorEvent, *, next_poll_seconds: float | None = None) -> None:
        line = (
            json.dumps(event.record(next_poll_seconds=next_poll_seconds), sort_keys=True)
            if self._json
            else event.text()
        )
        print(line, file=self._out, flush=True)

    def heartbeat(self, beat: Heartbeat) -> None:
        line = json.dumps(beat.record(), sort_keys=True) if self._json else beat.text()
        print(line, file=self._out, flush=True)

    def planner_due(self, message: str) -> None:
        line = (
            json.dumps({"type": "planner_update_due", "message": message})
            if self._json
            else message
        )
        print(line, file=self._out, flush=True)


def settled_detail(state: RunState) -> str:
    """One line saying why the stream is ending, in the run's own terms."""
    if state.finished:
        return "graph complete"
    if state.settlement == SETTLED_AWAITING_PLANNER:
        return summarize(f"settled, awaiting the planner: {state.detail}")
    if state.settlement == SETTLED_UNATTENDED:
        return summarize(f"settled, nothing is driving this run: {state.detail}")
    return state.detail


def stream(
    monitor: Monitor,
    writer: Writer,
    *,
    once: bool = False,
    until_settled: bool = False,
    heartbeat: float = DEFAULT_HEARTBEAT,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    max_poll_interval: float = DEFAULT_MAX_POLL_INTERVAL,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Replay what is known, then follow the run until it completes successfully.

    The exit contract is the point of this command: only a graph that *completed
    successfully* ends the stream (exit 0). Waiting on a human, a failed node, and
    an executor that died are all states someone acts on and the run then
    continues — through `next-round`, whose new round directory the next poll picks
    up — so each of them keeps heartbeating instead of exiting. A monitor that
    exited on them would report "finished" for a run that is merely stuck, which is
    the one lie a watching command must not tell.

    That contract governs the *follow*, and `follows` decides who gets one: a
    caller that cannot watch the stream incrementally gets ``once`` instead, where
    a single bounded pass replaces a wait it could never observe or end.

    ``until_settled`` widens the ending rather than replacing it: the same follow,
    ending at any of `SETTLEMENTS` — which still includes a completed graph, and
    adds the two endings a caller who *is* the planner has to be handed back at.
    It exits with that settlement's status, so "the run needs you" and "nothing is
    driving this run" are distinguishable without parsing the stream.
    """
    writer.header()
    shown_due = False
    last = monitor.clock()
    delay = poll_interval
    while True:
        indicator = due_indicator(monitor.run_dir / "channel")
        if indicator is not None and not shown_due:
            writer.planner_due(indicator)
            shown_due = True
        elif indicator is None:
            shown_due = False
        events = monitor.poll()
        delay = poll_interval if events else min(max_poll_interval, delay * 2)
        for event in events:
            writer.event(event, next_poll_seconds=delay)
            last = monitor.clock()
        state = monitor.state()

        rollup = monitor.snapshot.check_rollup
        # A completed graph reads the same either way: the detail describes the run,
        # not which mode happened to be reading it. Only the *unfinished* case
        # differs, and that difference is the whole point of `once` — it says "here
        # is where it stands" where the follow would keep waiting.
        settlement = state.settlement if until_settled else None
        if state.finished or settlement is not None or once:
            writer.heartbeat(
                Heartbeat(
                    monitor.clock(),
                    state.run_id,
                    state.round,
                    state.state,
                    settled_detail(state)
                    if state.finished or settlement is not None
                    else state.detail,
                    rollup.last_completed_check,
                    rollup.current_blocker,
                    delay,
                    settlement,
                )
            )
            return 0 if settlement is None else SETTLEMENTS[settlement]
        now = monitor.clock()
        if now - last >= heartbeat:
            writer.heartbeat(
                Heartbeat(
                    now,
                    state.run_id,
                    state.round,
                    state.state,
                    state.detail,
                    rollup.last_completed_check,
                    rollup.current_blocker,
                    delay,
                )
            )
            last = now
        sleep(delay)


def _positive(parser: argparse.ArgumentParser, name: str, value: float) -> float:
    if not math.isfinite(value) or value <= 0:
        parser.error(f"{name} must be a positive number of seconds")
    return value


class Watchable(Protocol):
    """The one thing this decision needs of an output stream."""

    def isatty(self) -> bool: ...


def follows(out: Watchable, *, follow: bool) -> bool:
    """Whether this invocation should follow the run rather than make one pass.

    Following is for a person watching a terminal: the stream is flushed line by
    line and Ctrl-C is how it ends. A caller whose stdout is a pipe or a file sees
    none of that — it gets the whole output when the process exits, and the exit
    contract says only a *successful* graph ever exits. So the command a run's own
    `launch.json` advertises was, for every automated planner, a command that
    produced nothing and never returned; the planner read `events.jsonl` and
    `/proc` by hand instead. Off a terminal the default is therefore one pass,
    which is a bounded answer to the same question. ``--follow`` asks for the
    follow anyway, for a caller that does consume the stream incrementally.
    """
    if follow:
        return True
    try:
        return bool(out.isatty())
    except ValueError:
        # A stream already closed cannot answer, and is not one a person is watching.
        return False


def attach(
    run_id: RunId,
    *,
    runs_dir: Path = DEFAULT_RUNS_DIR,
    out: TextIO | None = None,
    fmt: str = "text",
    once: bool = False,
    until_settled: bool = False,
    heartbeat: float = DEFAULT_HEARTBEAT,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    max_poll_interval: float = DEFAULT_MAX_POLL_INTERVAL,
    oneharness_bin: str = "oneharness",
    source_timeout: float | None = SOURCE_TIMEOUT_SECONDS,
    parked_after: float = PARKED_AFTER_SECONDS,
) -> int:
    """Watch one run and return its exit status: the whole of what "attach" means.

    Both attaching commands enter here — `just monitor` and the foreground `just
    orchestrate`, which passes ``until_settled=True``. That is deliberate and it is
    the whole reason `orchestrate` has no stream of its own: a second renderer would
    be a second answer to "what is happening right now", and the two would drift on
    the first source that changed.
    """
    run_dir = runs_dir / run_id
    monitor = Monitor(
        run_id=run_id,
        run_dir=run_dir,
        snapshot=load_snapshot(run_dir),
        oneharness_bin=oneharness_bin,
        source_timeout=source_timeout,
        parked_after=parked_after,
    )
    writer = Writer(fmt, sys.stdout if out is None else out)
    try:
        return stream(
            monitor,
            writer,
            once=once,
            until_settled=until_settled,
            heartbeat=heartbeat,
            poll_interval=poll_interval,
            max_poll_interval=max_poll_interval,
        )
    except (ChannelError, KeyboardInterrupt):
        # Ctrl-C is how a person ends a follow that is working as designed, so it is
        # a clean stop rather than a traceback. It ends the *attachment* only: the
        # run leads its own session and keeps working, which is why the foreground
        # `orchestrate` says so before it starts streaming.
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Follow one tracked-graph run as one concise stream, aggregating the run "
            "journal, the dispatched oneharness sessions labelled with the run, the "
            "commits on its lifecycle branches, and the state of its linked PRs. "
            "Defaults to the newest active run."
        )
    )
    parser.add_argument("run_id", nargs="?", metavar="RUN_ID")
    parser.add_argument(
        "--once", action="store_true", help="replay known events, report state, and exit 0"
    )
    parser.add_argument(
        "--follow",
        action="store_true",
        help="follow the run even when output is not a terminal (the default on a "
        "terminal); without it, a redirected or captured stream makes one pass and exits",
    )
    parser.add_argument(
        "--until-settled",
        action="store_true",
        help="follow the run (terminal or not) and return when it settles: the graph "
        "completed, a blocking planner surface is waiting on you, or nothing is driving "
        "the run any more (exit 3)",
    )
    parser.add_argument("--format", choices=("text", "jsonl"), default="text")
    parser.add_argument(
        "--heartbeat",
        type=float,
        default=DEFAULT_HEARTBEAT,
        metavar="SECONDS",
        help="seconds of silence before reporting run state (default: 60)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL,
        metavar="SECONDS",
        help="seconds between source polls (default: 2)",
    )
    parser.add_argument(
        "--max-poll-interval",
        type=float,
        default=DEFAULT_MAX_POLL_INTERVAL,
        metavar="SECONDS",
        help=(
            "maximum seconds between unchanged source polls "
            f"(default: {DEFAULT_MAX_POLL_INTERVAL:g})"
        ),
    )
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    parser.add_argument(
        "--oneharness-bin",
        default="oneharness",
        help="the binary the history source reads through (default: oneharness)",
    )
    parser.add_argument(
        "--source-timeout",
        type=float,
        default=SOURCE_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help=(
            "seconds one poll may wait on a single external source before treating it "
            f"as silent (default: {SOURCE_TIMEOUT_SECONDS:g})"
        ),
    )
    parser.add_argument(
        "--parked-after",
        type=float,
        default=PARKED_AFTER_SECONDS,
        metavar="SECONDS",
        help="report a launch with no child process, planner surface, or ledger write for "
        f"this long as parked (default: {PARKED_AFTER_SECONDS:g})",
    )
    args = parser.parse_args(argv)
    _positive(parser, "--heartbeat", args.heartbeat)
    _positive(parser, "--poll-interval", args.poll_interval)
    _positive(parser, "--max-poll-interval", args.max_poll_interval)
    _positive(parser, "--source-timeout", args.source_timeout)
    if not math.isfinite(args.parked_after) or args.parked_after <= 0:
        # Worded as `just runs`, `just status`, and `just orchestrate` word it: one
        # flag, one meaning, one refusal, wherever a planner types it.
        parser.error("--parked-after must be a positive, finite number of seconds")
    if args.max_poll_interval < args.poll_interval:
        parser.error("--max-poll-interval must be at least --poll-interval")
    if args.once and args.follow:
        parser.error("--once and --follow ask for opposite things")
    if args.once and args.until_settled:
        parser.error("--once and --until-settled ask for opposite things")
    try:
        run_id = resolve_run(args.runs_dir, args.run_id)
    except MonitorError as exc:
        print(f"monitor: {exc}", file=sys.stderr)
        return 2
    return attach(
        run_id,
        runs_dir=args.runs_dir,
        fmt=args.format,
        # `--until-settled` has its own ending, so it follows wherever it is run:
        # the one-pass default off a terminal exists because such a caller cannot
        # observe or end a follow, and this one ends itself.
        once=args.once or not (args.until_settled or follows(sys.stdout, follow=args.follow)),
        until_settled=args.until_settled,
        heartbeat=args.heartbeat,
        poll_interval=args.poll_interval,
        max_poll_interval=args.max_poll_interval,
        oneharness_bin=args.oneharness_bin,
        source_timeout=args.source_timeout,
        parked_after=args.parked_after,
    )
