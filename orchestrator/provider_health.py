"""Read-only provider capacity snapshot shared by planner views and the API."""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

from .journal import JOURNAL_NAME, read_events

IDENTITIES = (
    "claude-code:alternate",
    "claude-code:alternate2",
    "codex",
    "codex:alternate",
    "claude-code:primary",
)
#: Set to ``0`` to answer every view with unknown identities instead of probing.
#: The probe is read-only and cheap, but it does reach the configured providers, so
#: an offline or metered host — and this repository's own suite, which must never
#: touch a paid identity — turns it off here rather than by guessing from other
#: environment variables what kind of process is asking.
PROBE_ENV = "ORCHESTRATOR_PROVIDER_HEALTH_PROBE"
#: Bounded because a view must render whatever the providers are doing. Every exit
#: from `_read_usage` — answer, refusal, timeout, or a wrapper that cannot start —
#: leaves no probe process behind.
PROBE_TIMEOUT_SECONDS = 8.0
_USAGE_WRAPPER = Path(__file__).resolve().parents[1] / "scripts" / "oneharness-usage.sh"
#: One usage answer serves every view for this long. The read API re-lists runs on
#: each poll, so without it a watched run would spawn a probe per second.
_CACHE: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_CACHE_SECONDS = 30.0

#: The one boundary that spawns anything. Injected by tests of the parsing and
#: merging above it, so proving those never launches a subprocess at all.
UsageReader = Callable[[str, Path], str | None]


def probing_enabled() -> bool:
    """Whether an ambient caller may spend a probe on the configured providers."""
    return os.environ.get(PROBE_ENV, "1").strip().lower() not in {"0", "off", "false", "no"}


def _read_usage(oneharness_bin: str, cwd: Path) -> str | None:
    """Run the usage wrapper to completion or kill it; never leave one running."""
    try:
        completed = subprocess.run(
            [
                str(_USAGE_WRAPPER),
                "--harness",
                ",".join(IDENTITIES),
                "--cwd",
                str(cwd),
                "--format",
                "json",
                "--compact",
            ],
            text=True,
            capture_output=True,
            env={**os.environ, "ONEHARNESS_BIN": oneharness_bin},
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        # `subprocess.run` kills and waits for the child before it raises
        # `TimeoutExpired`, so the timeout path reaps as surely as the others.
        return None
    return completed.stdout if completed.returncode == 0 else None


def unknown_snapshot() -> dict[str, Any]:
    """Every configured identity, listed and explicitly unknown."""
    return {
        "schema_version": "0.1",
        "identities": [
            {"identity": identity, "availability": {"state": "unknown"}} for identity in IDENTITIES
        ],
    }


def probe(
    *,
    oneharness_bin: str = "oneharness",
    cwd: Path | None = None,
    read_usage: UsageReader | None = None,
) -> dict[str, Any]:
    """Probe every configured identity; failure is data and never blocks a view.

    ``read_usage`` is the caller's own boundary and is always used when given —
    `PROBE_ENV` governs only the ambient default, which is what a view reaches for.
    """
    selected_cwd = (cwd or Path.cwd()).resolve()
    if read_usage is None:
        if not probing_enabled():
            return unknown_snapshot()
        read_usage = _read_usage
    key = (oneharness_bin, str(selected_cwd))
    cached = _CACHE.get(key)
    if cached is not None and time.monotonic() - cached[0] < _CACHE_SECONDS:
        return cached[1]
    unknown = unknown_snapshot()
    stdout = read_usage(oneharness_bin, selected_cwd)
    try:
        value = json.loads(stdout) if stdout else None
    except json.JSONDecodeError:
        value = None
    if not isinstance(value, dict) or not isinstance(value.get("identities"), list):
        _CACHE[key] = (time.monotonic(), unknown)
        return unknown
    by_name: dict[str, dict[str, Any]] = {}
    for item in value["identities"]:
        if not isinstance(item, dict) or not isinstance(item.get("harness"), str):
            continue
        name = item["harness"] + (f":{item['variant']}" if item.get("variant") else "")
        by_name[name] = {**item, "identity": name}
    value["identities"] = [
        by_name.get(identity, unknown["identities"][index])
        for index, identity in enumerate(IDENTITIES)
    ]
    _CACHE[key] = (time.monotonic(), value)
    return value


def forget_probes() -> None:
    """Drop every cached usage answer, so the next probe asks again."""
    _CACHE.clear()


def render(snapshot: dict[str, Any]) -> str:
    """Compact operator-facing rendering, including every unknown identity."""
    lines = ["Provider health:"]
    identities = snapshot.get("identities")
    for item in identities if isinstance(identities, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("identity") or item.get("harness") or "unknown")
        availability = item.get("availability")
        if not isinstance(availability, dict) or availability.get("state") != "available":
            lines.append(f"  {name}: unknown")
            continue
        windows = availability.get("windows")
        rendered: list[str] = []
        for window in windows if isinstance(windows, list) else []:
            if not isinstance(window, dict):
                continue
            usage = window.get("usage")
            used = usage.get("used_percent") if isinstance(usage, dict) else None
            utilization = (
                f"{float(used) * 100:g}% used"
                if isinstance(used, (int, float))
                else "unknown utilization"
            )
            binding = "binding" if used == 1 or window.get("binding") is True else "non-binding"
            reset = (
                f", resets {window['resets_at']}"
                if isinstance(window.get("resets_at"), str)
                else ""
            )
            rendered.append(f"{window.get('id', 'window')} {binding}, {utilization}{reset}")
        lines.append(f"  {name}: " + ("; ".join(rendered) or "available, windows unknown"))
    return "\n".join(lines)


class Refusal(NamedTuple):
    """What makes two provider failures the same line in a planner's view.

    Named rather than a bare 4-tuple because it is built in one place and read in
    another: a reordering of positional fields would silently swap the side with
    the identity in every rendered line, which is exactly the misreading the
    rollup exists to prevent.
    """

    side: str
    identity: str
    cause: str
    reset_time: str


def failure_rollups(run_dir: Path) -> list[str]:
    """Collapse repeated provider failures into one immediately legible line."""
    try:
        events = read_events(run_dir / JOURNAL_NAME)
    except OSError:
        return []
    facts: Counter[Refusal] = Counter()
    for event in events:
        if event.kind not in {"node-failed", "step-settled"}:
            continue
        value = event.detail.get("failure_attribution")
        if not isinstance(value, dict):
            result = event.detail.get("result")
            value = result.get("failure_attribution") if isinstance(result, dict) else None
        if not isinstance(value, dict):
            continue
        facts[
            Refusal(
                side=str(value.get("side", "unknown")),
                identity=str(value.get("identity", "unknown")),
                cause=str(value.get("cause", "unknown")),
                reset_time=str(value.get("reset_time", "")),
            )
        ] += 1
    lines: list[str] = []
    for refusal, count in facts.items():
        noun = "node failed" if count == 1 else "nodes failed"
        suffix = f", resets {refusal.reset_time}" if refusal.reset_time else ""
        lines.append(
            f"{count} {noun} on {refusal.side}-side {refusal.identity} "
            f"{refusal.cause.replace('_', ' ')}{suffix}"
        )
    return lines
