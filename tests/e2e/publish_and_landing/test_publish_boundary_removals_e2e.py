"""`just publish-branch` lands a removal of a private term and refuses an addition of one.

A public destination's publication is screened for the terms of every private
repository this host has registered. The adopted onevcs never refuses a removal
(https://github.com/nickderobertis/onevcs/pull/579, cut as 0.57.4), and the engine
links it, so a branch that merged a base which had already scrubbed a private name now
lands rather than having to be recut by hand. Additions are still screened, including
text a branch adds and then removes again.

Every part is real: the recipe, the engine's `onepipeline publish-branch`, the
`onevcs` it links, git, and a bare origin on disk. A scratch `ONEVCS_HOME` isolates
it. Its rules declare one synthetic public identity, which this journey publishes
into, and one synthetic private identity, whose name is the term. Both visibilities
are declared, so the host is never asked. A `local-direct` identity is never drafted
for, and the paid provider is substituted anyway, so a turn this journey did not
expect fails rather than spending a subscription.
"""

# Both findings these answer are about which Nx project owns this file: `publish-and-landing`,
# the directory-owned project keyed on the recipes, scripts and configs its landing journeys
# drive, beside `test_publish_branch_e2e.py`, whose edge and tier these share.
# llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] see above
# llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] see above

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
import short_state
from harness_indirections import established_indirections
from project_fixtures import helper
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

pytestmark = [pytest.mark.usefixtures("module_node_scratch")]

#: The base every publication here lands on.
BASE = "main"

#: The finished branch, in the shape a dispatch leaves behind. It carries no term,
#: because the branch name is screened as well.
BRANCH = "claude/finished-work"

#: The synthetic public identity the branch publishes into. Its git remote is the bare
#: origin on disk; this is the origin the identity is filed under.
PUBLIC_ORIGIN = "https://github.com/acme-corp/hosted.git"

#: The synthetic private identity. Its registration is the only thing that makes its
#: name a term, so it has a stub checkout and no origin anyone can reach.
PRIVATE_ORIGIN = "https://github.com/hiddenco/quietharbor.git"
TERM = "quietharbor"

#: A file the base carries from its first commit, naming the private term.
NOTES = "notes.md"
NOTES_WITH_TERM = f"# Notes\nsee {TERM} for details\na generic line\n"
NOTES_SCRUBBED = "# Notes\na generic line\n"

#: The work every branch here carries besides whatever it does to the term.
WORK = "shipped.txt"

RULES = """version: 4
trailer_prefix: Orchestrator-
rules:
  - match: {host: github.com, owner: acme-corp, name: hosted}
    publication: local-direct
    approvals: none
    visibility: public
  - match: {host: github.com, owner: hiddenco, name: quietharbor}
    publication: local-direct
    approvals: none
    visibility: private
default:
  publication: local-direct
  approvals: none
"""

FAKE_CODEX = helper("fake_codex.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")
INDIRECTION_CALLER = "tests/e2e/publish_and_landing/test_publish_boundary_removals_e2e.py"

#: What a refused publication says, without the term, the identity or the path.
REFUSAL = "public output carries a term of a private repository"


def _git(*arguments: str, cwd: Path) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=e2e", "-c", "user.email=e2e@example.invalid", *arguments],
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert done.returncode == 0, f"git {' '.join(arguments)}: {done.stderr or done.stdout}"
    return done.stdout


def _just(*arguments: str, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["just", *arguments],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        check=False,
    )


def _commit(checkout: Path, message: str) -> None:
    _git("add", "-A", cwd=checkout)
    _git("commit", "-q", "-m", message, cwd=checkout)


class Scratch:
    """The public identity's checkout and origin, and the environment reading the registry."""

    def __init__(self, tmp_path: Path) -> None:
        home = tmp_path / "onevcs-home"
        home.mkdir()
        (home / "rules.yml").write_text(RULES, encoding="utf-8")
        seed = tmp_path / "seed"
        _git("init", "-q", "-b", BASE, str(seed), cwd=tmp_path)
        (seed / NOTES).write_text(NOTES_WITH_TERM, encoding="utf-8")
        _commit(seed, "feat: seed the notes")
        self.origin = tmp_path / "origin.git"
        _git("clone", "-q", "--bare", str(seed), str(self.origin), cwd=tmp_path)
        self.checkout = tmp_path / "checkout"
        _git("clone", "-q", str(self.origin), str(self.checkout), cwd=tmp_path)
        private = tmp_path / "private-stub"
        _git("init", "-q", "-b", BASE, str(private), cwd=tmp_path)
        _git("commit", "-q", "--allow-empty", "-m", "init", cwd=private)

        environment = dict(os.environ)
        environment["ONEVCS_HOME"] = str(home)
        # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
        environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
        # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
        environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
        environment["FAKE_CODEX_ANSWERS"] = json.dumps([json.dumps({"body": "unexpected"})])
        self.attempts = tmp_path / "launches"
        environment["FAKE_CODEX_ATTEMPT_LOG"] = str(self.attempts)
        environment["XDG_STATE_HOME"] = str(short_state.state_home(tmp_path))
        environment.update(established_indirections(INDIRECTION_CALLER))
        self.environment = environment

        for checkout, origin in ((self.checkout, PUBLIC_ORIGIN), (private, PRIVATE_ORIGIN)):
            registered = _just(
                "register-repo", str(checkout), "--origin", origin, environment=environment
            )
            assert registered.returncode == 0, registered.stderr + registered.stdout

    def advance_base(self, content: str, message: str) -> None:
        """Land a change on the origin's base, as another landing would."""
        _git("checkout", "-q", BASE, cwd=self.checkout)
        (self.checkout / NOTES).write_text(content, encoding="utf-8")
        _commit(self.checkout, message)
        _git("push", "-q", "origin", BASE, cwd=self.checkout)

    def branch(self) -> None:
        _git("checkout", "-q", "-b", BRANCH, cwd=self.checkout)
        (self.checkout / WORK).write_text("the work\n", encoding="utf-8")
        _commit(self.checkout, "feat: finish the work")

    def publish(self) -> subprocess.CompletedProcess[str]:
        _git("checkout", "-q", BASE, cwd=self.checkout)
        return _just(
            "publish-branch",
            BRANCH,
            "--repo",
            str(self.checkout),
            "--title",
            "feat: land the finished work",
            environment=self.environment,
        )

    def base_tip(self) -> str:
        return _git("rev-parse", BASE, cwd=self.origin).strip()

    def base_tree(self) -> list[str]:
        return _git("ls-tree", "--name-only", BASE, cwd=self.origin).split()


def _merged_a_scrubbed_base(scratch: Scratch) -> None:
    """The branch leaves the base while it carries the term; the base then scrubs it,
    and the branch merges that base in. The merge's diff against the branch's own parent
    removes a line the destination no longer carries, which the old check refused."""
    scratch.branch()
    _git("checkout", "-q", BASE, cwd=scratch.checkout)
    scratch.advance_base(NOTES_SCRUBBED, "fix: scrub the notes")
    _git("checkout", "-q", BRANCH, cwd=scratch.checkout)
    _git("merge", "-q", "--no-edit", BASE, cwd=scratch.checkout)


def _removes_the_term(scratch: Scratch) -> None:
    """The branch's own change deletes the term-bearing line the base carries."""
    scratch.branch()
    (scratch.checkout / NOTES).write_text(NOTES_SCRUBBED, encoding="utf-8")
    _commit(scratch.checkout, "fix: scrub the notes")


def _adds_the_term(scratch: Scratch) -> None:
    scratch.branch()
    (scratch.checkout / "more.md").write_text(f"ask {TERM} again\n", encoding="utf-8")
    _commit(scratch.checkout, "feat: add more notes")


def _adds_then_removes_the_term(scratch: Scratch) -> None:
    """Text added and removed again was never public at the base, so the commit that
    added it still refuses, whatever a later commit does."""
    _adds_the_term(scratch)
    (scratch.checkout / "more.md").unlink()
    _commit(scratch.checkout, "fix: drop the extra notes")


@pytest.mark.parametrize(
    "prepare",
    [
        pytest.param(_merged_a_scrubbed_base, id="merged-a-scrubbed-base"),
        pytest.param(_removes_the_term, id="plain-removal"),
    ],
)
def test_a_removal_of_a_private_term_lands(
    tmp_path: Path, prepare: Callable[[Scratch], None]
) -> None:
    scratch = Scratch(tmp_path)
    prepare(scratch)
    before = scratch.base_tip()

    published = scratch.publish()

    assert published.returncode == 0, published.stderr + published.stdout
    assert scratch.base_tip() != before, published.stdout
    assert WORK in scratch.base_tree(), f"the work never reached the origin's {BASE}"
    notes = _git("show", f"{BASE}:{NOTES}", cwd=scratch.origin)
    assert TERM not in notes, notes
    assert not scratch.attempts.exists(), "a local-direct landing spent a drafting turn"


@pytest.mark.parametrize(
    "prepare",
    [
        pytest.param(_adds_the_term, id="adds-the-term"),
        pytest.param(_adds_then_removes_the_term, id="adds-then-removes-the-term"),
    ],
)
def test_an_addition_of_a_private_term_is_refused(
    tmp_path: Path, prepare: Callable[[Scratch], None]
) -> None:
    scratch = Scratch(tmp_path)
    prepare(scratch)
    before = scratch.base_tip()

    published = scratch.publish()

    assert published.returncode != 0, published.stdout
    assert REFUSAL in published.stderr + published.stdout, published.stderr + published.stdout
    assert TERM not in published.stderr + published.stdout, "the refusal named the term"
    assert scratch.base_tip() == before, "a refused publication moved the origin's base"
    assert WORK not in scratch.base_tree()
