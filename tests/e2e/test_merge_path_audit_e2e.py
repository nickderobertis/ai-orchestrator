"""`scripts/repos.sh` absorbs one flag-spelling difference; the audit itself is onevcs's.

The published audit answers, per identity, each check its merge path requires, read off
that repository's own branch protection and rulesets. `onevcs` calls the flag
`--audit-gates`; the planner doctrine calls it `--audit-gate-coverage`; the recipe's
whole job beyond passing arguments straight through is accepting either spelling and
reaching the same place. That translation is the one thing this repository adds, so it
is the one thing this file tests.

Whether `onevcs` correctly reads a real repository's branch protection — or reports an
identity with none as requiring none, or refuses a bad argument, or handles an empty
registry — is `onevcs`'s own claim to prove in its own suite, not a second proof this
repository owes it. Earlier versions of this file asserted on specific required-check
names and reports read live off real GitHub repositories this host does not own
(`nickderobertis/nick-derobertis-site`, `nickderobertis/llmlint`), and separately on
behaviors — an empty registry's wording, a refused argument's silence, a plain
listing's omissions — that are `onevcs`'s own CLI contract rather than anything this
wrapper adds. Both depended on another repository's live, unpinned state or another
tool's own behavior for this repository's own gate to pass, and both drifted: the
first when `nickderobertis/llmlint`'s branch protection changed, the second would drift
identically the day `onevcs` reworded a message this repository never asserts anyone
reads. Both are gone. Nothing here registers a checkout with a real remote, or asks
anything of a repository this project does not own.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT


def repos(home: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run the real recipe against a scratch registry root."""
    return subprocess.run(
        ["just", *arguments],
        cwd=REPO_ROOT,
        env={**os.environ, "ONEVCS_HOME": str(home)},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
    )


def test_the_published_flag_spelling_reaches_the_same_report(tmp_path: Path) -> None:
    """Both spellings are accepted, and both get the same answer from an empty registry.

    Empty, so this is content-agnostic by construction rather than by omission: there is
    nothing registered for either spelling to ask a real repository about, so the two
    outputs can only agree by both reaching the one audit the recipe wraps.
    """
    home = tmp_path / "onevcs"
    home.mkdir()
    (home / "rules.yml").write_text(
        "version: 3\ntrailer_prefix: Orchestrator-\nrules: []\n"
        "default:\n  publication: change-auto\n  approvals: none\n",
        encoding="utf-8",
    )

    published = repos(home, "repos", "--audit-gates")

    assert published.returncode == 0, published.stderr
    assert published.stdout == repos(home, "repos", "--audit-gate-coverage").stdout
