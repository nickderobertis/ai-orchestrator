"""Launching-session provenance for ``just orchestrate``.

Implements the ``LaunchProvenance`` scheme fixed by
``docs/dag-ui/design.md``: a random 128-bit ``launch_id`` created at launch time, a
short-lived provenance record written **outside the repository** (it holds the
possibly-sensitive ``launcher_session_id``), and the ``launch_id`` + ``launcher``
history labels that let the read API join any worker/judge/orchestrator conversation
back to its launching session.

The split is deliberate: ``launch_id`` and ``launcher`` are non-sensitive and travel
on history labels and in the run directory, while ``launcher_session_id`` lives only
in the protected out-of-repo record and is surfaced by the server solely when its
redaction policy permits. Missing or expired provenance degrades a run's launcher to
``"unknown"`` without disturbing the graph attribution carried by the labels.
"""

from __future__ import annotations

import os
import re
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypedDict

from .config import ConfigError
from .coordination import atomic_json
from .runs import load_mapping

#: The top-level harnesses a launch may come from. ``unknown`` is the recorded
#: launcher when no provenance identifies the session, so a run is never silently
#: mis-attributed to a harness it did not come from. This is the single source of
#: truth for the enum; ``dispatch`` and ``read_model`` both import it.
LAUNCHER_KINDS: frozenset[str] = frozenset({"claude-code", "codex", "unknown"})

#: Launchers that identify a joinable session; only these get a provenance record.
KNOWN_LAUNCHERS: frozenset[str] = frozenset({"claude-code", "codex"})

PROVENANCE_SCHEMA_VERSION = 1
#: A launch record is short-lived: past this age the server treats it as expired and
#: reports ``launcher: "unknown"`` rather than resurfacing a stale session id.
DEFAULT_MAX_AGE_SECONDS = 7 * 24 * 3600

_LAUNCH_ID = re.compile(r"[0-9a-f]{32}\Z")
_MAX_SESSION_ID = 256


class LaunchError(ValueError):
    """A launcher kind or session id violates the provenance contract."""


class LaunchInfo(TypedDict):
    """The non-sensitive run->launch link persisted in the run directory."""

    launch_id: str


class LaunchProvenance(TypedDict):
    """The protected out-of-repo record joined by ``launch_id``."""

    schema_version: int
    launch_id: str
    launcher: str
    launcher_session_id: str
    started_at: str
    repository_identity: str


def generate_launch_id() -> str:
    """A fresh random 128-bit launch id, lowercase hex."""
    return secrets.token_hex(16)


def validate_launch_id(value: object) -> str | None:
    """Return ``value`` when it is a well-formed launch id, else ``None``."""
    return value if isinstance(value, str) and _LAUNCH_ID.match(value) else None


def resolve_launcher_kind(kind: str | None) -> str:
    """Validate a launcher kind, defaulting a missing one to ``unknown``.

    A missing launcher is a normal state (a hand-run ``just orchestrate``); a *wrong*
    value would corrupt session grouping, so an unrecognised harness is loud.
    """
    resolved = kind if kind else "unknown"
    if resolved not in LAUNCHER_KINDS:
        raise LaunchError("launcher kind must be one of " + ", ".join(sorted(LAUNCHER_KINDS)))
    return resolved


def validate_session_id(value: str | None) -> str | None:
    """Return a single-line, bounded session id, or ``None`` when absent."""
    if not value:
        return None
    if "\x00" in value or "\n" in value or len(value) > _MAX_SESSION_ID:
        raise LaunchError("launcher session id must be a single line of at most 256 chars")
    return value


def provenance_dir() -> Path:
    """The out-of-repo directory holding provenance records for this user."""
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state"
    )
    return Path(base) / "ai-orchestrator" / "launches"


def provenance_path(launch_id: str) -> Path:
    return provenance_dir() / f"{launch_id}.json"


def write_provenance(
    *,
    launch_id: str,
    launcher: str,
    launcher_session_id: str,
    repository_identity: str,
    started_at: str | None = None,
) -> Path:
    """Persist the protected provenance record outside the repository.

    Only called for a known launcher with a session id — the sensitive join target
    the server later resolves under its redaction policy.
    """
    if validate_launch_id(launch_id) is None:
        raise LaunchError("launch id must be 32 lowercase hex characters")
    if launcher not in KNOWN_LAUNCHERS:
        raise LaunchError("provenance is written only for a known launcher")
    record: LaunchProvenance = {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "launch_id": launch_id,
        "launcher": launcher,
        "launcher_session_id": launcher_session_id,
        "started_at": started_at or datetime.now(UTC).isoformat(),
        "repository_identity": repository_identity,
    }
    path = provenance_path(launch_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, record)
    return path


def _utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.endswith(("Z", "+00:00")):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def read_provenance(
    launch_id: str,
    *,
    max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
    now: datetime | None = None,
) -> LaunchProvenance | None:
    """Read and validate one provenance record; ``None`` if missing/expired/invalid.

    Every field is checked at this trust boundary: a record that fails validation or
    is older than ``max_age_seconds`` is treated as absent so the server falls back to
    ``launcher: "unknown"`` rather than trusting a stale or malformed join.
    """
    valid_id = validate_launch_id(launch_id)
    if valid_id is None:
        return None
    try:
        raw = load_mapping(provenance_path(valid_id))
    except (ConfigError, OSError):
        return None
    launcher = raw.get("launcher")
    session_id = raw.get("launcher_session_id")
    identity = raw.get("repository_identity")
    started = _utc(raw.get("started_at"))
    if not (
        raw.get("schema_version") == PROVENANCE_SCHEMA_VERSION
        and raw.get("launch_id") == valid_id
        and launcher in KNOWN_LAUNCHERS
        and isinstance(session_id, str)
        and session_id
        and isinstance(identity, str)
        and started is not None
    ):
        return None
    if (now or datetime.now(UTC)) - started > timedelta(seconds=max_age_seconds):
        return None
    return LaunchProvenance(
        schema_version=PROVENANCE_SCHEMA_VERSION,
        launch_id=valid_id,
        launcher=launcher,
        launcher_session_id=session_id,
        started_at=raw["started_at"],
        repository_identity=identity,
    )
