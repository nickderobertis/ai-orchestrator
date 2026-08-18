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

#: The types the scratch repository's `commit-msg` hook releases from, and the words it
#: refuses everything else with. A miniature of `.githooks/commit-msg`: what is under
#: test is that `onevcs` asks a repository's hook about the subject it is about to land,
#: not this policy's own wording, so the hook states the smallest policy that can refuse.
RELEASING_TYPES = ("feat", "fix", "perf")
HOOK_REFUSAL = "this repository does not release from that type"

#: How the adopted onevcs 0.6.1 refuses a subject the repository turns down, quoted to
#: the words that make it that release's refusal and not the previous one's.
#:
#: The discriminator is load-bearing and was measured both ways. Below 0.6.1 the hook was
#: still *reached* — a clone carries `core.hooksPath`, and git runs the hook itself on the
#: squash commit a publication writes — so a journey asserting only "refused" passes on
#: both releases and proves nothing. What 0.6.1 added is asking **before** anything is
#: written, and saying so: 0.5.0 answered `invalid input: git commit -m <subject> failed
#: (exit 1)` from the far side of a gate run and a merge, with the fix left for the
#: operator to infer.
ASKED_DELIBERATELY = "the repository's own commit-msg hook rejected the subject"
#: The other half of that refusal: what to do about it, which the bare git failure never
#: said. `--title` is the lever this journey itself pulls, so the sentence naming it is
#: the one an operator most needs.
NAMES_THE_FIX = "publish with an explicit title that satisfies it"

#: A subject the hook accepts, and one it does not. Both are passed as `--title`, so the
#: branch's own commits stay acceptable and the only thing under judgement is the subject
#: the publication composed — which is the whole of what a change request's title is.
RELEASING_TITLE = "feat: land the finished work"
NON_RELEASING_TITLE = "docs: describe the finished work"

#: What `_finished_branch` commits under. Releasing, so the hook accepts it when git
#: runs the hook on that commit — which is why it is the *first* subject recorded, and
#: why a `--title` is what the two journeys below actually vary.
BRANCH_SUBJECT = "feat: finish the work"

#: One pipe buffer on Linux, which is the size a gate had to exceed on stderr to wedge
#: the reader this host used to work around. Both streams are driven past it below.
PIPE_BUFFER = 64 * 1024
#: Lines per stream, and their width. 8000 x ~48 bytes is ~375 KiB each way — several
#: buffers, so the ordering of the reads matters rather than being incidental.
LOUD_LINES = 8000
#: A gate that is loud on both pipes and then succeeds. Written without quotes of any
#: kind because `_publication` renders the argv through `repr` and swaps `'` for `"`.
LOUD_GATE = (
    f"n=0; while [ $n -lt {LOUD_LINES} ]; do "
    "echo loud-gate-stdout-line-payload-padding-0123456789; "
    "echo loud-gate-stderr-line-payload-padding-0123456789 >&2; "
    "n=$((n+1)); done"
)


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


def _publication(tmp_path: Path, *, gate: list[str], subject_policy: bool = False) -> Publication:
    """A registered repository whose identity publishes locally under `gate`.

    `local-direct` deliberately: it is the one published policy that opens no change
    request, so the whole journey completes against a bare origin on disk with no
    network and no GitHub. The policy is written as the rules file's `default`,
    because a path origin has no host, owner, or name for a `match` to select on.

    `subject_policy` gives the repository a `commit-msg` hook arranged exactly the way
    `just bootstrap` arranges this repository's own — tracked under `.githooks/`, named
    by `core.hooksPath` — so a publication here meets a repository that states a subject
    policy rather than one that states none.
    """
    home = tmp_path / "onevcs-home"
    home.mkdir()
    seed = tmp_path / "seed"
    _git("init", "-q", "-b", BASE, str(seed), cwd=tmp_path)
    (seed / "README.md").write_text("seed\n", encoding="utf-8")
    subjects_seen = _write_subject_policy(tmp_path, seed) if subject_policy else None
    _git("add", "-A", cwd=seed)
    _git("commit", "-q", "-m", "init", cwd=seed)
    origin = tmp_path / "origin.git"
    _git("clone", "-q", "--bare", str(seed), str(origin), cwd=tmp_path)
    checkout = tmp_path / "checkout"
    _git("clone", "-q", str(origin), str(checkout), cwd=tmp_path)
    if subject_policy:
        # What `just bootstrap` does here, and the only half of the arrangement a clone
        # does not inherit: the tracked directory arrives with the content, the config
        # naming it does not.
        _git("config", "core.hooksPath", ".githooks", cwd=checkout)
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
    return Publication(checkout, origin, environment, subjects_seen)


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


def test_publish_branch_lands_a_branch_whose_gate_is_loud_on_both_pipes(tmp_path: Path) -> None:
    """The gate this host stopped silencing: far past one pipe buffer, on both streams.

    `config/onevcs.rules.yml` used to prepend `env NEXTEST_STATUS_LEVEL=fail` to nine
    gates, because an `onevcs` before 0.2.10 read a gate child's stdout to EOF before
    it read stderr at all: a gate that filled the 64 KiB stderr buffer wedged there
    forever and was reported as a *rejected* gate, twice failing complete work here.
    Retiring that wrapper is a claim about this stack rather than about cargo-nextest,
    so it is proven the way the defect appeared — by publishing behind a gate that is
    genuinely loud on both pipes, with nothing suppressing it.

    `LOUD_LINES` is asserted rather than assumed: the argv is run directly first, so a
    payload that quietly stopped exceeding the buffer would fail here instead of
    turning this into a journey that proves nothing.
    """
    gate = ["bash", "-c", LOUD_GATE]
    direct = subprocess.run(
        gate, text=True, capture_output=True, timeout=e2e_timeout(120), check=False
    )
    assert direct.returncode == 0, direct.stderr[-2000:]
    assert len(direct.stderr.encode()) > PIPE_BUFFER, len(direct.stderr.encode())
    assert len(direct.stdout.encode()) > PIPE_BUFFER, len(direct.stdout.encode())

    publication = _publication(tmp_path, gate=gate)
    _finished_branch(publication.checkout)

    published = _just(
        "publish-branch",
        FINISHED_BRANCH,
        "--repo",
        str(publication.checkout),
        environment=publication.environment,
    )

    assert published.returncode == 0, published.stderr[-2000:] + published.stdout[-2000:]
    assert PUBLISHED_FILE in _git("ls-tree", "--name-only", BASE, cwd=publication.origin), (
        f"the loud gate's branch never reached the origin's {BASE}:\n{published.stdout}"
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


def test_publish_branch_refuses_a_subject_the_repositorys_own_hook_turns_down(
    tmp_path: Path,
) -> None:
    """`onevcs` puts the subject it would land to the repository's `commit-msg` hook.

    New with the adopted onevcs 0.6.1 (#51), and narrower than it first looks — measured
    against both releases rather than read off the changelog. `commit-msg` appears nowhere
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
    publication = _publication(tmp_path, gate=["true"], subject_policy=True)
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
    # Asked deliberately and up front, which is the release difference; a journey held to
    # "refused" alone passes on the release below too, for the reason ASKED_DELIBERATELY
    # records.
    assert ASKED_DELIBERATELY in said, said
    assert NAMES_THE_FIX in said, said
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
    publication = _publication(tmp_path, gate=["true"], subject_policy=True)
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
