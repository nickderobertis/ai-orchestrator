"""A registered repository identity a journey may publish into, made from nothing.

Every journey that drives a lifecycle node needs one, and it may never be this host's.
A dispatched lifecycle session clones a registered execution checkout, cuts a worktree,
and — on the way in — reclaims run roots under the state root it was given, so a
journey pointed at the real registry can destroy a live dispatch's working directory.
That is not hypothetical: three dispatches of one run were destroyed within ninety
seconds of launch by a sibling's `session open`.

So the identity is seeded here: a bare origin with one commit, a clone to publish
from, and a clone to execute in, registered through the real `just repos-apply` against
a scratch `ONEVCS_HOME`. The alias `onevcs` gives each checkout is its **directory
name**, which is why the caller names the directories: a journey whose recipe resolves
a checkout by alias needs the scratch one to answer to that alias.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import NamedTuple

from conftest import git
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The scratch identity's policy: merged in the local checkout. It names no verifier
#: because onevcs 0.11.0 removed the concept, and a registered checkout that matched no
#: rule fails `just repos-apply` outright — so the catch-all is what makes a seeded
#: identity registrable at all.
RULES = """version: 3
trailer_prefix: Orchestrator-
rules:
  - match: {path: "*"}
    publication: local-direct
    approvals: none
default:
  publication: local-direct
  approvals: none
"""

#: The committer a seed commit carries. `tests/conftest.py` exports one per test, and
#: the fixtures that call this are module-scoped, so they run before it has.
GIT_IDENTITY = ("-c", "user.email=test@example.com", "-c", "user.name=ai-orchestrator-test")


class Identity(NamedTuple):
    """One seeded identity: where its work publishes to, and what it executes in."""

    #: The checkout a node's `repo` names, which a landing fast-forwards.
    publication: Path
    #: The checkout a session clones to cut its worktree from, named by alias.
    execution: Path
    #: The scratch registry both are registered in, for `ONEVCS_HOME`.
    home: Path


def seeded(
    root: Path, *, publication: str = "publication", execution: str = "execution"
) -> Identity:
    """Seed a bare origin and two clones of it, and register them in a scratch registry.

    The two clone names are the caller's because they become the registered aliases,
    and a journey driving a recipe that resolves a checkout by name needs them to be the
    names that recipe resolves.
    """
    origin = root / "origin.git"
    seed = root / "seed"
    git("init", "-q", "--bare", "-b", "main", str(origin))
    git("init", "-q", "-b", "main", str(seed))
    (seed / "README.md").write_text("seed\n", encoding="utf-8")
    git("add", "-A", cwd=seed)
    git(*GIT_IDENTITY, "commit", "-qm", "chore: seed", cwd=seed)
    git("remote", "add", "origin", str(origin), cwd=seed)
    git("push", "-q", "origin", "main", cwd=seed)
    identity = Identity(
        publication=root / publication, execution=root / execution, home=root / "onevcs"
    )
    git("clone", "-q", str(origin), str(identity.publication))
    git("clone", "-q", str(origin), str(identity.execution))
    identity.home.mkdir(exist_ok=True)
    _register(root, identity)
    return identity


def _register(root: Path, identity: Identity) -> None:
    """Bring the scratch registry up to a scratch configuration, through the real recipe."""
    manifest = root / "onevcs.checkouts"
    manifest.write_text(
        "".join(f"{path}\n" for path in (identity.publication, identity.execution)),
        encoding="utf-8",
    )
    rules = root / "onevcs.rules.yml"
    rules.write_text(RULES, encoding="utf-8")
    applied = subprocess.run(
        ["just", "repos-apply", "--checkouts", str(manifest), "--rules", str(rules)],
        cwd=REPO_ROOT,
        env={**os.environ, "ONEVCS_HOME": str(identity.home)},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert applied.returncode == 0, f"repos-apply failed:\n{applied.stdout}\n{applied.stderr}"
