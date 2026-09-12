"""`just import-branch` really makes work reachable, and `just work-status` really answers.

These two verbs exist for the two questions the landing verbs raise and cannot answer
themselves.

`import-branch` is the answer to *where the branch is*. Every landing verb reads the
branch from the identity's **publication checkout**, never from wherever a session
happened to be working, so work that lives only in a session worktree or a run clone is
refused as being in none of the identity's checkouts — and an agent refused there is an
agent one step from `git push`. So this drives the refusal, the import, and the
publication that then succeeds, in that order: the sequence is the claim, not any one
of its steps.

`work-status` is the answer to *what became of it*. A run records a node's landing as
its settlement observed it and nothing re-reads it, so a change that merged afterwards
still reads as unlanded there forever. This asks the same branch before and after it
lands and holds the two answers apart, which is the whole reason a planner has anything
to ask.

The third question is what a landing leaves once this host's records are gone, and the
answer differs by which workflow wrote the base commit; the last journey here holds that
split, which `docs/repo-lifecycle.md` states and nothing else re-takes.

Everything is real — the recipes, `onevcs`, git, and the origin. What makes that safe is
isolation rather than substitution, exactly as in `tests/e2e/test_publish_branch_e2e.py`:
`ONEVCS_HOME` points at a scratch registry and the repository is a throwaway checkout of
a throwaway bare origin, so nothing this host has registered is read and no remote of its
own can be reached.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import NamedTuple, NotRequired, TypedDict, cast

from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The branch under test, in the shape a dispatch leaves behind.
WORK_BRANCH = "claude/session-work"

#: The file the work adds, and how a landing is found on the base.
WORK_FILE = "shipped.txt"

#: The base every identity here publishes onto.
BASE = "main"
#: A second branch, landed the way a remote merge path lands one, and the file it adds.
SQUASHED_BRANCH = "claude/squashed-work"
SQUASHED_FILE = "squashed.txt"
#: The change request number a host's squash puts in the base subject — and nothing
#: else: no trailer, and no record in any `ONEVCS_HOME`.
SQUASHED_NUMBER = 7
#: The prefix `Orchestrator-Landed-Commit` is read under, and one it is not.
TRAILER_PREFIX = "Orchestrator-"
OTHER_PREFIX = "Elsewhere-"


#: The two `landed:` answers the lost-record journey tells apart, and the one tier it
#: expects to have decided. `onevcs` owns that vocabulary, so these are the values this
#: journey reads and not a copy of the CLI's whole set: a closed type over every answer
#: would be a second statement of that contract with nothing here reconciling it.
LANDED = "yes"
UNDECIDED = "unknown"
TRAILER_TIER = "trailer"


class LandingEvidence(TypedDict):
    """Which record tier decided a landing. Only `tier` is read; the rest stays the CLI's."""

    tier: str


class Landing(TypedDict):
    """`work-status`'s `landed:` answer, in the two fields the lost-record journey reads.

    `evidence` is present only when a record tier decided; the comparison tier is not a
    record and names nothing, so its absence is itself one of the answers read.
    """

    state: str
    evidence: NotRequired[LandingEvidence]


class Publication(TypedDict):
    """Where `work-status` says a piece of work stands with its remote."""

    state: str
    landed: Landing


class WorkStatus(TypedDict):
    """One `work-status --json` answer, in the terms this journey reads it.

    `onevcs` owns the whole report and it carries far more than this; these are the
    two fields the before-and-after question turns on, stated here rather than
    restated from the CLI's schema. `next` stays untyped on purpose — the journey
    asserts on the *presence* of a command in it, not on its shape, and pinning a
    shape this test never reads would be a second contract to keep in step.
    """

    publication: Publication
    next: object


class Identity(NamedTuple):
    """One throwaway identity: its publication checkout, its origin, and a clone beside it.

    `elsewhere` is the point of the fixture rather than an extra: it stands for the
    place a dispatch actually works — a session worktree or a per-run clone — which is
    *not* a registered checkout of the identity, and so is exactly what the landing
    verbs cannot see.
    """

    #: The registered publication checkout, and the `--repo` every verb is given.
    checkout: Path
    #: The bare origin, read to prove a publication left the checkout.
    origin: Path
    #: A clone of the same origin that the registry knows nothing about.
    elsewhere: Path
    #: The environment carrying `ONEVCS_HOME`, which is what makes this isolated.
    environment: dict[str, str]


def _git(*arguments: str, cwd: Path) -> str:
    """Run git for real, failing loudly."""
    done = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert done.returncode == 0, f"git {' '.join(arguments)}: {done.stderr or done.stdout}"
    return done.stdout


def _just(*arguments: str, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run one real recipe from this checkout, against the scratch registry."""
    return subprocess.run(
        ["just", *arguments],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        check=False,
    )


def _identity(tmp_path: Path) -> Identity:
    """A registered repository whose identity publishes locally, naming no verifier.

    `local-direct` deliberately, for the reason the publish-branch journey names: it is
    the one published policy that opens no change request, so the whole sequence
    completes against a bare origin on disk with no network and no GitHub.
    """
    home = tmp_path / "onevcs-home"
    home.mkdir()
    seed = tmp_path / "seed"
    _git("init", "-q", "-b", BASE, str(seed), cwd=tmp_path)
    (seed / "README.md").write_text("seed\n", encoding="utf-8")
    _git("add", "-A", cwd=seed)
    _git("commit", "-q", "-m", "init", cwd=seed)
    origin = tmp_path / "origin.git"
    _git("clone", "-q", "--bare", str(seed), str(origin), cwd=tmp_path)
    checkout = tmp_path / "checkout"
    _git("clone", "-q", str(origin), str(checkout), cwd=tmp_path)
    elsewhere = tmp_path / "elsewhere"
    _git("clone", "-q", str(origin), str(elsewhere), cwd=tmp_path)
    environment = _registry(home, checkout, prefix=TRAILER_PREFIX)
    return Identity(checkout, origin, elsewhere, environment)


def _rules(prefix: str) -> str:
    """This journey's rules file: every identity publishes locally, under `prefix`."""
    return (
        "version: 3\n"
        f"trailer_prefix: {prefix}\n"
        "rules: []\n"
        "default:\n"
        "  publication: local-direct\n"
        "  approvals: none\n"
    )


def _registry(home: Path, checkout: Path, *, prefix: str) -> dict[str, str]:
    """Register `checkout` into the state root at `home`, and return the environment.

    A fresh `home` holds no session record, so a landing asked through it has only what
    the repository's own history says — which is the state a host is in once its
    records are gone, and the one the lost-record journey needs to produce on purpose.
    """
    home.mkdir(exist_ok=True)
    (home / "rules.yml").write_text(_rules(prefix), encoding="utf-8")
    environment = dict(os.environ)
    environment["ONEVCS_HOME"] = str(home)
    registered = _just("register-repo", str(checkout), environment=environment)
    assert registered.returncode == 0, registered.stderr + registered.stdout
    return environment


def _work_outside_the_registry(identity: Identity) -> str:
    """Commit finished work in the clone the registry knows nothing about."""
    _git("checkout", "-q", "-b", WORK_BRANCH, cwd=identity.elsewhere)
    (identity.elsewhere / WORK_FILE).write_text("the work\n", encoding="utf-8")
    _git("add", "-A", cwd=identity.elsewhere)
    _git("commit", "-q", "-m", "feat: finish the work", cwd=identity.elsewhere)
    return _git("rev-parse", "HEAD", cwd=identity.elsewhere).strip()


def test_import_branch_makes_session_only_work_reachable_and_then_landable(
    tmp_path: Path,
) -> None:
    """Work no registered checkout holds is refused, imported, and then published.

    The refusal first, because it is the state an agent meets: a branch finished in a
    session worktree is not a branch the identity can land, and the message it gets is
    what decides whether it reaches for `import` or for `git`. Then the import, and then
    a real publication of the imported branch — asserted on the origin, because an
    import that made the branch merely *visible* without making it the same work would
    pass every check short of this one.
    """
    identity = _identity(tmp_path)
    head = _work_outside_the_registry(identity)

    refused = _just(
        "publish-branch",
        WORK_BRANCH,
        "--repo",
        str(identity.checkout),
        environment=identity.environment,
    )
    assert refused.returncode != 0, refused.stdout
    assert "is in none of the checkouts" in refused.stderr + refused.stdout, (
        refused.stderr + refused.stdout
    )

    imported = _just(
        "import-branch",
        WORK_BRANCH,
        "--repo",
        str(identity.checkout),
        "--from",
        str(identity.elsewhere),
        environment=identity.environment,
    )
    assert imported.returncode == 0, imported.stderr + imported.stdout
    # The same commit, not merely a branch of the same name: an import that resolved to
    # anything else would land work nobody wrote.
    assert _git("rev-parse", WORK_BRANCH, cwd=identity.checkout).strip() == head, imported.stdout

    published = _just(
        "publish-branch",
        WORK_BRANCH,
        "--repo",
        str(identity.checkout),
        environment=identity.environment,
    )
    assert published.returncode == 0, published.stderr + published.stdout
    assert WORK_FILE in _git("ls-tree", "--name-only", BASE, cwd=identity.origin), (
        f"the imported work never reached the origin's {BASE}:\n{published.stdout}"
    )


def test_work_status_answers_a_branch_before_and_after_it_lands(tmp_path: Path) -> None:
    """The re-read a settled run cannot give: unpublished first, and not afterwards.

    A run's own row dates its landing to the settlement and nothing re-reads it, so the
    two answers below are exactly what a planner has no other way to tell apart. What is
    held is the pair — the state, and the `next` that follows from it — rather than the
    exact word the second one lands on: a base that squash-merged and a base that
    fast-forwarded describe the same finished work differently, and pinning one of those
    spellings would make this journey a test of which merge git chose. What must not
    drift is that the work stops being unpublished and stops being told to publish.

    Read through `--json` as well as the table, because the machine-readable answer is
    what a later automation would key on and a table that drifted from it would be found
    by nobody.
    """
    identity = _identity(tmp_path)
    _work_outside_the_registry(identity)
    imported = _just(
        "import-branch",
        WORK_BRANCH,
        "--repo",
        str(identity.checkout),
        "--from",
        str(identity.elsewhere),
        environment=identity.environment,
    )
    assert imported.returncode == 0, imported.stderr + imported.stdout

    before = _just("work-status", WORK_BRANCH, "--json", environment=identity.environment)
    assert before.returncode == 0, before.stderr + before.stdout
    # `cast` rather than validation because the assertions below ARE the validation:
    # every field `WorkStatus` names is read on the next two lines, so a report that
    # stopped carrying one fails this journey with a KeyError naming it. Validating
    # first would only move the same failure earlier and add a second description of
    # a shape `onevcs` already owns.
    unpublished = cast(WorkStatus, json.loads(before.stdout))
    assert unpublished["publication"]["state"] == "unpublished", before.stdout
    assert "publish-branch" in json.dumps(unpublished["next"]), before.stdout

    published = _just(
        "publish-branch",
        WORK_BRANCH,
        "--repo",
        str(identity.checkout),
        environment=identity.environment,
    )
    assert published.returncode == 0, published.stderr + published.stdout

    after = _just("work-status", WORK_BRANCH, "--json", environment=identity.environment)
    assert after.returncode == 0, after.stderr + after.stdout
    # Same cast, same reason as above: the two asserts that follow read every field.
    landed = cast(WorkStatus, json.loads(after.stdout))
    assert landed["publication"]["state"] != "unpublished", after.stdout
    assert "publish-branch" not in json.dumps(landed["next"]), after.stdout
    # The same answer the operator reads, from the same run of the verb: a `--json`
    # shape that had moved on without the table would leave the table asserting an
    # unmeasured thing.
    table = _just("work-status", WORK_BRANCH, environment=identity.environment)
    assert table.returncode == 0, table.stderr + table.stdout
    assert landed["publication"]["state"].replace("-", " ") in table.stdout, table.stdout


def _land_the_way_a_host_squashes(identity: Identity) -> None:
    """Land a second branch as a remote merge path leaves one, and import it.

    A host's squash writes one commit on the base with the change request's number in
    its subject and nothing else — no trailer, because `onevcs` did not write the
    commit, and no record here, because nothing was published through it. That is done
    by hand with git rather than through `publish-branch`, since the identity resolves
    `local-direct` and the whole point is a landing this identity's publication did
    **not** stamp. The branch is then imported so the identity holds it, which is what
    lets `work-status` be asked about it at all.
    """
    _git("fetch", "-q", "origin", cwd=identity.elsewhere)
    _git("checkout", "-q", "-B", SQUASHED_BRANCH, f"origin/{BASE}", cwd=identity.elsewhere)
    (identity.elsewhere / SQUASHED_FILE).write_text("squashed\n", encoding="utf-8")
    _git("add", "-A", cwd=identity.elsewhere)
    _git("commit", "-q", "-m", "feat: the squashed work", cwd=identity.elsewhere)
    _git("push", "-q", "origin", SQUASHED_BRANCH, cwd=identity.elsewhere)

    _git("fetch", "-q", "origin", cwd=identity.checkout)
    _git("checkout", "-q", BASE, cwd=identity.checkout)
    _git("merge", "-q", "--ff-only", f"origin/{BASE}", cwd=identity.checkout)
    _git("merge", "-q", "--squash", f"origin/{SQUASHED_BRANCH}", cwd=identity.checkout)
    _git(
        "commit",
        "-q",
        "-m",
        f"feat: the squashed work (#{SQUASHED_NUMBER})",
        cwd=identity.checkout,
    )
    _git("push", "-q", "origin", BASE, cwd=identity.checkout)

    imported = _just(
        "import-branch",
        SQUASHED_BRANCH,
        "--repo",
        str(identity.checkout),
        "--from",
        str(identity.elsewhere),
        environment=identity.environment,
    )
    assert imported.returncode == 0, imported.stderr + imported.stdout


def _landing(branch: str, environment: dict[str, str]) -> tuple[Landing, str]:
    """One `work-status` answer about `branch`: the `landed` field, and the table."""
    machine = _just("work-status", branch, "--json", environment=environment)
    assert machine.returncode == 0, machine.stderr + machine.stdout
    # `cast` for the reason the landing journey gives: every field named is read below.
    status = cast(WorkStatus, json.loads(machine.stdout))
    table = _just("work-status", branch, environment=environment)
    assert table.returncode == 0, table.stderr + table.stdout
    return status["publication"]["landed"], table.stdout


def test_a_lost_landing_is_read_from_the_base_only_where_the_publication_wrote_a_trailer(
    tmp_path: Path,
) -> None:
    """Which landing survives the loss of this host's records depends on who wrote it.

    `onevcs` stamps only the commit it writes. A `local-direct` publication writes the
    base's squash commit itself, so the base carries `<prefix>Landed-Commit` and the
    trailer tier reads it on a host that has never held a record of the branch. A remote
    host's squash carries no trailer, and the change-request tier needs a record of the
    change request before it can look for its number — so once the record is gone the
    `(#N)` in the base's own subject goes unread and the answer falls to `content
    comparison`, which never says `yes`. This drives both halves through registries
    holding no session record, plus the one thing that turns the strong half off: the
    trailer is read under the configured prefix and not under a name `onevcs` knows on
    its own. The docs passage this reconciles is `docs/repo-lifecycle.md`'s *What
    `decided by:` can reach, per workflow*. It runs in well under a second — the
    identity's merge path runs nothing and `--no-draft` spends no turn — which is why it
    sits in this module's tier beside the two journeys above rather than behind an
    edge of its own.
    """
    identity = _identity(tmp_path)
    _work_outside_the_registry(identity)
    imported = _just(
        "import-branch",
        WORK_BRANCH,
        "--repo",
        str(identity.checkout),
        "--from",
        str(identity.elsewhere),
        environment=identity.environment,
    )
    assert imported.returncode == 0, imported.stderr + imported.stdout
    # `--no-draft`: a `local-direct` landing opens no change request, so there is no
    # body for a drafting turn to describe.
    published = _just(
        "publish-branch",
        WORK_BRANCH,
        "--repo",
        str(identity.checkout),
        "--no-draft",
        environment=identity.environment,
    )
    assert published.returncode == 0, published.stderr + published.stdout
    _land_the_way_a_host_squashes(identity)
    subjects = _git("log", "--format=%s", "-2", BASE, cwd=identity.origin)
    assert f"(#{SQUASHED_NUMBER})" in subjects, subjects

    # A registry that has never seen either branch: only the base's own history answers.
    without_records = _registry(tmp_path / "no-records", identity.checkout, prefix=TRAILER_PREFIX)

    stamped, stamped_table = _landing(WORK_BRANCH, without_records)
    assert stamped["state"] == LANDED, stamped_table
    assert stamped["evidence"]["tier"] == TRAILER_TIER, stamped_table
    assert "a landing trailer on the base" in stamped_table, stamped_table

    unstamped, unstamped_table = _landing(SQUASHED_BRANCH, without_records)
    assert unstamped["state"] == UNDECIDED, unstamped_table
    assert "evidence" not in unstamped, unstamped_table
    assert "content comparison" in unstamped_table, unstamped_table

    # The same branch and the same base, read under a prefix the base was not stamped
    # with: the trailer tier finds nothing, and the strong half is the weak one.
    other_prefix = _registry(tmp_path / "other-prefix", identity.checkout, prefix=OTHER_PREFIX)
    unread, unread_table = _landing(WORK_BRANCH, other_prefix)
    assert unread["state"] == UNDECIDED, unread_table
    assert "content comparison" in unread_table, unread_table


def test_work_status_refuses_a_reference_this_host_knows_nothing_about(tmp_path: Path) -> None:
    """An unknown reference is refused by name, and says what a reference may be.

    The failure path that matters: a planner asking after work reaches for whatever it
    has — a branch name from a stale report, a URL, a token — and an answer that read as
    "nothing happened to it" would be the same shape as the honest answer for work that
    truly went nowhere. So the refusal is non-zero and enumerates the four kinds of
    reference, which is what turns a wrong guess into a next step.
    """
    identity = _identity(tmp_path)

    refused = _just("work-status", "claude/never-existed", environment=identity.environment)

    assert refused.returncode != 0, refused.stdout
    reported = refused.stderr + refused.stdout
    assert "names no work this host knows" in reported, reported
    assert "change request" in reported and "session token" in reported, reported
