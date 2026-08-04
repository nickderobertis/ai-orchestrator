"""Read-only provider capacity snapshot shared by planner views and the API."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any

from .journal import JOURNAL_NAME, read_events

IDENTITIES = (
    "claude-code:alternate",
    "claude-code:alternate2",
    "codex",
    "codex:alternate",
    "claude-code:primary",
)
_CACHE: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_CACHE_SECONDS = 30.0


def probe(*, oneharness_bin: str = "oneharness", cwd: Path | None = None) -> dict[str, Any]:
    """Probe every configured identity; failure is data and never blocks a view."""
    selected_cwd = cwd or Path.cwd()
    key = (oneharness_bin, str(selected_cwd.resolve()))
    cached = _CACHE.get(key)
    if cached is not None and time.monotonic() - cached[0] < _CACHE_SECONDS:
        return cached[1]
    unknown = {
        "schema_version": "0.1",
        "identities": [
            {"identity": identity, "availability": {"state": "unknown"}} for identity in IDENTITIES
        ],
    }
    # A redirected history store is the deterministic command-provider/test mode;
    # it has no relationship to ambient paid identities. Its explicit usage seam is
    # tested by calling this function with a patched subprocess boundary.
    if os.environ.get("ONEHARNESS_HISTORY_DIR"):
        _CACHE[key] = (time.monotonic(), unknown)
        return unknown
    # Read-model tests and embedded callers commonly point history at a purpose-built
    # executable. It implements the history protocol, not the usage command; probing
    # it can strand its fake provider children. Only the configured oneharness CLI is
    # the usage boundary. Tests of this boundary patch subprocess under the default.
    if Path(oneharness_bin).name != "oneharness":
        _CACHE[key] = (time.monotonic(), unknown)
        return unknown
    resolved = shutil.which("oneharness")
    if (
        "/" in oneharness_bin
        and resolved is not None
        and Path(oneharness_bin).resolve() != Path(resolved).resolve()
    ):
        _CACHE[key] = (time.monotonic(), unknown)
        return unknown
    try:
        proc = subprocess.run(
            [
                oneharness_bin,
                "usage",
                "--harness",
                ",".join(IDENTITIES),
                "--cwd",
                str(selected_cwd),
                "--format",
                "json",
                "--compact",
            ],
            text=True,
            capture_output=True,
            timeout=8,
            check=False,
        )
        value = json.loads(proc.stdout) if proc.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        _CACHE[key] = (time.monotonic(), unknown)
        return unknown
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


def failure_rollups(run_dir: Path) -> list[str]:
    """Collapse repeated provider failures into one immediately legible line."""
    try:
        events = read_events(run_dir / JOURNAL_NAME)
    except OSError:
        return []
    facts: Counter[tuple[str, str, str, str]] = Counter()
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
            (
                str(value.get("side", "unknown")),
                str(value.get("identity", "unknown")),
                str(value.get("cause", "unknown")),
                str(value.get("reset_time", "")),
            )
        ] += 1
    lines: list[str] = []
    for (side, identity, cause, reset), count in facts.items():
        noun = "node failed" if count == 1 else "nodes failed"
        suffix = f", resets {reset}" if reset else ""
        lines.append(f"{count} {noun} on {side}-side {identity} {cause.replace('_', ' ')}{suffix}")
    return lines
