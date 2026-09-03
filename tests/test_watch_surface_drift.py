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
the comparison itself, and the one that drives the exemption's own condition, read
nothing outside this workspace and stay in the memoized tier.

**The exemption is the whole of what stands between this and a check that quietly
passes.** No engine offering the verb is installed here yet, so until
`config/onepipeline.version` names a release carrying it there is nothing to reconcile
against. Skipping would be a pass nobody earned, so those xfail, name the reason, and —
held by the companion below — come back on their own when the pin moves. What the
exemption must not also suspend is knowing the comparison *works*, which is why it is a
pure function driven below: an exempt check whose logic nobody has seen bite is one
trusted from its first green.

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

import re
import subprocess

import pytest
from published_surface import Surface, surface_of

from orchestrator.root import REPO_ROOT

#: The wrapper whose restatement is under test, and the verb it restates.
WRAPPER = REPO_ROOT / "scripts" / "watch-run.sh"
WATCH = ("watch",)


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


def _offers_the_watch_verb(surface: Surface) -> bool:
    """Whether this engine surface has the verb at all.

    Kept apart from the exemption below so that the condition — the whole of what
    decides when these checks reconcile anything — is a plain function a test can
    drive, rather than something only observable by a check exempting itself.
    """
    return WATCH[0] in surface.subcommands(())


# llmlint: ignore[contracts_have_one_source_or_a_drift_gate] The condition is the
# installed engine's own answer rather than a pin comparison, and it is the condition
# this repository's bar for the watch surface fixes; the companion below holds it in
# both directions. A verb that disappeared after adoption would not go unreported
# either: `scripts/watch-run.sh` refuses at exit 2 in its own words, naming the pin,
# before it watches anything.
def _exempt_while_the_engine_offers_no_watch_verb(surface: Surface) -> None:
    """Stop a reconciliation the installed engine has nothing to answer."""
    if not _offers_the_watch_verb(surface):
        pytest.xfail(
            f"the pinned onepipeline offers no `{WATCH[0]}` verb, so there is no engine "
            f"surface to reconcile this repository's restatement against; {_engine_pin()} "
            "is what carries it here"
        )


def _restated_options() -> frozenset[str]:
    """Every option `scripts/watch-run.sh` passes through to the verb."""
    return frozenset(name for kind, name in _restatement() if kind == "option")


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


#: A heading in clap's help output, e.g. `Options:` or `Exit status:`.
_SECTION = re.compile(r"^(?P<name>[A-Za-z][A-Za-z ]*):$")

#: One entry under the exit-status section: the status, then what the verb returns it
#: for. Two spaces or more, because clap lays a status table out the way it lays out
#: options — a single space would also match a sentence of prose that opens with a
#: number.
_STATUS_ENTRY = re.compile(r"^\s+(?P<code>\d+)\s{2,}(?P<described>\S.*?)\s*$")


def _stem(word: str) -> str:
    """One word of a condition name, with its inflection dropped.

    The engine documents a condition in a sentence and this repository names it in a
    slug, so `wait-elapsed` has to be recognised in "the wait elapsed" and
    `nothing-driving` in "nothing is driving this run". Matching the slug's words
    literally would fail on every tense; matching them as bare substrings would pass on
    almost anything. Stems are the middle: enough to survive "settles" against
    `settled`, not enough for "blocking" to be found in a line about waiting.
    """
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) > len(suffix) + 2:
            return word[: -len(suffix)]
    return word


def _documented_conditions(help_text: str) -> dict[int, str]:
    """Each exit status the verb documents, and the condition it documents it for.

    Read as a table rather than searched for as digits. A check that only asked whether
    each number appears somewhere in the help would pass on a `--wait 5` in an example,
    on a version string, and — the failure that matters — on an engine that kept all
    four statuses and swapped which condition two of them mean, which is a watch
    reporting a settled run while a blocking question waits.
    """
    found: dict[int, str] = {}
    section = ""
    for line in help_text.splitlines():
        heading = _SECTION.match(line)
        if heading is not None:
            section = heading.group("name")
            continue
        if "exit" not in section.casefold():
            continue
        entry = _STATUS_ENTRY.match(line)
        if entry is not None:
            found[int(entry.group("code"))] = entry.group("described")
    return found


def _disagreements(restated: dict[int, str], documented: dict[int, str]) -> list[str]:
    """Every place this repository's status table and the verb's own disagree.

    Both directions, because both are wrong in ways a caller pays for: a status this
    branches on that the verb no longer returns is a terminal condition never reported,
    and a status the verb returns that this does not know is a run whose ending is read
    as "none of the four".
    """
    if not documented:
        return [
            "the verb's own help documents no exit-status table at all, so nothing "
            "pairs a status with the condition it means"
        ]
    found: list[str] = []
    for code, condition in sorted(restated.items()):
        described = documented.get(code)
        if described is None:
            found.append(
                f"exit status {code} is this repository's `{condition}`, and the verb "
                f"documents no status {code}"
            )
            continue
        unnamed = [
            word
            for word in condition.split("-")
            if re.search(rf"\b{re.escape(_stem(word))}", described, re.IGNORECASE) is None
        ]
        if unnamed:
            found.append(
                f"exit status {code} is this repository's `{condition}`, and the verb "
                f"documents it as {described!r}, which names none of: {', '.join(unnamed)}"
            )
    for code, described in sorted(documented.items()):
        if code not in restated:
            found.append(
                f"the verb documents exit status {code} as {described!r}, and this "
                "repository branches on no such status"
            )
    return found


def _watch_help() -> str:
    """The pinned engine's own help for the verb, for the statuses it documents."""
    binary = REPO_ROOT / ".venv" / "bin" / "onepipeline"
    reported = subprocess.run(
        [str(binary), *WATCH, "--help"],
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert reported.returncode == 0, (
        f"`onepipeline {WATCH[0]} --help` exited {reported.returncode}, so what the verb "
        f"documents cannot be read from it:\n{reported.stderr}"
    )
    return reported.stdout


@pytest.mark.reads_checkouts
def test_every_option_this_repository_passes_is_one_the_engines_watch_verb_takes() -> None:
    """A watch refused for an option is a watch that never watched anything.

    Reported whole rather than at the first difference: an adopter reconciling a moved
    surface wants every option that moved, not the alphabetically first one.
    """
    surface = surface_of("onepipeline")
    _exempt_while_the_engine_offers_no_watch_verb(surface)

    missing = sorted(option for option in _restated_options() if not surface.accepts(WATCH, option))

    assert not missing, (
        f"`{WRAPPER.name}` passes options the pinned onepipeline's `{WATCH[0]}` verb does "
        f"not accept: {', '.join(missing)}. Reconcile the wrapper's restatement with the "
        f"engine adopted at {_engine_pin()}, or a watch is refused before it watches anything"
    )


@pytest.mark.reads_checkouts
def test_every_exit_status_is_paired_with_the_condition_the_verb_documents_it_for() -> None:
    """A status that kept its number and changed its meaning is the dangerous drift.

    The wrapper decides which of the four terminal conditions happened from the verb's
    exit status alone — that is the point of the verb, and the alternative is matching
    prose, which is how a watch here reported a healthy dispatch dead. So each status is
    reconciled against the condition the verb *documents it for*, out of the verb's own
    status table. Asking only whether the digit occurs somewhere in the help would be no
    check at all: `0`, `3`, `4` and `5` occur in examples, defaults and version strings,
    and an engine that swapped `4` and `5` would pass while `just watch` reported the
    wait elapsing every time a blocking question was waiting to be answered.
    """
    surface = surface_of("onepipeline")
    _exempt_while_the_engine_offers_no_watch_verb(surface)

    disagreements = _disagreements(_restated_statuses(), _documented_conditions(_watch_help()))

    assert not disagreements, (
        f"`{WRAPPER.name}`'s exit statuses and the pinned onepipeline's `{WATCH[0]}` verb "
        "disagree:\n"
        + "\n".join(f"  - {entry}" for entry in disagreements)
        + f"\nReconcile the wrapper's restatement with the engine adopted at {_engine_pin()} — "
        f"and if the verb documents its statuses somewhere other than `onepipeline "
        f"{WATCH[0]} --help`, reconcile this check against that place rather than "
        "widening it"
    )


def _engine_surface(*subcommands: str) -> Surface:
    """One engine's command tree offering exactly `subcommands` at its root.

    Composed rather than read off the installed binary, because the condition below has
    to be driven in the direction this host cannot currently produce: no engine offering
    the watch verb is installed here, so an engine that *does* offer it exists only as
    this. It is not a second restatement of the contract — nothing about the verb's
    options or statuses is stated here, only whether the root has a verb by that name,
    which is the whole of what the exemption turns on.
    """
    return Surface(
        "onepipeline",
        frozenset({(), *((name,) for name in subcommands)}),
        {(): frozenset({"--help"})} | {(name,): frozenset() for name in subcommands},
    )


def test_the_exemption_lasts_only_while_the_installed_engine_offers_no_watch_verb() -> None:
    """The two reconciliations above come back the moment an engine with the verb is here.

    Their exemption is the one thing between a released engine this repository has not
    adopted and a gate that has quietly stopped reconciling anything, so what it is
    conditioned on is asserted rather than assumed — and asserted in both directions,
    because only one of them is observable on this host. An engine without the verb
    earns the exemption and the reason names the verb and the pin that carries it; an
    engine with the verb earns nothing, and the checks reconcile.

    Driven through the exemption itself and not only through the predicate beneath it:
    what a reader has to trust is that an engine offering the verb cannot reach an
    `xfail`, and a predicate returning `True` is one `not` away from that being false.
    """
    without = _engine_surface("start", "monitor", "next")
    offering = _engine_surface("start", "monitor", "next", WATCH[0])

    assert not _offers_the_watch_verb(without)
    assert _offers_the_watch_verb(offering)

    with pytest.raises(pytest.xfail.Exception) as exempted:
        _exempt_while_the_engine_offers_no_watch_verb(without)
    reason = str(exempted.value)
    assert WATCH[0] in reason
    assert _engine_pin() in reason

    # No exemption, and therefore no `xfail`: the reconciliations run for real. Caught
    # and re-raised as a failure rather than simply called, because an `xfail` escaping
    # here would report *this* check as exempt too — the one shape of breakage that
    # would leave the whole reconciliation suspended and every tier still green.
    try:
        _exempt_while_the_engine_offers_no_watch_verb(offering)
    except pytest.xfail.Exception as exempted:
        raise AssertionError(
            f"an engine offering the `{WATCH[0]}` verb earned the exemption anyway, so "
            f"adopting one at {_engine_pin()} would reconcile nothing: {exempted}"
        ) from exempted


def _help_documenting(statuses: dict[int, str]) -> str:
    """A verb's help page documenting exactly `statuses`, in the shape clap renders one.

    Composed rather than quoted, and composed from whatever the caller passes rather
    than from a copy of the engine's own table: there is no engine offering this verb to
    copy from, and a fixture that pretended to be one would be a second restatement of
    the contract with nothing reconciling it. What these checks are about is the
    parsing and the pairing, and both are exercised by a table this repository already
    owns every word of.
    """
    documented = "\n".join(f"  {code}  {text}" for code, text in sorted(statuses.items()))
    return (
        "Watch one run until something a supervisor has to act on happens\n\n"
        "Usage: onepipeline watch [OPTIONS] <RUN>\n\n"
        f"Exit status:\n{documented}\n\n"
        "Options:\n      --wait <SECONDS>  how long to wait before giving up\n"
    )


def _documented_as_restated() -> dict[int, str]:
    """A help table that agrees with the wrapper, phrased as the verb would phrase it."""
    return {
        code: f"the watch returned {condition.replace('-', ' ')}"
        for code, condition in _restated_statuses().items()
    }


def test_the_status_table_is_read_as_a_table_rather_than_as_digits_in_the_help() -> None:
    """What is parsed out of the verb's help is the pairing, not the presence of a number.

    This runs whether or not an engine with the verb is installed, deliberately: the two
    checks above are exempt until one is, and a comparison nothing exercises in the
    meantime is a comparison nobody has ever seen work.
    """
    documented = _documented_as_restated()

    assert _documented_conditions(_help_documenting(documented)) == documented
    # A number outside the status table is not a status: the `--wait <SECONDS>` line and
    # the usage line are both in that help and neither contributes one.
    assert _documented_conditions("Usage: onepipeline watch\n  --wait 5  seconds\n") == {}


def test_the_reconciliation_names_a_swapped_a_missing_and_an_unknown_exit_status() -> None:
    """The comparison bites on every shape of drift, and is quiet on agreement.

    A swap is the shape that keeps every number and changes what two of them mean, and
    it is the one a presence check cannot see — so it is asserted first and by name.
    """
    restated = _restated_statuses()
    documented = _documented_conditions(_help_documenting(_documented_as_restated()))

    assert _disagreements(restated, documented) == []

    moved = sorted(restated)[-2:]
    swapped = dict(documented) | {
        moved[0]: documented[moved[1]],
        moved[1]: documented[moved[0]],
    }
    named = _disagreements(restated, swapped)
    assert len(named) == 2
    for code in moved:
        assert any(f"exit status {code}" in entry for entry in named)

    dropped = {code: text for code, text in documented.items() if code != moved[0]}
    assert _disagreements(restated, dropped) == [
        f"exit status {moved[0]} is this repository's `{restated[moved[0]]}`, and the "
        f"verb documents no status {moved[0]}"
    ]

    added = dict(documented) | {7: "the watch was interrupted"}
    assert _disagreements(restated, added) == [
        "the verb documents exit status 7 as 'the watch was interrupted', and this "
        "repository branches on no such status"
    ]

    assert _disagreements(restated, {}) == [
        "the verb's own help documents no exit-status table at all, so nothing pairs a "
        "status with the condition it means"
    ]
