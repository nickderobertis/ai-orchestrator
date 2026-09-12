"""What `AGENTS.md` tells a supervisor about watching a run, held to what this checkout has.

Four things are reconciled against something other than this repository's own prose —
the word the wrapper emits the cursor under, the count of its terminal conditions, the
observer a planning launch attaches, and the per-record flush the buffering account
rests on — because none of them announces itself when it drifts: a cursor extracted
under the wrong word is refused far from where it was read, and a buffered watch
produces no error at all.
"""

from __future__ import annotations

import re
import subprocess
from typing import NamedTuple

import pytest

from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_docs

#: The manager's own document, and the passage the watch claims live in. A passage
#: rather than the whole file, because each claim is made where a supervisor is doing
#: that thing — a phrase that survived somewhere else would satisfy a document-wide
#: search while the passage that has to carry it was gone.
MANAGER = "AGENTS.md"
WATCH_SECTION = "### Never let dispatched work run unwatched"

#: The wrapper the watch passage documents, and the one place it declares the word a
#: caller anchors the cursor on and the terminal conditions it branches between.
WRAPPER = REPO_ROOT / "scripts" / "watch-run.sh"
#: The renderer whose per-record flush is what makes "every line is written as it
#: happens" true of this command rather than an aspiration about it.
RENDERER = "scripts/watch-render.py"
#: The launcher whose observer default is why a planning run has nothing watching it.
PLAN_LAUNCHER = "scripts/plan.sh"
#: What that launcher names when the caller names no graph. Quoted as the assignment so
#: a default that moved to another spelling fails here rather than matching some other
#: `off` in the file.
PLANNING_OBSERVER = 'DEFAULT_DAG_GRAPH="off"'


def _text(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _flat(prose: str) -> str:
    """Collapse every run of whitespace, so a claim may be quoted as one line."""
    return " ".join(prose.split())


def _region(opener: str) -> str:
    """The passage a claim has to be made in, from its opener to the next heading.

    The opener is part of the passage rather than the boundary before it, because a
    passage's own first sentence states the rule the rest of it is about. Cut at a
    heading of any depth rather than at a top-level one: the passage sits under a
    `###` heading, and stopping only at `## ` would let a phrase deleted from it be
    satisfied by a neighbouring section that still carries it.
    """
    document = _text(MANAGER)
    assert opener in document, (
        f"{MANAGER} no longer carries {opener!r}, which is where a supervisor is told how "
        "to watch a run and how to read whether a release exists"
    )
    lines = (opener + document.split(opener, 1)[1]).splitlines()
    # From the second line on: the opener *is* a heading, and a passage bounded by the
    # first heading it meets would be the empty string.
    for line_number, line in enumerate(lines[1:], start=1):
        if line.startswith("#") and line.lstrip("#").startswith(" "):
            return "\n".join(lines[:line_number])
    return "\n".join(lines)


class SurfaceRow(NamedTuple):
    """One row of the wrapper's own account of itself: what kind of thing, and which."""

    kind: str
    rest: str


def _surface_rows() -> list[SurfaceRow]:
    """The wrapper's own account of what it emits."""
    # llmlint: ignore[shell_test_tiers_stay_split] This asks the wrapper for one declared
    # row of its own self-description rather than testing shell behaviour — an offline
    # sub-second read touching no network, no installed engine and no run — and no tier
    # here could hold it if it did: this module's subject is `AGENTS.md`, so it is
    # `reads_docs` and keyed on the whole workspace, and `tests/conftest.py` fails a
    # `reads_recipes` test that opens a document. The alternative to asking is a second
    # copy of the word `AGENTS.md` documents, which is the drift this gate prevents.
    # `tests/test_watch_surface_drift.py` reads the same declaration for the same reason.
    reported = subprocess.run(
        [str(WRAPPER), "--print-surface"],
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert reported.returncode == 0, (
        f"`{WRAPPER.name} --print-surface` exited {reported.returncode}, so what the "
        f"wrapper emits cannot be read from it:\n{reported.stderr}"
    )
    rows = []
    for line in reported.stdout.splitlines():
        kind, _, rest = line.partition(" ")
        rows.append(SurfaceRow(kind=kind, rest=rest))
    return rows


def test_the_word_a_caller_is_told_to_anchor_on_is_the_word_the_wrapper_emits() -> None:
    """The documented extraction and the emission, reconciled.

    `AGENTS.md` hands a caller a `sed` expression anchored on one word, and
    `scripts/watch-run.sh` is what writes that word. Two copies of it is how a
    documented extraction comes to return nothing at all — which reads as a watch that
    handed back no cursor, so the caller starts over and re-reads everything the last
    watch already showed rather than learning that it read the wrong word.
    """
    emitted = [row.rest for row in _surface_rows() if row.kind == "cursor-prefix"]
    assert len(emitted) == 1, (
        f"`{WRAPPER.name} --print-surface` names {len(emitted)} cursor prefixes, so there "
        "is no single word for the documented extraction to be reconciled against"
    )
    prefix = emitted[0]

    passage = _flat(_region(WATCH_SECTION))
    assert f"`{prefix} <cursor>`" in passage, (
        f"{MANAGER} no longer documents the cursor line as `{prefix} <cursor>`, which is "
        f"what {WRAPPER.name} emits. Reconcile the passage with the wrapper, or a caller "
        "anchors on a word no watch writes"
    )
    assert f"sed -n 's/^{prefix} //p'" in passage, (
        f"{MANAGER}'s documented extraction no longer anchors on {prefix!r}, which is the "
        f"word {WRAPPER.name} emits the cursor under"
    )


#: How the passage spells a small count, so the number it writes can be read back. Only
#: as far as the endings this verb could plausibly grow; a count past that is a document
#: this gate should fail on rather than quietly understand.
COUNTED = {
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
}

#: Where the passage states that count. Both sentences name it, and both are read: one
#: is what a caller branching on the statuses is told, and the other is what a supervisor
#: reading a mangled cursor's ending is told.
COUNTS_THE_ENDINGS = (
    re.compile(r"returns on one of (\w+) terminal\s+conditions"),
    re.compile(r"ends at a status that\s+is none of the (\w+)"),
)


def test_the_endings_the_passage_counts_are_the_ones_the_wrapper_has() -> None:
    """ "None of the four" is a number, and the wrapper is what decides it.

    The passage tells a supervisor how many terminal conditions a watch returns on, and
    that a watch re-armed with a mangled cursor ends at a status that is none of them. A
    wrapper that grew one more would leave both sentences miscounting the endings a
    caller branches on, which is the one thing about this verb a caller is asked to rely
    on.

    **The count is read out of the passage rather than written here**, and that is the
    repair this check needed rather than a new number. It asserted `4` as a literal, so
    the release that gave the verb a fifth ending failed it with a demand to *re-count
    the passage* — which is right — and would have gone on passing had somebody moved
    only the literal. Reading both sides means the gate says the same thing at any count
    the verb reaches, and says it about the document rather than about itself.
    """
    conditions = [row.rest for row in _surface_rows() if row.kind == "status"]
    passage = _flat(_region(WATCH_SECTION))

    for spelling in COUNTS_THE_ENDINGS:
        written = spelling.search(passage)
        assert written is not None, (
            f"{MANAGER} no longer counts the terminal conditions where this gate reads "
            f"it ({spelling.pattern!r}), so the count a caller branches on is "
            "reconciled against nothing"
        )
        counted = COUNTED.get(written.group(1))
        assert counted == len(conditions), (
            f"{WRAPPER.name} branches on {len(conditions)} terminal conditions "
            f"({[name.split(' ', 1)[-1] for name in conditions]}), and {MANAGER} says "
            f"{written.group(0)!r}. Re-count the passage, or a supervisor is told about "
            "endings the command does not have"
        )


def test_the_planning_launch_really_attaches_no_monitor_of_its_own() -> None:
    """The claim a planning run watches least of itself has to be true of the launcher.

    It is the reason that property is stated at all: a supervisor who believed a
    planning run had an observer would read its silence as a monitor with nothing to
    report. If `just plan` ever attached one, the property would be telling a supervisor
    to distrust a tier that was working.
    """
    launcher = _text(PLAN_LAUNCHER)

    assert PLANNING_OBSERVER in launcher, (
        f"{PLAN_LAUNCHER} no longer defaults to {PLANNING_OBSERVER!r}, and {MANAGER} tells "
        "a supervisor that a planning run attaches no monitor of its own. Either the "
        "launcher changed or the passage did"
    )


def test_the_buffering_account_rests_on_a_renderer_that_really_flushes() -> None:
    """ "Every line is written as it happens" is a property of this command, not a hope.

    What the passage asks a supervisor to conclude from silence — that a filter is
    holding the lines rather than that the watch has stopped producing them — is only
    sound while this end of the pipe writes each line as it arrives. A renderer that
    stopped flushing would make the whole account backwards: the silence would be the
    watch's own, and the advice would send a supervisor to fix their filter.
    """
    renderer = _text(RENDERER)

    assert "flush=True" in renderer, (
        f"{RENDERER} no longer flushes each rendered record, so {MANAGER}'s account of a "
        "buffered watch is about the wrong end of the pipe: the silence would be this "
        "command's own rather than the filter's"
    )
