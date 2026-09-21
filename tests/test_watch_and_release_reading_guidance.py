"""What `AGENTS.md` tells a supervisor about watching a run, held to what this checkout has.

Three things are reconciled against something other than this repository's own prose —
the word the engine's ending line carries the cursor under, the observer a planning
launch attaches, and the split between the two forms the watch writes that the
buffering account rests on — because none of them announces itself when it drifts: a
cursor read from under the wrong word is refused far from where it was read, and a
buffered watch produces no error at all.

The two that ask the installed engine are in the uncached `reads_checkouts` tier, because
their subject is a producer outside this workspace.

llmlint: ignore-file[shell_test_tiers_stay_split,test_tiers_split_by_project_not_by_marker] The
marker is this repository's tier mechanism rather than a shortcut around one: one Nx
project runs four tiers keyed on four `nx.json` named inputs, and `reads_checkouts`
selects the uncached `orchestrator:test-checkouts` target that exists because no key over
this workspace can describe an installed engine. A project of its own would need a key
over the same nothing; `tests/test_nx_cache_scope.py` holds the selectors to a partition
of the suite. `tests/test_watch_surface_drift.py` carries the same directive for the
same reason.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest
from watch_rule import ENDING_LINE, rule

from orchestrator.root import REPO_ROOT

#: The manager's own document, and the passage the watch claims live in. A passage
#: rather than the whole file, because each claim is made where a supervisor is doing
#: that thing — a phrase that survived somewhere else would satisfy a document-wide
#: search while the passage that has to carry it was gone.
MANAGER = "AGENTS.md"
WATCH_SECTION = "### Never let dispatched work run unwatched"

#: The engine this host installed, from this checkout's own environment, and a recorded
#: run it is driven over: one that settled, so a watch reads it once and returns.
ENGINE = REPO_ROOT / ".venv" / "bin" / "onepipeline"
RECORDED_RUNS = REPO_ROOT / "tests" / "fixtures" / "timeline-runs"
SETTLED_RUN = "gate-parity-2"
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


def _watched() -> subprocess.CompletedProcess[str]:
    """The installed verb over the settled recorded run, read once."""
    return subprocess.run(
        [str(ENGINE), "watch", SETTLED_RUN, "--timeout", "0"],
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
        env={**os.environ, "ONEPIPELINE_RUNS_DIR": str(RECORDED_RUNS)},
    )


@pytest.mark.reads_checkouts
def test_the_word_a_caller_is_told_to_anchor_on_is_the_word_the_engine_emits() -> None:
    """The documented cursor line and the emission, reconciled.

    `AGENTS.md` tells a caller to re-arm from the cursor the engine's ending line carries,
    anchored on one word, and says it is the same token as the return record's. Two
    copies of that word is how a caller comes to read the wrong line — which reads as a
    watch that handed back no cursor, so the caller starts over and re-reads everything
    the last watch already showed rather than learning that it read the wrong word.
    """
    word = rule().cursor_word
    watched = _watched()
    record = json.loads(watched.stdout.splitlines()[-1])
    ending = ENDING_LINE.match(watched.stderr.splitlines()[-1])

    assert ending is not None, (
        f"the engine's last line on standard error is not an ending line:\n{watched.stderr}"
    )
    assert f" {word} {record['cursor']}" in ending.group(0), (
        f"{MANAGER} tells a caller to re-arm from `{word} <cursor>` on the ending line, and "
        f"the engine's ending line does not carry its return record's cursor that way: "
        f"{ending.group(0)!r}"
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


@pytest.mark.reads_checkouts
def test_the_buffering_account_rests_on_the_forms_the_engine_really_splits() -> None:
    """The human form on standard error and the machine record on standard output.

    What the passage asks a supervisor to redirect or line-buffer is only sound while the
    two forms are where it says: a filter over standard output would otherwise be
    filtering the operator's lines, and the silence it reads would be a different stream's.
    """
    assert "Its human form is on stderr" in " ".join(_region(WATCH_SECTION).split()), (
        f"{MANAGER} no longer says which descriptor carries which form of a watch"
    )

    watched = _watched()

    assert all(json.loads(line).get("watch") for line in watched.stdout.splitlines()), (
        f"the engine's standard output carries a line that is not a machine record:\n"
        f"{watched.stdout}"
    )
    assert watched.stderr.splitlines()[-1].startswith("-- watch "), watched.stderr
