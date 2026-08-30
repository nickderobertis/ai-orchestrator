"""`just sweep` reclaims every family this host accumulates, and says what it did not.

Nothing here is doubled. The recipe, the wrapper, and both published verbs are the real
ones; what makes that safe is isolation rather than substitution — `ONEAGENTGRAPH_STATE_DIR`,
`TMPDIR`, and `ONEVCS_HOME` put every family this run judges inside `tmp_path`, and
`AI_ORCHESTRATOR_HOME` does the same for the legacy worktree root the trailer reports on.
`TMPDIR` carries twice as much weight as it used to: it is both where `oneagentgraph`
writes the family it owns and the host scratch root the trailer now measures, so a
journey that let it point at the real `/tmp` would report on 83 GiB of this host.

A sweep with nothing to act on is a short form of two sentences, so several journeys
below rehearse with `--dry-run` first and then sweep for real: the rehearsal is where
the reports are read, and the real pass is where the disk and the short form are.
`--dry-run` keeps its report because it removes nothing and is asked in order to be
answered.

Several journeys remove for real rather than rehearsing, because their claims are about
what is left on disk. The one that holds a directory open holds it with an actual
process — a child that takes the exclusive `owner.lock` the sweeper's ownership proof
consults and then sits there — since a stub would prove only that this suite can write a
file the sweeper reads. It is preceded by a rehearsal whose only job is to prove the
isolation took: a real removal pass is worth running only once the report has named
`tmp_path` as the roots it will sweep.

What the command covers and why is docs/orchestration.md, "The recorded run".
"""

from __future__ import annotations

import os
import re
import subprocess
import textwrap
import time
from pathlib import Path

import pytest
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The wrapper whose trailer is the subject of most of this module.
WRAPPER = REPO_ROOT / "scripts" / "sweep.sh"


def _declared(constant: str) -> str:
    """One constant's value, read out of the wrapper itself.

    Read rather than restated, because a restatement would let the wrapper and this
    module drift apart while both stayed green — and it is the wrapper's claim, not
    this module's, that the journeys below hold against what the verbs report.
    """
    for line in WRAPPER.read_text().splitlines():
        name, separator, value = line.partition("=")
        if name == constant and separator:
            return value.strip("'")
    raise AssertionError(f"{WRAPPER.name} declares no {constant}")


def _declared_families(constant: str) -> tuple[str, ...]:
    """The families the trailer claims for one verb, as the wrapper declares them."""
    return tuple(family.strip() for family in _declared(constant).split(","))


#: The families each verb owns, as the trailer claims them. Held against what the
#: installed verbs report examining, so a release that adds or renames a family fails
#: here rather than leaving the trailer describing the previous one.
ONEAGENTGRAPH_FAMILIES = _declared_families("ONEAGENTGRAPH_FAMILIES")
ONEVCS_FAMILIES = _declared_families("ONEVCS_FAMILIES")

#: Every family this recipe composes, as its own short forms name them. Composed from
#: the families declared above rather than pasted, so a release that renames one fails
#: here too instead of leaving the short form describing the previous release.
FAMILIES = f"oneagentgraph {', '.join(ONEAGENTGRAPH_FAMILIES)}; onevcs {', '.join(ONEVCS_FAMILIES)}"


def _declared_block(constant: str) -> list[str]:
    """One multi-line single-quoted constant's value, read out of the wrapper itself.

    Separate from `_declared` because that one reads a value off one line and this one
    is a sentence spanning several: the wrapper writes it with `printf`, so what an
    operator sees is these lines with the first indented under the verdict above it.
    """
    text = WRAPPER.read_text()
    opening = f"\n{constant}='"
    start = text.find(opening)
    if start < 0:
        raise AssertionError(f"{WRAPPER.name} declares no multi-line {constant}")
    start += len(opening)
    end = text.index("'", start)
    return text[start:end].splitlines()


#: The clause every one-line verdict ends with, and the paragraph the long form carries
#: instead. Read rather than restated, for the reason `_declared` gives: a successful
#: sweep is one line, so the clause is part of the verdict rather than a note under it.
FREE_SPACE_CLAUSE = _declared("FREE_SPACE_CLAUSE")
FREE_SPACE_LINES = [
    f"    {line}" if index == 0 else line
    for index, line in enumerate(_declared_block("FREE_SPACE_NOTE"))
]

#: The two readings that used to be one sentence. Nothing reclaimed with candidates
#: examined means every candidate was live or within retention — the sweep working —
#: and nothing reclaimed with none examined means there was nothing to judge. A host
#: filling up looks like the first and reads like the second.
NOTHING_EXAMINED = (
    f"just sweep: nothing reclaimed — no candidate was examined; every family ({FAMILIES}) "
    f"was empty; {FREE_SPACE_CLAUSE}."
)


def nothing_reclaimed(examined: int) -> str:
    """The short form for a sweep that judged candidates and could take none of them."""
    return (
        f"just sweep: nothing reclaimed — {examined} candidate(s) examined across every "
        f"family ({FAMILIES}), all live or within retention; {FREE_SPACE_CLAUSE}."
    )


def reclaimed(taken: int, examined: int) -> str:
    """The short form for a sweep that took something and left nothing to act on."""
    return (
        f"just sweep: reclaimed {taken} of {examined} candidate(s) examined — every "
        f"family examined: {FAMILIES}; {FREE_SPACE_CLAUSE}."
    )


def short_form(verdict: str) -> list[str]:
    """The whole of what the composition writes when there is nothing to act on: one line."""
    return [verdict]


#: Every section heading of the long form — both verbs' and the trailer's — which the
#: composition prints only when something is left for an operator to act on.
SECTIONS = (
    "=== oneagentgraph sweep — the scratch a dispatch leaves behind ===",
    "=== onevcs sweep — the workspaces a publication leaves behind ===",
    "=== just sweep — what this run looked at ===",
)

#: The floor the recipe passes when the caller names none. It is the recipe's own
#: choice rather than either verb's default, so the journeys below ask both verbs
#: whether they take it and whether they then apply it.
RECIPE_DEFAULT_AGE = re.compile(r"This recipe passes (\d+) when you name none")

#: The one number the composed help restates that neither verb owns: the age floor
#: both of them default to when nobody passes one. Matched against what each installed
#: verb declares, below.
DEFAULT_AGE_CLAIM = re.compile(r"Their own default is (\d+),")

#: The prefix `oneagentgraph` gives the scratch it writes under `TMPDIR`. A directory
#: without it is not in the `temp` family and is not a candidate — which is what makes
#: everything else under that root the family the trailer reports on and nothing
#: reclaims.
TEMP_FAMILY_PREFIX = "oneagentgraph-"

#: A real `uv` lock, of the shape `uv run` takes in that root on the way to each verb —
#: so no sweep can observe a root without one. A lock is the only thing besides an
#: examined family left out of the count, and the journeys below hold every part of
#: that: this one out of the count, any other loose file in it, this one's bytes still
#: in the size, and the exclusion named in the report rather than only in the source.
UV_LOCK_TRANSIENT = "uv-eff31e9f3b703349.lock"

#: The pattern the wrapper excludes, as the wrapper declares it. The report has to name
#: this: an exclusion an operator cannot read is a count of part of the root reading as
#: a count of the root, which is the all-clear-shaped answer the trailer exists to stop
#: giving one root up.
UV_LOCK_PATTERN = _declared("UV_LOCK_TRANSIENT")

#: An entry under the host scratch root shaped like the one that really fills this
#: host: `nx` writes one of these per invocation, never reuses one, and never removes
#: one, and 3,646 of them were 75 GiB of the 83 GiB in `/tmp` while every sweep
#: reported success. The trailing id is what makes them read as thousands of unrelated
#: producers, and folding it is what the trailer's name groups are for.
NX_CACHE_FAMILY = "nx-native-file-cache-"

#: The file the sweeper's ownership proof consults, and the two facts it records: the
#: owner's pid and the kernel's start token for it. A recycled pid is why the token is
#: there, and an exclusive lock on this file is what a live owner holds.
OWNER_LOCK = "owner.lock"

#: A pid no process on this host has. Its directory is the one that must be reclaimed,
#: so that a journey asserting the held one survived is not passing because nothing was
#: swept at all.
DEAD_OWNER = "999999 1\n"

#: Written by the holder below: it takes the lock, records itself, and says so. The
#: parent waits for that line rather than for a duration, because a sweep that ran
#: before the lock was taken would prove nothing and would prove it intermittently.
HOLDER = textwrap.dedent(
    """
    import fcntl, os, sys, time
    from pathlib import Path

    directory = Path(sys.argv[1])
    os.chdir(directory)
    stat = Path(f"/proc/{os.getpid()}/stat").read_text()
    # Field 22 of `/proc/<pid>/stat`, counted past the parenthesised command name so a
    # process whose name contains a space or a bracket is still read correctly.
    token = stat[stat.rindex(")") + 2 :].split()[19]
    lock = open(directory / "owner.lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    lock.write(f"{os.getpid()} {token}\\n")
    lock.flush()
    print("held", flush=True)
    time.sleep(600)
    """
)


def _age(path: Path, hours: float) -> None:
    """Move `path` and everything under it that many hours into the past.

    Both verbs judge an age floor from what is on disk, so a journey about the floor
    has to move the disk rather than the clock: nothing here is allowed to substitute
    either verb's idea of now.
    """
    when = time.time() - hours * 3600
    for target in (path, *path.rglob("*")):
        os.utime(target, (when, when))


def _git(*arguments: str, cwd: Path | None = None) -> None:
    """One git command, with an identity so a commit works under any host config."""
    subprocess.run(
        ["git", "-c", "user.email=sweep@example.invalid", "-c", "user.name=sweep", *arguments],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


class Host:
    """The scratch roots one sweep judges, and the environment that points it at them."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path
        self.runs = tmp_path / "oneagentgraph-state"
        self.temp = tmp_path / "tmp"
        self.onevcs_home = tmp_path / "onevcs-home"
        self.worktrees = tmp_path / "ai-orchestrator-home" / "worktrees"
        self.runs.mkdir(parents=True)
        self.temp.mkdir(parents=True)
        (self.onevcs_home / "workspaces").mkdir(parents=True)

    @property
    def environment(self) -> dict[str, str]:
        return {
            **os.environ,
            "ONEAGENTGRAPH_STATE_DIR": str(self.runs),
            "TMPDIR": str(self.temp),
            "ONEVCS_HOME": str(self.onevcs_home),
            "AI_ORCHESTRATOR_HOME": str(self.worktrees.parent),
        }

    def scratch(self, name: str, *, held: bool, age_hours: float = 0, payload: int = 4096) -> Path:
        """One `temp`-family directory, with an owner that is either alive or gone."""
        directory = self.temp / f"{TEMP_FAMILY_PREFIX}{name}"
        directory.mkdir()
        (directory / "payload").write_bytes(b"\0" * payload)
        if not held:
            (directory / OWNER_LOCK).write_text(DEAD_OWNER)
        if age_hours:
            _age(directory, age_hours)
        return directory

    def loose_file(self, name: str, *, payload: int = 4096) -> Path:
        """One file directly under the host scratch root, belonging to no verb here.

        `uv` writes one of these — `uv-<hash>.lock` — into this root on the way to
        every sweep, so this is not a hypothetical shape.
        """
        path = self.temp / name
        path.write_bytes(b"\0" * payload)
        return path

    def unowned(self, name: str, *, payload: int = 4096) -> Path:
        """One entry under the host scratch root that no verb this recipe composes owns.

        Deliberately unprefixed: measured on the adopted release, `oneagentgraph`
        counts a directory beside its own in the `temp` family's directory total and
        then judges it by nothing — neither reclaiming it nor retaining it with a
        reason — so an entry shaped like this belongs to no sweeper here. That is the
        whole of what the trailer's new family is made of.
        """
        directory = self.temp / name
        directory.mkdir()
        (directory / "payload").write_bytes(b"\0" * payload)
        return directory

    def _lender(self) -> Path:
        """The checkout a run clone borrows its objects from, cut once per host."""
        lender = self.root / "lender"
        if not lender.exists():
            _git("init", "--quiet", str(lender))
            _git("commit", "--quiet", "--allow-empty", "-m", "lender", cwd=lender)
        return lender

    def workspace(
        self,
        session: str,
        *,
        finished: bool,
        family: str = "publications",
        age_hours: float = 0,
    ) -> Path:
        """One run root shaped as `onevcs` cuts one — really, not just in outline.

        A tree that merely looks the part is retained: onevcs proves a workspace is its
        own from the run clone inside it and refuses one whose repository borrows no
        lender's objects, so the clone here is a real `git clone --shared`, which is
        what a run clone is. `finished` is the other half of that proof — a gate
        verdict under `gate-logs` is what says the publication got to the end — and a
        root without one is retained with that reason.
        """
        root = self.onevcs_home / "workspaces" / family / f"{session}-1a2b3c-18cd0-0"
        (root / "worktree").mkdir(parents=True)
        _git("clone", "--quiet", "--shared", str(self._lender()), str(root / "clone"))
        if finished:
            gate = root / "gate-logs" / session
            gate.mkdir(parents=True)
            (gate / "gate-0001.log").write_text("gate passed\n")
        if age_hours:
            _age(root, age_hours)
        return root

    def legacy_worktree(self, name: str) -> Path:
        """One directory under the pre-adoption worktree root nothing here may reclaim."""
        directory = self.worktrees / name
        directory.mkdir(parents=True)
        (directory / "payload").write_bytes(b"\0" * 4096)
        return directory


@pytest.fixture
def host(tmp_path: Path) -> Host:
    return Host(tmp_path)


def sweep(host: Host, *arguments: str) -> subprocess.CompletedProcess[str]:
    """The real recipe, against the roots this journey owns."""
    return subprocess.run(
        ["just", "sweep", *arguments],
        cwd=REPO_ROOT,
        env=host.environment,
        check=False,
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=e2e_timeout(120),
    )


def trailer(report: str) -> str:
    """The part of the report the composition itself writes."""
    _, _, tail = report.partition("=== just sweep — what this run looked at ===")
    return tail


def examined(report: str) -> str:
    section, _, _ = trailer(report).partition("Families not examined:")
    return section


def not_examined(report: str) -> str:
    _, _, section = trailer(report).partition("Families not examined:")
    return section


def test_a_directory_a_live_process_holds_survives_a_real_sweep(host: Host) -> None:
    """The claim the whole sweep rests on, proven by removing for real with an owner alive.

    A publication holds a working tree that no command-line pattern reveals, and a
    liveness check that matched argv once reported a live publication's tree as free.
    So the holder here is a real process holding the real lock, and the assertion is
    that its directory is still on disk after a pass that removed the one beside it.
    """
    held = host.scratch("held", held=True)
    dead = host.scratch("dead", held=False)

    holder = subprocess.Popen(
        ["python3", "-c", HOLDER, str(held)],
        stdout=subprocess.PIPE,
        text=True,
        cwd=str(held),
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held", "the holder never took the lock"

        # The rehearsal that licenses the removal below: it must name this journey's own
        # roots, so a release that stopped honouring the overrides fails here rather
        # than sweeping the operator's scratch. It is also where the retention is read,
        # because the real pass below has nothing to act on and so says the short form.
        rehearsal = sweep(host, "--dry-run", "--min-age-hours", "0")
        assert rehearsal.returncode == 0, rehearsal.stderr
        assert str(host.temp) in rehearsal.stdout
        assert str(host.runs) in rehearsal.stdout
        assert "is still locked by its owner" in rehearsal.stdout

        result = sweep(host, "--min-age-hours", "0")

        assert result.returncode == 0, result.stderr
        assert held.exists(), "a directory a live process holds was removed"
        assert not dead.exists(), "the dead directory beside it was not reclaimed"
        assert result.stdout.splitlines() == short_form(reclaimed(1, 2))
    finally:
        holder.terminate()
        holder.wait(timeout=e2e_timeout(30))


def test_the_report_names_every_family_examined_and_every_family_it_could_not(
    host: Host,
) -> None:
    """Each family appears in exactly one of the two lists, so neither can hide one.

    Every family this recipe knows about is on this host at once: the two each verb
    owns, the pre-adoption worktree root, and the host scratch root. The verbs' four
    are claimed as examined and the other two as not, and no name is in both lists.
    """
    host.workspace("onevcs-s-aaaaaaaaaaaa", finished=False)
    host.legacy_worktree("nickderobertis__llmlint")
    host.unowned(f"{NX_CACHE_FAMILY}3a91b2c")

    result = sweep(host, "--dry-run")

    assert result.returncode == 0, result.stderr
    for family in ONEAGENTGRAPH_FAMILIES + ONEVCS_FAMILIES:
        assert family in examined(result.stdout), f"{family} is in neither list"
        assert family not in not_examined(result.stdout), f"{family} is in both lists"
    assert str(host.worktrees) in not_examined(result.stdout)
    assert str(host.temp) in not_examined(result.stdout)
    assert str(host.temp) not in examined(result.stdout), "the scratch root is in both lists"


def test_the_families_the_trailer_claims_are_the_families_the_verbs_examined(
    host: Host,
) -> None:
    """The trailer names families rather than pointing at the reports above it.

    That is worth more to a reader and costs a restatement, so the restatement is held
    to what the installed verbs report examining: a release that renames `temp` or adds
    a third family fails here instead of leaving the trailer describing the one before.
    """
    result = sweep(host, "--dry-run")

    assert result.returncode == 0, result.stderr
    reported = {
        line.split('"')[1]
        for line in result.stdout.splitlines()
        if line.startswith("sweep: examined family ")
    }
    assert reported == set(ONEAGENTGRAPH_FAMILIES)
    onevcs_report, _, _ = result.stdout.partition("Families not examined:")
    _, _, onevcs_families = onevcs_report.partition("Families examined:")
    reported = {
        line.split(" — ")[0].strip()
        for line in onevcs_families.splitlines()
        if line.startswith("  ")
    }
    assert reported == set(ONEVCS_FAMILIES)


def test_a_sweep_that_reclaimed_nothing_still_says_what_it_looked_at(host: Host) -> None:
    """The failure the composition exists to prevent: `0 B` with no account of the scope."""
    result = sweep(host, "--dry-run")

    assert result.returncode == 0, result.stderr
    assert "would reclaim 0 B from 0 directories" in result.stdout
    assert "would reclaim 0 workspace(s), 0 bytes" in result.stdout
    for family in ONEAGENTGRAPH_FAMILIES + ONEVCS_FAMILIES:
        assert family in examined(result.stdout)


def test_a_sweep_with_nothing_left_to_act_on_is_the_short_form(host: Host) -> None:
    """Coverage was complete and nothing here is an operator's, so the sections stay away.

    Proven on a pass that really reclaimed rather than on an idle host, because the
    claim is about a *working* sweep being quiet — one that swept nothing would print
    the same short form for the wrong reason, which is what the pair of journeys below
    is about. The verdict still names all four families and now says how many
    candidates it judged, so the short form is an account of the scope and not an
    unqualified all-clear.
    """
    dead = host.scratch("dead", held=False)

    result = sweep(host, "--min-age-hours", "0")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == short_form(reclaimed(1, 1))
    assert not dead.exists(), "the short form came from a sweep that reclaimed nothing"


def test_nothing_reclaimed_with_candidates_examined_is_not_the_sentence_for_an_empty_host(
    host: Host,
) -> None:
    """The two readings a single `Reclaimed: none` used to give one answer for.

    A host whose families are full of live work reclaims nothing, and so does a host
    with no families to judge at all. The first is the sweep working; the second is a
    sweep that looked at nothing, and on a host where the disk is filling those are
    opposite pieces of news. Both are driven here against the same recipe and the same
    roots, and the assertion is that the two answers differ — asserted as a comparison
    as well as against each sentence, so a future edit that made both say one thing
    again fails here rather than passing two half-checks.
    """
    empty = sweep(host, "--min-age-hours", "0")

    assert empty.returncode == 0, empty.stderr
    assert empty.stdout.splitlines() == short_form(NOTHING_EXAMINED), empty.stdout

    live = host.scratch("live", held=True)
    holder = subprocess.Popen(
        ["python3", "-c", HOLDER, str(live)],
        stdout=subprocess.PIPE,
        text=True,
        cwd=str(live),
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held", "the holder never took the lock"

        judged = sweep(host, "--min-age-hours", "0")
    finally:
        holder.terminate()
        holder.wait(timeout=e2e_timeout(30))

    assert judged.returncode == 0, judged.stderr
    assert judged.stdout.splitlines() == short_form(nothing_reclaimed(1)), judged.stdout
    assert live.exists(), "the candidate this journey is about was reclaimed"
    assert judged.stdout != empty.stdout, (
        "a sweep that judged a candidate and could take none of it said exactly what a "
        "sweep with nothing to judge said; those are opposite pieces of news on a host "
        "whose disk is filling"
    )


def test_a_family_neither_verb_examined_keeps_both_reports_and_the_trailer(
    host: Host,
) -> None:
    """The other half, and the state this whole composition exists for.

    Every verb succeeds and the recipe still exits 0, exactly as in the journey above.
    The one difference is that the pre-adoption worktree root is on this host, so a
    family nothing here examines is in reach — and that alone has to bring back both
    reports and the trailer that names it. A `0 B reclaimed` with that root unmentioned
    is the all-clear that filled this host's disk.
    """
    dead = host.scratch("dead", held=False)
    legacy = host.legacy_worktree("nickderobertis__llmlint")

    result = sweep(host, "--min-age-hours", "0")

    assert result.returncode == 0, result.stderr
    for section in SECTIONS:
        assert section in result.stdout, "an unexamined family did not bring the report back"
    assert str(host.worktrees) in not_examined(result.stdout)
    for verdict in (NOTHING_EXAMINED, nothing_reclaimed(1), reclaimed(1, 1)):
        assert verdict not in result.stdout, "the short form stood in for the long one"
    assert "\n".join(FREE_SPACE_LINES) in result.stdout, (
        "the long form is what a reader opens because something is wrong, with the "
        "`Reclaimed:` line above it; it has to say what really answers that question"
    )
    assert legacy.exists(), "a root this sweep only reports on was reclaimed"
    assert not dead.exists(), "the families the verbs do own went unswept"


def test_the_host_scratch_root_is_named_in_the_trailer_with_what_it_measured(
    host: Host,
) -> None:
    """The family that actually fills this host, measured and named and left alone.

    Neither verb reaches it: `oneagentgraph` owns only what it prefixed under this
    root and `onevcs` keeps its workspaces elsewhere, so before this the root was in
    neither list — and a `0 B reclaimed` beside 139 GB of it read as an all-clear. The
    claims here are what an operator needs in order to act: a size, a count that
    leaves the prefixed family out rather than double-counting it, and the largest
    name group with its trailing ids folded, since the one that filled this disk was
    3,646 directories of one producer and hid inside a total as a long tail.

    It sweeps for real rather than rehearsing, because the other half of the claim is
    that nothing here removed or rewrote any of it while the families the verbs do own
    were reclaimed around it.
    """
    grouped = [
        host.unowned(f"{NX_CACHE_FAMILY}{token}", payload=64 * 1024)
        for token in ("3a91b2c", "7f0d415", "c21e9a8")
    ]
    alone = host.unowned("node-compile-cache")
    dead = host.scratch("dead", held=False)

    result = sweep(host, "--min-age-hours", "0")

    assert result.returncode == 0, result.stderr
    entry = not_examined(result.stdout)
    assert re.search(
        rf"{re.escape(str(host.temp))} — \d+ KiB across 4 entries, "
        r"and no verb here examines any of it\.",
        entry,
    ), entry
    assert re.search(rf"{NX_CACHE_FAMILY}\w+ and 2 more like it — [\d.]+ [KMGT]iB", entry), entry
    assert re.search(r"node-compile-cache — [\d.]+ KiB", entry), entry
    assert f"A {UV_LOCK_PATTERN} is out of that count" in entry, entry
    for directory in [*grouped, alone]:
        assert (directory / "payload").exists(), f"{directory} was reclaimed by this recipe"
    assert not dead.exists(), "the family the verbs do own went unswept beside it"


def test_what_oneagentgraph_owns_is_left_out_of_both_of_the_numbers(host: Host) -> None:
    """Nothing is counted in two families at once, which is what the trailer is for.

    A root holding one directory of each: an owned one four thousand times the size of
    the other, still on disk because this is a rehearsal. A count that included it
    would say two entries, and a size that included it would be reported in MiB — so
    the family would be reported as unexamined while a verb above reported examining
    it, which is the one outcome the two lists exist to rule out.
    """
    host.scratch("owned", held=True, payload=4 * 1024 * 1024)
    host.unowned("node-compile-cache")

    result = sweep(host, "--dry-run")

    assert result.returncode == 0, result.stderr
    entry = not_examined(result.stdout)
    assert re.search(rf"{re.escape(str(host.temp))} — \d+ KiB across 1 entries", entry), entry


def test_the_trailer_names_the_three_largest_name_groups_and_stops(host: Host) -> None:
    """Three groups, largest first, and the rest deliberately unnamed.

    A screenful of groups is the skimming the trailer is rationed against, and one
    group hides whether the rest of the root is a second producer or ten thousand small
    ones — so the count is three and the order is by size. Five groups are on this root
    and the two smallest have to be absent, which is the half of the rule that a
    listing printing everything would still pass.
    """
    host.unowned(f"{NX_CACHE_FAMILY}3a91b2c", payload=256 * 1024)
    host.unowned(f"{NX_CACHE_FAMILY}7f0d415", payload=256 * 1024)
    host.unowned("node-compile-cache", payload=512 * 1024)
    host.unowned("nds-audit", payload=192 * 1024)
    host.unowned("xwin-cache", payload=128 * 1024)
    # Not `pytest-of-nick`, which is the shape this host really carries but is also in
    # the path of every temporary directory pytest hands this journey.
    host.unowned("bun-install", payload=64 * 1024)

    result = sweep(host, "--dry-run")

    assert result.returncode == 0, result.stderr
    entry = not_examined(result.stdout)
    groups = [line.strip() for line in entry.splitlines() if line.startswith(" " * 6)]
    assert len(groups) == 3, groups
    assert groups[0].startswith(NX_CACHE_FAMILY), groups
    assert "and 1 more like it" in groups[0], groups
    assert groups[1].startswith("node-compile-cache — "), groups
    assert groups[2].startswith("nds-audit — "), groups
    assert "xwin-cache" not in entry, "the trailer named more groups than it rations"
    assert "bun-install" not in entry


def test_a_loose_file_is_in_both_numbers_even_though_it_is_in_no_name_group(
    host: Host,
) -> None:
    """A loose file fills a device as well as a directory does, so it is in the account.

    The name groups are built from `du -d 1`, which lists directories, so a root filled
    by one enormous file is in no group — and that is the whole of what it is missing
    from. It moves the count, which is of entries, and it moves the size, which is the
    root's. A family reported as one directory and a few KiB while a 4 MiB file sat
    beside it would be an account of part of the root reading as an account of the
    root, which is the failure this trailer exists to prevent one root up.

    It is also proof of the other half of criterion one: this sweep measures the file
    and does not touch it. Nothing here writes to this root or removes anything under
    it, so the file is still on disk, at its own length, afterwards.
    """
    host.unowned("node-compile-cache", payload=4096)
    loose = host.loose_file("core.20260823", payload=4 * 1024 * 1024)

    result = sweep(host, "--dry-run")

    assert result.returncode == 0, result.stderr
    entry = not_examined(result.stdout)
    assert re.search(rf"{re.escape(str(host.temp))} — \d+(\.\d+)? MiB across 2 entries", entry), (
        entry
    )
    assert re.search(r"node-compile-cache — \d+ KiB", entry), entry
    assert "core.20260823" not in entry, "a loose file is in the numbers, not in a group"
    assert loose.stat().st_size == 4 * 1024 * 1024, "a file this recipe only measures moved"


def test_the_lock_left_out_of_the_count_is_named_as_left_out_rather_than_dropped(
    host: Host,
) -> None:
    """The exclusion is disclosed where an operator reads it, not only in the source.

    A `uv-*.lock` is out of the count, and that is right: `uv run` holds one in this
    root for the length of each verb it runs, so counting it would leave the family
    non-empty on every host that has ever swept. But an exclusion nobody can see turns
    a count of part of the root into an account of the root, which is the same
    all-clear-shaped answer this trailer exists to stop giving one root up. So the
    report names it and says why, and both halves are held here: the lock is still out
    of the count, and the count now says so.

    It is out of the count and of nothing else, which is the other half of what the
    report has to get right: the size is the root's less the family a verb above
    examined, so a lock's bytes are in it. A real one has none, and the lock below is
    given 4 MiB precisely so that a size which quietly dropped them would report KiB
    here and fail.

    The root also holds however many locks the recipe's own two `uv run`s take on their
    way past, every one of them out of the count too, which is why the count is of the
    single directory beside them.
    """
    host.unowned("node-compile-cache")
    lock = host.loose_file(UV_LOCK_TRANSIENT, payload=4 * 1024 * 1024)

    result = sweep(host, "--dry-run")

    assert result.returncode == 0, result.stderr
    entry = not_examined(result.stdout)
    assert re.search(rf"{re.escape(str(host.temp))} — \d+(\.\d+)? MiB across 1 entries", entry), (
        entry
    )
    assert f"A {UV_LOCK_PATTERN} is out of that count" in entry, entry
    assert "bytes stay in the size above" in entry, entry
    assert lock.stat().st_size == 4 * 1024 * 1024, "a file this recipe only measures moved"


def test_a_scratch_root_holding_only_examined_scratch_and_this_recipes_lock_is_quiet(
    host: Host,
) -> None:
    """The quiet form survives the new family, and is quiet for the right reason.

    A root whose every entry either belongs to a verb that examined it or was written
    by this recipe on its way there leaves nothing for an operator to do, so it is not
    a family and the sweep stays in its short form. Reporting it anyway would put a section in
    front of a reader at every dispatch start, and what a reader learns to skim past is
    the trailer that names the family nothing looked at — which is the whole thing this
    rationing protects.

    The lock file below is the reason the count has an exclusion at all, and it is what
    `uv run` really leaves here on the way to each verb rather than a shape invented
    for this journey: counted, this family would be non-empty on every host that has
    ever run the recipe and the short form below would be unreachable rather than
    rationed. It is the *only* exclusion — the journey above puts an ordinary loose
    file in the same root and it moves the count.
    """
    dead = host.scratch("dead", held=False)
    loose = host.loose_file(UV_LOCK_TRANSIENT)

    result = sweep(host, "--min-age-hours", "0")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == short_form(reclaimed(1, 1))
    assert str(host.temp) not in result.stdout
    assert loose.exists(), "a file this recipe only measures was removed"
    assert not dead.exists(), "the short form came from a sweep that reclaimed nothing"


def test_the_floor_this_recipe_passes_by_default_is_one_both_verbs_apply(
    host: Host,
) -> None:
    """The default is the recipe's own, and both verbs have to take it and obey it.

    Both verbs default to 24 hours, and on this host that reclaimed 0 B while four
    hours reclaimed 23.9 GB: the families churn hourly, so a floor nothing is ever
    older than never fires, and a sweep that never fires reports success while the
    device fills. So the recipe names four rather than leaving the verbs to their own
    default — and a number only one of them accepts, or applies differently, would be
    worse than no default at all. Every claim here is read from behaviour: a
    directory either side of the floor, in each verb's own families.
    """
    inside = host.scratch("inside", held=False, age_hours=3)
    outside = host.scratch("outside", held=False, age_hours=5)
    kept = host.workspace("onevcs-s-aaaaaaaaaaaa", finished=True, age_hours=3)
    swept = host.workspace("onevcs-s-bbbbbbbbbbbb", finished=True, age_hours=5)

    result = sweep(host, "--dry-run")

    assert result.returncode == 0, result.stderr
    assert f"would reclaim {outside}" in result.stdout
    assert f"{inside} was written" in result.stdout
    assert "inside this sweep's 14400s floor" in result.stdout
    assert "keeping anything written inside the last 4 hour(s)" in result.stdout
    assert f"{swept} — " in result.stdout.partition("Retained:")[0]
    assert f"{kept} — it was written 3 hour(s) ago" in result.stdout


@pytest.mark.parametrize(
    ("arguments", "floor"),
    [
        ((), "4 hour(s)"),
        (("--min-age-hours", "0"), "0 second(s)"),
        (("--min-age-hours=0",), "0 second(s)"),
    ],
    ids=("default", "spaced", "joined"),
)
def test_the_age_floor_means_one_thing_across_every_family(
    host: Host, arguments: tuple[str, ...], floor: str
) -> None:
    """One `--min-age-hours` reaches both verbs, in either spelling, and both obey it.

    The `oneagentgraph` half is read from its decision rather than from its echo: a
    directory whose owner is gone is inside the default floor and outside a zero one, so
    the same scratch is retained under one and reclaimed under the other. The default
    row is this recipe's own four hours rather than the verbs' twenty-four; what makes
    that a floor both of them take, and apply, is the journey above.
    """
    dead = host.scratch("dead", held=False)

    result = sweep(host, "--dry-run", *arguments)

    assert result.returncode == 0, result.stderr
    assert f"keeping anything written inside the last {floor}" in result.stdout
    reclaimed = f"would reclaim {dead}" in result.stdout
    assert reclaimed is (floor == "0 second(s)")


def test_a_verb_that_fails_leaves_its_families_in_the_not_examined_list(host: Host) -> None:
    """The recovery path: one sweeper down must not cost the other its reclamation.

    A `workspaces` that is a file is what a half-provisioned or hand-edited state root
    looks like, and `onevcs sweep` refuses it outright. The families it owns then move
    into the not-examined list — never quietly out of both — the other verb still sweeps,
    and the recipe's status says a family was left unswept.
    """
    (host.onevcs_home / "workspaces").rmdir()
    (host.onevcs_home / "workspaces").write_text("not a directory\n")
    dead = host.scratch("dead", held=False)

    result = sweep(host, "--min-age-hours", "0")

    assert result.returncode == 1, result.stdout
    assert not dead.exists(), "a failing verb stopped the other one from reclaiming"
    for section in SECTIONS:
        assert section in result.stdout, "a failed verb cost the reports that explain it"
    for family in ONEVCS_FAMILIES:
        assert family in not_examined(result.stdout)
        assert family not in examined(result.stdout)
    for family in ONEAGENTGRAPH_FAMILIES:
        assert family in examined(result.stdout)


def test_both_of_onevcss_families_are_reclaimed_for_real(host: Host) -> None:
    """The half of the sweep the rename is about, removing for real in both families.

    `onevcs sweep` owns two families and this composition is the only reason either is
    reclaimed here at all, so neither is proven by a report. Both roots below are real
    shared run clones carrying a recorded gate verdict, and the claim is that they are
    off the disk afterwards. The third has no verdict under `gate-logs` — nothing there
    can say its publication finished — and its survival is what keeps the two removals
    from passing because everything in reach was swept.
    """
    reclaimable = [
        host.workspace("onevcs-s-aaaaaaaaaaaa", finished=True, family=family)
        for family in ONEVCS_FAMILIES
    ]
    unfinished = host.workspace("onevcs-s-bbbbbbbbbbbb", finished=False)

    # The rehearsal reads the report — every root named, in both families, with the
    # retention that keeps the third — and the pass after it reads the disk.
    rehearsal = sweep(host, "--dry-run", "--min-age-hours", "0")
    assert rehearsal.returncode == 0, rehearsal.stderr
    for root in [*reclaimable, unfinished]:
        assert str(root) in rehearsal.stdout
    assert "its gate has recorded no verdict" in rehearsal.stdout
    for family in ONEVCS_FAMILIES:
        assert family in examined(rehearsal.stdout)

    result = sweep(host, "--min-age-hours", "0")

    assert result.returncode == 0, result.stderr
    for root in reclaimable:
        assert not root.exists(), f"{root} survived a sweep that had every reason to take it"
    assert unfinished.exists(), "a workspace whose gate recorded no verdict was reclaimed"


def test_a_sweep_whose_every_verb_failed_says_so_rather_than_listing_nothing(
    host: Host,
) -> None:
    """An empty `Families examined:` reads as a formatting artefact, not as an answer.

    Both refusals are the verbs' own — `oneagentgraph sweep` rejects a fractional hour,
    and `onevcs sweep` refuses a `workspaces` that is a file — so this is the real state
    of a host where nothing got swept, and the sentence in place of the empty list is
    what an operator watching a filling disk has to be told.
    """
    (host.onevcs_home / "workspaces").rmdir()
    (host.onevcs_home / "workspaces").write_text("not a directory\n")
    dead = host.scratch("dead", held=False)

    result = sweep(host, "--min-age-hours", "1.5")

    assert result.returncode == 1, result.stdout
    assert "none — every sweeper this recipe composes failed" in examined(result.stdout)
    for family in ONEAGENTGRAPH_FAMILIES + ONEVCS_FAMILIES:
        assert family in not_examined(result.stdout)
    # Each half says what to do about itself, so a report of two unswept families is
    # still actionable.
    assert "Re-run uv run oneagentgraph sweep" in not_examined(result.stdout)
    assert "Re-run uv run onevcs sweep" in not_examined(result.stdout)
    assert dead.exists(), "a sweep in which nothing ran removed something"


def test_the_other_verb_still_sweeps_when_oneagentgraph_is_the_one_that_fails(
    host: Host,
) -> None:
    """The honesty property from the other side, driven by a refusal that is really theirs.

    The two verbs do not accept the same numbers: measured on the adopted releases,
    `onevcs sweep` takes a fractional hour and `oneagentgraph sweep` refuses one. The
    wrapper validates the option's shape and lets the verbs judge its value, so
    `--min-age-hours 1.5` is a real half-sweep — and a half-sweep must read as one.
    `oneagentgraph`'s families move into the not-examined list carrying the status
    that put them there, `onevcs` still examines its own and reports what it retained,
    and the recipe exits non-zero so the gap is in the status and not only in the
    report. The failure a family disappears from *both* lists is the one thing this
    whole composition exists to rule out.
    """
    workspace = host.workspace("onevcs-s-aaaaaaaaaaaa", finished=False)
    dead = host.scratch("dead", held=False)

    result = sweep(host, "--min-age-hours", "1.5")

    assert result.returncode == 1, result.stdout
    assert "invalid value '1.5' for '--min-age-hours" in result.stderr
    assert "oneagentgraph sweep exited 2" in not_examined(result.stdout)
    for family in ONEAGENTGRAPH_FAMILIES:
        assert family in not_examined(result.stdout)
        assert family not in examined(result.stdout)
    for family in ONEVCS_FAMILIES:
        assert family in examined(result.stdout)
        assert family not in not_examined(result.stdout)
    # onevcs did not merely print a header: it read this journey's own state and named
    # what it found there.
    assert str(workspace) in result.stdout
    assert dead.exists(), "the refused verb swept anyway"


def test_the_two_age_floors_the_composed_help_claims_are_the_ones_the_verbs_have(
    host: Host,
) -> None:
    """The help states a floor of its own and one of theirs, and both are asked.

    `--min-age-hours` is forwarded rather than interpreted here, so the help makes two
    claims about two other repositories: that they still default to twenty-four when
    nobody passes one — the number this recipe's own default is chosen against — and
    that each of them takes the number it passes instead. `oneagentgraph sweep` refuses
    a fractional hour where `onevcs sweep` takes one, so a default only one of them
    accepts is a half-sweep at every bare invocation, and it would be this recipe that
    caused it. Neither claim is observable from the age journeys above, which read one
    verb's echo and the other's behaviour rather than what either declares.
    """
    help_text = sweep(host, "--help").stdout
    theirs = DEFAULT_AGE_CLAIM.search(help_text)
    assert theirs is not None, "the composed help no longer claims a shared default age"
    ours = RECIPE_DEFAULT_AGE.search(help_text)
    assert ours is not None, "the composed help no longer names the floor it passes"

    for verb in ("oneagentgraph", "onevcs"):
        declared = subprocess.run(
            ["uv", "run", verb, "sweep", "--help"],
            cwd=REPO_ROOT,
            env=host.environment,
            check=True,
            text=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=e2e_timeout(120),
        )
        assert f"[default: {theirs.group(1)}]" in declared.stdout, (
            f"{verb} sweep no longer defaults to the {theirs.group(1)} hours "
            "just sweep --help tells an operator both verbs do"
        )
        taken = subprocess.run(
            ["uv", "run", verb, "sweep", "--dry-run", "--min-age-hours", ours.group(1)],
            cwd=REPO_ROOT,
            env=host.environment,
            check=False,
            text=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=e2e_timeout(120),
        )
        assert taken.returncode == 0, (
            f"{verb} sweep refuses the {ours.group(1)} hours this recipe passes when "
            f"the caller names none: {taken.stderr}"
        )


@pytest.mark.parametrize("flag", ["--help", "-h"], ids=("long", "short"))
def test_asking_for_help_answers_and_sweeps_nothing(host: Host, flag: str) -> None:
    """Help is a question, not a sweep — including on a host whose scratch is reclaimable.

    A `--help` that fell through to the verbs would remove directories on the way to
    printing its own usage, which is the worst possible reading of an operator asking
    what the command does.
    """
    dead = host.scratch("dead", held=False)

    result = sweep(host, flag)

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("Usage: just sweep [--dry-run] [--min-age-hours HOURS]")
    assert "--min-age-hours HOURS" in result.stdout
    assert "=== oneagentgraph sweep" not in result.stdout, "help reached the verbs"
    assert "=== just sweep — what this run looked at ===" not in result.stdout
    assert dead.exists(), "asking for help swept"


#: A root nothing can read is the one way to make `find` and `du` fail here without
#: substituting either, and root reads a mode-000 directory regardless. The journeys
#: below say so rather than passing vacuously.
needs_unprivileged = pytest.mark.skipif(
    os.geteuid() == 0,
    reason="root reads a mode-000 directory, so the unreadable root these prove cannot exist",
)


@needs_unprivileged
def test_a_legacy_root_this_sweep_cannot_read_is_still_named_and_the_trailer_still_prints(
    host: Host,
) -> None:
    """The measurement that fails must not take the report with it.

    This root is the family least likely to be readable — its directories belong to
    other checkouts — and it is the last thing the run touches. Under `set -euo
    pipefail` a `find` that cannot walk it aborted the whole sweep *after* both verbs
    had already run, discarding their reports and this trailer and leaving the
    operator `find`'s bare errno. So the numbers degrade, the root is still named,
    and the exit still belongs to the verbs.
    """
    host.legacy_worktree("nickderobertis__llmlint")
    host.worktrees.chmod(0o000)
    try:
        result = sweep(host, "--dry-run")
    finally:
        host.worktrees.chmod(0o755)

    assert result.returncode == 0, result.stderr
    assert (
        f"{host.worktrees} — an unmeasurable size across an unreadable number of directories."
        in not_examined(result.stdout)
    )
    assert "check the root with ls -ld" in not_examined(result.stdout)
    for family in ONEAGENTGRAPH_FAMILIES + ONEVCS_FAMILIES:
        assert family in examined(result.stdout), "a failed measurement cost a verb its report"


@needs_unprivileged
def test_a_legacy_root_whose_size_cannot_be_measured_keeps_the_count_it_could_get(
    host: Host,
) -> None:
    """One unreadable worktree makes `du`'s total partial, and a partial total is not one.

    The count is a listing of the root itself and still answers, so the entry carries
    the number it has and names the one it does not. Reporting the partial figure
    instead would understate exactly the family this trailer exists to stop
    understating.
    """
    unreadable = host.legacy_worktree("nickderobertis__llmlint")
    host.legacy_worktree("nickderobertis__onevcs")
    unreadable.chmod(0o000)
    try:
        result = sweep(host, "--dry-run")
    finally:
        unreadable.chmod(0o755)

    assert result.returncode == 0, result.stderr
    assert f"{host.worktrees} — an unmeasurable size across 2 directories." in not_examined(
        result.stdout
    )
    assert "check the root with ls -ld" in not_examined(result.stdout)


@needs_unprivileged
def test_a_scratch_root_this_sweep_cannot_read_is_still_named_and_nothing_is_lost(
    host: Host,
) -> None:
    """The measurement that fails must not take the sweep with it.

    This root is walked last, after both verbs have already swept and after the
    trailer's other measurement, so under `set -euo pipefail` a `find` or `du` that
    cannot read it would abort the run *there* — discarding both reports, the trailer,
    and the other family it names, and leaving the operator a bare errno. So each
    number degrades to a phrase, the root is still named, and the exit still belongs
    to the verbs.
    """
    host.unowned(f"{NX_CACHE_FAMILY}3a91b2c")
    host.temp.chmod(0o333)
    try:
        result = sweep(host, "--dry-run")
    finally:
        host.temp.chmod(0o755)

    assert result.returncode == 0, result.stderr
    assert (
        f"{host.temp} — an unmeasurable size across an unreadable number of entries"
        in not_examined(result.stdout)
    )
    assert "check the root with ls -ld" in not_examined(result.stdout)
    for section in SECTIONS:
        assert section in result.stdout, "a failed measurement cost a verb its report"
    for family in ONEVCS_FAMILIES:
        assert family in examined(result.stdout), "a failed measurement cost a verb its report"


@needs_unprivileged
def test_a_scratch_root_this_sweep_can_only_partly_read_reports_a_floor_not_a_total(
    host: Host,
) -> None:
    """A partial total is not a total, and the difference is the point of the number.

    The root itself lists, so the count answers and the size is as much of it as this
    sweep could reach. Printing that as *the* size would understate exactly the family
    the trailer exists to stop understating, so it is reported as a floor and says so.
    """
    unreadable = host.unowned(f"{NX_CACHE_FAMILY}3a91b2c")
    (unreadable / "inner").mkdir()
    (unreadable / "inner").chmod(0o000)
    try:
        result = sweep(host, "--dry-run")
    finally:
        (unreadable / "inner").chmod(0o755)

    assert result.returncode == 0, result.stderr
    assert re.search(
        rf"{re.escape(str(host.temp))} — at least [\d.]+ [KMGT]iB across 1 entries",
        not_examined(result.stdout),
    ), not_examined(result.stdout)
    assert "the family is at least this large" in not_examined(result.stdout)


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [
        (("--min-age-hours",), "needs a number of hours after it"),
        (("--min-age-hours", "yesterday"), "takes a number of hours, not 'yesterday'"),
        (("--min-age-hours=-1",), "takes a number of hours, not '-1'"),
        (("--force",), "unrecognized argument '--force'"),
    ],
    ids=("bare", "not-a-number", "negative", "unknown"),
)
def test_an_argument_neither_verb_takes_is_refused_before_either_runs(
    host: Host, arguments: tuple[str, ...], reason: str
) -> None:
    """One refusal naming what is accepted, rather than the same error from two verbs."""
    dead = host.scratch("dead", held=False)

    result = sweep(host, *arguments)

    assert result.returncode == 2, result.stdout
    assert reason in result.stderr
    assert dead.exists(), "a refused invocation swept anyway"


#: A `uv` that answers `uv run <verb> sweep` with a report whose *shape* is a released
#: verb's and whose count lines are not. This is what a release rewording one of them
#: looks like from the recipe: every section is there, the exit status is 0, and the two
#: numbers the composition reads out of them are gone.
REWORDED_UV = """#!/usr/bin/env bash
set -euo pipefail
if [ "${1:-}" != run ]; then exec /usr/bin/env "$@"; fi
case "${2:-}" in
  oneagentgraph)
    printf 'sweep: examined family "runs" at /somewhere — 3 folders\\n'
    printf 'sweep: examined family "temp" at /elsewhere — 1 folder\\n'
    printf 'sweep: reclaimed 0 B from 0 folders; examined: runs, temp; unexamined: none\\n'
    ;;
  onevcs)
    printf 'Families examined:\\n'
    printf '  publications — 2 roots in /somewhere\\n'
    printf '  recoveries — 0 roots in /elsewhere\\n'
    printf 'Families not examined:\\n  none\\n'
    printf 'Reclaimed:\\n  none\\n'
    ;;
esac
"""


# llmlint: ignore-block[e2e_not_mocked] The recipe, its parsing and its output are the
# real ones; what is substituted is `uv`, the boundary the recipe crosses to reach the
# two published verbs, and it is substituted with a report *only a future release could
# write*. That is the whole condition under test — a verb that succeeded and reworded a
# line — and it cannot be arranged with the installed verbs, which by construction still
# write the lines this recipe reads.
def test_a_report_whose_counts_cannot_be_read_prints_the_sections_rather_than_guessing(
    host: Host, tmp_path: Path
) -> None:
    """An unreadable count is a third answer, and it may not collapse into either of two.

    The composition tells "nothing reclaimed, candidates examined" from "nothing
    reclaimed, nothing examined" by reading counts out of each verb's own report. A
    release that rewords one of those lines leaves it reading zero, which is the second
    sentence — and that sentence, wrongly given, is an all-clear on a host where nothing
    was looked at. So it prints the verbs' own reports instead, which is where the
    operator can see what really happened.
    """
    stub = tmp_path / "stub-bin"
    stub.mkdir()
    (stub / "uv").write_text(REWORDED_UV, encoding="utf-8")
    (stub / "uv").chmod(0o755)
    environment = {**host.environment, "PATH": f"{stub}{os.pathsep}{host.environment['PATH']}"}

    result = subprocess.run(
        ["just", "sweep", "--min-age-hours", "0"],
        cwd=REPO_ROOT,
        env=environment,
        check=False,
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=e2e_timeout(120),
    )

    assert result.returncode == 0, result.stderr
    for verdict in (NOTHING_EXAMINED, nothing_reclaimed(0), reclaimed(0, 0)):
        assert verdict not in result.stdout, (
            "a report whose counts could not be read was answered with a verdict about "
            f"how many candidates were judged:\n{result.stdout}"
        )
    for section in SECTIONS:
        assert section in result.stdout, (
            f"an unreadable count cost the reports that are the answer:\n{result.stdout}"
        )
    assert "3 folders" in result.stdout, (
        "the verb's own report is not in front of the operator, so nothing here says "
        f"which line stopped being readable:\n{result.stdout}"
    )


# llmlint: ignore-end[e2e_not_mocked]
