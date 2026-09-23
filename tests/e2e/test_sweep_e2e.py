"""`just sweep` runs the two published sweep verbs, and each says what it did not reach.

The recipe runs `onevcs sweep` and then `oneagentgraph sweep`, each with the caller's
options and a four-hour floor when the caller names none, prints both verbs' own
reports, and fails when either verb did. Nothing is parsed: every family either verb
leaves alone is named in that verb's own "Families not examined" section with who owns
it, and these journeys hold both installed verbs' `--format json` to that contract — the
field set, and every `not_examined[]` entry carrying a non-empty `owner` — over a state
root holding a pool slot and a preserved unpublished branch, the two families a wrapper
here once had to name itself.

Nothing is doubled. The recipe and both published verbs are the real ones; what makes
that safe is isolation rather than substitution — `ONEAGENTGRAPH_STATE_DIR`, `TMPDIR` and
`ONEVCS_HOME` put every family this run judges inside `tmp_path`. The pool slot is one a
real `onevcs session open` placed and `session close` returned, and the preserved branch
is a real commit its origin does not have. What each recipe argument reaches is the
delegated-recipes table's row; what the verbs do with it is proven here.

What the command covers and why is docs/orchestration.md, "The recorded run".

llmlint: ignore-file[shell_test_tiers_stay_split] Which Nx project owns this module is
a property of the module, not of any journey in it: it has driven real host tools —
`just`, both published sweep verbs, real `git` — since it was written. Splitting it out
is a follow-up.
llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] Same site, same
follow-up: this module declares no marker-based tier and the project it sits in is
pre-existing.
llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] These journeys are not
expensive: every one runs on local state under `tmp_path` in under half a second
(`pytest --durations` over the module), spending no harness turn, no launch and no
network, so an edge of their own would save their tier nothing it pays for.
llmlint: ignore-file[tests_mirror_real_usage] Two states here have no interface that
produces them: a `temp`-family directory whose owner died or is alive — the journey that
holds one alive does so with a real process taking the real lock — and a `workspaces` that
is a file, which is what a half-provisioned onevcs state root looks like and the one way to
make the real verb refuse. The pool slot and the preserved branch are produced through
`onevcs register`, `session open` and `session close` and real git.
"""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path
from typing import TypedDict, cast

import pytest
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The installed verbs, named by path so a journey measures the release this checkout
#: pins rather than whichever copy another checkout left on the search path.
ONEVCS = REPO_ROOT / ".venv" / "bin" / "onevcs"
ONEAGENTGRAPH = REPO_ROOT / ".venv" / "bin" / "oneagentgraph"

#: The fields both verbs' JSON reports carry, as the sweeper nodes of the plan that
#: retired this repository's own composition state them.
REPORT_FIELDS = frozenset(
    {"schema_version", "verb", "dry_run", "min_age_hours", "examined", "not_examined", "totals"}
)

#: Each installed verb's whole field set: the contract's fields and the ones each adds
#: beyond it. Held by equality, so a field either release adds or drops fails here by
#: name rather than passing through a restatement nobody re-reads.
VERB_FIELDS = {
    "onevcs": REPORT_FIELDS | {"reclaimed", "retained", "root", "session_records"},
    "oneagentgraph": REPORT_FIELDS | {"reclaimed", "retained"},
}

#: The families onevcs names as not examined over an identity holding a pool and a
#: registered checkout: the two this repository's retired wrapper used to name itself.
OWNED_ELSEWHERE = frozenset({"pool", "preserved-branches"})

#: The prefix `oneagentgraph` writes its `temp` family under; a directory without it is
#: not a candidate.
TEMP_FAMILY_PREFIX = "oneagentgraph-"

#: The file the sweeper's ownership proof consults, and a pid no process on this host
#: has: its directory is the one that must be reclaimed, so that a journey asserting the
#: held one survived is not passing because nothing was swept at all.
OWNER_LOCK = "owner.lock"
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


def _git(*arguments: str, cwd: Path | None = None) -> str:
    """One git command, with an identity so a commit works under any host config."""
    return subprocess.run(
        ["git", "-c", "user.email=sweep@example.invalid", "-c", "user.name=sweep", *arguments],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=e2e_timeout(60),
    ).stdout


class SweepReport(TypedDict):
    """The fields of a sweep verb's `--format json` report these journeys read."""

    schema_version: int
    verb: str
    dry_run: bool
    min_age_hours: float
    examined: list[dict[str, object]]
    not_examined: list[dict[str, str]]
    totals: dict[str, int]


class Host:
    """The scratch roots one sweep judges, and the environment that points it at them."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path
        self.runs = tmp_path / "oneagentgraph-state"
        self.temp = tmp_path / "tmp"
        self.onevcs_home = tmp_path / "onevcs-home"
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
        }

    def onevcs(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(ONEVCS), *arguments],
            cwd=self.root,
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=e2e_timeout(120),
        )

    def identity_with_a_pool_slot_and_a_preserved_branch(self) -> Path:
        """A registered checkout holding an unpublished branch, and one warm pool slot.

        Every piece is made by the tool that owns it: the origin and the checkout by
        git, the registration by `onevcs register`, and the slot by a real `onevcs
        session open` placed on the pool and `session close` returning it — so the
        state root is one onevcs itself would recognise, not an outline of one.
        """
        origin = self.root / "origin.git"
        _git("init", "--quiet", "--bare", "--initial-branch=main", str(origin))
        checkout = self.root / "project"
        _git("clone", "--quiet", str(origin), str(checkout))
        _git("commit", "--quiet", "--allow-empty", "-m", "seed", cwd=checkout)
        _git("push", "--quiet", "origin", "HEAD:main", cwd=checkout)
        _git("checkout", "--quiet", "-b", "preserved", cwd=checkout)
        _git("commit", "--quiet", "--allow-empty", "-m", "unpublished", cwd=checkout)
        assert _git("log", "--oneline", "origin/main..preserved", cwd=checkout).strip(), (
            "the preserved branch carries no commit its origin lacks"
        )

        registered = self.onevcs("register", str(checkout))
        assert registered.returncode == 0, registered.stderr
        opened = self.onevcs("session", "open", str(checkout), "--pool", "1")
        assert opened.returncode == 0, opened.stderr
        session = json.loads(opened.stdout)
        assert "/pool/" in session["worktree"], (
            f"the session was not placed on a pool slot: {session}"
        )
        closed = self.onevcs("session", "close", session["token"])
        assert closed.returncode == 0, closed.stderr
        return checkout

    def scratch(self, name: str, *, held: bool) -> Path:
        """One `temp`-family directory, with an owner that is either alive or gone."""
        directory = self.temp / f"{TEMP_FAMILY_PREFIX}{name}"
        directory.mkdir()
        (directory / "payload").write_bytes(b"\0" * 4096)
        if not held:
            (directory / OWNER_LOCK).write_text(DEAD_OWNER)
        return directory


@pytest.fixture
def host(tmp_path: Path) -> Host:
    return Host(tmp_path)


def sweep(
    host: Host, *arguments: str, environment: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """The real recipe, against the roots this journey owns."""
    return subprocess.run(
        ["just", "sweep", *arguments],
        cwd=REPO_ROOT,
        env=host.environment if environment is None else environment,
        check=False,
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=e2e_timeout(180),
    )


def _report(verb: Path, host: Host) -> SweepReport:
    """One installed verb's JSON report over this journey's roots, as a rehearsal."""
    answered = subprocess.run(
        [str(verb), "sweep", "--dry-run", "--min-age-hours", "4", "--format", "json"],
        cwd=host.root,
        env=host.environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=e2e_timeout(120),
    )
    assert answered.returncode == 0, answered.stderr
    report = json.loads(answered.stdout)
    assert isinstance(report, dict), answered.stdout
    # The shape is what the journeys below assert field by field — a missing field fails
    # there by name — so the cast only lets the type checker read what they then check.
    return cast(SweepReport, report)


@pytest.mark.parametrize("verb", [ONEVCS, ONEAGENTGRAPH], ids=lambda verb: verb.name)
def test_each_verb_reports_every_family_it_left_with_its_owner(host: Host, verb: Path) -> None:
    """The contract this recipe prints on: every family named is examined or owned.

    Over a state root holding a pool slot and a preserved unpublished branch, both
    families onevcs deliberately leaves alone are named with who reaches them, and no
    `not_examined[]` entry of either verb — whichever families it names — lacks an owner:
    a family named with neither is what turns "reclaimed nothing" into an all-clear.
    """
    checkout = host.identity_with_a_pool_slot_and_a_preserved_branch()

    report = _report(verb, host)

    expected = VERB_FIELDS[verb.name]
    assert set(report) == expected, (
        f"`{verb.name} sweep --format json` lacks {sorted(expected - set(report))} "
        f"and adds {sorted(set(report) - expected)}"
    )
    assert report["verb"] == f"{verb.name} sweep", report["verb"]
    assert report["dry_run"] is True
    assert report["min_age_hours"] == 4
    assert isinstance(report["examined"], list) and report["examined"], report
    assert isinstance(report["totals"], dict), report
    unowned = [
        entry
        for entry in report["not_examined"]
        if not (isinstance(entry.get("owner"), str) and entry["owner"].strip())
    ]
    assert not unowned, f"{verb.name} names families not examined with no owner: {unowned}"
    if verb == ONEVCS:
        named = {entry["family"]: entry for entry in report["not_examined"]}
        assert set(named) >= OWNED_ELSEWHERE, (
            f"onevcs left {sorted(OWNED_ELSEWHERE - set(named))} unnamed over a state root "
            f"holding a pool slot and a preserved branch: {report['not_examined']}"
        )
        assert named["preserved-branches"]["path"] == str(checkout), named["preserved-branches"]


def test_the_recipe_prints_both_reports_onevcs_first_with_the_default_floor(
    host: Host,
) -> None:
    """Both verbs' own text reports, in order, each told the four-hour floor."""
    host.identity_with_a_pool_slot_and_a_preserved_branch()

    result = sweep(host, "--dry-run")

    assert result.returncode == 0, result.stderr
    reported = result.stdout + result.stderr
    onevcs_at = reported.find("Families not examined:")
    oneagentgraph_at = reported.find('sweep: examined family "runs"')
    assert onevcs_at != -1, f"onevcs's report is not in the output:\n{reported}"
    assert oneagentgraph_at != -1, f"oneagentgraph's report is not in the output:\n{reported}"
    assert onevcs_at < oneagentgraph_at, f"oneagentgraph's report came first:\n{reported}"
    assert "pool, reached by `onevcs pool status project`" in reported, reported
    assert "preserved-branches, reached by `onevcs recoverable --repo project`" in reported
    assert str(host.temp) in reported and str(host.runs) in reported, reported
    # The floor both verbs were given: onevcs says it of the session record the slot's
    # session left, which it keeps as inside the floor.
    assert "inside the 4 hour(s) the age floor leaves alone" in reported, reported


def test_a_floor_the_caller_names_replaces_the_default(host: Host) -> None:
    """`--min-age-hours 0` reaches both verbs in place of the four hours."""
    host.identity_with_a_pool_slot_and_a_preserved_branch()

    result = sweep(host, "--dry-run", "--min-age-hours", "0")

    assert result.returncode == 0, result.stderr
    reported = result.stdout + result.stderr
    assert "the age floor leaves alone" not in reported, (
        f"a verb still applied a floor the caller replaced with 0:\n{reported}"
    )


def test_one_verb_failing_leaves_the_other_sweeping_and_fails_the_recipe(host: Host) -> None:
    """onevcs refusing its state root costs oneagentgraph nothing, and the status says so.

    A `workspaces` that is a file is what a half-provisioned onevcs state root looks
    like, and the real verb refuses it.
    """
    (host.onevcs_home / "workspaces").rmdir()
    (host.onevcs_home / "workspaces").write_text("not a directory\n", encoding="utf-8")
    dead = host.scratch("dead", held=False)

    result = sweep(host, "--min-age-hours", "0")

    assert result.returncode != 0, "the recipe hid a verb's failure"
    assert "cannot read the workspaces under" in result.stderr, result.stderr
    assert not dead.exists(), "oneagentgraph did not sweep after onevcs failed"


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
        # than sweeping the operator's scratch.
        rehearsal = sweep(host, "--dry-run", "--min-age-hours", "0")
        assert rehearsal.returncode == 0, rehearsal.stderr
        rehearsed = rehearsal.stdout + rehearsal.stderr
        assert str(host.temp) in rehearsed, rehearsed

        result = sweep(host, "--min-age-hours", "0")

        assert result.returncode == 0, result.stderr
        assert held.exists(), "a directory a live process holds was removed"
        assert not dead.exists(), "the dead directory beside it was not reclaimed"
    finally:
        holder.terminate()
        holder.wait(timeout=e2e_timeout(30))
