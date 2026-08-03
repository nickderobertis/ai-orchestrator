"""Launching-session provenance for ``just orchestrate``.

Implements the ``LaunchProvenance`` scheme fixed by ``docs/dag-ui/design.md``.

Why the record lives outside the repository: ``launcher_session_id`` may be
sensitive, so a checkout only ever holds non-sensitive values — the ``launch_id``
that joins to the protected record, and the irreversible `session_key` derived
from the session id. The key is what makes attribution *durable*: the protected
record is short-lived and lives outside the runs root, so joining to it is the only
thing that can expire, and a run that has recorded its own key still names the
session that launched it once the record is gone. Missing or expired provenance
degrades only what the key cannot carry — never the attribution itself.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NewType, NotRequired, TypedDict, TypeGuard

from .config import ConfigError
from .coordination import atomic_json
from .runs import load_mapping

#: Hex characters of the session digest a run records. 128 bits: the key stands in
#: for the session id in every ownership comparison, so it has to be wide enough
#: that two live planners cannot collide into each other's runs.
SESSION_KEY_CHARS = 32
#: Hex characters of the short form printed on a planner's terminal.
SESSION_FINGERPRINT_CHARS = 8

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
_SESSION_KEY = re.compile(rf"[0-9a-f]{{{SESSION_KEY_CHARS}}}\Z")
_MAX_SESSION_ID = 256
#: Tolerance for a record written by a host whose clock runs slightly ahead of ours.
#: Beyond it the timestamp is not skew but a forged or corrupt record that would
#: otherwise outlive every expiry check, so it is treated as invalid.
_MAX_CLOCK_SKEW_SECONDS = 300


class LaunchError(ValueError):
    """A launcher kind or session id violates the provenance contract."""


#: The run-directory file carrying the planner's launch record. Named here, beside
#: the type and the reader below, so the writer (`dispatch`) and the read API cannot
#: drift on where the link lives or what it is called.
LAUNCH_RECORD_NAME = "launch.json"
#: The key under which `LaunchInfo` sits inside that record.
LAUNCH_INFO_KEY = "launch"


class LaunchInfo(TypedDict):
    """The non-sensitive run->launch link persisted in the run directory.

    ``launcher`` and ``session_key`` are the durable half: they name the launching
    session for as long as the run directory exists, with no dependency on the
    out-of-repo provenance record. They are optional because a run recorded before
    they existed carries only ``launch_id``, and because a launch from a plain shell
    has no session to name.
    """

    launch_id: str
    launcher: NotRequired[str]
    session_key: NotRequired[str]


@dataclass(frozen=True)
class LaunchLink:
    """One run's recorded link to the session that launched it."""

    launch_id: LaunchId
    #: A `KNOWN_LAUNCHERS` value, or ``None`` when the record predates this field.
    launcher: str | None = None
    #: The irreversible session digest, or ``None`` when the run names no session.
    session_key: str | None = None

    @property
    def session(self) -> LaunchSession | None:
        """The launching session this link names on its own, without provenance."""
        if self.launcher not in KNOWN_LAUNCHERS or self.session_key is None:
            return None
        return LaunchSession(str(self.launcher), self.session_key)


def launch_info(*, launch_id: LaunchId, launcher: str, session_id: str | None) -> LaunchInfo:
    """The run-directory link to persist for one launch.

    Built here rather than by the writer so the on-disk contract has one source: the
    reader below and this builder are the two halves of it. Only a *known* launcher
    naming a session contributes durable attribution — anything else records the join
    key alone, exactly as it always did.
    """
    info: LaunchInfo = {"launch_id": launch_id}
    if launcher in KNOWN_LAUNCHERS and session_id:
        info["launcher"] = launcher
        info["session_key"] = session_key(session_id)
    return info


def _recorded_key(value: object) -> str | None:
    """One recorded session key, or ``None`` when it is not one this scheme writes."""
    return value if isinstance(value, str) and _SESSION_KEY.match(value) else None


def read_launch_link(run_dir: Path) -> LaunchLink | None:
    """What a run recorded about its launch, or ``None`` when absent or malformed.

    The reader lives beside the `LaunchInfo` type the writer persists so the two
    halves of this on-disk contract are one source. Every field is revalidated: the
    record is a file on disk, and a hand-edited launcher or key must degrade to "not
    recorded" rather than attribute the run to a session that never launched it.
    """
    path = run_dir / LAUNCH_RECORD_NAME
    if not path.is_file():
        return None
    try:
        raw = load_mapping(path)
    except (ConfigError, OSError):
        return None
    info = raw.get(LAUNCH_INFO_KEY)
    if not isinstance(info, dict):
        return None
    launch_id = validate_launch_id(info.get("launch_id"))
    if launch_id is None:
        return None
    launcher = info.get("launcher")
    return LaunchLink(
        launch_id,
        launcher if launcher in KNOWN_LAUNCHERS else None,
        _recorded_key(info.get("session_key")),
    )


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
    """The out-of-repo directory holding provenance records for this user.

    A relative ``XDG_STATE_HOME`` is ignored in favour of the default, as the XDG
    base-directory spec requires: honouring one would resolve the record against
    whatever directory the process happens to be in, so the same launch would write
    and read different files. The ``HOME``-derived fallback gets the same treatment
    for the same reason — but there is nowhere further to fall back to, so a relative
    one raises rather than silently anchoring a protected record to the working
    directory. Reads degrade on that (`read_provenance` reports no launcher); only
    the write is loud.
    """
    configured = os.environ.get("XDG_STATE_HOME") or ""
    if configured and Path(configured).is_absolute():
        base = Path(configured)
    else:
        home = Path(os.path.expanduser("~"))
        if not home.is_absolute():
            raise LaunchError(
                "provenance needs an absolute state directory: set XDG_STATE_HOME or "
                f"HOME to an absolute path (HOME is {str(home)!r})"
            )
        base = home / ".local" / "state"
    return base / "ai-orchestrator" / "launches"


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
    except (ConfigError, OSError, LaunchError):
        # LaunchError here means the state directory itself is unusable. A viewer
        # should still render the graph, reporting no launcher.
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


@dataclass(frozen=True)
class _LauncherEnvironment:
    """How one known harness names itself, and where it puts its session id.

    Environment, never process ancestry: a launch may sit several shells below the
    harness that started it, and the design fixes attribution on what the harness
    exports rather than on who happens to be the parent.
    """

    launcher: str
    #: Variables whose mere presence identifies this harness.
    markers: tuple[str, ...]
    #: Variables carrying the session id, most specific first.
    session_ids: tuple[str, ...]


#: The ambient environment each known launcher leaves for what it runs. Ordered, and
#: read in order, so a session nested inside another resolves to the first harness
#: that claims it rather than to whichever variable happened to be scanned first.
_LAUNCHER_ENVIRONMENTS: tuple[_LauncherEnvironment, ...] = (
    _LauncherEnvironment(
        "claude-code",
        markers=("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID"),
        session_ids=("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID"),
    ),
    # `CODEX_HOME` is deliberately not a marker: it is ambient configuration a
    # developer may export in a shell profile, so a plain shell would claim to be
    # codex. A marker has to be something only a running session sets.
    _LauncherEnvironment(
        "codex",
        markers=("CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_SANDBOX"),
        session_ids=("CODEX_THREAD_ID", "CODEX_SESSION_ID"),
    ),
)


#: Every variable the detection above reads, derived from it rather than restated.
#: A caller that has to neutralise an ambient session — a test isolating the launch it
#: is exercising from the developer session running it — needs exactly this set, and
#: deriving it means a marker production learns to read cannot silently keep leaking
#: into an environment that believes it cleared them all.
LAUNCHER_ENVIRONMENT_VARIABLES: frozenset[str] = frozenset(
    name
    for candidate in _LAUNCHER_ENVIRONMENTS
    for name in candidate.markers + candidate.session_ids
)


@dataclass(frozen=True)
class DetectedLaunch:
    """The launching harness and session the ambient environment identifies."""

    launcher: str
    #: ``None`` when the harness is recognised but names no session to join on.
    session_id: str | None


def _first_set(environ: Mapping[str, str], names: tuple[str, ...]) -> str | None:
    return next((value for name in names if (value := environ.get(name))), None)


def detect_launch(environ: Mapping[str, str] | None = None) -> DetectedLaunch:
    """Identify the launching harness and its session from the ambient environment.

    An unrecognised environment is ``unknown`` with no session, exactly as a
    hand-run launch has always been: this only removes the requirement that a
    planner remember two flags, it never invents an attribution.

    A session id in a form this scheme cannot carry degrades to ``None`` rather than
    raising. Nobody typed it, so it must not fail a launch that never asked to be
    attributed; the run then reads ``unknown``, which is the honest answer for a
    session that cannot be named.
    """
    values = os.environ if environ is None else environ
    for candidate in _LAUNCHER_ENVIRONMENTS:
        if not any(values.get(marker) for marker in candidate.markers):
            continue
        raw = _first_set(values, candidate.session_ids)
        try:
            session_id = validate_session_id(raw)
        except LaunchError:
            session_id = None
        return DetectedLaunch(candidate.launcher, session_id)
    return DetectedLaunch("unknown", None)


def select_launch(
    *,
    launcher: str | None,
    session_id: str | None,
    environ: Mapping[str, str] | None = None,
) -> DetectedLaunch:
    """Combine explicitly requested provenance with what the environment detected.

    An explicit value always wins. A detected session is only paired with a launcher
    the same detection named: adopting it under a different harness would join a run
    to a session that never launched it, which is the one error this whole scheme
    exists to prevent.
    """
    detected = detect_launch(environ)
    chosen = launcher or detected.launcher
    if session_id:
        return DetectedLaunch(chosen, session_id)
    return DetectedLaunch(chosen, detected.session_id if chosen == detected.launcher else None)


@dataclass(frozen=True)
class LaunchSession:
    """One launching session, named the way anything outside that session may.

    This is the durable form: a known harness plus the irreversible digest of the
    session id. Every comparison and every rendering of "whose run is this" goes
    through it, so the sensitive id itself never has to leave the session that owns
    it — or the protected record that can expire.
    """

    launcher: str
    key: str

    @property
    def label(self) -> str:
        """How this session is named on an operator's terminal, and in the UI."""
        return f"{self.launcher}:{self.key[:SESSION_FINGERPRINT_CHARS]}"


@dataclass(frozen=True)
class LaunchIdentity:
    """One launching session as the session itself knows it: harness plus raw id."""

    launcher: str
    session_id: str

    @property
    def session(self) -> LaunchSession:
        """This identity in the durable form everything else compares against."""
        return LaunchSession(self.launcher, session_key(self.session_id))

    @property
    def label(self) -> str:
        """How this session is named on an operator's terminal.

        The session id itself is never printed — it is the possibly-sensitive half
        of the scheme — so a stable, truncated digest stands in for it. That still
        tells two concurrent planners apart, which is the whole job of the label.
        """
        return self.session.label


def session_key(session_id: str) -> str:
    """A stable, non-reversible key for one launching session.

    Wide enough to compare on: this is what a run directory records and what
    ownership is decided by once the protected provenance record is gone.
    """
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:SESSION_KEY_CHARS]


def session_fingerprint(session_id: str) -> str:
    """A short, stable, non-reversible label for one launching session.

    A prefix of `session_key`, so the short form a planner reads on the terminal and
    the key a run records are the same digest and correlate by eye.
    """
    return session_key(session_id)[:SESSION_FINGERPRINT_CHARS]


def caller_identity(environ: Mapping[str, str] | None = None) -> LaunchIdentity | None:
    """This process's own launching session, or ``None`` when it has none.

    ``None`` is not a failure: a launch from a plain shell has no session to be. It
    does mean the caller owns nothing, because ownership is a join between two
    named sessions and there is nothing here to join to.
    """
    detected = detect_launch(environ)
    if detected.launcher not in KNOWN_LAUNCHERS or detected.session_id is None:
        return None
    return LaunchIdentity(detected.launcher, detected.session_id)


@dataclass(frozen=True)
class RunOwner:
    """Who launched one run, as far as this host can establish it."""

    #: A `KNOWN_LAUNCHERS` value, or ``"unknown"`` when no record names the launcher.
    launcher: str
    #: The launching session, or ``None`` when the run is unattributable.
    identity: LaunchSession | None

    def is_(self, caller: LaunchIdentity | None) -> bool:
        """Whether ``caller`` launched this run.

        Unknown is never mine. Both halves must be present and equal: a run nobody
        can attribute belongs to another planner until proven otherwise, and a
        caller with no session of its own cannot be the owner of anything.
        """
        return caller is not None and self.identity == caller.session

    def label(self, caller: LaunchIdentity | None) -> str:
        """One short ownership indicator for a planner-facing row."""
        if self.identity is None:
            return "unknown"
        return "mine" if self.is_(caller) else self.identity.label


UNKNOWN_OWNER = RunOwner("unknown", None)


def resolve_launch_session(
    link: LaunchLink, *, now: datetime | None = None
) -> tuple[str, LaunchSession | None]:
    """The launcher and launching session one recorded link resolves to.

    The protected provenance record is preferred — it is the record the launcher
    validated on the way in — but it is not required. What the run itself recorded
    answers the same question and cannot expire, so a joined record and an aged-out
    one attribute the run identically, and only a run that recorded neither reads as
    nobody's.
    """
    provenance = read_provenance(link.launch_id, now=now)
    if provenance is not None:
        launcher = provenance["launcher"]
        return launcher, LaunchSession(launcher, session_key(provenance["launcher_session_id"]))
    session = link.session
    return (session.launcher if session is not None else "unknown"), session


def read_run_owner(run_dir: Path, *, now: datetime | None = None) -> RunOwner:
    """Resolve one run's launching session from what it recorded at launch.

    Every degraded state — no ``launch.json``, no join key, no recorded session, and
    no provenance record to fall back on — lands on the same `UNKNOWN_OWNER`, so a
    run this host cannot attribute is reported as nobody's rather than as the
    reader's own.
    """
    link = read_launch_link(run_dir)
    if link is None:
        return UNKNOWN_OWNER
    launcher, session = resolve_launch_session(link, now=now)
    return UNKNOWN_OWNER if session is None else RunOwner(launcher, session)
