"""`just unwatched` and the `Stop` hook that reads it, against the installed engine.

AGENTS.md's watch rule is what this pair enforces. Property 1 of it — *a watch is armed
before you turn to anything else* — was the one property with nothing behind it but the
model's memory, and the measured cost of that is dispatched work sitting for hours with
nothing looking at it. The verb answers which of a session's runs nothing is watching;
the hook asks it at the end of every turn and refuses the stop while the answer is not
empty.

**Two kinds of journey, held to two different bars.** Every journey whose subject is
*what the verb says* — which runs it names, which it excludes, which it leaves undecided,
what the recipe carries back, what a real watch does to its answer — obtains that answer
from the `onepipeline` this checkout installs from `config/onepipeline.version`, driven
over runs roots it assembles. Nothing stands in for it there: the evidence behind a
verb-shaped answer is the engine's, and a double would be this repository asserting its
own guess at what a watcher record means. That is what makes this an adoption rather than
a pin edit, and `test_the_installed_engine_is_the_release_the_pin_names_and_offers_the_verb`
is what says the binary those journeys ask is the pinned one.

The other kind is about *the hook's own branching* — that it treats `6` as a block and
`0` as clear, that it says so and ends the turn on a verb that cannot be executed or
answers a status of its own, that it does not hang on one that never returns, that it
resolves the engine where it says it does — and those are states the real verb has no way
to produce without inventing run state to provoke them. Each of those plants a sibling
beside a copy of the hook, and says so at the site:
`test_the_hook_says_the_turn_is_unguarded_when_the_engine_does_not_answer`,
`test_the_hook_says_the_turn_is_unguarded_when_the_engine_runs_past_its_bound`,
`test_the_wrapper_says_the_turn_is_unguarded_when_it_cannot_reach_its_own_half` and
`test_the_hook_falls_through_to_the_engine_on_the_search_path`. Nothing in them is a
claim about the verb.

llmlint: ignore-file[e2e_not_mocked] The engine is real in every journey that obtains an
answer here; what is planted is a *sibling* of a copy of this hook, in the four journeys
about an engine that does not answer. Those are host conditions the real verb has no way to
produce — a binary that cannot be executed, one that answers a status of its own, one that
never returns, and none at all — and what the hook says on each is precisely what has to be
shown. The hook itself is the tracked file, copied byte for byte.

llmlint: ignore-file[tests_mirror_real_usage] A run root in a state the verb decides
differently about is the subject here, and three of those states no interface produces: a
summary document an earlier build wrote, one a crash left unreadable, and a watcher record
a writer did not finish. Everything an interface *can* produce is produced through one —
the run roots are the engine's own records through `tests/e2e/probe_run_root.py`, which
`tests/test_engine_contracts.py` reconciles field by field against the installed engine;
`onepipeline runs` is what folds and stores each summary document; and `onepipeline stop`
is what settles a run. What the hook remembers is read directly in exactly one journey,
which is the journey about *where* it is kept: there is no interface that answers that,
and the alternative is a claim about a location nothing checks.

"""

from __future__ import annotations

import ast
import json
import os
import shutil
import signal
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import NamedTuple, NewType, TypedDict

import probe_run_root
import pytest
from nx_workspace import SHARED_TOOLCHAIN_GROUP
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: This module is its own Nx project's, `unwatched`; see `tests/unwatched/project.json` and
#: the guard in `tests/conftest.py`. No marker routes it: the directory decides.
#:
#: Every journey here reaches a tool through `uv run` — `just unwatched` and `just watch`
#: both do — and one of them takes this checkout's project-environment lock outright to
#: show the hook does not wait on it. `uv` holds that lock exclusively, so both halves
#: belong in the group AGENTS.md's four-worker invariant names: `--dist loadgroup`
#: co-locates the tests sharing a group *name* and says nothing about two different ones,
#: so a holder in one group and a waiter in another are as concurrent as if neither
#: declared anything.
pytestmark = pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP)

#: The engine this checkout installs from its own pin. Named as a path rather than
#: resolved off `PATH`, because a journey that measured whichever copy some other
#: checkout left on the search path would be a journey about that checkout.
ONEPIPELINE = REPO_ROOT / ".venv" / "bin" / "onepipeline"

#: The hook `.claude/settings.json` registers, driven here exactly as the harness runs it.
HOOK = REPO_ROOT / "scripts" / "stop-unwatched-guard.sh"

#: The status the verb answers when a run the asked-about session owns has nothing
#: watching it, and the one number the hook acts on. Spelled here so that the journeys
#: below say which of the two answers they got rather than comparing to a bare integer.
RUNS_UNWATCHED = 6

#: The status that says nothing this session owns is unwatched.
NOTHING_UNWATCHED = 0


#: A launching session, which is the whole of what this verb asks about and is a `str`
#: beside every path, run id and reason these journeys also carry.
Session = NewType("Session", str)


class Owned(NamedTuple):
    """One journey's runs root and the session that owns what is in it."""

    root: Path
    session: Session


def _installed() -> None:
    """Skip where this checkout has no engine to ask, rather than measuring another's."""
    if not ONEPIPELINE.is_file():
        pytest.skip(f"this checkout has no installed engine at {ONEPIPELINE}")


def _environment(owned: Owned, *, reader: Session | None = None) -> dict[str, str]:
    """The environment a reader of `owned`'s runs root runs in.

    `reader` is who is *asking*, which is a different question from who owns the runs and
    is the whole of what the hook must not confuse: it becomes
    `ONEPIPELINE_LAUNCHER_SESSION`, and it defaults to the owning session only because
    most journeys here have no reason to part the two.
    """
    environment = dict(os.environ)
    environment["ONEPIPELINE_RUNS_DIR"] = str(owned.root)
    environment["ONEPIPELINE_LAUNCHER"] = "claude-code"
    environment["ONEPIPELINE_LAUNCHER_SESSION"] = owned.session if reader is None else reader
    return environment


def _verb(
    *arguments: str, environment: dict[str, str], seconds: float = 60
) -> subprocess.CompletedProcess[str]:
    """Ask the installed engine, from a working directory that holds no runs root of its own."""
    return subprocess.run(  # noqa: S603 - the installed engine, named by absolute path
        [str(ONEPIPELINE), *arguments],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
        cwd=environment["ONEPIPELINE_RUNS_DIR"],
        timeout=e2e_timeout(seconds),
    )


def _unwatched(
    owned: Owned, *, reader: Session | None = None, about: Session | None = None
) -> subprocess.CompletedProcess[str]:
    """`onepipeline unwatched` about one session, asked as some session."""
    environment = _environment(owned, reader=reader)
    session = owned.session if about is None else about
    return _verb("unwatched", "--session", session, environment=environment)


def _assembled(owned: Owned, run: str, *, driver_pid: int | None = None) -> Path:
    """One run root of `owned`'s session, with the dispatch registry every verb expects.

    The registry directory is created empty rather than left absent because `stop` — the
    verb these journeys settle a run with — refuses a run whose registry it cannot read
    at all, and an absent directory is that refusal rather than an empty registry.
    """
    directory = probe_run_root.run_root(
        owned.root, run, session=owned.session, driver_pid=driver_pid
    )
    (directory / "dispatches").mkdir(exist_ok=True)
    return directory


def _summary(owned: Owned, run: str) -> Path:
    return owned.root / run / "summary.json"


def _make_current(owned: Owned, run: str) -> None:
    """Have the engine fold and store this run's summary document.

    A **listing** is what writes that document — it is the listing this release serves
    from it — and that is what makes the document the engine's own account of the run
    rather than this journey's guess at one. A run with no current document is one whose
    settlement the verb declines to decide, so a journey that skipped this would be
    measuring the undecidable case everywhere instead of the one it came for.

    The listing folds every run under the root rather than the one named, which is why
    the journeys that want an *undecidable* run take its document away afterwards rather
    than by never folding it.
    """
    read = _verb("runs", environment=_environment(owned))
    assert read.returncode == 0, f"the engine could not list this root:\n{read.stderr}"
    assert _summary(owned, run).is_file(), (
        f"listing this root left no summary document at {_summary(owned, run)}, so nothing "
        "here can put a run into the state the verb decides from. The engine folds and "
        "stores that document as it serves a listing; an engine that stopped doing so is "
        "what this says"
    )


def _settled(owned: Owned, run: str) -> None:
    """Stop the run for real, so its own record says it stopped and its document is current."""
    stopped = _verb("stop", run, environment=_environment(owned))
    assert stopped.returncode == 0, f"run {run} was not stopped:\n{stopped.stdout}{stopped.stderr}"
    assert _summary(owned, run).is_file(), f"stopping run {run} left no summary document"


def _unreadable_watcher_record(owned: Owned, run: str) -> Path:
    """A watcher record this build cannot read, which is not a live watch and says so.

    Left by a writer that did not finish, which is the one way a record reaches this
    state: the verb writes each record whole, so nothing but a crash mid-write produces
    one that will not parse.
    """
    directory = owned.root / run / "watchers"
    directory.mkdir(exist_ok=True)
    record = directory / "1-halfwritten.json"
    record.write_text('{"schema_version": 1, "run_id": "', encoding="utf-8")
    return record


def _hook(
    payload: object,
    environment: dict[str, str],
    *,
    cwd: Path | None = None,
    seconds: float = 60,
) -> subprocess.CompletedProcess[str]:
    """Run the real hook the way the harness runs it: payload on standard input."""
    return subprocess.run(  # noqa: S603 - the checked-in hook, run as the harness runs it
        ["bash", str(HOOK)],
        input=payload if isinstance(payload, str) else json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
        env=environment,
        cwd=str(cwd if cwd is not None else REPO_ROOT),
        timeout=e2e_timeout(seconds),
    )


# llmlint: ignore[contracts_have_one_source_or_a_drift_gate] The producer of this shape is
# the harness, whose Stop payload exists on this host only as a process handing one to a
# hook: no schema verb, no installed document, nothing a gate could read it off. So the one
# source here is the consumer's — the two field names the hook declares — and
# `test_these_journeys_key_their_payloads_as_the_hook_reads_them` below reconciles these
# keys against it; the other two are what the harness sends and the hook ignores, and no
# drift in them can change what a journey here measures.
class StopPayload(TypedDict):
    """The Stop payload the harness hands a hook, in the fields this one is driven with.

    Declared rather than left a bare mapping because it is the shape under test: the hook
    reads two of these keys and ignores the rest, and a journey that misspelled one would
    drive the *unreadable-payload* path while reading like it drove the others.
    """

    session_id: Session
    transcript_path: str
    hook_event_name: str
    stop_hook_active: bool


def _stop_payload(session: Session, *, again: bool = False) -> StopPayload:
    """One Stop payload, as the harness hands one to a hook."""
    return {
        "session_id": session,
        "transcript_path": "/dev/null",
        "hook_event_name": "Stop",
        "stop_hook_active": again,
    }


def test_these_journeys_key_their_payloads_as_the_hook_reads_them() -> None:
    """The two field names above are the two the hook declares, reconciled rather than trusted.

    They are a copy: the hook is the one thing here that reads the harness's payload, and
    these journeys have to *compose* one. A copy that drifted would not fail loudly — it
    would drive the unreadable-payload path, which is a real ending with a real journey of
    its own, so every other journey in this module would go on passing while measuring
    that instead of what it says it measures.
    """
    keyed = set(StopPayload.__annotations__)
    for field in ("SESSION_FIELD", "CONTINUATION_FIELD"):
        declared = _declared(field)
        assert declared in keyed, (
            f"{GUARD.name} reads {declared!r} off the Stop payload and this module composes "
            f"payloads keyed {sorted(keyed)}; every journey here would drive the "
            "unreadable-payload ending instead of its own"
        )


def _blocked(answered: subprocess.CompletedProcess[str]) -> str:
    """The reason a blocking answer carries, having held it to the whole shape first."""
    assert answered.returncode == 0, f"the hook exited {answered.returncode}, not 0"
    assert answered.stderr == "", f"the hook wrote to standard error: {answered.stderr!r}"
    assert answered.stdout.strip(), "the hook wrote nothing on standard output, so it did not block"
    decision = json.loads(answered.stdout)
    assert decision["decision"] == "block", f"the hook did not block: {answered.stdout!r}"
    reason = decision["reason"]
    assert isinstance(reason, str)
    return reason


def _warned(answered: subprocess.CompletedProcess[str], why: str) -> str:
    """Hold an answer to the ending that is neither a block nor silence, and hand back what it said.

    The hook says so and ends the turn where it has a positive answer it cannot safely
    act on, and where it could not obtain an answer at all: one JSON object on standard
    output carrying a `systemMessage` — which the harness renders to the user as a warning
    and ends the turn on — and no `decision`, so nothing is refused.
    """
    assert answered.returncode == 0, f"{why}: the hook exited {answered.returncode}, not 0"
    assert answered.stderr == "", f"{why}: the hook wrote {answered.stderr!r} to standard error"
    said = json.loads(answered.stdout)
    assert "decision" not in said, f"{why}: the hook refused the turn: {answered.stdout!r}"
    message = said.get("systemMessage")
    assert isinstance(message, str) and message, f"{why}: the hook said nothing: {said}"
    return message


def _silent(answered: subprocess.CompletedProcess[str], why: str) -> None:
    """Hold an answer to the silent ending: nothing unwatched, or nothing the hook could ask about.

    The first is the real verb answering `0`, and the second is a payload naming no
    session — which are the only two outcomes that end a turn with nothing said, now that
    every way the verb can fail to answer says so.
    """
    assert answered.returncode == 0, f"{why}: the hook exited {answered.returncode}, not 0"
    assert answered.stdout == "", f"{why}: the hook wrote {answered.stdout!r} to standard output"
    assert answered.stderr == "", f"{why}: the hook wrote {answered.stderr!r} to standard error"


def _working_tree() -> tuple[str, ...]:
    """Every path this checkout's git reports as changed or untracked, in one order."""
    reported = subprocess.run(  # noqa: S603 - git, over this checkout, reading only
        ["git", "status", "--porcelain", "--untracked-files=all"],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
        timeout=e2e_timeout(120),
    ).stdout
    return tuple(sorted(reported.splitlines()))


@pytest.fixture(name="state_home")
def _state_home(tmp_path: Path) -> Iterator[Path]:
    """A home of this journey's own, so the hook's memory is this journey's memory.

    The hook keeps what it last blocked a session on under the XDG state root, which on
    a real host is the operator's. Pointing `HOME` and `XDG_STATE_HOME` at a directory
    per journey is what keeps one journey's block out of another's continuation — and it
    is also how the journey below can say the memory is nowhere near the tracked tree or
    the runs root, by naming where it *is*.
    """
    home = tmp_path / "home"
    (home / ".local" / "state").mkdir(parents=True)
    yield home
    shutil.rmtree(home, ignore_errors=True)


def _at_home(environment: dict[str, str], home: Path) -> dict[str, str]:
    return {**environment, "HOME": str(home), "XDG_STATE_HOME": str(home / ".local" / "state")}


def _hook_tree(
    tmp_path: Path,
    engine: str | None,
    *,
    executable: bool = True,
    without: str = "",
) -> Path:
    """A directory the checked-in hook runs in, with the engine beside it planted.

    The hook resolves the engine as this checkout's own `.venv/bin/onepipeline` before
    anything on the search path, which is what stops it measuring whichever copy some
    other checkout left there — and it is also why the four host conditions below cannot
    be reached by planting on `PATH`. A copy of the tracked hook, at a root whose
    `.venv/bin` this journey owns, is the only way to ask what it does when the engine it
    resolves cannot be executed or answers a status of its own. Both scripts are copied
    byte for byte, so the file under test is the tracked one and only its *sibling* is
    this journey's.

    `engine` is the script planted at that path, or `None` for a root that has none —
    which, with the search path scrubbed, is the missing-binary condition. `without` names
    a script to leave out, which is how the wrapper's own prerequisites are driven: it
    resolves its Python half beside itself, so a root missing that half is the only way to
    ask what it does about one.
    """
    root = tmp_path / "hook-tree"
    (root / "scripts").mkdir(parents=True)
    for script in ("stop-unwatched-guard.sh", "stop-unwatched-guard.py"):
        if script == without:
            continue
        shutil.copy2(REPO_ROOT / "scripts" / script, root / "scripts" / script)
    if engine is not None:
        binaries = root / ".venv" / "bin"
        binaries.mkdir(parents=True)
        planted = binaries / "onepipeline"
        planted.write_text(engine, encoding="utf-8")
        planted.chmod(0o755 if executable else 0o644)
    return root


def _planted_hook(
    root: Path, payload: object, environment: dict[str, str], *, seconds: float = 60
) -> subprocess.CompletedProcess[str]:
    """Run the copied hook out of `root`, so the engine it resolves is the planted one."""
    return subprocess.run(  # noqa: S603 - the tracked hook script, copied, run as the harness does
        ["bash", str(root / "scripts" / "stop-unwatched-guard.sh")],
        input=payload if isinstance(payload, str) else json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
        env=environment,
        cwd=str(root),
        timeout=e2e_timeout(seconds),
    )


def test_the_installed_engine_is_the_release_the_pin_names_and_offers_the_verb() -> None:
    """The binary every journey below asks is this checkout's own, at its own pin.

    Read rather than assumed, because everything here is a claim about one binary: a
    journey that had drifted onto another checkout's copy — or onto whatever `PATH`
    resolves inside a dispatch — would go on passing while saying nothing about the
    release this repository adopted.
    """
    _installed()
    pinned = (REPO_ROOT / "config" / "onepipeline.version").read_text(encoding="utf-8").strip()
    reported = subprocess.run(  # noqa: S603 - the installed engine, named by absolute path
        [str(ONEPIPELINE), "--version"],
        capture_output=True,
        text=True,
        check=True,
        timeout=e2e_timeout(60),
    ).stdout
    assert reported.split() == ["onepipeline", pinned], (
        f"{ONEPIPELINE} reports {reported.strip()!r} where config/onepipeline.version "
        f"names {pinned}: this checkout is not running the release it adopted, so nothing "
        "below is a measurement of that release"
    )
    verbs = subprocess.run(  # noqa: S603 - the installed engine, named by absolute path
        [str(ONEPIPELINE), "--help"],
        capture_output=True,
        text=True,
        check=True,
        timeout=e2e_timeout(60),
    ).stdout
    assert any(line.split()[:1] == ["unwatched"] for line in verbs.splitlines() if line.strip()), (
        f"the engine at {pinned} offers no `unwatched` verb, so the recipe and the hook "
        f"this repository ships have nothing to delegate to:\n{verbs}"
    )


def _row(listing: str, run: str) -> str:
    """The one row a listing renders for `run`, without the notes indented under it.

    A row opens at the left margin with the marker the listing puts on every run; the
    lines beneath it are indented and name the same run again, which is why a match on
    the id alone would find more than one.
    """
    rows = [
        line
        for line in listing.splitlines()
        if not line.startswith(" ") and line.split()[1:2] == [run]
    ]
    assert len(rows) == 1, f"the listing renders {len(rows)} rows for {run}:\n{listing}"
    return rows[0]


def test_a_listing_answers_from_the_summary_document_without_opening_the_event_store(
    tmp_path: Path,
) -> None:
    """`runs --mine` serves a run whose summary document is current, journal unread.

    Established from what the process **did** rather than from how fast it did it. The
    journal is made unreadable to this user, and the row is then read for a figure that
    is only in the document — the node count it carries — which the process could not
    have obtained from a file it cannot open.

    The control is the same root with the document's own stamp moved off the journal
    beside it, which is what sends a reader to fold. It does not fail: it renders a row
    it folded out of what little it could reach, and that row no longer carries the
    figure. Asserting only that the row *appeared* would therefore have passed against a
    listing that folds every time, which is the whole thing this is about.
    """
    _installed()
    owned = Owned(tmp_path / "runs", Session("listing-from-the-document"))
    owned.root.mkdir(parents=True)
    _assembled(owned, "listed")
    _make_current(owned, "listed")
    document = _summary(owned, "listed")
    stored = json.loads(document.read_text(encoding="utf-8"))
    counted = f"/{sum(int(count) for count in stored['node_counts'].values())} done"

    journal = owned.root / "listed" / "events.jsonl"
    journal.chmod(0o000)
    try:
        served = _verb("runs", "--mine", environment=_environment(owned))
        assert served.returncode == 0, f"the listing failed:\n{served.stdout}{served.stderr}"
        assert counted in _row(served.stdout, "listed"), (
            f"the row does not carry {counted!r}, which is the node count this run's own "
            "summary document holds, so this journey is not reading the figure it is "
            f"about:\n{served.stdout}{served.stderr}"
        )

        # The control. Nothing about the run changed and nothing about the journal did;
        # only the document stopped accounting for it, which is what sends a reader to
        # fold — and folding is what this journey says the served path does not do.
        stored["journal_len"] = int(stored["journal_len"]) + 1
        document.write_text(json.dumps(stored), encoding="utf-8")
        folded = _verb("runs", "--mine", environment=_environment(owned))
        assert counted not in _row(folded.stdout, "listed"), (
            "the same listing rendered the same figure with the document no longer "
            "accounting for the journal and the journal unreadable, so that figure was "
            f"not coming from the document after all:\n{folded.stdout}{folded.stderr}"
        )
    finally:
        journal.chmod(0o644)


def test_the_recipe_reaches_the_verb_and_carries_each_status_back(tmp_path: Path) -> None:
    """`just unwatched` is the same read as a command, statuses included.

    Both answers, because the pair is the whole interface: a caller — and the operator
    reading it — has to tell "a run this session owns is unwatched" from "none is", and a
    recipe that collapsed the two would be worth nothing.
    """
    _installed()
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    owned = Owned(tmp_path / "runs", Session("recipe-carries-the-status"))
    owned.root.mkdir(parents=True)
    _assembled(owned, "unwatched-run")
    _make_current(owned, "unwatched-run")

    reported = subprocess.run(  # noqa: S603 - the real recipe, run as an operator runs it
        ["just", "unwatched", "--session", owned.session],
        capture_output=True,
        text=True,
        check=False,
        env=_environment(owned),
        cwd=REPO_ROOT,
        timeout=e2e_timeout(180),
    )
    assert reported.returncode == RUNS_UNWATCHED, (
        f"`just unwatched` exited {reported.returncode} over a root holding one owned, "
        f"unsettled, unwatched run:\n{reported.stdout}{reported.stderr}"
    )
    assert "unwatched-run" in reported.stdout, (
        f"the recipe named no run on standard output:\n{reported.stdout}"
    )

    _settled(owned, "unwatched-run")
    quiet = subprocess.run(  # noqa: S603 - the real recipe, run as an operator runs it
        ["just", "unwatched", "--session", owned.session],
        capture_output=True,
        text=True,
        check=False,
        env=_environment(owned),
        cwd=REPO_ROOT,
        timeout=e2e_timeout(180),
    )
    assert quiet.returncode == NOTHING_UNWATCHED, (
        f"`just unwatched` exited {quiet.returncode} over a root whose one run has "
        f"stopped:\n{quiet.stdout}{quiet.stderr}"
    )
    assert quiet.stdout == "", f"the recipe reported a settled run:\n{quiet.stdout}"


def test_the_hook_blocks_on_the_session_the_payload_names(tmp_path: Path, state_home: Path) -> None:
    """The payload's session is the one asked about, and the environment's is not.

    The two are deliberately different here and only the payload's owns the run, which is
    the arrangement a *manager's* turn ends in the other way round: this settings file is
    tracked, so every dispatched claude-code worker inherits the hook, and every dispatch
    inherits its manager's `ONEPIPELINE_LAUNCHER_SESSION`. A hook that read the
    environment would block every worker's turn on its manager's unwatched runs.
    """
    _installed()
    owned = Owned(tmp_path / "runs", Session("manager-session"))
    owned.root.mkdir(parents=True)
    _assembled(owned, "left-unwatched")
    _make_current(owned, "left-unwatched")

    verb = _unwatched(owned)
    assert verb.returncode == RUNS_UNWATCHED, f"the verb answered {verb.returncode}"

    environment = _at_home(
        _environment(owned, reader=Session("somebody-elses-session")), state_home
    )
    reason = _blocked(_hook(_stop_payload(owned.session), environment))
    assert reason == verb.stdout, (
        "the hook's reason is not the verb's own standard-output lines, which are what "
        f"name the runs to watch:\n reason: {reason!r}\n verb:   {verb.stdout!r}"
    )
    assert "left-unwatched" in reason


def test_the_hook_is_silent_for_a_worker_whose_own_session_owns_nothing(
    tmp_path: Path, state_home: Path
) -> None:
    """The dispatched-worker shape: the manager's session is in the environment, and the
    worker's is on the payload.

    This is the case that decides whether the hook can be checked in at all. The worker's
    own session id owns no run, so asking about it is silence — while the manager's, right
    there in the environment the dispatch inherited, owns an unsettled run nothing is
    watching. A hook that consulted the environment blocks every turn of every worker in
    this repository, which is a defect nobody would attribute to a supervision aid.
    """
    _installed()
    owned = Owned(tmp_path / "runs", Session("the-managers-session"))
    owned.root.mkdir(parents=True)
    _assembled(owned, "the-managers-run")
    _make_current(owned, "the-managers-run")

    manager = _unwatched(owned)
    assert manager.returncode == RUNS_UNWATCHED, (
        "the manager's own session owns nothing unwatched, so this journey is not in the "
        f"state it is about:\n{manager.stdout}{manager.stderr}"
    )

    environment = _at_home(_environment(owned), state_home)
    assert environment["ONEPIPELINE_LAUNCHER_SESSION"] == owned.session
    _silent(
        _hook(_stop_payload(Session("a-dispatched-workers-own-session")), environment),
        "a worker whose own session owns no run",
    )


def test_the_hook_is_silent_when_the_session_owns_no_unwatched_run(
    tmp_path: Path, state_home: Path
) -> None:
    """A stopped run of this session, and a live one of somebody else's: nothing to say.

    Both halves, because they are the two ways "no such run" happens and each would hide
    a different defect — a hook that reported settled runs would block on every run this
    host has ever finished, and one that ignored ownership would block a manager on
    another manager's work.
    """
    _installed()
    owned = Owned(tmp_path / "runs", Session("owns-nothing-unwatched"))
    owned.root.mkdir(parents=True)
    _assembled(owned, "already-stopped")
    _settled(owned, "already-stopped")
    _assembled(Owned(owned.root, Session("another-managers-session")), "somebody-elses")
    _make_current(Owned(owned.root, Session("another-managers-session")), "somebody-elses")

    verb = _unwatched(owned)
    assert verb.returncode == NOTHING_UNWATCHED, (
        f"the verb answered {verb.returncode} where nothing this session owns is "
        f"unwatched:\n{verb.stdout}{verb.stderr}"
    )
    _silent(
        _hook(_stop_payload(owned.session), _at_home(_environment(owned), state_home)),
        "a session owning one stopped run and nothing else",
    )


def test_a_mixed_root_blocks_on_what_was_proven_and_not_on_what_could_not_be_decided(
    tmp_path: Path, state_home: Path
) -> None:
    """Three owned runs, three answers, and only two of them are the hook's business.

    The distinction this holds is the one the whole design turns on. *Unwatched* is what
    an absence of evidence honestly answers, so a run whose watcher record cannot be read
    is reported — a record this build cannot read is not a live watch. *Undecidable* is
    the other thing entirely: a run whose own summary document is gone says nothing about
    whether it settled, and watching it would not clear that, so it is named on standard
    error where it changes no status and blocks nothing. Folding the two would either
    hold a turn open on a question nobody can answer or silence a run nobody is watching.
    """
    _installed()
    owned = Owned(tmp_path / "runs", Session("one-of-each"))
    owned.root.mkdir(parents=True)
    for run in ("plainly-unwatched", "watcher-unreadable", "settlement-undecidable"):
        _assembled(owned, run)
        _make_current(owned, run)
    _unreadable_watcher_record(owned, "watcher-unreadable")
    _summary(owned, "settlement-undecidable").unlink()

    verb = _unwatched(owned)
    assert verb.returncode == RUNS_UNWATCHED, f"the verb answered {verb.returncode}"
    assert "settlement-undecidable" in verb.stderr, (
        "the verb said nothing about the run whose settlement it could not decide, and a "
        f"reader is owed that reason:\n{verb.stderr}"
    )

    reason = _blocked(
        _hook(_stop_payload(owned.session), _at_home(_environment(owned), state_home))
    )
    assert "plainly-unwatched" in reason, (
        f"the proven-unwatched run is not in the reason:\n{reason}"
    )
    assert "watcher-unreadable" in reason, (
        f"a run whose watcher evidence cannot be read is not a watched run:\n{reason}"
    )
    assert "settlement-undecidable" not in reason, (
        "a run whose settlement could not be decided reached the block, so an answer "
        f"nobody could obtain is holding a turn open:\n{reason}"
    )


def test_a_root_holding_only_an_undecidable_run_leaves_the_turn_alone(
    tmp_path: Path, state_home: Path
) -> None:
    """Nothing proven, nothing reported, nothing said: the turn ends.

    The other side of the journey above, and the one that matters on a real host, where
    old run roots whose documents are long gone outnumber live runs. A hook that blocked
    on those would refuse every turn for ever.
    """
    _installed()
    owned = Owned(tmp_path / "runs", Session("nothing-decidable"))
    owned.root.mkdir(parents=True)
    _assembled(owned, "no-document")
    _make_current(owned, "no-document")
    _summary(owned, "no-document").unlink()

    verb = _unwatched(owned)
    assert verb.returncode == NOTHING_UNWATCHED, (
        f"the verb answered {verb.returncode} where it proved nothing:\n{verb.stdout}"
    )
    assert "no-document" in verb.stderr, f"the reason was not reported:\n{verb.stderr}"
    _silent(
        _hook(_stop_payload(owned.session), _at_home(_environment(owned), state_home)),
        "a session owning one run whose settlement cannot be decided",
    )


def test_a_run_recorded_by_an_earlier_build_reads_unwatched_rather_than_undecidable(
    tmp_path: Path,
) -> None:
    """A run started before this landed is unwatched until it is watched again.

    Its stored document declares an earlier build's schema, which says its fields are
    that build's to mean — and that settles nothing about whether the run stopped. So it
    is not the undecidable case above and must not be folded into it: an unread document
    is an answer that could not be obtained, while this one *was* obtained and says the
    run is not proven settled. AGENTS.md states both consequences beside each other for
    exactly this reason, and a build that quietly excused an older document would leave
    every run this host started before the adoption unwatched and unmentioned.

    The document is the engine's own, at its own stamp, with only the version it declares
    moved back — which is what a document an earlier build wrote is, and the one part of
    it this journey has no other way to obtain.
    """
    _installed()
    owned = Owned(tmp_path / "runs", Session("recorded-by-an-earlier-build"))
    owned.root.mkdir(parents=True)
    _assembled(owned, "started-before")
    _settled(owned, "started-before")
    assert _unwatched(owned).returncode == NOTHING_UNWATCHED, (
        "a stopped run whose document this build reads is excluded, which is the control "
        "this journey moves one field away from"
    )

    document = _summary(owned, "started-before")
    stored = json.loads(document.read_text(encoding="utf-8"))
    assert stored["schema_version"] >= 2, (
        "this build writes summary schema_version "
        f"{stored['schema_version']}, so there is no earlier version to stand in for"
    )
    stored["schema_version"] -= 1
    document.write_text(json.dumps(stored), encoding="utf-8")

    verb = _unwatched(owned)
    assert verb.returncode == RUNS_UNWATCHED, (
        "a run whose stored document declares a version this build has moved past was "
        f"not reported, so it was excused rather than read:\n{verb.stdout}{verb.stderr}"
    )
    assert "started-before" in verb.stdout
    assert "started-before" not in verb.stderr, (
        "an older document was reported as evidence that could not be read, where it was "
        f"read and proved nothing:\n{verb.stderr}"
    )


#: A search path holding a shell and no `onepipeline`. The journeys that plant an engine
#: need the planted one to be the only one reachable, and one of them needs *none* to be.
SYSTEM_PATH = "/usr/bin:/bin"

#: An engine that cannot be executed at all. The interpreter it names is not there, so the
#: kernel refuses the exec — which is what a broken install, a half-written wheel, or a
#: binary built for another platform looks like from the calling process.
CANNOT_RUN = "#!/nonexistent/interpreter\n"

#: An engine answering a status that is neither of the verb's two answers. `2` is this
#: binary's own refusal — no session to ask about, a runs root it may not read — and a
#: refusal is not evidence that a run is unwatched.
REFUSES = "#!/usr/bin/env bash\necho 'refused' >&2\nexit 2\n"

#: An engine answering the blocking status and naming no run on standard output. The
#: status says runs are unwatched and the output says which, and an answer carrying only
#: the first is one the hook cannot put in front of a manager: there is nothing to watch.
NAMES_NOTHING = "#!/usr/bin/env bash\nexit 6\n"

#: An engine the hook must pass over rather than ask: planted at the checkout's own
#: `.venv/bin` without an execute bit, it would block if it were ever run — so the line it
#: would print is what a fall-through journey asserts is *absent* from the block's reason.
NOT_EXECUTABLE = (
    "#!/usr/bin/env bash\nprintf 'asked-the-unexecutable  ACTIVE  unwatched\\n'\nexit 6\n"
)


def test_the_hook_ends_the_turn_silently_on_a_working_directory_with_no_runs_root(
    tmp_path: Path, state_home: Path
) -> None:
    """No runs root, no runs, nothing to say — and the real engine is what says it.

    The ordinary state of every checkout on this host that has never launched anything,
    and the one host condition here that needs nothing planted: the verb reads the runs
    root the environment names or `runs` beside the working directory, and a root that is
    not there holds no runs and is not an error.
    """
    _installed()
    elsewhere = tmp_path / "no-runs-here"
    elsewhere.mkdir()
    environment = _at_home(dict(os.environ), state_home)
    environment.pop("ONEPIPELINE_RUNS_DIR", None)
    environment["ONEPIPELINE_LAUNCHER"] = "claude-code"
    environment["ONEPIPELINE_LAUNCHER_SESSION"] = "no-runs-root"
    _silent(
        _hook(_stop_payload(Session("no-runs-root")), environment, cwd=elsewhere),
        "a working directory holding no runs root",
    )


def test_the_hook_ends_the_turn_silently_on_a_payload_it_cannot_read(
    tmp_path: Path, state_home: Path
) -> None:
    """Something that is not the payload this hook understands, and the turn still ends.

    A hook handed something it cannot read knows nothing about whether a run is watched,
    and a diagnostic on a manager's terminal every turn would be noise about the hook
    rather than about the runs.
    """
    _installed()
    owned = Owned(tmp_path / "runs", Session("unreadable-payload"))
    owned.root.mkdir(parents=True)
    _assembled(owned, "would-have-blocked")
    _make_current(owned, "would-have-blocked")
    assert _unwatched(owned).returncode == RUNS_UNWATCHED, (
        "this root holds nothing unwatched, so a silent answer below would prove nothing"
    )

    environment = _at_home(_environment(owned), state_home)
    # The last is a session no launcher could have minted and no argument vector can
    # carry: handed on to the verb it would end the Python half before it asked, which is
    # the wrapper's loud ending rather than this silent one.
    for payload in (
        "",
        "not json at all",
        "[]",
        json.dumps({"session_id": "   "}),
        json.dumps({"session_id": "a-session\u0000with-a-nul"}),
    ):
        _silent(_hook(payload, environment), f"the payload {payload!r}")


@pytest.mark.parametrize(
    ("condition", "engine", "executable", "named"),
    [
        ("an engine that cannot be executed at all", CANNOT_RUN, True, ("could not run",)),
        ("an engine answering a status of its own", REFUSES, True, ("status 2", "refused")),
        (
            "an engine answering the blocking status and naming no run",
            NAMES_NOTHING,
            True,
            ("status 6", "naming no run"),
        ),
        ("no engine to ask", None, True, ("found no `onepipeline`",)),
    ],
    ids=["cannot-run", "another-status", "names-nothing", "missing"],
)
def test_the_hook_says_the_turn_is_unguarded_when_the_engine_does_not_answer(
    condition: str,
    engine: str | None,
    executable: bool,
    named: tuple[str, ...],
    tmp_path: Path,
    state_home: Path,
) -> None:
    """Every way the engine can fail to answer ends the turn, refuses nothing, and says so.

    **The subject is the hook's branching and not the verb's behaviour**, which is why
    the engine here is planted: these are states the real verb has no way to produce.
    The hook itself is the tracked file, copied whole, and what is planted is the sibling
    it resolves.

    None of these is evidence that a run is unwatched, which is the only thing this hook
    ever blocks on, so none of them blocks. But none is evidence that every run is watched
    either, and a guard that fell silent here would go on being believed: the operator
    reads a turn that ended as a turn the hook cleared. So each says so — and says *which*
    condition, which is what `named` holds the warning to, since a warning that could not
    tell a missing engine from a refusing one would send the person to look in the wrong
    place.
    """
    root = _hook_tree(tmp_path, engine, executable=executable)
    environment = _at_home(dict(os.environ), state_home)
    # A search path with a shell on it and no engine, so the planted root is the only
    # place an engine could be found — which is what makes "no engine to ask" reachable
    # at all. Kept as real system directories rather than emptied: `subprocess` resolves
    # the command against the environment it is handed, so an empty one finds no `bash`
    # either and the journey would be about that instead.
    environment["PATH"] = SYSTEM_PATH
    if shutil.which("onepipeline", path=SYSTEM_PATH) is not None:
        pytest.skip(f"an engine is on {SYSTEM_PATH}, so a root without one is not reachable")
    environment["ONEPIPELINE_LAUNCHER_SESSION"] = "engine-that-does-not-answer"
    answered = _planted_hook(
        root, _stop_payload(Session("engine-that-does-not-answer")), environment
    )
    _unguarded(_warned(answered, condition), condition, named)


def _unguarded(said: str, condition: str, named: tuple[str, ...]) -> None:
    """Hold a warning to being the *unguarded* one, about `condition` and not about runs.

    The hook has two warnings and they mean opposite things to the person reading them:
    one names runs that *are* unwatched and asks for a watch on each, the other says the
    question was never asked. A journey about the second that accepted the first would
    pass on a hook that had invented unwatched runs out of a verb that did not answer.
    """
    assert "unguarded" in said, (
        f"{condition}: the warning does not say the turn is unguarded: {said!r}"
    )
    assert "just unwatched" in said, (
        f"{condition}: the warning does not say how to ask by hand: {said!r}"
    )
    assert "are unwatched" not in said, (
        f"{condition}: the warning claims runs are unwatched: {said!r}"
    )
    for phrase in named:
        assert phrase in said, f"{condition}: the warning does not name it ({phrase!r}): {said!r}"


def test_the_wrapper_says_the_turn_is_unguarded_when_it_cannot_reach_its_own_half(
    tmp_path: Path, state_home: Path
) -> None:
    """The three prerequisites the shell half has of its own, and each says so.

    **The subject is the wrapper's branching and not the verb's behaviour**: the engine
    planted beside it would block if it were ever asked, so an unguarded warning is the
    wrapper stopping short of its Python half rather than the verb reporting nothing.

    All three are what a half-restored checkout looks like from a hook's seat — the
    Python half missing, no interpreter to run it with, and an interpreter that ran it
    and got no answer out of it — and none says anything about whether a run is watched.
    The wrapper cannot compose an answer, which is what its Python half is for, but it
    can write a constant one: so each ending is the same `systemMessage` object the
    Python half's unanswered endings write, naming which of the three it was, and none
    blocks. A wrapper that fell silent here would end a turn nobody asked about as though
    the hook had cleared it, which is the ending the person is least able to notice.
    """
    blocks = "#!/usr/bin/env bash\nprintf 'a-run  ACTIVE  unwatched\\n'\nexit 6\n"

    missing_half = _hook_tree(tmp_path / "no-half", blocks, without="stop-unwatched-guard.py")
    environment = _at_home(dict(os.environ), state_home)
    environment["ONEPIPELINE_LAUNCHER_SESSION"] = "cannot-reach-its-own-half"
    condition = "a checkout whose Python half is not there"
    _unguarded(
        _warned(
            _planted_hook(
                missing_half, _stop_payload(Session("cannot-reach-its-own-half")), environment
            ),
            condition,
        ),
        condition,
        ("no scripts/stop-unwatched-guard.py",),
    )

    # And with the half present but nothing to run it with. The tree carries no
    # `.venv/bin/python3` of its own, so a search path holding a shell and nothing else is
    # the whole of what makes an interpreter unreachable — a shell, because `subprocess`
    # resolves the command against the environment it is handed and an empty path would
    # find no `bash` to run the hook with either.
    no_interpreter = _hook_tree(tmp_path / "no-python", blocks)
    shell_only = tmp_path / "a-shell-and-nothing-else"
    shell_only.mkdir()
    bash = shutil.which("bash")
    assert bash is not None, "this host has no bash, so the hook cannot be run at all"
    (shell_only / "bash").symlink_to(bash)
    without_python = dict(environment)
    without_python["PATH"] = str(shell_only)
    assert shutil.which("python3", path=without_python["PATH"]) is None
    condition = "a checkout with no interpreter to run that half with"
    _unguarded(
        _warned(
            _planted_hook(
                no_interpreter, _stop_payload(Session("cannot-reach-its-own-half")), without_python
            ),
            condition,
        ),
        condition,
        ("no python3",),
    )

    # And with an interpreter that runs the half and gets nothing out of it. The Python
    # half exits 0 on every path it controls, so the only way to a status it did not
    # choose is an interpreter that cannot run it — a `python3` too old for its syntax is
    # the live form — and that is provoked with a stand-in at the path the wrapper
    # prefers, `.venv/bin/python3`, which answers nothing and exits 1 as such an
    # interpreter would. A stand-in rather than the real thing because no interpreter on
    # this host fails to run the half, and the subject is the wrapper's own arm.
    half_ran_silent = _hook_tree(tmp_path / "half-answers-nothing", blocks)
    interpreter = half_ran_silent / ".venv" / "bin" / "python3"
    interpreter.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    interpreter.chmod(0o755)
    condition = "a checkout whose Python half ran and answered nothing"
    _unguarded(
        _warned(
            _planted_hook(
                half_ran_silent, _stop_payload(Session("cannot-reach-its-own-half")), environment
            ),
            condition,
        ),
        condition,
        ("status other than 0",),
    )

    # And with an interpreter whose own launch fails, which is the form of that ending
    # that comes with a complaint: a `.venv/bin/python3` whose shebang names a program
    # that is not there is executable, is chosen, and is refused by the kernel — bash
    # reports `bad interpreter` on standard error and exits 126. The contract gives the
    # harness nothing on that stream, so the wrapper owns the complaint as well as the
    # status: the same constant warning, and standard error still empty, which
    # `_warned` holds. A stand-in interpreter for the reason the one above is.
    interpreter_gone = _hook_tree(tmp_path / "interpreter-gone", blocks)
    launcher = interpreter_gone / ".venv" / "bin" / "python3"
    launcher.write_text("#!/nonexistent/interpreter\n", encoding="utf-8")
    launcher.chmod(0o755)
    condition = "a checkout whose interpreter cannot be launched"
    _unguarded(
        _warned(
            _planted_hook(
                interpreter_gone, _stop_payload(Session("cannot-reach-its-own-half")), environment
            ),
            condition,
        ),
        condition,
        ("status other than 0",),
    )


def test_a_memory_this_hook_cannot_read_stands_aside_rather_than_blocking_again(
    tmp_path: Path, state_home: Path
) -> None:
    """A continuation whose record cannot be read is a question this hook cannot answer.

    Whether the condition moved is exactly what the record is for, and without it the
    hook has an unwatched run in hand and no way to say whether the manager was already
    told. Blocking there would be blocking on an answer it could not obtain — and that
    block is the irreversible act, since it holds the operator's own session. So it says
    so and steps out of the way: the runs, the reason, and no refusal.
    """
    _installed()
    owned = Owned(tmp_path / "runs", Session("memory-that-cannot-be-read"))
    owned.root.mkdir(parents=True)
    _assembled(owned, "still-unwatched")
    _make_current(owned, "still-unwatched")
    environment = _at_home(_environment(owned), state_home)

    _blocked(_hook(_stop_payload(owned.session), environment))
    remembered = [path for path in state_home.rglob("*") if path.is_file()]
    assert len(remembered) == 1, f"the block remembered {remembered}"
    remembered[0].chmod(0o000)
    try:
        said = _warned(
            _hook(_stop_payload(owned.session, again=True), environment),
            "a continuation whose record cannot be read",
        )
        assert "still-unwatched" in said, f"the runs were not named:\n{said}"
        assert "could not read" in said, f"the reason was not named:\n{said}"
    finally:
        remembered[0].chmod(0o644)


def test_a_memory_this_hook_cannot_decode_stands_aside_rather_than_crashing(
    tmp_path: Path, state_home: Path
) -> None:
    """A record the hook can open but cannot decode is the same unanswerable question.

    Whether the condition moved is read out of that record, and a file holding bytes that
    are not UTF-8 answers that no better than one this user may not open — the hook is
    left with an unwatched run in hand and no way to say whether the manager was already
    told. Decoding is a different failure from opening, and a hook that caught only the
    second crashed on the first: a traceback on standard error, a non-zero exit, and a
    harness reading neither as the warning it owes. So it steps aside here exactly as
    it does for a record it may not read: the runs, the reason, and no refusal.
    """
    _installed()
    owned = Owned(tmp_path / "runs", Session("memory-that-cannot-be-decoded"))
    owned.root.mkdir(parents=True)
    _assembled(owned, "still-unwatched")
    _make_current(owned, "still-unwatched")
    environment = _at_home(_environment(owned), state_home)

    _blocked(_hook(_stop_payload(owned.session), environment))
    remembered = [path for path in state_home.rglob("*") if path.is_file()]
    assert len(remembered) == 1, f"the block remembered {remembered}"
    remembered[0].write_bytes(b"\xff\xfe not a digest, and not UTF-8\n")
    said = _warned(
        _hook(_stop_payload(owned.session, again=True), environment),
        "a continuation whose record cannot be decoded",
    )
    assert "still-unwatched" in said, f"the runs were not named:\n{said}"
    assert "could not read" in said, f"the reason was not named:\n{said}"


def test_a_memory_this_hook_cannot_write_stands_aside_rather_than_blocking_for_ever(
    tmp_path: Path, state_home: Path
) -> None:
    """A block this hook cannot write down is one it must not make.

    The record is what makes a block safe: without it the next continuation cannot tell
    an unchanged condition from a moved one and blocks again, and again after that, on
    an answer that never moves — a session held open by this hook, which is the one
    thing it may never do. So a home it may not write is a reason to step aside rather
    than a courtesy it can do without. The unwatched run is still put in front of the
    person, because silence would swallow the one positive answer the hook has.

    The direction matters and is the opposite of the watcher record's: every unknown
    there resolves toward *reporting* a run, because being wrong costs one re-armed
    watch, while every unknown here resolves toward *not blocking*, because being wrong
    costs the operator's own session.
    """
    _installed()
    owned = Owned(tmp_path / "runs", Session("memory-that-cannot-be-written"))
    owned.root.mkdir(parents=True)
    _assembled(owned, "unwatched-either-way")
    _make_current(owned, "unwatched-either-way")
    environment = _at_home(_environment(owned), state_home)

    read_only = state_home / ".local" / "state"
    read_only.chmod(0o500)
    try:
        said = _warned(
            _hook(_stop_payload(owned.session), environment),
            "a home the hook may not write",
        )
        assert "unwatched-either-way" in said, f"the runs were not named:\n{said}"
        assert "could not record" in said, f"the reason was not named:\n{said}"
        assert not [path for path in read_only.rglob("*")], (
            "the hook wrote into a directory it was refused, so this journey is not the "
            "one it says it is"
        )
        # And the same on a continuation, which is the loop this exists to prevent: a
        # hook that blocked here would go on blocking with nothing to compare against.
        _warned(
            _hook(_stop_payload(owned.session, again=True), environment),
            "a continuation with a home the hook may not write",
        )
    finally:
        read_only.chmod(0o700)


def test_a_memory_this_hook_cannot_drop_neither_makes_a_clear_turn_loud_nor_silences_the_next_block(
    tmp_path: Path, state_home: Path
) -> None:
    """A record the hook may not remove after a clear turn, and nothing changes.

    A block leaves a record; a later stop with nothing to block on drops it, and the drop
    can be refused — the directory the record sits in is one the hook may not write. That
    is the same refusal the journey above steps aside on, at the opposite end of a block,
    and it costs nothing: the record is *for* telling a continuation whether the condition
    moved, and a turn that ended clear leaves no block for the next one to continue. So
    the clear turn stays exactly as clear — nothing on either stream — and the stale record
    silences nothing afterwards, because an ordinary stop never consults it.

    **The second half is only evidence if the stale record matches what the next block
    reports byte for byte.** The defect it excludes is a hook that consults the record on
    an ordinary stop and goes quiet when the digest matches; a second block reported
    under different lines would be blocked by that hook too, and the journey would pass
    over the very defect it names. So the run is never settled: the clear turn comes from
    asking about a runs root that holds nothing, and the block that follows is over the
    same run, reported under the same lines the record was written from.
    """
    _installed()
    owned = Owned(tmp_path / "runs", Session("memory-that-cannot-be-dropped"))
    owned.root.mkdir(parents=True)
    _assembled(owned, "cleared-later")
    _make_current(owned, "cleared-later")
    environment = _at_home(_environment(owned), state_home)
    first = _blocked(_hook(_stop_payload(owned.session), environment))
    remembered = [path for path in state_home.rglob("*") if path.is_file()]
    assert len(remembered) == 1, f"the block remembered {remembered}"
    record = remembered[0].read_bytes()

    # A clear turn: the same session asked about a runs root holding no run of anybody's,
    # which the real verb answers `0`. The run itself is left exactly as it is.
    nothing_here = tmp_path / "runs-holding-nothing"
    nothing_here.mkdir()
    elsewhere = dict(environment)
    elsewhere["ONEPIPELINE_RUNS_DIR"] = str(nothing_here)
    read_only = remembered[0].parent
    read_only.chmod(0o500)
    try:
        _silent(
            _hook(_stop_payload(owned.session), elsewhere),
            "a clear turn whose stale record the hook may not remove",
        )
        assert remembered[0].read_bytes() == record, (
            "the record was changed in a directory the hook may not write, so this "
            "journey is not the one it says it is"
        )
    finally:
        read_only.chmod(0o700)

    # The stale record is still standing, and the next stop is an ordinary one over the
    # same run — reported under the very bytes the record was written from, so a hook
    # that consulted the record here would find a match and go quiet. It must block.
    again = _blocked(_hook(_stop_payload(owned.session), environment))
    assert again == first, (
        "the second block was reported under different lines from the first, so a hook "
        f"wrongly consulting the record would not have matched:\n{first}\n---\n{again}"
    )
    assert "cleared-later" in again, f"the run was not named:\n{again}"


@pytest.mark.parametrize(
    ("condition", "own"),
    [
        ("no engine of its own", None),
        ("an engine of its own it cannot execute", NOT_EXECUTABLE),
    ],
    ids=["absent", "not-executable"],
)
def test_the_hook_falls_through_to_the_engine_on_the_search_path(
    condition: str, own: str | None, tmp_path: Path, state_home: Path
) -> None:
    """A checkout whose own engine cannot be asked asks whichever one is on the search path.

    **The subject is the hook's resolution and not the verb's behaviour**: what is read
    is *which* engine the hook asked, which only a planted one can say.

    The fallback that makes this hook work in a checkout provisioned some other way, and
    the half of `_binary` no other journey reaches: every one of them runs in a tree whose
    own `.venv/bin` answers first, so without this the fall-through is a production path
    nothing has ever taken. It has two entrances and both are driven — a `.venv/bin` with
    no engine in it, and one holding a file the hook may not execute, which is what a
    half-provisioned checkout leaves and which must be passed over rather than asked and
    refused by the kernel.
    """
    root = _hook_tree(tmp_path, own, executable=own is None)
    on_the_path = tmp_path / "somewhere-else"
    on_the_path.mkdir()
    planted = on_the_path / "onepipeline"
    planted.write_text(
        "#!/usr/bin/env bash\nprintf 'found-on-the-path  ACTIVE  unwatched\\n'\nexit 6\n",
        encoding="utf-8",
    )
    planted.chmod(0o755)
    environment = _at_home(dict(os.environ), state_home)
    environment["PATH"] = f"{on_the_path}{os.pathsep}{SYSTEM_PATH}"
    environment["ONEPIPELINE_LAUNCHER_SESSION"] = "engine-on-the-path"
    reason = _blocked(
        _planted_hook(root, _stop_payload(Session("engine-on-the-path")), environment)
    )
    assert "found-on-the-path" in reason, (
        f"{condition}: the hook did not ask the engine on the search path:\n{reason}"
    )
    assert "asked-the-unexecutable" not in reason, (
        f"{condition}: the hook asked the engine it may not execute:\n{reason}"
    )


@pytest.mark.parametrize("named", ["", "a/relative/state/root"], ids=["absent", "relative"])
def test_a_state_root_that_is_not_an_absolute_path_falls_back_to_the_home(
    named: str, tmp_path: Path, state_home: Path
) -> None:
    """What the hook remembers lands under the home when the environment names no root.

    Both halves of the same rule, and the relative one is the half that matters: this hook
    runs in whatever working directory the harness gives it, and a relative state root
    would put its memory under that — which is the shape the *runs root* itself defaults
    to, so the memory would land beside the runs of whichever checkout the turn ended in.
    """
    _installed()
    owned = Owned(tmp_path / "runs", Session("state-root-that-is-not-absolute"))
    owned.root.mkdir(parents=True)
    _assembled(owned, "unwatched-run")
    _make_current(owned, "unwatched-run")
    environment = _at_home(_environment(owned), state_home)
    if named:
        environment["XDG_STATE_HOME"] = named
    else:
        environment.pop("XDG_STATE_HOME")

    _blocked(_hook(_stop_payload(owned.session), environment, cwd=tmp_path))

    under_the_home = [
        path for path in (state_home / ".local" / "state").rglob("*") if path.is_file()
    ]
    assert under_the_home, (
        f"the hook remembered nothing under {state_home}, so a state root it was given no "
        f"absolute path for did not fall back to the home. It left "
        f"{sorted(str(path) for path in tmp_path.rglob('*') if path.is_file())[:20]}"
    )
    if named:
        assert not (tmp_path / named).exists(), (
            f"the hook resolved the relative {named!r} against its working directory, "
            "which is where a run's own records live when nothing names a runs root"
        )


def test_the_hook_says_the_turn_is_unguarded_when_the_engine_runs_past_its_bound(
    tmp_path: Path, state_home: Path
) -> None:
    """A verb that never answers must not hold the turn open, and does not — and says so.

    **The subject is the hook's branching and not the verb's behaviour**: no state the
    real verb can be put in makes it run past this bound, so what is planted is one that
    does, and what is read is what the hook does about it.

    The bound is why this hook may be registered at all: it runs at the end of every
    manager turn, so a wedged read would make ending a turn a thing that could simply
    stop happening. What it costs when it fires is one turn's worth of not knowing,
    which is the same thing every other unanswered condition above costs — and, as with
    each of those, the person is told rather than left to assume the turn was guarded.
    """
    bound = _bound()
    root = _hook_tree(tmp_path, f"#!/usr/bin/env bash\nsleep {bound * 10}\n")
    environment = _at_home(dict(os.environ), state_home)
    environment["ONEPIPELINE_LAUNCHER_SESSION"] = "past-the-bound"
    began = time.monotonic()
    answered = _planted_hook(
        root, _stop_payload(Session("past-the-bound")), environment, seconds=bound * 8
    )
    took = time.monotonic() - began
    condition = "an engine that runs past the hook's bound"
    _unguarded(_warned(answered, condition), condition, (f"{bound:g}s bound",))
    assert took < bound * 4, (
        f"the hook took {took:.1f}s against its own {bound}s bound, so it is not the "
        "bound that ended it"
    )


#: The hook whose own declarations these journeys are driven by, so that no value it
#: owns is spelled twice.
GUARD = REPO_ROOT / "scripts" / "stop-unwatched-guard.py"


def _declared(name: str) -> str:
    """One constant the hook declares, as it declares it.

    Read out of its source rather than imported, because the hook is a script the harness
    runs by path and not a module this suite installs — and a copy of any value it owns is
    the one thing a journey about that value must not have.
    """
    for line in GUARD.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name} ="):
            return str(ast.literal_eval(line.split("=", 1)[1].strip()))
    raise AssertionError(f"{GUARD} declares no {name} for this journey to read")


def _bound() -> float:
    """The bound the hook gives the verb, read out of the hook rather than restated.

    A second copy of that number here would be a second statement of it, and the journey
    above would go on passing against a bound that had moved.
    """
    return float(_declared("VERB_TIMEOUT_SECONDS"))


def test_the_hook_does_not_wait_on_this_checkouts_project_environment_lock(
    tmp_path: Path, state_home: Path
) -> None:
    """The lock every `just` view here waits on is held, and the hook answers anyway.

    `uv` takes an **exclusive** lock on this checkout's project environment, and every
    `just` recipe in this suite reaches its tool through `uv run`. AGENTS.md's four-worker
    invariant carries the measurement: with that lock held twelve seconds, `uv run
    onepipeline --version` took 10.87s against 0.001s for the installed binary run
    directly. A hook at the end of every turn that waited on it would make the length of
    a turn a property of what else the host is doing, so it reaches the binary the way
    `just plans` reaches its own CLI instead.
    """
    _installed()
    if shutil.which("flock") is None:
        pytest.skip("flock is not installed, so this checkout's uv lock cannot be held")
    owned = Owned(tmp_path / "runs", Session("under-the-lock"))
    owned.root.mkdir(parents=True)
    _assembled(owned, "held-open")
    _make_current(owned, "held-open")
    environment = _at_home(_environment(owned), state_home)
    bound = _bound()

    lock = REPO_ROOT / ".venv" / ".lock"
    lock.touch(exist_ok=True)
    holder = subprocess.Popen(  # noqa: S603 - flock over this checkout's own project environment
        ["flock", "-x", str(lock), "sleep", str(bound * 3)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        began = time.monotonic()
        answered = _hook(_stop_payload(owned.session), environment, seconds=bound * 2)
        took = time.monotonic() - began
        assert "held-open" in _blocked(answered)
        assert took < bound, (
            f"the hook took {took:.1f}s with this checkout's project-environment lock "
            f"held, against its own {bound}s bound: it is waiting on that lock"
        )
    finally:
        holder.kill()
        holder.wait(timeout=e2e_timeout(30))


def test_the_guard_ends_a_continuation_the_condition_has_not_moved_under(
    tmp_path: Path, state_home: Path
) -> None:
    """A block, then its continuation: silent while nothing changed, blocking once it has.

    `stop_hook_active` says this stop follows a block of this hook's own, and the guard on
    it is a guard on the **condition** rather than on a count. Three answers in sequence,
    because each is a different failure if it goes the other way. The same runs again is a
    manager who did nothing, and blocking there would hold the session open on a condition
    nobody can end from inside the loop. Nothing at all is a manager who armed the watch,
    and blocking there would punish exactly the response this hook asks for. Different
    runs is a condition that moved — one re-watched and another not, or one that went
    unwatched since — and *silence* there is the failure that matters most: the model has
    not seen where the condition moved to, which is the whole thing this hook exists to
    tell it.
    """
    _installed()
    owned = Owned(tmp_path / "runs", Session("guarded-across-a-block"))
    owned.root.mkdir(parents=True)
    _assembled(owned, "first-run")
    _make_current(owned, "first-run")
    environment = _at_home(_environment(owned), state_home)

    first = _blocked(_hook(_stop_payload(owned.session), environment))
    assert "first-run" in first

    again = _hook(_stop_payload(owned.session, again=True), environment)
    _silent(again, "a continuation whose verb reports exactly what the block reported")

    _assembled(owned, "second-run")
    _make_current(owned, "second-run")
    moved = _blocked(_hook(_stop_payload(owned.session, again=True), environment))
    assert "second-run" in moved, (
        "a continuation whose verb now reports a run the block never named ended the turn "
        f"silently, so the model never learns where the condition moved to:\n{moved}"
    )

    _settled(owned, "first-run")
    _settled(owned, "second-run")
    _silent(
        _hook(_stop_payload(owned.session, again=True), environment),
        "a continuation whose verb reports nothing at all",
    )


def test_each_session_is_guarded_on_its_own(tmp_path: Path, state_home: Path) -> None:
    """Two sessions reporting the same words, and one's block does not answer the other's.

    The lines really are identical rather than merely alike: each session owns a runs root
    of its own holding a run of the same name, so what the verb writes for the two is the
    same text down to the byte. That is the one arrangement in which a memory keyed on
    anything but the session would silence a manager who has never been told.
    """
    _installed()
    sessions = []
    for name in ("first-manager", "second-manager"):
        owned = Owned(tmp_path / name / "runs", Session(name))
        owned.root.mkdir(parents=True)
        _assembled(owned, "same-run-name")
        _make_current(owned, "same-run-name")
        sessions.append(owned)
    first, second = sessions

    said = [_unwatched(owned).stdout for owned in sessions]
    assert said[0] == said[1] != "", (
        f"the two sessions do not report identical lines, so this journey is not the "
        f"arrangement it is about:\n{said[0]!r}\n{said[1]!r}"
    )

    _blocked(_hook(_stop_payload(first.session), _at_home(_environment(first), state_home)))
    reason = _blocked(
        _hook(_stop_payload(second.session, again=True), _at_home(_environment(second), state_home))
    )
    assert reason == said[1], (
        "a continuation of the second manager's session was answered against the first "
        "manager's block, so the memory is keyed on the words rather than on the session"
    )


def test_what_the_hook_remembers_is_outside_the_tracked_tree_and_the_runs_root(
    tmp_path: Path, state_home: Path
) -> None:
    """Wherever the memory lives, it is neither this repository nor the run's own record.

    Two places it must not be, for two reasons. The tracked tree is a repository, and a
    file written at the end of every turn does not belong in one — it would arrive in
    every diff and in every dispatch's worktree. The runs root is the run's own record of
    itself, and this is a fact about a *conversation*: a manager's turn is not something a
    run should carry, and every view over that root reads what is in it.
    """
    _installed()
    owned = Owned(tmp_path / "runs", Session("where-the-memory-lives"))
    owned.root.mkdir(parents=True)
    _assembled(owned, "blocked-on")
    _make_current(owned, "blocked-on")
    before_root = {path for path in owned.root.rglob("*")}
    before_tree = _working_tree()

    _blocked(_hook(_stop_payload(owned.session), _at_home(_environment(owned), state_home)))

    remembered = [path for path in state_home.rglob("*") if path.is_file()]
    assert remembered, (
        f"the hook blocked and remembered nothing under {state_home}, so the guard on a "
        "continuation has nothing to compare against"
    )
    assert {path for path in owned.root.rglob("*")} == before_root, (
        "the hook wrote into the runs root, which is the run's own record of itself and "
        f"not a place for a fact about a conversation: {sorted(owned.root.rglob('*'))}"
    )
    # Compared against what the tree said a moment ago rather than against an empty
    # answer: this journey runs inside a checkout that is *being worked in*, so what says
    # the hook wrote nothing is that nothing moved across it.
    assert _working_tree() == before_tree, (
        "running the hook changed this checkout's working tree, so what it remembers is "
        "in a repository:\n" + "\n".join(sorted(set(_working_tree()) ^ set(before_tree)))
    )


#: The standings a *dead* watcher leaves, in the engine's own words on a reported line.
#: Two of them, because which one a host gives depends on whether anything has reaped the
#: corpse yet — a question this journey deliberately does not settle, since both answer
#: the only thing it is about. Distinguishing them is `onepipeline`'s own suite's.
DEAD = ("its process is gone", "its process ended and is waiting to be reaped")


def _written_just_now(owned: Owned, run: str) -> None:
    """Date this run's journal to now, so a live pid reads as a run being driven.

    A live pid is ownership rather than progress: the engine reports a launch that has
    not written for half an hour as parked however alive its process is, and a parked run
    is one a watch returns from immediately. A run something is really driving has just
    written, so that is what this makes true of it — the records are the builder's own,
    stamped now instead of at a date chosen to be safely in the past.
    """
    journal = owned.root / run / "events.jsonl"
    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + ".000Z"
    rewritten = []
    for line in journal.read_text(encoding="utf-8").splitlines():
        envelope = json.loads(line)
        envelope["ts"] = now
        rewritten.append(json.dumps(envelope))
    journal.write_text("".join(f"{line}\n" for line in rewritten), encoding="utf-8")


def _watcher_pids(owned: Owned, run: str) -> list[int]:
    directory = owned.root / run / "watchers"
    if not directory.is_dir():
        return []
    pids = []
    for record in sorted(directory.iterdir()):
        try:
            pids.append(int(json.loads(record.read_text(encoding="utf-8"))["pid"]))
        except (OSError, ValueError, KeyError):
            continue
    return pids


def _until(what: str, ready: Callable[[], bool], *, seconds: float = 120) -> None:
    """Poll a condition to a bound, saying what was awaited when it does not arrive."""
    deadline = time.monotonic() + e2e_timeout(seconds)
    while time.monotonic() < deadline:
        if ready():
            return
        time.sleep(0.5)
    raise AssertionError(f"waited for {what} and it did not happen")


def test_the_watch_recipe_arms_a_watch_the_verb_sees_and_its_death_undoes(
    tmp_path: Path,
) -> None:
    """`just watch` is what makes a run watched, and nothing but the watch's life keeps it so.

    This is the property the whole hook rests on, and the half no amount of reading the
    verb can establish: the recipe AGENTS.md's watch rule names has to write the evidence
    the verb decides from, and it has to stop being evidence the moment the watch dies.
    There is no heartbeat, no expiry and no cleanup step between a watcher dying and its
    run reading unwatched — which is deliberate, because the deaths that matter have no
    clean exit, and it is why the middle of this journey is not enough on its own.

    The watch is killed and this journey reaps nothing, so which death the host reports —
    a process gone, or one still waiting for a parent that will never come — is not
    something asserted here: both are the same answer to the only question being asked,
    and telling them apart is `onepipeline`'s own suite's job.

    The run is driven by a `sleep` this journey starts, named in the launch record with
    the start time this host reports for it. A watch returns immediately on a run nothing
    is driving, so a run that is not driven is a run no watch can be armed on.
    """
    _installed()
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    owned = Owned(tmp_path / "runs", Session("watched-and-then-not"))
    owned.root.mkdir(parents=True)
    driver = subprocess.Popen(  # noqa: S603 - this journey's own process, standing in for a driver
        ["sleep", "600"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL
    )
    watch: subprocess.Popen[bytes] | None = None
    streamed = tmp_path / "watch.log"
    try:
        _assembled(owned, "the-run", driver_pid=driver.pid)
        _written_just_now(owned, "the-run")
        _make_current(owned, "the-run")
        assert _unwatched(owned).returncode == RUNS_UNWATCHED, (
            "the run reads as watched before any watch was armed, so nothing below "
            "measures the recipe"
        )

        with streamed.open("wb") as sink:
            watch = subprocess.Popen(  # noqa: S603 - the real recipe, run as a supervisor runs it
                ["just", "watch", "the-run", "--timeout", "none"],
                cwd=REPO_ROOT,
                env=_environment(owned),
                stdin=subprocess.DEVNULL,
                stdout=sink,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            _until(
                "the armed watch to make the run read as watched",
                lambda: _unwatched(owned).returncode == NOTHING_UNWATCHED,
                seconds=300,
            )
            armed = _watcher_pids(owned, "the-run")
            assert armed, "nothing recorded a watch, so the silence above was not the watch"

            os.killpg(os.getpgid(watch.pid), signal.SIGKILL)

        gone = None

        def _reports_it_again() -> bool:
            nonlocal gone
            gone = _unwatched(owned)
            return gone.returncode == RUNS_UNWATCHED

        _until("the killed watch to stop counting as one", _reports_it_again, seconds=300)
        assert gone is not None
        assert any(phrase in gone.stdout for phrase in DEAD), (
            "the run reads unwatched again, but not because the watch this journey "
            f"killed is reported dead:\n{gone.stdout}"
        )
        assert any(f"pid {pid}:" in gone.stdout for pid in armed), (
            f"the reported line names none of the watches that were armed {armed}:\n{gone.stdout}"
        )
    finally:
        if watch is not None:
            watch.wait(timeout=e2e_timeout(60))
        driver.kill()
        driver.wait(timeout=e2e_timeout(30))
        if streamed.is_file() and not streamed.stat().st_size:
            streamed.unlink()
