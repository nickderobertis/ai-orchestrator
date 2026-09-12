"""The watch surface this repository restates is the one the installed engine offers.

`scripts/watch-run.sh` restates two things about somebody else's interface: the options
it passes to the engine's blocking watch verb, and the exit statuses it branches on to
say which of the four terminal conditions happened. A copy drifts in silence, and this
one drifts in the direction that hurts most — an option the engine no longer takes is a
watch refused before it watches anything, and a status whose meaning moved is a watch
reporting the wrong ending while looking healthy.

Neither side of the reconciliation is a table kept here: the wrapper's own
`--print-surface` is the one source of what this repository passes, and
`tests/published_surface.py` resolves what the engine has by asking it.

The two checks that ask the engine anything are in the uncached tier, because their
subject is an installed producer rather than this workspace: a memo keyed on this tree
would replay a green across the very upgrade they exist to catch. The checks that drive
the comparison itself read nothing outside this workspace and stay in the memoized tier.

**The exit statuses are reconciled by driving the verb, not by reading its help.** This
module used to parse an `Exit status:` table out of `onepipeline watch --help`, written
speculatively while no engine offered the verb; the engine that does documents its
statuses in its own repository and puts none of that in `--help`, so that read could only
ever have failed. What the installed verb *does* carry is the pairing itself: its final
NDJSON record is `{"watch":"return", …, "condition": …, "exit": …}`, which is the engine
naming a condition and the status it is returning for it in one place. So each terminal
condition this host can drive the verb into is driven, and the pair it answers with is
compared against this repository's table for equality.

**The `--until` vocabulary is reconciled by handing every value to the parser**, which
is the only place it can be: the verb declares the conditions in its own source and puts
none of them in `--help`, so a read of the help would reconcile this repository's list
against nothing. Each condition the wrapper offers is therefore given to the installed
verb over a recorded run, and the refusal an unrecognised one gets — the verb naming the
vocabulary it does have — is what this fails on. The one value that is a *shape* rather
than a word, `node=<ID>`, is driven with a node the recorded run's own graph holds, and
beside it the two refusals a selector makes about a run are driven as well: a node the
graph does not hold, and — for the wait that has no bound — that the verb takes the
spelling this repository puts in front of an operator.

**Three of the terminal conditions cannot be produced against a static runs root, and
they are declared rather than skipped.** `surface-waiting`, `elapsed` and `node-settled`
are all answers about a run that is *live* — one with a blocking surface waiting, one
still being driven when the wait runs out, one with a node settling while another holds
the graph incomplete — and the engine proves liveness from the driving process itself
rather than from the ledger, which is why a run root with a forged lock still answers
`nothing-driving`. Forging a driver to reach them would be a fixture asserting this
repository's own guess at what the engine inspects. They are named in
`UNDRIVABLE_CONDITIONS` with that reason and the declaration is asserted to be exactly
those three.

What *does* drive them is a run rather than a fixture:
`tests/e2e/test_watch_selector_e2e.py` launches one, holds it live, and takes all three
against the installed engine — which is where they belong, since a static runs root is
this module's subject and a launch is not. The wrapper's own branching on every ending is
held beside that by `tests/e2e/test_watch_recipe_e2e.py`, which doubles the verb because
no single run can be made to produce them all on demand.

llmlint: ignore-file[test_tiers_split_by_project_not_by_marker,shell_test_tiers_stay_split] The
marker is this repository's tier mechanism rather than a shortcut around one: it runs
four tiers over one Nx project, keyed on four `nx.json` named inputs, and
`reads_checkouts` selects the *uncached* target `orchestrator:test-checkouts` — the tier
that exists precisely because no key over this workspace can describe state outside it,
which is what an installed engine is. The rule's remedy, its own project so `nx affected`
can skip it, is the opposite of what this tier needs: a second project would need its own
key over the same nothing. `just check` runs that target in its uniform set, and
`tests/test_nx_cache_scope.py` holds the four selectors to a partition of the suite, so a
marker that stopped routing is a failing check rather than a test nothing runs.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import NamedTuple

import pytest
from published_surface import surface_of

from orchestrator.root import REPO_ROOT

#: The wrapper whose restatement is under test, and the verb it restates.
WRAPPER = REPO_ROOT / "scripts" / "watch-run.sh"
WATCH = ("watch",)
#: The engine this host installed, read from this checkout's own environment rather
#: than from PATH: a sibling checkout's copy is a different release and would be
#: reconciled against the wrong surface.
ENGINE = REPO_ROOT / ".venv" / "bin" / "onepipeline"
#: The recorded runs this drives the verb over. Checked in, so what the engine is asked
#: is fixed by this tree rather than by whatever this host happens to have run.
RECORDED_RUNS = REPO_ROOT / "tests" / "fixtures" / "timeline-runs"
#: How long the verb is given. `0` reads the store once and returns, which is what makes
#: a terminal condition observable off a run that is not going to change again.
READ_ONCE = "0"

#: The recorded run whose graph holds several nodes, one of which settled — so a
#: condition naming that node is answered rather than refused, and a condition naming
#: one the graph does not hold is refused for exactly the reason under test. Its settled
#: node settled `failed`, which matters: the verb refuses a wait for a node that settled
#: `done` behind the cursor, because nothing dispatches such a node again.
NAMED_NODE_RUN = "triage-by-root-cause-2"
#: The node of that run this repository names when it drives the shape. Read back out of
#: the recorded run's own plan below rather than trusted, so a fixture edited under this
#: module fails here rather than turning the reconciliation into an assertion about a
#: node nothing holds.
NAMED_NODE = "basis"
#: A node no recorded run holds, for the refusal that names the ids the graph does hold.
ABSENT_NODE = "no-such-node-in-this-graph"
#: What the shape in the wrapper's own vocabulary stands in for. Substituted with a node
#: the recorded run holds before the shape is driven, so what is reconciled stays the
#: spelling rather than whether a particular id exists.
NODE_ID_PLACEHOLDER = "<ID>"
#: A recorded run that settled, for the wait with no bound: it returns on the first pass
#: whatever the wait says, which is what makes an unbounded spelling drivable at all.
SETTLED_RUN = "gate-parity-2"


#: What tells an accepted value from a refused one, without reading either's words. A
#: condition or a wait the verb takes leaves it reading the run and returning a terminal
#: record; one it refuses is answered at the command line and writes none. That is the
#: comparison rather than a phrase, because the two refusals this drives have been
#: worded two different ways by two releases of the same verb — clap's `invalid value
#: '…' for '--until <CONDITION>'` and the parser's own `'…' is not a condition this verb
#: returns on` — and a check keyed on either sentence passes vacuously against a build
#: that writes the other. Which is not hypothetical: keyed on the second, this module
#: passed against an engine that refused every one of these.
def _took_it(answered: subprocess.CompletedProcess[str]) -> bool:
    """Whether the verb accepted this command line and went on to read the run."""
    return terminal_record(answered.stdout) is not None


class Terminal(NamedTuple):
    """One ending the verb reported, as the verb's own record spells it."""

    #: The engine's own word for the condition.
    condition: str
    #: The status it returned for it.
    exit: int


#: The conditions no static runs root can produce, and why. Two of them are answers about
#: a run that is **live** — one with a blocking surface waiting to be answered, one still
#: being driven when the wait runs out — and the engine proves a run is being driven from
#: the driving process rather than from anything in the ledger: a run root carrying a
#: forged `owner.lock` naming a live process still answers `nothing-driving`. A fixture
#: that got past that would be asserting this repository's own guess at what the engine
#: inspects, which is the opposite of a reconciliation.
#:
#: **The third is undrivable for a sharper reason, and it is one worth knowing.** A node
#: settling is not a state a recorded run lacks — every run here has one. Two endings are
#: checked before any condition a caller named and cannot be skipped, though either can
#: be named: a complete graph answers `settled`, and a run nothing is driving answers
#: `nothing-driving`, because they are "facts *about* the run where a settlement is a
#: fact *within* one" and a settlement read out of a run nobody is driving is not the
#: thing to act on. A recorded run is by definition undriven, so one of those two answers
#: every wait over one, whatever `--until` it was given — which is what makes a node
#: condition safe to ask for and what puts this ending out of reach here.
#:
#: Deliberately not a fourth rung above it: a waiting surface ends a wait only when
#: `surface` was asked for, so it does not outrank a node condition asked for on its own.
#: `tests/e2e/test_watch_selector_e2e.py` launches a run and drives both halves of that
#: rather than asserting either from the source.
#:
#: They are declared here so the set cannot quietly grow, and the wrapper's branching on
#: every ending is held end to end by `tests/e2e/test_watch_recipe_e2e.py`, which doubles
#: the verb for exactly this reason.
UNDRIVABLE_CONDITIONS = frozenset({"surface-waiting", "elapsed", "node-settled"})


def _engine_pin() -> str:
    """The pin that carries the verb, read from the wrapper rather than restated here.

    It is named in every failure below for the same reason the wrapper names it in its
    own refusal — it is the one thing a reader has to move — and taken from the wrapper
    because a second copy of a path is a second thing to keep current.
    """
    for kind, rest in _restatement():
        if kind == "pin":
            return rest
    raise AssertionError("the wrapper's surface names no engine pin")


def _restated_options() -> frozenset[str]:
    """Every option `scripts/watch-run.sh` passes through to the verb."""
    return frozenset(name for kind, name in _restatement() if kind == "option")


def _restated_conditions() -> tuple[str, ...]:
    """Every `--until` condition `scripts/watch-run.sh` offers an operator."""
    return tuple(rest for kind, rest in _restatement() if kind == "until")


def _restated_unbounded_wait() -> str:
    """The spelling the wrapper offers for a wait with no bound at all."""
    for kind, rest in _restatement():
        if kind == "timeout-unbounded":
            return rest
    raise AssertionError("the wrapper's surface names no unbounded wait")


def _restated_statuses() -> dict[int, str]:
    """Every exit status the wrapper branches on, and what it calls that condition."""
    found: dict[int, str] = {}
    for kind, rest in _restatement():
        if kind == "status":
            code, _, condition = rest.partition(" ")
            found[int(code)] = condition
    return found


def _restatement() -> list[tuple[str, str]]:
    """The wrapper's own account of the surface it restates, as `(kind, rest)` rows."""
    reported = subprocess.run(
        [str(WRAPPER), "--print-surface"],
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert reported.returncode == 0, (
        f"`{WRAPPER.name} --print-surface` exited {reported.returncode}, so the surface "
        f"this repository restates cannot be read from it:\n{reported.stderr}"
    )
    rows: list[tuple[str, str]] = []
    for line in reported.stdout.splitlines():
        kind, _, rest = line.partition(" ")
        rows.append((kind, rest))
    assert ("verb", WATCH[0]) in rows, (
        f"`{WRAPPER.name} --print-surface` named no `{WATCH[0]}` verb, so what it "
        f"restates cannot be reconciled with what the engine offers:\n{reported.stdout}"
    )
    return rows


def _recorded_runs() -> list[str]:
    """The checked-in runs the verb is driven over."""
    found = sorted(entry.name for entry in RECORDED_RUNS.iterdir() if entry.is_dir())
    assert found, (
        f"{RECORDED_RUNS} holds no recorded runs, so the verb cannot be driven into any "
        "terminal condition and the pairing below would be reconciled against nothing"
    )
    return found


def terminal_record(stdout: str) -> Terminal | None:
    """The ending the verb reported, out of the NDJSON it wrote on standard output.

    Pure and separate from the run below so the parsing is provable without an engine:
    the verb writes one record per line and only the last is terminal, and a build that
    stopped writing one has to fail here rather than be read as some other condition.
    """
    for line in reversed(stdout.splitlines()):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("watch") != "return":
            continue
        condition, status = record.get("condition"), record.get("exit")
        if isinstance(condition, str) and isinstance(status, int):
            return Terminal(condition=condition, exit=status)
    return None


def _driven(run: str, *arguments: str, timeout: float = 120) -> subprocess.CompletedProcess[str]:
    """The installed verb, run over one recorded run and given nothing else.

    The runs root is the checked-in one rather than this host's, so what the engine is
    asked is fixed by this tree. The wait is bounded by the caller's own `timeout` as
    well as by whatever the command line says, because one of the things driven below
    is the spelling that means *no* bound: a build that took it and then blocked would
    otherwise wedge this tier rather than fail it.
    """
    return subprocess.run(
        [str(ENGINE), *WATCH, run, *arguments],
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
        env={**os.environ, "ONEPIPELINE_RUNS_DIR": str(RECORDED_RUNS)},
    )


def _ending(run: str, *arguments: str) -> Terminal:
    """The ending the verb reported for one invocation, checked against its own status."""
    answered = _driven(run, *arguments, "--timeout", READ_ONCE)
    spelled = " ".join(arguments)
    reported = terminal_record(answered.stdout)
    assert reported is not None, (
        f"`onepipeline {WATCH[0]} {run} {spelled}` wrote no terminal record on standard "
        f"output, so what it ended on cannot be read from it. It exited "
        f"{answered.returncode} and said:\n{answered.stderr}"
    )
    assert reported.exit == answered.returncode, (
        f"`onepipeline {WATCH[0]} {run} {spelled}` reported `exit: {reported.exit}` in "
        f"its own terminal record and exited {answered.returncode}. A caller branches on "
        "the status, so a record that disagrees with it is worse than no record"
    )
    return reported


#: The conditions each recorded run is driven under, as the arguments that select them.
#: The default — no `--until` at all — is what a caller that predates the selector gets.
#: The second asks for a node condition as well, and is driven not because it produces a
#: new ending here — the note on `UNDRIVABLE_CONDITIONS` says why it cannot — but because
#: what it must not do is *change* one: a selector that suppressed `nothing-driving` on
#: the run it was asked about would be the silence this verb exists to end, and reading
#: the same pairing under both is what says it does not.
DRIVEN_SELECTIONS: tuple[tuple[str, ...], ...] = ((), ("--until", "node-settled"))


def _observed() -> dict[str, Terminal]:
    """Every terminal condition the installed verb can be driven into here.

    Driven rather than read out of the verb's help, which documents no status table at
    all: the pairing lives in the verb's own terminal record, which is the engine naming
    a condition and the status it returns for it in one place.

    Keyed by the invocation rather than by the run, because one run answers differently
    under different selectors — which is the whole of what the selector is — and a
    reader of a failure needs to know which invocation answered what.
    """
    found: dict[str, Terminal] = {}
    for run in _recorded_runs():
        for selection in DRIVEN_SELECTIONS:
            answered = _driven(run, *selection, "--timeout", READ_ONCE)
            # An invocation that wrote no terminal record ended at the command line
            # rather than on a condition: the verb does not read this selector at all,
            # or this run cannot answer it — every node of it settled `done`, or it
            # holds none. Neither is a pairing to reconcile, and the condition is
            # reconciled off whichever recorded run does answer it. The check below on
            # the declared set is what fails when *no* run can.
            if not _took_it(answered):
                continue
            found[" ".join((run, *selection))] = _ending(run, *selection)
    return found


def disagreements(restated: dict[int, str], observed: dict[str, Terminal]) -> list[str]:
    """Every place this repository's status table and the verb's own answers disagree.

    `observed` is keyed by the invocation that produced each ending — the run and the
    conditions it was driven under — because one run answers differently under different
    selectors, and a failure that named only the run would leave a reader unable to
    reproduce it.

    Compared for **equality** on the condition's name rather than by looking for the
    wrapper's words inside the verb's prose. The wrapper names each condition with the
    engine's own word, so there is nothing to interpret, and a swap — the shape that
    keeps every number and changes what two of them mean — is exactly what an equality
    catches and a prose search does not.
    """
    found: list[str] = []
    for invocation, reported in sorted(observed.items()):
        named = restated.get(reported.exit)
        if named is None:
            found.append(
                f"the verb answered `{reported.condition}` at exit status "
                f"{reported.exit} on `{invocation}`, and this repository branches on no "
                f"such status"
            )
        elif named != reported.condition:
            found.append(
                f"the verb answered `{reported.condition}` at exit status "
                f"{reported.exit} on `{invocation}`, and this repository calls status "
                f"{reported.exit} `{named}`"
            )
    return found


@pytest.mark.reads_checkouts
def test_every_option_this_repository_passes_is_one_the_engines_watch_verb_takes() -> None:
    """A watch refused for an option is a watch that never watched anything.

    Reported whole rather than at the first difference: an adopter reconciling a moved
    surface wants every option that moved, not the alphabetically first one.
    """
    surface = surface_of("onepipeline")

    missing = sorted(option for option in _restated_options() if not surface.accepts(WATCH, option))

    assert not missing, (
        f"`{WRAPPER.name}` passes options the pinned onepipeline's `{WATCH[0]}` verb does "
        f"not accept: {', '.join(missing)}. Reconcile the wrapper's restatement with the "
        f"engine adopted at {_engine_pin()}, or a watch is refused before it watches anything"
    )


@pytest.mark.reads_checkouts
def test_every_status_the_verb_returns_is_paired_with_the_condition_this_repository_gives_it() -> (
    None
):
    """A status that kept its number and changed its meaning is the dangerous drift.

    The wrapper decides which of the four terminal conditions happened from the verb's
    exit status alone — that is the point of the verb, and the alternative is matching
    prose, which is how a watch here reported a healthy dispatch dead. So each status
    the installed verb really returns is paired against the condition the verb itself
    names in the same record, and against what this repository calls that status.
    """
    observed = _observed()
    assert observed, "the verb was driven over no run, so nothing was reconciled"

    disagreed = disagreements(_restated_statuses(), observed)

    assert not disagreed, (
        f"`{WRAPPER.name}`'s exit statuses and the pinned onepipeline's `{WATCH[0]}` verb "
        "disagree:\n"
        + "\n".join(f"  - {entry}" for entry in disagreed)
        + f"\nReconcile the wrapper's restatement with the engine adopted at {_engine_pin()}"
    )


def test_the_node_this_module_names_is_one_the_recorded_run_actually_holds() -> None:
    """The shape is driven with a real id, so the fixture is read rather than trusted.

    A recorded run edited under this module would otherwise turn the reconciliation
    below into an assertion about a node nothing holds: the verb would refuse it for
    naming a node the graph does not have, that refusal is not the vocabulary refusal
    the check watches for, and the check would pass having proved nothing.
    """
    plan = json.loads((RECORDED_RUNS / NAMED_NODE_RUN / "plan.json").read_text(encoding="utf-8"))
    ids = [task.get("id") for task in plan.get("tasks", [])]

    assert NAMED_NODE in ids, (
        f"the recorded run {NAMED_NODE_RUN!r} no longer holds a node named "
        f"{NAMED_NODE!r}; it holds {ids}. Name one of those, or the condition shape "
        "below is driven against a node the graph does not hold"
    )
    assert ABSENT_NODE not in ids


@pytest.mark.reads_checkouts
@pytest.mark.parametrize("condition", _restated_conditions())
def test_every_until_condition_this_repository_offers_is_one_the_verb_reads(
    condition: str,
) -> None:
    """A condition this host tells a supervisor to type is one the parser accepts.

    Driven rather than read out of `--help`, which names no condition at all: the verb
    declares its vocabulary in its own source and renders `--until` with a placeholder,
    so the only thing that can answer whether a spelling is read is the parser. Its
    refusal names the whole vocabulary it does have, which is what a failure here hands
    an adopter.

    The one entry that is a shape rather than a word is driven with a node the recorded
    run's own graph holds, so what is under test stays the *spelling* rather than
    whether some particular id exists. And acceptance is read off the verb *reading the
    run* rather than off the absence of a refusal sentence, for the reason `_took_it`
    gives: two releases of this verb have refused the same value in two different words.
    """
    spelled = condition.replace(NODE_ID_PLACEHOLDER, NAMED_NODE)

    answered = _driven(NAMED_NODE_RUN, "--until", spelled, "--timeout", READ_ONCE)

    assert _took_it(answered), (
        f"`{WRAPPER.name}` offers `--until {condition}`, and the pinned onepipeline's "
        f"`{WATCH[0]}` verb did not read the run under it — it exited "
        f"{answered.returncode} saying: {answered.stderr.strip()}. Reconcile the "
        f"wrapper's vocabulary with the engine adopted at {_engine_pin()}, or an "
        "operator following this repository's own usage line is refused by a verb they "
        "did not think they were arguing with"
    )


@pytest.mark.reads_checkouts
def test_a_condition_naming_a_node_the_run_does_not_hold_is_refused_by_the_verb() -> None:
    """The refusal that makes the vocabulary check above mean something.

    A parser that read every string as a node would accept the shape whatever followed
    it, and the check above would then pass against a verb that validated nothing. So
    the other side is driven too: a node this run's graph does not hold is refused,
    naming that node and the ids the graph does hold, before anything is streamed.
    """
    answered = _driven(NAMED_NODE_RUN, "--until", f"node={ABSENT_NODE}", "--timeout", READ_ONCE)

    assert not _took_it(answered), (
        f"`onepipeline {WATCH[0]} {NAMED_NODE_RUN} --until node={ABSENT_NODE}` read the "
        "run anyway, so the conditions this wrapper passes through are validated "
        f"against nothing. Reconcile with the engine adopted at {_engine_pin()}"
    )
    assert ABSENT_NODE in answered.stderr and NAMED_NODE in answered.stderr, (
        f"the verb refused `--until node={ABSENT_NODE}` without naming both that node "
        f"and the ids run {NAMED_NODE_RUN!r} does hold, so a supervisor cannot correct "
        f"the command from the refusal: {answered.stderr.strip()}"
    )


@pytest.mark.reads_checkouts
def test_the_unbounded_wait_this_repository_offers_is_one_the_verb_takes() -> None:
    """`--timeout none` is a spelling this wrapper puts in front of an operator.

    It is the value that lets a supervisor write no loop at all, and it is distinct from
    the `0` whose published meaning is to read the run once and return — so a wrapper
    offering a spelling the verb dropped would compose a watch refused before it watched
    anything. Driven over a run that has settled, which returns on the first pass
    whatever the wait is, and under a bound of this module's own so a build that took the
    value and then blocked fails here rather than wedging the tier.
    """
    unbounded = _restated_unbounded_wait()

    answered = _driven(SETTLED_RUN, "--timeout", unbounded, timeout=120)

    assert _took_it(answered), (
        f"`{WRAPPER.name}` offers `--timeout {unbounded}`, and the pinned onepipeline's "
        f"`{WATCH[0]}` verb did not read the run under it — it exited "
        f"{answered.returncode} saying: {answered.stderr.strip()}. Reconcile with the "
        f"engine adopted at {_engine_pin()}"
    )
    reported = terminal_record(answered.stdout)
    assert reported is not None and reported.exit == answered.returncode, (
        f"`onepipeline {WATCH[0]} {SETTLED_RUN} --timeout {unbounded}` did not report a "
        f"terminal condition it agrees with. It exited {answered.returncode} and "
        f"said:\n{answered.stderr}"
    )


@pytest.mark.reads_checkouts
def test_the_conditions_no_recorded_run_can_produce_are_exactly_the_declared_ones() -> None:
    """The declaration cannot quietly grow, and it cannot quietly stop applying.

    Both directions, because each is a way this gate stops reconciling without failing.
    A condition that became drivable and stayed declared is one nobody is driving; a
    condition that stopped being drivable and was not declared would leave the set the
    checks above reconcile shrinking with nothing said about it.
    """
    driven = {reported.condition for reported in _observed().values()}
    named = set(_restated_statuses().values())

    assert driven == named - UNDRIVABLE_CONDITIONS, (
        f"the installed verb answers {sorted(driven)} over the recorded runs, and this "
        f"repository names {sorted(named)} less the declared {sorted(UNDRIVABLE_CONDITIONS)}. "
        "Add a recorded run that produces a newly drivable condition and take it out of "
        "UNDRIVABLE_CONDITIONS, or say why one that was drivable no longer is"
    )


def test_the_terminal_record_is_read_as_the_verbs_own_pairing() -> None:
    """What is read out of the verb's output is the pairing, not a status seen anywhere.

    Driven as a pure function so the parsing is provable without an engine in a
    particular state: the verb writes one record per line, only the last is terminal, and
    a build that stopped writing one has to be a failure rather than some other reading.
    """
    stream = (
        '{"watch":"event","event":{"kind":"node-settled"}}\n'
        '{"watch":"heartbeat","run_id":"r","unread":{"count":0}}\n'
        '{"watch":"return","run_id":"r","condition":"settled","exit":0,"cursor":"1:r:9"}\n'
    )

    assert terminal_record(stream) == Terminal(condition="settled", exit=0)
    # A stream with no terminal record is not some other ending: it is unreadable.
    assert terminal_record('{"watch":"event","event":{}}\n') is None
    assert terminal_record("") is None
    # A line that is not JSON at all is skipped rather than fatal — the verb's own human
    # form goes to the other descriptor, and a caller that merged them still has a record.
    assert terminal_record(
        'not json\n{"watch":"return","run_id":"r","condition":"elapsed","exit":5}\n'
    ) == Terminal(condition="elapsed", exit=5)
    # A record missing either half of the pairing answers neither half of it.
    assert terminal_record('{"watch":"return","condition":"settled"}\n') is None


def test_the_reconciliation_names_a_swapped_and_an_unknown_exit_status() -> None:
    """The comparison bites on every shape of drift, and is quiet on agreement.

    A swap is the shape that keeps every number and changes what two of them mean, and
    it is the one a check that only asked whether a status was known cannot see — so it
    is asserted first and by name.
    """
    restated = _restated_statuses()
    agreeing = {
        f"run-{status}": Terminal(condition=condition, exit=status)
        for status, condition in restated.items()
    }

    assert disagreements(restated, agreeing) == []

    moved = sorted(restated)[-2:]
    swapped = dict(agreeing) | {
        f"run-{moved[0]}": Terminal(condition=restated[moved[1]], exit=moved[0]),
        f"run-{moved[1]}": Terminal(condition=restated[moved[0]], exit=moved[1]),
    }
    named = disagreements(restated, swapped)
    assert len(named) == 2
    for status in moved:
        assert any(f"exit status {status}" in entry for entry in named)

    unknown = {"run-7": Terminal(condition="interrupted", exit=7)}
    assert disagreements(restated, unknown) == [
        "the verb answered `interrupted` at exit status 7 on `run-7`, and this "
        "repository branches on no such status"
    ]
