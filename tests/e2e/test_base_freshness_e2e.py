"""Which two refs `scripts/base-freshness.sh` compares, and what it does when it cannot.

`just lint-llm-diff` refuses a base its own origin ref has moved past, and this is the
program that decides it. The refusal itself is driven through the real recipe in
`tests/e2e/test_llmlint_cache_e2e.py`, which is where the operator-visible behaviour
belongs; what is driven here is the part a recipe run cannot reach — *which* ref counts
as "its own", and the two answers that are not a comparison at all.

Everything is real: the shipped script, real `git`, and real repositories built in a
scratch directory. Nothing is faked, and nothing here runs Nx or a judge, which is why
it sits in the recipe-scoped tier beside the other script journeys rather than in the
whole-workspace one.

llmlint: ignore-file[shell_test_tiers_stay_split] `git` is the one host tool here, and
`recipeWorkspace` is already the tier for that: `tests/e2e/test_workspace_contract_e2e.py`
beside it builds real repositories and drives real `just` under the same key, which is the
justfile and `scripts/**` — the two things this suite exercises and nothing else. A project
of its own would add a fourth Nx project to hold one module whose edges would be that same
pair, and a change to this repository's Nx project selection is a sibling node of this
plan's to make. What this file already does not pay for is the expensive part: no Nx, no
copied checkout, and no judge, which is why it is not in the module it was split out of.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from orchestrator.root import REPO_ROOT

FRESHNESS = REPO_ROOT / "scripts" / "base-freshness.sh"

#: What the script exits with when it could not answer at all — a caller that named no
#: base, or a git that failed rather than saying "no". Distinct from the exit 0 both
#: comparison answers share, because "the base is fine" and "nobody could tell" are the
#: two things a judged verdict must never be issued on the strength of one another.
COULD_NOT_ANSWER = 2

pytestmark = pytest.mark.reads_recipes


def _git(where: Path, *arguments: str) -> str:
    """One real git command, with an identity so commits do not need the host's."""
    return subprocess.run(
        ["git", "-c", "user.name=e2e", "-c", "user.email=e2e@invalid", *arguments],
        cwd=where,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def _asked(where: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run the shipped script the way the recipe runs it."""
    return subprocess.run(  # noqa: S603 - the real script, as the recipe invokes it
        [str(FRESHNESS), *arguments],
        cwd=where,
        text=True,
        capture_output=True,
        check=False,
    )


def _repository(root: Path) -> Path:
    """A working repository on `main` with one commit and no remote yet."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "commit", "-q", "--allow-empty", "-m", "one")
    return root


def _remote(root: Path, name: str, at: Path) -> None:
    """Add a bare remote and put this repository's `main` on it."""
    subprocess.run(["git", "init", "-q", "--bare", str(at)], check=True)
    _git(root, "remote", "add", name, str(at))
    _git(root, "push", "-q", name, "main")


def test_the_upstream_git_records_decides_before_the_origin_ref_of_the_same_name(
    tmp_path: Path,
) -> None:
    """ "Its own origin ref" is the one git was told to track, not the one named `origin`.

    A branch tracking a second remote is tracking it deliberately, so that is the ref it
    is behind or level with — and the fallback to `origin/<base>` is what covers the
    ordinary clone where a base was fetched and never checked out with tracking. Both
    paths reach the same comparison, so which of them was taken is only visible in the
    ref the refusal names, which is what is asserted.
    """
    root = _repository(tmp_path / "work")
    _remote(root, "origin", tmp_path / "origin.git")
    _remote(root, "upstream", tmp_path / "upstream.git")
    _git(root, "branch", "--set-upstream-to", "upstream/main", "main")
    # Only the tracked remote moves. `origin/main` stays exactly level with the branch,
    # so a comparison that fell back to it would find nothing to say.
    behind = _git(root, "rev-parse", "HEAD")
    _git(root, "commit", "-q", "--allow-empty", "-m", "two")
    _git(root, "push", "-q", "upstream", "main")
    ahead = _git(root, "rev-parse", "HEAD")
    _git(root, "reset", "--hard", "-q", "HEAD~1")

    tracked = _asked(root, "main")

    assert tracked.returncode == 0, tracked.stderr
    assert "'upstream/main' is at" in tracked.stdout, (
        f"the comparison did not take the upstream git records for this branch, so a "
        f"branch tracking a second remote is judged against a ref it does not "
        f"track:\n{tracked.stdout}"
    )
    for named in (behind, ahead):
        assert named in tracked.stdout, tracked.stdout

    # The same disagreement with no upstream recorded, so only the fallback can find it.
    _git(root, "branch", "--unset-upstream", "main")
    _git(root, "push", "-q", "origin", f"{ahead}:refs/heads/main", "--force")
    _git(root, "fetch", "-q", "origin")

    fallen_back = _asked(root, "main")

    assert fallen_back.returncode == 0, fallen_back.stderr
    assert "'origin/main' is at" in fallen_back.stdout, (
        f"with no upstream recorded the comparison found no ref at all, so a base that "
        f"was fetched and never tracked is never checked:\n{fallen_back.stdout}"
    )


def test_a_base_no_ref_has_moved_past_is_answered_with_nothing_to_say(
    tmp_path: Path,
) -> None:
    """Four shapes that are not "behind", including the one a comparison cannot order.

    Level, ahead, and no origin ref at all are the states the recipe's own journey
    drives. Diverged is the fourth and it is here rather than there because it is the
    one a reader would expect to be refused: the branch and the ref hold commits the
    other does not, so the ref has not moved *past* anything — refusing it would refuse
    every worker judging against a base somebody rebased, which is a verdict they still
    want.
    """
    root = _repository(tmp_path / "work")
    _remote(root, "origin", tmp_path / "origin.git")
    _git(root, "branch", "--set-upstream-to", "origin/main", "main")

    assert _asked(root, "main").stdout == "", "a base level with its origin ref was refused"

    _git(root, "commit", "-q", "--allow-empty", "-m", "only here")
    assert _asked(root, "main").stdout == "", "a base ahead of its origin ref was refused"

    # The origin takes a commit of its own, from a branch cut before this one, so
    # neither ref contains the other.
    _git(root, "checkout", "-q", "-b", "elsewhere", "HEAD~1")
    _git(root, "commit", "-q", "--allow-empty", "-m", "only there")
    _git(root, "push", "-q", "origin", "elsewhere:main", "--force")
    _git(root, "checkout", "-q", "main")
    _git(root, "fetch", "-q", "origin")

    diverged = _asked(root, "main")

    assert diverged.returncode == 0, diverged.stderr
    assert diverged.stdout == "", (
        f"a base that has diverged from its origin ref was reported as behind it, so a "
        f"branch judged against a rebased base is refused:\n{diverged.stdout}"
    )
    assert _asked(root, "elsewhere").stdout == "", (
        "a branch with no origin ref of its own was compared against something"
    )


def test_a_question_this_cannot_answer_is_never_answered_as_a_fresh_base(
    tmp_path: Path,
) -> None:
    """The failure mode the exit statuses exist to prevent, in both its shapes.

    "Nothing to say" is this script's answer for a base nothing has moved past, and the
    recipe judges on the strength of it. So a caller that named no base, and a git that
    failed rather than answering, must not reach that answer — a repository error read
    as an absent upstream reports a stale base as fresh, which is the verdict over the
    wrong range this whole check exists to stop, wearing the check's own voice.
    """
    unnamed = _asked(_repository(tmp_path / "work"))

    assert unnamed.returncode == COULD_NOT_ANSWER, unnamed.stdout + unnamed.stderr
    assert "no base was named" in unnamed.stderr, unnamed.stderr
    assert "base-freshness.sh <base>" in unnamed.stderr, (
        f"the refusal does not say how to call it:\n{unnamed.stderr}"
    )

    outside = tmp_path / "not-a-repository"
    outside.mkdir()

    failed = _asked(outside, "main")

    assert failed.returncode == COULD_NOT_ANSWER, (
        f"git failed rather than answering and this reported the base as fresh, so the "
        f"recipe judges a base nothing checked:\n{failed.stdout}{failed.stderr}"
    )
    assert "rather than answering" in failed.stderr, failed.stderr
    assert "git rev-parse" in failed.stderr, (
        f"the refusal does not name the command that failed:\n{failed.stderr}"
    )
