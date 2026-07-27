"""Launching-session provenance for ``just orchestrate``.

Implements the ``LaunchProvenance`` scheme fixed by ``docs/dag-ui/design.md``.

Why the record lives outside the repository: ``launcher_session_id`` may be
sensitive, so a checkout only ever holds the non-sensitive ``launch_id`` that joins
to it. Missing or expired provenance degrades a run's launcher to ``"unknown"``
rather than failing the read.
"""

from __future__ import annotations

import os
import re
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NewType, TypedDict, TypeGuard

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

#: A launch id is a security-sensitive join token, not incidental text: distinguishing
#: it from an arbitrary string keeps an unvalidated one from reaching a lookup.
LaunchId = NewType("LaunchId", str)

_LAUNCH_ID = re.compile(r"[0-9a-f]{32}\Z")
_MAX_SESSION_ID = 256
#: Tolerance for a record written by a host whose clock runs slightly ahead of ours.
#: Beyond it the timestamp is not skew but a forged or corrupt record that would
#: otherwise outlive every expiry check, so it is treated as invalid.
_MAX_CLOCK_SKEW_SECONDS = 300


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


def generate_launch_id() -> LaunchId:
    """A fresh random 128-bit launch id, lowercase hex."""
    return LaunchId(secrets.token_hex(16))


def validate_launch_id(value: object) -> LaunchId | None:
    """Return ``value`` when it is a well-formed launch id, else ``None``."""
    return LaunchId(value) if isinstance(value, str) and _LAUNCH_ID.match(value) else None


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
    """Return a single-line, bounded session id, or ``None`` when absent.

    ``isprintable`` is the single-line test: it rejects NUL, newline, carriage return,
    and every other control character in one check, so no separator can smuggle a
    second line into a log or history label.
    """
    if not value:
        return None
    if not value.isprintable() or len(value) > _MAX_SESSION_ID:
        raise LaunchError(
            f"launcher session id must be a single printable line of at most "
            f"{_MAX_SESSION_ID} chars"
        )
    return value


def _identity_ok(value: object) -> TypeGuard[str]:
    """Whether a repository identity is a bounded, single printable line.

    Applied by both the writer and the reader: the record is an out-of-repo file, so
    a value this reader would refuse must never be written, and a value another
    process wrote must never be trusted.
    """
    return isinstance(value, str) and value.isprintable() and len(value) <= _MAX_SESSION_ID


def provenance_dir() -> Path:
    """The out-of-repo directory holding provenance records for this user."""
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state"
    )
    return Path(base) / "ai-orchestrator" / "launches"


def provenance_path(launch_id: LaunchId) -> Path:
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

    Every field is validated here rather than trusted from the caller: this is the
    only writer of the sensitive join target the server later resolves, so a record
    this writer accepts must be one the reader will accept back. Writing a value only
    the read side rejects would produce a record that silently never joins.
    """
    valid_id = validate_launch_id(launch_id)
    if valid_id is None:
        raise LaunchError("launch id must be 32 lowercase hex characters")
    if launcher not in KNOWN_LAUNCHERS:
        raise LaunchError("provenance is written only for a known launcher")
    session_id = validate_session_id(launcher_session_id)
    if session_id is None:
        raise LaunchError("provenance requires a non-empty launcher session id")
    if not _identity_ok(repository_identity):
        raise LaunchError(
            f"repository identity must be a single printable line of at most "
            f"{_MAX_SESSION_ID} chars"
        )
    stamped = started_at or datetime.now(UTC).isoformat()
    if _utc(stamped) is None:
        raise LaunchError("started_at must be an RFC 3339 UTC timestamp")
    record: LaunchProvenance = {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "launch_id": valid_id,
        "launcher": launcher,
        "launcher_session_id": session_id,
        "started_at": stamped,
        "repository_identity": repository_identity,
    }
    path = provenance_path(valid_id)
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
    raw_session_id = raw.get("launcher_session_id")
    identity = raw.get("repository_identity")
    started = _utc(raw.get("started_at"))
    # Re-applied on read, not just on write: the record is an out-of-repo file another
    # process (or a hand edit) can have replaced since we wrote it.
    try:
        session_id = (
            validate_session_id(raw_session_id) if isinstance(raw_session_id, str) else None
        )
    except LaunchError:
        return None
    if not (
        raw.get("schema_version") == PROVENANCE_SCHEMA_VERSION
        and raw.get("launch_id") == valid_id
        and launcher in KNOWN_LAUNCHERS
        and session_id is not None
        and _identity_ok(identity)
        and started is not None
    ):
        return None
    age = (now or datetime.now(UTC)) - started
    if age > timedelta(seconds=max_age_seconds) or -age > timedelta(
        seconds=_MAX_CLOCK_SKEW_SECONDS
    ):
        return None
    return LaunchProvenance(
        schema_version=PROVENANCE_SCHEMA_VERSION,
        launch_id=valid_id,
        launcher=launcher,
        launcher_session_id=session_id,
        started_at=raw["started_at"],
        repository_identity=identity,
    )
