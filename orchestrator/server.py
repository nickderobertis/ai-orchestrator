"""Read-only, loopback-bound FastAPI + SSE surface over the recorded runs.

This is the live read API the DAG UI consumes. It exposes the assembled read model
(`read_model`) as JSON and a Server-Sent-Events stream that *invalidates* — it tells
a client which run changed so the client refetches detail, rather than being a second
copy of the state model. The server never mutates a run, runs a command, accepts a
file path, or globs: run and conversation ids are validated opaque identifiers
resolved beneath one configured runs root, and the default bind is loopback.

Every read here is blocking work — filesystem walks plus an `oneharness history`
subprocess — so none of it runs on the event loop: the read routes are plain `def`
so FastAPI serves them from its threadpool, and the SSE generator defers its own
reads with `_off_loop`. Only `/healthz` and the request-parsing parts of the stream
endpoint are coroutines, because they touch no storage.

The contract is `docs/dag-ui/design.md`.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections.abc import AsyncIterator, Callable, Mapping
from enum import StrEnum
from functools import partial
from pathlib import Path
from typing import Any, TypeVar

import anyio.to_thread
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

from .config import ConfigError
from .conversations import run_conversations
from .history import HistoryError
from .read_model import (
    ConversationNotFound,
    InvalidConversationId,
    InvalidRunId,
    ProjectionFailed,
    ReadError,
    RunNotFound,
    contained_run_dir,
    list_runs,
    run_conversation,
    run_detail,
    run_signature,
)
from .runs import RunId, validate_run_id
from .timeline import run_timeline

DEFAULT_POLL_INTERVAL = 0.5
DEFAULT_HEARTBEAT_INTERVAL = 15.0
#: Conversations are polled slower than the runs root: each tick spawns a real
#: `oneharness history` subprocess, which is affordable per detail view, not per poll.
DEFAULT_CONVERSATION_INTERVAL = 5.0
DEFAULT_PORT = 8787
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class SseEvent(StrEnum):
    """The closed SSE ``event`` vocabulary fixed by ``docs/dag-ui/design.md``.

    ``check-dag-state-contract`` reconciles these members against that contract, so a
    name added on one side without the other fails the gate.
    """

    SNAPSHOT = "snapshot"
    RUN_CHANGED = "run.changed"
    CONVERSATION_CHANGED = "conversation.changed"
    RUN_REMOVED = "run.removed"


def _error(status: int, code: str, message: str) -> JSONResponse:
    """Render the contract error envelope without leaking paths or record contents."""
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _status_for(exc: ReadError) -> tuple[int, str]:
    match exc:
        case InvalidRunId():
            return 422, "invalid_run_id"
        case InvalidConversationId():
            return 422, "invalid_conversation_id"
        case RunNotFound():
            return 404, "run_not_found"
        case ConversationNotFound():
            return 404, "conversation_not_found"
        case ProjectionFailed():
            return 409, "projection_error"
        case _:  # pragma: no cover - exhaustive over ReadError subclasses
            return 500, "read_error"


def _parse_cursor(value: str | None) -> int | None:
    """Parse an SSE resume cursor, tolerating any malformed ``Last-Event-ID``.

    A crafted header must never crash the stream, so anything this process could not
    have issued — unparseable, or negative like the ``after`` query's ``ge=0`` bound
    rejects — falls back to ``None`` and the connection opens with a fresh snapshot.
    """
    if value is None:
        return None
    try:
        cursor = int(value)
    except ValueError:
        return None
    return cursor if cursor >= 0 else None


def _sse(cursor: int, event: SseEvent, data: Mapping[str, Any]) -> str:
    payload = json.dumps(data, separators=(",", ":"), sort_keys=True)
    return f"id: {cursor}\nevent: {event.value}\ndata: {payload}\n\n"


_T = TypeVar("_T")


async def _off_loop(call: Callable[[], _T]) -> _T:
    """Await one blocking read on a worker thread rather than on the event loop.

    A coroutine that called these reads directly would hold the loop for the whole
    read — seconds of subprocess and filesystem work — which stalls *every* other
    request, every other live stream, and `/healthz` besides, so one slow scan
    presented as a hung server rather than as one slow response. The route handlers
    get this by being plain `def`; the stream is a coroutine by nature, so it defers
    each blocking call here instead. Only the call itself moves: the stream's own
    cursor and baselines stay on the loop, so nothing crosses threads.
    """
    return await anyio.to_thread.run_sync(call)


def create_app(
    runs_dir: Path,
    *,
    oneharness_bin: str = "oneharness",
    expose_launcher_session_id: bool = False,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
    conversation_interval: float = DEFAULT_CONVERSATION_INTERVAL,
) -> FastAPI:
    """Build the read-only API bound to one runs root.

    ``expose_launcher_session_id`` is the redaction switch: the launching session id
    may be sensitive, so it is withheld by default and surfaced only when a deployment
    explicitly opts in.

    Raises ``ValueError`` for a non-positive interval: a zero or negative poll would
    turn the stream's ``await sleep`` into a busy loop that starves the event loop.
    """
    for label, interval in (
        ("poll_interval", poll_interval),
        ("heartbeat_interval", heartbeat_interval),
        ("conversation_interval", conversation_interval),
    ):
        if not interval > 0:
            raise ValueError(f"{label} must be a positive number of seconds, got {interval!r}")
    app = FastAPI(title="ai-orchestrator DAG read API", version="1")
    root = Path(runs_dir)

    @app.exception_handler(RequestValidationError)
    async def _on_invalid_query(_request: Request, _exc: RequestValidationError) -> JSONResponse:
        """Return the contract error envelope for a malformed query/path parameter."""
        return _error(422, "invalid_request", "invalid request parameters")

    @app.exception_handler(Exception)
    async def _on_unexpected(_request: Request, _exc: Exception) -> JSONResponse:
        """Return the contract error envelope for an unanticipated read failure.

        Without this the framework's own 500 body would break the envelope every
        other response honors. The exception is deliberately not rendered: it can
        carry a filesystem path or record contents, which the contract forbids
        leaking. It still reaches the server log for the operator.
        """
        return _error(500, "read_error", "unexpected read failure")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """Liveness that never touches run storage."""
        return {"status": "ok"}

    # Every read route below is annotated `-> Any` rather than with its payload type.
    # Each returns *either* that payload or the contract error envelope, and FastAPI
    # treats a handler's return annotation as its `response_model`: declaring the
    # payload would make the framework validate — and silently reshape — the error
    # body against it, which is exactly the envelope `_error` exists to keep intact.
    # The payload shapes stay enforced where they are built, in the read model, and
    # reconciled against the contract by `check-dag-state-contract`.
    #
    # These four are deliberately `def`, not `async def`: each is blocking storage
    # work, and FastAPI runs a non-async handler in its threadpool, so one slow read
    # occupies a worker rather than the loop every other request shares.
    @app.get("/api/v1/runs")
    def get_runs(include_settled: bool = False) -> Any:
        try:
            return list_runs(
                root,
                include_settled=include_settled,
                oneharness_bin=oneharness_bin,
                expose_launcher_session_id=expose_launcher_session_id,
            )
        except ReadError as exc:  # pragma: no cover - list degrades rather than raising
            status, code = _status_for(exc)
            return _error(status, code, str(exc))

    @app.get("/api/v1/runs/{run_id}")
    def get_run(run_id: str, include_conversations: bool = True) -> Any:
        """The run detail; ``include_conversations=false`` serves no transcripts.

        The opt-out is a size lever, not a version change: `conversations` stays
        required and present, empty, for a client that reads the timeline instead of
        re-downloading every transcript on each live update.
        """
        try:
            return run_detail(
                root,
                run_id,
                oneharness_bin=oneharness_bin,
                expose_launcher_session_id=expose_launcher_session_id,
                include_conversations=include_conversations,
            )
        except ReadError as exc:
            status, code = _status_for(exc)
            return _error(status, code, str(exc))

    @app.get("/api/v1/runs/{run_id}/timeline")
    def get_timeline(run_id: str) -> Any:
        """The whole run's ordered spans and events; a consumer filters by node."""
        try:
            return run_timeline(root, run_id, oneharness_bin=oneharness_bin)
        except ReadError as exc:
            status, code = _status_for(exc)
            return _error(status, code, str(exc))

    @app.get("/api/v1/runs/{run_id}/conversations/{conversation_id}")
    def get_conversation(run_id: str, conversation_id: str) -> Any:
        try:
            return run_conversation(root, run_id, conversation_id, oneharness_bin=oneharness_bin)
        except ReadError as exc:
            status, code = _status_for(exc)
            return _error(status, code, str(exc))

    @app.get("/api/v1/events")
    async def events(
        request: Request,
        run_id: str | None = Query(default=None),
        # Cursors are server-issued and monotonically increasing from zero; a negative
        # resume point cannot name a frame this process ever emitted.
        after: int | None = Query(default=None, ge=0),
    ) -> Any:
        watched: str | None = None
        if run_id is not None:
            try:
                watched = validate_run_id(run_id)
            except ConfigError as exc:
                return _error(422, "invalid_run_id", str(exc))
        resume_from = (
            after if after is not None else _parse_cursor(request.headers.get("last-event-id"))
        )
        return StreamingResponse(
            _event_stream(
                request,
                root,
                watched,
                resume_from,
                oneharness_bin,
                poll_interval,
                heartbeat_interval,
                conversation_interval,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


def _conversation_signature(
    watched: str | None, oneharness_bin: str
) -> tuple[tuple[str, str], ...]:
    """Session id and content digest per conversation of the watched run, or ``()``.

    Digesting the whole served conversation rather than counting its turns is what
    makes an *edited* turn visible: the turn a live agent is still writing keeps its
    position while its text, status, usage, and tool events all change, so a count
    would report nothing until the next turn began.

    History is a separate, slower source than the runs root, so this is the only part
    of a poll that spawns a subprocess; the digest is negligible beside it. A missing
    or unreadable store degrades to an empty signature rather than ending the stream.
    """
    if watched is None:
        return ()
    try:
        found = run_conversations(RunId(watched), oneharness_bin=oneharness_bin)
    except (HistoryError, ConfigError):
        return ()
    return tuple(
        (
            item["conversation"]["id"],
            hashlib.sha256(
                json.dumps(item, separators=(",", ":"), sort_keys=True).encode()
            ).hexdigest(),
        )
        for item in found
    )


def _signatures(runs_dir: Path, watched: str | None) -> dict[str, tuple[int, ...]]:
    """Change tokens for every run (or the single watched run) under the root.

    A directory name becomes a ``run_id`` in an emitted event, so it is validated like
    any other identifier: whatever else the root contains, this stream never hands a
    client an id the API would reject on the way back in.
    """
    if not runs_dir.is_dir():
        return {}
    signatures: dict[str, tuple[int, ...]] = {}
    for entry in sorted(runs_dir.iterdir()):
        if watched is not None and entry.name != watched:
            continue
        try:
            validate_run_id(entry.name)
        except ConfigError:
            continue
        # Containment, not just a valid name: a symlinked entry must not make the
        # stream watch — or name in an event — a tree outside the configured root.
        if (run_dir := contained_run_dir(runs_dir, entry.name)) is None:
            continue
        signatures[entry.name] = run_signature(run_dir)
    return signatures


async def _event_stream(
    request: Request,
    runs_dir: Path,
    watched: str | None,
    resume_from: int | None,
    oneharness_bin: str,
    poll_interval: float,
    heartbeat_interval: float,
    conversation_interval: float = DEFAULT_CONVERSATION_INTERVAL,
) -> AsyncIterator[str]:
    """Emit a snapshot then invalidation events until the client disconnects.

    *Every* connection opens with a ``snapshot``, including a reconnect carrying a
    cursor. This process retains no event history, so it cannot replay what a
    disconnected client missed; issuing a snapshot is the only way the client cannot
    silently sit on stale state. A resume cursor therefore only continues the id
    sequence, keeping ids monotonic across a reconnect within one process.

    Repeated changes to one run between two polls coalesce into a single
    ``run.changed``, so a busy run cannot flood the stream. Conversations live in
    oneharness history rather than under the runs root, so they are polled on their
    own slower interval and only for a single watched run — one subprocess per tick
    is affordable for a detail view, one per run is not.

    Every read here goes through `_off_loop`: the opening snapshot scans the whole
    root and each conversation poll spawns a subprocess, and running either inline
    would make one stream's tick stall every other connection the server holds.
    """
    cursor = resume_from if resume_from is not None else 0
    baseline = await _off_loop(partial(_signatures, runs_dir, watched))
    conversations = await _off_loop(partial(_conversation_signature, watched, oneharness_bin))
    loop = asyncio.get_event_loop()
    cursor += 1
    yield _sse(
        cursor,
        SseEvent.SNAPSHOT,
        await _off_loop(
            partial(list_runs, runs_dir, include_settled=True, oneharness_bin=oneharness_bin)
        ),
    )
    last_emit = loop.time()
    last_conversation_poll = loop.time()
    while True:
        if await request.is_disconnected():
            return
        await asyncio.sleep(poll_interval)
        current = await _off_loop(partial(_signatures, runs_dir, watched))
        for name in sorted(current):
            if current[name] != baseline.get(name):
                cursor += 1
                changed = {"run_id": name, "round": current[name][0]}
                yield _sse(cursor, SseEvent.RUN_CHANGED, changed)
                last_emit = loop.time()
        for name in sorted(set(baseline) - set(current)):
            cursor += 1
            yield _sse(cursor, SseEvent.RUN_REMOVED, {"run_id": name})
            last_emit = loop.time()
        baseline = current
        now = loop.time()
        if watched is not None and now - last_conversation_poll >= conversation_interval:
            last_conversation_poll = now
            latest = await _off_loop(partial(_conversation_signature, watched, oneharness_bin))
            if latest != conversations:
                conversations = latest
                cursor += 1
                yield _sse(cursor, SseEvent.CONVERSATION_CHANGED, {"run_id": watched})
                last_emit = loop.time()
                now = loop.time()
        if now - last_emit >= heartbeat_interval:
            yield ": keep-alive\n\n"
            last_emit = now


def _port(value: str) -> int:
    """An in-range TCP port, rejected by argparse rather than deep inside uvicorn."""
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"port must be an integer, got {value!r}") from None
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"port must be between 1 and 65535, got {port}")
    return port


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - process entrypoint
    """Serve the read API, loopback-bound by default."""
    import uvicorn

    parser = argparse.ArgumentParser(description="Serve the read-only DAG telemetry API.")
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=_port, default=DEFAULT_PORT)
    parser.add_argument("--oneharness-bin", default="oneharness")
    parser.add_argument(
        "--allow-nonloopback",
        action="store_true",
        help="permit a non-loopback bind (deployment must add its own auth)",
    )
    parser.add_argument(
        "--expose-launcher-session-id",
        action="store_true",
        help="surface the (possibly sensitive) launcher session id in the read API",
    )
    args = parser.parse_args(argv)
    if args.host not in _LOOPBACK_HOSTS and not args.allow_nonloopback:
        print(
            f"server: refusing to bind non-loopback host {args.host!r} without "
            "--allow-nonloopback and deployment-supplied auth",
            file=sys.stderr,
        )
        return 2
    app = create_app(
        args.runs_dir,
        oneharness_bin=args.oneharness_bin,
        expose_launcher_session_id=args.expose_launcher_session_id,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
