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
from typing import NamedTuple, TypedDict, cast

from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The branch under test, in the shape a dispatch leaves behind.
WORK_BRANCH = "claude/session-work"

#: The file the work adds, and how a landing is found on the base.
WORK_FILE = "shipped.txt"

#: The base every identity here publishes onto.
BASE = "main"


class Publication(TypedDict):
    """Where `work-status` says a piece of work stands with its remote."""

    state: str


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
    """A registered repository whose identity publishes locally under a passing gate.

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
    (home / "rules.yml").write_text(
        "version: 2\n"
        "trailer_prefix: Orchestrator-\n"
        "rules: []\n"
        "default:\n"
        "  publication: local-direct\n"
        "  approvals: none\n"
        "  gate:\n"
        '    command: ["true"]\n',
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment["ONEVCS_HOME"] = str(home)
    registered = _just("register-repo", str(checkout), environment=environment)
    assert registered.returncode == 0, registered.stderr + registered.stdout
    return Identity(checkout, origin, elsewhere, environment)


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
