"""Render the watch verb's machine-readable stream into the lines an operator reads.

`scripts/watch-run.sh` asks the engine's blocking watch verb for its machine-readable
form and pipes it here, and never reads the verb's own human-readable lines — see
AGENTS.md's watch rule for what that is worth. So every line below is composed from
fields, and the one field that must never be dropped or invented is the unread-surface
count a heartbeat carries.

Three properties are deliberate. Nothing here stops the stream: a record this cannot
read is reported and the watch goes on, because a watch that dies on one line is a watch
that goes silent. Every line is flushed as it arrives, because a watch buffered until it
exits reports nothing while it matters. And a record this could not use is counted, and
makes this exit non-zero, so the wrapper refuses rather than passing the engine's
terminal status on.

Those last two are about different things and the difference is load-bearing. A record
whose **kind** this does not recognise is not a failure — it is rendered whole and the
exit stays clean, so a verb that grows a fifth record kind is not a broken watch. A
record this could not **parse**, and a field it could not **use**, are: the first loses
whatever that record carried, and the second would put a number in front of a supervisor
with nothing behind it.

Keep this deterministic and stdlib-only: `scripts/watch-run.sh` spawns it as a
subprocess, resolving this repository's interpreter when one exists and a bare
`python3` otherwise, so it cannot import `orchestrator`.

llmlint: ignore-file[contracts_have_one_source_or_a_drift_gate] The whole of this file is
one restatement of the engine's wire shape — the record kinds, the `unread` fields, the
field aliases, and the terminal record's condition and cursor — so the directive is
file-scoped because the property is, not to clear one site. What can be reconciled
against the installed engine is, and it is next door:
`tests/test_watch_surface_drift.py` drives the verb into every terminal condition a
static runs root can produce and compares the pairing it answers with. What cannot is
this rendering, and that is a property of the artifact rather than an omission — the
engine's wire shape is declared in its own repository and in nothing the wheel installs,
so a gate here would be a second copy of the guess. What stands in for one is that
nothing here is read strictly: an unrecognised kind renders whole, a missing field
degrades to a named absence, and an unusable one says so — so a drifted wire shape shows
on the watch's own face rather than passing as a count somebody could act on. The whole
of that guess was wrong once already, and silently: written before any engine offered the
verb, it keyed on a `kind` field the verb does not write, so every record fell to the
`record` fallback and the resume cursor reached the wrapper from nothing. What caught it
was running `just watch` against a real run, which is what to do after moving the pin.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from enum import StrEnum
from pathlib import Path
from typing import Any

#: A cursor this is willing to hand back. The wrapper prints it inside a copyable
#: `just watch <run> --cursor <cursor>` line, so a value carrying whitespace, a quote,
#: or a shell metacharacter would compose a command line that does something other than
#: resume a watch. An opaque token is what the contract promises and what this accepts;
#: anything else is refused rather than narrowed, because a cursor this had to mangle
#: would resume from somewhere nobody chose.
_CURSOR = re.compile(r"^[A-Za-z0-9._:+/=-]{1,256}$")

#: Characters no field may carry into the line an operator reads: C0 and C1 controls and
#: DEL. This renders another program's values straight to a terminal, so an escape
#: sequence in a node name or an unread kind would move the cursor, erase what a watch
#: had already reported, or colour a failure green. They are replaced rather than
#: dropped, so the field's shape survives the sanitising.
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")

#: How much of one field a watch line carries. A watch is read as it happens, so a field
#: that arrived as a whole document must not push the next event off the screen.
_FIELD = 200


def _one_line(value: object) -> str:
    """One of another program's values, safe to put on a line an operator reads."""
    collapsed = " ".join(_CONTROL.sub(" ", str(value)).split())
    return collapsed if len(collapsed) <= _FIELD else f"{collapsed[:_FIELD]}…"


class RecordKind(StrEnum):
    """The record kinds the watch contract names, as they appear on the wire.

    One line per meaningful event, one heartbeat per interval of silence, and the
    terminal record naming the condition the verb returned on and the cursor a later
    watch resumes from. Each record says which it is under :data:`KIND_FIELD`.
    """

    EVENT = "event"
    HEARTBEAT = "heartbeat"
    TERMINAL = "return"


#: The field each record names its own kind in. Not `kind`, which is what the *event*
#: inside an event record uses for its own kind — the two are different vocabularies at
#: two levels, and reading the outer record by the inner field is what made every record
#: here fall to the unrecognised-kind fallback.
KIND_FIELD = "watch"
#: Where an event record carries the event. The record is an envelope: its own fields say
#: what kind of watch record it is, and the whole engine envelope sits under this one.
EVENT_FIELD = "event"


# `Any` rather than a narrower type because these are records decoded from another
# program's JSON: their values are whatever that program wrote, and there is no static
# type that says more than "decoded JSON". `object` would say the same and force a cast
# at every read, which is ceremony around the same runtime checks made below.
# llmlint: ignore[modern_domain_modeling] A TypedDict here would be a static claim
# about bytes this program does not own and cannot check: the values arrive as
# whatever the verb wrote, every field that matters is checked where it is used, and
# the type would go on describing a wire shape that had moved. The kinds are a
# `StrEnum` precisely because that half *is* this repository's own vocabulary.
def _text(record: dict[str, Any], *names: str) -> str:
    """The first of `names` this record carries, rendered on one line.

    Whatever the value turns out to be is rendered as text, and deliberately not
    type-checked first: it becomes a phrase on a watch line either way, and
    `_one_line` is what makes any value safe to put there. That is the opposite of
    how `_unread` reads its count below, and the difference is what each field
    *becomes* — a count nobody can act on must never be invented, where a detail that
    arrived as a list is still a truthful rendering of what the verb sent.
    """
    for name in names:
        value = record.get(name)
        if value not in (None, "", [], {}):
            # llmlint: ignore[boundary_inputs_validated] Validated as what it is used
            # as: this becomes operator-facing text, and `_one_line` strips its
            # control characters and bounds its length. Refusing a non-string would
            # drop a field the verb did send.
            return _one_line(value)
    return ""


def _unread(record: dict[str, Any]) -> str:
    """How many planner surfaces are unread and of which kinds, as one clause.

    Read from the record rather than counted here — the verb is what knows — and
    rendered even when the answer is none, because "nothing unread" and "this watch
    stopped telling you" are the two states a supervisor most needs told apart. Which
    is also why every field is type-checked before it is rendered rather than
    formatted as whatever arrived: this clause is the one signal the watch rule forbids
    filtering out, and a `total` that came through as a string or a `kinds` that came
    through as a number would read as a count somebody could act on. An unusable field
    is named as unreported, and the two are different sentences.
    """
    unread = record.get("unread")
    if not isinstance(unread, dict):
        return "unread surfaces: not reported by this record"
    total = next(
        (unread[name] for name in ("count", "total") if name in unread),
        None,
    )
    counted = (
        total if isinstance(total, int) and not isinstance(total, bool) and total >= 0 else None
    )
    named = _named_kinds(unread.get("kinds"))
    if counted is None:
        return f"unread surfaces: this record reports no usable count{named}"
    if counted == 0 and not named:
        return "no planner update is unread"
    return f"{counted} planner update(s) unread{named}"


def _named_kinds(kinds: object) -> str:
    """The unread kinds as a parenthesised clause, or nothing when there are none.

    Two shapes are accepted because both say the same thing — a mapping of kind to
    count, and a bare list of kinds — and an entry of either whose count is not a count
    is dropped rather than rendered, for the reason above.
    """
    match kinds:
        case dict():
            counted = {
                _one_line(kind): count
                for kind, count in kinds.items()
                if isinstance(count, int) and not isinstance(count, bool) and count >= 0
            }
            named = ", ".join(f"{kind} {count}" for kind, count in sorted(counted.items()))
        case list():
            # llmlint: ignore[boundary_inputs_validated] A kind is a name rather than a
            # count, so it is rendered as text under the same stripping and bound every
            # other field gets; only the counts beside them are type-checked, because
            # only a count reads as something to act on.
            named = ", ".join(_kind_entry(entry) for entry in kinds if _kind_entry(entry))
        case _:
            named = ""
    return f" ({named})" if named else ""


def _event(record: dict[str, Any]) -> str:
    """One event record's own line: when, whose, what, and what it said.

    The event is an envelope of the engine's own — a `ts`, a `kind`, `labels` naming the
    run and the node, and a `payload` whose shape is that kind's — so the node comes out
    of the labels and the detail out of the payload rather than off the record itself. A
    payload this cannot summarise is rendered whole and bounded, which is the same
    degrade every other field here takes: what a supervisor must never get is silence.
    """
    envelope = record.get(EVENT_FIELD)
    if not isinstance(envelope, dict):
        return _one_line(json.dumps(record, sort_keys=True))
    at = _text(envelope, "ts", "at", "timestamp", "time")
    labels = envelope.get("labels")
    payload = envelope.get("payload")
    parts = [
        _text(envelope, "kind", "name", "type") or "(unnamed event)",
        _text(labels, "node", "step") if isinstance(labels, dict) else "",
        _detail(payload),
    ]
    said = "  ".join(part for part in parts if part)
    return f"{at}  {said}" if at else said


def _detail(payload: object) -> str:
    """What one event said, out of the payload whose shape is that event kind's.

    The kinds a watch relays carry different fields — a settlement its outcome, a
    surface its message, a stop its reason — so the ones worth a line are named and
    anything else is rendered whole and bounded rather than dropped. A payload that is
    not an object at all is still what the verb sent, so it is rendered too.
    """
    if not isinstance(payload, dict):
        return _text({"payload": payload}, "payload")
    return _text(payload, "message", "reason", "detail", "outcome", "status") or (
        _one_line(json.dumps(payload, sort_keys=True)) if payload else ""
    )


def _kind_entry(entry: object) -> str:
    """One entry of an unread-kinds list: a bare name, or a name with its own count.

    Both shapes say the same thing and the verb writes the second — `{"kind": …,
    "count": …}` — where an older reading of this contract expected the first. A count
    that is not a count is dropped and the name kept, for the reason `_unread` gives:
    only a number reads as something to act on.
    """
    if not isinstance(entry, dict):
        return _one_line(entry)
    named = _text(entry, "kind", "name")
    count = entry.get("count")
    if not named:
        return ""
    if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
        return f"{named} {count}"
    return named


def _render(record: dict[str, Any]) -> str:
    """One line for one record, composed from its fields."""
    at = _text(record, "at", "timestamp", "time")
    stamped = f"{at}  " if at else ""
    match record.get(KIND_FIELD):
        case RecordKind.EVENT:
            return f"event      {_event(record)}"
        case RecordKind.HEARTBEAT:
            return f"heartbeat  {stamped}{_unread(record)}"
        case RecordKind.TERMINAL:
            condition = _text(record, "condition") or "(unnamed condition)"
            return f"terminal   {stamped}{condition}; {_unread(record)}"
        case _:
            # Through `_one_line` like every other rendered value: a record whose kind
            # this build has never seen is still another program's, so an escape
            # sequence or a whole document inside one must not reach the terminal.
            return f"record     {stamped}{_one_line(json.dumps(record, sort_keys=True))}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cursor-file",
        type=Path,
        required=True,
        help="where to leave the cursor the terminal record carried, for the wrapper's "
        "resume hint. Left absent when no terminal record named one.",
    )
    arguments = parser.parse_args(argv)

    # The wrapper names its own `mktemp` here, but this is a program with a command line
    # and the argument is a write target: a directory, a device, or a symlink — at the
    # target itself or anywhere above it — would put the cursor somewhere nobody asked
    # for. The whole path is checked rather than the last component, because a symlinked
    # parent redirects the write just as effectively, and `O_NOFOLLOW` below only ever
    # answers for the final name.
    target = arguments.cursor_file
    redirected = os.path.realpath(target.parent) != os.path.abspath(target.parent)
    # `O_TRUNC` below overwrites whatever is at that path, and this program takes the
    # path on a command line, so an existing file with anything in it is refused: the
    # recipe's own `mktemp` is empty, and every other non-empty regular file at that
    # path is somebody's rather than this program's to truncate.
    occupied = target.is_file() and target.stat().st_size > 0
    # This program has a command line of its own, so the guard stays; `just watch`
    # cannot reach it, since it always passes a fresh mktemp of its own making.
    # llmlint: ignore[changed_behavior_has_e2e] unreachable: the recipe's mktemp is fresh.
    if redirected or target.is_symlink() or occupied or (target.exists() and not target.is_file()):
        print(
            f"watch: {target} is not somewhere this will write a cursor — it or a "
            "directory above it is a symlink, it is not a regular file, or it already "
            "holds something this will not overwrite. Name an empty or absent regular "
            "path under real directories, or run this through 'just watch', which "
            "supplies its own",
            file=sys.stderr,
        )
        return 1

    cursor = ""
    unusable = 0
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            unusable += 1
            print(
                "watch: the watch verb wrote a line that is not JSON, so whatever it "
                f"carried is lost: {_one_line(line)} — 'just watch' refuses rather than "
                "reporting a terminal condition it cannot vouch for, so read the run "
                "with 'just status <run-id>' and report the engine's output",
                file=sys.stderr,
            )
            continue
        if not isinstance(record, dict):
            unusable += 1
            print(
                "watch: the watch verb wrote a record that is not an object, so this "
                f"cannot tell what it was: {_one_line(line)} — read the run with 'just "
                "status <run-id>' and report the engine's output",
                file=sys.stderr,
            )
            continue
        if record.get(KIND_FIELD) == RecordKind.TERMINAL:
            named = record.get("cursor")
            if named is not None and not (isinstance(named, str) and _CURSOR.match(named)):
                unusable += 1
                print(
                    "watch: the watch verb named a cursor this cannot hand back — a "
                    f"cursor is an opaque token and this one is not: {_one_line(named)} "
                    "— start a fresh watch, or resume from a cursor an earlier watch "
                    "printed",
                    file=sys.stderr,
                )
            elif isinstance(named, str):
                cursor = named
        # One line per record is what a watch *is*: a supervisor reads the events, the
        # heartbeats and their unread-surface counts as they happen, and the lines are
        # composed here from fields rather than passed through. Reducing that to a
        # summary is the silence this whole command exists to end.
        # llmlint: ignore[tool_output_is_signal] the live stream is this command's product.
        print(_render(record), flush=True)

    if cursor:
        # `O_NOFOLLOW` rather than a plain write: the check above answered for the path
        # as it was, and this answers for the path as it is when the bytes go through.
        #
        # A write that fails loses the resume hint and nothing else — the stream was read
        # whole and the terminal condition with it — so it is reported rather than raised
        # and does not count as unusable: refusing the watch here would tell a supervisor
        # nothing was watched when everything was. The cursor is printed in the message,
        # because the whole of what the file was carrying is that one token.
        try:
            opened = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with os.fdopen(opened, "w", encoding="utf-8") as handle:
                handle.write(cursor)
        # llmlint: ignore[changed_behavior_has_e2e] unreachable: the recipe's mktemp is fresh.
        except OSError as refused:
            print(
                f"watch: the watch itself was read whole, but its resume cursor could "
                f"not be written to {target} ({refused.strerror}), so no resume command "
                f"is printed below. Resume this watch with --cursor {cursor}",
                file=sys.stderr,
            )
    return 1 if unusable else 0


if __name__ == "__main__":
    raise SystemExit(main())
