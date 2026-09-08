"""`just watch` is the command AGENTS.md's watch rule names, driven end to end.

That rule states why watching needs a command and what a watch owes; these journeys hold
this recipe to the four parts of it that are the recipe's own. It reports which of four
terminal conditions happened **from the verb's exit status**, never from its prose. It
puts the unread-surface count a heartbeat carries in front of its caller, and never
invents one from a field it cannot use. It hands back a cursor a later watch resumes
from, and refuses one it would not put in a copied command. And against an engine with
no watch verb it says so in this repository's own words, naming the pin that carries it.

The real recipe, wrapper script, renderer and shell run here; the published engine is
the double, at the boundary below the seam under test.

llmlint: ignore-file[contracts_have_one_source_or_a_drift_gate] The stream this double
writes restates the engine's wire shape for the same reason `scripts/watch-render.py`
does and under the same condition — no engine offering the verb exists to reconcile it
against — and it is the fixture *for* the reader whose restatement that file's own
directive explains.

llmlint: ignore-file[e2e_not_mocked] The engine's blocking watch verb is what this
wrapper delegates *to*, and doubling it is what makes the wrapper's own behaviour
observable: the four terminal conditions are its exit statuses, and no real run can be
made to produce all four on demand.

llmlint: ignore-file[shell_test_tiers_stay_split,test_tiers_split_by_project_not_by_marker] This
repository runs one Nx project and splits its test tiers by pytest marker over four keys
`nx.json` declares, which is a settled design rather than an omission: every test in this
module is `reads_recipes`, so the property is file-wide rather than per site.
`orchestrator:test-recipes` is a target of that same project running exactly `-m
reads_recipes` and keyed on `recipeWorkspace` — the justfile, `scripts/**` and the modules
that collect these tests, which is precisely what this journey reads — `tests/conftest.py`
fails a marked test that opens anything outside that key, and `tests/test_nx_cache_scope.py`
holds the four selectors to a partition of the suite. A second Nx project for one recipe
journey would add a key nothing enforces beside the ones that are.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: The scripts a checkout needs for this recipe to run: the wrapper, the renderer it
#: pipes through, and the one entry point every `onepipeline` verb goes through.
WRAPPER_SCRIPTS = ("watch-run.sh", "watch-render.py", "onepipeline.sh")

RUN = "run-1"

#: A failing engine's own output, at a length no terminal line should carry. Long enough
#: that the wrapper's bound has to cut it, and made of one repeated word so a journey can
#: say which end of it survived.
AT_LENGTH = "unreadable " * 80


def _record(kind: str, at: str, /, **fields: object) -> str:
    """One line of the machine-readable stream the doubled verb writes.

    A record names its own kind under `watch` — not `kind`, which is what the *event*
    inside an event record uses for its own kind. The two vocabularies at two levels are
    the shape the engine really writes, and reading the outer record by the inner field
    is the mistake this double exists to keep this repository's reader from making again.

    ``at`` is the stamp a record carries, and only an event record carries one: the
    engine puts it on the envelope inside, so this puts it there too, and a caller that
    passes no stamp gets a record with none. The payload stays a mapping because that is
    what it is — another program's JSON, whose fields this repository reads leniently on
    purpose.

    Both parameters are positional-only, because an event's envelope carries a key named
    `kind` of its own and a keyword parameter of that name would collide with it.
    """
    if kind == "event":
        envelope = dict(fields)
        if at:
            envelope["ts"] = at
        return json.dumps({"watch": "event", "event": envelope})
    return json.dumps({"watch": kind, **({"at": at} if at else {}), **fields})


def _event(at: str, kind: str, node: str = "", **payload: object) -> str:
    """One event record, in the envelope shape the engine relays an event inside.

    Composed rather than spelled per journey because the envelope is three levels — the
    record, the event, and its `labels` and `payload` — and a journey that built one by
    hand would be restating the shape rather than using it.
    """
    envelope: dict[str, object] = {"kind": kind}
    if node:
        envelope["labels"] = {"node": node}
    if payload:
        envelope["payload"] = payload
    return _record("event", at, **envelope)


#: One stream carrying every record kind the contract names: a meaningful event, a
#: heartbeat with the unread-surface count, and the terminal record naming the condition
#: and the cursor a later watch resumes from.
STREAM = "\n".join(
    (
        _event("T1", "node-settled", "adopt", outcome="done"),
        _record(
            "heartbeat",
            "",
            unread={"count": 2, "kinds": [{"kind": "finding", "count": 1}, {"kind": "monitor"}]},
        ),
        _record(
            "return",
            "",
            condition="settled",
            exit=0,
            cursor="c-42",
            unread={"count": 2, "kinds": [{"kind": "finding", "count": 1}]},
        ),
    )
)


def _checkout(tmp_path: Path) -> tuple[Path, Path]:
    """A checkout the real recipe runs in, with only the published engine doubled."""
    checkout = tmp_path / "watch"
    (checkout / "scripts").mkdir(parents=True)
    (checkout / "bin").mkdir()
    shutil.copy2(ROOT / "justfile", checkout / "justfile")
    for name in WRAPPER_SCRIPTS:
        copied = checkout / "scripts" / name
        shutil.copy2(ROOT / "scripts" / name, copied)
        copied.chmod(0o755)
    trace = checkout / "trace"
    uv = checkout / "bin/uv"
    # Records every command line, answers the wrapper's preflight — the engine's own
    # command list, which `FAKE_HAS_WATCH` puts the verb into and `FAKE_ENGINE_BROKEN`
    # refuses outright — and answers the watch itself with the stream and the exit
    # status each journey states. The two are stated apart deliberately: a verb whose
    # stream and whose status disagree is what proves the wrapper branches on the
    # status rather than on what the stream says.
    uv.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf 'uv %s\\n' "$*" >>"$TRACE_FILE"
if [ "$*" = "run onepipeline --help" ]; then
  if [ "${FAKE_ENGINE_BROKEN:-0}" = 1 ]; then
    printf 'error: the engine could not start\n\033[2Kand went on saying so:\n' >&2
    printf '%s\n' "$SAID_AT_LENGTH" >&2
    exit 101
  fi
  echo "Commands:"
  echo "  monitor     Stream a run's merged events"
  if [ "${FAKE_HAS_WATCH:-1}" = 1 ]; then
    echo "  watch       Watch one run until something a supervisor has to act on happens"
  fi
  exit 0
fi
if [ -n "${FAKE_WATCH_STREAM:-}" ]; then cat "$FAKE_WATCH_STREAM"; fi
exit "${FAKE_WATCH_EXIT:-0}"
"""
    )
    uv.chmod(0o755)
    return checkout, trace


def _watch(
    checkout: Path,
    trace: Path,
    *args: str,
    stream: str | None = None,
    exit_status: int = 0,
    has_watch: bool = True,
    engine_broken: bool = False,
    path: str | None = None,
    scratch: str | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PATH"] = path or f"{checkout / 'bin'}{os.pathsep}{environment['PATH']}"
    if scratch is not None:
        environment["TMPDIR"] = scratch
    environment["TRACE_FILE"] = str(trace)
    environment["FAKE_WATCH_EXIT"] = str(exit_status)
    environment["FAKE_HAS_WATCH"] = "1" if has_watch else "0"
    environment["FAKE_ENGINE_BROKEN"] = "1" if engine_broken else "0"
    environment["SAID_AT_LENGTH"] = AT_LENGTH
    if stream is not None:
        written = checkout / "stream.jsonl"
        written.write_text(stream + "\n", encoding="utf-8")
        environment["FAKE_WATCH_STREAM"] = str(written)
    return subprocess.run(
        ["just", "watch", *args],
        cwd=checkout,
        env=environment,
        check=False,
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )


class TerminalCondition(NamedTuple):
    """One ending the verb returns on, as the wrapper's own surface names it."""

    status: int
    name: str

    def named_by(self, reported: str) -> bool:
        """Whether `reported` names this condition — every word of it, in any order."""
        spoken = reported.casefold()
        return all(word in spoken for word in self.name.split("-"))


class SurfaceRow(NamedTuple):
    """One row of the wrapper's own account of itself: what kind of thing, and which."""

    kind: str
    rest: str


def _surface_rows() -> tuple[SurfaceRow, ...]:
    """That account, read from the wrapper rather than restated here.

    `scripts/watch-run.sh` is the one source of which status means which condition and
    of the word a caller anchors the cursor on, so a copy here would be a copy the drift
    gate does not read and would go on asserting the old meanings after the wrapper's
    moved.
    """
    reported = subprocess.run(
        [str(ROOT / "scripts" / "watch-run.sh"), "--print-surface"],
        text=True,
        capture_output=True,
        timeout=60,
        check=True,
    )
    return tuple(
        SurfaceRow(kind=kind, rest=rest)
        for kind, _, rest in (line.partition(" ") for line in reported.stdout.splitlines())
    )


def _terminal_conditions() -> tuple[TerminalCondition, ...]:
    """The four the wrapper branches on."""
    found = []
    for row in _surface_rows():
        if row.kind == "status":
            code, _, name = row.rest.partition(" ")
            found.append(TerminalCondition(int(code), name))
    return tuple(found)


def _cursor_prefix() -> str:
    """The word the cursor is emitted under, which is what a caller anchors on."""
    for row in _surface_rows():
        if row.kind == "cursor-prefix":
            return row.rest
    raise AssertionError("the wrapper's surface names no cursor prefix to anchor on")


TERMINAL_CONDITIONS = _terminal_conditions()
CURSOR_PREFIX = _cursor_prefix()


@pytest.mark.reads_recipes
@pytest.mark.parametrize("condition", TERMINAL_CONDITIONS, ids=lambda row: row.name)
def test_each_terminal_condition_is_reported_from_the_verbs_exit_status(
    tmp_path: Path, condition: TerminalCondition
) -> None:
    """Four conditions, four statuses, four distinguishable answers — and the status wins.

    The stream says `settled` in every case here while the verb exits with each of the
    four in turn. A recipe reading the human lines, or believing the stream over the
    status, would report a settled run three times out of four while a blocking
    question waited and nothing drove the run.
    """
    checkout, trace = _checkout(tmp_path)

    result = _watch(checkout, trace, RUN, stream=STREAM, exit_status=condition.status)

    assert result.returncode == condition.status, result.stderr
    reported = result.stdout + result.stderr
    assert condition.named_by(reported), reported
    assert RUN in reported


@pytest.mark.reads_recipes
def test_a_heartbeats_unread_surface_count_reaches_the_caller(tmp_path: Path) -> None:
    """The one signal the rule forbids filtering out arrives, with its kinds.

    A blocking surface produces no other signal until it is read, so a watch that
    emitted only on events would be silent through a queue of them — which is what
    happened here while twenty-six updates queued behind a question asked three times.
    """
    checkout, trace = _checkout(tmp_path)

    result = _watch(checkout, trace, RUN, stream=STREAM, exit_status=0)

    assert result.returncode == 0, result.stderr
    heartbeat = [line for line in result.stdout.splitlines() if line.startswith("heartbeat")]
    assert heartbeat == ["heartbeat  2 planner update(s) unread (finding 1, monitor)"]


#: Every render shape the wrapper composes, as `(record, the line it must produce)`. The
#: rendering is this repository's own — the verb hands over fields and these are the
#: sentences a supervisor reads — so what each shape comes out as is asserted rather than
#: left to whichever record a journey happened to use.
RENDERED = (
    (
        _event("T1", "node-settled", "adopt", outcome="done"),
        "event      T1  node-settled  adopt  done",
    ),
    (_record("event", "T1"), "event      T1  (unnamed event)"),
    (
        _record("heartbeat", "T1", unread={"count": 0, "kinds": []}),
        "heartbeat  T1  no planner update is unread",
    ),
    (
        _record("heartbeat", "T1", unread={"count": 3, "kinds": ["finding", "monitor"]}),
        "heartbeat  T1  3 planner update(s) unread (finding, monitor)",
    ),
    (
        _record(
            "heartbeat",
            "T1",
            unread={"count": 3, "kinds": [{"kind": "finding", "count": 1}, {"kind": "monitor"}]},
        ),
        "heartbeat  T1  3 planner update(s) unread (finding 1, monitor)",
    ),
    (
        _record("heartbeat", "T1", unread={"count": 3, "kinds": {"finding": "lots"}}),
        "heartbeat  T1  3 planner update(s) unread",
    ),
    (
        _record("return", "T1", unread={"count": 1, "kinds": {"finding": 1}}),
        "terminal   T1  (unnamed condition); 1 planner update(s) unread (finding 1)",
    ),
    (
        _record("wind-change", "T1", note="new"),
        'record     T1  {"at": "T1", "note": "new", "watch": "wind-change"}',
    ),
    # A record kind this build has never seen is rendered whole, and is still another
    # program's — so it reaches the terminal under the same bound every known field has,
    # and its escape sequences arrive inert: JSON spells a control character as its own
    # escape, so what a supervisor reads is the six characters rather than a cursor move.
    (
        _record("wind-change", "T1", note="new\u001bceased"),
        'record     T1  {"at": "T1", "note": "new\\u001bceased", "watch": "wind-change"}',
    ),
    (
        _record("wind-change", "T1", note="y" * 400),
        f'record     T1  {{"at": "T1", "note": "{"y" * 178}…',
    ),
    (
        _event("T1", "node-settled", "adopt\u001b[2Kdone"),
        "event      T1  node-settled  adopt [2Kdone",
    ),
    (
        _event("T1", "node-settled", message="x" * 400),
        f"event      T1  node-settled  {'x' * 200}…",
    ),
    # An event record whose envelope is not an object at all is still another program's
    # record: it is rendered whole under the same bound rather than dropped, because a
    # watch that showed nothing for a line the verb sent is the silence this ends.
    (
        json.dumps({"watch": "event", "event": "node-settled"}),
        'event      {"event": "node-settled", "watch": "event"}',
    ),
    # The fields a watch line is composed of are each read under more than one name,
    # because the record is another program's JSON and this repository reads it leniently
    # rather than strictly — a stamp under `timestamp`, an event kind under `name`, a
    # detail under `summary`. Each alias is a line an operator would otherwise not get,
    # so each is driven rather than left to whichever spelling a landed engine picks.
    (
        _record("event", "", timestamp="T2", name="node-settled"),
        "event      T2  node-settled",
    ),
    (
        _record("event", "", time="T3", type="node-started", payload={"message": "dispatching"}),
        "event      T3  node-started  dispatching",
    ),
    (
        _event("T4", "node-settled", detail="one commit"),
        "event      T4  node-settled  one commit",
    ),
)


@pytest.mark.reads_recipes
@pytest.mark.parametrize(("record", "line"), RENDERED, ids=lambda row: row[:40])
def test_each_record_shape_is_rendered_from_its_fields(
    tmp_path: Path, record: str, line: str
) -> None:
    """What a supervisor reads is composed here, so what it composes is stated here.

    Every shape the verb can hand over is one a watch has to survive and say something
    useful about: a record missing the field that names it, an unread count with no
    kinds, kinds as a list rather than a mapping, a count whose kinds are unusable, and a
    record kind this build has never seen. None of them is a failure and none of them is
    allowed to come out as a number nobody can act on.
    """
    checkout, trace = _checkout(tmp_path)

    result = _watch(checkout, trace, RUN, stream=record, exit_status=0)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == line


@pytest.mark.reads_recipes
def test_a_resumed_watch_carries_the_cursor_the_first_one_printed(tmp_path: Path) -> None:
    """The cursor a watch hands back is the one the next watch is given.

    Resuming from it is the verb's own promise; what the recipe owes is that the cursor
    reaches an operator in a form they can run, and that running it puts the cursor on
    the verb's command line rather than starting the watch over.
    """
    checkout, trace = _checkout(tmp_path)

    first = _watch(checkout, trace, RUN, stream=STREAM, exit_status=0)
    assert first.returncode == 0, first.stderr
    assert f"just watch {RUN} --cursor c-42" in first.stdout

    resumed = _watch(checkout, trace, RUN, "--cursor", "c-42", stream="", exit_status=0)

    assert resumed.returncode == 0, resumed.stderr
    assert f"uv run onepipeline watch {RUN} --cursor c-42" in trace.read_text().splitlines()
    # The second watch emitted only what its own stream carried: a resumed watch that
    # replayed the first one's events would be a watch nobody can read.
    assert "node-settled" not in resumed.stdout


#: How `AGENTS.md` tells a caller to read the cursor back: anchored on the word the
#: wrapper emits it under, taking the token and nothing around it. Driven as the real
#: `sed` an operator would type rather than reimplemented in Python, because what these
#: journeys owe is that the *documented* extraction works — a Python equivalent of it
#: could pass while the sentence a caller follows did not.
def _extract_cursor(reported: str) -> str:
    """The cursor a caller reads out of a finished watch, the documented way."""
    read = subprocess.run(
        ["sed", "-n", f"s/^{CURSOR_PREFIX} //p"],
        input=reported,
        text=True,
        capture_output=True,
        timeout=60,
        check=True,
    )
    return read.stdout.splitlines()[-1] if read.stdout.strip() else ""


@pytest.mark.reads_recipes
def test_the_cursor_is_emitted_where_a_caller_reads_it_back_without_parsing_prose(
    tmp_path: Path,
) -> None:
    """A caller extracts the cursor, re-arms with it, and reaches a terminal condition.

    The resume sentence prints the cursor inside a shell quotation, and a caller
    extracting it from there takes the closing quote with the token: `--cursor c-42'` is
    a cursor the verb refuses, and the watch that was meant to resume ends at a status
    that is none of the four terminal conditions. That is a watch stopping rather than
    continuing, and from a supervisor's seat it is the same silence as no watch at all.

    So the whole round trip is driven: the emitted line, the documented `sed` over it,
    the re-arm, what the engine was handed, and the ending the caller branched on.
    """
    checkout, trace = _checkout(tmp_path)

    first = _watch(checkout, trace, RUN, stream=STREAM, exit_status=0)
    assert first.returncode == 0, first.stderr
    # The token alone, with nothing around it to strip — not `c-42'`.
    assert f"{CURSOR_PREFIX} c-42" in first.stdout.splitlines()
    cursor = _extract_cursor(first.stdout)
    assert cursor == "c-42"

    resumed = _watch(checkout, trace, RUN, "--cursor", cursor, stream=STREAM, exit_status=0)

    assert resumed.returncode in {condition.status for condition in TERMINAL_CONDITIONS}
    assert resumed.returncode == 0, resumed.stderr
    assert f"uv run onepipeline watch {RUN} --cursor c-42" in trace.read_text().splitlines()


@pytest.mark.reads_recipes
def test_the_resume_sentence_a_reader_copies_is_emitted_beside_the_callers_line(
    tmp_path: Path,
) -> None:
    """Two readers, two forms, and the machine one did not replace the human one.

    A person reading a finished watch needs the command rather than the token, and a
    caller re-arming one needs the token rather than the command. Emitting only the
    first is what sent a caller into the quotation; emitting only the second would leave
    a supervisor holding an opaque word with nothing saying what to do with it.
    """
    checkout, trace = _checkout(tmp_path)

    result = _watch(checkout, trace, RUN, stream=STREAM, exit_status=0)

    assert result.returncode == 0, result.stderr
    assert f"just watch {RUN} --cursor c-42" in result.stdout
    assert f"{CURSOR_PREFIX} c-42" in result.stdout.splitlines()


@pytest.mark.reads_recipes
def test_a_caller_is_handed_the_cursor_after_an_ending_that_is_none_of_the_four(
    tmp_path: Path,
) -> None:
    """The engine's own queued and refused are where a caller most needs to resume.

    `1` and `2` belong to no terminal condition, and a watch that met one has still read
    part of the stream — so withholding the cursor there would make the caller start
    over and re-read everything the watch already showed, which is the repetition the
    cursor exists to prevent.
    """
    checkout, trace = _checkout(tmp_path)

    result = _watch(checkout, trace, RUN, stream=STREAM, exit_status=1)

    assert result.returncode == 1
    assert _extract_cursor(result.stdout) == "c-42"


@pytest.mark.reads_recipes
def test_no_cursor_is_emitted_when_the_verb_named_none(tmp_path: Path) -> None:
    """An absent cursor is an absent line, never an empty one.

    A caller anchoring on the word gets nothing to extract and starts a fresh watch,
    which is what a verb naming no cursor is asking for. A line carrying the word and no
    token would be extracted as the empty string and re-armed as `--cursor ''`, which is
    a refusal rather than a fresh watch.
    """
    checkout, trace = _checkout(tmp_path)
    stream = _record("return", "T1", condition="settled", exit=0)

    result = _watch(checkout, trace, RUN, stream=stream, exit_status=0)

    assert result.returncode == 0, result.stderr
    assert CURSOR_PREFIX not in result.stdout
    assert "--cursor" not in result.stdout
    assert "the run settled" in result.stdout


#: A stream whose records this repository cannot read: one line that is not JSON at all,
#: and one that parses but is not an object. Both are records whose events, heartbeat or
#: terminal condition are simply lost, which is a different thing from a record kind this
#: build does not recognise.
UNREADABLE_STREAM = "\n".join(("this is not json at all", '"not an object either"'))


@pytest.mark.reads_recipes
def test_machine_output_that_cannot_be_read_is_a_failure_and_never_a_settled_run(
    tmp_path: Path,
) -> None:
    """The verb exits 0 and its stream is unreadable — and this must not report a settled run.

    That pairing is the dangerous one rather than a contrived one: the exit status and
    the stream are two halves of one answer, and a wrapper that trusts the status while
    the stream carried nothing tells an operator the run settled on the strength of a
    channel that delivered no event, no heartbeat and no unread-surface count. The
    operator then sees a clean exit and stops looking, which is the silence-read-as-
    progress this whole command exists to end. So the refusal replaces the terminal
    condition rather than being printed beside it.
    """
    checkout, trace = _checkout(tmp_path)

    result = _watch(checkout, trace, RUN, stream=UNREADABLE_STREAM, exit_status=0)

    assert result.returncode == 2, result.stdout + result.stderr
    reported = result.stdout + result.stderr
    for condition in TERMINAL_CONDITIONS:
        assert not condition.named_by(reported)
    assert "could not be read" in result.stderr
    assert "not having happened" in result.stderr


@pytest.mark.reads_recipes
def test_a_record_kind_this_build_does_not_recognise_is_not_a_failure(tmp_path: Path) -> None:
    """A verb that grows a fifth record kind is not a broken watch.

    The refusal above has to be about records that could not be *read*, not about
    records this does not recognise — a wrapper that failed on the second would refuse
    every watch the day the verb adds a kind, and the failure it would report is one
    nothing is wrong with. So the unknown kind is rendered whole and the terminal
    condition is reported as it would be without it.
    """
    checkout, trace = _checkout(tmp_path)
    stream = "\n".join((STREAM, json.dumps({"kind": "wind-change", "at": "T4", "note": "new"})))

    result = _watch(checkout, trace, RUN, stream=stream, exit_status=0)

    assert result.returncode == 0, result.stderr
    assert "wind-change" in result.stdout
    assert "the run settled" in result.stdout


@pytest.mark.reads_recipes
def test_an_engine_that_cannot_be_asked_is_not_reported_as_one_without_the_verb(
    tmp_path: Path,
) -> None:
    """A broken installation and an older engine are two problems with two answers.

    Deciding the verb's absence from a *failed* probe would report every one of these as
    a missing verb and send an operator off to adopt a release that was never the
    trouble. So the question is asked positively, of the engine's own command list, and
    an engine that cannot answer it carries its own words into the refusal.
    """
    checkout, trace = _checkout(tmp_path)

    result = _watch(checkout, trace, RUN, engine_broken=True)

    assert result.returncode == 2
    assert "could not be asked what verbs it has" in result.stderr
    assert "the engine could not start" in result.stderr
    assert "offers no watch verb" not in result.stderr
    assert "config/onepipeline.version" not in result.stderr

    # A failing engine is the one whose output is least likely to be well formed, and it
    # is relayed straight onto an operator's screen — so it arrives stripped of the
    # escape sequences that would rewrite what a watch had already reported, folded onto
    # the one line this refusal is, and cut before a failing engine can fill the screen
    # with itself. Cut at the far end rather than the near one: what the engine said
    # first is what says why it failed.
    said = [line for line in result.stderr.splitlines() if "could not be asked" in line]
    assert len(said) == 1
    assert "\x1b" not in said[0]
    assert "and went on saying so:" in said[0]
    assert AT_LENGTH.strip() not in said[0]
    assert "unreadable unreadable" in said[0]


#: Each option shape a caller can put before the run, and the run that must still be the
#: one this watch reports on. Together they are every branch of the wrapper's argument
#: reading: a value-taking option, one written with `=`, and the two that take none.
OPTIONS_BEFORE_THE_RUN = (
    ("--timeout", "600"),
    ("--filter=monitor",),
    ("--all",),
    ("--tick-interval", "5"),
    ("--all", "--cursor", "c-7"),
)


@pytest.mark.reads_recipes
def test_a_field_carrying_terminal_escapes_cannot_rewrite_what_the_watch_reported(
    tmp_path: Path,
) -> None:
    """Another program's values reach a terminal here, so they are stripped on the way.

    A watch renders fields it did not write straight onto an operator's screen. An escape
    sequence in one of them moves the cursor, erases lines a watch had already reported,
    or colours a failure green — so a supervisor reading a live watch would be reading
    something the producer composed rather than what happened.

    What is removed is the escape *introducer*, which is what makes the rest of the
    sequence inert: the residue reads as the literal characters it is, which is the
    honest rendering of a field that carried them. Deleting the whole sequence instead
    would hide from an operator that the producer sent one.
    """
    checkout, trace = _checkout(tmp_path)
    stream = "\n".join(
        (
            _event("T1", "node-settled", detail="done\u001b[1;32m fine"),
            _record("heartbeat", "T2", unread={"count": 1, "kinds": {"find\u001b[2Jing": 1}}),
        )
    )

    result = _watch(checkout, trace, RUN, stream=stream, exit_status=0)

    assert result.returncode == 0, result.stderr
    assert "\x1b" not in result.stdout
    assert result.stdout.splitlines()[:2] == [
        "event      T1  node-settled  done [1;32m fine",
        "heartbeat  T2  1 planner update(s) unread (find [2Jing 1)",
    ]


@pytest.mark.reads_recipes
def test_an_invocation_of_nothing_but_options_names_no_run_and_is_refused(
    tmp_path: Path,
) -> None:
    """Options are not a run, and a watch of no run delegates nothing.

    This is a different path from naming no arguments at all: the wrapper reads its
    arguments, finds every one of them accounted for as an option or an option's value,
    and refuses — rather than delegating a watch with no run and letting the engine
    answer for a shape this repository composed.
    """
    checkout, trace = _checkout(tmp_path)

    result = _watch(checkout, trace, "--timeout", "600", "--all")

    assert result.returncode == 2
    assert "name no run to watch, only options" in result.stderr
    assert not trace.exists()


@pytest.mark.reads_recipes
def test_a_run_that_is_not_an_opaque_token_is_refused_before_it_reaches_a_command_line(
    tmp_path: Path,
) -> None:
    """The run is printed inside a command an operator copies, so it is validated too.

    Same boundary as the cursor and the same answer: a value carrying a space or a shell
    metacharacter would compose a `just watch … --cursor …` line that does something
    other than resume a watch, and this one arrives from whoever typed the command.
    """
    checkout, trace = _checkout(tmp_path)

    result = _watch(checkout, trace, "run-1; id")

    assert result.returncode == 2
    assert "is not a run id this will put into a command line" in result.stderr
    assert not trace.exists()


@pytest.mark.reads_recipes
@pytest.mark.parametrize("before", OPTIONS_BEFORE_THE_RUN, ids=lambda row: " ".join(row))
def test_the_run_is_found_whichever_option_shape_precedes_it(
    tmp_path: Path, before: tuple[str, ...]
) -> None:
    """`--timeout 600` is an option and its value, not a run — and neither is `--all`.

    The run is what every line this wrapper writes is about: the terminal summary and
    the resume command both name it. Reading an option, or an option's value, as the run
    would put the wrong word in front of a supervisor and compose a resume command that
    watches nothing.
    """
    checkout, trace = _checkout(tmp_path)

    result = _watch(checkout, trace, *before, RUN, stream=STREAM, exit_status=4)

    assert result.returncode == 4
    assert f"watch: {RUN}: a blocking planner surface is waiting" in result.stdout
    assert f"just watch {RUN} --cursor c-42" in result.stdout


@pytest.mark.reads_recipes
def test_the_caller_s_own_arguments_are_all_the_verb_is_given(tmp_path: Path) -> None:
    """The wrapper adds nothing to the command line it was handed.

    It used to append `--json`, because the surface it was written against — before any
    engine offered the verb — was guessed to have one. The verb that exists writes both
    forms unconditionally, the operator's lines on standard error and the machine form on
    standard output, so there is nothing to ask for; appending an option the verb does not
    take is a watch refused before it watches anything, which is the failure the drift
    gate over this wrapper exists to catch.
    """
    checkout, trace = _checkout(tmp_path)

    result = _watch(checkout, trace, RUN, "--tick-interval", "5", stream=STREAM, exit_status=0)

    assert result.returncode == 0, result.stderr
    delegated = [line for line in trace.read_text().splitlines() if "watch" in line]
    assert delegated == [f"uv run onepipeline watch {RUN} --tick-interval 5"]


@pytest.mark.reads_recipes
def test_a_watch_naming_no_run_is_refused_before_anything_is_delegated(
    tmp_path: Path,
) -> None:
    """A watch with no run to watch names the shape it wanted and delegates nothing.

    It shares the refusal status with the two other things that stop this wrapper
    watching, which is the point of that status: none of them is a terminal condition,
    and a caller branching on the four cannot mistake one for a run that ended.
    """
    checkout, trace = _checkout(tmp_path)

    result = _watch(checkout, trace)

    assert result.returncode == 2
    assert "name the run to watch" in result.stderr
    assert not trace.exists()


@pytest.mark.reads_recipes
def test_a_run_named_after_an_option_is_still_the_run_this_watch_reports_on(
    tmp_path: Path,
) -> None:
    """An operator types the run first; a caller need not, and gets its own run named back.

    The run is what every line this wrapper writes is about — the terminal summary and
    the resume command both name it — so reading `--timeout` as the run would put the wrong
    word in front of a supervisor and compose a resume command that watches nothing.
    """
    checkout, trace = _checkout(tmp_path)

    result = _watch(checkout, trace, "--timeout", "600", RUN, stream=STREAM, exit_status=4)

    assert result.returncode == 4
    assert f"watch: {RUN}: a blocking planner surface is waiting" in result.stdout
    assert f"just watch {RUN} --cursor c-42" in result.stdout


@pytest.mark.reads_recipes
def test_a_heartbeat_reporting_no_usable_unread_count_says_so_rather_than_a_number(
    tmp_path: Path,
) -> None:
    """The one clause the rule forbids filtering out is never invented from a bad field.

    A `total` that arrived as something other than a count is not zero and is not two;
    rendering it as either would put a number a supervisor can act on in front of them
    with nothing behind it. So the absence is named, and the watch still runs.
    """
    checkout, trace = _checkout(tmp_path)
    stream = "\n".join(
        (
            _record("heartbeat", "T1", unread={"total": "many", "kinds": {"finding": 1}}),
            _record("heartbeat", "T2"),
        )
    )

    result = _watch(checkout, trace, RUN, stream=stream, exit_status=0)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[:2] == [
        "heartbeat  T1  unread surfaces: this record reports no usable count (finding 1)",
        "heartbeat  T2  unread surfaces: not reported by this record",
    ]


@pytest.mark.reads_recipes
def test_a_cursor_that_is_not_an_opaque_token_is_refused_rather_than_handed_back(
    tmp_path: Path,
) -> None:
    """The cursor is printed inside a command an operator copies, so it is validated.

    A value carrying a space or a shell metacharacter would compose a `just watch …
    --cursor …` line that does something other than resume a watch. Refusing it is the
    only safe answer: a cursor this had to mangle would resume from somewhere nobody
    chose, so the watch reports that it can vouch for nothing rather than guessing.
    """
    checkout, trace = _checkout(tmp_path)
    stream = _record("return", "T1", condition="settled", cursor="$(id) later")

    result = _watch(checkout, trace, RUN, stream=stream, exit_status=0)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "cursor is an opaque token and this one is not" in result.stderr
    assert "--cursor" not in result.stdout
    assert "the run settled" not in (result.stdout + result.stderr)


@pytest.mark.reads_recipes
def test_an_exit_status_that_is_none_of_the_four_conditions_is_reported_as_that(
    tmp_path: Path,
) -> None:
    """The engine's own refusals are neither hidden nor dressed up as an ending.

    `1` and `2` are the engine's queued and refused and belong to no terminal condition,
    so a watch that met one says which status it met and hands it back unchanged — a
    caller branching on the four is then told plainly that it got none of them.
    """
    checkout, trace = _checkout(tmp_path)

    result = _watch(checkout, trace, RUN, stream=STREAM, exit_status=1)

    assert result.returncode == 1
    assert "ended at exit status 1, which is none of its four terminal conditions" in result.stderr
    # The stream's own words are rendered faithfully whatever they say, so it is this
    # wrapper's summary — the only line that claims an ending — that must claim none.
    summary = [line for line in result.stderr.splitlines() if line.startswith("watch: ")]
    assert len(summary) == 1
    for condition in TERMINAL_CONDITIONS:
        assert not condition.named_by(summary[0])


@pytest.mark.reads_recipes
def test_an_engine_without_the_watch_verb_produces_this_repositorys_own_message(
    tmp_path: Path,
) -> None:
    """The recipe lands before the pin that carries the verb, and says so in one line.

    An operator who read the rule and typed the command learns what is missing and
    which pin carries it, rather than reading a clap usage error about a subcommand
    they never typed — and the engine's own words are not passed on as if they were
    this repository's answer.
    """
    checkout, trace = _checkout(tmp_path)

    result = _watch(checkout, trace, RUN, has_watch=False)

    assert result.returncode == 2
    assert "offers no watch verb" in result.stderr
    assert "config/onepipeline.version" in result.stderr
    # The engine's own answer is read, not relayed: an operator gets this repository's
    # sentence about a pin, rather than a command list they have to interpret.
    assert "Commands:" not in result.stderr
    assert trace.read_text().splitlines() == ["uv run onepipeline --help"]


#: What the recipe needs on PATH to run at all, once the interpreter it renders with is
#: taken away: the shell and `just` that reach the wrapper, and the utilities the wrapper
#: itself calls. `uv` is the double the checkout already carries.
WITHOUT_PYTHON = (
    "env",
    "bash",
    "sh",
    "just",
    "mktemp",
    "rm",
    "cat",
    "tr",
    "cut",
    "grep",
    "dirname",
)


@pytest.mark.reads_recipes
def test_a_checkout_with_no_python_to_render_with_says_so_rather_than_dying_mid_watch(
    tmp_path: Path,
) -> None:
    """The interpreter is resolved before the watch, so its absence is not a dead run.

    The renderer is what reads the verb's machine-readable form, so a checkout with no
    interpreter can watch nothing — and named inside the pipeline instead, its absence
    arrives as the shell's own `command not found` in the middle of what looks like a
    watch, which a supervisor reads as the run having gone wrong. So it is resolved
    first, refused in this command's own words, and the engine is never asked to watch.
    """
    checkout, trace = _checkout(tmp_path)
    bare = checkout / "nopython"
    bare.mkdir()
    for tool in WITHOUT_PYTHON:
        found = shutil.which(tool)
        assert found is not None, f"this host has no {tool}, so the journey cannot run"
        (bare / tool).symlink_to(found)

    result = _watch(
        checkout, trace, RUN, stream=STREAM, path=f"{checkout / 'bin'}{os.pathsep}{bare}"
    )

    assert result.returncode == 2
    assert "needs a python3" in result.stderr
    assert "just bootstrap" in result.stderr
    assert "nothing was watched" in result.stderr
    # Refused before the run was watched rather than part-way through it: the preflight
    # is the only thing the engine was asked, and no terminal condition is claimed.
    assert trace.read_text().splitlines() == ["uv run onepipeline --help"]
    assert result.stdout == ""


@pytest.mark.reads_recipes
def test_a_scratch_directory_it_cannot_write_to_is_refused_in_this_commands_own_words(
    tmp_path: Path,
) -> None:
    """`mktemp`'s bare diagnostic says nothing about what this was doing or what to do.

    The wrapper reads the resume cursor back through a temporary file, so a scratch
    directory it cannot create one in stops the watch before it starts — and under
    `set -e` that would end with only the shell's own line, naming neither the command
    nor a way out.
    """
    checkout, trace = _checkout(tmp_path)

    result = _watch(
        checkout, trace, RUN, stream=STREAM, scratch=str(tmp_path / "no-such-directory")
    )

    assert result.returncode == 2
    assert "resume cursor" in result.stderr
    assert "no-such-directory" in result.stderr
    assert "nothing was watched" in result.stderr
    assert trace.read_text().splitlines() == ["uv run onepipeline --help"]
