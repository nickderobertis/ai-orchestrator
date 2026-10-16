"""`just sweep` runs the two published sweep verbs, and each says what it did not reach.

The recipe runs `onevcs sweep` and then `oneagentgraph sweep`, each with the caller's
options and a four-hour floor when the caller names none, prints both verbs' own
reports, and fails when either verb did. Nothing is parsed: every family either verb
leaves alone is named in that verb's own "Families not examined" section with who owns
it, and these journeys hold both installed verbs' `--format json` to that contract — the
field set, and every `not_examined[]` entry carrying a non-empty `owner` — over a state
root holding a pool slot and a preserved unpublished branch, the two families a wrapper
here once had to name itself.

Nothing but the remote host is doubled, and only where a journey has one. The recipe and
both published verbs are the real ones; what makes
that safe is isolation rather than substitution — `ONEAGENTGRAPH_STATE_DIR`, `TMPDIR` and
`ONEVCS_HOME` put every family this run judges inside `tmp_path`. The pool slot is one a
real `onevcs session open` placed and `session close` returned, and the preserved branch
is a real commit its origin does not have. What each recipe argument reaches is the
delegated-recipes table's row; what the verbs do with it is proven here.

The finished-branches pass records each branch's verdict under everything its derivation
read, and a later pass over unchanged inputs reuses it instead of re-deriving it — the
onevcs 0.35.0 contract, `derivation: derived | reused` on every `finished-branches` entry.
One journey drives the real recipe twice over a registered checkout holding a branch the
pass keeps and a session branch it can prove retirable, and reads that field.

Two hosted identities publishing one branch name must each keep their own open change
request through a sweep — the retirement fix onevcs 0.49.0 carries — while a branch whose
own change request merged at its head is retired. One journey holds that on the installed
verb through the real recipe, with only the remote host's decisioning substituted, by
`tests/e2e/fake_gh.py` through `ONEVCS_GH`.

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
`onevcs register`, `session open` and `session close` and real git. A third has no
interface any more: the legacy branch-keyed `publish-branch-<branch>` stream whose events
carry no `labels.identity`, one stream for every repository publishing that branch name,
which every onevcs since 0.49.0 writes labelled. The journey over two hosted identities
writes those records directly, in the shape this host's streams hold, because they are
the input the retirement fix it proves is about; the branches, sessions, origins and
change requests beside them are made by `onevcs`, real git and the host double.
"""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path
from typing import Literal, NamedTuple, TypedDict, cast

import fake_gh
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
    "onevcs": REPORT_FIELDS
    | {"reclaimed", "retained", "root", "session_records", "finished_branches"},
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


Classification = Literal["keep", "retirable"]
Derivation = Literal["derived", "reused"]


class Verdict(NamedTuple):
    """The verdict a pass must reach for one branch: its class, and the word that says why.

    ``why`` is the keep reason for a kept branch and the proof's kind for a retirable
    one, the two places the report spells what decided the class.
    """

    classification: Classification
    why: str


class RetirementProof(TypedDict):
    """The part of a retirable entry's proof these journeys read."""

    kind: str


#: The fields of one `finished-branches` entry the verdict-reuse journey reads, in the
#: functional form because the report's own `class` is a Python keyword.
FinishedBranch = TypedDict(
    "FinishedBranch",
    {
        "branch": str,
        "class": Classification,
        "reason": str | None,
        "proof": RetirementProof | None,
        "derivation": Derivation,
    },
)


def _verdict(entry: FinishedBranch) -> Verdict:
    """The verdict one entry reports, spelled as the fixture's expectations are."""
    proof = entry["proof"]
    return Verdict(entry["class"], str(entry["reason"] if proof is None else proof["kind"]))


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
            # The recipe takes the host sweep lock under the cache home; a journey's own
            # keeps it off this host's, which a real sweep may be holding.
            "XDG_CACHE_HOME": str(self.root / "cache"),
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

    def identity_with_a_kept_and_a_retirable_branch(self) -> dict[str, Verdict]:
        """A registered checkout whose finished-branches pass keeps one branch and retires one.

        The kept branch is local work its origin never received, which the pass names
        because it holds commits no origin ref has and keeps as unmerged. The retirable
        one is a real session's branch whose change reached the base as a squash commit
        rather than as its own commit, which is how a `local-direct` publication lands:
        the base does not carry its tip, so only the content proof can retire it. Every
        piece is made by the tool that owns it — git, `onevcs register`, `session open`
        and `session close` — and the map is each branch's expected verdict.
        """
        origin = self.root / "origin.git"
        _git("init", "--quiet", "--bare", "--initial-branch=main", str(origin))
        checkout = self.root / "project"
        _git("clone", "--quiet", str(origin), str(checkout))
        _git("commit", "--quiet", "--allow-empty", "-m", "seed", cwd=checkout)
        _git("push", "--quiet", "origin", "HEAD:main", cwd=checkout)
        _git("checkout", "--quiet", "-b", "unfinished", cwd=checkout)
        (checkout / "unfinished.txt").write_text("not landed\n", encoding="utf-8")
        _git("add", "unfinished.txt", cwd=checkout)
        _git("commit", "--quiet", "-m", "unfinished work", cwd=checkout)
        _git("checkout", "--quiet", "main", cwd=checkout)

        registered = self.onevcs("register", str(checkout))
        assert registered.returncode == 0, registered.stderr
        opened = self.onevcs("session", "open", str(checkout))
        assert opened.returncode == 0, opened.stderr
        session = json.loads(opened.stdout)
        worktree = Path(session["worktree"])
        (worktree / "landed.txt").write_text("landed by squash\n", encoding="utf-8")
        _git("add", "landed.txt", cwd=worktree)
        _git("commit", "--quiet", "-m", "landed work", cwd=worktree)
        closed = self.onevcs("session", "close", session["token"])
        assert closed.returncode == 0, closed.stderr

        # The squash: the same content as a new commit on the base, pushed to the origin.
        _git("pull", "--quiet", "--ff-only", "origin", "main", cwd=checkout)
        (checkout / "landed.txt").write_text("landed by squash\n", encoding="utf-8")
        _git("add", "landed.txt", cwd=checkout)
        _git("commit", "--quiet", "-m", "landed work (squashed)", cwd=checkout)
        _git("push", "--quiet", "origin", "HEAD:main", cwd=checkout)
        return {
            "unfinished": Verdict("keep", "unmerged-unique-commits"),
            session["branch"]: Verdict("retirable", "content-identical"),
        }

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


def _finished_branches(result: subprocess.CompletedProcess[str]) -> dict[str, FinishedBranch]:
    """The `finished-branches` entries of the onevcs report a `--format json` recipe run printed.

    The recipe prints onevcs's document and then oneagentgraph's on one stdout, so the
    first JSON value is the one read, and its `verb` is checked rather than assumed.
    """
    assert result.returncode == 0, result.stderr
    report, _ = json.JSONDecoder().raw_decode(result.stdout.lstrip())
    assert report["verb"] == "onevcs sweep", f"the first report is not onevcs's: {report}"
    # The fields the journey reads are asserted on by name there, so a release that drops
    # one fails by name; the cast only lets the type checker follow them.
    entries = cast(list[FinishedBranch], report["finished_branches"]["examined"])
    return {entry["branch"]: entry for entry in entries}


def test_a_second_pass_over_unchanged_branches_reuses_every_recorded_verdict(
    host: Host,
) -> None:
    """The fix `just sweep` carries from onevcs 0.35.0, proven on the installed verb.

    The first pass derives each branch's verdict and records it — a dry run records too —
    and the second, over nothing changed, answers every one from that record. The
    fixture holds a branch kept and a branch proven retirable, so a family that came back
    empty, dropped a branch, or re-derived one fails here by name.
    """
    expected = host.identity_with_a_kept_and_a_retirable_branch()

    first = _finished_branches(sweep(host, "--dry-run", "--format", "json"))
    second = _finished_branches(sweep(host, "--dry-run", "--format", "json"))

    assert first, "the finished-branches pass examined nothing"
    assert set(first) == set(expected), (
        f"the first pass examined {sorted(first)}, not {sorted(expected)}"
    )
    assert set(second) == set(first), (
        f"the second pass examined {sorted(second)} where the first examined {sorted(first)}"
    )
    for branch, verdict in expected.items():
        assert _verdict(first[branch]) == verdict, f"{branch} was classified {first[branch]}"
    assert {branch: entry["derivation"] for branch, entry in first.items()} == dict.fromkeys(
        first, "derived"
    ), f"the first pass did not derive every verdict: {first}"
    assert {branch: entry["derivation"] for branch, entry in second.items()} == dict.fromkeys(
        second, "reused"
    ), f"the second pass re-derived a verdict nothing changed under: {second}"
    assert {branch: _verdict(entry) for branch, entry in second.items()} == {
        branch: _verdict(entry) for branch, entry in first.items()
    }, "a reused verdict differs from the one derived"
    assert list((host.onevcs_home / "verdicts").iterdir()), "no verdict was recorded"


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


#: The branch name both hosted repositories below publish, as the fleet whose shared
#: stream retired eighteen open branches in a day did.
SHARED_BRANCH = "nick/dev-shared-name"
#: A branch of the first repository whose own change request the host merged at its head.
LANDED_BRANCH = "nick/dev-landed"
#: A branch of the second repository whose own change request was closed without merging.
CLOSED_BRANCH = "nick/dev-closed"


#: A session's token, as `onevcs session` names one.
type SessionToken = str


def _stream_token(branch: str) -> SessionToken:
    """The legacy branch-keyed stream a `publish-branch` of `branch` recorded into."""
    return "publish-branch-" + branch.replace("/", "-")


class HostedRepository(NamedTuple):
    """One repository of the substituted host, registered under its own GitHub origin."""

    slug: fake_gh.Repository
    origin: Path
    checkout: Path

    @property
    def identity(self) -> str:
        return f"github.com/{self.slug}"

    @property
    def name(self) -> str:
        return self.slug.split("/")[1]

    def url(self, number: int) -> str:
        return f"https://github.com/{self.slug}/pull/{number}"


class Worked(NamedTuple):
    """A branch worked in a closed session and pushed: the session, and where it stands."""

    token: SessionToken
    tip: fake_gh.Commit


class LegacyEvent(NamedTuple):
    """One event of a legacy branch-keyed stream, before it is enveloped and numbered."""

    kind: str
    phase: str
    payload: dict[str, object]


class HostedFleet:
    """Two hosted identities on one scratch state root, and the host that answers both.

    The origins are real bare repositories and the checkouts real clones, registered by
    `onevcs register` under the GitHub origin each is resolved from; the branches are
    worked in real `onevcs session open` sessions that `session close` closed. What is
    substituted is the remote host's decisioning alone — `tests/e2e/fake_gh.py` through
    `ONEVCS_GH`, the seam `onevcs` publishes for it — answering each repository's change
    requests separately, under numbers that collide across the two.
    """

    def __init__(self, host: Host) -> None:
        self.host = host
        self.gh_state = host.root / "gh-state"
        self.gh_calls = host.root / "gh-calls.jsonl"
        self.repositories = {name: self._repository(name) for name in ("alpha", "beta")}
        # A hosted identity's change requests are opened for review, so the host is
        # what decides whether one is open: the policy every repository here resolves.
        (host.onevcs_home / "rules.yml").write_text(
            "version: 3\n"
            "trailer_prefix: Orchestrator-\n"
            "rules: []\n"
            "default:\n"
            "  publication: change-open\n"
            "  approvals: required\n",
            encoding="utf-8",
        )
        for repository in self.repositories.values():
            registered = host.onevcs(
                "register",
                str(repository.checkout),
                "--origin",
                f"https://github.com/{repository.slug}.git",
            )
            assert registered.returncode == 0, registered.stderr

    @property
    def environment(self) -> dict[str, str]:
        return {
            **self.host.environment,
            # llmlint: ignore[e2e_not_mocked] Only the remote host's decisioning, at onevcs's seam.
            "ONEVCS_GH": str(fake_gh.__file__),
            "FAKE_GH_STATE": str(self.gh_state),
            "FAKE_GH_CALLS": str(self.gh_calls),
            "FAKE_GH_ORIGINS": json.dumps(
                {
                    repository.slug: str(repository.origin)
                    for repository in self.repositories.values()
                }
            ),
        }

    def _repository(self, name: str) -> HostedRepository:
        origin = self.host.root / f"{name}.git"
        _git("init", "--quiet", "--bare", "--initial-branch=main", str(origin))
        checkout = self.host.root / name
        _git("clone", "--quiet", str(origin), str(checkout))
        _git("commit", "--quiet", "--allow-empty", "-m", "seed", cwd=checkout)
        _git("push", "--quiet", "origin", "HEAD:main", cwd=checkout)
        return HostedRepository(f"acme-corp/{name}", origin, checkout)

    def onevcs(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(ONEVCS), *arguments],
            cwd=self.host.root,
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=e2e_timeout(120),
        )

    def worked(self, repository: HostedRepository, branch: str, file: str) -> Worked:
        """Work `branch` in a closed session and push it, as a publication left it."""
        opened = self.onevcs(
            "session", "open", str(repository.checkout), "--branch", branch, "--pool", "0"
        )
        assert opened.returncode == 0, opened.stderr
        session = json.loads(opened.stdout)
        worktree = Path(session["worktree"])
        (worktree / file).write_text(f"{repository.slug} wrote this\n", encoding="utf-8")
        _git("add", file, cwd=worktree)
        _git("commit", "--quiet", "-m", f"feat: {repository.slug}'s {file}", cwd=worktree)
        closed = self.onevcs("session", "close", session["token"])
        assert closed.returncode == 0, closed.stderr
        _git("push", "--quiet", "origin", branch, cwd=repository.checkout)
        tip = _git("rev-parse", branch, cwd=repository.checkout).strip()
        return Worked(session["token"], tip)

    def merged_long_ago(self, repository: HostedRepository, number: int) -> str:
        """Change request `number` of `repository`, merged at a squash its base carries."""
        work = self.host.root / f"{repository.slug.replace('/', '-')}-old-{number}"
        _git("clone", "--quiet", str(repository.origin), str(work))
        (work / f"old-{number}.txt").write_text("old work\n", encoding="utf-8")
        _git("add", "-A", cwd=work)
        _git("commit", "--quiet", "-m", f"feat: old work (#{number})", cwd=work)
        _git("push", "--quiet", "origin", "HEAD:main", cwd=work)
        merged = _git("rev-parse", "HEAD", cwd=work).strip()
        fake_gh.hold(
            self.gh_state, repository.slug, number=number, head=f"old/{number}", head_sha=merged
        )
        fake_gh.merge(self.gh_state, repository.slug, number=number, commit=merged)
        _git("pull", "--quiet", "--ff-only", "origin", "main", cwd=repository.checkout)
        return merged

    def squash_merged(self, repository: HostedRepository, number: int, branch: str) -> str:
        """A person squash-merging `repository`'s change request `number` from `branch`."""
        work = self.host.root / f"{repository.slug.replace('/', '-')}-merge-{number}"
        _git("clone", "--quiet", str(repository.origin), str(work))
        _git("fetch", "--quiet", "origin", branch, cwd=work)
        _git("merge", "--quiet", "--squash", "FETCH_HEAD", cwd=work)
        _git("commit", "--quiet", "-m", f"feat: {branch} (#{number})", cwd=work)
        _git("push", "--quiet", "origin", "HEAD:main", cwd=work)
        merged = _git("rev-parse", "HEAD", cwd=work).strip()
        fake_gh.merge(self.gh_state, repository.slug, number=number, commit=merged)
        _git("pull", "--quiet", "--ff-only", "origin", "main", cwd=repository.checkout)
        return merged

    def legacy_stream(self, branch: str, events: list[LegacyEvent]) -> None:
        """The `publish-branch-<branch>` stream as a build before the identity label wrote it.

        One stream per branch *name*, every repository's events in it, and none carrying
        `labels.identity` — the shape this host's streams hold. No installed verb writes
        this any more, which is why it is written here.
        """
        token = _stream_token(branch)
        streams = self.host.onevcs_home / "streams"
        streams.mkdir(parents=True, exist_ok=True)
        lines = [
            json.dumps(
                {
                    "v": 1,
                    "ts": f"2026-10-12T11:{seq:02d}:00.000Z",
                    "stream": token,
                    "seq": seq,
                    "source": "vcs",
                    "kind": event.kind,
                    "phase": event.phase,
                    "labels": {"session": token},
                    "payload": event.payload,
                    "artifacts": [],
                }
            )
            for seq, event in enumerate(events, start=1)
        ]
        (streams / f"{token}.ndjson").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def calls(self) -> list[list[str]]:
        """Every call the host was asked to make, as the argv `onevcs` passed."""
        if not self.gh_calls.exists():
            return []
        return [
            json.loads(line)
            for line in self.gh_calls.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def viewed(self) -> set[tuple[str, str]]:
        """Every `(repository, number)` the host was asked `pr view` about."""
        return {
            (call[call.index("--repo") + 1], call[2])
            for call in self.calls()
            if call[:2] == ["pr", "view"] and "--repo" in call
        }


def _pushed(branch: str, head: str) -> LegacyEvent:
    """A `push` of the branch's own name as the legacy stream records one, with its head."""
    return LegacyEvent(
        "push",
        "development",
        {"branch": branch, "remote": "origin", "accepted": True, "head": head},
    )


def _opened(url: str, number: int) -> LegacyEvent:
    """A `change-opened` event as the legacy stream records one."""
    return LegacyEvent(
        "change-opened",
        "review",
        {"url": url, "host": "github", "id": str(number), "base": "main", "author": "tester"},
    )


def _landed(url: str, commit: str) -> LegacyEvent:
    """A `change-merged` landing as the legacy stream records one, with no reported head."""
    return LegacyEvent(
        "change-merged",
        "review",
        {
            "url": url,
            "sha": commit,
            "landed_at": "2026-06-20T23:12:25.000Z",
            "landing": commit,
        },
    )


def _tip(place: Path, branch: str) -> str | None:
    """The commit `branch` stands at in a checkout or a bare origin, or `None`."""
    done = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=place,
        capture_output=True,
        text=True,
        check=False,
        timeout=e2e_timeout(60),
    )
    return done.stdout.strip() or None


def test_a_branch_name_two_repositories_share_is_kept_on_each_ones_own_open_change_request(
    host: Host,
) -> None:
    """The retirement fix this host adopted, through the real recipe and the installed onevcs.

    Two hosted repositories each publish one branch name with its own content, and the one
    legacy `publish-branch-<branch>` stream holds both publications — alpha's push and #1,
    then beta's push and #2 — and, newest of all, a landing naming beta's #1, which merged
    long ago. Alpha's #2 merged long ago too. A pass that took the shared stream's newest
    records reads beta's #2 as both branches' change request and retires both: measured
    on onevcs 0.48.1, this fixture deletes both open branches on `merged-change-request`
    naming beta's #2, without asking the host — the incident. So each branch must be kept
    as `open-change-request` on the change request its own repository opened, asked of its
    own repository; a branch whose own change request was closed without merging must be
    kept as `closed-change-request`; and a branch whose own change request the host merged
    at that branch's head must be retired on that proof, which a release keeping every
    branch fails.
    """
    fleet = HostedFleet(host)
    alpha, beta = fleet.repositories["alpha"], fleet.repositories["beta"]
    beta_landing = fleet.merged_long_ago(beta, 1)
    fleet.merged_long_ago(alpha, 2)
    tokens: dict[str, str] = {}
    tips: dict[str, str] = {}
    for repository, number in ((alpha, 1), (beta, 2)):
        worked = fleet.worked(repository, SHARED_BRANCH, f"{repository.name}.txt")
        tokens[repository.slug], tips[repository.slug] = worked
        fake_gh.hold(
            fleet.gh_state, repository.slug, number=number, head=SHARED_BRANCH, head_sha=worked.tip
        )
    fleet.legacy_stream(
        SHARED_BRANCH,
        [
            _pushed(SHARED_BRANCH, tips[alpha.slug]),
            _opened(alpha.url(1), 1),
            _pushed(SHARED_BRANCH, tips[beta.slug]),
            _opened(beta.url(2), 2),
            _landed(beta.url(1), beta_landing),
        ],
    )
    landed_tip = fleet.worked(alpha, LANDED_BRANCH, "landed.txt").tip
    fake_gh.hold(fleet.gh_state, alpha.slug, number=3, head=LANDED_BRANCH, head_sha=landed_tip)
    fleet.legacy_stream(
        LANDED_BRANCH, [_pushed(LANDED_BRANCH, landed_tip), _opened(alpha.url(3), 3)]
    )
    fleet.squash_merged(alpha, 3, LANDED_BRANCH)
    closed_tip = fleet.worked(beta, CLOSED_BRANCH, "closed.txt").tip
    fake_gh.hold(fleet.gh_state, beta.slug, number=3, head=CLOSED_BRANCH, head_sha=closed_tip)
    fleet.legacy_stream(
        CLOSED_BRANCH, [_pushed(CLOSED_BRANCH, closed_tip), _opened(beta.url(3), 3)]
    )
    fake_gh.close(fleet.gh_state, beta.slug, number=3)
    for repository in (alpha, beta):
        for place in (repository.checkout, repository.origin):
            assert _tip(place, SHARED_BRANCH) == tips[repository.slug], (
                f"the premise: {place} holds {repository.slug}'s own {SHARED_BRANCH}"
            )
    assert tips[alpha.slug] != tips[beta.slug], "the premise: each carries its own content"

    result = sweep(host, "--min-age-hours", "0", "--format", "json", environment=fleet.environment)

    assert result.returncode == 0, result.stderr
    report, _ = json.JSONDecoder().raw_decode(result.stdout.lstrip())
    assert report["verb"] == "onevcs sweep", f"the first report is not onevcs's: {report}"
    examined = {
        (entry["identity"], entry["branch"]): entry
        for entry in report["finished_branches"]["examined"]
    }
    for repository in (alpha, beta):
        kept = examined[(repository.identity, SHARED_BRANCH)]
        assert (kept["class"], kept["reason"], kept["outcome"]) == (
            "keep",
            "open-change-request",
            "kept",
        ), kept
        for place in (repository.checkout, repository.origin):
            assert _tip(place, SHARED_BRANCH) == tips[repository.slug], (
                f"{repository.slug}'s {SHARED_BRANCH} moved or went from {place}: {kept}"
            )
    # Each kept branch stood on its own repository's change request: the host was asked
    # about alpha's #1 and beta's #2, each of its own repository, and never about the
    # number the other repository's change request took there.
    viewed = fleet.viewed()
    assert {(alpha.slug, "1"), (beta.slug, "2")} <= viewed, viewed
    assert not {(alpha.slug, "2"), (beta.slug, "1")} & viewed, (
        f"a repository was asked about the other one's change request number: {viewed}"
    )
    # …and that is the change request each one's own session reads out of the shared
    # stream, never the newest one across both.
    for repository, own in ((alpha, alpha.url(1)), (beta, beta.url(2))):
        status = fleet.onevcs("status", tokens[repository.slug], "--json")
        assert status.returncode == 0, status.stderr
        publication = json.loads(status.stdout)["publication"]
        assert publication["change_url"] == own, (repository.slug, publication)
        assert publication["landed"]["state"] != "yes", (repository.slug, publication)

    # A change request closed without merging keeps its branch too, under its own word.
    shut = examined[(beta.identity, CLOSED_BRANCH)]
    assert (shut["class"], shut["reason"], shut["outcome"]) == (
        "keep",
        "closed-change-request",
        "kept",
    ), shut
    for place in (beta.checkout, beta.origin):
        assert _tip(place, CLOSED_BRANCH) == closed_tip, f"{CLOSED_BRANCH} went from {place}"

    retired = examined[(alpha.identity, LANDED_BRANCH)]
    assert retired["class"] == "retirable", retired
    assert retired["proof"] == {
        "kind": "merged-change-request",
        "change_url": alpha.url(3),
        "head": landed_tip,
    }, retired
    assert retired["outcome"] == "retired", retired
    assert _tip(alpha.checkout, LANDED_BRANCH) is None, "the merged branch stayed on its checkout"
    assert _tip(alpha.origin, LANDED_BRANCH) is None, "the merged branch stayed on its origin"

    asked = fleet.calls()
    assert not [call for call in asked if call[:2] in (["pr", "close"], ["pr", "merge"])], (
        f"the pass closed or merged a change request: {asked}"
    )
