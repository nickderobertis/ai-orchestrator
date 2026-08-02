#!/usr/bin/env python3
"""Publish a streamed agent turn's activity while preserving onejudge's contract.

`oneharness run --stream` writes one NDJSON `{"type":"event",...}` line the instant
each event is observed, then a terminal `{"type":"result","report":{...}}` line. A
plain `oneharness run` writes the bare report once, at the end. onejudge accepts
only the second shape — it parses this process's stdout as exactly one JSON
document — so the agent side cannot simply hand the stream through.

This filter is that reconciliation, and it is the whole reason `--stream` is usable
here at all:

* every line the child wrote is appended verbatim to the stdout record, so the raw
  transcript is preserved exactly as `tee` preserved it before;
* each ``event`` line republishes a small, bounded activity summary the planner's
  views read *while the turn is still running* (`orchestrator/activity.py` reads it
  back and `just status` renders it);
* the terminal ``result`` line's ``report`` is unwrapped onto stdout as the exact
  text oneharness wrote, so what onejudge parses is oneharness's own report.

Anything it does not recognize is forwarded verbatim rather than swallowed: a
degraded run that answered with a bare report still reaches onejudge intact, which
is what keeps a streaming problem from becoming a dispatch failure.

Keep this deterministic and stdlib-only. It is spawned as a subprocess by
`scripts/oneharness-agent.sh`, which resolves this repository's interpreter when
one exists and a bare `python3` otherwise, so it cannot import `orchestrator`.
Bounding happens here because the value crosses a process boundary; *redaction*
deliberately does not, and happens where every other harness-authored evidence
string is redacted — when the dispatcher reads it back.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, TypedDict, cast

#: How much of one event's rendered input the activity summary keeps. The summary
#: exists to say what a node is doing right now, which the head of a command or a
#: path says; a full tool input can be a whole file.
DETAIL_CHARS = 160
#: Fields whose value renders an event's input most usefully, in the order they are
#: preferred. A tool call's shape is the harness's, normalized by oneharness, so
#: this is a best-effort rendering and never a contract: an event carrying none of
#: them still publishes its kind, its name, and the count.
DETAIL_KEYS = ("command", "file_path", "path", "pattern", "url", "description", "prompt")
#: The variable oneharness stamps history records from, which `orchestrator/labels.py`
#: writes for every dispatch. It is also the only thing in this process's environment
#: that says *where in the tracked graph* this turn is, and a reader that finds this
#: file under an anonymous scratch directory has no other way to learn it — so the
#: summary carries its own locator rather than requiring a second file to join on.
LABEL_ENV = "ONEHARNESS_HISTORY_LABELS"
#: The locator keys a planner view joins on. Everything else oneharness was told to
#: stamp stays out: this file is read by a view, not by the history store.
LOCATOR_KEYS = ("run_id", "round", "node", "step", "persona")


class Locator(TypedDict, total=False):
    """Where in the tracked graph the turn publishing these summaries is running.

    Every key is optional because a bare `just dispatch` has no graph position at
    all, and `orchestrator.labels` omits rather than empties the ones it has none
    for. `tests/test_activity.py` drift-gates this against the labels that module
    can actually produce, since the two sides of this contract are written in
    different languages in different processes.
    """

    run_id: str
    round: str
    node: str
    step: str
    persona: str


class Summary(Locator, total=False):
    """One published activity record: the locator, plus what is happening there.

    `orchestrator.activity.NodeActivity` is the reader's side of this record. It
    validates every field again rather than trusting these types, because between
    the two is a file under a shared scratch root that anything could have written.
    """

    at: float
    kind: str
    name: str
    detail: str
    events: int


def _locator(raw: str | None) -> Locator:
    """Parse the graph locator out of the inherited history-label value.

    The wire format is ``key=value`` pairs separated by commas, with no escape — so
    a pair this cannot read is dropped rather than guessed at, exactly as
    `orchestrator.labels.parse_labels` drops one. A dispatch with no graph position
    (a bare `just dispatch`) legitimately yields nothing.
    """
    located: dict[str, str] = {}
    for chunk in (raw or "").split(","):
        key, separator, value = chunk.partition("=")
        key, value = key.strip(), value.strip()
        if separator and key in LOCATOR_KEYS and value:
            located[key] = value[:DETAIL_CHARS]
    return cast(Locator, located)


def _collapse(value: str) -> str:
    """Bound one harness-authored string to a single short line."""
    return " ".join(value.split())[:DETAIL_CHARS]


def _detail(event: dict[str, Any]) -> str:
    """Render what this event is doing, as far as its shape allows."""
    match event.get("input"):
        case str() as text:
            return _collapse(text)
        case dict() as payload:
            for key in DETAIL_KEYS:
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return _collapse(value)
            return ""
        case _:
            return ""


def _publish(activity_path: str, summary: Summary) -> None:
    """Replace the activity file atomically, or give up on it silently.

    A reader must never see a half-written summary, and this must never be able to
    fail the turn: activity is an observation of the run, not part of it. A
    publication that cannot be written leaves the previous one in place, which is
    stale rather than wrong — the reader ages it against its own clock.
    """
    temporary = f"{activity_path}.tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(summary) + "\n")
        os.replace(temporary, activity_path)
    except OSError:
        pass


def _forward(line: str) -> None:
    """Hand one line to onejudge, flushed, because nothing else will."""
    sys.stdout.write(line)
    sys.stdout.flush()


def _report_text(line: str) -> str:
    """Return the ``report`` value's own text, cut out of the terminal stream line.

    The caller has already parsed this line and seen a ``report`` object in it, so
    the key is present and is the envelope's, not a nested string's — the only other
    field is ``type``, whose value is ``result``. Cutting the raw span rather than
    re-serializing the parse keeps every number, escape and key order exactly as
    oneharness emitted them, so what onejudge parses is oneharness's report.

    A line that somehow defeats the scan degrades to the re-serialized parse, which
    is still the same document; it is fidelity that is lost there, never content.
    """
    _, marker, remainder = line.partition('"report":')
    if marker:
        # `raw_decode` starts at the value itself, so any space after the colon is
        # dropped first rather than read as the start of one.
        remainder = remainder.lstrip()
        try:
            _, end = json.JSONDecoder().raw_decode(remainder)
        except ValueError:
            pass
        else:
            return remainder[:end]
    return json.dumps(json.loads(line)["report"])


def _translate(record: Any, activity_path: str, locator: Locator) -> None:
    """Pump the child's stdout, recording, publishing and unwrapping as it goes.

    The three cases are the stream protocol's own: an event to publish, the terminal
    result to unwrap, and everything else — which is forwarded rather than swallowed,
    so a run that answered with something this does not model still reaches onejudge.
    """
    events = 0
    for line in sys.stdin:
        # The raw record first and always: it is the only account of a child that
        # dies mid-stream, and the diagnostics the wrapper greps out of it must not
        # depend on this filter understanding what it read.
        record.write(line)
        record.flush()
        try:
            envelope = json.loads(line)
        except ValueError:
            envelope = None
        match envelope:
            case {"type": "event", "event": dict() as event}:
                events += 1
                _publish(
                    activity_path,
                    {
                        **locator,
                        "at": time.time(),
                        "kind": str(event.get("kind") or "event")[:DETAIL_CHARS],
                        "name": str(event.get("name") or "")[:DETAIL_CHARS],
                        "detail": _detail(event),
                        "events": events,
                    },
                )
            case {"type": "event"}:
                # An event envelope carrying no event object: nothing to publish and
                # nothing onejudge could do with it either.
                continue
            case {"type": "result", "report": dict()}:
                # The one document onejudge parses, forwarded as the exact text
                # oneharness wrote rather than re-serialized from the parse — so what
                # onejudge reads is oneharness's own report, not this filter's
                # rendering of it.
                _forward(_report_text(line) + "\n")
            case _:
                _forward(line)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("oneharness-stream: expected <stdout-record> <activity-file>", file=sys.stderr)
        return 2
    record_path, activity_path = argv
    try:
        with open(record_path, "a", encoding="utf-8") as record:
            _translate(record, activity_path, _locator(os.environ.get(LABEL_ENV)))
    except OSError as error:
        # The same fate `tee` met here before: a stdout record that cannot be kept
        # fails the capture, and the wrapper turns that into a failed turn rather
        # than into a turn whose transcript quietly went missing.
        print(
            f"oneharness-stream: cannot keep the agent stdout record at {record_path}: "
            f"{error}; retry through orchestrator dispatch, which creates and owns the "
            "status directory this writes into",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
