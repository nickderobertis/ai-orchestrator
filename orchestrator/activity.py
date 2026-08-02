"""What a dispatched node is doing *right now*, read back from live dispatches.

Every other planner-visible source answers after the fact. A oneharness history
record is written when a turn *finishes*, and the run journal records a node
*starting* and *settling* — so between those two the honest best a view could do was
"in flight for 23m, no completed turn yet". On this host a first turn routinely runs
600-2000 seconds, and that gap is where a healthy working node has twice been
reported as possibly dead.

`scripts/oneharness-agent.sh` closes it by streaming the agent turn: each normalized
event republishes a small summary into the dispatch's own watchdog scratch
directory, carrying the graph locator it was dispatched with. This module is the
reader for that publication.

Three properties make it usable rather than decorative:

* **It never blocks and never fails a view.** Every read degrades to "no activity
  known", which is exactly the picture a view had before streaming existed. The
  journal join stays the guarantee; this only ever adds to it.
* **It is a trust boundary.** These files live under a shared scratch root and are
  written by a subprocess, so a publication is believed only when a live dispatcher
  still holds the owner lock on the directory it sits in, and every field is then
  validated against its own domain, bounded, and redacted before it can reach a
  planner's terminal. That lock is ownership evidence, not an access control: it
  cannot stop a local user who sets one up deliberately, and nothing here is
  security boundary for that. It does mean a summary is only ever reported for a
  dispatch something is still running.
* **It is scoped to one run.** A shared host runs several planners' dispatches at
  once, so a summary is matched to the run whose id it carries and ignored
  otherwise.
"""

from __future__ import annotations

import json
import math
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .labels import MAX_VALUE_CODEPOINTS
from .redaction import redact
from .scratch import (
    AGENT_ACTIVITY_NAME,
    AGENT_STATUS_DIR_NAME,
    WATCHDOG_PATTERN,
    watchdog_has_a_live_owner,
)

#: How much of one summary field survives to a planner's terminal. The publisher
#: already bounds what it writes; this is the reader's own bound on a value it did
#: not write, applied after redaction so a credential cannot be split across the cut.
SUMMARY_CHARS = 120
#: Beyond this, a summary describes a turn that has since moved on or a dispatch
#: whose stream stopped, so reporting it as what the node is doing now would be a
#: fresher-looking lie than saying nothing. Comfortably longer than the gap between
#: two tool calls in a working turn, and far shorter than the in-flight times this
#: exists to explain.
STALE_AFTER_SECONDS = 900.0
#: The ceiling on the running event count a summary may claim. A turn's transcript
#: is thousands of events at the very outside, so anything past this says only "a
#: great many", and is reported as that rather than as a number nobody measured.
MAX_REPORTED_EVENTS = 1_000_000
#: How large a published summary may be. It is one short JSON line whose every field
#: the publisher already bounds, so this is generous by an order of magnitude — and
#: a file past it is not a summary at all, whatever its first bytes look like.
MAX_SUMMARY_BYTES = 8192


@dataclass(frozen=True)
class NodeActivity:
    """The most recent event a live dispatch published for one graph node."""

    round: str
    node: str
    at: float
    kind: str
    name: str
    detail: str
    events: int

    def describe(self, *, now: float) -> str:
        """One short phrase naming what this node is doing, and how long ago."""
        what = " ".join(part for part in (self.name, self.detail) if part) or self.kind
        ago = max(0, int(now - self.at))
        return f"now {what} ({self.events} event(s), {ago}s ago)"


def _bounded(value: Any) -> str:
    """Collapse, redact and bound one field a subprocess wrote."""
    if not isinstance(value, str):
        return ""
    return " ".join(redact(value).split())[:SUMMARY_CHARS]


def _counted(value: Any) -> int:
    """Bound the published event count into what a turn can actually have observed.

    It is a running total a subprocess wrote, so its domain is non-negative and, at
    this cap, already far past any turn's real transcript. A negative or absurd count
    would print as an authoritative statement about how much work a node has done.
    """
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return 0
    return min(value, MAX_REPORTED_EVENTS)


def _summary(payload: object, run_id: str, *, now: float) -> NodeActivity | None:
    """Validate one published summary, or reject it.

    Every field is checked against the domain the journal records rather than
    trusted on sight: ``round`` and ``node`` become a join key, ``at`` becomes an
    age, and the rest becomes text a planner reads. A payload that fails any of it
    is not this run's, or is not usable, and is dropped.
    """
    if not isinstance(payload, dict) or payload.get("run_id") != run_id:
        return None
    recorded_round, node = payload.get("round"), payload.get("node")
    # Both reach the publisher as history labels, so the label contract is their
    # domain and its length limit is theirs too. That bound is load-bearing rather
    # than tidy: `int` refuses a string past CPython's digit limit by *raising*, so a
    # summary claiming a round of several thousand digits would pass `isdigit` and
    # then take down the view below. Length is checked first for the same reason.
    if not isinstance(recorded_round, str) or len(recorded_round) > MAX_VALUE_CODEPOINTS:
        return None
    if not recorded_round.isdigit():
        return None
    if not isinstance(node, str) or not node or len(node) > MAX_VALUE_CODEPOINTS:
        return None
    at = payload.get("at")
    # `NaN` and the infinities are valid JSON numbers to Python's parser and pass
    # every comparison below by silently answering false, so a summary carrying one
    # would reach `describe`, where `int(now - at)` raises and takes the whole
    # read-only view down. This boundary fails closed instead.
    if not isinstance(at, (int, float)) or isinstance(at, bool) or not math.isfinite(at):
        return None
    # A future timestamp is a clock the reader cannot reason about; an old one
    # describes a turn that has moved on. Both are dropped rather than aged.
    if at > now + STALE_AFTER_SECONDS or now - at > STALE_AFTER_SECONDS:
        return None
    return NodeActivity(
        # Normalized through `int` so a zero-padded label and the journal's own
        # integer round cannot spell the same round two ways.
        round=str(int(recorded_round)),
        node=node,
        at=float(at),
        kind=_bounded(payload.get("kind")) or "event",
        name=_bounded(payload.get("name")),
        detail=_bounded(payload.get("detail")),
        events=_counted(payload.get("events")),
    )


def live_activity(
    run_id: str, *, root: Path | None = None, now: float | None = None
) -> dict[tuple[str, str], NodeActivity]:
    """The latest activity each of this run's live dispatches published, by locator.

    Scans the same scratch root `orchestrator.scratch` sweeps, because that is where
    a dispatch's watchdog directory is: the run directory never learns the path, and
    a dispatch never learns the run directory. A reader whose ``TMPDIR`` differs from
    the dispatcher's finds nothing and the view degrades to the journal alone.
    """
    at = time.time() if now is None else now
    scratch_root = (root or Path(tempfile.gettempdir())).resolve()
    latest: dict[tuple[str, str], NodeActivity] = {}
    try:
        candidates = sorted(scratch_root.glob(f"{WATCHDOG_PATTERN}/{AGENT_STATUS_DIR_NAME}"))
    except OSError:
        return latest
    for status_dir in candidates:
        # A watchdog-shaped directory under a shared root is a shape, not a claim.
        # The owner lock is: a live dispatcher holds it for the whole scope of its
        # scratch tree, so requiring it is what separates a running dispatch's
        # publication from a directory anything could have left there. It is also
        # what keeps a *finished* dispatch's last summary from being reported as
        # what a node is doing now, before the sweep reclaims it.
        if not watchdog_has_a_live_owner(status_dir.parent):
            continue
        path = status_dir / AGENT_ACTIVITY_NAME
        try:
            # Bounded because this is a foreign file under a shared root: a summary
            # is one short line, and anything longer is not one. One byte past the
            # cap is read so that an oversized file is *rejected* rather than
            # truncated into a prefix that happens to parse — a reader that accepted
            # the prefix would report whatever the first summary-shaped bytes of some
            # much larger document said.
            with path.open("rb") as handle:
                raw = handle.read(MAX_SUMMARY_BYTES + 1)
        except OSError:
            continue
        if len(raw) > MAX_SUMMARY_BYTES:
            continue
        try:
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            continue
        summary = _summary(payload, run_id, now=at)
        if summary is None:
            continue
        key = (summary.round, summary.node)
        # A retried node can leave a finished dispatch's directory beside its
        # replacement's for as long as the reaper takes; the newer publication is
        # the one describing what is running.
        previous = latest.get(key)
        if previous is None or summary.at > previous.at:
            latest[key] = summary
    return latest
