"""`scripts/launcher-session.sh` decides who is acting, for every verb on this host.

Ownership is a comparison: a run records the session that launched it, `just stop`
refuses a run this session does not own, `just unwatched` reports only this session's
runs, and — from the adopted `onepipeline-ui` — a mutation from the browser is performed
as the session `scripts/telemetry-server.sh` hands the API. All of those compare against
the value this one helper derives, which is why it is one file rather than a ladder
copied into each caller.

It is also a **trust boundary**. The value arrives from whichever harness exported it
and becomes an ownership credential that reaches a command line, a launch record and
every later comparison. So the two directions are held here: a usable id is claimed, and
an unusable one leaves the process unattributed *and says so* — which is the safe half of
`AGENTS.md`'s rule that a run misattributed to a planner who did not launch it is worse
than one attributed to nobody.

Everything drives the real helper under a real bash, sourced the way every caller
sources it, in an environment built from nothing so no variable this dispatch exports
can reach a verdict.
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import NamedTuple

import pytest

from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_recipes

HELPER = REPO_ROOT / "scripts" / "launcher-session.sh"


class Marker(NamedTuple):
    """One variable the ladder reads, and the launcher name claiming it records."""

    variable: str
    launcher: str


#: Every variable the ladder reads, in the order it reads them, paired with the launcher
#: name that claiming one records. Read from here rather than restated per test, so a
#: marker added to the helper without a row is a marker nothing holds.
MARKERS = (
    Marker("CLAUDE_CODE_SESSION_ID", "claude-code"),
    Marker("CLAUDE_SESSION_ID", "claude-code"),
    Marker("CODEX_THREAD_ID", "codex"),
    Marker("CODEX_SESSION_ID", "codex"),
)


class Resolved(NamedTuple):
    """What the helper exported, and what it said while doing it."""

    launcher: str
    session: str
    stderr: str


def _sourced(environment: dict[str, str]) -> Resolved:
    """Source the helper under ``environment`` alone and read back what it exported."""
    read = subprocess.run(
        [
            "bash",
            "-c",
            f'. "{HELPER}"; '
            'printf "%s\\n%s\\n" "${ONEPIPELINE_LAUNCHER-}" "${ONEPIPELINE_LAUNCHER_SESSION-}"',
        ],
        env={"PATH": "/usr/bin:/bin", **environment},
        capture_output=True,
        text=True,
        check=False,
    )
    assert read.returncode == 0, read.stderr
    launcher, session = read.stdout.splitlines()[:2]
    return Resolved(launcher, session, read.stderr)


@pytest.mark.parametrize(("variable", "launcher"), MARKERS, ids=[name for name, _ in MARKERS])
def test_each_harness_marker_claims_its_own_launcher(variable: str, launcher: str) -> None:
    """Every variable the ladder reads resolves, and records the harness it names.

    Per marker rather than for the one this host's own sessions happen to set: the
    fallback spellings are what a host running the other harness, or an older one of
    this one, identifies itself by, and a ladder that silently stopped reading one would
    leave every run that host launched owned by nobody.
    """
    resolved = _sourced({variable: "a-session-id"})

    assert (resolved.launcher, resolved.session) == (launcher, "a-session-id"), resolved


def test_a_claude_marker_wins_over_a_codex_one() -> None:
    """The order is the contract: a session nested inside another takes the first claim.

    Held because the two are not symmetric in practice — a Claude session that shells
    out under a developer's exported Codex variables is the ordinary case, and the
    reverse reading would hand every such run to the wrong harness.
    """
    resolved = _sourced({"CLAUDE_CODE_SESSION_ID": "the-claude-one", "CODEX_THREAD_ID": "thr_1"})

    assert (resolved.launcher, resolved.session) == ("claude-code", "the-claude-one"), resolved


def test_an_environment_naming_no_harness_claims_nothing_and_says_nothing() -> None:
    """A plain shell stays unidentified, quietly.

    Quietly is the assertion that matters: this helper is sourced by every read-only
    view as well as by every launch, so a complaint here would land on `just runs` in a
    terminal that has no harness and never wanted one.
    """
    resolved = _sourced({})

    assert (resolved.launcher, resolved.session) == ("", ""), resolved
    assert resolved.stderr == "", resolved.stderr


def test_an_already_exported_identity_is_honoured_untouched() -> None:
    """A nested dispatch keeps its planner's identity, whatever the harness around it."""
    resolved = _sourced(
        {"ONEPIPELINE_LAUNCHER_SESSION": "a-planner-session", "CLAUDE_CODE_SESSION_ID": "other"}
    )

    assert resolved.session == "a-planner-session", resolved
    assert resolved.stderr == "", resolved.stderr


def test_a_malformed_inherited_identity_is_reported_and_kept() -> None:
    """Validated like a derived one, and answered differently on purpose.

    Kept, because the run it names was launched under it by the process that exported
    it: dropping it here would leave this process unattributed while its parent is
    attributed, splitting one run's ownership across two identities — the reassignment
    the ladder refuses to make. Reported, because a boundary that accepted it silently
    would be no boundary, and the process to repair is whichever exported it.
    """
    resolved = _sourced(
        {"ONEPIPELINE_LAUNCHER_SESSION": "a value with spaces", "CLAUDE_CODE_SESSION_ID": "other"}
    )

    assert resolved.session == "a value with spaces", resolved
    assert "inherited ONEPIPELINE_LAUNCHER_SESSION is not the shape" in resolved.stderr, (
        resolved.stderr
    )


@pytest.mark.parametrize(
    ("value", "why"),
    [
        ("a session id", "a space would split into two words on a command line"),
        ("a;rm -rf /", "a metacharacter is the one that has to be refused"),
        ("a,b", "the history-label wire format has no escape for a comma"),
        ("-leading-hyphen", "a value that reads as an option to whatever is handed it"),
        ("", "nothing at all, which is not an identity"),
        ("x" * 201, "a runaway value long enough to be a command line of its own"),
    ],
    ids=("space", "metacharacter", "comma", "leading-hyphen", "empty", "too-long"),
)
def test_a_session_id_that_is_not_one_leaves_the_process_unattributed(value: str, why: str) -> None:
    """A malformed credential is refused, and the refusal is audible.

    Both halves are the point. Exporting it would attribute runs to something no later
    read can match, which `AGENTS.md` calls worse than no attribution. Dropping it
    silently would read as a host with no harness at all, and send an operator looking
    for a missing variable rather than a bad one.
    """
    resolved = _sourced({"CLAUDE_CODE_SESSION_ID": value})

    assert (resolved.launcher, resolved.session) == ("", ""), f"{resolved} — {why}"
    if value:
        assert "stays unattributed" in resolved.stderr, resolved.stderr
    else:
        # An empty variable is not a harness claiming anything, so it is the quiet case
        # above rather than a malformed credential: nothing is there to complain about.
        assert resolved.stderr == "", resolved.stderr


def test_the_markers_held_here_are_exactly_the_ones_the_helper_reads() -> None:
    """`MARKERS` is read back out of the helper, so the two cannot drift apart.

    Every other test here drives the rows of `MARKERS`, which makes the list a second
    copy of the helper's own — and a copy nothing reconciles is how a marker added to
    the helper would go untested while this module stayed green. So the variables the
    helper's ladder expands are extracted from its source and compared, whole.
    """
    source = HELPER.read_text(encoding="utf-8")
    ladder = [line for line in source.splitlines() if line.lstrip().startswith("launcher_")]
    read = {name for line in ladder for name in re.findall(r"\$\{([A-Z][A-Z0-9_]*):-", line)}

    assert read == {variable for variable, _ in MARKERS}, (
        f"{HELPER.name}'s ladder reads {sorted(read)} and this module holds "
        f"{sorted(variable for variable, _ in MARKERS)}. Add the row for whichever marker "
        "the helper gained, so the claim it makes is driven rather than assumed"
    )


def test_the_session_id_the_running_harness_exported_is_one_the_helper_accepts() -> None:
    """The grammar, held to the one producer this suite can read: the harness running it.

    The shape the helper admits is this host's own rule, not a copy of a published one —
    neither harness publishes a session-id grammar — so there is no contract document to
    reconcile it against. What there is, is the producer itself: this suite runs inside a
    harness session, and that harness exported its id in one of the markers above. If a
    release of either harness changed the shape of the ids it mints, every run this host
    launched would become unattributed, silently, and this is where that shows first.

    Skipped rather than passed where no marker is set, because a suite run outside any
    harness has no producer to read, and a pass there would claim a reconciliation that
    never happened.
    """
    produced = [(variable, value) for variable, _ in MARKERS if (value := os.environ.get(variable))]
    if not produced:
        pytest.skip("this suite is not running inside a harness session, so no id to read")

    variable, value = produced[0]
    resolved = _sourced({variable: value})

    assert resolved.session == value, (
        f"the harness running this suite exported {variable} with an id of {len(value)} "
        f"characters that {HELPER.name} refuses as an identity: {resolved.stderr.strip()}. "
        "That harness now mints ids outside the grammar, so every run this host launches is "
        "unattributed — widen the grammar to admit the new shape"
    )


def test_the_helper_is_sourced_rather_than_run() -> None:
    """No shebang and no execute bit, which is what its callers rely on.

    A helper that established the acting session in a child process would establish it
    for that child and nobody else, and the failure is silent: every caller reads back
    an unset variable and carries on unattributed. The shape is what prevents somebody
    reaching for `scripts/launcher-session.sh` as a command, and its neighbours under
    `scripts/` that are also sourced carry the same two properties.
    """
    text = HELPER.read_text(encoding="utf-8")

    assert not text.startswith("#!"), (
        "the helper carries a shebang, which invites running it — and a run of it exports "
        "the acting session into a child process nothing reads"
    )
    assert text.startswith("# shellcheck shell=bash"), text.splitlines()[0]
    assert not HELPER.stat().st_mode & 0o111, (
        f"{HELPER} is executable; every sourced helper beside it is not, for the reason above"
    )


def test_every_caller_sources_this_one_definition() -> None:
    """Every caller reaches the ladder through this file and holds no copy of it.

    This is the property the file exists for: a reader that identified itself
    differently from the launcher would match no run, and `just stop` would refuse
    every run on the host. Asserted by the marker names, because a second copy is what
    a copy looks like — the same variables read somewhere else.
    """
    callers = (
        REPO_ROOT / "scripts" / "onepipeline.sh",
        REPO_ROOT / "scripts" / "telemetry-server.sh",
        REPO_ROOT / "scripts" / "unpublished.sh",
    )

    for caller in callers:
        text = caller.read_text(encoding="utf-8")
        assert f'. "$script_dir/{HELPER.name}"' in text or (
            f'launcher_session_helper="$script_dir/{HELPER.name}"' in text
            and '. "$launcher_session_helper"' in text
        ), (
            f"{caller.name} does not source {HELPER.name}, so it derives the acting "
            "session somewhere else or not at all"
        )
        for variable, _ in MARKERS:
            assert variable not in text, (
                f"{caller.name} reads {variable} itself; that is a second copy of the "
                f"ladder, and the two agreeing today is not a property anything holds"
            )


def test_no_other_script_reads_a_harness_marker_for_itself() -> None:
    """The whole of `scripts/` holds one reader of these variables, and it is this one.

    Widened from the two callers deliberately: the failure this prevents is a *third*
    caller written later that derives the identity its own way, which the paired
    assertion above cannot see. A script that genuinely needs the value sources this
    file, as both callers do.
    """
    offenders = {
        path.name: variable
        for path in sorted((REPO_ROOT / "scripts").glob("*.sh"))
        if path != HELPER
        for variable, _ in MARKERS
        if variable in path.read_text(encoding="utf-8")
    }

    assert not offenders, (
        f"these scripts read a harness session variable themselves: {offenders}. Source "
        f"scripts/{HELPER.name} instead — two spellings of the ladder is how a reader "
        "ends up owning nothing while the launcher owns everything"
    )
