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

# The finding these answer is about which Nx project owns this file, so it is the file
# that is suppressed and a project split that would resolve it. The third is the same
# question asked of a shell suite, which these are not: they are pytest journeys over
# the same recipes and scripts, and the xdist group is what holds them off the toolchain
# lock.
# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] see above
# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] see above
# llmlint: ignore-block[shell_test_tiers_stay_split] see above

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import stat
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import pytest
from harness_indirections import established_indirections
from nx_workspace import SHARED_TOOLCHAIN_GROUP
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: Every journey here reaches its tool through `uv run`, which waits on the exclusive
#: lock `uv` holds on this checkout's `.venv` while another journey re-provisions it.
#: The constant rather than a string, because `--dist loadgroup` co-locates only tests
#: sharing one group *name*, and the writers name this one.
pytestmark = pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP)

#: The branch under test, in the shape a dispatch leaves behind.
FINISHED_BRANCH = "claude/finished-work"

#: The file the published commit adds, and how the assertion finds it on the base.
PUBLISHED_FILE = "shipped.txt"

#: The base every identity here publishes onto.
BASE = "main"

#: The types the scratch repository's `commit-msg` hook releases from, and the words it
#: refuses everything else with. A miniature of `.githooks/commit-msg`: what is under
#: test is that `onevcs` asks a repository's hook about the subject it is about to land,
#: not this policy's own wording, so the hook states the smallest policy that can refuse.
RELEASING_TYPES = ("feat", "fix", "perf")
HOOK_REFUSAL = "this repository does not release from that type"

#: How the adopted onevcs refuses a subject the repository turns down, quoted to the
#: words that make it 0.6.1's refusal and not the release before it, re-measured on
#: every adoption since — which is what this journey is for. Kept phrased against the
#: *arrival* rather than the pin: the adopted onevcs is several releases past it and has
#: changed nothing here, and a bump that had to retype this number would invite retyping
#: it without re-reading which release the words belong to. The pin is deliberately not
#: named: this journey drives whichever onevcs `config/onevcs.version` installs, so the
#: release under test is that one and re-stating its number here would be a second
#: source for it.
#:
#: The discriminator is load-bearing and was measured both ways. Below 0.6.1 the hook was
#: still *reached* — a clone carries `core.hooksPath`, and git runs the hook itself on the
#: squash commit a publication writes — so a journey asserting only "refused" passes on
#: both releases and proves nothing. What 0.6.1 added is asking **before** anything is
#: written, and saying so: 0.5.0 answered `invalid input: git commit -m <subject> failed
#: (exit 1)` from the far side of a gate run and a merge, with the fix left for the
#: operator to infer.
#: The other half of that refusal: what to do about it, which the bare git failure never
#: said. `--title` is the lever this journey itself pulls, so the sentence naming it is
#: the one an operator most needs.

#: A subject the hook accepts, and one it does not. Both are passed as `--title`, so the
#: branch's own commits stay acceptable and the only thing under judgement is the subject
#: the publication composed — which is the whole of what a change request's title is.
RELEASING_TITLE = "feat: land the finished work"
NON_RELEASING_TITLE = "docs: describe the finished work"

#: What `_finished_branch` commits under. Releasing, so the hook accepts it when git
#: runs the hook on that commit — which is why it is the *first* subject recorded, and
#: why a `--title` is what the two journeys below actually vary.
BRANCH_SUBJECT = "feat: finish the work"

#: The issue-closing lines a branch's commits carry, and the one line each survives a
#: squash as. GitHub closes an issue from such a line only in the commit that reaches
#: the default branch, and a `local-direct` publication composes that commit's message
#: itself — the subject and the provenance trailers — so before
#: https://github.com/nickderobertis/onevcs/pull/163 every issue a landing delivered
#: stayed open until somebody closed it by hand. Written in the three shapes the host
#: reads — a bare number, an `owner/name#N`, and a github.com issue URL — in a sentence
#: and with a second keyword for one of them, because what is asserted is the normalised
#: set the squash carries, each issue once, and not the prose the commits happened to say.
CLOSING_SHAPES = ("#12", "acme-corp/tracker#7", "https://github.com/acme-corp/tracker/issues/9")
CLOSING_COMMITS = (
    f"{BRANCH_SUBJECT}\n\nThis fixes {CLOSING_SHAPES[0]} and Closes {CLOSING_SHAPES[1]}.\n",
    f"feat: finish the rest of the work\n\nResolves {CLOSING_SHAPES[2]}\n"
    f"Fixes {CLOSING_SHAPES[0]}\n",
)
#: What the squash's message has to carry after its subject: one line per issue, the
#: keyword capitalised, the first spelling kept — and a github.com URL written back as
#: the `owner/name#N` the host reads it as, which is the one shape onevcs rewrites.
CLOSING_REFERENCES = ("#12", "acme-corp/tracker#7", "acme-corp/tracker#9")
CLOSING_LINES = (
    f"Fixes {CLOSING_REFERENCES[0]}",
    f"Closes {CLOSING_REFERENCES[1]}",
    f"Resolves {CLOSING_REFERENCES[2]}",
)

#: One pipe buffer on Linux, which is the size a verifier had to exceed on stderr to
#: wedge the reader this host used to work around. Both streams are driven past it.
PIPE_BUFFER = 64 * 1024
#: Lines per stream, and their width. 8000 x ~48 bytes is ~375 KiB each way — several
#: buffers, so the ordering of the reads matters rather than being incidental.
LOUD_LINES = 8000
#: A `pre-push` hook that is loud on both pipes and then succeeds. The tier this used
#: to be written against was the identity gate, which onevcs 0.11.0 removed; the
#: property is not the tier's, it is the reader's, and the merge path's own verifier is
#: what `onevcs` now reads both pipes of.
LOUD_HOOK = (
    f"n=0; while [ $n -lt {LOUD_LINES} ]; do "
    "echo loud-gate-stdout-line-payload-padding-0123456789; "
    "echo loud-gate-stderr-line-payload-padding-0123456789 >&2; "
    "n=$((n+1)); done"
)


#: The remote host's decisioning, as the program `ONEVCS_GH` names. Only the change
#: requests it is asked to open are substituted; the pushes beside them are real git.
FAKE_GH = Path(__file__).resolve().parent / "fake_gh.py"

#: The wrapper both landing recipes delegate to. Driven directly for the one thing no
#: recipe can reach: the verb is the recipes' own first argument, so a wrong one is a
#: mistake only a caller of the script itself can make.
LANDER = REPO_ROOT / "scripts" / "land-branch.sh"

#: The paid provider's stand-in for the drafting turn, and the guard covering the
#: identities `ONEHARNESS_BIN_*` cannot reach. `graphs/pr-author.yaml`'s member is
#: single-sided `kind: oneharness`, so it runs its turn through the oneharness library
#: and no substituted `oneharness` CLI is on its path at all — the provider binary is.
FAKE_CODEX = Path(__file__).resolve().parent / "fake_codex.py"
PAID_PROVIDER_GUARD = Path(__file__).resolve().parent / "no-paid-provider"

#: Who the indirection helpers attribute their diagnostics to when one of them refuses.
INDIRECTION_CALLER = "tests/e2e/test_publish_branch_e2e.py"

#: A branch carrying an unattested incomplete-step marker, which is the one state
#: `just repo-recover` lands and `just publish-branch` refuses.
INCOMPLETE_BRANCH = "claude/interrupted-work"

#: What marks that branch's last commit, in the two ways `onevcs` recognizes one: the
#: subject suffix, and the trailer under this host's own configured prefix.
INCOMPLETE_SUBJECT = "feat: land half the work (incomplete step)"
INCOMPLETE_TRAILER = "Orchestrator-Status: incomplete"

#: The origin the hosted identity is resolved from. A GitHub URL, because a change
#: request is a host's object and `onevcs` has a host only for a hosted identity; the
#: remote git pushes to is still the throwaway bare origin on disk.
HOSTED_ORIGIN = "https://github.com/acme-corp/hosted.git"

#: The body the scripted provider answers the drafting turn with. It carries the
#: characters a shell and a file round trip are tempted to mangle — a trailing newline,
#: blank lines, and backticks — because what is asserted is the change request's own
#: description, byte for byte.
DRAFTED_BODY = "## What\n\nAdded `shipped.txt`.\n\n## Why\n\nThe work had to land.\n"


class Publication(NamedTuple):
    """One throwaway repository, its scratch registry, and the origin behind it."""

    #: The registered publication checkout, and the `--repo` every verb is given.
    checkout: Path
    #: The bare origin it pushes to, read to prove a publication left the checkout.
    origin: Path
    #: The environment carrying `ONEVCS_HOME`, which is what makes this isolated.
    environment: dict[str, str]
    #: Every subject the checkout's `commit-msg` hook was asked about, one per line, or
    #: `None` where this repository states no subject policy. Outside the checkout, so
    #: reading it cannot be confused with the branch's own content.
    subjects_seen: Path | None = None
    #: Where the substituted provider records each launch it was asked to make. A
    #: `local-direct` landing must draft nothing, and this is what says so: the file is
    #: written by the stand-in on its first turn, so its absence is the absence of a turn
    #: rather than the absence of a body.
    drafting_attempts: Path | None = None


class OpenedChange(NamedTuple):
    """One change request the substituted host was asked to open.

    Named rather than the JSON mapping it is read from: what a journey asserts on is a
    small, stable set of fields, and a typo in a string key would read as an absent
    field rather than as the mistake it is.
    """

    #: Where it was opened, which is what the verb reports back to the operator.
    url: str
    #: The subject the publication composed, which is what a repository's own hook is
    #: asked about.
    title: str
    #: The description a reviewer reads, and the whole point of drafting.
    body: str


class Hosted(NamedTuple):
    """One throwaway identity whose rules open a change request, and what watched it."""

    #: The registered publication checkout, and the `--repo` the recipe is given.
    checkout: Path
    #: The bare origin the branch is really pushed to.
    origin: Path
    #: The environment carrying the scratch registry, the substituted host, and the
    #: scripted provider.
    environment: dict[str, str]
    #: Where the substituted host records each change request it was asked to open.
    gh_state: Path
    #: Every call that host was asked to make, one JSON argv per line.
    gh_calls: Path
    #: The file `tests/e2e/fake_codex.py` appends each turn's actual prompt to, which
    #: is how a journey says whether a drafting turn was spent at all.
    prompts: Path


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


def _just(
    *arguments: str, environment: dict[str, str], timeout: float = 180
) -> subprocess.CompletedProcess[str]:
    """Run one real recipe from this checkout, against the scratch registry.

    `timeout` is a hang guard like every other here, raised by the journeys whose
    recipe spends a real drafting turn before it reaches `onevcs` at all.
    """
    return subprocess.run(
        ["just", *arguments],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(timeout),
        check=False,
    )


def _publication(
    tmp_path: Path,
    *,
    pre_push: str | None = None,
    retired_gate: list[str] | None = None,
    subject_policy: bool = False,
) -> Publication:
    """A registered repository that publishes locally, and whatever verifies it.

    `local-direct` deliberately: it is the one published policy that opens no change
    request, so the whole journey completes against a bare origin on disk with no
    network and no GitHub. The policy is written as the rules file's `default`,
    because a path origin has no host, owner, or name for a `match` to select on.

    `pre_push` is the body of an executable `pre-push` hook, which is **the verifier**
    for a `local-direct` identity: `store::merge_path_coverage` reports exactly this
    hook, and git runs it at the publishing push. It is arranged the way `just
    bootstrap` arranges this repository's own — tracked under `.githooks/`, named by
    `core.hooksPath` — because that is the arrangement `onevcs` carries into the
    disposable clone a publication works in.

    `retired_gate` writes a **version 2** rules file naming that gate, which is the one
    thing this fixture can do that proves a negative. onevcs 0.11.0 removed the gate
    concept and accepts an unmigrated file by ignoring it, so a gate that would have
    refused the publication outright on 0.10.0 lets it through here — and the sentinel
    it would have written is absent. Anything else writes the migrated `version: 3`
    file, which cannot name a gate at all.

    `subject_policy` gives the repository a `commit-msg` hook arranged the same way, so
    a publication here meets a repository that states a subject policy.
    """
    home = tmp_path / "onevcs-home"
    home.mkdir()
    seed = tmp_path / "seed"
    _git("init", "-q", "-b", BASE, str(seed), cwd=tmp_path)
    (seed / "README.md").write_text("seed\n", encoding="utf-8")
    subjects_seen = _write_subject_policy(tmp_path, seed) if subject_policy else None
    if pre_push is not None:
        _write_pre_push(seed, pre_push)
    _git("add", "-A", cwd=seed)
    _git("commit", "-q", "-m", "init", cwd=seed)
    origin = tmp_path / "origin.git"
    _git("clone", "-q", "--bare", str(seed), str(origin), cwd=tmp_path)
    checkout = tmp_path / "checkout"
    _git("clone", "-q", str(origin), str(checkout), cwd=tmp_path)
    if subject_policy or pre_push is not None:
        # What `just bootstrap` does here: the tracked directory arrives with the
        # content, the config naming it does not. Measured while writing these
        # journeys, and worth knowing because it is *not* what the arrangement rests
        # on — `onevcs` gives the publication clone the lender's `core.hooksPath` **or**
        # its tracked `.githooks/`, so the hooks are reached with this config unset too.
        # It is set anyway, because the point of the fixture is to be the arrangement a
        # real checkout has rather than the minimum that happens to work.
        _git("config", "core.hooksPath", ".githooks", cwd=checkout)
    policy = (
        "version: 3\n"
        "trailer_prefix: Orchestrator-\n"
        "rules: []\n"
        "default:\n"
        "  publication: local-direct\n"
        "  approvals: none\n"
    )
    if retired_gate is not None:
        policy = (
            "version: 2\n"
            "trailer_prefix: Orchestrator-\n"
            "rules: []\n"
            "default:\n"
            "  publication: local-direct\n"
            "  approvals: none\n"
            "  gate:\n"
            f"    command: {retired_gate!r}\n".replace("'", '"')
        )
    (home / "rules.yml").write_text(policy, encoding="utf-8")
    environment = dict(os.environ)
    environment["ONEVCS_HOME"] = str(home)
    # The paid provider is substituted here exactly as it is for the hosted identity
    # below, and for a sharper reason: these journeys must reach it **never**, and an
    # environment that named no stand-in could not tell "drafted nothing" from "drafted
    # against a real subscription". It was the second: with the drafting turn live one
    # of these ran 10m25s into its own 720-second bound, and 2m10s with the provider
    # substituted. Both halves are needed — `ONEHARNESS_BIN_*` keys on a harness id and
    # reaches no variant, and `PATH` is the one seam every variant of every family
    # shares.
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # And the identities that seam cannot reach; see `no_paid_provider`.
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    # An answer it would give if it were ever asked, so a journey that reached it fails
    # on the attempt below rather than on a stand-in with nothing to say.
    environment["FAKE_CODEX_ANSWERS"] = json.dumps([json.dumps({"body": DRAFTED_BODY})])
    attempts = tmp_path / "launches"
    environment["FAKE_CODEX_ATTEMPT_LOG"] = str(attempts)
    environment["FAKE_CODEX_PROMPT_LOG"] = str(tmp_path / "prompts.jsonl")
    # Keeps this journey's harness history out of the host's.
    environment["XDG_STATE_HOME"] = str(tmp_path / "state")
    environment.update(established_indirections(INDIRECTION_CALLER))
    registered = _just("register-repo", str(checkout), environment=environment)
    assert registered.returncode == 0, registered.stderr + registered.stdout
    return Publication(checkout, origin, environment, subjects_seen, attempts)


def _write_pre_push(seed: Path, body: str) -> None:
    """Install the tracked `pre-push` hook that verifies a `local-direct` publication.

    Tracked under `.githooks/` rather than written into `.git/hooks/`, because that is
    the arrangement that survives the clone: `onevcs` carries the lender's
    `core.hooksPath` into the disposable clone a publication is built in, and a hook
    written into one checkout's `.git/hooks/` reaches nothing.
    """
    hooks = seed / ".githooks"
    hooks.mkdir(exist_ok=True)
    hook = hooks / "pre-push"
    hook.write_text(f"#!/usr/bin/env bash\nset -uo pipefail\n{body}\n", encoding="utf-8")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _write_subject_policy(tmp_path: Path, seed: Path) -> Path:
    """Write the tracked `commit-msg` hook, and answer where it records what it judged.

    This repository's own hook in miniature, and deliberately the same *shape*: it reads
    the message file it is handed and nothing else — no index, no diff, no branch —
    because a publication asks it where none of those exist. It also appends every
    subject it judged, outside the checkout, which is what lets a journey assert that
    the composed publication subject is what reached it rather than merely that some
    hook ran.
    """
    seen = tmp_path / "subjects-seen"
    hooks = seed / ".githooks"
    hooks.mkdir()
    hook = hooks / "commit-msg"
    releasing = "|".join(RELEASING_TYPES)
    hook.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'subject=$(head -n 1 "$1")\n'
        f'printf "%s\\n" "$subject" >>{str(seen)!r}\n'
        f"[[ $subject =~ ^({releasing})(\\(.+\\))?!?: ]] && exit 0\n"
        f'echo "{HOOK_REFUSAL}: $subject" >&2\n'
        "exit 1\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    return seen


def _asked_of_the_publication(publication: Publication) -> set[str]:
    """Every subject the hook was asked about *by the publication*, and nothing else.

    The first line the hook records is always the branch's own commit, because
    `core.hooksPath` is set before `_finished_branch` runs and git asks the same hook
    about it — which is the arrangement being modelled, not noise to suppress. What
    follows is the publication asking, and it is read as a set rather than a list:
    how many times one publication asks is the sibling's business, and a journey
    counting it would fail on a release that asked once more.
    """
    assert publication.subjects_seen is not None
    judged = publication.subjects_seen.read_text(encoding="utf-8").splitlines()
    assert judged[:1] == [BRANCH_SUBJECT], (
        f"git did not run the repository's hook on the branch's own commit: {judged}"
    )
    asked = set(judged[1:])
    assert asked, f"the publication never asked the repository's hook anything: {judged}"
    return asked


def _finished_branch(checkout: Path) -> str:
    """Commit finished work on a branch, exactly as a settled dispatch leaves it."""
    _git("checkout", "-q", "-b", FINISHED_BRANCH, cwd=checkout)
    (checkout / PUBLISHED_FILE).write_text("the work\n", encoding="utf-8")
    _git("add", "-A", cwd=checkout)
    _git("commit", "-q", "-m", BRANCH_SUBJECT, cwd=checkout)
    head = _git("rev-parse", "HEAD", cwd=checkout).strip()
    _git("checkout", "-q", BASE, cwd=checkout)
    return head


def _closing_branch(checkout: Path) -> None:
    """Commit finished work whose messages close issues, as a dispatch that delivered them does.

    Two commits rather than one, so the squash is a real fold of several messages and
    the issue named in both of them is a real duplicate for the landing to collapse.
    """
    _git("checkout", "-q", "-b", FINISHED_BRANCH, cwd=checkout)
    for index, message in enumerate(CLOSING_COMMITS):
        (checkout / f"{PUBLISHED_FILE}.{index}").write_text(f"the work {index}\n", encoding="utf-8")
        _git("add", "-A", cwd=checkout)
        _git("commit", "-q", "-m", message, cwd=checkout)
    _git("checkout", "-q", BASE, cwd=checkout)


def _incomplete_branch(checkout: Path) -> None:
    """Commit work a step did not finish, as a stopped dispatch leaves it.

    The state only `recover` lands: the marker its subject carries is what `onevcs`
    recognizes, and the trailer is written under the prefix this host's rules file
    configures rather than the published default.
    """
    _git("checkout", "-q", "-b", INCOMPLETE_BRANCH, cwd=checkout)
    # The finished part of the work, whose subject is what a publication composes from:
    # a branch carrying nothing but its marker describes no change and `onevcs` refuses
    # it outright, which is a branch state no step ever leaves behind.
    (checkout / PUBLISHED_FILE).write_text("the work\n", encoding="utf-8")
    _git("add", "-A", cwd=checkout)
    _git("commit", "-q", "-m", BRANCH_SUBJECT, cwd=checkout)
    (checkout / "half.txt").write_text("half the work\n", encoding="utf-8")
    _git("add", "-A", cwd=checkout)
    _git("commit", "-q", "-m", f"{INCOMPLETE_SUBJECT}\n\n{INCOMPLETE_TRAILER}\n", cwd=checkout)
    _git("checkout", "-q", BASE, cwd=checkout)


def test_publish_branch_lands_a_complete_branch_on_its_base(tmp_path: Path) -> None:
    """The recipe publishes for real: the work reaches the base branch and the origin.

    This is the claim the recipe is worth having — not that `onevcs publish-branch` is
    reached with the right arguments, which the delegation table already holds, but
    that the branch state it names is genuinely landed. Read from the origin rather
    than from the command's own report, because a verb that said it published and
    pushed nothing is exactly the failure an operator would discover later.
    """
    publication = _publication(tmp_path)
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


@pytest.mark.parametrize(
    ("recipe", "branch", "prepare"),
    [
        pytest.param("publish-branch", FINISHED_BRANCH, _finished_branch, id="publish-branch"),
        pytest.param("repo-recover", INCOMPLETE_BRANCH, _incomplete_branch, id="repo-recover"),
    ],
)
def test_a_local_direct_landing_spends_no_drafting_turn(
    tmp_path: Path,
    recipe: str,
    branch: str,
    prepare: Callable[[Path], object],
) -> None:
    """`local-direct` opens no change request, so there is nothing for a body to describe.

    The wrapper puts a drafting turn in front of the two verbs that open a change
    request — and this policy is not one of them: it builds the base's squash commit
    itself, and `onevcs` has no path that attaches a body to a commit. Every turn spent
    here bought prose nothing would ever read, on the slowest seam in the landing.

    Both verbs, because the skip sits in the one wrapper both go through and a guard
    read for one verb and not the other is exactly the kind of drift a shared wrapper
    invites: a recovery drafts through the same graph as a publication, so it has the
    same turn to save on the same policy.

    Read from the provider's own launch log rather than from the landing's report,
    because the two failure modes are indistinguishable in that report: a drafter that
    ran and a drafter that did not both leave a base commit with no body attached. The
    log is written by the stand-in on its first turn, so its absence is the absence of a
    turn.

    The identity carries a passing `pre-push` hook because `recover` refuses one whose
    merge path verifies nothing — an attestation with nothing behind it attests nothing —
    and a publication reaches the same hook, so both verbs land here under one fixture.
    """
    publication = _publication(tmp_path, pre_push="exit 0")
    prepare(publication.checkout)
    assert publication.drafting_attempts is not None

    published = _just(
        recipe,
        branch,
        "--repo",
        str(publication.checkout),
        environment=publication.environment,
        timeout=600,
    )

    assert published.returncode == 0, published.stderr + published.stdout
    # The landing still happened, so this is a turn saved rather than a verb skipped.
    assert PUBLISHED_FILE in _git("ls-tree", "--name-only", BASE, cwd=publication.origin), (
        f"the finished work never reached the origin's {BASE}:\n{published.stdout}"
    )
    assert not publication.drafting_attempts.exists(), (
        f"a `local-direct` landing reached the drafting provider "
        f"{publication.drafting_attempts.read_text(encoding='utf-8')!r}; that policy opens "
        f"no change request, so the turn bought a body nothing can carry"
    )


def test_a_landing_still_drafts_where_the_policy_opens_a_change_request(
    tmp_path: Path,
) -> None:
    """The other half of the rule above, which is what keeps it from being a blanket skip.

    Reading the workflow wrong in the safe direction — skipping every draft — is a
    regression this repository already paid for once, when every branch landed by hand
    opened with an empty description. So the skip is held to the policy it is about: the
    same wrapper, the same flags, an identity whose rules resolve `change-open`, and a
    body on the change request it opened.
    """
    hosted = _hosted(tmp_path, answers=[json.dumps({"body": DRAFTED_BODY})])
    _finished_branch(hosted.checkout)

    published = _just(
        "publish-branch",
        FINISHED_BRANCH,
        "--repo",
        str(hosted.checkout),
        environment=hosted.environment,
    )

    assert published.returncode == 0, published.stderr + published.stdout
    opened = _opened_change_requests(hosted)
    assert [change.body for change in opened] == [DRAFTED_BODY], (
        f"the workflow read that skips drafting for `local-direct` also skipped it for "
        f"`change-open`, whose change request is exactly what a body is for: {opened}"
    )


def test_publish_branch_refuses_a_branch_the_repositorys_own_merge_path_rejects(
    tmp_path: Path,
) -> None:
    """The merge path stops the publication, and the base is left exactly as it was.

    The recovery path, and the reason the verb is worth routing through at all. What
    runs the repository's verification is no longer `onevcs` — 0.11.0 removed the tier
    it ran itself — it is the repository's own `pre-push` hook, which git runs at the
    publishing push and which `store::merge_path_coverage` reports as this identity's
    verifier. A refusal that had already advanced the base would be worse than no
    verifier at all, so what is asserted is the base, not the exit status alone.
    """
    ran = tmp_path / "the-hook-ran"
    publication = _publication(tmp_path, pre_push=f'touch {ran}\necho "{HOOK_REFUSAL}" >&2\nexit 1')
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
    # The refusal has to be *this* one. A publication can fail for reasons that are not
    # the merge path at all, and a journey satisfied by any non-zero status would keep
    # passing if the hook stopped being reached.
    assert ran.exists(), (
        "the publishing push never reached the repository's own `pre-push` hook, so "
        f"whatever refused this branch was not its merge path:\n{refused.stdout[-2000:]}"
    )
    assert _git("rev-parse", BASE, cwd=publication.origin).strip() == before, (
        "the base moved even though the merge path rejected the branch"
    )


#: A gate duration planted as this host's last recording, and the bound
#: `scripts/lock-timeout.sh` derives from it: 120 x 8 + 300, above `onevcs`'s 900-second
#: default, so what arrives is the derivation rather than its floor. Stated rather than
#: read out of the helper, so the journey cannot agree with whatever the helper says.
RECORDED_GATE_SECONDS = 120
DERIVED_LOCK_BOUND = "1260"


def test_publish_branch_starts_onevcs_under_the_bound_the_last_gate_derives(
    tmp_path: Path,
) -> None:
    """A manager's landing queues for the merge-queue lock under the derived bound.

    A `local-direct` publication runs this identity's whole gate under that lock, so a
    landing started beside a sibling's waits as long as that gate takes; `onevcs`'s own
    900-second default is what refused `aio-1108` behind `aio-1110`
    (ai-orchestrator#1164). Read where the real `onevcs` this recipe starts has handed
    its environment on: the repository's own `pre-push` hook, which that publication
    runs, records the bound it was started under.
    """
    seen = tmp_path / "bound-seen"
    publication = _publication(
        tmp_path, pre_push=f'printf "%s\\n" "${{ONEVCS_LOCK_TIMEOUT_SECONDS:-unset}}" >{seen}'
    )
    # A bound the launching shell exported is kept as a caller's, which is not what is
    # under test here.
    publication.environment.pop("ONEVCS_LOCK_TIMEOUT_SECONDS", None)
    recording = Path(publication.environment["XDG_STATE_HOME"]) / "ai-orchestrator"
    recording.mkdir(parents=True, exist_ok=True)
    # llmlint: ignore[tests_mirror_real_usage] Producing this through the pre-push interface would need a gate that takes a known, bound-moving time — two minutes for any value above the floor — and `tests/e2e/test_lock_timeout_bound_e2e.py` already drives the real recording path; what this journey adds is the landing recipe's export, read at the hook.  # noqa: E501
    (recording / "local-direct-gate-seconds").write_text(
        f"{RECORDED_GATE_SECONDS}\n", encoding="utf-8"
    )
    _finished_branch(publication.checkout)

    published = _just(
        "publish-branch",
        FINISHED_BRANCH,
        "--repo",
        str(publication.checkout),
        environment=publication.environment,
    )

    assert published.returncode == 0, published.stderr + published.stdout
    assert seen.is_file(), f"the publication never ran the merge path:\n{published.stdout}"
    assert seen.read_text(encoding="utf-8").strip() == DERIVED_LOCK_BOUND, (
        f"the onevcs a landing starts queued under "
        f"{seen.read_text(encoding='utf-8').strip()!r} rather than the {DERIVED_LOCK_BOUND}s "
        f"a {RECORDED_GATE_SECONDS}s gate derives"
    )


#: The one line a merge-path hook says a host prerequisite is missing with, as onevcs's
#: `HOST_PREREQUISITE_MARKER` spells it (`tests/test_engine_contracts.py` holds the name
#: to the linked crate), and what this hook names after it. A hook emits it only for a
#: missing host tool or credential, never for a check whose outcome depends on the tree.
HOST_PREREQUISITE_MARKER = "onevcs: host-prerequisite:"
HOST_PREREQUISITE_REMEDY = "shellcheck is not installed; apt-get install shellcheck"


def test_publish_branch_names_a_host_prerequisite_the_merge_path_says_it_is_missing(
    tmp_path: Path,
) -> None:
    """A hook refusing for the host's sake is reported as that, not as the tree's.

    The other refusal a merge path can give, told apart from the one above by the hook's
    own line: since https://github.com/nickderobertis/onevcs/pull/163 a refused push whose
    output carries the marker is the failure kind `host-prerequisite`, its reason the
    remediation after the marker, and it is that kind the adopted engine settles a
    lifecycle node on once, as `infrastructure-failure`, rather than re-dispatching a
    worker onto a branch no edit can fix. Before it the same line was `push-rejected`
    like any other, and read as the work's. What is read here is the verb's own report,
    which is what an operator running `just publish-branch` meets; the base stays where
    it was, exactly as for any refusal.
    """
    publication = _publication(
        tmp_path,
        pre_push=f'echo "{HOST_PREREQUISITE_MARKER} {HOST_PREREQUISITE_REMEDY}" >&2\nexit 1',
    )
    _finished_branch(publication.checkout)
    before = _git("rev-parse", BASE, cwd=publication.origin).strip()

    refused = _just(
        "publish-branch",
        FINISHED_BRANCH,
        "--repo",
        str(publication.checkout),
        environment=publication.environment,
    )

    reported = refused.stdout + refused.stderr
    assert refused.returncode != 0, reported
    # The verb's own sentence, and not the hook's echo, which the report relays on both
    # releases: what says the kind was read is the sentence built from it.
    assert "onevcs: host prerequisite missing:" in reported, (
        f"the refusal is not reported as the host's:\n{reported[-2000:]}"
    )
    assert f"this host is missing a prerequisite: {HOST_PREREQUISITE_REMEDY}" in reported, (
        f"the hook's own remediation did not reach the report as the reason:\n{reported[-2000:]}"
    )
    assert "push rejected" not in reported, (
        f"a host prerequisite was reported as the tree being rejected:\n{reported[-2000:]}"
    )
    assert _git("rev-parse", BASE, cwd=publication.origin).strip() == before, (
        "the base moved even though the merge path refused the push"
    )


def test_a_gate_the_previous_release_would_have_run_is_ignored_and_the_branch_lands(
    tmp_path: Path,
) -> None:
    """The removal, demonstrated on a real publication rather than from a version string.

    This is the one shape of journey that can prove a negative here. The rules file is
    `version: 2` and names a gate that **writes a sentinel file and then fails**. On
    onevcs 0.10.0 that gate is resolved (`rules check` prints it), run in the
    publication worktree, and its non-zero status refuses the publication outright —
    which is exactly what `test_publish_branch_refuses_a_branch_its_identity_gate_rejects`
    used to assert on this fixture. On 0.11.0 the concept is gone: the file still loads,
    the gate is reported once as ignored, the branch lands, and the sentinel does not
    exist — so nothing ran it, rather than something running it and disregarding the
    answer.

    The sentinel is what makes this evidence instead of inference. A gate spelled
    `["false"]` that stopped refusing would be consistent with the gate running and its
    verdict being dropped; a gate that never wrote its file did not run.
    """
    sentinel = tmp_path / "the-gate-ran"
    publication = _publication(
        tmp_path,
        retired_gate=["bash", "-c", f"touch {sentinel}; exit 1"],
    )
    _finished_branch(publication.checkout)

    published = _just(
        "publish-branch",
        FINISHED_BRANCH,
        "--repo",
        str(publication.checkout),
        environment=publication.environment,
    )

    assert published.returncode == 0, published.stderr + published.stdout
    assert not sentinel.exists(), (
        "the retired gate ran: onevcs 0.11.0 must not invoke a `gate:` a version 2 "
        f"rules file still names\n{published.stdout}{published.stderr}"
    )
    assert PUBLISHED_FILE in _git("ls-tree", "--name-only", BASE, cwd=publication.origin), (
        f"the branch never reached the origin's {BASE} behind an ignored gate:\n{published.stdout}"
    )
    assert "version 3 removed" in published.stderr, published.stderr


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
    publication = _publication(tmp_path)
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


def test_publish_branch_lands_a_branch_whose_merge_path_is_loud_on_both_pipes(
    tmp_path: Path,
) -> None:
    """The output this host stopped silencing: far past one pipe buffer, on both streams.

    `config/onevcs.rules.yml` used to prepend `env NEXTEST_STATUS_LEVEL=fail` to nine
    gates, because an `onevcs` before 0.2.10 read a verifying child's stdout to EOF
    before it read stderr at all: a child that filled the 64 KiB stderr buffer wedged
    there forever and was reported as a *rejection*, twice failing complete work here.

    The gate that used to be the loud child is gone — onevcs 0.11.0 removed the tier —
    and the property was never the tier's. It is the reader's, and the child `onevcs`
    reads both pipes of on a `local-direct` publication is now the merge path's own
    verifier: the `pre-push` hook git runs at the publishing push, whose output
    `record_push` stores. So it is proven the way the defect appeared, against the
    process that is really there.

    `LOUD_LINES` is asserted rather than assumed: the hook body is run directly first,
    so a payload that quietly stopped exceeding the buffer would fail here instead of
    turning this into a journey that proves nothing.
    """
    direct = subprocess.run(
        ["bash", "-c", LOUD_HOOK],
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert direct.returncode == 0, direct.stderr[-2000:]
    assert len(direct.stderr.encode()) > PIPE_BUFFER, len(direct.stderr.encode())
    assert len(direct.stdout.encode()) > PIPE_BUFFER, len(direct.stdout.encode())

    ran = tmp_path / "the-hook-ran"
    publication = _publication(tmp_path, pre_push=f"touch {ran}\n{LOUD_HOOK}")
    _finished_branch(publication.checkout)

    published = _just(
        "publish-branch",
        FINISHED_BRANCH,
        "--repo",
        str(publication.checkout),
        environment=publication.environment,
    )

    assert published.returncode == 0, published.stderr[-2000:] + published.stdout[-2000:]
    # Without this the journey would pass on a hook nothing ever reached, which is the
    # one way "a loud verifier still publishes" could be true of nothing at all.
    assert ran.exists(), (
        "the publishing push never reached the repository's own `pre-push` hook, so "
        f"nothing loud was read:\n{published.stdout[-2000:]}"
    )
    assert PUBLISHED_FILE in _git("ls-tree", "--name-only", BASE, cwd=publication.origin), (
        f"the loud merge path's branch never reached the origin's {BASE}:\n{published.stdout}"
    )


def test_publish_branch_refuses_a_branch_that_is_not_there(tmp_path: Path) -> None:
    """A branch the checkout cannot reach is named in the refusal, not guessed at."""
    publication = _publication(tmp_path)
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


def test_publish_branch_refuses_a_subject_the_repositorys_own_hook_turns_down(
    tmp_path: Path,
) -> None:
    """`onevcs` puts the subject it would land to the repository's `commit-msg` hook.

    New at onevcs 0.6.1 (#51) and carried unchanged by every adopted release since,
    which is what running this against the installed CLI on each bump establishes. It is
    narrower than it first looks — measured against both releases rather than read off
    the changelog. `commit-msg` appears nowhere
    in 0.5.0's sources, but the hook was reached there anyway: a clone carries
    `core.hooksPath`, so git ran it on the squash commit a publication writes, from the
    far side of a gate run and a merge, and reported it as `invalid input: git commit
    -m <subject> failed`. What 0.6.1 adds is asking the question **first** and answering
    it as a refusal an operator can act on, which is why the assertions below are on that
    wording and not on the exit status.

    Two things are then proven that the sibling's own suite cannot prove for this host.
    The hook reached is the *repository's*, carried into the disposable clone
    `publish-branch` works in rather than lost with the checkout it was configured on;
    and a subject it turns down stops the publication, so the base is what is asserted
    rather than the refusal alone. A refusal that had already advanced the base is the
    failure this whole routing exists to prevent.
    """
    publication = _publication(tmp_path, subject_policy=True)
    _finished_branch(publication.checkout)
    before = _git("rev-parse", BASE, cwd=publication.origin).strip()

    refused = _just(
        "publish-branch",
        FINISHED_BRANCH,
        "--repo",
        str(publication.checkout),
        "--title",
        NON_RELEASING_TITLE,
        environment=publication.environment,
    )

    said = refused.stderr + refused.stdout
    assert refused.returncode != 0, said
    assert "git commit -m" in said, said
    # And carrying the hook's own words, which are the whole of what says which policy
    # refused this subject.
    assert HOOK_REFUSAL in said, said
    assert _git("rev-parse", BASE, cwd=publication.origin).strip() == before, (
        f"the base moved even though the repository's commit-msg hook refused:\n{said}"
    )
    assert publication.subjects_seen is not None
    assert _asked_of_the_publication(publication) == {NON_RELEASING_TITLE}, (
        publication.subjects_seen.read_text(encoding="utf-8")
    )


def test_publish_branch_lands_a_subject_the_repositorys_own_hook_accepts(
    tmp_path: Path,
) -> None:
    """The control: the same hook, a subject it releases from, and the work lands.

    Without it the refusal above proves only that *something* about a repository with a
    hook stops a publication. Both journeys build the identical repository and differ in
    the one `--title` the hook judges, so what is under test is the verdict rather than
    the presence of a policy — and a hook that refused everything, or an `onevcs` that
    refused any repository stating a policy at all, fails here.
    """
    publication = _publication(tmp_path, subject_policy=True)
    _finished_branch(publication.checkout)

    published = _just(
        "publish-branch",
        FINISHED_BRANCH,
        "--repo",
        str(publication.checkout),
        "--title",
        RELEASING_TITLE,
        environment=publication.environment,
    )

    assert published.returncode == 0, published.stderr + published.stdout
    assert PUBLISHED_FILE in _git("ls-tree", "--name-only", BASE, cwd=publication.origin), (
        f"the accepted branch never reached the origin's {BASE}:\n{published.stdout}"
    )
    landed = _git("log", "-1", "--format=%s", BASE, cwd=publication.origin).strip()
    assert landed == RELEASING_TITLE, (
        f"the base carries {landed!r}, not the subject the hook was asked about"
    )
    assert publication.subjects_seen is not None
    assert _asked_of_the_publication(publication) == {RELEASING_TITLE}, (
        publication.subjects_seen.read_text(encoding="utf-8")
    )


def test_a_local_direct_squash_keeps_the_closing_lines_the_branch_carried(
    tmp_path: Path,
) -> None:
    """The one commit a landing leaves on the base still closes what the branch closed.

    Read from the origin's base, because that commit is the only one the host reads a
    closing line from: the branch's own commits do not survive the squash, so a landing
    that composed its message from the subject and the trailers alone — which is what
    every onevcs before that pull request did — left the issues open while the work was in. Each
    issue is asserted once, under a capitalised keyword, in the order the commits first
    named it, and the trailers still follow: what a squash keeps is the references, not
    the sentences around them.
    """
    publication = _publication(tmp_path)
    _closing_branch(publication.checkout)

    published = _just(
        "publish-branch",
        FINISHED_BRANCH,
        "--repo",
        str(publication.checkout),
        environment=publication.environment,
    )

    assert published.returncode == 0, published.stderr + published.stdout
    tree = _git("ls-tree", "--name-only", BASE, cwd=publication.origin)
    assert all(f"{PUBLISHED_FILE}.{index}" in tree for index in range(len(CLOSING_COMMITS))), (
        f"the branch's work never reached the origin's {BASE}:\n{published.stdout}"
    )
    message = _git("log", "-1", "--format=%B", BASE, cwd=publication.origin)
    lines = [line for line in message.splitlines() if line.strip()]
    assert lines[0] == BRANCH_SUBJECT, f"the squash's subject is not the branch's:\n{message}"
    assert lines[1 : 1 + len(CLOSING_LINES)] == list(CLOSING_LINES), (
        f"the squash on {BASE} does not carry the branch's closing lines, each once, "
        f"after its subject:\n{message}"
    )
    # Each issue exactly once: the one named by two commits under two keywords is one
    # line, because a squash naming an issue twice reads as two deliveries.
    for reference in CLOSING_REFERENCES:
        assert message.count(reference) == 1, (
            f"{reference} appears {message.count(reference)} times:\n{message}"
        )
    # And the provenance the landing already stamped is still there, after the lines.
    assert "Orchestrator-Landed-Commit:" in "\n".join(lines[1 + len(CLOSING_LINES) :]), (
        f"the landing's own trailer is missing or displaced:\n{message}"
    )


def _hosted(tmp_path: Path, *, answers: list[str], pre_push: str | None = None) -> Hosted:
    """A registered identity whose rules open a change request, with the drafter live.

    `local-direct` above is enough to prove a branch lands; it opens no change request,
    so it can say nothing about the description one carries. This is the other policy —
    `change-open` — and everything below the remote host stays real: the branch is
    pushed with real git into the same throwaway bare origin, that push is judged by a
    real `pre-push` hook when one is asked for, and the body is drafted by the real
    `graphs/pr-author.yaml` through the real `oneagentgraph`.

    Two things are substituted, both external and both at their published seam. The
    remote host's decisioning is `tests/e2e/fake_gh.py`, named by `ONEVCS_GH` — the
    variable `onevcs` publishes for exactly this and uses the same way in its own
    suite. The paid provider is `tests/e2e/fake_codex.py`, named at the one seam the
    drafting graph's single-sided `kind: oneharness` member reaches a provider through.
    """
    home = tmp_path / "onevcs-home"
    home.mkdir()
    seed = tmp_path / "seed"
    _git("init", "-q", "-b", BASE, str(seed), cwd=tmp_path)
    (seed / "README.md").write_text("seed\n", encoding="utf-8")
    if pre_push is not None:
        _write_pre_push(seed, pre_push)
    _git("add", "-A", cwd=seed)
    _git("commit", "-q", "-m", "init", cwd=seed)
    origin = tmp_path / "origin.git"
    _git("clone", "-q", "--bare", str(seed), str(origin), cwd=tmp_path)
    checkout = tmp_path / "checkout"
    _git("clone", "-q", str(origin), str(checkout), cwd=tmp_path)
    if pre_push is not None:
        _git("config", "core.hooksPath", ".githooks", cwd=checkout)
    # A reviewed path: `change-open` opens the change request and stops, which is the
    # state whose description is under test. The rules name no verifier — onevcs 0.11.0
    # removed the concept — so what can refuse this branch before the change request
    # exists is the publishing push, and a journey about what a *refused* branch costs
    # states its own hook.
    (home / "rules.yml").write_text(
        "version: 3\n"
        "trailer_prefix: Orchestrator-\n"
        "rules: []\n"
        "default:\n"
        "  publication: change-open\n"
        "  approvals: required\n",
        encoding="utf-8",
    )

    state = tmp_path / "gh-state"
    state.mkdir()
    calls = tmp_path / "gh-calls.jsonl"
    environment = dict(os.environ)
    environment["ONEVCS_HOME"] = str(home)
    # llmlint: ignore[e2e_not_mocked] Only the remote host's decisioning, at onevcs's seam.
    environment["ONEVCS_GH"] = str(FAKE_GH)
    environment["FAKE_GH_STATE"] = str(state)
    environment["FAKE_GH_CALLS"] = str(calls)
    environment["FAKE_GH_ORIGIN"] = str(origin)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # And the identities that seam cannot reach; see `no_paid_provider`.
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment["FAKE_CODEX_ANSWERS"] = json.dumps(answers)
    prompts = tmp_path / "prompts.jsonl"
    environment["FAKE_CODEX_PROMPT_LOG"] = str(prompts)
    environment["FAKE_CODEX_ATTEMPT_LOG"] = str(tmp_path / "launches")
    # Keeps this journey's harness history out of the host's.
    environment["XDG_STATE_HOME"] = str(tmp_path / "state")
    environment.update(established_indirections(INDIRECTION_CALLER))

    # The origin the identity is resolved from is the one the rules and the host are
    # written for; the remote git actually pushes to is still the bare origin above.
    registered = _just(
        "register-repo",
        str(checkout),
        "--origin",
        HOSTED_ORIGIN,
        environment=environment,
    )
    assert registered.returncode == 0, registered.stderr + registered.stdout
    return Hosted(checkout, origin, environment, state, calls, prompts)


def _opened_change_requests(hosted: Hosted) -> list[OpenedChange]:
    """Every change request the substituted host was asked to open, with its body."""
    opened = []
    for record in sorted(hosted.gh_state.glob("pr-*.json")):
        change = json.loads(record.read_text(encoding="utf-8"))
        body = record.with_suffix(".body")
        opened.append(
            OpenedChange(
                url=change["url"],
                title=change["title"],
                body=body.read_text(encoding="utf-8") if body.exists() else "",
            )
        )
    return opened


def _drafting_turns(hosted: Hosted) -> list[str]:
    """Every prompt the paid provider's stand-in was given, which is every turn spent."""
    if not hosted.prompts.exists():
        return []
    return [
        json.loads(line)["prompt"]
        for line in hosted.prompts.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_publish_branch_opens_the_change_request_with_the_drafted_body(
    tmp_path: Path,
) -> None:
    """The whole journey this recipe exists for: a landed branch that describes itself.

    Every pull request opened from this host lately carried an empty description,
    because out-of-band landing is how branches usually reach a base here and it was
    the one path that could not carry a body at all. What is asserted is the body the
    host was asked to open the change request with, byte for byte: a wrapper that
    drafted and then dropped the result, or handed `onevcs` a path instead of prose,
    would land the branch just the same and leave the reviewer with nothing.
    """
    hosted = _hosted(tmp_path, answers=[json.dumps({"body": DRAFTED_BODY})])
    _finished_branch(hosted.checkout)

    published = _just(
        "publish-branch",
        FINISHED_BRANCH,
        "--repo",
        str(hosted.checkout),
        environment=hosted.environment,
        timeout=600,
    )

    assert published.returncode == 0, published.stderr + published.stdout
    opened = _opened_change_requests(hosted)
    assert len(opened) == 1, f"the host was asked to open {len(opened)} change requests"
    assert opened[0].body == DRAFTED_BODY, (
        f"the change request opened with {opened[0].body!r}; the drafting turn "
        f"answered {DRAFTED_BODY!r}, and what a reviewer reads is this"
    )
    # The branch really landed on the reviewed path: it reached the origin, and the
    # base did not move — which is what `change-open` means.
    assert FINISHED_BRANCH in _git("branch", "--list", FINISHED_BRANCH, cwd=hosted.origin), (
        f"the branch never reached the origin:\n{published.stdout}"
    )
    assert opened[0].url in published.stdout, published.stdout


def test_publish_branch_lands_the_branch_when_the_draft_produces_no_body(
    tmp_path: Path,
) -> None:
    """A draft that produced nothing costs the branch nothing: it lands, with no body.

    Drafting sits in front of a landing, so the failure to protect against is a drafter
    that can refuse one. `onepipeline`'s own closeout never lets a bodyless draft block
    a publication, and neither does this: the change request opens with no body, the
    branch lands anyway, and the one line naming which ending it was reaches the
    operator's stderr rather than being swallowed — that word is what says whether the
    fix is the graph, the schema, or the persona's prose.
    """
    # A conforming answer with nothing in it: validation passes and there is no body,
    # which is `onepipeline`'s `no-body` ending and costs exactly one turn.
    hosted = _hosted(tmp_path, answers=[json.dumps({"body": "   \n\n"})])
    _finished_branch(hosted.checkout)

    # `--repo=<checkout>` rather than the two-word form, so the spelling an operator
    # types interchangeably is proven to be read well enough to draft from: a turn is
    # spent here, and a turn is only spent once the branch and the checkout were both
    # read out of the argument list.
    published = _just(
        "publish-branch",
        FINISHED_BRANCH,
        f"--repo={hosted.checkout}",
        environment=hosted.environment,
        timeout=600,
    )

    assert published.returncode == 0, published.stderr + published.stdout
    opened = _opened_change_requests(hosted)
    assert len(opened) == 1, f"the host was asked to open {len(opened)} change requests"
    assert opened[0].body == "", (
        f"the change request opened with {opened[0].body!r}; a draft that produced "
        "nothing must publish nothing rather than something composed to stand in"
    )
    assert "no-body" in published.stderr, (
        "the operator was never told why the change request opened with no body:\n"
        f"{published.stderr}"
    )
    assert len(_drafting_turns(hosted)) == 1, (
        f"drafting spent {len(_drafting_turns(hosted))} turns; it never retries"
    )


#: The spellings a caller states its own body in. Every one the wrapper accepts, because
#: each is a separate arm of its reading and a body it failed to notice is a drafting
#: turn spent to overwrite a description somebody wrote deliberately. `--body-file` is
#: the form a caller with real prose uses, and either `=` spelling is what an operator
#: types without thinking about it.
BODY_SPELLINGS = ("--body", "--body=", "--body-file", "--body-file=")


@pytest.mark.parametrize("spelling", BODY_SPELLINGS)
def test_publish_branch_forwards_a_callers_own_body_and_drafts_nothing(
    tmp_path: Path, spelling: str
) -> None:
    """A caller who brought a body keeps it, and pays for no turn.

    The body is prose the caller decided on, so re-deriving it from the diff would both
    overwrite a deliberate description and spend an agent turn to do it. Read from what
    the host was asked to open the change request with, because that is the only place
    the two could differ.
    """
    hosted = _hosted(tmp_path, answers=[json.dumps({"body": DRAFTED_BODY})])
    _finished_branch(hosted.checkout)
    caller_body = "## What\n\nWhat the caller says it is.\n"
    if spelling.startswith("--body-file"):
        written = tmp_path / "caller-body.md"
        written.write_text(caller_body, encoding="utf-8")
        value = str(written)
    else:
        value = caller_body
    # The joined spellings carry their value in the one argument, which is exactly the
    # arm a wrapper reading only the two-word forms would forward without noticing.
    stated = (f"{spelling}{value}",) if spelling.endswith("=") else (spelling, value)

    published = _just(
        "publish-branch",
        FINISHED_BRANCH,
        "--repo",
        str(hosted.checkout),
        *stated,
        environment=hosted.environment,
        timeout=600,
    )

    assert published.returncode == 0, published.stderr + published.stdout
    opened = _opened_change_requests(hosted)
    assert [change.body for change in opened] == [caller_body], (
        f"the change request opened with {[change.body for change in opened]!r} "
        f"rather than the caller's own {caller_body!r}"
    )
    assert _drafting_turns(hosted) == [], (
        "a drafting turn was spent although the caller supplied the body"
    )


def test_publish_branch_skips_drafting_when_told_to_and_does_not_forward_the_flag(
    tmp_path: Path,
) -> None:
    """`--no-draft` is the escape for a bulk landing, and `onevcs` never sees it.

    An operator working down `just recoverable` lands dozens of branches, and each
    drafting turn is a real agent turn. The flag is this wrapper's own — `onevcs` has
    no such option and would refuse the whole invocation — so what is proven is both
    halves: no turn was spent, and the landing still succeeded.
    """
    hosted = _hosted(tmp_path, answers=[json.dumps({"body": DRAFTED_BODY})])
    _finished_branch(hosted.checkout)

    published = _just(
        "publish-branch",
        FINISHED_BRANCH,
        "--repo",
        str(hosted.checkout),
        "--no-draft",
        environment=hosted.environment,
        timeout=600,
    )

    assert published.returncode == 0, published.stderr + published.stdout
    opened = _opened_change_requests(hosted)
    assert [change.body for change in opened] == [""], (
        f"the change request opened with {[change.body for change in opened]!r} "
        "although drafting was skipped"
    )
    assert _drafting_turns(hosted) == [], "a drafting turn was spent despite --no-draft"


def test_publish_branch_reads_a_branch_stated_after_the_end_of_options_marker(
    tmp_path: Path,
) -> None:
    """`--` ends the options, and the branch behind it is still the one drafted for.

    The marker is how a caller states a branch whose name would otherwise be read as a
    flag, so the wrapper stops reading options at it and takes the next word as the
    branch. Getting that wrong is silent in both directions: reading `--` as the branch
    drafts a body for a name nothing has, and reading nothing at all lands the branch
    with the empty description this whole recipe exists to end. What proves which
    happened is that a turn was spent and the change request carries the drafted prose.
    """
    hosted = _hosted(tmp_path, answers=[json.dumps({"body": DRAFTED_BODY})])
    _finished_branch(hosted.checkout)

    published = _just(
        "publish-branch",
        "--repo",
        str(hosted.checkout),
        "--",
        FINISHED_BRANCH,
        environment=hosted.environment,
        timeout=600,
    )

    assert published.returncode == 0, published.stderr + published.stdout
    opened = _opened_change_requests(hosted)
    assert [change.body for change in opened] == [DRAFTED_BODY], (
        f"the change request opened with {[change.body for change in opened]!r}; the "
        f"branch behind `--` is the one that had to be drafted for"
    )
    assert len(_drafting_turns(hosted)) == 1, (
        f"drafting spent {len(_drafting_turns(hosted))} turns for one landing"
    )


#: What a caller can put where the verb goes, other than the two the wrapper knows.
#: `integrate` is the third landing verb and the plausible mistake; the empty case is a
#: caller that stated no verb at all, which the refusal has to say rather than quote.
WRONG_VERBS = (
    pytest.param("integrate", id="the-third-landing-verb"),
    pytest.param("", id="no-verb-at-all"),
)


@pytest.mark.parametrize("verb", WRONG_VERBS)
def test_the_landing_wrapper_refuses_a_verb_that_is_not_one_of_the_two_it_lands(
    tmp_path: Path, verb: str
) -> None:
    """A verb this wrapper does not know is refused here, not forwarded to `onevcs`.

    The argument list is read for one of two verbs and appended to for whichever it
    was, so forwarding an unknown one would hand `onevcs` a list assembled for a
    command it is not being asked to run. The refusal has to name both verbs and what
    it got, because a caller reaching this has typed the wrong one of three.
    """
    stated = [str(LANDER), verb, FINISHED_BRANCH] if verb else [str(LANDER)]
    refused = subprocess.run(
        stated,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )

    assert refused.returncode == 2, (
        f"the wrapper exited {refused.returncode} for the verb {verb!r}; a command line "
        f"it cannot act on is a usage failure\n{refused.stdout}\n{refused.stderr}"
    )
    assert refused.stdout == "", f"the wrapper printed {refused.stdout!r} having landed nothing"
    assert "publish-branch" in refused.stderr and "recover" in refused.stderr, (
        f"the refusal names neither verb it does land:\n{refused.stderr}"
    )
    assert (verb or "nothing") in refused.stderr, (
        f"the refusal does not say what it was given instead:\n{refused.stderr}"
    )


def test_repo_recover_opens_the_change_request_with_the_drafted_body(tmp_path: Path) -> None:
    """The other landing verb drafts too, through the same wrapper and the same graph.

    It lives beside `publish-branch`'s journeys because both recipes are one wrapper
    given a different verb, and the verb is exactly what a wrapper gets wrong: an
    argument list read for `publish-branch` and forwarded to `recover` would land the
    branch and describe it, and a body appended for one verb and dropped for the other
    would leave the recovery path exactly where it started. What is asserted is the
    body the host was asked to open the change request with, for a branch whose
    provenance only `recover` knows how to attest.
    """
    hosted = _hosted(tmp_path, answers=[json.dumps({"body": DRAFTED_BODY})])
    _incomplete_branch(hosted.checkout)

    recovered = _just(
        "repo-recover",
        INCOMPLETE_BRANCH,
        "--repo",
        str(hosted.checkout),
        environment=hosted.environment,
        timeout=600,
    )

    assert recovered.returncode == 0, recovered.stderr + recovered.stdout
    opened = _opened_change_requests(hosted)
    assert len(opened) == 1, f"the host was asked to open {len(opened)} change requests"
    assert opened[0].body == DRAFTED_BODY, (
        f"the recovery opened its change request with {opened[0].body!r}; the drafting "
        f"turn answered {DRAFTED_BODY!r}"
    )


@pytest.mark.parametrize(
    ("sent", "expected"),
    [
        pytest.param(signal.SIGINT, 130, id="interrupted-at-the-terminal"),
        pytest.param(signal.SIGTERM, 143, id="terminated"),
        pytest.param(signal.SIGHUP, 129, id="hung-up-on"),
    ],
)
def test_a_signalled_landing_leaves_no_drafted_body_behind(
    tmp_path: Path, sent: signal.Signals, expected: int
) -> None:
    """A landing somebody stopped mid-draft tidies up, and lands nothing.

    The drafted body is written to a temporary file only so `onevcs` can read it back,
    and an `EXIT` trap alone does not cover the exits an operator actually takes: an
    unhandled signal kills the shell outright and the file survives, which leaves a
    change request's prose lying in the host's temporary directory. All three are
    driven because each needs its own handler and a missing one is invisible from the
    others — Ctrl-C at a terminal is `SIGINT`, a supervisor stopping it is `SIGTERM`,
    and a closed session is `SIGHUP`. The wrapper is run directly rather than through
    `just`, because signalling `just` says nothing about which process handled it.

    Nothing may land either: the signal arrives while the turn is in flight, which is
    before `onevcs` has been called at all.
    """
    hosted = _hosted(tmp_path, answers=[json.dumps({"body": DRAFTED_BODY})])
    _finished_branch(hosted.checkout)
    # Long enough that the signal lands on a turn still in flight even when this host is
    # busy, and no longer: the wrapper waits out the whole hold before its trap can run.
    hosted.environment["FAKE_CODEX_HOLD_SECONDS"] = "4"
    # Its own temporary directory, so what the wrapper left behind is readable rather
    # than mixed in with the host's.
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    hosted.environment["TMPDIR"] = str(scratch)
    launches = Path(hosted.environment["FAKE_CODEX_ATTEMPT_LOG"])

    landing = subprocess.Popen(
        [
            str(REPO_ROOT / "scripts" / "land-branch.sh"),
            "publish-branch",
            FINISHED_BRANCH,
            "--repo",
            str(hosted.checkout),
        ],
        cwd=REPO_ROOT,
        env=hosted.environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        until = deadline(120)
        while not launches.exists():
            assert time.monotonic() < until, "the drafting turn never reached the provider"
            assert landing.poll() is None, "the landing exited before it spent a turn"
            time.sleep(0.05)
        landing.send_signal(sent)
        printed, reported = landing.communicate(timeout=e2e_timeout(120))
    finally:
        if landing.poll() is None:  # pragma: no cover - only on a wrapper that ignored it
            landing.kill()
            # Bounded, because a killed process is not a process whose pipes are
            # closed: anything it spawned inherited them and can hold them open, and
            # an undrained `communicate()` then waits on that grandchild for as long
            # as this tier is left running. Expiry is not a failure here — this is
            # cleanup, and raising would mask whatever the journey was reporting.
            with contextlib.suppress(subprocess.TimeoutExpired):
                landing.communicate(timeout=e2e_timeout(60))

    assert landing.returncode == expected, (
        f"the landing signalled with {sent.name} exited {landing.returncode}; {expected} is "
        f"what says a caller stopped it rather than that the verb refused the branch"
        f"\n{printed}\n{reported}"
    )
    # Named by the wrapper's own prefix rather than by an empty directory: `uv` leaves a
    # lock of its own in whatever `TMPDIR` names, and that is not this wrapper's to clear.
    left = list(scratch.glob("orchestrator-land-branch-*"))
    assert left == [], f"the drafted body outlived the landing a caller interrupted: {left}"
    assert _opened_change_requests(hosted) == [], (
        "a change request was opened although the landing was stopped before its verb ran"
    )


def test_publish_branch_forwards_an_option_the_wrapper_does_not_know_and_drafts_nothing(
    tmp_path: Path,
) -> None:
    """An argument list the wrapper cannot read lands exactly as it did before it drafted.

    The failure a drafter must not introduce is turning a working invocation into a
    refusal of its own. `--help` is the case that exists today: the wrapper does not
    know whether the next word is that option's value or the branch, so it stops
    reading, spends no turn, and hands the list to `onevcs` — which answers it. A
    wrapper that guessed instead would draft for whatever word it mistook for a branch.
    """
    hosted = _hosted(tmp_path, answers=[json.dumps({"body": DRAFTED_BODY})])
    _finished_branch(hosted.checkout)

    helped = _just("publish-branch", "--help", environment=hosted.environment)

    assert helped.returncode == 0, helped.stderr + helped.stdout
    assert "--body-file" in helped.stdout, (
        f"`onevcs` never answered the option the wrapper forwarded:\n{helped.stdout}"
    )
    assert _drafting_turns(hosted) == [], "a drafting turn was spent on an unreadable list"


def _listed_alias(hosted: Hosted) -> str:
    """The alias `just repos` lists this checkout under — the form an operator copies.

    Read out of the listing rather than written here: the listing is the whole of what
    an operator has to go on, so a release that derived a different alias changes what
    these journeys drive rather than leaving them asserting a name nothing lists.
    """
    listed = _just("repos", environment=hosted.environment)
    assert listed.returncode == 0, listed.stderr + listed.stdout
    for line in listed.stdout.splitlines():
        if not line.startswith("  "):
            continue
        alias, _, path = line.strip().partition("\t")
        if path and Path(path).resolve() == hosted.checkout.resolve():
            return alias
    raise AssertionError(f"`just repos` lists no alias for {hosted.checkout}:\n{listed.stdout}")


@pytest.mark.parametrize(
    ("recipe", "branch", "prepare"),
    [
        pytest.param("publish-branch", FINISHED_BRANCH, _finished_branch, id="publish-branch"),
        pytest.param("repo-recover", INCOMPLETE_BRANCH, _incomplete_branch, id="repo-recover"),
    ],
)
def test_a_landing_drafts_when_repo_names_the_alias_just_repos_lists(
    tmp_path: Path,
    recipe: str,
    branch: str,
    prepare: Callable[[Path], object],
) -> None:
    """An alias is a checkout's name here, so a landing that gets one still drafts.

    `onevcs` takes an alias wherever it takes a `--repo` and an operator types the one
    `just repos` printed, so while this wrapper only stat'd that value every alias-form
    landing met the drafter's `is not a directory` refusal, spent no turn, and opened
    its change request with an empty description — behind a message that read like a
    refusal, in front of a landing that succeeded.

    Both recipes are driven because both are this one wrapper given a different verb.
    What is asserted is the description the host was asked to open the change request
    with, since that is the thing that was lost, and the alias is checked against the
    recipes' own working directory: were it a path there, this would prove nothing.
    """
    hosted = _hosted(tmp_path, answers=[json.dumps({"body": DRAFTED_BODY})])
    prepare(hosted.checkout)
    alias = _listed_alias(hosted)
    assert not (REPO_ROOT / alias).exists(), (
        f"the checkout these recipes run from holds a {alias!r} of its own, so `--repo "
        f"{alias}` would be read as a path and this journey would prove nothing"
    )

    landed = _just(
        recipe,
        branch,
        "--repo",
        alias,
        environment=hosted.environment,
        timeout=600,
    )

    assert landed.returncode == 0, landed.stderr + landed.stdout
    opened = _opened_change_requests(hosted)
    assert len(opened) == 1, f"the host was asked to open {len(opened)} change requests"
    assert opened[0].body == DRAFTED_BODY, (
        f"the alias-form landing opened its change request with {opened[0].body!r}; the "
        f"drafting turn answered {DRAFTED_BODY!r}"
    )


#: A `--repo` this registry answers to nothing: not a directory of the checkout the
#: recipes run from, and not a registered alias, identity, or origin.
UNKNOWN_REPO = "no-such-checkout"


def test_publish_branch_lands_with_no_body_when_repo_names_neither_directory_nor_alias(
    tmp_path: Path,
) -> None:
    """A `--repo` nothing resolves is `onevcs`'s to judge, exactly as before.

    The lookup that makes an alias work must not become a second opinion on the
    argument, so a value neither side knows keeps today's ending: one drafter line, no
    turn, nothing from the wrapper, and the verb's own answer as the exit status. That
    verb is run directly beside the recipe because an exit status asserted against a
    number would pass on a wrapper that had started deciding these itself.
    """
    hosted = _hosted(tmp_path, answers=[json.dumps({"body": DRAFTED_BODY})])
    _finished_branch(hosted.checkout)
    direct = subprocess.run(
        ["uv", "run", "onevcs", "publish-branch", FINISHED_BRANCH, "--repo", UNKNOWN_REPO],
        cwd=REPO_ROOT,
        env=hosted.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        check=False,
    )
    assert direct.returncode != 0, direct.stdout + direct.stderr

    landed = _just(
        "publish-branch",
        FINISHED_BRANCH,
        "--repo",
        UNKNOWN_REPO,
        environment=hosted.environment,
        timeout=600,
    )

    said = landed.stderr + landed.stdout
    assert landed.returncode == direct.returncode, (
        f"the wrapper exited {landed.returncode} where the verb it forwards to exits "
        f"{direct.returncode}; an unresolvable `--repo` is the verb's to answer\n{said}"
    )
    # The verb was reached with the value the caller typed, rather than something the
    # lookup substituted for it: `onevcs`'s own refusal names it back.
    assert UNKNOWN_REPO in said, f"`onevcs` never answered the value the caller typed:\n{said}"
    diagnosed = [line for line in landed.stderr.splitlines() if line.startswith("draft-pr-body:")]
    assert len(diagnosed) == 1, (
        f"the operator was given {len(diagnosed)} drafting diagnostics for one unresolvable "
        f"`--repo`; the ending is one line\n{landed.stderr}"
    )
    assert "land-branch:" not in landed.stderr, (
        f"the wrapper refused a `--repo` of its own accord:\n{landed.stderr}"
    )
    assert _drafting_turns(hosted) == [], "a drafting turn was spent on a checkout nothing names"
    assert _opened_change_requests(hosted) == [], (
        "a change request was opened for a landing the verb itself refused"
    )


def test_a_branch_its_merge_path_refuses_has_already_paid_for_its_body(
    tmp_path: Path,
) -> None:
    """The accepted cost, proven rather than assumed: the turn is spent before the push.

    The body is an argument to `onevcs` and `onevcs` is what pushes, so drafting cannot
    wait for a verdict nothing has asked for yet. What that buys a branch the merge path
    then refuses is nothing, and this is the journey that says so out loud — it is the
    trade `docs/repo-lifecycle.md` documents, and a reader who doubts it can run this.
    Anyone moving drafting behind the push will fail here, which is the point: that
    change is a different repository's design and would be noticed.
    """
    hosted = _hosted(
        tmp_path,
        answers=[json.dumps({"body": DRAFTED_BODY})],
        pre_push='echo "the merge path refuses this branch" >&2; exit 1',
    )
    _finished_branch(hosted.checkout)

    refused = _just(
        "publish-branch",
        FINISHED_BRANCH,
        "--repo",
        str(hosted.checkout),
        environment=hosted.environment,
        timeout=600,
    )

    assert refused.returncode != 0, refused.stdout
    assert len(_drafting_turns(hosted)) == 1, (
        f"the refused branch spent {len(_drafting_turns(hosted))} drafting turns; one is "
        "what being drafted before the push costs"
    )
    assert _opened_change_requests(hosted) == [], (
        "a change request was opened for a branch its merge path refused"
    )


def test_publish_branch_lands_the_branch_when_the_drafting_dispatch_fails(
    tmp_path: Path,
) -> None:
    """The other bodyless ending lands too, and says which one it was.

    `no-body` is a turn that succeeded and answered with nothing; this is a dispatch
    that never produced an answer the schema accepted at all, which is the ending an
    exhausted retry budget reaches. They are separate endings because they take separate
    fixes, and they are separate journeys because a wrapper that absorbed one and not the
    other would leave every quota failure refusing a landing that has nothing to do with
    the body.
    """
    # Prose rather than the object the schema declares, at every attempt: the member dies
    # with no retained report, which is `onepipeline`'s `dispatch-failed`.
    hosted = _hosted(tmp_path, answers=["Here is the body, as prose rather than the object."])
    _finished_branch(hosted.checkout)

    published = _just(
        "publish-branch",
        FINISHED_BRANCH,
        "--repo",
        str(hosted.checkout),
        environment=hosted.environment,
        timeout=900,
    )

    assert published.returncode == 0, published.stderr + published.stdout
    opened = _opened_change_requests(hosted)
    assert [change.body for change in opened] == [""], (
        f"the change request opened with {[change.body for change in opened]!r}; a drafting "
        "dispatch that failed must publish nothing rather than something composed to stand in"
    )
    assert "dispatch-failed" in published.stderr, (
        "the operator was never told which ending left the change request bodyless:\n"
        f"{published.stderr}"
    )


def test_a_landing_that_cannot_remove_the_drafted_body_reports_it_and_still_lands(
    tmp_path: Path,
) -> None:
    """A body the wrapper could not delete is said out loud, and the branch lands anyway.

    The drafted body is written to a temporary file only so `onevcs` can read it back,
    and a copy of a change request's prose left in the host's temporary directory is
    exactly what an operator has to be told about — the sibling journey above proves the
    ordinary and the signalled exits leave none. What it must not do is take the landing
    with it: the verb has already verified and published the branch by the time the trap
    runs, so a wrapper that exited on a failed removal would report a landing that
    happened as one that did not.

    The condition is a real one and lands while the provider holds the drafting turn
    open: unlinking an entry needs write permission on the directory holding it, so a
    read-only subtree beside the body is what `rm -rf` reports and leaves. It is planted
    beside the body rather than over it, because what is under test is a landing that
    otherwise succeeded.
    """
    hosted = _hosted(tmp_path, answers=[json.dumps({"body": DRAFTED_BODY})])
    _finished_branch(hosted.checkout)
    # Long enough that the condition lands on a turn still in flight even when this host
    # is busy, and no longer: the wrapper waits out the whole hold before its trap runs.
    hosted.environment["FAKE_CODEX_HOLD_SECONDS"] = "4"
    # Its own temporary directory, so the scratch this landing makes is the only one
    # under it and what it left behind is readable rather than mixed in with the host's.
    scratch_root = tmp_path / "tmp"
    scratch_root.mkdir()
    hosted.environment["TMPDIR"] = str(scratch_root)
    launches = Path(hosted.environment["FAKE_CODEX_ATTEMPT_LOG"])

    landing = subprocess.Popen(
        [
            str(REPO_ROOT / "scripts" / "land-branch.sh"),
            "publish-branch",
            FINISHED_BRANCH,
            "--repo",
            str(hosted.checkout),
        ],
        cwd=REPO_ROOT,
        env=hosted.environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    held: Path | None = None
    try:
        until = deadline(120)
        while not launches.exists():
            assert time.monotonic() < until, "the drafting turn never reached the provider"
            assert landing.poll() is None, "the landing exited before it spent a turn"
            time.sleep(0.05)
        # Named by the wrapper's own prefix: `uv` leaves a lock of its own in whatever
        # `TMPDIR` names, and that is not this wrapper's scratch.
        made = sorted(scratch_root.glob("orchestrator-land-branch-*"))
        assert made, f"the landing made no scratch directory under {scratch_root}"
        scratch = made[0]
        held = scratch / "held"
        held.mkdir()
        (held / "kept").write_text("kept\n", encoding="utf-8")
        held.chmod(stat.S_IRUSR | stat.S_IXUSR)
        printed, reported = landing.communicate(timeout=e2e_timeout(600))
    finally:
        if landing.poll() is None:  # pragma: no cover - only on a landing that hung
            landing.kill()
            # Bounded, because a killed process is not a process whose pipes are
            # closed: anything it spawned inherited them and can hold them open, and
            # an undrained `communicate()` then waits on that grandchild for as long
            # as this tier is left running. Expiry is not a failure here — this is
            # cleanup, and raising would mask whatever the journey was reporting.
            with contextlib.suppress(subprocess.TimeoutExpired):
                landing.communicate(timeout=e2e_timeout(60))
        if held is not None:
            held.chmod(stat.S_IRWXU)
        shutil.rmtree(scratch_root, ignore_errors=True)

    assert landing.returncode == 0, (
        f"the landing exited {landing.returncode} over cleanup it only had to report; the "
        f"branch was already published by then\n{printed}\n{reported}"
    )
    assert f"the drafted body could not be removed from {scratch}" in reported, (
        f"the copy of the change request's prose left behind was never reported:\n{reported}"
    )
    opened = _opened_change_requests(hosted)
    assert len(opened) == 1, f"the host was asked to open {len(opened)} change requests"
    assert opened[0].body == DRAFTED_BODY, (
        f"the change request opened with {opened[0].body!r}; a cleanup the wrapper could "
        f"not finish must not change what a reviewer reads"
    )


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]
# llmlint: ignore-end[shell_test_tiers_stay_split]
