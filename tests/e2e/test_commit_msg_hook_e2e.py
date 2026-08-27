"""`.githooks/commit-msg` judges a subject the same way for both of its callers.

Git invokes it on a local commit; `onevcs` invokes it on the subject a publication is
about to open under, where there is no index, no diff, and no branch to consult. So
both are driven here against the same subjects and held to the same verdicts, and the
limit the hook refuses at is reconciled with the engine's own.

Nothing is faked: a real `git commit` in a real repository with the hook activated the
way `just bootstrap` activates it, the hook invoked directly on a message file outside
any repository at all, and a real `just orchestrate` for the limit.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from project_fixtures import local_project

from orchestrator.root import REPO_ROOT

#: The hook under test, and its sibling. Both live here because `core.hooksPath` is a
#: directory: `just bootstrap` points git at `.githooks` once and every hook in it is
#: live, which is the whole of "activated the way `pre-push` is".
HOOKS = REPO_ROOT / ".githooks"
COMMIT_MSG_HOOK = HOOKS / "commit-msg"

#: Subjects this repository releases from, so a hook that rejected one would stop
#: ordinary work. `refactor!` is here deliberately: the breaking marker is what makes
#: it releasing, and reading the type alone would refuse it.
RELEASING_SUBJECTS = (
    "feat: add the health endpoint",
    "fix(repos): keep merge-path gates under the capture limit",
    "perf(gate): replay the judged diff instead of re-rolling it",
    "refactor!: become a configuration layer over the published CLIs",
    "chore(deps)!: adopt an engine release that drops a plan field",
)

#: Subjects this repository does not release from. The first two are the measured
#: failures: both describe a change to tracked source, and both would have merged
#: green and never cut a release.
NON_RELEASING_SUBJECTS = (
    "docs: convert the corpus library",
    "chore(deps): adopt onepipeline 0.6.1",
    "test: stabilize pre-push smoke coverage",
    "refactor(llmlint): move the fingerprint",
)

#: The word the refusal is read by. The hook says which type it refused and what that
#: costs; a journey that matched the whole sentence would fail on a reword.
REFUSED_TYPE = "does not release from"

#: Subjects no policy here applies to, because nobody wrote them as a description of a
#: change: git's own generated wording, autosquash markers rewritten before they reach
#: history, and the two provenance commits `onevcs` squashes away. A hook that refused
#: these would break `git merge`, `git revert`, an autosquash rebase, and — worst —
#: `repo-recover`, which commits its marker and attestation through this same path.
EXEMPT_SUBJECTS = (
    "Merge branch 'main' into topic",
    'Revert "feat: add the health endpoint"',
    "fixup! feat: add the health endpoint",
    "squash! docs: fold this into the commit above",
    "amend! docs: reword the commit above",
    "chore: land the drafting graph; ## What (incomplete step)",
    "wip: land the drafting graph (incomplete step)",
    "chore: attest verified recovery of preserved work",
)

#: An editor-shaped message: git's instructions, the subject, a body, and more
#: instructions. The subject is a type this repository does not release from and the
#: body carries one it does, so a hook reading any other line reaches the other verdict.
EDITOR_MESSAGE = (
    "# Please enter the commit message for your changes. Lines starting\n"
    "# with '#' will be ignored, and an empty message aborts the commit.\n"
    "\n"
    "docs: convert the corpus library\n"
    "\n"
    "feat: this line is a body, not the subject.\n"
    "# On branch main\n"
)

#: The same shape with nothing but instructions left, which is what an abandoned commit
#: hands over. Git refuses an empty message itself, so the hook has nothing to say.
ABANDONED_MESSAGE = "\n# Please enter the commit message for your changes.\n"

#: How the hook declares the limit it refuses at. Read rather than restated: a second
#: copy here would agree with the hook by construction and prove nothing about either.
SUBJECT_LIMIT_DECLARATION = re.compile(r"^SUBJECT_LIMIT=(\d+)$", re.MULTILINE)

#: The registered checkouts this host tracks, and the origin whose provenance subjects
#: the hook exempts. Which checkout of it a host holds is per-host, so it is resolved
#: from that list rather than named here.
TRACKED_CHECKOUTS = REPO_ROOT / "config/onevcs.checkouts"
ONEVCS_ORIGIN = "nickderobertis/onevcs"

#: Where `onevcs` declares the subjects it writes, and how. The exemptions above are
#: this repository's copy of those two constants, so this is the file they are
#: reconciled against.
ONEVCS_PROVENANCE = Path("crates/onevcs/src/provenance.rs")
PROVENANCE_SUBJECT = re.compile(
    r"^pub\(crate\) const (?P<name>ATTESTATION_SUBJECT|INCOMPLETE_SUFFIX): "
    r'&str = "(?P<value>[^"]*)";$',
    re.MULTILINE,
)


def _declared_subject_limit() -> int:
    """What the hook holds a subject to, from the hook."""
    declared = SUBJECT_LIMIT_DECLARATION.search(COMMIT_MSG_HOOK.read_text(encoding="utf-8"))
    assert declared, f"{COMMIT_MSG_HOOK} no longer declares SUBJECT_LIMIT"
    return int(declared.group(1))


def _onevcs_checkout() -> Path | None:
    """Whichever registered checkout of `onevcs` this host actually holds, if any.

    The tracked list carries both host layouts, so the path is resolved by asking git
    which origin a checkout really has rather than by trusting a directory name.
    """
    for line in TRACKED_CHECKOUTS.read_text(encoding="utf-8").splitlines():
        entry = line.partition("#")[0].strip()
        path = Path(entry).expanduser() if entry else None
        if path is None or not (path / ".git").exists():
            continue
        origin = subprocess.run(
            ["git", "-C", str(path), "remote", "get-url", "origin"],
            text=True,
            capture_output=True,
            check=False,
        )
        resolved = origin.stdout.strip().removesuffix("/").removesuffix(".git")
        if origin.returncode == 0 and resolved.endswith(ONEVCS_ORIGIN):
            return path
    return None


def _run_hook_on(content: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """Invoke the hook on a whole message file, the one argument git gives it."""
    message = tmp_path / "subject.txt"
    message.write_text(content, encoding="utf-8")
    return subprocess.run(
        [str(COMMIT_MSG_HOOK), str(message)],
        # Deliberately outside any git checkout: a publication subject is judged with
        # no repository state, so a hook that reached for some would fail here.
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )


def _run_hook(subject: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """Invoke the hook the way a tool with a subject and no repository does."""
    return _run_hook_on(subject + "\n", tmp_path)


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """A real repository with the hook activated exactly as `just bootstrap` does."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    for command in (
        ["git", "init", "--initial-branch", "main"],
        ["git", "config", "user.name", "Journey"],
        ["git", "config", "user.email", "journey@example.invalid"],
        # The one activation step, and the same one for every hook in that directory.
        ["git", "config", "core.hooksPath", str(HOOKS)],
    ):
        subprocess.run(command, cwd=checkout, check=True, capture_output=True)
    return checkout


def _commit(repository: Path, subject: str, name: str) -> subprocess.CompletedProcess[str]:
    """Make one ordinary local commit, changing tracked source as every commit here does."""
    (repository / name).write_text(f"{subject}\n", encoding="utf-8")
    subprocess.run(["git", "add", name], cwd=repository, check=True, capture_output=True)
    return subprocess.run(
        ["git", "commit", "-m", subject],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize("subject", RELEASING_SUBJECTS)
def test_an_ordinary_commit_under_a_releasing_type_is_recorded(
    repository: Path, subject: str
) -> None:
    """The policy has to leave ordinary work alone, or it is the thing that gets removed."""
    committed = _commit(repository, subject, "source.txt")

    assert committed.returncode == 0, committed.stderr
    recorded = subprocess.run(
        ["git", "log", "-1", "--format=%s"],
        cwd=repository,
        text=True,
        capture_output=True,
        check=True,
    )
    assert recorded.stdout.strip() == subject


@pytest.mark.parametrize("subject", NON_RELEASING_SUBJECTS)
def test_a_commit_under_a_type_this_repository_does_not_release_from_is_refused(
    repository: Path, subject: str
) -> None:
    """The measured failure, stopped at the one moment it is cheap to fix.

    The commit must not be recorded at all: a refusal that still wrote the object
    would leave the subject on the branch and the operator believing otherwise.
    """
    refused = _commit(repository, subject, "source.txt")

    assert refused.returncode != 0, f"{subject!r} was recorded: {refused.stdout}"
    assert REFUSED_TYPE in refused.stderr, refused.stderr
    listed = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
    )
    assert listed.stdout.strip() == "", f"the refused subject was recorded: {listed.stdout}"


@pytest.mark.parametrize("subject", EXEMPT_SUBJECTS)
def test_a_subject_nobody_wrote_as_a_description_is_left_alone(
    repository: Path, subject: str
) -> None:
    """Git's own wording and the lifecycle's provenance commits are not descriptions.

    `repo-recover` writes its `(incomplete step)` marker and the attestation that
    clears it through this same hook path, so refusing either would break recovery in
    this repository — the branch state a publication squashes away is exactly what no
    subject policy applies to.
    """
    committed = _commit(repository, subject, "source.txt")

    assert committed.returncode == 0, committed.stderr


def test_a_subject_that_is_not_a_conventional_commit_is_refused(repository: Path) -> None:
    """Release automation reads the type, so a subject carrying none names nothing."""
    refused = _commit(repository, "made the thing work", "source.txt")

    assert refused.returncode != 0, refused.stdout
    assert "not a Conventional Commit" in refused.stderr, refused.stderr


def test_the_subject_is_the_first_line_of_the_message_that_carries_content(
    tmp_path: Path,
) -> None:
    """Git hands over the whole file, so the subject has to be found in it.

    A message written in an editor arrives with git's `#` instructions and a body
    around it. Reading the first line would judge an instruction; reading any line
    would judge the body — which here carries the opposite verdict on purpose.
    """
    refused = _run_hook_on(EDITOR_MESSAGE, tmp_path)

    assert refused.returncode != 0, f"the body or a comment was judged: {refused.stdout}"
    assert REFUSED_TYPE in refused.stderr, refused.stderr
    assert "docs: convert the corpus library" in refused.stderr, refused.stderr


def test_the_hook_invoked_with_no_message_file_refuses_rather_than_passing(
    tmp_path: Path,
) -> None:
    """A caller that names no file is refused, not answered with silent approval.

    `onevcs` invokes this the way git does, and a hook that exited 0 on a call it could
    not read would report every subject as acceptable — the one failure a subject policy
    cannot afford, because it is indistinguishable from working.
    """
    refused = subprocess.run(
        [str(COMMIT_MSG_HOOK)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert refused.returncode != 0, "the hook approved a call carrying no message file"
    assert "message file" in refused.stderr, refused.stderr


def test_a_message_carrying_no_content_is_left_to_git(tmp_path: Path) -> None:
    """An abandoned commit is git's to refuse, and refusing it twice would say nothing."""
    accepted = _run_hook_on(ABANDONED_MESSAGE, tmp_path)

    assert accepted.returncode == 0, accepted.stderr


def _subject_of_length(length: int) -> str:
    """A releasing subject of exactly `length` characters, so only its length is judged."""
    return "feat: " + "y" * (length - len("feat: "))


def test_a_subject_over_the_publication_limit_is_refused(repository: Path) -> None:
    """Refused here, where a reword is free, rather than after the gate has been paid for."""
    limit = _declared_subject_limit()

    refused = _commit(repository, _subject_of_length(limit + 1), "source.txt")

    assert refused.returncode != 0, refused.stdout
    assert str(limit) in refused.stderr, refused.stderr


def test_a_subject_at_the_publication_limit_is_recorded(repository: Path) -> None:
    """The boundary is inclusive, so the longest publishable subject is not refused."""
    committed = _commit(repository, _subject_of_length(_declared_subject_limit()), "source.txt")

    assert committed.returncode == 0, committed.stderr


@pytest.mark.reads_docs
def test_the_limit_the_hook_refuses_at_is_the_one_the_engine_publishes_under(
    tmp_path: Path,
) -> None:
    """The hook's number is reconciled with onevcs's, not merely with itself.

    `SUBJECT_LIMIT` is this repository's copy of `onevcs::provenance::SUBJECT_LIMIT`,
    and a copy that drifts high refuses nothing the publication would refuse while one
    that drifts low refuses work the publication would take. Neither is visible from
    inside the hook, so the engine is asked: `onepipeline`'s plan loader holds every
    lifecycle node title to that same limit and names it when it refuses one, which it
    does before dispatching anything.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    limit = _declared_subject_limit()
    plan = {
        "schema_version": 3,
        "name": "subject-limit-probe",
        "tasks": [
            {
                "id": "probe",
                "title": _subject_of_length(limit + 1),
                "task": "## What\nProbe the limit.\n\n## Why\nKeep contracts aligned.\n\n"
                "## Acceptance criteria\n- The limit is reported.\n",
                "repo": "ai-orchestrator-isolated",
            }
        ],
    }
    project = local_project(json.dumps(plan), "subject-limit-probe")

    refused = subprocess.run(
        ["just", "orchestrate", project],
        cwd=REPO_ROOT,
        env={**os.environ, "ONEPIPELINE_RUNS_DIR": str(tmp_path / "runs")},
        text=True,
        capture_output=True,
        check=False,
    )

    assert refused.returncode != 0, f"the engine accepted a {limit + 1}-character title"
    assert f"over the {limit}-character limit" in refused.stdout + refused.stderr, (
        f"the hook refuses at {limit} characters and the engine refuses at some other "
        f"length:\n{refused.stdout}\n{refused.stderr}"
    )


@pytest.mark.parametrize(
    "subject",
    [*RELEASING_SUBJECTS, *NON_RELEASING_SUBJECTS, *EXEMPT_SUBJECTS, "made the thing work"],
)
def test_a_publication_subject_is_judged_exactly_as_a_local_commit_is(
    repository: Path, tmp_path: Path, subject: str
) -> None:
    """One policy, two callers.

    `onevcs` runs this hook against a subject it is about to publish — with no index,
    no diff, and no branch of its own to read. A policy that consulted any of those
    would mean one thing at commit time and another at publication time, which moves
    the failure rather than closing it. So the same subjects are driven both ways and
    the two verdicts are compared, not each asserted against a restated expectation.
    """
    directly = _run_hook(subject, tmp_path)
    through_git = _commit(repository, subject, "source.txt")

    assert (directly.returncode == 0) == (through_git.returncode == 0), (
        f"{subject!r} was judged differently by its two callers: the hook alone exited "
        f"{directly.returncode} ({directly.stderr}), git exited {through_git.returncode} "
        f"({through_git.stderr})"
    )
    if directly.returncode != 0:
        # And for the same stated reason, not merely with the same exit code: git
        # passes a hook's stderr through, so the refusal an operator reads is this one.
        assert directly.stderr.strip() in through_git.stderr, (
            f"the two callers refused {subject!r} for different reasons: "
            f"{directly.stderr!r} against {through_git.stderr!r}"
        )


@pytest.mark.reads_checkouts
def test_the_provenance_subjects_the_hook_exempts_are_the_ones_onevcs_writes(
    tmp_path: Path,
) -> None:
    """The drift gate: the exemptions here, against the engine that authors them.

    `EXEMPT` carries this repository's copy of two subjects no file here decides —
    the attestation `repo-recover` commits and the suffix an incomplete-step marker
    carries. `onevcs` writes both through this hook, so a reword on its side turns
    every recovery in this repository into a refused commit, found the first time
    somebody recovers a branch rather than on the change that caused it. The literals
    are driven through the hook exactly as their author spells them.

    Uncached for the reason `test-checkouts` exists: its input is another
    repository's checkout, which no `nx.json` key covers, so a memoized green would
    be a verdict on whatever `onevcs` said when it was recorded. A host holding no
    checkout of it resolves nothing and reports that rather than passing.
    """
    checkout = _onevcs_checkout()
    if checkout is None:
        pytest.skip("this host holds no registered checkout of onevcs to reconcile against")
    declared = {
        found["name"]: found["value"]
        for found in PROVENANCE_SUBJECT.finditer(
            (checkout / ONEVCS_PROVENANCE).read_text(encoding="utf-8")
        )
    }
    assert declared.keys() == {"ATTESTATION_SUBJECT", "INCOMPLETE_SUFFIX"}, (
        f"{checkout / ONEVCS_PROVENANCE} no longer declares the provenance subjects "
        f"this hook exempts, so nothing here is reconciled: {sorted(declared)}"
    )

    # A marker's suffix is appended to a subject the policy would otherwise refuse, so
    # only the suffix can be what exempts it.
    onevcs_writes = (
        declared["ATTESTATION_SUBJECT"],
        f"chore: land the drafting graph {declared['INCOMPLETE_SUFFIX']}",
    )
    for subject in onevcs_writes:
        exempted = _run_hook(subject, tmp_path)
        assert exempted.returncode == 0, (
            f"onevcs writes {subject!r} through this hook and the hook refuses it, so "
            f"every recovery in this repository is a refused commit: {exempted.stderr}"
        )


def test_the_hook_is_activated_by_the_mechanism_that_activates_the_pre_push_gate() -> None:
    """`core.hooksPath` is a directory, so both hooks are live from one `just bootstrap`.

    What that leaves to check is the part a new hook can get wrong on its own: git
    ignores a hook file it cannot execute, and ignores it silently.
    """
    assert COMMIT_MSG_HOOK.parent == (HOOKS / "pre-push").parent
    assert os.access(COMMIT_MSG_HOOK, os.X_OK), (
        f"{COMMIT_MSG_HOOK} is not executable, so git would skip it without saying so"
    )
