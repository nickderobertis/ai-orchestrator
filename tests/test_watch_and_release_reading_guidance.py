"""What `AGENTS.md` tells a supervisor about watching a run and reading a release.

Each claim those two passages make is enumerated rather than summarized, because none
of them announces itself when it goes missing: a buffered watch produces no error, a
cursor extracted with its quote is refused far from where it was read, and a comparison
that called a live publication a failure looks exactly like one that caught a real one.

Four are reconciled against something other than this repository's own prose — the word
the wrapper emits the cursor under, the count of its terminal conditions, the observer a
planning launch attaches, and the per-record flush the buffering account rests on.
"""

from __future__ import annotations

import subprocess
from typing import NamedTuple

import pytest

from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_docs

#: The manager's own document, and the two passages these claims live in. Passages
#: rather than the whole file, because each claim is made where a supervisor is doing
#: that thing — a phrase that survived somewhere else would satisfy a document-wide
#: search while the passage that has to carry it was gone.
MANAGER = "AGENTS.md"
WATCH_SECTION = "### Never let dispatched work run unwatched"
RELEASE_OPENER = "**Whether a release exists is read from the registry"

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


class Claim(NamedTuple):
    """One thing a passage has to say, and the phrase that says it."""

    #: What the claim is about, for the failure message and the test id.
    subject: str
    #: Which passage has to carry it.
    region: str
    phrase: str


#: Every claim these passages exist to make. Each is a thing a supervisor would
#: otherwise learn by watching a run go silent, which is how all of them were learned.
REQUIRED_CLAIMS = (
    Claim(
        "the verb is invoked rather than scripted around",
        WATCH_SECTION,
        "**Invoke that verb directly, and write no loop around it.**",
    ),
    Claim(
        "why a hand-written loop is the failure this replaces",
        WATCH_SECTION,
        "Every loop written here is a fresh chance to lose the invariant in a new way",
    ),
    Claim(
        "and why fixing one fixes none of the others",
        WATCH_SECTION,
        "no fifth loop inherits a fix for any of them",
    ),
    Claim(
        "where the properties hold by construction",
        WATCH_SECTION,
        "hold by construction inside the command and by somebody's memory anywhere else",
    ),
    Claim(
        "a missing condition is reported rather than looped around",
        WATCH_SECTION,
        "a missing terminal condition worth reporting rather than a loop worth writing",
    ),
    Claim(
        "the cursor reaches a caller as well as a reader",
        WATCH_SECTION,
        "**The cursor is emitted for a caller as well as printed for a reader.**",
    ),
    Claim(
        "on a line of its own",
        WATCH_SECTION,
        "as a line of its own carrying `watch-cursor <cursor>` and nothing else",
    ),
    Claim(
        "how a caller reads it back",
        WATCH_SECTION,
        "cursor=$(sed -n 's/^watch-cursor //p' watch.log | tail -n 1)",
    ),
    Claim(
        "what parsing it out of the sentence costs",
        WATCH_SECTION,
        "Extracting the token out of the sentence takes the closing quote along with it",
    ),
    Claim(
        "and where the watch re-armed with it ends",
        WATCH_SECTION,
        "ends at a status that is none of the four",
    ),
    Claim(
        "what a piped watch depends on",
        WATCH_SECTION,
        "**What you pipe a watch into decides whether you see any of it.**",
    ),
    Claim(
        "the lines are written as they happen",
        WATCH_SECTION,
        "Every line is written as it happens",
    ),
    Claim(
        "which filters hold everything until the watch exits",
        WATCH_SECTION,
        "`sed` and `awk` delivered nothing at all",
    ),
    Claim(
        "what tail does by construction",
        WATCH_SECTION,
        "`tail` shows nothing by construction because it is holding out for the end",
    ),
    Claim(
        "which filters pass a line through",
        WATCH_SECTION,
        "`cat` and GNU `grep` pass each line through as it arrives",
    ),
    Claim(
        "what a buffered watch is indistinguishable from",
        WATCH_SECTION,
        "**A buffered watch reads from outside exactly like a healthy quiet run and "
        "exactly like a dead one**",
    ),
    Claim(
        "and what to do about it",
        WATCH_SECTION,
        "redirect it to a file and read the file, or make the filter line-buffer "
        "(`stdbuf -oL`, `sed -u`, `grep --line-buffered`)",
    ),
    Claim(
        "monitoring is critical for every dispatch",
        WATCH_SECTION,
        "**Monitoring is critical for every dispatch, and a planning run is a dispatch.**",
    ),
    Claim(
        "no class of launch is exempt",
        WATCH_SECTION,
        "There is no class of launch this rule exempts",
    ),
    Claim(
        "a planning run attaches no observer",
        WATCH_SECTION,
        "`just plan` names `--dag-graph off` deliberately",
    ),
    Claim(
        "what a supervisor owes a launch that attaches no monitor",
        WATCH_SECTION,
        "What a supervisor owes a launch that attaches no monitor of its own is the same "
        "thing it owes every other one",
    ),
    Claim(
        "whether a release exists is read from the registry",
        RELEASE_OPENER,
        "read from the registry, and from nothing else",
    ),
    Claim(
        "not from a verification workflow's conclusion",
        RELEASE_OPENER,
        "a **verification workflow's conclusion**, which reports one verdict over three "
        "different jobs of work",
    ),
    Claim(
        "what that conclusion cannot tell apart",
        RELEASE_OPENER,
        "a genuine post-publish failure, a pre-publish gate failure and ordinary noise are "
        "indistinguishable in it",
    ),
    Claim(
        "not from a comparison of a tag against a registry",
        RELEASE_OPENER,
        "a **comparison of a tag against a registry**",
    ),
    Claim(
        "what that comparison misreads",
        RELEASE_OPENER,
        "reads work still in flight as work that failed",
    ),
    Claim(
        "that it was reproduced after the rule was recorded",
        RELEASE_OPENER,
        "from a watcher written *after* this rule had already been recorded",
    ),
    Claim(
        "and what to do instead",
        RELEASE_OPENER,
        "ask the registry for the version, and where it is not there yet, wait and ask it again",
    ),
)


def _text(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _flat(prose: str) -> str:
    """Collapse every run of whitespace, so a claim may be quoted as one line."""
    return " ".join(prose.split())


def _region(opener: str) -> str:
    """The passage a claim has to be made in, from its opener to the next heading.

    The opener is part of the passage rather than the boundary before it, because a
    passage's own first sentence states the rule the rest of it is about. Cut at a
    heading of any depth rather than at a top-level one: both passages here sit under
    `###` headings, and stopping only at `## ` would let a phrase deleted from one
    passage be satisfied by a neighbouring section that still carries it.
    """
    document = _text(MANAGER)
    assert opener in document, (
        f"{MANAGER} no longer carries {opener!r}, which is where a supervisor is told how "
        "to watch a run and how to read whether a release exists"
    )
    lines = (opener + document.split(opener, 1)[1]).splitlines()
    # From the second line on: one of these two openers *is* a heading, and a passage
    # bounded by the first heading it meets would be the empty string.
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


@pytest.mark.parametrize("claim", REQUIRED_CLAIMS, ids=lambda claim: claim.subject)
def test_the_supervisor_is_told_every_part_of_watching_and_of_reading_a_release(
    claim: Claim,
) -> None:
    """A claim dropped from here is one a supervisor relearns by losing a run.

    Each of these was learned the expensive way and none of them announces itself: a
    buffered watch, an expired one, a loop matching its own shell, and a comparison
    calling a live publication a failure all look from outside like everything working.
    """
    assert _flat(claim.phrase) in _flat(_region(claim.region)), (
        f"{MANAGER}'s {claim.region!r} passage no longer says {claim.subject}: the phrase "
        f"{claim.phrase!r} is gone"
    )


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


def test_the_endings_the_passage_counts_are_the_ones_the_wrapper_has() -> None:
    """ "None of the four" is a number, and the wrapper is what decides it.

    The passage tells a supervisor that a watch re-armed with a mangled cursor ends at a
    status that is none of the four terminal conditions. A wrapper that grew a fifth
    would leave that sentence miscounting the endings a caller branches on, which is the
    one thing about this verb a caller is asked to rely on.
    """
    conditions = [row.rest for row in _surface_rows() if row.kind == "status"]

    assert len(conditions) == 4, (
        f"{WRAPPER.name} now branches on {len(conditions)} terminal conditions "
        f"({[name.split(' ', 1)[-1] for name in conditions]}), and {MANAGER} counts four. "
        "Re-count the passage, or a supervisor is told about endings the command no "
        "longer has"
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
