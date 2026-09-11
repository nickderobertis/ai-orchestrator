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

**An identity is seeded under a hosted origin the same way, without a host.** `onevcs`
files a repository under the normalized origin its clone's own remote names, so a clone
made from `git@github.com:owner/name.git` is registered as `github.com/owner/name` —
which is the identity a task record's `repositories` field names, and the shape every
plan this host writes names its repository in. What answers that remote is a fake `ssh`:
`GIT_SSH_COMMAND` names a script that ignores the host and `exec`s the
`git-upload-pack` / `git-receive-pack` it is handed against the bare origin on disk, so
every fetch and push the identity's checkouts make reaches the seed and nothing leaves
this machine. `url.<base>.insteadOf` does **not** do this — `onevcs` reads the effective
URL, so the identity would come out as the path. The variable travels in
:attr:`Identity.environment`, which a journey merges into the environment of every
process that reaches the origin: the launch, and through it every `onevcs` verb a
dispatch runs.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

from conftest import git
from waits import timeout as e2e_timeout

from orchestrator.project_store import hosted_origin
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

#: The origin `scripts/plan-brief.sh` names as a planning flow's default publication
#: repository — this repository's own, as `onevcs` files it. A journey driving `just
#: plan` or `just finish-plan` as an operator types them seeds a scratch identity under
#: this origin, so the record the recipe writes when nobody tells it anything is one its
#: launch can resolve, in a registry that is never this host's.
PLANNING_FLOW_ORIGIN = "github.com/nickderobertis/ai-orchestrator"

#: The variable git reaches an ssh remote through, and the one name a hosted scratch
#: identity puts in a journey's environment.
GIT_SSH_COMMAND = "GIT_SSH_COMMAND"

#: The fake `ssh` a hosted origin is served through. Called as `ssh <host> <command>`,
#: where the command is `git-upload-pack 'owner/name.git'` or `git-receive-pack
#: 'owner/name.git'`; it holds the requested path to the one repository it serves, so a
#: clone of any other name fails as a real host would refuse it, and runs the verb
#: against the bare origin on disk.
FAKE_SSH = """#!/usr/bin/env bash
set -euo pipefail
shift
read -r verb requested <<<"$1"
requested=${{requested#\\'}}
requested=${{requested%\\'}}
if [ "$requested" != {expected} ]; then
    echo "fake-ssh: this origin serves {expected}, not $requested" >&2
    exit 1
fi
exec "$verb" {origin}
"""


class Identity(NamedTuple):
    """One seeded identity: where its work publishes to, and what it executes in."""

    #: The checkout a node's `repo` names, which a landing fast-forwards.
    publication: Path
    #: The checkout a session clones to cut its worktree from, named by alias.
    execution: Path
    #: The scratch registry both are registered in, for `ONEVCS_HOME`.
    home: Path
    #: What a process reaching this identity's origin over git needs in its environment:
    #: empty for an origin on disk, and the fake `ssh` for a hosted one. A journey merges
    #: it into the environment of its launch, which every `onevcs` verb the dispatch
    #: runs inherits.
    environment: dict[str, str]


def seeded(
    root: Path,
    *,
    publication: str = "publication",
    execution: str = "execution",
    origin: str | None = None,
) -> Identity:
    """Seed a bare origin and two clones of it, and register them in a scratch registry.

    The two clone names are the caller's because they become the registered aliases,
    and a journey driving a recipe that resolves a checkout by name needs them to be the
    names that recipe resolves.

    ``origin`` names the identity `onevcs` registers the pair under, as a normalized
    `host/owner/name`; left ``None``, the identity is the bare origin's own path, which
    is the one shape the reserved `onepipeline.repo` key exists for. Given one, both
    clones are made from that host's ssh remote through the fake `ssh` the module
    docstring describes, and the rules file matches the identity by host and owner.
    """
    bare = root / "origin.git"
    seed = root / "seed"
    git("init", "-q", "--bare", "-b", "main", str(bare))
    git("init", "-q", "-b", "main", str(seed))
    (seed / "README.md").write_text("seed\n", encoding="utf-8")
    git("add", "-A", cwd=seed)
    git(*GIT_IDENTITY, "commit", "-qm", "chore: seed", cwd=seed)
    git("remote", "add", "origin", str(bare), cwd=seed)
    git("push", "-q", "origin", "main", cwd=seed)
    environment: dict[str, str] = {}
    remote = str(bare)
    rules = RULES
    if origin is not None:
        host, owner, name = _hosted(origin)
        served = root / "fake-ssh"
        served.write_text(
            FAKE_SSH.format(
                expected=shlex.quote(f"{owner}/{name}.git"), origin=shlex.quote(remote)
            ),
            encoding="utf-8",
        )
        served.chmod(0o755)
        # The one thing substituted is the transport to a host this suite may never
        # reach: git, `onevcs`, the registry, the clones and every fetch and push are
        # real, and only the bytes leave for a bare origin on disk instead of GitHub.
        # See the module docstring.
        # llmlint: ignore[e2e_not_mocked] only the transport to GitHub is substituted
        environment[GIT_SSH_COMMAND] = str(served)
        remote = f"git@{host}:{owner}/{name}.git"
        rules = rules_for_hosted(host, owner)
    identity = Identity(
        publication=root / publication,
        execution=root / execution,
        home=root / "onevcs",
        environment=environment,
    )
    for clone in (identity.publication, identity.execution):
        cloned = subprocess.run(
            ["git", "clone", "-q", remote, str(clone)],
            env={**os.environ, **environment},
            text=True,
            capture_output=True,
            timeout=e2e_timeout(60),
            check=False,
        )
        assert cloned.returncode == 0, f"cloning {remote} failed:\n{cloned.stderr}"
    identity.home.mkdir(exist_ok=True)
    _register(root, identity, rules)
    return identity


def _hosted(origin: str) -> tuple[str, str, str]:
    """``origin``'s host, owner and name, refused unless it is a normalized origin."""
    normalized = hosted_origin(origin)
    assert normalized is not None, (
        f"a hosted scratch identity is seeded under a normalized `host/owner/name` origin, "
        f"not {origin!r}"
    )
    host, owner, name = normalized.split("/")
    return host, owner, name


def rules_for_hosted(host: str, owner: str) -> str:
    """A rules file matching every identity of ``owner`` on ``host``, merged locally.

    `local-direct` for the reason :data:`RULES` is: it keeps the publication off `gh`,
    which a scratch identity has no change request on and this machine no credential
    for. A hosted identity cannot be matched by `path`, so the rule names the two halves
    of its origin the way `config/onevcs.rules.yml` names this host's own.
    """
    return RULES.replace('match: {path: "*"}', f"match: {{host: {host}, owner: {owner}}}")


def _register(root: Path, identity: Identity, rules_text: str = RULES) -> None:
    """Bring the scratch registry up to a scratch configuration, through the real recipe."""
    manifest = root / "onevcs.checkouts"
    manifest.write_text(
        "".join(f"{path}\n" for path in (identity.publication, identity.execution)),
        encoding="utf-8",
    )
    rules = root / "onevcs.rules.yml"
    rules.write_text(rules_text, encoding="utf-8")
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


def rules_for(publication: str) -> str:
    """A whole-registry policy naming ``publication`` for every identity in it.

    One policy per registry rather than a rule per identity, because `onevcs`'s `path`
    matcher answers a scratch registry's identities only through its catch-all — a
    journey that needs two workflows uses two registries and two `ONEVCS_HOME`s, which
    is what a run does anyway.
    """
    return RULES.replace("local-direct", publication)


def registered(
    root: Path, names: Sequence[str], *, publication: str = "local-direct"
) -> tuple[Path, ...]:
    """Seed one checkout per name in a scratch registry, and answer where each one is.

    The registry is `root / "onevcs"`, which is the `ONEVCS_HOME` a caller exports; each
    checkout is `root / name`, and the alias `onevcs` gives it is that directory name —
    so a plan node naming it as its `repo` resolves the identity this seeded.

    Its own origin per name, because two checkouts of one origin are two aliases of one
    identity and share its whole policy: what a caller wants from several names is
    several identities.
    """
    checkouts = []
    for name in names:
        origin = root / f"{name}-origin.git"
        seed = root / f"{name}-seed"
        git("init", "-q", "--bare", "-b", "main", str(origin))
        git("init", "-q", "-b", "main", str(seed))
        (seed / "README.md").write_text("seed\n", encoding="utf-8")
        git("add", "-A", cwd=seed)
        git(*GIT_IDENTITY, "commit", "-qm", "chore: seed", cwd=seed)
        git("remote", "add", "origin", str(origin), cwd=seed)
        git("push", "-q", "origin", "main", cwd=seed)
        git("clone", "-q", str(origin), str(root / name))
        checkouts.append(root / name)
    home = root / "onevcs"
    home.mkdir(exist_ok=True)
    manifest = root / "onevcs.checkouts"
    manifest.write_text("".join(f"{path}\n" for path in checkouts), encoding="utf-8")
    rules = root / "onevcs.rules.yml"
    rules.write_text(rules_for(publication), encoding="utf-8")
    applied = subprocess.run(
        ["just", "repos-apply", "--checkouts", str(manifest), "--rules", str(rules)],
        cwd=REPO_ROOT,
        env={**os.environ, "ONEVCS_HOME": str(home)},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert applied.returncode == 0, f"repos-apply failed:\n{applied.stdout}\n{applied.stderr}"
    return tuple(checkouts)
