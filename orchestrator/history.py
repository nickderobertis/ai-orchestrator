"""Human-facing views over oneharness' cross-project run history."""

# llmlint: ignore-file[boundary_inputs_validated] the `path` and other fields come
# from `oneharness history list --format json` — a local tool this module invokes
# itself, reporting paths inside oneharness' own history store, not an untrusted
# network/user boundary. `from_value` type-checks every field before use.

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, NewType, cast

from .config import ConfigError
from .detail_snapshot import SNAPSHOT_VERSION, CommitDetail, PrDetail
from .ids import DetailId, DetailIdError, GitId, GraphId, OneharnessId, PrId, parse_detail_id
from .journal import JOURNAL_NAME, Event, read_events
from .runs import GraphResultItem, RunId, as_result_payload, load_mapping, rounds


class HistoryError(Exception):
    """History could not be read or a requested session was not found."""


JUDGE_PREFIXES = (
    "you-are-a-strict-careful-evaluator",
    "you-are-roleplaying-the-user-in",
)
LLMLINT_PROMPT_PREFIXES = (
    "Evaluate each rule against the target files",
    "Your previous verdict reported rule violations in files that those rules do not cover",
)
LLMLINT_NAME_PREFIXES = tuple(
    re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-") for prompt in LLMLINT_PROMPT_PREFIXES
)
SessionId = NewType("SessionId", str)
SessionRole = Literal["agent", "judge", "llmlint"]


def _session_labels(value: object) -> dict[str, str]:
    """Read the history labels a dispatch stamped on this session.

    Labels are optional and are dropped rather than rejected when malformed: a
    session recorded before the orchestrator labelled its dispatches has none, and
    an unlabelled session must still list. `labels` is written by `labels.py` at
    dispatch time, so a value that fails the contract here came from somewhere else
    and has no claim on a run.
    """
    if not isinstance(value, dict):
        return {}
    return {
        key: item
        for key, item in value.items()
        if isinstance(key, str) and key and isinstance(item, str) and item
    }


@dataclass(frozen=True)
class HistorySession:
    """Validated session metadata returned by ``oneharness history list``."""

    session_id: SessionId
    name: str
    project: Path
    started: str
    path: Path
    #: The ``ONEHARNESS_HISTORY_LABELS`` this dispatch was stamped with, which is
    #: what lets a reader ask "which run/round/node produced this session?" rather
    #: than inferring it from the project path or the task name.
    labels: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_value(cls, value: Any) -> HistorySession | None:
        if not isinstance(value, dict):
            return None
        session_id = value.get("id")
        name = value.get("name")
        project = value.get("project")
        started = value.get("started")
        path = value.get("path")
        fields = (session_id, name, project, started, path)
        if not all(isinstance(field, str) and field.strip() for field in fields):
            return None
        assert isinstance(session_id, str)
        assert isinstance(name, str)
        assert isinstance(project, str)
        assert isinstance(started, str)
        assert isinstance(path, str)
        return cls(
            session_id=SessionId(session_id),
            name=name,
            project=Path(project),
            started=started,
            path=Path(path),
            labels=_session_labels(value.get("labels")),
        )


def _run_history(*args: str, oneharness_bin: str = "oneharness") -> Any:
    try:
        proc = subprocess.run(
            [oneharness_bin, "history", *args, "--all-projects", "--format", "json"],
            text=True,
            capture_output=True,
        )
    except FileNotFoundError as exc:
        raise HistoryError("oneharness not found — run 'just bootstrap'") from exc
    if proc.returncode:
        detail = proc.stderr.strip() or "unknown error"
        raise HistoryError(f"oneharness history failed: {detail}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise HistoryError("oneharness history returned invalid JSON") from exc


def _records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise HistoryError(f"cannot read history session {path}: {exc}") from exc
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def _is_worker(session: HistorySession) -> bool:
    return session_role(session) == "agent"


def _strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _is_llmlint(session: HistorySession) -> bool:
    if session.name.startswith(LLMLINT_NAME_PREFIXES):
        return True
    try:
        records = _records(session.path)
    except HistoryError:
        return False
    return bool(records) and any(
        value.startswith(LLMLINT_PROMPT_PREFIXES) for value in _strings(records[0])
    )


def session_role(session: HistorySession) -> SessionRole:
    """Classify a session from its validated label, falling back for legacy history."""
    role = session.labels.get("role")
    if role in {"agent", "judge", "llmlint"}:
        return cast(SessionRole, role)
    if _is_llmlint(session):
        return "llmlint"
    return "judge" if session.name.startswith(JUDGE_PREFIXES) else "agent"


def _sessions(value: Any) -> list[HistorySession]:
    if not isinstance(value, list):
        raise HistoryError("oneharness history list returned an unexpected response")
    return [session for item in value if (session := HistorySession.from_value(item))]


def worker_sessions(*, oneharness_bin: str = "oneharness") -> list[HistorySession]:
    """Return validated worker sessions, newest first, across every project."""
    return [
        session for session in all_sessions(oneharness_bin=oneharness_bin) if _is_worker(session)
    ]


def all_sessions(*, oneharness_bin: str = "oneharness") -> list[HistorySession]:
    """Return every validated session, newest first, across every project."""
    return _sessions(_run_history("list", oneharness_bin=oneharness_bin))


# llmlint: ignore[modern_domain_modeling] harness records as dicts, per history.py convention
def session_records(session: HistorySession) -> list[dict[str, Any]]:
    """Read the normalized records belonging to ``session``."""
    return _records(session.path)


def session_duration_ms(records: list[dict[str, Any]]) -> int:
    """Sum measured, non-negative record durations without treating bools as integers."""
    return sum(
        duration
        for record in records
        if isinstance((duration := record.get("duration_ms")), int)
        and not isinstance(duration, bool)
        and duration >= 0
    )


def recent_runs(limit: int, *, oneharness_bin: str = "oneharness") -> str:
    """Return a compact table of the newest worker sessions across projects."""
    if limit <= 0:
        raise HistoryError("N must be a positive integer")
    rows = worker_sessions(oneharness_bin=oneharness_bin)[:limit]
    header = (
        "UTC TIME              ID             PROJECT              TASK"
        "                         HARNESS/MODEL        STATUS"
    )
    output = [header]
    for item in rows:
        records = _records(item.path)
        latest = records[-1] if records else {}
        project = item.project.name or "?"
        harness = str(latest.get("harness", "?"))
        model = str(latest.get("model", "?"))
        output.append(
            f"{item.started[:20]:20}  {item.session_id[-14:]:14} "
            f" {project[:20]:20} {item.name[:28]:28} "
            f"{f'{harness}/{model}'[:20]:20} {latest.get('status', '?')}"
        )
    if not rows:
        output.append("No worker sessions recorded. Dispatch wrappers set ONEHARNESS_HISTORY=1.")
    return "\n".join(output)


def _command(event: Any) -> str | None:
    if not isinstance(event, dict) or event.get("kind") != "tool_call":
        return None
    value = event.get("input")
    if not isinstance(value, dict):
        return None
    for key in ("command", "cmd"):
        command = value.get(key)
        if isinstance(command, str):
            return command
    return None


@dataclass(frozen=True)
class Digest:
    session_id: SessionId
    turns: int
    input_tokens: int
    output_tokens: int
    status: str
    duration_ms: int
    commands: list[str]
    text: str


def digest(records: list[dict[str, Any]], session_id: SessionId) -> Digest:
    """Defensively summarize normalized oneharness records."""
    commands: list[str] = []
    input_tokens = output_tokens = 0
    for record in records:
        usage = record.get("usage")
        if isinstance(usage, dict):
            input_tokens += (
                usage.get("input_tokens", 0) if isinstance(usage.get("input_tokens"), int) else 0
            )
            output_tokens += (
                usage.get("output_tokens", 0) if isinstance(usage.get("output_tokens"), int) else 0
            )
        events = record.get("events")
        if isinstance(events, list):
            commands.extend(command for event in events if (command := _command(event)))
    latest = records[-1] if records else {}
    return Digest(
        session_id=session_id,
        turns=len(records),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        status=str(latest.get("status", "unknown")),
        duration_ms=session_duration_ms(records),
        commands=commands[-5:],
        text=str(latest.get("text", "")),
    )


DEFAULT_RUNS_DIR = Path("runs")

_PR_URL_NUMBER = re.compile(r"/pull/(\d+)/?\Z")
# The detail keys that carry a commit. Read as a set rather than one blessed key
# because the transitions that name a commit are recorded by different subsystems,
# and a snapshot lookup that only knew one of their spellings would answer "no such
# commit" for a commit sitting in the journal under another.
_SHA_DETAIL_KEYS = ("sha", "commit", "checkpoint", "head")


def _pr_number(value: object) -> int | None:
    """The PR number a recorded URL names, if it names one."""
    if not isinstance(value, str) or (match := _PR_URL_NUMBER.search(value)) is None:
        return None
    return int(match.group(1))


def _detail_number(value: object) -> int | None:
    """A journal detail's number, rejecting the bool that `isinstance` would admit."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


@dataclass(frozen=True)
class Snapshot:
    """One tracked-graph node as the ledger and journal persisted it.

    This is the answer to a typed id when oneharness' history cannot be one: the
    ledger records a node's *outcome* and the journal records how it got there, and
    both outlive the session that produced them. A `git:`/`pr:`/`graph:` id names a
    thing in the graph rather than a conversation, so this — not a session digest —
    is its primary source.
    """

    ref: GraphId
    item: GraphResultItem
    events: list[Event]

    @property
    def identity(self) -> str | None:
        """The ``owner/name`` slug this node worked in, if it recorded one."""
        repo = self.item.get("repo")
        return repo if isinstance(repo, str) and repo else None

    @property
    def pr_numbers(self) -> set[int]:
        """Every PR number this node recorded, from the ledger and the journal.

        The local direct-merge path synthesizes a `PullRequest` numbered 0 so the
        result has a stable ref to name; it opened no PR, so that 0 is dropped here
        rather than made addressable as ``pr:owner/name#0``.
        """
        found = [_pr_number(self.item.get("pr"))]
        for event in self.events:
            found.append(_pr_number(event.detail.get("pr")))
            found.append(_detail_number(event.detail.get("number")))
        return {number for number in found if number is not None and number >= 1}

    @property
    def shas(self) -> set[str]:
        """Every commit this node recorded, from the ledger and the journal."""
        found = {
            value
            for event in self.events
            for key in _SHA_DETAIL_KEYS
            if isinstance(value := event.detail.get(key), str) and value
        }
        resume = self.item.get("resume")
        if isinstance(resume, dict) and isinstance(checkpoint := resume.get("checkpoint"), str):
            found.add(checkpoint)
        return found


def _round_snapshots(run_id: RunId, number: int, round_dir: Path) -> list[Snapshot]:
    result_path = round_dir / "result.json"
    if not result_path.exists():
        return []
    try:
        payload = as_result_payload(load_mapping(result_path))
    except ConfigError as exc:
        raise HistoryError(f"cannot read recorded round {result_path}: {exc}") from exc
    events = read_events(round_dir.parent / JOURNAL_NAME)
    return [
        Snapshot(
            ref=GraphId(run_id=run_id, round=number, node=node),
            item=item,
            events=[
                event
                for event in events
                if event.run_id == run_id and event.round == number and event.node == node
            ],
        )
        for node, item in payload["results"].items()
    ]


def snapshots(runs_dir: Path = DEFAULT_RUNS_DIR) -> list[Snapshot]:
    """Every persisted node of every recorded round, oldest round first."""
    if not runs_dir.is_dir():
        return []
    return [
        snapshot
        for run_dir in sorted(entry for entry in runs_dir.iterdir() if entry.is_dir())
        for number, round_dir in rounds(run_dir)
        for snapshot in _round_snapshots(RunId(run_dir.name), number, round_dir)
    ]


def _graph_snapshot(ref: GraphId, runs_dir: Path) -> Snapshot | None:
    """Read exactly the node a `graph:` id addresses, without scanning the ledger."""
    round_dir = runs_dir / ref.run_id / ref.round_name
    return next(
        (
            snapshot
            for snapshot in _round_snapshots(RunId(ref.run_id), ref.round, round_dir)
            if snapshot.ref.node == ref.node
        ),
        None,
    )


def _matching_snapshots(ref: GitId | PrId, runs_dir: Path) -> list[Snapshot]:
    """Every node in the ledger that recorded the commit or PR ``ref`` names."""
    return [
        snapshot
        for snapshot in snapshots(runs_dir)
        if snapshot.identity == ref.identity
        and (
            any(ref.matches(sha) for sha in snapshot.shas)
            if isinstance(ref, GitId)
            else ref.number in snapshot.pr_numbers
        )
    ]


def _stamp(at: float) -> str:
    return datetime.fromtimestamp(at, UTC).isoformat(timespec="seconds")


def _render_snapshot(snapshot: Snapshot, ref: DetailId) -> str:
    item = snapshot.item
    lines = [f"Reference: {ref}", f"Graph node: {snapshot.ref}"]
    status = item.get("status", "unknown")
    outcome = item.get("outcome")
    lines.append(f"Status: {status}" + (f" ({outcome})" if outcome else ""))
    for label, value in (
        ("Repo", item.get("repo")),
        ("Branch", item.get("branch")),
        ("Base", item.get("base_branch")),
    ):
        if isinstance(value, str) and value:
            lines.append(f"{label}: {value}")
    if isinstance(pr := item.get("pr"), str) and pr:
        lines.append(f"PR: {pr}")
    if isinstance(detail := item.get("detail"), str) and detail:
        lines.append(f"Detail: {detail}")
    journal = "\n".join(
        f"  {_stamp(event.at)} {event.kind}"
        + (f" [{event.step}]" if event.step else "")
        + (f" {json.dumps(dict(event.detail), sort_keys=True)}" if event.detail else "")
        for event in snapshot.events
    )
    lines.append("Journal:\n" + (journal or "  (no recorded transitions)"))
    lines.append(f"Full detail: just runs; cat runs/{snapshot.ref.run_id}/{JOURNAL_NAME}")
    return "\n".join(lines)


def _persisted_sections(runs_dir: Path, section: str) -> Iterator[Mapping[str, object]]:
    """Yield understood snapshot sections newest-first, rejecting schema drift."""
    if not runs_dir.is_dir():
        return
    for run_dir in sorted((path for path in runs_dir.iterdir() if path.is_dir()), reverse=True):
        path = run_dir / "monitor" / "details.json"
        if not path.exists():
            continue
        try:
            raw = load_mapping(path)
        except (ConfigError, OSError):
            continue
        records = raw.get(section)
        if raw.get("version") == SNAPSHOT_VERSION and isinstance(records, dict):
            yield records


def _persisted_detail(ref: GitId | PrId, runs_dir: Path) -> CommitDetail | PrDetail | None:
    """Find the newest monitor snapshot that observed ``ref``.

    A real lifecycle records its branch and PR transitions in the journal, but a
    commit SHA is discovered by git and mutable PR state comes from GitHub. The
    monitor persists those observations precisely so detail lookup still works
    after merge/cleanup, so this resolver must consult that durable source too.
    """
    match ref:
        case GitId(identity=identity):
            for records in _persisted_sections(runs_dir, "commits"):
                for value in records.values():
                    detail = CommitDetail.from_value(value)
                    if (
                        detail is not None
                        and detail.identity == identity
                        and ref.matches(detail.sha)
                    ):
                        return detail
        case PrId():
            for records in _persisted_sections(runs_dir, "prs"):
                pr_detail = PrDetail.from_value(records.get(str(ref)))
                if pr_detail is not None:
                    return pr_detail
    return None


def _render_persisted_detail(ref: GitId | PrId, detail: CommitDetail | PrDetail) -> str:
    match ref, detail:
        case GitId(), CommitDetail():
            lines = [f"Reference: {ref}", f"Commit: {detail.sha}"]
            for label, value in (
                ("Repo", detail.identity),
                ("Branch", detail.branch),
                ("Base", detail.base),
            ):
                if value:
                    lines.append(f"{label}: {value}")
            if detail.subject:
                lines.append(f"Subject: {detail.subject}")
            lines.append("Commit and diff:\n" + (detail.detail or "(unavailable)"))
            return "\n".join(lines)
        case PrId(), PrDetail():
            checks = [
                {"name": check.name, "state": check.state, "required": check.required}
                for check in detail.checks
            ]
            return "\n".join(
                [
                    f"Reference: {ref}",
                    f"PR: {detail.url or ref}",
                    f"State: {detail.state}",
                    f"Merged: {detail.merged}",
                    f"Draft: {detail.draft}",
                    f"Merge state: {detail.merge_state_status}",
                    f"Checks: {json.dumps(checks, sort_keys=True)}",
                ]
            )
        case _:
            raise HistoryError(f"persisted detail type does not match {ref}")


def _show_snapshot(ref: GitId | PrId | GraphId, runs_dir: Path) -> str:
    """Resolve a graph/git/PR id against the persisted ledger and journal."""
    if isinstance(ref, GraphId):
        found = [snapshot] if (snapshot := _graph_snapshot(ref, runs_dir)) else []
    else:
        found = _matching_snapshots(ref, runs_dir)
    detail_ref: GitId | PrId | None = ref if isinstance(ref, GitId | PrId) else None
    persisted = _persisted_detail(detail_ref, runs_dir) if detail_ref is not None else None
    if not found and persisted is not None and detail_ref is not None:
        return _render_persisted_detail(detail_ref, persisted)
    if not found:
        raise HistoryError(
            f"no recorded tracked-graph node matches {ref} under {runs_dir}/ "
            "(pass --runs-dir if the run was recorded elsewhere)"
        )
    # Newest wins, matching the session view's "newest first" resolution: a branch
    # is legitimately carried across rounds, so one commit or PR can appear in
    # several, and the latest round is the one that says where it ended up.
    rendered = _render_snapshot(found[-1], ref)
    if persisted is not None and detail_ref is not None:
        rendered += "\n\nPersisted remote detail:\n" + _render_persisted_detail(
            detail_ref, persisted
        )
    if len(found) > 1:
        others = ", ".join(str(snapshot.ref) for snapshot in found[:-1])
        rendered += f"\nAlso recorded in: {others}"
    return rendered


def show_run(
    query: str, *, oneharness_bin: str = "oneharness", runs_dir: Path = DEFAULT_RUNS_DIR
) -> str:
    """Render whatever ``query`` names: a typed detail id, or a legacy session.

    A typed id says which namespace to resolve in, so it is dispatched there. Any
    other string is a legacy oneharness id or substring and keeps its existing
    meaning exactly — this view predates the typed ids and the muscle memory for it
    is the reason they are a *widening* rather than a replacement.
    """
    if not query.strip():
        raise HistoryError("id-or-substring must not be empty")
    try:
        ref = parse_detail_id(query.strip())
    except DetailIdError as exc:
        raise HistoryError(str(exc)) from exc
    if ref is None:
        return _show_session(query, oneharness_bin=oneharness_bin)
    if isinstance(ref, OneharnessId):
        # `oh:` is the namespace-explicit spelling of the legacy query, so it
        # resolves through the same path: anything else would make the typed form
        # subtly weaker than the string it replaces.
        return _show_session(ref.history_id, oneharness_bin=oneharness_bin)
    return _show_snapshot(ref, runs_dir)


def _show_session(query: str, *, oneharness_bin: str = "oneharness") -> str:
    """Resolve a substring to the newest worker session and render its digest."""
    matches = [
        session
        for session in _sessions(_run_history("list", oneharness_bin=oneharness_bin))
        if _is_worker(session) and (query in session.session_id or query in session.name)
    ]
    if matches:
        item = matches[0]
        session_id = item.session_id
        # The list response already resolved the exact backing file. Reading that
        # file keeps substring lookup and legacy display-name lookup unchanged.
        parsed = session_records(item)
        detail_query = str(session_id)
    elif _is_uuid(query):
        # UUIDv7 history identities live on v0.2 records rather than the backwards-
        # compatible list envelope.  Delegate an otherwise-unmatched query to the
        # CLI's exact-ID lookup; it also synthesizes stable IDs while normalizing
        # legacy v0.1 records.
        value = _run_history("show", query, oneharness_bin=oneharness_bin)
        if not isinstance(value, list):
            raise HistoryError("oneharness history show returned an unexpected response")
        parsed = [record for record in value if isinstance(record, dict)]
        if not parsed:
            raise HistoryError(f"no worker history session matches {query!r}")
        raw_session = parsed[0].get("session")
        session_id = SessionId(raw_session if isinstance(raw_session, str) else query)
        detail_query = query
    else:
        raise HistoryError(f"no worker history session matches {query!r}")
    result = digest(parsed, session_id)
    commands = "\n".join(f"  $ {command}" for command in result.commands) or "  (none recorded)"
    return (
        f"Session: {result.session_id}\n"
        f"Turns: {result.turns}\n"
        f"Tokens: {result.input_tokens:,} input / {result.output_tokens:,} output\n"
        f"Latest: {result.status} ({result.duration_ms / 1000:.1f}s)\n"
        f"Recent commands:\n{commands}\n"
        f"Latest agent text:\n{result.text or '(none recorded)'}\n\n"
        f"Full detail: oneharness history show {detail_query} --format text"
    )


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def _exit_error(exc: HistoryError) -> int:
    print(f"history: {exc}", file=sys.stderr)
    return 2


def main_list(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List recent dispatched worker sessions.")
    parser.add_argument("limit", nargs="?", default=15, type=int, metavar="N")
    args = parser.parse_args(argv)
    try:
        print(recent_runs(args.limit))
    except HistoryError as exc:
        return _exit_error(exc)
    return 0


def main_show(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Show one dispatched worker, or the recorded node a typed id names: "
            "oh:<history-id>, git:<owner/name>@<sha>, pr:<owner/name>#<number>, "
            "graph:<run>/<round>/<node>. Any other value is a legacy session "
            "id-or-substring."
        )
    )
    parser.add_argument("session", metavar="ID-OR-SUBSTRING")
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    args = parser.parse_args(argv)
    try:
        print(show_run(args.session, runs_dir=args.runs_dir))
    except HistoryError as exc:
        return _exit_error(exc)
    return 0
