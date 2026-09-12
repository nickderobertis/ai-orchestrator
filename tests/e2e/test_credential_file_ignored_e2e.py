"""What a copy of this checkout's credential file is to git, asked of git itself.

`scripts/credentials-env.sh` reads one gitignored `.env` at the repository root, and
the careful way to edit a file like that is to copy it first. The rule named `/.env`
exactly, so `.env.bak` — a live GitHub token — was ignored by nothing and tracked by
nothing: an untracked file in the working tree, one `git add -A` from a commit. That
happened here and a person caught it by reading a diff.

Nothing else in this repository would have. `orchestrator/redaction.py` and
`scripts/preserved-log.sh` hide credential-shaped values found in the **process
environment**, which never sees a file sitting in the tree, and both run downstream of
the commit in any case.

So the rule is asked of `git check-ignore` against this checkout rather than read out
of `.gitignore` as text: what a pattern means is git's answer and not a regular
expression's, and the exception below — an example file that stays committable — is
precisely where a pattern read by eye and a pattern read by git part company.

llmlint: ignore-file[shell_test_tiers_stay_split] These are pytest journeys rather than
a shell test suite, and what they cost is one `git check-ignore` apiece — milliseconds,
on a tool every checkout already has. A project of their own would be keyed on the one
file they read, `.gitignore`, which the root project's own key already covers.
"""

from __future__ import annotations

import subprocess

import pytest
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The file a launch reads, and the copies somebody editing it leaves beside it. Every
#: one of these is a whole credential file rather than a fragment, which is why the
#: rule covers the family instead of the one name.
CREDENTIAL_COPIES = (
    ".env",
    ".env.bak",
    ".env.backup",
    ".env.orig",
    ".env.save",
    ".env.2",
    ".env.local",
    ".env~",
)

#: The one member of that family that is documentation rather than a credential, so it
#: stays committable. Named back in explicitly, the way `/scratch/*` names its own one
#: exception, rather than left to depend on nobody having written one.
COMMITTABLE = ".env.example"


def _ignored(path: str) -> bool:
    """Whether git ignores ``path`` in this checkout, asked of git.

    `--no-index` so the answer is the rule's rather than the index's: a path that
    happened to be tracked would otherwise report as not ignored for that reason, which
    is the opposite of what is being asked.
    """
    asked = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "check-ignore", "--no-index", "--quiet", "--", path],
        capture_output=True,
        text=True,
        timeout=e2e_timeout(30),
        check=False,
    )
    assert asked.returncode in (0, 1), (
        f"git could not answer whether {path!r} is ignored: {asked.stderr.strip()}"
    )
    return asked.returncode == 0


@pytest.mark.parametrize("copy", CREDENTIAL_COPIES)
def test_a_copy_of_the_credential_file_is_ignored(copy: str) -> None:
    """The whole family, because the copy is what the careful edit produces."""
    assert _ignored(copy), (
        f"{copy} is not ignored by this checkout, so a copy of the credential file made "
        f"beside it sits in the working tree ready to be committed; widen `/.env*` in "
        f".gitignore"
    )


def test_the_example_file_stays_committable() -> None:
    """The exception is real: a widened rule that swallowed it would be a regression."""
    assert not _ignored(COMMITTABLE), (
        f"{COMMITTABLE} is ignored, so an example credential file could not be tracked; "
        f"`.gitignore` must name it back in after the `/.env*` family"
    )


def test_the_family_rule_stops_at_the_repository_root() -> None:
    """Anchored, so it is this checkout's own credential file and not every `.env` name.

    A `.env` some dependency ships inside a directory of its own is that project's
    content, and an unanchored rule would hide it from a reader of this tree.
    """
    assert not _ignored("scripts/.env"), (
        "the credential rule is matching `.env` files below the repository root; it must "
        "stay anchored with a leading slash, as `scripts/credentials-env.sh` reads only "
        "the root one"
    )
