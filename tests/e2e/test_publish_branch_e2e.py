"""`just publish-branch` really publishes a complete branch, and really refuses a red one.

The verb exists to close the one branch state this harness had no path for: finished,
unpublished, held by no session. `onevcs recover` refuses it for having no incomplete
provenance and `integrate` is a local merge train, so landing one used to mean raw
`git` or `gh` — publication without the repository's own gate in front of it. A
wrapper that only forwards arguments correctly would not show that the state is
actually covered, so this drives the verb through to its outcome: the base branch.

Every part is real — the recipe, `onevcs`, git, the origin, and the identity's gate.
What makes that safe is isolation rather than substitution: `ONEVCS_HOME` points at a
scratch registry, the repository is a throwaway checkout of a throwaway bare origin,
and the rules file is written for that home alone. Nothing this host has registered
is read, and no branch or remote of this host's own can be reached from here.
`tests/e2e/test_repo_registry_apply_e2e.py` drives real `onevcs` against a scratch
registry the same way.

The gate is the pivot of both journeys, so it is a real command the rules file names:
publication runs it and believes its exit status, which is what makes the red case a
refusal rather than a slower merge.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import NamedTuple

from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The branch under test, in the shape a dispatch leaves behind.
FINISHED_BRANCH = "claude/finished-work"

#: The file the published commit adds, and how the assertion finds it on the base.
PUBLISHED_FILE = "shipped.txt"

#: The base every identity here publishes onto.
BASE = "main"


class Publication(NamedTuple):
    """One throwaway repository, its scratch registry, and the origin behind it."""

    #: The registered publication checkout, and the `--repo` every verb is given.
    checkout: Path
    #: The bare origin it pushes to, read to prove a publication left the checkout.
    origin: Path
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


def _publication(tmp_path: Path, *, gate: list[str]) -> Publication:
    """A registered repository whose identity publishes locally under `gate`.

    `local-direct` deliberately: it is the one published policy that opens no change
    request, so the whole journey completes against a bare origin on disk with no
    network and no GitHub. The policy is written as the rules file's `default`,
    because a path origin has no host, owner, or name for a `match` to select on.
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
    # A command gate is argv `onevcs` runs directly — no shell — so the verdict under
    # test is this list's own exit status.
    (home / "rules.yml").write_text(
        "version: 2\n"
        "trailer_prefix: Orchestrator-\n"
        "rules: []\n"
        "default:\n"
        "  publication: local-direct\n"
        "  approvals: none\n"
        "  gate:\n"
        f"    command: {gate!r}\n".replace("'", '"'),
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment["ONEVCS_HOME"] = str(home)
    registered = _just("register-repo", str(checkout), environment=environment)
    assert registered.returncode == 0, registered.stderr + registered.stdout
    return Publication(checkout, origin, environment)


def _finished_branch(checkout: Path) -> str:
    """Commit finished work on a branch, exactly as a settled dispatch leaves it."""
    _git("checkout", "-q", "-b", FINISHED_BRANCH, cwd=checkout)
    (checkout / PUBLISHED_FILE).write_text("the work\n", encoding="utf-8")
    _git("add", "-A", cwd=checkout)
    _git("commit", "-q", "-m", "feat: finish the work", cwd=checkout)
    head = _git("rev-parse", "HEAD", cwd=checkout).strip()
    _git("checkout", "-q", BASE, cwd=checkout)
    return head


def test_publish_branch_lands_a_complete_branch_on_its_base(tmp_path: Path) -> None:
    """The recipe publishes for real: the work reaches the base branch and the origin.

    This is the claim the recipe is worth having — not that `onevcs publish-branch` is
    reached with the right arguments, which the delegation table already holds, but
    that the branch state it names is genuinely landed. Read from the origin rather
    than from the command's own report, because a verb that said it published and
    pushed nothing is exactly the failure an operator would discover later.
    """
    publication = _publication(tmp_path, gate=["true"])
    _finished_branch(publication.checkout)

    before = _git("rev-list", "--count", BASE, cwd=publication.origin).strip()

    published = _just(
        "publish-branch",
        FINISHED_BRANCH,
        "--repo",
        str(publication.checkout),
        environment=publication.environment,
    )

    assert published.returncode == 0, published.stderr + published.stdout
    # The origin is the side of this an operator's other clones would see, and the
    # content is what is asserted rather than the branch's own commit: every path that
    # advances a base here squash-merges, so the branch's sha is deliberately not the
    # one that lands.
    assert PUBLISHED_FILE in _git("ls-tree", "--name-only", BASE, cwd=publication.origin), (
        f"the finished work never reached the origin's {BASE}:\n{published.stdout}"
    )
    # One commit, whichever way it landed. Every path that advances a base here leaves
    # exactly one, so this holds for the squash and for the fast-forward of a
    # single-commit branch alike — and fails a merge commit beside the work.
    after = _git("rev-list", "--count", BASE, cwd=publication.origin).strip()
    assert int(after) == int(before) + 1, (
        f"publication left {int(after) - int(before)} commits on {BASE}, not the one "
        f"every base-advancing path here leaves:\n{published.stdout}"
    )
    # And it names the commit the base now points at, which is what an operator needs
    # to find the work afterwards.
    landed = _git("rev-parse", BASE, cwd=publication.origin).strip()
    assert landed in published.stdout, published.stdout


def test_publish_branch_refuses_a_branch_its_identity_gate_rejects(tmp_path: Path) -> None:
    """A red gate stops the publication, and the base is left exactly as it was.

    The recovery path, and the reason the verb is worth routing through at all: the
    whole point of not reaching for `gh pr create` by hand is that this path runs the
    repository's own gate first. A refusal that had already advanced the base would be
    worse than no gate, so what is asserted is the base, not the exit status alone.
    """
    publication = _publication(tmp_path, gate=["false"])
    _finished_branch(publication.checkout)
    before = _git("rev-parse", BASE, cwd=publication.origin).strip()

    refused = _just(
        "publish-branch",
        FINISHED_BRANCH,
        "--repo",
        str(publication.checkout),
        environment=publication.environment,
    )

    assert refused.returncode != 0, refused.stdout
    assert _git("rev-parse", BASE, cwd=publication.origin).strip() == before, (
        "the base moved even though the gate rejected the branch"
    )


def test_publishing_an_already_published_branch_changes_nothing_and_says_so(
    tmp_path: Path,
) -> None:
    """Running it twice is safe, which is what makes it usable from `just recoverable`.

    That listing is a snapshot, so an operator working down it will re-run this verb on
    a branch that has since landed — and the harmful shapes are a second merge commit
    on the base, or a non-zero exit that reads as a failure needing repair. It is
    neither: the second run succeeds, reports that the base already carries the
    content, and leaves the base on exactly the commit the first run put it on.
    """
    publication = _publication(tmp_path, gate=["true"])
    _finished_branch(publication.checkout)
    first = _just(
        "publish-branch",
        FINISHED_BRANCH,
        "--repo",
        str(publication.checkout),
        environment=publication.environment,
    )
    assert first.returncode == 0, first.stderr + first.stdout
    landed = _git("rev-parse", BASE, cwd=publication.origin).strip()

    again = _just(
        "publish-branch",
        FINISHED_BRANCH,
        "--repo",
        str(publication.checkout),
        environment=publication.environment,
    )

    assert again.returncode == 0, again.stderr + again.stdout
    assert "nothing to publish" in again.stdout, again.stdout
    assert _git("rev-parse", BASE, cwd=publication.origin).strip() == landed, (
        "a second publication moved the base again"
    )


def test_publish_branch_refuses_a_branch_that_is_not_there(tmp_path: Path) -> None:
    """A branch the checkout cannot reach is named in the refusal, not guessed at."""
    publication = _publication(tmp_path, gate=["true"])
    _finished_branch(publication.checkout)

    refused = _just(
        "publish-branch",
        "claude/never-existed",
        "--repo",
        str(publication.checkout),
        environment=publication.environment,
    )

    assert refused.returncode != 0, refused.stdout
    assert "claude/never-existed" in (refused.stderr + refused.stdout), (
        refused.stderr + refused.stdout
    )
