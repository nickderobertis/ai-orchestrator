"""Read-only, loopback-bound FastAPI + SSE surface over the recorded runs.

This is the live read API the DAG UI consumes. It exposes the assembled read model
(`read_model`) as JSON and a Server-Sent-Events stream that *invalidates* — it tells
a client which run changed so the client refetches detail, rather than being a second
copy of the state model. The server never mutates a run, runs a command, accepts a
file path, or globs: run and conversation ids are validated opaque identifiers
resolved beneath one configured runs root, and the default bind is loopback.

The contract is `docs/dag-ui/design.md`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

from .config import ConfigError
from .read_model import (
    InvalidRunId,
    ProjectionFailed,
    ReadError,
    RunNotFound,
    list_runs,
    run_conversation,
    run_detail,
    run_signature,
)
from .runs import validate_run_id

DEFAULT_POLL_INTERVAL = 0.5
DEFAULT_HEARTBEAT_INTERVAL = 15.0
DEFAULT_PORT = 8787
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

_SSE_EVENTS = frozenset({"snapshot", "run.changed", "conversation.changed", "run.removed"})


def _error(status: int, code: str, message: str) -> JSONResponse:
    """Render the contract error envelope without leaking paths or record contents."""
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _status_for(exc: ReadError) -> tuple[int, str]:
    match exc:
        case InvalidRunId():
            return 422, "invalid_run_id"
        case RunNotFound():
            return 404, "run_not_found"
        case ProjectionFailed():
            return 409, "projection_error"
        case _:  # pragma: no cover - exhaustive over ReadError subclasses
            return 500, "read_error"


def _sse(cursor: int, event: str, data: dict[str, Any]) -> str:
    if event not in _SSE_EVENTS:  # pragma: no cover - guarded by callers
        raise ValueError(f"unknown SSE event {event!r}")
    payload = json.dumps(data, separators=(",", ":"), sort_keys=True)
    return f"id: {cursor}\nevent: {event}\ndata: {payload}\n\n"


def create_app(
    runs_dir: Path,
    *,
    oneharness_bin: str = "oneharness",
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
) -> FastAPI:
    """Build the read-only API bound to one runs root."""
    app = FastAPI(title="ai-orchestrator DAG read API", version="1")
    root = Path(runs_dir)

    @app.exception_handler(RequestValidationError)
    async def _on_invalid_query(_request: Request, _exc: RequestValidationError) -> JSONResponse:
        """Return the contract error envelope for a malformed query/path parameter."""
        return _error(422, "invalid_request", "invalid request parameters")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """Liveness that never touches run storage."""
        return {"status": "ok"}

    @app.get("/api/v1/runs")
    async def get_runs(include_settled: bool = False) -> Any:
        try:
            return list_runs(root, include_settled=include_settled, oneharness_bin=oneharness_bin)
        except ReadError as exc:  # pragma: no cover - list degrades rather than raising
            status, code = _status_for(exc)
            return _error(status, code, str(exc))

    @app.get("/api/v1/runs/{run_id}")
    async def get_run(run_id: str) -> Any:
        try:
            return run_detail(root, run_id, oneharness_bin=oneharness_bin)
        except ReadError as exc:
            status, code = _status_for(exc)
            return _error(status, code, str(exc))

    @app.get("/api/v1/runs/{run_id}/conversations/{conversation_id}")
    async def get_conversation(run_id: str, conversation_id: str) -> Any:
        try:
            return run_conversation(root, run_id, conversation_id, oneharness_bin=oneharness_bin)
        except ReadError as exc:
            status, code = _status_for(exc)
            return _error(status, code, str(exc))

    @app.get("/api/v1/events")
    async def events(
        request: Request,
        run_id: str | None = Query(default=None),
        after: int | None = Query(default=None),
    ) -> Any:
        watched: str | None = None
        if run_id is not None:
            try:
                watched = validate_run_id(run_id)
            except ConfigError as exc:
                return _error(422, "invalid_run_id", str(exc))
        header = request.headers.get("last-event-id")
        resume_from = after
        if resume_from is None and header is not None and header.lstrip("-").isdigit():
            resume_from = int(header)
        return StreamingResponse(
            _event_stream(
                request,
                root,
                watched,
                resume_from,
                oneharness_bin,
                poll_interval,
                heartbeat_interval,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


def _signatures(runs_dir: Path, watched: str | None) -> dict[str, tuple[int, int]]:
    """Change tokens for every run (or the single watched run) under the root."""
    if not runs_dir.is_dir():
        return {}
    signatures: dict[str, tuple[int, int]] = {}
    for entry in sorted(runs_dir.iterdir()):
        if not entry.is_dir() or (watched is not None and entry.name != watched):
            continue
        signatures[entry.name] = run_signature(entry)
    return signatures


async def _event_stream(
    request: Request,
    runs_dir: Path,
    watched: str | None,
    resume_from: int | None,
    oneharness_bin: str,
    poll_interval: float,
    heartbeat_interval: float,
) -> AsyncIterator[str]:
    """Emit a snapshot then invalidation events until the client disconnects.

    A fresh connection (no resume cursor) opens with a ``snapshot`` carrying the full
    run list; a reconnect with a cursor resumes strictly after it, accepting that our
    per-process cursors reset. Repeated changes to one run between two polls coalesce
    into a single ``run.changed``, so a busy run cannot flood the stream.
    """
    cursor = resume_from if resume_from is not None else 0
    baseline = _signatures(runs_dir, watched)
    loop = asyncio.get_event_loop()
    last_emit = loop.time()
    if resume_from is None:
        cursor += 1
        yield _sse(
            cursor,
            "snapshot",
            list_runs(runs_dir, include_settled=True, oneharness_bin=oneharness_bin),
        )
        last_emit = loop.time()
    while True:
        if await request.is_disconnected():
            return
        await asyncio.sleep(poll_interval)
        current = _signatures(runs_dir, watched)
        for name in sorted(current):
            if current[name] != baseline.get(name):
                cursor += 1
                yield _sse(cursor, "run.changed", {"run_id": name, "round": current[name][0]})
                last_emit = loop.time()
        for name in sorted(set(baseline) - set(current)):
            cursor += 1
            yield _sse(cursor, "run.removed", {"run_id": name})
            last_emit = loop.time()
        baseline = current
        now = loop.time()
        if now - last_emit >= heartbeat_interval:
            yield ": keep-alive\n\n"
            last_emit = now


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - process entrypoint
    """Serve the read API, loopback-bound by default."""
    import uvicorn

    parser = argparse.ArgumentParser(description="Serve the read-only DAG telemetry API.")
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--oneharness-bin", default="oneharness")
    parser.add_argument(
        "--allow-nonloopback",
        action="store_true",
        help="permit a non-loopback bind (deployment must add its own auth)",
    )
    args = parser.parse_args(argv)
    if args.host not in _LOOPBACK_HOSTS and not args.allow_nonloopback:
        print(
            f"server: refusing to bind non-loopback host {args.host!r} without "
            "--allow-nonloopback and deployment-supplied auth",
            file=sys.stderr,
        )
        return 2
    app = create_app(args.runs_dir, oneharness_bin=args.oneharness_bin)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
