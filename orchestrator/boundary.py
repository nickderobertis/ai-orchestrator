"""Bounded retries for the requests a round boundary depends on.

Two runs were orphaned in one night by the same shape: the round finished, the
orchestrator asked its provider what to do next, and that request died on quota the
round itself had just accumulated. One refused request, and the process that owned
the whole run was gone — with nothing wrong except the timing.

Both places that make such a request now retry it, bounded, before the process is
allowed to die. This module is the one policy they share and the one record they
leave, so "how many attempts, how long apart, and where is that visible" has a single
answer rather than one per caller.

The record is a plain JSONL file rather than the run journal, because the *first*
caller is a shell wrapper (`scripts/oneharness-orchestrator.sh`) running between
rounds, with no journal open anywhere and no Python in the path. `drain_attempts`
is the other half: the next round reads what accumulated and journals it, so a retry
that happened while nothing was recording still reaches `events.jsonl` — attempt
counts included — rather than being visible only to whoever tails a log.

Every value read back here is validated. The file is written by a subprocess through
a path taken from the environment, and its contents reach a planner's terminal and
the run's own durable record.
"""

from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from .redaction import redact

#: How many times one boundary request is attempted in total. Three is the shape
#: `orchestrator/smoke.py` already uses for a launch path: enough to ride out a
#: refusal that is about *this moment*, few enough that a genuinely broken path
#: still fails promptly rather than looking like a hang.
DEFAULT_ATTEMPTS = 3
#: The first wait, doubled per attempt. A provider that refused this second is
#: rarely ready the next one, and the round is already over — nothing is waiting
#: on this but the planner's next update.
DEFAULT_BACKOFF_SECONDS = 5.0
BACKOFF_FACTOR = 2.0
#: A ceiling on the doubling, so a large configured backoff cannot leave a run
#: silently asleep for longer than a planner would wait before intervening.
MAX_BACKOFF_SECONDS = 120.0
#: And a ceiling on the count, for the same reason: "bounded retry" has to stay a
#: bound. A configured 10_000 is not a more patient policy, it is a run that never
#: reports the outage it is riding out.
MAX_ATTEMPTS = 10

ATTEMPTS_ENV = "ORCHESTRATOR_BOUNDARY_ATTEMPTS"
BACKOFF_ENV = "ORCHESTRATOR_BOUNDARY_BACKOFF_SECONDS"
#: Where the shell wrapper appends what it retried. Exported by the launch so the
#: wrapper does not have to know a run directory layout.
ATTEMPTS_LOG_ENV = "ORCHESTRATOR_BOUNDARY_ATTEMPTS_LOG"
ATTEMPTS_LOG_NAME = "boundary-attempts.jsonl"
CURSOR_NAME = "boundary-attempts-cursor.json"

#: Bound on one recorded reason before it reaches a journal and a terminal.
MAX_REASON_CHARS = 200
#: Bound on one recorded line. Each is a short JSON object this wrapper wrote, so
#: anything past this is not one, and skipping it costs a record rather than the
#: fold. Applied per line rather than to the file, because a byte cap on the file
#: would make every record past it permanently unreachable — the cursor counts
#: lines, so a prefix that never grows is a fold that never advances.
MAX_LOG_LINE_BYTES = 8 * 1024
#: Bound on how many lines one fold examines. Retries are rare, so a round that
#: finds more than this has a log something else is writing; the cursor still
#: advances past them, so the next round continues rather than restarting here.
MAX_LOG_LINES_PER_FOLD = 1_000

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    """How many times a boundary request is attempted, and how long apart."""

    attempts: int = DEFAULT_ATTEMPTS
    backoff: float = DEFAULT_BACKOFF_SECONDS

    def delay(self, attempt: int) -> float:
        """The wait before attempt ``attempt + 1``, counting attempts from one."""
        return min(self.backoff * BACKOFF_FACTOR ** (attempt - 1), MAX_BACKOFF_SECONDS)

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> RetryPolicy:
        """The configured policy, falling back to the default for anything unusable.

        Deliberately forgiving: this decides how hard a *recovery* tries, so a
        malformed value must not be the thing that stops one. The defaults are
        already the behaviour the caller asked for.
        """
        values = os.environ if environ is None else environ
        return cls(
            attempts=_positive_int(values.get(ATTEMPTS_ENV), DEFAULT_ATTEMPTS),
            backoff=_positive_float(values.get(BACKOFF_ENV), DEFAULT_BACKOFF_SECONDS),
        )


def _positive_int(raw: object, fallback: int) -> int:
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        return fallback
    return min(value, MAX_ATTEMPTS) if value >= 1 else fallback


def _positive_float(raw: object, fallback: float) -> float:
    try:
        value = float(str(raw))
    except (TypeError, ValueError):
        return fallback
    return value if math.isfinite(value) and value > 0 else fallback


@dataclass(frozen=True)
class BoundaryAttempt:
    """One retried boundary request, as the next round journals it."""

    at: float
    role: str
    attempt: int
    attempts: int
    reason: str

    def detail(self) -> dict[str, str | int | float]:
        return {
            "at": self.at,
            "role": self.role,
            "attempt": self.attempt,
            "attempts": self.attempts,
            "reason": self.reason,
        }


def attempts_log(run_dir: Path) -> Path:
    return run_dir / "orchestrator" / ATTEMPTS_LOG_NAME


def record_attempt(path: Path, attempt: BoundaryAttempt) -> None:
    """Append one retried attempt to the shared log, best effort.

    Best effort on purpose: this is observation of a recovery in progress, and a
    log that cannot be written must not be what turns a recoverable refusal into a
    dead run.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(attempt.detail(), sort_keys=True) + "\n")
    except OSError:
        return


def _parsed(line: str) -> BoundaryAttempt | None:
    """One recorded attempt, or ``None`` when the line is not one this reads.

    Every field is checked: the writer is a subprocess and the reason ends up both
    in the run's durable journal and on an operator's terminal, so an unbounded or
    control-carrying value must be dropped rather than passed on.
    """
    try:
        value = json.loads(line)
    except ValueError:
        return None
    if not isinstance(value, dict):
        return None
    at, role, attempt, attempts, reason = (
        value.get("at"),
        value.get("role"),
        value.get("attempt"),
        value.get("attempts"),
        value.get("reason"),
    )
    if (
        not isinstance(at, (int, float))
        or isinstance(at, bool)
        or not math.isfinite(at)
        or not isinstance(role, str)
        or not role.isprintable()
        or not 0 < len(role) <= 40
        or not isinstance(attempt, int)
        or isinstance(attempt, bool)
        or attempt < 1
        or not isinstance(attempts, int)
        or isinstance(attempts, bool)
        or attempts < attempt
        or not isinstance(reason, str)
    ):
        return None
    return BoundaryAttempt(
        at=float(at),
        role=role,
        attempt=attempt,
        attempts=attempts,
        reason=" ".join(redact(reason).split())[:MAX_REASON_CHARS],
    )


def drain_attempts(run_dir: Path) -> list[BoundaryAttempt]:
    """Every attempt recorded since this run last folded, advancing the cursor.

    A cursor rather than truncation, so the raw log stays readable beside the
    journal and two folds of one round cannot double-count. A cursor this reader
    cannot make sense of starts the fold from the beginning, which repeats records
    at worst — the failure mode that loses nothing.
    """
    path = attempts_log(run_dir)
    cursor_path = path.with_name(CURSOR_NAME)
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    consumed = 0
    try:
        recorded = json.loads(cursor_path.read_text(encoding="utf-8"))
        count = recorded.get("lines") if isinstance(recorded, dict) else None
        # `bool` is an `int`, so `True` would otherwise stand in for "one line
        # already folded" — a cursor value nothing here ever writes.
        if isinstance(count, int) and not isinstance(count, bool):
            consumed = max(0, min(count, len(lines)))
    except (OSError, ValueError):
        consumed = 0
    examined = lines[consumed : consumed + MAX_LOG_LINES_PER_FOLD]
    found = [
        attempt
        for line in examined
        if len(line.encode("utf-8")) <= MAX_LOG_LINE_BYTES
        and (attempt := _parsed(line)) is not None
    ]
    # Advanced past everything examined, including a line this reader skipped: the
    # cursor is what the *next* fold continues from, so leaving it behind would make
    # one unreadable line stall every retry recorded after it.
    # The fold already happened; a cursor that could not be written means the next
    # round repeats these records rather than losing them.
    with suppress(OSError):
        cursor_path.write_text(json.dumps({"lines": consumed + len(examined)}), encoding="utf-8")
    return found


def retry_boundary_request(
    operation: Callable[[], T],
    *,
    role: str,
    policy: RetryPolicy,
    retryable: Callable[[Exception], bool],
    report: Callable[[BoundaryAttempt], None],
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
) -> T:
    """Run ``operation``, retrying a retryable failure with bounded backoff.

    The last attempt's exception is raised rather than swallowed: exhausting the
    policy means the boundary genuinely could not be crossed, and the caller's own
    handling of that is unchanged. ``retryable`` is the caller's, because what is
    worth a second ask differs — the orchestrator's own turn is retried on any
    refusal that produced nothing, while a check-in is retried only for a
    classified provider refusal, so an agent that simply did its job badly is not
    asked to do it twice.
    """
    for attempt in range(1, max(1, policy.attempts) + 1):
        try:
            return operation()
        except Exception as exc:
            if attempt >= policy.attempts or not retryable(exc):
                raise
            report(
                BoundaryAttempt(
                    at=now(),
                    role=role,
                    attempt=attempt,
                    attempts=policy.attempts,
                    reason=str(exc),
                )
            )
            sleep(policy.delay(attempt))
    raise AssertionError("unreachable: the loop above returns or raises")  # pragma: no cover
