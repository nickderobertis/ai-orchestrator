"""`just integrate` lands a complete branch of a hosted identity whose rules resolve `local-direct`.

The landing-verb table in `docs/repo-lifecycle.md` names the merge train for a branch that
is *complete, and the identity publishes `local-direct`*. This repository's own identity
met that condition exactly and the verb refused it — `direct integration is refused for
identity … (repo_type: team)` — because through onevcs 0.19.3 it gated on two stored
identity fields `register` inferred from whether the origin had a host, which nothing
could configure and which read `remote` / `team` for every `github.com` identity here.
onevcs 0.21.0 (https://github.com/nickderobertis/onevcs/pull/136) removed the two fields
and gates the train on the resolved publication policy, which is what this journey holds
the recipe to: a hosted scratch identity — `github.com/…`, served over the fake `ssh`
`tests/e2e/scratch_identity.py` describes — whose rules resolve `local-direct`, landed by
the train through the real recipe; and the same identity under a change-request policy,
refused by name.

The recipe is driven the way an operator drives it for a repository other than this one:
`onevcs integrate` reads the checkout it is run *inside*, so the justfile is named and the
working directory is the identity's publication checkout, with `uv` pointed back at this
project so the recipe's `uv run` resolves the installed `onevcs` rather than looking for
a project in the scratch checkout.

llmlint: ignore-file[e2e_not_mocked] The scratch identity stands in for this host's own
clones, which a test may not land work in; the recipe, `onevcs`, git, and the registry
are all real, and only the transport to a host this suite may never reach is substituted.
"""

# The finding these answer is about which Nx project owns this file. It sits beside
# `tests/e2e/test_publish_branch_e2e.py`, the landing journey it is the shape of, in the
# code-keyed tier every real `onevcs` landing in this repository is in; what it depends
# on — the recipe, the installed `onevcs`, the scratch identity — is the whole of that
# key, so no narrower edge exists to put it behind.
# llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] see above
# llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] see above

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from conftest import git
from scratch_identity import GIT_IDENTITY, rules_for_hosted, seeded
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: A hosted identity, spelled as `onevcs` files one and as every plan here names one.
#: `register` used to record it `remote` / `team` on the strength of the host alone.
ORIGIN = "github.com/scratchowner/merge-train"
HOST, OWNER = "github.com", "scratchowner"
#: The finished branch the train lands, and the file its one commit adds.
BRANCH = "feature/landed-by-the-train"
LANDED_FILE = "landed.txt"


def _finished_branch(checkout: Path) -> None:
    """Cut one finished branch off the base in ``checkout``, and return to the base."""
    git("checkout", "-q", "-b", BRANCH, cwd=checkout)
    (checkout / LANDED_FILE).write_text("landed by the train\n", encoding="utf-8")
    git("add", "-A", cwd=checkout)
    git(*GIT_IDENTITY, "commit", "-qm", "feat: add the file the train lands", cwd=checkout)
    git("checkout", "-q", "main", cwd=checkout)


def _integrate(
    checkout: Path, home: Path, transport: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run the real recipe inside ``checkout``, against the scratch registry."""
    return subprocess.run(
        [
            "just",
            "--justfile",
            str(REPO_ROOT / "justfile"),
            "--working-directory",
            str(checkout),
            "integrate",
            BRANCH,
            "--push",
        ],
        cwd=checkout,
        env={
            **os.environ,
            **transport,
            "ONEVCS_HOME": str(home),
            # The recipe's `uv run` resolves this project's environment from wherever
            # it is run; the scratch checkout has no project of its own.
            "UV_PROJECT": str(REPO_ROOT),
        },
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        check=False,
    )


def test_the_train_lands_a_complete_branch_of_a_hosted_local_direct_identity(
    tmp_path: Path,
) -> None:
    """The table's row, as the verb now enforces it: the policy, whatever the host.

    Landed for real — merged into the base in the publication checkout and pushed to the
    origin — so what is asserted is the base carrying the branch's file on both ends,
    not the absence of a refusal.
    """
    identity = seeded(tmp_path, origin=ORIGIN)
    _finished_branch(identity.publication)

    landed = _integrate(identity.publication, identity.home, identity.environment)

    assert landed.returncode == 0, landed.stdout + landed.stderr
    assert f"{BRANCH}: merged" in landed.stdout, landed.stdout
    assert "Base advanced: yes" in landed.stdout, landed.stdout
    assert "Pushed: yes" in landed.stdout, landed.stdout
    assert "repo_type" not in landed.stderr, landed.stderr
    assert (identity.publication / LANDED_FILE).is_file()
    # The origin took the push: the execution clone, fetched, sees the file on `main`.
    git("fetch", "-q", "origin", cwd=identity.execution, env={**os.environ, **identity.environment})
    on_origin = git("ls-tree", "--name-only", "origin/main", cwd=identity.execution)
    assert LANDED_FILE in on_origin.split(), on_origin


def test_the_train_refuses_an_identity_whose_rules_resolve_a_change_request_policy(
    tmp_path: Path,
) -> None:
    """The refusal that replaced the stored one names the policy, and lands nothing.

    The same hosted identity, re-ruled to `change-auto` after registration — the rules
    file is what every verb reads, so no re-registration is needed — is refused by the
    policy the rules resolve, in words that tell an operator which file to edit.
    """
    identity = seeded(tmp_path, origin=ORIGIN)
    _finished_branch(identity.publication)
    (identity.home / "rules.yml").write_text(
        rules_for_hosted(HOST, OWNER).replace(
            "publication: local-direct", "publication: change-auto"
        ),
        encoding="utf-8",
    )

    refused = _integrate(identity.publication, identity.home, identity.environment)

    assert refused.returncode != 0, refused.stdout + refused.stderr
    assert "direct integration is refused" in refused.stderr, refused.stderr
    assert "change-auto" in refused.stderr, refused.stderr
    assert "local-direct" in refused.stderr, refused.stderr
    assert "repo_type" not in refused.stderr, refused.stderr
    assert not (identity.publication / LANDED_FILE).exists()
