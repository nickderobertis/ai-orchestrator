"""Human-facing views over oneharness' cross-project run history."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class HistoryError(Exception):
    """History could not be read or a requested session was not found."""


JUDGE_PREFIXES = (
    "you-are-a-strict-careful-evaluator",
    "you-are-roleplaying-the-user-in",
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


def _is_worker(session: dict[str, Any]) -> bool:
    name = session.get("name")
    return isinstance(name, str) and not name.startswith(JUDGE_PREFIXES)


def _session_records(session: dict[str, Any]) -> list[dict[str, Any]]:
    path = session.get("path")
    if not isinstance(path, str):
        raise HistoryError("oneharness history entry has no usable path")
    return _records(Path(path))


def recent_runs(limit: int, *, oneharness_bin: str = "oneharness") -> str:
    """Return a compact table of the newest worker sessions across projects."""
    if limit <= 0:
        raise HistoryError("N must be a positive integer")
    value = _run_history("list", oneharness_bin=oneharness_bin)
    if not isinstance(value, list):
        raise HistoryError("oneharness history list returned an unexpected response")
    rows = [item for item in value if isinstance(item, dict) and _is_worker(item)][:limit]
    header = (
        "UTC TIME              ID             PROJECT              TASK"
        "                         HARNESS/MODEL        STATUS"
    )
    output = [header]
    for item in rows:
        records = _session_records(item)
        latest = records[-1] if records else {}
        project = Path(str(item.get("project", "?"))).name or "?"
        session_id = str(item.get("id", "?"))
        harness = str(latest.get("harness", "?"))
        model = str(latest.get("model", "?"))
        output.append(
            f"{str(item.get('started', '?'))[:20]:20}  {session_id[-14:]:14} "
            f" {project[:20]:20} {str(item.get('name', '?'))[:28]:28} "
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
    session_id: str
    turns: int
    input_tokens: int
    output_tokens: int
    status: str
    duration_ms: int
    commands: list[str]
    text: str


def digest(records: list[dict[str, Any]], session_id: str) -> Digest:
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
        duration_ms=latest.get("duration_ms", 0)
        if isinstance(latest.get("duration_ms"), int)
        else 0,
        commands=commands[-5:],
        text=str(latest.get("text", "")),
    )


def show_run(query: str, *, oneharness_bin: str = "oneharness") -> str:
    """Resolve a substring to the newest worker session and render its digest."""
    if not query.strip():
        raise HistoryError("id-or-substring must not be empty")
    value = _run_history("list", oneharness_bin=oneharness_bin)
    if not isinstance(value, list):
        raise HistoryError("oneharness history list returned an unexpected response")
    matches = [
        item
        for item in value
        if isinstance(item, dict)
        and _is_worker(item)
        and any(query in str(item.get(key, "")) for key in ("id", "name"))
    ]
    if not matches:
        raise HistoryError(f"no worker history session matches {query!r}")
    item = matches[0]
    session_id = str(item.get("id", ""))
    records = _run_history("show", session_id, oneharness_bin=oneharness_bin)
    if not isinstance(records, list):
        raise HistoryError("oneharness history show returned an unexpected response")
    parsed = [record for record in records if isinstance(record, dict)]
    result = digest(parsed, session_id)
    commands = "\n".join(f"  $ {command}" for command in result.commands) or "  (none recorded)"
    return (
        f"Session: {result.session_id}\n"
        f"Turns: {result.turns}\n"
        f"Tokens: {result.input_tokens:,} input / {result.output_tokens:,} output\n"
        f"Latest: {result.status} ({result.duration_ms / 1000:.1f}s)\n"
        f"Recent commands:\n{commands}\n"
        f"Latest agent text:\n{result.text or '(none recorded)'}\n\n"
        f"Full detail: oneharness history show {result.session_id} --format text"
    )


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
    parser = argparse.ArgumentParser(description="Show progress for one dispatched worker.")
    parser.add_argument("session", metavar="ID-OR-SUBSTRING")
    args = parser.parse_args(argv)
    try:
        print(show_run(args.session))
    except HistoryError as exc:
        return _exit_error(exc)
    return 0
