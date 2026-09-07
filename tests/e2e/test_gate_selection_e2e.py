"""Real command-surface journeys for comparison-base selection."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from conftest import git

ROOT = Path(__file__).parents[2]

#: The status `scripts/comparison-base.sh` refuses a base that was named and cannot be
#: used with, read from that script rather than restated here. The hook runs the helper
#: and lets `set -e` carry its status out, so what these journeys hold is that the
#: refusal reaches the pusher intact — a literal would be a second source of the number
#: and would pass while the two drifted apart.
NAMED_BASE_UNUSABLE = int(
    re.search(
        r"^readonly NAMED_BASE_UNUSABLE=(\d+)$",
        (ROOT / "scripts/comparison-base.sh").read_text(encoding="utf-8"),
        re.MULTILINE,
    ).group(1)
)
#: Its sibling: the status for a tree that has no base to offer at all, which
#: `scripts/nx-selection.sh` answers by selecting every project. Read from the same
#: script, so the journey below holds the two apart rather than holding either to a
#: number written here.
NO_BASE_AVAILABLE = int(
    re.search(
        r"^readonly NO_BASE_AVAILABLE=(\d+)$",
        (ROOT / "scripts/comparison-base.sh").read_text(encoding="utf-8"),
        re.MULTILINE,
    ).group(1)
)


def _resolve(
    repo: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    # Both spellings are cleared by default: the journeys below that care about one
    # of them set it, and the rest are about discovery with neither named.
    return subprocess.run(
        [str(ROOT / "scripts/comparison-base.sh"), *args],
        cwd=repo,
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "ORCHESTRATOR_COMPARISON_BASE": "",
            "ORCHESTRATOR_COMPARISON_REMOTE": "",
            "ONEVCS_COMPARISON_BASE": "",
            "ONEVCS_COMPARISON_REMOTE": "",
            **(env or {}),
        },
    )


def test_comparison_base_uses_upstream_without_remote_head(tmp_path, bare_origin) -> None:
    origin = bare_origin(branch="main")
    clone = tmp_path / "clone"
    git("clone", str(origin), str(clone))
    assert _resolve(clone).stdout.strip() == "origin/main"

    for branch in ("ai-orchestrator/first", "ai-orchestrator/second"):
        git("branch", branch, "origin/main", cwd=clone)
        git("push", "origin", branch, cwd=clone)
    git("fetch", "--prune", "origin", cwd=clone)
    git("symbolic-ref", "--delete", "refs/remotes/origin/HEAD", cwd=clone)

    resolved = _resolve(clone)
    assert resolved.returncode == 0
    assert resolved.stdout.strip() == "origin/main"


def test_comparison_base_parts_a_remote_it_was_given_from_an_origin_it_has_none_of(
    tmp_path, bare_origin
) -> None:
    """Both are "no remote", and only one of them is somebody's mistake.

    A checkout with no `origin` is the state a fresh copy of a tree is in, and
    `scripts/nx-selection.sh` answers it by checking every project. A remote that was
    named — by an operator, or by the identity a lifecycle exports into this gate —
    and is not configured here is a value to repair, and answering it the same way
    would hide it behind a green run over a base nobody chose.
    """
    clone = tmp_path / "clone"
    git("clone", str(bare_origin(branch="main")), str(clone))
    bare = tmp_path / "bare"
    git("init", "-q", "-b", "main", str(bare))

    named = _resolve(clone, "nosuch", "main")
    absent = _resolve(bare)

    assert named.returncode == NAMED_BASE_UNUSABLE
    assert "comparison-base: remote 'nosuch' does not exist" in named.stderr
    assert "just gate <remote> <branch>" in named.stderr
    assert absent.returncode == NO_BASE_AVAILABLE, (
        "a checkout with no origin has no base to offer, which is a state and not a typo"
    )
    assert "this checkout has no 'origin' to compare against" in absent.stderr


def test_comparison_base_refuses_a_remote_name_no_ref_can_be_built_from(
    tmp_path, bare_origin
) -> None:
    """A remote nothing could name is the caller's to repair, and is not a tree with no base.

    `scripts/nx-selection.sh` answers "this tree has no base to offer" by selecting every
    project in silence, which is right for a fresh copy. A mistyped remote reaching that
    same branch would come back as a green full run rather than as the typo it is.
    """
    clone = tmp_path / "clone"
    git("clone", str(bare_origin(branch="main")), str(clone))

    refused = _resolve(clone, "bad//remote", "main")

    assert refused.returncode == NAMED_BASE_UNUSABLE
    assert "comparison-base: 'bad//remote' is not a valid remote name" in refused.stderr
    assert "just gate <remote> <branch>" in refused.stderr, (
        "a refused name has to say what a usable one would be"
    )


def test_comparison_base_requires_explicit_base_when_ambiguous(tmp_path, bare_origin) -> None:
    origin = bare_origin(branch="main")
    clone = tmp_path / "clone"
    git("clone", str(origin), str(clone))
    git("branch", "release", "origin/main", cwd=clone)
    git("push", "origin", "release", cwd=clone)
    git("fetch", "--prune", "origin", cwd=clone)
    git("symbolic-ref", "--delete", "refs/remotes/origin/HEAD", cwd=clone)
    git("checkout", "--detach", "origin/main", cwd=clone)

    ambiguous = _resolve(clone)
    assert ambiguous.returncode == 2
    assert "just gate origin <branch>" in ambiguous.stderr
    assert _resolve(clone, "origin", "main").stdout.strip() == "origin/main"


def test_pre_push_hook_clears_git_environment_and_forwards_comparison(tmp_path) -> None:
    clone = tmp_path / "clone"
    git("clone", str(ROOT), str(clone))
    shutil.copy2(ROOT / ".githooks/pre-push", clone / ".githooks/pre-push")
    git("remote", "rename", "origin", "upstream", cwd=clone)
    proc = subprocess.run(
        [str(clone / ".githooks/pre-push"), "upstream", str(ROOT)],
        cwd=clone,
        env={
            **os.environ,
            "GIT_DIR": str(ROOT / ".git"),
            "GIT_WORK_TREE": str(ROOT),
            "ORCHESTRATOR_COMPARISON_BASE": "invalid..base",
        },
        text=True,
        capture_output=True,
    )
    assert proc.returncode == NAMED_BASE_UNUSABLE
    assert "comparison remote=upstream base=invalid..base" in proc.stderr
    assert "comparison-base: 'invalid..base' is not a valid branch name" in proc.stderr


def test_comparison_base_takes_the_base_the_lifecycle_names(tmp_path, bare_origin) -> None:
    """One judged diff, one verdict — and `onevcs` is what names the diff now.

    The lifecycle exports the comparison ref into the gate it runs and the push it
    makes so the worker's verdict and the publishing push judge the same base. That
    export moved to `onevcs`'s own spelling when the recipes became a thin shell over
    it, and reading only `ORCHESTRATOR_COMPARISON_*` left the base unset on every
    lifecycle path: each side then resolved its own base, which is two diffs and two
    independent rolls of a non-deterministic judge.
    """
    origin = bare_origin(branch="main")
    clone = tmp_path / "clone"
    git("clone", str(origin), str(clone))
    git("branch", "release", "origin/main", cwd=clone)
    git("push", "origin", "release", cwd=clone)
    git("fetch", "--prune", "origin", cwd=clone)

    resolved = _resolve(clone, env={"ONEVCS_COMPARISON_BASE": "release"})

    assert resolved.returncode == 0, resolved.stderr
    assert resolved.stdout.strip() == "origin/release"


def test_an_explicit_operator_base_still_beats_the_lifecycles(tmp_path, bare_origin) -> None:
    """The `ORCHESTRATOR_*` spelling is also the documented operator override."""
    origin = bare_origin(branch="main")
    clone = tmp_path / "clone"
    git("clone", str(origin), str(clone))
    git("branch", "release", "origin/main", cwd=clone)
    git("push", "origin", "release", cwd=clone)
    git("fetch", "--prune", "origin", cwd=clone)

    resolved = _resolve(
        clone,
        env={"ORCHESTRATOR_COMPARISON_BASE": "main", "ONEVCS_COMPARISON_BASE": "release"},
    )

    assert resolved.returncode == 0, resolved.stderr
    assert resolved.stdout.strip() == "origin/main"


def test_comparison_base_takes_the_remote_the_lifecycle_names(tmp_path, bare_origin) -> None:
    """A comparison ref has two halves, and a checkout need not call its remote `origin`.

    Resolving the exported base against a hardcoded `origin` is the same two-diffs
    failure as not reading the base at all, in a clone whose publication remote has
    any other name.
    """
    origin = bare_origin(branch="main")
    clone = tmp_path / "clone"
    git("clone", str(origin), str(clone))
    git("remote", "rename", "origin", "upstream", cwd=clone)

    resolved = _resolve(
        clone,
        env={"ONEVCS_COMPARISON_REMOTE": "upstream", "ONEVCS_COMPARISON_BASE": "main"},
    )

    assert resolved.returncode == 0, resolved.stderr
    assert resolved.stdout.strip() == "upstream/main"


def test_an_explicit_operator_remote_still_beats_the_lifecycles(tmp_path, bare_origin) -> None:
    """The remote half honours the same precedence its base half does."""
    origin = bare_origin(branch="main")
    clone = tmp_path / "clone"
    git("clone", str(origin), str(clone))
    git("remote", "rename", "origin", "upstream", cwd=clone)
    git("remote", "add", "mirror", str(origin), cwd=clone)
    git("fetch", "mirror", cwd=clone)

    resolved = _resolve(
        clone,
        env={
            "ORCHESTRATOR_COMPARISON_REMOTE": "upstream",
            "ONEVCS_COMPARISON_REMOTE": "mirror",
            "ONEVCS_COMPARISON_BASE": "main",
        },
    )

    assert resolved.returncode == 0, resolved.stderr
    assert resolved.stdout.strip() == "upstream/main"


def test_the_pre_push_hook_reads_the_base_the_lifecycle_exported(tmp_path) -> None:
    """The hook is where the worker's verdict is replayed rather than re-rolled."""
    clone = tmp_path / "clone"
    git("clone", str(ROOT), str(clone))
    shutil.copy2(ROOT / ".githooks/pre-push", clone / ".githooks/pre-push")
    git("remote", "rename", "origin", "upstream", cwd=clone)
    proc = subprocess.run(
        [str(clone / ".githooks/pre-push"), "upstream", str(ROOT)],
        cwd=clone,
        env={
            **os.environ,
            "GIT_DIR": str(ROOT / ".git"),
            "GIT_WORK_TREE": str(ROOT),
            "ORCHESTRATOR_COMPARISON_BASE": "",
            "ONEVCS_COMPARISON_BASE": "invalid..base",
        },
        text=True,
        capture_output=True,
    )
    assert proc.returncode == NAMED_BASE_UNUSABLE
    # It reached the resolver as the base, rather than being discarded for discovery.
    assert "comparison remote=upstream base=invalid..base" in proc.stderr
