"""`just sweep` reclaims every family this host accumulates, and says what it did not.

Nothing here is doubled. The recipe, the wrapper, and both published verbs are the real
ones; what makes that safe is isolation rather than substitution — `ONEAGENTGRAPH_STATE_DIR`,
`TMPDIR`, and `ONEVCS_HOME` put every family this run judges inside `tmp_path`, and
`AI_ORCHESTRATOR_HOME` does the same for the legacy worktree root the trailer reports on.

A sweep with nothing to act on is one line, so several journeys below rehearse with
`--dry-run` first and then sweep for real: the rehearsal is where the reports are read,
and the real pass is where the disk and the one line are. `--dry-run` keeps its report
because it removes nothing and is asked in order to be answered.

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
from pathlib import Path

import pytest
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The wrapper whose trailer is the subject of most of this module.
WRAPPER = REPO_ROOT / "scripts" / "sweep.sh"


def _declared_families(constant: str) -> tuple[str, ...]:
    """The families the trailer claims for one verb, read out of the wrapper itself.

    Read rather than restated, because a restatement would let the wrapper and this
    module drift apart while both stayed green — and it is the wrapper's claim, not
    this module's, that the journeys below hold against what the verbs report.
    """
    for line in WRAPPER.read_text().splitlines():
        name, separator, value = line.partition("=")
        if name == constant and separator:
            return tuple(family.strip() for family in value.strip("'").split(","))
    raise AssertionError(f"{WRAPPER.name} declares no {constant}")


#: The families each verb owns, as the trailer claims them. Held against what the
#: installed verbs report examining, so a release that adds or renames a family fails
#: here rather than leaving the trailer describing the previous one.
ONEAGENTGRAPH_FAMILIES = _declared_families("ONEAGENTGRAPH_FAMILIES")
ONEVCS_FAMILIES = _declared_families("ONEVCS_FAMILIES")

#: The whole of a sweep that examined every family and left nothing to act on. Composed
#: from the families declared above rather than pasted, so a release that renames one
#: fails here too instead of leaving the short form describing the previous release.
SHORT_FORM = (
    "just sweep: nothing to act on — every family examined: "
    f"oneagentgraph {', '.join(ONEAGENTGRAPH_FAMILIES)}; "
    f"onevcs {', '.join(ONEVCS_FAMILIES)}."
)

#: Every section heading of the long form — both verbs' and the trailer's — which the
#: composition prints only when something is left for an operator to act on.
SECTIONS = (
    "=== oneagentgraph sweep — the scratch a dispatch leaves behind ===",
    "=== onevcs sweep — the workspaces a publication leaves behind ===",
    "=== just sweep — what this run looked at ===",
)

#: The one number the composed help restates that neither verb owns: the age floor
#: both of them default to. Matched against what each installed verb declares, below.
DEFAULT_AGE_CLAIM = re.compile(r"Both verbs default to (\d+) ")

#: The prefix `oneagentgraph` gives the scratch it writes under `TMPDIR`. A directory
#: without it is not in the `temp` family and is not a candidate.
TEMP_FAMILY_PREFIX = "oneagentgraph-"

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

    def scratch(self, name: str, *, held: bool) -> Path:
        """One `temp`-family directory, with an owner that is either alive or gone."""
        directory = self.temp / f"{TEMP_FAMILY_PREFIX}{name}"
        directory.mkdir()
        (directory / "payload").write_bytes(b"\0" * 4096)
        if not held:
            (directory / OWNER_LOCK).write_text(DEAD_OWNER)
        return directory

    def _lender(self) -> Path:
        """The checkout a run clone borrows its objects from, cut once per host."""
        lender = self.root / "lender"
        if not lender.exists():
            _git("init", "--quiet", str(lender))
            _git("commit", "--quiet", "--allow-empty", "-m", "lender", cwd=lender)
        return lender

    def workspace(self, session: str, *, finished: bool, family: str = "publications") -> Path:
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
        # because the real pass below has nothing to act on and so says one line.
        rehearsal = sweep(host, "--dry-run", "--min-age-hours", "0")
        assert rehearsal.returncode == 0, rehearsal.stderr
        assert str(host.temp) in rehearsal.stdout
        assert str(host.runs) in rehearsal.stdout
        assert "is still locked by its owner" in rehearsal.stdout

        result = sweep(host, "--min-age-hours", "0")

        assert result.returncode == 0, result.stderr
        assert held.exists(), "a directory a live process holds was removed"
        assert not dead.exists(), "the dead directory beside it was not reclaimed"
        assert result.stdout.splitlines() == [SHORT_FORM]
    finally:
        holder.terminate()
        holder.wait(timeout=e2e_timeout(30))


def test_the_report_names_every_family_examined_and_every_family_it_could_not(
    host: Host,
) -> None:
    """Each family appears in exactly one of the two lists, so neither can hide one."""
    host.workspace("onevcs-s-aaaaaaaaaaaa", finished=False)
    host.legacy_worktree("nickderobertis__llmlint")

    result = sweep(host, "--dry-run")

    assert result.returncode == 0, result.stderr
    for family in ONEAGENTGRAPH_FAMILIES + ONEVCS_FAMILIES:
        assert family in examined(result.stdout), f"{family} is in neither list"
        assert family not in not_examined(result.stdout), f"{family} is in both lists"
    assert str(host.worktrees) in not_examined(result.stdout)


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


def test_a_sweep_with_nothing_left_to_act_on_is_one_line(host: Host) -> None:
    """Coverage was complete and nothing here is an operator's, so the sections stay away.

    Proven on a pass that really reclaimed rather than on an idle host, because the
    claim is about a *working* sweep being quiet — one that swept nothing would print
    the same line for the wrong reason. The line still names all four families, so the
    short form is an account of the scope and not an unqualified all-clear.
    """
    dead = host.scratch("dead", held=False)

    result = sweep(host, "--min-age-hours", "0")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [SHORT_FORM]
    assert not dead.exists(), "the one line came from a sweep that reclaimed nothing"


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
    assert SHORT_FORM not in result.stdout
    assert legacy.exists(), "a root this sweep only reports on was reclaimed"
    assert not dead.exists(), "the families the verbs do own went unswept"


@pytest.mark.parametrize(
    ("arguments", "floor"),
    [
        ((), "24 hour(s)"),
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
    the same scratch is retained under one and reclaimed under the other.
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


def test_the_default_age_the_composed_help_claims_is_the_one_both_verbs_declare(
    host: Host,
) -> None:
    """The help restates a floor neither verb owns, so both are asked whether it is theirs.

    `--min-age-hours` is forwarded rather than interpreted here, so that number is a
    claim about two other repositories' defaults, and a release that moved either one
    would leave an operator reading a floor no verb has. The age journey above cannot
    catch that: it reads the *echo* of the default from one verb and its *behaviour*
    from the other, and neither of those is the number this help prints.
    """
    claimed = DEFAULT_AGE_CLAIM.search(sweep(host, "--help").stdout)
    assert claimed is not None, "the composed help no longer claims a shared default age"

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
        assert f"[default: {claimed.group(1)}]" in declared.stdout, (
            f"{verb} sweep no longer defaults to the {claimed.group(1)} hours "
            "just sweep --help tells an operator both verbs do"
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
