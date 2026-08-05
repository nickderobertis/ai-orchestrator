"""Assemble the read-only DAG-UI JSON from the recorded run sources.

This is the pure read model behind the FastAPI surface: it joins the strict round
projection, the machine telemetry index, the per-node result items, the mapped
agent/subagent conversations, the persisted PR/commit detail, and the launching
session provenance into the exact ``RunList`` / ``RunDetail`` / ``Round`` shapes the
DAG UI contract fixes (`docs/dag-ui/design.md`). It reads; it never writes, spawns a
run, or reaches the network beyond the history/GitHub reads its sources already own.

Every input is a separate trust boundary. A missing run is `RunNotFound`; a corrupt
authoritative journal is `ProjectionFailed`; a malformed launch record degrades to
"no launcher" rather than failing the read.
"""

# llmlint: ignore-file[contracts_have_one_source_or_a_drift_gate] These payload types
# are gated by scripts/check-dag-state-contract.py, which reconciles their field names,
# optionality, and closed value vocabularies against the authoritative declarations.
# Structural field *types* stay ungated on purpose — see that script's own note.
#
# llmlint: ignore-file[modern_domain_modeling] `RunDetail.run` and `.details` are
# pass-throughs of `RunTelemetry.record()` and `DetailSnapshot.to_record()`. Those
# modules own those shapes; restating them here would create the second source this
# file exists to avoid, and the design contract references the owning types by name
# for exactly that reason.

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NewType, NotRequired, TypedDict
from urllib.parse import quote, urlparse

from .config import ConfigError
from .conversations import DagConversation, run_conversations
from .goals import normalize_goal
from .history import HistoryError, SessionScan
from .journal import JOURNAL_NAME, Event
from .launch import (
    LAUNCH_RECORD_NAME,
    read_launch_link,
    read_provenance,
    resolve_launch_session,
)
from .monitor import DetailSnapshot, load_snapshot, snapshot_path
from .projection import (
    NodeState,
    NodeStatus,
    ProjectedPlan,
    ProjectionError,
    node_statuses,
    project_round,
    read_strict_events,
)
from .runs import (
    GraphPayload,
    GraphResultItem,
    RunId,
    latest_round,
    result_state_is_terminal,
    validate_run_id,
)
from .telemetry import (
    TELEMETRY_SCHEMA_VERSION,
    LinkageQuality,
    RunTelemetry,
    TimingQuality,
    TimingRecord,
    collect_run,
    native_session_groups,
)

API_VERSION = 2


class ReadError(Exception):
    """Base class for a read the server maps to an HTTP status."""


class InvalidRunId(ReadError):
    """A run identifier failed validation at the trust boundary (422)."""


class RunNotFound(ReadError):
    """No recorded run matches the identifier (404)."""


class InvalidConversationId(ReadError):
    """A conversation identifier failed validation at the trust boundary (422)."""


class ConversationNotFound(ReadError):
    """The run exists but records no such conversation (404).

    Distinct from `RunNotFound` so a client can tell "this run is gone" from "this
    run has no transcript by that id" — the second is routine while a session is
    still being written, the first means the view should stop polling.
    """


class ArtifactNotFound(ReadError):
    """The run records no readable artifact with the requested opaque id (404)."""


class ProjectionFailed(ReadError):
    """The authoritative journal cannot be folded into a consistent graph (409)."""


#: Bound on the opaque conversation id accepted from a request path. History session
#: ids are short native identifiers; anything longer, empty, or carrying control
#: characters is rejected here rather than scanned against every discovered session.
_MAX_CONVERSATION_ID = 256


def validate_conversation_id(value: str) -> str:
    """Return a well-formed opaque conversation id, else raise ``InvalidConversationId``."""
    if not value or len(value) > _MAX_CONVERSATION_ID or not value.isprintable():
        raise InvalidConversationId(
            "conversation id must be 1-256 printable characters on a single line"
        )
    return value


class RunLaunch(TypedDict):
    """A run's join to its launching session, per ``docs/dag-ui/design.md``.

    ``session_key`` is the opaque, stable, irreversible name of that session, served
    by default because grouping a list of runs by the planner that launched them is
    what a viewer needs and no part of it is sensitive. It is omitted for a run that
    named no session — one launched from a plain shell, or recorded before the key
    existed. ``launcher_session_id`` is the raw id behind it and is present only when
    the server's redaction policy is configured to expose it, so it is omitted rather
    than nulled by default.
    """

    launch_id: str
    launcher: str
    session_key: NotRequired[str]
    launcher_session_id: NotRequired[str]


RunsCursor = NewType("RunsCursor", str)


class RunSummary(TypedDict):
    """One ``RunSummary`` row of the run-list view."""

    run_id: str
    state: str
    phase: str
    #: Null for a run that has recorded no journal event yet; see ``RunTelemetry``.
    last_event: str | None
    timing_quality: TimingQuality
    linkage_quality: LinkageQuality
    timing: TimingRecord
    node_counts: dict[str, int]
    last_progress_at: NotRequired[float]
    launch: NotRequired[RunLaunch]


class RunList(TypedDict):
    """The ``RunList`` envelope served by ``GET /api/v2/runs`` and the SSE snapshot."""

    api_version: int
    telemetry_schema_version: int
    observed_at: str
    runs: list[RunSummary]
    next_cursor: NotRequired[RunsCursor]


class Round(TypedDict):
    """One strict ``RoundProjection`` serialized as the contract's ``Round``.

    ``node_status`` is the authoritative per-node vocabulary every renderer reads;
    ``node_states`` is the strict fold it is derived from and is retained unchanged.
    See ``orchestrator.projection.NodeStatus`` for how the two relate.
    """

    run_id: str
    round: int
    plan: ProjectedPlan
    node_states: dict[str, NodeState]
    node_status: dict[str, NodeStatus]
    node_gated_by: dict[str, list[str]]
    node_results: dict[str, GraphResultItem]
    attestations: list[str]
    result: GraphPayload | None
    last_seq: int


class RunDetail(TypedDict):
    """The full ``RunDetail`` served by ``GET /api/v2/runs/{run_id}``."""

    api_version: int
    telemetry_schema_version: int
    observed_at: str
    run: dict[str, Any]
    rounds: list[Round]
    conversations: list[DagConversation]
    details: dict[str, Any]
    node_details: dict[str, dict[str, Any]]
    logs: NotRequired[dict[str, str]]
    launch: NotRequired[RunLaunch]


class ArtifactContent(TypedDict):
    """The bounded public representation of one recorded artifact."""

    id: str
    kind: str
    content: str
    truncated: bool


def _now(now: datetime | None) -> str:
    return (now or datetime.now(UTC)).isoformat()


def contained_run_dir(runs_dir: Path, run_id: str) -> Path | None:
    """A *recorded run's* directory beneath the configured root, else ``None``.

    Two separate things are established, and the name promises both. Containment:
    validating the identifier only proves it is a well-formed *name*, while a
    directory or symlink under the root can still point outside it, so containment is
    checked after resolution and no id can make the server read a tree the operator
    did not point it at. Existence: a directory that has recorded no round is not a
    run — an empty or unrelated directory under the root must read as absent rather
    than as a run with nothing in it.
    """
    try:
        root = runs_dir.resolve(strict=True)
        candidate = (runs_dir / run_id).resolve(strict=True)
    except OSError:
        return None
    if not candidate.is_dir() or not candidate.is_relative_to(root):
        return None
    return candidate if latest_round(candidate) is not None else None


def _run_dirs(runs_dir: Path) -> Iterator[Path]:
    if not runs_dir.is_dir():
        return
    for entry in sorted(runs_dir.iterdir()):
        if (contained := contained_run_dir(runs_dir, entry.name)) is not None:
            yield contained


def read_launch_id(run_dir: Path) -> str | None:
    """The non-sensitive ``launch_id`` the run recorded, or ``None`` when absent."""
    link = read_launch_link(run_dir)
    return None if link is None else link.launch_id


def resolve_launch(
    run_dir: Path,
    *,
    expose_launcher_session_id: bool = False,
    now: datetime | None = None,
) -> RunLaunch | None:
    """Resolve a run's launching session from what it recorded at launch.

    Attribution comes from the run's own record first and last: the ``launcher`` and
    the opaque ``session_key`` it wrote survive the out-of-repo provenance record
    expiring or being deleted, so a viewer groups runs by their launching planner
    however old they are. The provenance record is still consulted, because it is the
    only source of the raw session id — served only when the caller's redaction
    policy permits it — and because a run recorded before the durable key existed has
    nothing else to resolve. A run neither can name reads as ``"unknown"``, without
    disturbing the graph.
    """
    link = read_launch_link(run_dir)
    if link is None:
        return None
    launcher, session = resolve_launch_session(link, now=now)
    result: RunLaunch = {"launch_id": link.launch_id, "launcher": launcher}
    if session is not None:
        result["session_key"] = session.key
    if expose_launcher_session_id:
        provenance = read_provenance(link.launch_id, now=now)
        if provenance is not None:
            result["launcher_session_id"] = provenance["launcher_session_id"]
    return result


#: Bytes of any single log tail returned to the UI. A log is a scan aid, not a
#: download; a bounded tail keeps one slow run from streaming an unbounded blob.
_LOG_TAIL_BYTES = 64_000

#: Logs that live *beneath the run directory* and are therefore safe to serve under
#: the configured root. A node's own merge-path gate log is reached through the node
#: result's artifact pointers instead, not through this run-level map.
_RUN_LOGS = {"orchestrator_stderr": ("orchestrator", "stderr.log")}

_ARTIFACT_KINDS = ("gate_log", "worker_report", "oneharness_session")


def _artifact_id(kind: str, path: str) -> str:
    """Stable opaque address for a recorded artifact, without exposing its path."""
    digest = hashlib.sha256(f"{kind}\0{path}".encode()).hexdigest()[:24]
    return f"{kind}-{digest}"


def _artifact_paths(rounds: list[Round]) -> dict[str, tuple[str, str]]:
    found: dict[str, tuple[str, str]] = {}
    for round_record in rounds:
        results = [*round_record["node_results"].values()]
        graph_results = (round_record["result"] or {}).get("results", {})
        if isinstance(graph_results, dict):
            results.extend(graph_results.values())
        for result in results:
            if not isinstance(result, dict):
                continue
            steps = result.get("steps")
            records = [
                result,
                *(steps if isinstance(steps, list) else []),
            ]
            for record in records:
                artifacts = record.get("artifacts") if isinstance(record, dict) else None
                if not isinstance(artifacts, dict):
                    continue
                for kind in _ARTIFACT_KINDS:
                    path = artifacts.get(kind)
                    if isinstance(path, str) and path:
                        found[_artifact_id(kind, path)] = (kind, path)
    return found


def _replace_served_artifacts_with_ids(rounds: list[Round]) -> None:
    """Expose supported result artifacts only, addressed by opaque API id."""
    for round_record in rounds:
        results = [*round_record["node_results"].values()]
        graph_results = (round_record["result"] or {}).get("results", {})
        if isinstance(graph_results, dict):
            results.extend(graph_results.values())
        for result in results:
            if not isinstance(result, dict):
                continue
            steps = result.get("steps")
            for record in [result, *(steps if isinstance(steps, list) else [])]:
                artifacts = record.get("artifacts") if isinstance(record, dict) else None
                if isinstance(artifacts, dict):
                    record["artifacts"] = {
                        kind: _artifact_id(kind, path)
                        for kind, path in artifacts.items()
                        if kind in _ARTIFACT_KINDS and isinstance(path, str) and path
                    }


def read_artifact(runs_dir: Path, run_id: str, artifact_id: str) -> ArtifactContent:
    """Read one recorded node artifact through an opaque, contained, 64KB tail."""
    try:
        validated = validate_run_id(run_id)
    except ConfigError as exc:
        raise InvalidRunId(str(exc)) from exc
    run_dir = contained_run_dir(runs_dir, validated)
    if run_dir is None:
        raise RunNotFound(f"no recorded run {validated!r}")
    rounds = _rounds(run_dir, validated)
    recorded = _artifact_paths(rounds).get(artifact_id)
    if recorded is None:
        raise ArtifactNotFound("no recorded artifact with that id")
    kind, raw_path = recorded
    candidate = Path(raw_path)
    try:
        root = run_dir.resolve(strict=True)
        path = (candidate if candidate.is_absolute() else run_dir / candidate).resolve(strict=True)
    except OSError as exc:
        raise ArtifactNotFound("recorded artifact is unavailable") from exc
    if not path.is_file() or not path.is_relative_to(root):
        raise ArtifactNotFound("recorded artifact is unavailable")
    tail = _tail(root, tuple(path.relative_to(root).parts), _LOG_TAIL_BYTES)
    if tail is None:
        raise ArtifactNotFound("recorded artifact is unavailable")
    return {
        "id": artifact_id,
        "kind": kind,
        "content": tail,
        "truncated": path.stat().st_size > _LOG_TAIL_BYTES,
    }


def _github_root(identity: object) -> str | None:
    if not isinstance(identity, str) or not identity:
        return None
    if identity.startswith("https://github.com/"):
        parsed = urlparse(identity.removesuffix(".git").rstrip("/"))
        parts = parsed.path.strip("/").split("/")
        if parsed.netloc == "github.com" and len(parts) == 2 and all(parts):
            return f"https://github.com/{quote(parts[0])}/{quote(parts[1])}"
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", identity):
        owner, repo = identity.split("/", 1)
        return f"https://github.com/{quote(owner)}/{quote(repo)}"
    return None


def _public_url(value: object) -> str:
    if not isinstance(value, str):
        return ""
    parsed = urlparse(value)
    return value if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def _branch(value: object) -> str:
    if not isinstance(value, str) or not value or not value.isprintable():
        return ""
    return "" if value.startswith("/") or ".." in value.split("/") else value


def _commit(value: object) -> str:
    return value if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{7,64}", value) else ""


def _detail_hook_present(detail: Mapping[str, object], key: str) -> bool:
    value = detail.get(key)
    if not isinstance(value, str):
        raise ProjectionFailed(f"journal {key} must be a string path")
    return bool(value)


def _detail_bool(detail: Mapping[str, object], key: str) -> bool:
    value = detail.get(key)
    if not isinstance(value, bool):
        raise ProjectionFailed(f"journal {key} must be boolean")
    return value


def _detail_text(detail: Mapping[str, object], key: str, *, fallback: str = "") -> str:
    value = detail.get(key)
    if value is None:
        return fallback
    if not isinstance(value, str):
        raise ProjectionFailed(f"journal {key} must be a string")
    return value


def _detail_text_list(detail: Mapping[str, object], key: str) -> list[str]:
    value = detail.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ProjectionFailed(f"journal {key} must be a string list")
    return value


def _node_details(
    events: list[Event], rounds: list[Round], snapshot: DetailSnapshot
) -> dict[str, dict[str, Any]]:
    """Typed verification and publication facts, keyed by node id."""
    # `Any` is confined to this open, heterogeneous API assembly: verification and
    # publication have disjoint optional fields and are validated at each source
    # boundary before being placed in the contract dictionary.
    records: dict[str, dict[str, Any]] = {}
    artifact_ids = _artifact_paths(rounds)
    path_ids = {path: artifact_id for artifact_id, (_kind, path) in artifact_ids.items()}
    for event in events:
        if event.node is None:
            continue
        node = str(event.node)
        detail = event.detail
        record = records.setdefault(node, {"verification": {"records": []}})
        verification = record["verification"]
        match event.kind:
            case "merge-gate-coverage":
                verification.update(
                    {
                        "pre_push_hook": _detail_hook_present(detail, "pre_push_hook"),
                        "required_checks": _detail_text_list(detail, "required_checks"),
                        "required_checks_status": _detail_text(
                            detail, "required_checks_status", fallback="unknown"
                        ),
                        "expected_gate": _detail_text_list(detail, "expected_gate"),
                    }
                )
            case "verification-finished":
                item: dict[str, Any] = {
                    "ok": _detail_bool(detail, "ok"),
                    "output_tail": _detail_text(detail, "output_tail"),
                }
                log_path = detail.get("log_path")
                if isinstance(log_path, str) and log_path in path_ids:
                    item["artifact_id"] = path_ids[log_path]
                verification["records"].append(item)

    for round_record in rounds:
        results = dict(round_record["node_results"])
        graph_results = (round_record["result"] or {}).get("results", {})
        if isinstance(graph_results, dict):
            results = {**graph_results, **results}
        for node, result in results.items():
            if not isinstance(result, dict):
                continue
            record = records.setdefault(node, {"verification": {"records": []}})
            pr_url = _public_url(result.get("pr"))
            pr = next(
                (value for value in snapshot.prs.values() if value.get("url") == pr_url), None
            )
            checks = pr.get("checks", []) if isinstance(pr, dict) else []
            record["verification"]["checks"] = checks
            branch = _branch(result.get("branch"))
            base = _branch(result.get("base_branch"))
            identity = (pr or {}).get("identity") if isinstance(pr, dict) else result.get("repo")
            root = _github_root(identity)
            merged = (
                bool((pr or {}).get("merged"))
                if isinstance(pr, dict)
                else result.get("outcome") == "merged"
            )
            publication: dict[str, Any] = {"merged": merged}
            if pr_url:
                publication["pr_url"] = pr_url
            if branch:
                publication["branch"] = branch
                if root:
                    publication["branch_url"] = f"{root}/tree/{quote(branch, safe='/')}"
            if base:
                publication["base_branch"] = base
            commit = _commit(result.get("commit"))
            if not commit:
                matching = [
                    value
                    for value in snapshot.commits.values()
                    if value.get("branch") in {branch, base} and isinstance(value.get("sha"), str)
                ]
                commit = _commit(matching[-1].get("sha")) if matching else ""
            if publication["merged"] and commit:
                publication["commit"] = commit
                if root:
                    publication["commit_url"] = f"{root}/commit/{commit}"
            record["publication"] = publication
    return records


def _tail(run_dir: Path, parts: tuple[str, ...], max_bytes: int) -> str | None:
    """The last ``max_bytes`` of a log beneath ``run_dir``, or ``None`` when unusable.

    Containment is rechecked per file for the same reason it is checked per run: the
    log path is fixed, but the file at it can be a symlink to anywhere, and serving
    64kB of whatever it points at is exactly what the comment above forbids.
    """
    try:
        root = run_dir.resolve(strict=True)
        path = run_dir.joinpath(*parts).resolve(strict=True)
    except OSError:
        return None
    if not path.is_file() or not path.is_relative_to(root):
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return data[-max_bytes:].decode("utf-8", "replace")


def read_logs(run_dir: Path) -> dict[str, str]:
    """Bounded tails of the run's own logs, keyed by name and free of paths."""
    logs: dict[str, str] = {}
    for name, parts in _RUN_LOGS.items():
        tail = _tail(run_dir, parts, _LOG_TAIL_BYTES)
        if tail:
            logs[name] = tail
    return logs


#: Any UTF-16 surrogate code point. Recorded text reaches this module carrying them
#: two ways: a reader that does not pair JSON's 😀 escapes leaves one astral
#: character as two lone surrogates, and text decoded with `surrogateescape` keeps
#: each undecodable byte as \uDC80-\uDCFF. Neither survives `str.encode("utf-8")`.
_SURROGATES = re.compile("[\ud800-\udfff]")


def servable_text(value: str) -> str:
    r"""``value`` with every unpaired surrogate replaced, so the payload can be served.

    A run is only worth reading if it can be read at all. Serializing a response
    string that holds a lone surrogate raises inside the response encoder, far from
    anything that names the run, and the generic handler turns it into an opaque 500
    — the recorded run disappears from the UI with no hint why. One replacement
    character in a diff the operator is skimming is strictly the better failure.

    A *valid* surrogate pair is recombined rather than replaced: re-encoding through
    UTF-16 restores the astral character the two halves always meant, so the emoji a
    JSON-escape-unaware reader split back into ``\uD83D``/``\uDDBC`` is served as the
    picture frame it always was rather than as two question marks. Only what is
    genuinely unpaired — a truncated pair, or a ``surrogateescape`` byte — becomes
    U+FFFD.
    """
    if _SURROGATES.search(value) is None:
        return value
    return value.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


def make_servable(payload: object) -> object:
    """Repair every unpaired surrogate in ``payload``, dict keys included.

    A container is repaired *in place* and returned, so a caller holding one may
    ignore the return; only a bare string has to take it. In place because these
    payloads are freshly built and wholly owned by their one builder, and a run detail
    is megabytes of transcript — rebuilding it to touch the handful of strings that
    need it would double the peak memory of every read.
    """
    match payload:
        case str():
            return servable_text(payload)
        case dict():
            for key in [k for k in payload if isinstance(k, str) and _SURROGATES.search(k)]:
                payload[servable_text(key)] = payload.pop(key)
            for key, value in payload.items():
                payload[key] = make_servable(value)
        case list():
            payload[:] = [make_servable(item) for item in payload]
    return payload


def _node_counts(run_dir: Path, telemetry: RunTelemetry) -> dict[str, int]:
    """How many nodes of the newest round hold each authoritative ``NodeStatus``.

    Counted over the same derivation the run detail serves as ``Round.node_status``,
    so a list row and the graph it opens cannot report different graphs. The telemetry
    index alone cannot supply this: it holds one entry per node the journal recorded,
    which omits every node the scheduler gated or has yet to start.

    A run whose authoritative stream will not fold degrades to the recorded telemetry
    statuses rather than dropping the row — this view exists to show an operator a run
    that is going wrong, and a corrupt journal is exactly that.
    """
    latest = latest_round(run_dir)
    if latest is not None:
        try:
            events = read_strict_events(run_dir / JOURNAL_NAME, RunId(telemetry.run_id))
            projected = project_round(events, RunId(telemetry.run_id), latest[0])
        except ProjectionError:
            pass
        else:
            return dict(Counter(node_statuses(projected).status.values()))
    return dict(Counter(node.status for node in telemetry.nodes))


def run_summary(
    run_dir: Path,
    telemetry: RunTelemetry,
    *,
    expose_launcher_session_id: bool = False,
    now: datetime | None = None,
) -> RunSummary:
    """One ``RunSummary`` row for the run-list view."""
    summary: RunSummary = {
        "run_id": telemetry.run_id,
        "state": telemetry.state,
        "phase": telemetry.phase,
        "last_event": telemetry.last_event,
        "timing_quality": telemetry.timing_quality,
        "linkage_quality": telemetry.linkage_quality,
        "timing": telemetry.timing,
        "node_counts": _node_counts(run_dir, telemetry),
    }
    if telemetry.last_progress_at is not None:
        summary["last_progress_at"] = telemetry.last_progress_at
    launch = resolve_launch(run_dir, expose_launcher_session_id=expose_launcher_session_id, now=now)
    if launch is not None:
        summary["launch"] = launch
    return summary


def list_runs(
    runs_dir: Path,
    *,
    include_settled: bool = False,
    oneharness_bin: str = "oneharness",
    expose_launcher_session_id: bool = False,
    now: datetime | None = None,
    limit: int = 50,
    cursor: RunsCursor | None = None,
) -> RunList:
    """The ``RunList``: every watchable run, most recent progress first.

    A run whose telemetry cannot be collected — a corrupt persisted result — is
    skipped rather than failing the whole list, so one bad run never blinds the UI
    to every healthy one.

    Every run is collected against **one** `SessionScan`, so the whole list costs a
    single `oneharness history` subprocess however many runs the root holds. That
    read is the dominant cost of a summary, and it returns the same whole-store
    answer for each of them, so a per-run read made this view degrade by about a
    second per run recorded — for a view whose job is watching the live ones.
    """
    scan = SessionScan(oneharness_bin=oneharness_bin)
    summaries: list[RunSummary] = []
    for run_dir in _run_dirs(runs_dir):
        try:
            telemetry = collect_run(run_dir, scan=scan)
        except (ConfigError, HistoryError, FileNotFoundError):
            continue
        if telemetry is None:
            continue
        if not include_settled and result_state_is_terminal(telemetry.state):
            continue
        summaries.append(
            run_summary(
                run_dir,
                telemetry,
                expose_launcher_session_id=expose_launcher_session_id,
                now=now,
            )
        )
    if limit < 1 or limit > 200:
        raise InvalidRunId("limit must be between 1 and 200")
    summaries.sort(key=lambda item: (-(item.get("last_progress_at") or 0.0), item["run_id"]))
    if cursor is not None:
        try:
            decoded = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            match decoded:
                case [int() | float() as progress, str() as cursor_run_id] if not isinstance(
                    progress, bool
                ) and math.isfinite(progress):
                    pass
                case _:
                    raise ValueError
            cursor_key = (-float(progress), str(validate_run_id(cursor_run_id)))
        except (
            ValueError,
            TypeError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            binascii.Error,
        ) as exc:
            raise InvalidRunId("invalid runs cursor") from exc
        summaries = [
            item
            for item in summaries
            if (-(item.get("last_progress_at") or 0.0), item["run_id"]) > cursor_key
        ]
    page = summaries[:limit]
    result: RunList = {
        "api_version": API_VERSION,
        "telemetry_schema_version": TELEMETRY_SCHEMA_VERSION,
        "observed_at": _now(now),
        "runs": page,
    }
    if len(summaries) > limit:
        last = page[-1]
        raw = json.dumps(
            [last.get("last_progress_at") or 0.0, last["run_id"]], separators=(",", ":")
        )
        result["next_cursor"] = RunsCursor(
            base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
        )
    make_servable(result)
    return result


def _contract_plan(plan: ProjectedPlan) -> ProjectedPlan:
    """The projected plan with the goal id the contract requires.

    A run journalled before goals carried an id recorded its text alone, and the plan is
    served as recorded — so the id is derived here through `normalize_goal`, the rule
    plan loading applies, rather than by rewriting the journal.

    Only a goal `project_round` already accepted reaches this, since its own
    `parse_graph` normalizes through the same function; a malformed one is refused
    upstream as the 409 this never sees.
    """
    if plan.get("goal") is None:
        return plan
    return {**plan, "goal": normalize_goal(plan["goal"])}


def round_record(events: list[Any], run_id: RunId, round_number: int) -> Round:
    """Serialize one strict ``RoundProjection`` as the contract's ``Round``."""
    projection = project_round(events, run_id, round_number)
    statuses = node_statuses(projection)
    plan_ids = {task["id"] for task in projection.plan["tasks"]}
    if set(statuses.status) != plan_ids:
        raise ProjectionFailed("authoritative node status does not cover exactly the round plan")
    gated_ids = set(statuses.gated_by)
    blocker_ids = {blocker for blockers in statuses.gated_by.values() for blocker in blockers}
    if not gated_ids | blocker_ids <= plan_ids:
        raise ProjectionFailed("node gates name a node outside the round plan")
    return {
        "run_id": projection.run_id,
        "round": projection.round,
        "plan": _contract_plan(projection.plan),
        "node_states": projection.node_states,
        "node_status": statuses.status,
        "node_gated_by": statuses.gated_by,
        "node_results": projection.node_results,
        "attestations": list(projection.attestations),
        "result": projection.result,
        "last_seq": projection.last_seq,
    }


def _rounds(run_dir: Path, run_id: RunId) -> list[Round]:
    """Project every started round, or fail the whole detail on a corrupt stream."""
    try:
        events = read_strict_events(run_dir / JOURNAL_NAME, run_id)
        started = sorted({event.round for event in events if event.kind == "round-started"})
        return [round_record(events, run_id, number) for number in started]
    except ProjectionError as exc:
        raise ProjectionFailed(str(exc)) from exc


def run_detail(
    runs_dir: Path,
    run_id: str,
    *,
    oneharness_bin: str = "oneharness",
    expose_launcher_session_id: bool = False,
    include_conversations: bool = True,
    now: datetime | None = None,
) -> RunDetail:
    """The full ``RunDetail`` for one run: telemetry, rounds, and conversations.

    ``include_conversations=False`` serves ``conversations`` as an empty array. It is
    an opt-out, not a schema change: transcripts dominate this payload — a real run
    carries megabytes of them across hundreds of sessions — and a client that reads
    the timeline instead refetches all of it on every live update for nothing. The
    field stays required and present; the caller simply asked for nothing in it, so
    ``api_version`` is untouched.
    """
    try:
        validated = validate_run_id(run_id)
    except ConfigError as exc:
        raise InvalidRunId(str(exc)) from exc
    run_dir = contained_run_dir(runs_dir, validated)
    if run_dir is None:
        raise RunNotFound(f"no recorded run {validated!r}")
    try:
        telemetry = collect_run(run_dir, oneharness_bin=oneharness_bin)
    except ConfigError as exc:
        raise ProjectionFailed(str(exc)) from exc
    if telemetry is None:  # pragma: no cover - latest_round already proved a round exists
        raise RunNotFound(f"no recorded run {validated!r}")
    rounds = _rounds(run_dir, validated)
    snapshot = load_snapshot(run_dir)
    try:
        events = read_strict_events(run_dir / JOURNAL_NAME, validated)
    except ProjectionError as exc:
        raise ProjectionFailed(str(exc)) from exc
    node_details = _node_details(events, rounds, snapshot)
    _replace_served_artifacts_with_ids(rounds)
    detail: RunDetail = {
        "api_version": API_VERSION,
        "telemetry_schema_version": TELEMETRY_SCHEMA_VERSION,
        "observed_at": _now(now),
        "run": telemetry.record(),
        "rounds": rounds,
        "conversations": (
            run_conversations(
                validated,
                oneharness_bin=oneharness_bin,
                native_groups=native_session_groups(events),
            )
            if include_conversations
            else []
        ),
        "details": snapshot.to_record(),
        "node_details": node_details,
    }
    if logs := read_logs(run_dir):
        detail["logs"] = logs
    launch = resolve_launch(run_dir, expose_launcher_session_id=expose_launcher_session_id, now=now)
    if launch is not None:
        detail["launch"] = launch
    make_servable(detail)
    return detail


def run_conversation(
    runs_dir: Path,
    run_id: str,
    conversation_id: str,
    *,
    oneharness_bin: str = "oneharness",
) -> DagConversation:
    """One complete ``DagConversation`` addressed by its session id."""
    try:
        validated = validate_run_id(run_id)
    except ConfigError as exc:
        raise InvalidRunId(str(exc)) from exc
    wanted = validate_conversation_id(conversation_id)
    run_dir = contained_run_dir(runs_dir, validated)
    if run_dir is None:
        raise RunNotFound(f"no recorded run {validated!r}")
    # The same linkage the detail view resolves, so one transcript describes itself
    # identically however a client fetched it. A journal this route never needed
    # before must not start failing it, so an unreadable one costs only the linkage.
    try:
        native_groups = native_session_groups(read_strict_events(run_dir / JOURNAL_NAME, validated))
    except ProjectionError:
        native_groups = []
    for conversation in run_conversations(
        validated, oneharness_bin=oneharness_bin, native_groups=native_groups
    ):
        if conversation["conversation"]["id"] == wanted:
            make_servable(conversation)
            return conversation
    raise ConversationNotFound(f"no conversation {wanted!r} in run {validated!r}")


def _file_token(path: Path) -> tuple[int, int]:
    """Size and modification time of one served file; ``(0, 0)`` when it is absent."""
    try:
        stat = path.stat()
    except OSError:
        return 0, 0
    return stat.st_size, stat.st_mtime_ns


def run_signature(run_dir: Path) -> tuple[int, ...]:
    """A cheap change token over every run-directory input ``run_detail`` serves.

    The SSE layer polls this to decide whether a run changed without re-projecting
    it. Journal byte length advances on every appended authoritative event and the
    round number advances when a new round lands, but the monitor's PR/check snapshot
    and the run's logs are written *outside* that event stream — watching only the
    journal would leave the UI showing a stale PR status with no invalidation to
    correct it.

    Conversations are deliberately out of scope: they live in oneharness history
    rather than under this root, and the contract invalidates them with
    ``conversation.changed`` rather than a run signature.
    """
    latest = latest_round(run_dir)
    round_number = latest[0] if latest is not None else 0
    watched = (
        run_dir / JOURNAL_NAME,
        snapshot_path(run_dir),
        run_dir / LAUNCH_RECORD_NAME,
        *(run_dir.joinpath(*parts) for parts in _RUN_LOGS.values()),
    )
    tokens: tuple[int, ...] = ()
    for path in watched:
        tokens += _file_token(path)
    return (round_number, *tokens)
