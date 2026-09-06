"""`scripts/draft-pr-body.sh` really drafts one branch's change request body.

Drafting used to be reachable from one place: `onepipeline`'s publication closeout.
Every other way a branch lands here — `just publish-branch`, `just repo-recover`, the
`just integrate` train — opened a change request with no body, and that is the dominant
way branches land on this host. This script is the drafter reached from anywhere, and
`scripts/land-branch.sh` is what puts it in front of the two landing verbs that open a
change request; `tests/e2e/test_publish_branch_e2e.py` drives that pairing. What this
one holds is what the drafter itself owes, which is exactly what the run path produces:
the same graph, run in a tree of the branch, read back out of the same field.

Everything below that seam is real — the script, `oneagentgraph`, the real
`graphs/pr-author.yaml`, the real `oneharness.pr-author.toml`, the real
`config/pr-author-body.schema.json`, real git, and (where a base is resolved) a real
registered `onevcs` identity. Only the paid provider is scripted, at the one seam a
single-sided `kind: oneharness` member reaches it through: `tests/e2e/fake_codex.py`,
exactly as the drafting journeys in `tests/e2e/test_orchestrate_launch_e2e.py` do.
What makes that safe is isolation rather than substitution — `ONEVCS_HOME` points at a
scratch registry and the repository is a throwaway checkout of a throwaway bare origin,
so nothing this host has registered is read and no remote of its own is reachable.
"""

# The finding these answer is about which Nx project owns this file, so it is the file
# that is suppressed and a project split that would resolve it.
# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] see above
# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] see above

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import time
from pathlib import Path
from typing import NamedTuple

import pytest
from drafting_task_contract import onepipeline_opening
from harness_indirections import (
    INDIRECTION_SOURCES,
    INDIRECTIONS,
    established_indirections,
    harness_routing,
)
from nx_workspace import copy_working_tree
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

DRAFTER = REPO_ROOT / "scripts" / "draft-pr-body.sh"

#: The paid provider's stand-in, and the guard covering the identities
#: `ONEHARNESS_BIN_*` cannot reach. `graphs/pr-author.yaml`'s member is single-sided
#: `kind: oneharness`, so it runs its turn through the oneharness library and no
#: substituted `oneharness` CLI is on its path at all — the provider binary is.
FAKE_CODEX = Path(__file__).resolve().parent / "fake_codex.py"
PAID_PROVIDER_GUARD = Path(__file__).resolve().parent / "no-paid-provider"

#: Who the indirection helpers attribute their diagnostics to when one of them refuses.
INDIRECTION_CALLER = "tests/e2e/test_draft_pr_body_e2e.py"

#: The throwaway identity's base, and the branch drafted against it.
BASE = "main"
WORK_BRANCH = "work"

#: What the branch says about its own work. Two commits, because "the commit messages
#: of `base..branch` in order" is a claim about all of them and one commit cannot fail
#: it. The second carries a body as well as a subject, since a full message is what the
#: composed task promises the drafter and a subject-only log would pass either way.
FIRST_COMMIT = "feat: serve a health endpoint at /healthz"
SECOND_COMMIT = (
    "test: cover the health endpoint's failure path\n\n"
    "A deploy went out with the endpoint returning 500 and nothing caught it.\n"
)

#: The body the scripted provider answers with. Deliberately carries the characters a
#: shell is tempted to mangle — a trailing newline, blank lines, and backticks — because
#: "exactly the characters it answered with" is the whole stdout contract.
DRAFTED_BODY = "## What\n\nAdded `/healthz`.\n\n## Why\n\nOperators had nothing to poll.\n"


class Identity(NamedTuple):
    """One throwaway registered identity and the environment that isolates it."""

    #: The registered publication checkout, and the `--repo` the drafter is given.
    checkout: Path
    #: The environment carrying the scratch registry and the scripted provider.
    environment: dict[str, str]
    #: The file `tests/e2e/fake_codex.py` appends each turn's actual prompt to.
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


def _identity(tmp_path: Path, *, answers: list[str], register: bool = True) -> Identity:
    """A throwaway repository holding a finished branch, with the provider scripted.

    `register` is what the base-resolution journeys turn on: the drafter asks `onevcs`
    for a branch's base, and an unregistered checkout is the state in which it has no
    answer to give.
    """
    home = tmp_path / "onevcs-home"
    home.mkdir()
    (home / "rules.yml").write_text(
        "version: 3\n"
        "trailer_prefix: Orchestrator-\n"
        "rules: []\n"
        "default:\n"
        "  publication: local-direct\n"
        "  approvals: none\n",
        encoding="utf-8",
    )
    seed = tmp_path / "seed"
    _git("init", "-q", "-b", BASE, str(seed), cwd=tmp_path)
    (seed / "README.md").write_text("seed\n", encoding="utf-8")
    _git("add", "-A", cwd=seed)
    _git("commit", "-q", "-m", "init", cwd=seed)
    origin = tmp_path / "origin.git"
    _git("clone", "-q", "--bare", str(seed), str(origin), cwd=tmp_path)
    checkout = tmp_path / "checkout"
    _git("clone", "-q", str(origin), str(checkout), cwd=tmp_path)

    _git("checkout", "-q", "-b", WORK_BRANCH, cwd=checkout)
    (checkout / "healthz.py").write_text(
        "def healthz() -> str:\n    return 'ok'\n", encoding="utf-8"
    )
    _git("add", "-A", cwd=checkout)
    _git("commit", "-q", "-m", FIRST_COMMIT, cwd=checkout)
    (checkout / "test_healthz.py").write_text(
        "def test_healthz() -> None:\n    pass\n", encoding="utf-8"
    )
    _git("add", "-A", cwd=checkout)
    _git("commit", "-q", "-m", SECOND_COMMIT, cwd=checkout)
    # Back on the base, which is the state a publication checkout is kept in: the
    # drafter must cut its own tree rather than borrow whatever is checked out here.
    _git("checkout", "-q", BASE, cwd=checkout)

    environment = dict(os.environ)
    environment["ONEVCS_HOME"] = str(home)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # And the identities that seam cannot reach; see `no_paid_provider`.
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment["FAKE_CODEX_ANSWERS"] = json.dumps(answers)
    environment["FAKE_CODEX_ATTEMPT_LOG"] = str(tmp_path / "launches")
    prompts = tmp_path / "prompts.jsonl"
    environment["FAKE_CODEX_PROMPT_LOG"] = str(prompts)
    # Keeps this journey's harness history out of the host's.
    environment["XDG_STATE_HOME"] = str(tmp_path / "state")
    environment.update(established_indirections(INDIRECTION_CALLER))

    if register:
        registered = subprocess.run(
            ["just", "register-repo", str(checkout)],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(120),
            check=False,
        )
        assert registered.returncode == 0, registered.stderr + registered.stdout
    return Identity(checkout, environment, prompts)


def _draft(identity: Identity, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run the real script, from a directory that is neither the checkout nor the root.

    Deliberately not `REPO_ROOT`: the script has to reach its own checkout to name the
    graph, and a journey that ran it from there would never notice if it stopped.
    """
    return subprocess.run(
        [str(DRAFTER), *arguments],
        cwd=identity.checkout.parent,
        env=identity.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
        check=False,
    )


def _conforming(body: str = DRAFTED_BODY) -> list[str]:
    """The provider answering with the object the schema declares."""
    return [json.dumps({"body": body})]


class Untouched(NamedTuple):
    """A publication checkout's state, in the three ways the drafter could move it.

    Named rather than positional because a mismatch is read, not just counted: which
    field differs says whether a worktree entry outlived the turn, whether something
    was written into the tree, or whether the checkout was moved off its base.
    """

    #: Every linked worktree the checkout records, which is what `git worktree add`
    #: writes into its `.git` and what removing the directory alone leaves behind.
    worktrees: str
    #: Anything added, changed, or staged in the checkout's own tree.
    changes: str
    #: The commit it is on, which a turn run in the checkout instead of a cut worktree
    #: would move.
    head: str


def _untouched(checkout: Path) -> Untouched:
    """Everything about a publication checkout the drafter promises not to move."""
    return Untouched(
        worktrees=_git("worktree", "list", "--porcelain", cwd=checkout),
        changes=_git("status", "--porcelain", cwd=checkout),
        head=_git("rev-parse", "HEAD", cwd=checkout),
    )


def test_a_conforming_answer_reaches_stdout_as_the_characters_it_answered_with(
    tmp_path: Path,
) -> None:
    """The drafted body, byte for byte, and nothing else on stdout.

    `onevcs` opens a change request with exactly these characters, so a stdout that
    added a newline, stripped one, or carried a progress line beside the body would put
    that difference into a published description. Nothing else in this journey can catch
    that: every other assertion here is about which answer was chosen, not about what
    reached the caller.
    """
    identity = _identity(tmp_path, answers=_conforming())

    drafted = _draft(identity, WORK_BRANCH, "--repo", str(identity.checkout), "--base", BASE)

    assert drafted.returncode == 0, drafted.stderr + drafted.stdout
    assert drafted.stdout == DRAFTED_BODY, (
        f"the drafter printed {drafted.stdout!r}; `onevcs` publishes exactly what is "
        f"printed here, and the turn answered {DRAFTED_BODY!r}"
    )


def test_the_drafting_graph_starts_from_a_shell_that_establishes_no_indirection(
    tmp_path: Path,
) -> None:
    """The drafter establishes its own indirections, which is what a plain shell has.

    `oneharness` refuses to start a variant whose `env_from` names a variable the parent
    process does not set, and `oneharness.pr-author.toml`'s chain names one per alternate
    identity. A dispatch exports all three, so a drafter that only inherits them passes
    every other journey here and fails every out-of-band landing, which is the only way
    this script is run.

    What is stripped is read from those configs rather than listed, so a newly declared
    indirection reaches this journey at once; `HOME` is this journey's own because the
    helpers derive from it, which is what makes the derivation observable below.
    """
    assert INDIRECTIONS, (
        "no `env_from` indirection was found in the drafting configs, so stripping them "
        "proves nothing; check SINGLE_SIDED_CONFIGS in tests/e2e/harness_indirections.py"
    )
    identity = _identity(tmp_path, answers=_conforming())
    for indirection in INDIRECTIONS:
        identity.environment.pop(indirection, None)
    home = tmp_path / "plain-home"
    home.mkdir()
    identity.environment["HOME"] = str(home)

    drafted = _draft(identity, WORK_BRANCH, "--repo", str(identity.checkout), "--base", BASE)

    assert drafted.returncode == 0, (
        f"the drafter could not run without {', '.join(INDIRECTIONS)} in its "
        f"environment, which is every out-of-band landing:\n{drafted.stderr}{drafted.stdout}"
    )
    assert drafted.stdout == DRAFTED_BODY, drafted.stderr + drafted.stdout
    # `ensure_codex_alt_home` creates the directory it resolves, so finding one under a
    # `HOME` that held nothing is that helper having run inside the drafter.
    assert (home / ".codex-alt").is_dir(), (
        "the alternate Codex home was never derived under this journey's own HOME, so "
        "the drafter did not establish the indirections through the helpers that own them"
    )


#: The helpers the drafter has to source for those indirections, derived from the module
#: that declares each one's ONE source rather than listed again here: a helper added there
#: is one this journey demands the drafter cannot run without, without anybody remembering
#: to widen a second list.
SOURCED_HELPERS = tuple(Path(source.helper).name for source in INDIRECTION_SOURCES)


#: The three states a real restore can leave a sourced helper in. All three are driven
#: because the drafter tests two things about that path and no one state proves both:
#: `[ -f ]` refuses `absent` and `not-a-file`, and `[ -r ]` refuses `unreadable`.
UNUSABLE = ("absent", "unreadable", "not-a-file")


def _make_unusable(helper: Path, state: str) -> None:
    """Put one helper into the named state, in the checkout under test."""
    match state:
        case "absent":
            helper.unlink()
        case "unreadable":
            helper.chmod(0o000)
        case "not-a-file":
            helper.unlink()
            helper.mkdir()
        case unknown:
            raise AssertionError(f"{unknown} is not a state this journey knows how to make")


@pytest.mark.parametrize("unusable", UNUSABLE)
@pytest.mark.parametrize("helper", SOURCED_HELPERS)
def test_a_checkout_whose_helper_cannot_be_sourced_is_refused_by_that_file(
    tmp_path: Path, helper: str, unusable: str
) -> None:
    """A drafter that cannot establish the indirections says which file it wanted.

    Driven against a checkout of the scripts alone, so the refusal is this repository's
    own rather than one produced by breaking it. What must not happen is falling through
    to the turn: a helper this script cannot source is a value it cannot derive, and
    running anyway is the `unstartable` death the sourcing exists to prevent — reported
    from `oneagentgraph`'s exit code rather than from the file nobody can read.
    """
    scripts = tmp_path / "restored" / "scripts"
    shutil.copytree(REPO_ROOT / "scripts", scripts)
    _make_unusable(scripts / helper, unusable)
    identity = _identity(tmp_path, answers=_conforming(), register=False)
    launches = Path(identity.environment["FAKE_CODEX_ATTEMPT_LOG"])

    refused = subprocess.run(  # noqa: S603 - the real script, in a checkout one helper short
        [
            str(scripts / "draft-pr-body.sh"),
            WORK_BRANCH,
            "--repo",
            str(identity.checkout),
            "--base",
            BASE,
        ],
        cwd=identity.checkout.parent,
        env=identity.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )

    assert refused.returncode == 2, (
        f"the drafter exited {refused.returncode} with {helper} unsourceable; a helper it "
        f"cannot read is an environment failure\n{refused.stdout}\n{refused.stderr}"
    )
    assert refused.stdout == "", f"the drafter printed {refused.stdout!r} having drafted nothing"
    assert helper in refused.stderr, (
        f"the refusal does not name {helper}, so it does not say which file to restore:"
        f"\n{refused.stderr}"
    )
    assert not launches.exists(), "a drafting turn was spent by a drafter that could not start"


#: What a caller can set an indirection to that its helper will not take. Both refuse a
#: relative path outright, which is the one unusable value a caller can actually reach:
#: everything else about these paths the helpers derive from `HOME` themselves.
NOT_ABSOLUTE = "relative/not/absolute"


@pytest.mark.parametrize("overridden", INDIRECTIONS)
def test_an_indirection_its_helper_refuses_stops_the_drafter_before_the_turn(
    tmp_path: Path, overridden: str
) -> None:
    """An unusable override is the caller's mistake, and it is reported as one.

    `oneharness` would read this value into the variant it starts, so a drafter that
    carried it through would spend a turn to reach the same refusal from further away.
    What is asserted is what this script owes — a refusal, attributed to it, naming the
    variable an operator has to fix. The wording is the helpers' own, and pinning it here
    would be the second copy those files exist to prevent.
    """
    identity = _identity(tmp_path, answers=_conforming(), register=False)
    identity.environment[overridden] = NOT_ABSOLUTE
    launches = Path(identity.environment["FAKE_CODEX_ATTEMPT_LOG"])

    refused = _draft(identity, WORK_BRANCH, "--repo", str(identity.checkout), "--base", BASE)

    assert refused.returncode == 2, (
        f"the drafter exited {refused.returncode} on a {overridden} its helper refuses"
        f"\n{refused.stdout}\n{refused.stderr}"
    )
    assert refused.stdout == "", f"the drafter printed {refused.stdout!r} having drafted nothing"
    assert overridden in refused.stderr, (
        f"the refusal does not name {overridden}, so an operator is not told which "
        f"indirection to fix:\n{refused.stderr}"
    )
    assert "draft-pr-body" in refused.stderr, (
        f"the refusal is not attributed to the drafter, so an operator cannot tell which "
        f"command refused:\n{refused.stderr}"
    )
    assert not launches.exists(), "a drafting turn was spent on an environment it could not use"


def test_the_composed_task_carries_onepipelines_sentence_and_the_branchs_own_commits(
    tmp_path: Path,
) -> None:
    """The drafter is told which branch it is on, and what that branch says about itself.

    `graphs/pr-author.yaml`'s member replaces whatever task it is handed with its own
    prose and interpolates `{task}` back in, so this composed text is the only thing
    that names the branch at all — a drafter given anything less describes a diff it was
    never introduced to. The prompt is read where a single-sided member's prompt can be
    read: the provider binary's own log, since that member's turn is an in-process
    oneharness call with no spawned CLI to observe.
    """
    identity = _identity(tmp_path, answers=_conforming())

    drafted = _draft(identity, WORK_BRANCH, "--repo", str(identity.checkout), "--base", BASE)
    assert drafted.returncode == 0, drafted.stderr + drafted.stdout

    given = [
        json.loads(line)["prompt"]
        for line in identity.prompts.read_text(encoding="utf-8").splitlines()
    ]
    assert given, "the provider was never given a prompt, so nothing was drafted"
    composed = given[0]
    # The sentence itself is read from the script's one declaration, and whether that
    # declaration is still `onepipeline`'s is `tests/test_drafting_task_contract.py`'s
    # question. What is proven here is the half that document cannot see: that the
    # declared sentence actually reaches the prompt, rather than being declared and
    # dropped somewhere between composing the task and handing it to the graph.
    assert onepipeline_opening() in composed, (
        "the composed task does not open with `onepipeline`'s own sentence, so this "
        f"drafter is answering a different question than the run path's:\n{composed}"
    )
    assert WORK_BRANCH in composed and BASE in composed, (
        f"the composed task names neither the branch nor its base:\n{composed}"
    )
    for message in (FIRST_COMMIT, SECOND_COMMIT.strip()):
        assert message in composed, (
            f"the composed task is missing the commit message {message!r}, so the branch's "
            f"own record of its work never reached the drafter:\n{composed}"
        )
    # The one thing the member's own prose gets wrong out of band: it is told to source
    # `## Why` from the task's `## Why`, and there is none here. The composed task has to
    # say so, or the drafter invents the motivation nobody wrote down.
    assert "A `## Why` you invented is not." in composed, (
        "the composed task never tells the drafter to leave `## Why` thin rather than "
        f"invent one, which is the whole difference from the run path's task:\n{composed}"
    )


def test_an_answer_the_schema_refuses_at_every_attempt_draws_no_body(tmp_path: Path) -> None:
    """A refused answer is never published, however plausible its prose.

    The schema is this member's whole review — it has no judge side — so an answer it
    rejects is the failure `schema_max_retries` exists to absorb and, past that, the
    failure this exit code exists to report. What must not happen is the refused text
    reaching stdout: `onevcs` would open a change request with it and nothing downstream
    is watching.

    Which of the three endings that is, is measured rather than assumed. On the adopted
    `oneagentgraph`, an exhausted retry budget kills the member and retains no report at
    all, so the honest reading is `onepipeline`'s `dispatch-failed` — the drafting
    dispatch ran without succeeding — and `schema-refused` is what a retained report
    whose entries all failed validation would be.
    """
    refused = "Here is the body you asked for, as prose rather than as the object."
    identity = _identity(tmp_path, answers=[refused])

    drafted = _draft(identity, WORK_BRANCH, "--repo", str(identity.checkout), "--base", BASE)

    assert drafted.returncode == 3, (
        f"the drafter exited {drafted.returncode}; a run that drafted no body exits 3\n"
        f"{drafted.stdout}\n{drafted.stderr}"
    )
    assert drafted.stdout == "", (
        f"the drafter printed {drafted.stdout!r} having drafted no body; only a drafted "
        "body ever reaches stdout"
    )
    assert refused not in drafted.stderr, "the refused answer's own text was reported back"
    ending = drafted.stderr.strip().splitlines()[-1]
    assert "dispatch-failed" in ending, (
        f"the drafter reported {ending!r}, which names none of `onepipeline`'s endings; "
        "an operator reads that word to know whether to fix the graph, the schema, or "
        "the persona's prose"
    )


def test_an_answer_that_conforms_with_nothing_in_it_draws_no_body(tmp_path: Path) -> None:
    """A conforming empty body is the third ending, and not a published empty description.

    `config/pr-author-body.schema.json` requires a string and states no minimum length,
    so a turn that answers `{"body": ""}` passes validation and carries nothing. Printing
    it would publish exactly the empty description this script exists to end, which is
    why the read requires a body with something in it once trimmed.
    """
    identity = _identity(tmp_path, answers=_conforming(body="   \n\n"))

    drafted = _draft(identity, WORK_BRANCH, "--repo", str(identity.checkout), "--base", BASE)

    assert drafted.returncode == 3, drafted.stdout + drafted.stderr
    assert drafted.stdout == "", f"the drafter printed the empty body {drafted.stdout!r}"
    assert "no-body" in drafted.stderr, (
        f"the drafter did not report `no-body`, which is the ending that points at the "
        f"drafting persona's prose:\n{drafted.stderr}"
    )


#: The variable a mutated drafting chain reads its one candidate's identity out of, and
#: that nothing the drafter establishes sets. `oneharness` refuses to start a variant
#: whose `env_from` names an unset variable, which is the state an operator's landing
#: was actually in when this diagnosis went missing.
UNSET_INDIRECTION = "ORCHESTRATOR_DRAFT_PR_BODY_NOBODY_SETS_THIS"

#: The same thing, long enough that `oneagentgraph`'s own bound on a death detail cuts
#: it. Measured rather than chosen: that bound is 4096 characters on the adopted
#: release, and only a detail past it carries the flag this journey reads back.
OVERLONG_INDIRECTION = f"ORCHESTRATOR_{'U' * 8000}"

#: What the drafter appends to a detail `oneagentgraph` flagged as cut.
TRUNCATION_FLAG = "(oneagentgraph truncated this detail)"

#: The chain declaration a mutated config narrows to one candidate. Anchored to the
#: start of a line so the same word inside the prose above it cannot be rewritten.
HARNESS_CHAIN = re.compile(r"^harnesses = \[[^]]*\]", re.MULTILINE)

#: How the `dispatch-failed` ending names the file its diagnosis was read out of.
KEPT_EVENTS = re.compile(r"its events are kept at (?P<path>\S+)")

#: The line the drafter writes above that ending for each member `oneagentgraph` said
#: it killed. Read for presence by the death journeys and for absence by the run that
#: recorded none, so the same pattern decides both and neither can be vacuous.
DEATH_SENTENCE = re.compile(r"^draft-pr-body: .* died: ", re.MULTILINE)

#: The drafting chain this journey makes unstartable, as the drafter really resolves it.
DRAFTING_CONFIG = REPO_ROOT / "oneharness.pr-author.toml"


class Candidate(NamedTuple):
    """One identity of the drafting chain, and the indirection its variant reads.

    Named rather than positional because the two are both strings and are used for
    opposite things: one is written into the chain to keep it, the other is written
    over to kill it, and swapping them would produce a chain that starts.
    """

    #: The chain entry, spelled as `harnesses` spells it (`codex:alternate`).
    identity: str
    #: The variable in the parent process that entry takes its own value out of.
    source: str


class DiedDrafting(NamedTuple):
    """One real drafting run whose only candidate could not start.

    Three unrelated things, which is why they are named: the finished process whose
    stderr is the diagnosis under test, the identity it drafted against, and what its
    publication checkout looked like before the run.
    """

    drafted: subprocess.CompletedProcess[str]
    identity: Identity
    before: Untouched


def _indirected_candidate() -> Candidate:
    """One identity of the drafting chain that reads an indirection, and that variable.

    Read out of the config rather than named here. The chain reorders and renames as
    identities come and go, and a journey that killed one this drafter no longer routes
    to would pass while proving nothing about the chain it actually runs.
    """
    routing = harness_routing(DRAFTING_CONFIG)
    for candidate in routing.get("harnesses", []):
        harness, _, variant = candidate.partition(":")
        declared = (
            routing.get("harness", {})
            .get(harness, {})
            .get("variant", {})
            .get(variant, {})
            .get("env_from", {})
        )
        for source in declared.values():
            return Candidate(candidate, source)
    raise AssertionError(
        f"no identity in {DRAFTING_CONFIG.name}'s chain reads an environment "
        "indirection, so nothing in it can be made unstartable this way"
    )


def _unstartable_checkout(tmp_path: Path, indirection: str) -> Path:
    """A copy of this checkout whose drafting chain holds one candidate that cannot start.

    A copy, because the script resolves the graph, the configs, and the schema from its
    own directory: the only way to hand it a chain that dies is to hand it a checkout of
    its own. The working tree rather than a `git worktree add` of HEAD, because the
    script this proves is the uncommitted one.

    The mutation is the one this suite already makes to prove an indirection refusal —
    an `env_from` pointed at a variable the parent process does not set — with the chain
    narrowed to that candidate first, so no identity behind it can rescue the turn and
    hide the death.
    """
    checkout = tmp_path / "drafter-checkout"
    checkout.mkdir()
    copy_working_tree(checkout)
    candidate = _indirected_candidate()
    configuration = checkout / DRAFTING_CONFIG.name
    routing = configuration.read_text(encoding="utf-8")
    narrowed, chains = HARNESS_CHAIN.subn(f'harnesses = ["{candidate.identity}"]', routing)
    assert chains == 1, (
        f"{DRAFTING_CONFIG.name} declares {chains} `harnesses = [...]` lines where one "
        "was expected, so this journey cannot narrow the chain it must narrow"
    )
    assert candidate.source in narrowed, (
        f"{DRAFTING_CONFIG.name} no longer names {candidate.source}, so repointing it "
        f"would leave {candidate.identity} startable and the member alive"
    )
    configuration.write_text(narrowed.replace(candidate.source, indirection), encoding="utf-8")
    return checkout


def _draft_from(
    checkout: Path, identity: Identity, *arguments: str
) -> subprocess.CompletedProcess[str]:
    """Run a copied checkout's own drafter, against this checkout's provisioned tools.

    The copy is not a synced project of its own and must not become one: `uv run` inside
    it is pointed at the environment this checkout already has, which is the same wiring
    every other journey that copies this tree uses.
    """
    return subprocess.run(
        [str(checkout / "scripts" / "draft-pr-body.sh"), *arguments],
        cwd=identity.checkout.parent,
        env={
            **identity.environment,
            "UV_NO_SYNC": "1",
            "UV_PROJECT_ENVIRONMENT": str(REPO_ROOT / ".venv"),
        },
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
        check=False,
    )


def _died_drafting(tmp_path: Path, indirection: str) -> DiedDrafting:
    """One real drafting run whose only candidate cannot start, and what it started from."""
    identity = _identity(tmp_path, answers=_conforming(), register=False)
    # Its own temporary directory, so the scratch a kept diagnosis leaves behind is
    # under this journey's `tmp_path` rather than the host's `/tmp`.
    scratch_root = tmp_path / "tmp"
    scratch_root.mkdir()
    identity.environment["TMPDIR"] = str(scratch_root)
    checkout = _unstartable_checkout(tmp_path, indirection)
    before = _untouched(identity.checkout)

    drafted = _draft_from(
        checkout, identity, WORK_BRANCH, "--repo", str(identity.checkout), "--base", BASE
    )

    assert drafted.returncode == 3, (
        f"the drafter exited {drafted.returncode} on a member that never started; a run "
        f"that drafted no body exits 3\n{drafted.stdout}\n{drafted.stderr}"
    )
    assert drafted.stdout == "", (
        f"the drafter printed {drafted.stdout!r} having drafted no body; only a drafted "
        "body ever reaches stdout"
    )
    return DiedDrafting(drafted, identity, before)


@pytest.mark.reads_docs
def test_a_member_that_could_not_start_reports_what_oneagentgraph_said_killed_it(
    tmp_path: Path,
) -> None:
    """The dead member's own classification reaches the operator reading the failure.

    `oneagentgraph` hands over exactly the three things that decide what to fix — how it
    classified the death, what it attributed it to, and the sentence naming the variable
    nobody set — and the drafter used to drop all three and report the exit code the
    graph ended on. Finding that variable by hand cost twenty minutes, and drafting is
    read mid-landing, between the drafting turn and the publication.

    The events file behind that diagnosis has to survive too. It lives in the scratch
    directory the drafter removes on every other path, so a run whose only record of why
    it failed had already been deleted left nothing to check the sentence against.
    """
    died = _died_drafting(tmp_path, UNSET_INDIRECTION)
    launches = Path(died.identity.environment["FAKE_CODEX_ATTEMPT_LOG"])
    report = died.drafted.stderr

    assert not launches.exists(), "a turn was spent by a member that could not start"
    assert DEATH_SENTENCE.search(report) is not None, (
        f"the failure carries no `<member> died:` line, so the pattern the result-less "
        f"run reads for absence would pass against any report at all:\n{report}"
    )
    for stated in ("rule=unstartable", "cause=spawn", "detail=", UNSET_INDIRECTION):
        assert stated in report, (
            f"the failure does not carry {stated!r}, so it does not say what killed the "
            f"drafting member:\n{report}"
        )
    assert TRUNCATION_FLAG not in report, (
        f"a detail oneagentgraph handed over whole was reported as cut:\n{report}"
    )
    ending = report.strip().splitlines()[-1]
    assert "dispatch-failed" in ending, (
        f"the drafter reported {ending!r} last, where the ending an operator reads first "
        f"belongs; the diagnosis goes above it, not in place of it:\n{report}"
    )

    named = KEPT_EVENTS.search(report)
    assert named is not None, (
        f"the failure names no events file, so the diagnosis it just quoted cannot be "
        f"checked against the run that produced it:\n{report}"
    )
    kept = Path(named.group("path"))
    assert kept.is_file(), (
        f"the events file the failure named at {kept} did not survive the run, so what "
        "an operator is sent to read was deleted on the way out"
    )
    assert "member-died" in kept.read_text(encoding="utf-8"), (
        f"the events kept at {kept} hold no member-died envelope, so they are not the "
        "record the reported diagnosis came out of"
    )
    assert _untouched(died.identity.checkout) == died.before, (
        "the drafter kept its scratch directory and this checkout's worktree entry with "
        "it; what a diagnosis needs is the events file, never an entry left behind in "
        f"somebody else's repository:\nbefore {died.before}\n"
        f"after  {_untouched(died.identity.checkout)}"
    )


@pytest.mark.reads_docs
def test_a_detail_oneagentgraph_cut_is_reported_as_cut(tmp_path: Path) -> None:
    """A bounded detail is said to be bounded, rather than presented as the whole of it.

    `oneagentgraph` bounds a death detail itself and flags the ones it cut, so the honest
    report is its flag rather than a second bound applied here — and a cut detail passed
    on silently is worse than a long one, because the thing an operator has to fix can be
    in the part that is missing. This one is: the variable's name is cut off its front.
    """
    report = _died_drafting(tmp_path, OVERLONG_INDIRECTION).drafted.stderr
    assert TRUNCATION_FLAG in report, (
        f"a detail oneagentgraph flagged as truncated was reported as though it were "
        f"whole:\n{report}"
    )
    assert OVERLONG_INDIRECTION not in report, (
        "the whole indirection reached the failure, so this run did not truncate and the "
        "flag above was read off some other detail"
    )


def _configless_checkout(tmp_path: Path) -> Path:
    """A copy of this checkout with the config `graphs/pr-author.yaml` names taken out.

    That ref is relative to the graph document, so `oneagentgraph` refuses the document
    before it starts anything: no member, no turn, and no death to classify. It is the
    state a checkout whose files were never restored is really in — the one the helper
    checks in this script answer with "run 'just bootstrap'" — and it is the half of the
    result-less path a dead member cannot produce, because there the diagnosis is the
    death and here there is no diagnosis at all.
    """
    checkout = tmp_path / "configless-checkout"
    checkout.mkdir()
    copy_working_tree(checkout)
    configuration = checkout / DRAFTING_CONFIG.name
    assert configuration.is_file(), (
        f"{DRAFTING_CONFIG.name} is not in a copy of this working tree, so removing it "
        "is not what makes the drafting graph unrunnable and this journey proves nothing"
    )
    configuration.unlink()
    return checkout


@pytest.mark.reads_docs
def test_a_dispatch_that_recorded_no_death_still_names_the_events_it_kept(
    tmp_path: Path,
) -> None:
    """Every result-less dispatch keeps its events and says where, death or no death.

    Naming and retaining that file is not the death diagnosis's own doing: the ending
    carries the path for any dispatch that produced no result, and the scratch directory
    survives with it. When `oneagentgraph` did classify a death the sentence above the
    ending is what an operator fixes from; when it recorded nothing — this run, where it
    refused the graph document before starting a member — the path is the only evidence
    there is, and a retention that silently stopped would leave the ending pointing at
    a file the trap had already removed.
    """
    identity = _identity(tmp_path, answers=_conforming(), register=False)
    # As the death journeys do: the scratch this run is expected to keep lands under
    # `tmp_path` rather than in the host's `/tmp`, so pytest reclaims what it retains.
    scratch_root = tmp_path / "tmp"
    scratch_root.mkdir()
    identity.environment["TMPDIR"] = str(scratch_root)
    checkout = _configless_checkout(tmp_path)
    before = _untouched(identity.checkout)

    drafted = _draft_from(
        checkout, identity, WORK_BRANCH, "--repo", str(identity.checkout), "--base", BASE
    )

    assert drafted.returncode == 3, (
        f"the drafter exited {drafted.returncode} on a graph that never ran; a run that "
        f"drafted no body exits 3\n{drafted.stdout}\n{drafted.stderr}"
    )
    assert drafted.stdout == "", (
        f"the drafter printed {drafted.stdout!r} having drafted no body; only a drafted "
        "body ever reaches stdout"
    )
    report = drafted.stderr
    assert not Path(identity.environment["FAKE_CODEX_ATTEMPT_LOG"]).exists(), (
        "a turn was spent by a graph oneagentgraph refused, so this run is not the "
        f"nothing-was-recorded case it is written to drive:\n{report}"
    )
    assert DEATH_SENTENCE.search(report) is None, (
        f"the drafter reported a death for a run that recorded none, so the sentence an "
        f"operator fixes from was invented rather than quoted:\n{report}"
    )
    ending = report.strip().splitlines()[-1]
    assert "dispatch-failed" in ending, (
        f"the drafter reported {ending!r} last, which names none of `onepipeline`'s "
        f"endings:\n{report}"
    )

    named = KEPT_EVENTS.search(report)
    assert named is not None, (
        f"the failure names no events file, so a run whose only evidence is that file "
        f"never tells an operator where it is:\n{report}"
    )
    kept = Path(named.group("path"))
    assert kept.is_file(), (
        f"the events file the failure named at {kept} did not survive the run; with no "
        "death to quote, the ending is pointing at evidence the trap removed"
    )
    assert "member-died" not in kept.read_text(encoding="utf-8"), (
        f"the events kept at {kept} hold a member-died envelope, so this run had a "
        "diagnosis after all and the retention it proves is the death journey's"
    )
    assert _untouched(identity.checkout) == before, (
        "the drafter kept its scratch directory and this checkout's worktree entry with "
        f"it; retention covers the events file alone:\nbefore {before}\n"
        f"after  {_untouched(identity.checkout)}"
    )


@pytest.mark.parametrize(
    ("answers", "expected"),
    [
        pytest.param(_conforming(), 0, id="drafted"),
        pytest.param(["not the object the schema declares"], 3, id="not-drafted"),
    ],
)
def test_the_checkout_is_left_exactly_as_it_was_found(
    tmp_path: Path, answers: list[str], expected: int
) -> None:
    """The turn's worktree is gone and the publication checkout never moved.

    This repository's standing rule is that a publication checkout is never worked in,
    and a temporary worktree is a change to it: `git worktree add` writes an entry into
    its `.git`, and removing only the directory would leave that entry behind. Both
    outcomes are driven because the failing path is the one that skips its own cleanup
    — a drafter that tidied up only after a body would strand a worktree on every
    branch it could not draft.
    """
    identity = _identity(tmp_path, answers=answers)
    before = _untouched(identity.checkout)

    drafted = _draft(identity, WORK_BRANCH, "--repo", str(identity.checkout), "--base", BASE)
    assert drafted.returncode == expected, drafted.stdout + drafted.stderr

    assert _untouched(identity.checkout) == before, (
        "the drafter left the publication checkout changed:\n"
        f"before {before}\nafter  {_untouched(identity.checkout)}"
    )
    assert not (identity.checkout / ".git" / "worktrees").exists(), (
        "a linked-worktree record is still in the checkout's .git, so the entry outlived "
        "the turn it was cut for"
    )


def test_the_base_is_resolved_from_onevcs_when_none_is_named(tmp_path: Path) -> None:
    """With no `--base`, the base is the one `onevcs` reports for that branch.

    Proven through what the drafter was shown rather than through the answer it got: the
    base decides the range of commits the composed task carries, so a drafter handed the
    wrong one describes the wrong work while still exiting 0. Both commits of
    `main..work` appearing is what says the resolved base was `main` and not the branch
    tip or this host's idea of a trunk.
    """
    identity = _identity(tmp_path, answers=_conforming())

    drafted = _draft(identity, WORK_BRANCH, "--repo", str(identity.checkout))

    assert drafted.returncode == 0, drafted.stderr + drafted.stdout
    composed = json.loads(identity.prompts.read_text(encoding="utf-8").splitlines()[0])["prompt"]
    for message in (FIRST_COMMIT, SECOND_COMMIT.strip()):
        assert message in composed, (
            f"the resolved base left {message!r} out of the range, so it was not the base "
            f"`onevcs` reports for this branch:\n{composed}"
        )


def test_a_base_that_cannot_be_resolved_is_refused_rather_than_guessed(tmp_path: Path) -> None:
    """An unregistered branch is a usage failure, not a draft against a guess.

    `onevcs` is what knows a branch's base, and it has no answer for a checkout nothing
    registered — the same state a name several identities answer to produces. Falling
    back to whatever this host calls its trunk would draft a body about a diff nobody
    asked about, and exit 0 while doing it.
    """
    identity = _identity(tmp_path, answers=_conforming(), register=False)

    drafted = _draft(identity, WORK_BRANCH, "--repo", str(identity.checkout))

    assert drafted.returncode == 2, (
        f"the drafter exited {drafted.returncode}; an unresolvable base is a usage "
        f"failure\n{drafted.stdout}\n{drafted.stderr}"
    )
    assert drafted.stdout == "", f"the drafter printed {drafted.stdout!r} having drafted nothing"
    assert "--base" in drafted.stderr, (
        f"the refusal does not name the flag that answers it:\n{drafted.stderr}"
    )
    assert not identity.prompts.exists(), (
        "a drafting turn was spent on a branch whose base was never resolved"
    )


def test_out_writes_the_body_and_leaves_stdout_silent(tmp_path: Path) -> None:
    """`--out` is what a caller that composes a publication command uses.

    Its whole point is that stdout stays empty, so the body — which is Markdown a shell
    would otherwise have to carry through a pipe — reaches a file intact and nothing has
    to be parsed out of a stream.
    """
    identity = _identity(tmp_path, answers=_conforming())
    body_file = tmp_path / "body.md"

    drafted = _draft(
        identity,
        WORK_BRANCH,
        "--repo",
        str(identity.checkout),
        "--base",
        BASE,
        "--out",
        str(body_file),
    )

    assert drafted.returncode == 0, drafted.stderr + drafted.stdout
    assert drafted.stdout == "", (
        f"the drafter printed {drafted.stdout!r} as well as writing the file; --out exists "
        "so that stdout carries nothing"
    )
    assert body_file.read_text(encoding="utf-8") == DRAFTED_BODY


#: The stand-ins a refusal case names instead of a path it cannot know yet. Resolved
#: against the fixture at call time, so each case below reads as the command line an
#: operator would actually type.
CHECKOUT = "<checkout>"
ABSENT = "<absent>"
PLAIN_DIRECTORY = "<plain-directory>"
UNWRITABLE_OUT = "<unwritable-out>"
OUT_IS_A_DIRECTORY = "<out-is-a-directory>"


def _resolved(arguments: tuple[str, ...], identity: Identity, tmp_path: Path) -> list[str]:
    """One refusal case's command line, with its stand-ins filled in."""
    plain = tmp_path / "not-a-repository"
    plain.mkdir(exist_ok=True)
    substitutions = {
        CHECKOUT: str(identity.checkout),
        ABSENT: str(tmp_path / "no-such-checkout"),
        PLAIN_DIRECTORY: str(plain),
        UNWRITABLE_OUT: str(tmp_path / "no-such-directory" / "body.md"),
        OUT_IS_A_DIRECTORY: str(plain),
    }
    return [substitutions.get(argument, argument) for argument in arguments]


@pytest.mark.parametrize(
    ("arguments", "names"),
    [
        pytest.param((), "no branch was named", id="no-branch"),
        pytest.param(("--repo", CHECKOUT), "must be the branch", id="flag-first"),
        pytest.param((WORK_BRANCH,), "no checkout was named", id="no-repo"),
        pytest.param((WORK_BRANCH, "--repo"), "--repo was given no value", id="repo-without-value"),
        pytest.param((WORK_BRANCH, "--repo="), "--repo was given no value", id="empty-equals-form"),
        pytest.param(
            (WORK_BRANCH, "--repo", CHECKOUT, "--base"),
            "--base was given no value",
            id="base-without-value",
        ),
        pytest.param(
            (WORK_BRANCH, "--repo", CHECKOUT, "--base="),
            "--base was given no value",
            id="empty-base-equals-form",
        ),
        pytest.param(
            (WORK_BRANCH, "--repo", CHECKOUT, "--base", BASE, "--out"),
            "--out was given no value",
            id="out-without-value",
        ),
        pytest.param(
            (WORK_BRANCH, "--repo", CHECKOUT, "--base", BASE, "--out="),
            "--out was given no value",
            id="empty-out-equals-form",
        ),
        pytest.param(
            (WORK_BRANCH, "--repo", CHECKOUT, "--base", "-x"),
            "git would read as an option",
            id="option-shaped-base",
        ),
        pytest.param(
            (WORK_BRANCH, "--repo", CHECKOUT, "--base=-x"),
            "git would read as an option",
            id="option-shaped-base-equals-form",
        ),
        pytest.param(
            (WORK_BRANCH, "--repo", CHECKOUT, "--pr", "12"),
            "is not an argument this drafter takes",
            id="unknown-argument",
        ),
        pytest.param(
            (WORK_BRANCH, "--repo", ABSENT), "is not a directory", id="checkout-does-not-exist"
        ),
        pytest.param(
            (WORK_BRANCH, "--repo", PLAIN_DIRECTORY),
            "is not a git repository",
            id="checkout-is-not-a-repository",
        ),
        pytest.param(
            ("no-such-branch", "--repo", CHECKOUT), "has no branch", id="branch-not-in-checkout"
        ),
        pytest.param(
            (WORK_BRANCH, "--repo", CHECKOUT, "--base", "v9.9.9"),
            "is not a commit the checkout",
            id="named-base-the-checkout-cannot-resolve",
        ),
        pytest.param(
            (WORK_BRANCH, "--repo", CHECKOUT, "--base", BASE, "--out", UNWRITABLE_OUT),
            "cannot be written to",
            id="out-into-a-directory-that-is-not-there",
        ),
        pytest.param(
            (WORK_BRANCH, "--repo", CHECKOUT, "--base", BASE, "--out", OUT_IS_A_DIRECTORY),
            "which is a directory",
            id="out-naming-a-directory",
        ),
    ],
)
def test_a_refusable_command_line_is_refused_before_a_turn_is_spent(
    tmp_path: Path, arguments: tuple[str, ...], names: str
) -> None:
    """Every precondition is checked before the drafting turn, not after it.

    A drafting turn costs a real provider call, and each of these is a mistake the
    caller has to fix whatever the drafter would have answered — so paying for the turn
    first would spend quota to reach the same refusal. The attempt log is what proves
    that: it is written by the provider binary itself, so a file that never appears is
    a turn that never happened. Each case also has to say which mistake it was, because
    a single `usage:` line leaves an operator to guess which of four flags was wrong.
    """
    identity = _identity(tmp_path, answers=_conforming(), register=False)
    launches = Path(identity.environment["FAKE_CODEX_ATTEMPT_LOG"])

    refused = _draft(identity, *_resolved(arguments, identity, tmp_path))

    assert refused.returncode == 2, (
        f"the drafter exited {refused.returncode}; a command line it cannot act on is a "
        f"usage failure\n{refused.stdout}\n{refused.stderr}"
    )
    assert refused.stdout == "", f"the drafter printed {refused.stdout!r} having drafted nothing"
    assert names in refused.stderr, (
        f"the refusal does not say {names!r}, so it does not name the mistake:\n{refused.stderr}"
    )
    assert not launches.exists(), "a drafting turn was spent before this command line was refused"


#: A revision expression naming a commit this checkout really holds, without naming any
#: branch. `^{commit}` is the peel every revision check accepts, so it is what tells a
#: name check apart from a resolution check.
REVISION_EXPRESSION = f"{BASE}^{{commit}}"


def test_a_revision_expression_is_refused_rather_than_read_as_a_branch(tmp_path: Path) -> None:
    """The branch argument is an exact ref name, not anything git can resolve.

    Everything below the check treats the argument as a branch name — the worktree is
    cut from it, `base..branch` is walked with it, and the composed task names it to the
    drafter — so admitting a revision expression drafts a body for whatever that
    expression peels to and attributes it to a branch nobody has. `main` exists here on
    purpose: the expression resolves, and it is still not a branch this checkout holds.

    The base is stated rather than resolved, so nothing short of the branch check can
    refuse this: a drafter that admitted the expression would cut a detached worktree at
    the commit it peels to and spend a real turn describing it.
    """
    identity = _identity(tmp_path, answers=_conforming(), register=False)
    launches = Path(identity.environment["FAKE_CODEX_ATTEMPT_LOG"])
    # The premise, measured rather than assumed: the expression really does resolve
    # through the `refs/heads/<argument>` a revision check is handed, and resolves to the
    # base's own commit — so a drafter validating it that way admits it.
    peeled = _git(
        "rev-parse", "--verify", f"refs/heads/{REVISION_EXPRESSION}", cwd=identity.checkout
    )
    assert peeled == _git("rev-parse", "--verify", BASE, cwd=identity.checkout), (
        f"{REVISION_EXPRESSION!r} no longer peels to {BASE}, so this journey no longer "
        "drives the check it was written for"
    )

    refused = _draft(
        identity, REVISION_EXPRESSION, "--repo", str(identity.checkout), "--base", BASE
    )

    assert refused.returncode == 2, (
        f"the drafter exited {refused.returncode} for a branch argument that names no "
        f"branch\n{refused.stdout}\n{refused.stderr}"
    )
    assert refused.stdout == "", f"the drafter printed {refused.stdout!r} having drafted nothing"
    assert "has no branch" in refused.stderr, (
        f"the refusal does not say the checkout has no such branch:\n{refused.stderr}"
    )
    assert not launches.exists(), "a drafting turn was spent on an argument that names no branch"


def test_a_worktree_the_checkout_cannot_cut_is_refused_rather_than_worked_around(
    tmp_path: Path,
) -> None:
    """A checkout that cannot be written to is reported, never drafted around.

    The turn has to read the branch's own tree, and cutting one writes into the
    checkout's `.git`. The tempting repair when that fails is to run the turn in
    whatever tree is already there — which is the publication checkout, on its base,
    describing a diff that is not the branch's. So this is a refusal with a reason,
    and the checkout is still left exactly as it was found.
    """
    identity = _identity(tmp_path, answers=_conforming(), register=False)
    before = _untouched(identity.checkout)
    git_directory = identity.checkout / ".git"
    writable = git_directory.stat().st_mode
    git_directory.chmod(writable & ~stat.S_IWUSR)
    try:
        refused = _draft(identity, WORK_BRANCH, "--repo", str(identity.checkout), "--base", BASE)
    finally:
        git_directory.chmod(writable)

    assert refused.returncode == 2, (
        f"the drafter exited {refused.returncode}\n{refused.stdout}\n{refused.stderr}"
    )
    assert refused.stdout == "", f"the drafter printed {refused.stdout!r} having drafted nothing"
    assert "could not be cut" in refused.stderr, (
        f"the refusal does not say the worktree could not be cut:\n{refused.stderr}"
    )
    assert _untouched(identity.checkout) == before


@pytest.mark.parametrize(
    ("sent", "expected"),
    [
        pytest.param(signal.SIGINT, 130, id="interrupted-at-the-terminal"),
        pytest.param(signal.SIGTERM, 143, id="terminated"),
        pytest.param(signal.SIGHUP, 129, id="hung-up-on"),
    ],
)
def test_a_signalled_drafter_leaves_the_checkout_as_it_found_it(
    tmp_path: Path, sent: signal.Signals, expected: int
) -> None:
    """An interrupted turn tidies up too, which is the path a plain `EXIT` trap misses.

    `set -e` runs an `EXIT` trap for a refusal and for a failed command, but an
    unhandled signal kills the shell outright and the trap never runs — leaving a
    linked worktree in a checkout this repository promises not to touch, on the exits
    an operator takes deliberately. All three are driven because each needs its own
    handler and a missing one is invisible from the others: Ctrl-C at a terminal is
    `SIGINT`, a supervisor stopping the drafter is `SIGTERM`, and a closed session is
    `SIGHUP`. The turn is held open by the provider so the signal lands on a drafter
    that is provably mid-turn rather than one that might already have finished, and the
    exit status is the signalled one so a caller can tell an interrupt from a refusal.
    """
    identity = _identity(tmp_path, answers=_conforming(), register=False)
    # Long enough that the signal lands on a turn still in flight even when this host
    # is busy, and no longer: the drafter waits out the whole hold before its trap can
    # run, so every second of it is wall-clock this journey spends three times.
    identity.environment["FAKE_CODEX_HOLD_SECONDS"] = "4"
    launches = Path(identity.environment["FAKE_CODEX_ATTEMPT_LOG"])
    before = _untouched(identity.checkout)

    drafting = subprocess.Popen(
        [str(DRAFTER), WORK_BRANCH, "--repo", str(identity.checkout), "--base", BASE],
        cwd=identity.checkout.parent,
        env=identity.environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        until = deadline(120)
        while not launches.exists():
            assert time.monotonic() < until, "the drafting turn never reached the provider"
            assert drafting.poll() is None, "the drafter exited before it spent a turn"
            time.sleep(0.05)
        drafting.send_signal(sent)
        printed, reported = drafting.communicate(timeout=e2e_timeout(120))
    finally:
        if drafting.poll() is None:  # pragma: no cover - only on a drafter that ignored it
            drafting.kill()
            # Bounded, because a killed process is not a process whose pipes are
            # closed: anything it spawned inherited them and can hold them open, and
            # an undrained `communicate()` then waits on that grandchild for as long
            # as this tier is left running. Expiry is not a failure here — this is
            # cleanup, and raising would mask whatever the journey was reporting.
            with contextlib.suppress(subprocess.TimeoutExpired):
                drafting.communicate(timeout=e2e_timeout(60))

    assert drafting.returncode == expected, (
        f"the drafter signalled with {sent.name} exited {drafting.returncode}; {expected} is "
        f"what says a caller stopped it rather than that it refused the work"
        f"\n{printed}\n{reported}"
    )
    assert printed == "", f"the drafter printed {printed!r} having drafted nothing"
    assert _untouched(identity.checkout) == before, (
        "the signalled drafter left the publication checkout changed:\n"
        f"before {before}\nafter  {_untouched(identity.checkout)}"
    )
    assert not (identity.checkout / ".git" / "worktrees").exists(), (
        "a linked-worktree record outlived the drafter a caller interrupted"
    )


def test_a_branch_carrying_nothing_its_base_does_not_is_still_drafted_from_its_diff(
    tmp_path: Path,
) -> None:
    """A branch with no commits of its own says so, rather than promising a log it lacks.

    A merged or reset branch reaches the landing verbs like any other, and the composed
    task promises the drafter "the full commit messages of `base..branch`". Handing it
    that heading with nothing under it is what makes a model fill the gap: it is told a
    record exists, sees none, and writes the `## Why` nobody wrote down. So the empty
    range is named as the state it is, and the turn still runs — the diff is a real
    record even when the log is not.
    """
    identity = _identity(tmp_path, answers=_conforming())
    _git("branch", "unchanged", BASE, cwd=identity.checkout)

    drafted = _draft(identity, "unchanged", "--repo", str(identity.checkout), "--base", BASE)

    assert drafted.returncode == 0, drafted.stderr + drafted.stdout
    assert drafted.stdout == DRAFTED_BODY
    composed = json.loads(identity.prompts.read_text(encoding="utf-8").splitlines()[0])["prompt"]
    assert "carries no commit its base does not" in composed, (
        "the composed task does not tell the drafter the branch has no commits of its "
        f"own, so it was promised a record that is not there:\n{composed}"
    )
    assert "oldest first:" not in composed, (
        f"the composed task still heads a list of commit messages it has none of:\n{composed}"
    )


def test_every_flag_carries_its_value_in_the_form_the_caller_wrote_it(tmp_path: Path) -> None:
    """`--flag value` and `--flag=value` are one command line, not two behaviours.

    Both spellings are what a caller reaches for — the second especially from a script
    composing the arguments — and each is parsed separately here, so a flag whose
    equals form dropped its value would refuse a command line that reads as correct.
    Driven as one journey through all three flags at once, because what matters is that
    the whole line still drafts the same body: a checkout that resolved, a base that
    bounded the right range, and a destination that received it.
    """
    identity = _identity(tmp_path, answers=_conforming())
    body_file = tmp_path / "equals-form.md"

    drafted = _draft(
        identity,
        WORK_BRANCH,
        f"--repo={identity.checkout}",
        f"--base={BASE}",
        f"--out={body_file}",
    )

    assert drafted.returncode == 0, drafted.stderr + drafted.stdout
    assert drafted.stdout == ""
    assert body_file.read_text(encoding="utf-8") == DRAFTED_BODY
    composed = json.loads(identity.prompts.read_text(encoding="utf-8").splitlines()[0])["prompt"]
    assert FIRST_COMMIT in composed, (
        f"the equals-form base did not bound the range the drafter was shown:\n{composed}"
    )


#: The unrelated stranded worktree a checkout is already carrying when the drafter
#: arrives, and the branch it sits on. It is *prunable* — its directory is gone and git
#: says so — which is exactly the entry a whole-checkout sweep would take with it.
STRANDED_WORKTREE = "stranded"
STRANDED_BRANCH = "someone-elses-work"


def _strand_a_worktree(identity: Identity, tmp_path: Path) -> str:
    """Leave the checkout holding a prunable worktree entry that is nobody's to reclaim.

    This is the ordinary state of a busy checkout here: a run whose clone was swept, a
    dispatch killed mid-flight, a recovery still to happen. `git worktree list` keeps
    naming it and marks it `prunable`, which is the only record left of where that work
    was — so an unrelated command that quietly reclaims it destroys the pointer.
    """
    stranded = tmp_path / STRANDED_WORKTREE
    _git("worktree", "add", "-b", STRANDED_BRANCH, str(stranded), BASE, cwd=identity.checkout)
    shutil.rmtree(stranded)
    listed = _git("worktree", "list", "--porcelain", cwd=identity.checkout)
    assert "prunable" in listed, (
        f"the seeded worktree is not prunable, so this journey proves nothing:\n{listed}"
    )
    return listed


@pytest.mark.parametrize(
    ("answers", "expected"),
    [
        pytest.param(_conforming(), 0, id="drafted"),
        pytest.param(["not the object the schema declares"], 3, id="not-drafted"),
    ],
)
def test_a_worktree_entry_the_drafter_did_not_add_survives_it(
    tmp_path: Path, answers: list[str], expected: int
) -> None:
    """Cleanup reclaims this script's own worktree and no one else's.

    `git worktree prune` would clear the drafter's entry too, and it is the obvious
    thing to reach for — but it is a whole-checkout sweep, and a publication checkout on
    this host routinely carries prunable entries belonging to stranded work somebody
    still has to recover. Reclaiming those is `just recoverable`'s business and an
    operator's decision; a drafter that did it as a side effect of writing a pull
    request description would delete the last pointer to where that work lived, with
    nothing in its output to say so. So the entry is removed by name, and what this
    holds is the whole list rather than just the seeded row: an entry added, reordered,
    or lost anywhere in it is the same defect.
    """
    identity = _identity(tmp_path, answers=answers, register=False)
    before = _strand_a_worktree(identity, tmp_path)

    drafted = _draft(identity, WORK_BRANCH, "--repo", str(identity.checkout), "--base", BASE)
    assert drafted.returncode == expected, drafted.stdout + drafted.stderr

    after = _git("worktree", "list", "--porcelain", cwd=identity.checkout)
    assert after == before, (
        "the drafter changed a worktree list it was only supposed to leave alone:\n"
        f"before {before!r}\nafter  {after!r}"
    )
    assert STRANDED_WORKTREE in after, (
        f"the drafter reclaimed a stranded worktree that was not its own:\n{after}"
    )


#: The prefix `mktemp -d` gives the drafter's scratch directory, which is how a journey
#: finds the one this run created without the script having to report it.
DRAFTER_SCRATCH = "orchestrator-draft-pr-body-*"


def _scratch_of(root: Path, prefix: str, running: subprocess.Popen[str]) -> Path:
    """The scratch directory a live run made under `root`, waited for rather than raced."""
    until = deadline(120)
    while True:
        made = sorted(root.glob(prefix))
        if made:
            return made[0]
        assert time.monotonic() < until, f"nothing matching {prefix} was created under {root}"
        assert running.poll() is None, "the run exited before it made its scratch directory"
        time.sleep(0.05)


def _held_open(scratch: Path) -> Path:
    """Put a subtree in `scratch` that `rm -rf` reports and leaves behind.

    Unlinking an entry needs write permission on the directory holding it, so a
    read-only directory with a file in it is exactly what a removal fails on. It is
    planted beside what the run writes rather than over it: making the scratch itself
    read-only would break the turn instead of its cleanup, and proving the diagnostic
    means reaching a run that otherwise succeeded.
    """
    held = scratch / "held"
    held.mkdir()
    (held / "kept").write_text("kept\n", encoding="utf-8")
    held.chmod(stat.S_IRUSR | stat.S_IXUSR)
    return held


def test_a_drafter_that_cannot_tidy_up_reports_it_and_still_answers(tmp_path: Path) -> None:
    """Cleanup that fails is said out loud, and costs the caller nothing.

    The promise is that the checkout is left as it was found, so the two ways that can
    fail — a worktree entry still registered against a checkout this repository will not
    touch again, and a scratch directory holding a change request's prose — are the two
    things somebody has to be told about. Neither is fatal: the body is what the caller
    ran the drafter for, and swallowing the failure or exiting on it are both worse than
    printing it. Both are driven together because the trap runs them in one pass, and a
    handler that gave up after the first would still tidy up nothing.

    The conditions are real ones, produced without disturbing the turn: a locked
    worktree is what `git worktree remove --force` refuses, and a read-only subtree is
    what `rm -rf` reports. Both land while the provider holds the turn open, so they are
    in place before the drafter can exit.
    """
    identity = _identity(tmp_path, answers=_conforming(), register=False)
    # Its own temporary directory, so the scratch this run makes is the only one under it.
    scratch_root = tmp_path / "tmp"
    scratch_root.mkdir()
    identity.environment["TMPDIR"] = str(scratch_root)
    # Long enough that both conditions land on a turn still in flight even when this host
    # is busy, and no longer: the drafter waits out the whole hold before its trap runs.
    identity.environment["FAKE_CODEX_HOLD_SECONDS"] = "4"
    launches = Path(identity.environment["FAKE_CODEX_ATTEMPT_LOG"])

    drafting = subprocess.Popen(
        [str(DRAFTER), WORK_BRANCH, "--repo", str(identity.checkout), "--base", BASE],
        cwd=identity.checkout.parent,
        env=identity.environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    held: Path | None = None
    worktree: Path | None = None
    try:
        until = deadline(120)
        while not launches.exists():
            assert time.monotonic() < until, "the drafting turn never reached the provider"
            assert drafting.poll() is None, "the drafter exited before it spent a turn"
            time.sleep(0.05)
        scratch = _scratch_of(scratch_root, DRAFTER_SCRATCH, drafting)
        worktree = scratch / "branch"
        _git("worktree", "lock", str(worktree), cwd=identity.checkout)
        held = _held_open(scratch)
        printed, reported = drafting.communicate(timeout=e2e_timeout(300))
    finally:
        if drafting.poll() is None:  # pragma: no cover - only on a drafter that hung
            drafting.kill()
            # Bounded, because a killed process is not a process whose pipes are
            # closed: anything it spawned inherited them and can hold them open, and
            # an undrained `communicate()` then waits on that grandchild for as long
            # as this tier is left running. Expiry is not a failure here — this is
            # cleanup, and raising would mask whatever the journey was reporting.
            with contextlib.suppress(subprocess.TimeoutExpired):
                drafting.communicate(timeout=e2e_timeout(60))
        if held is not None:
            held.chmod(stat.S_IRWXU)
        if worktree is not None:
            subprocess.run(
                ["git", "worktree", "unlock", str(worktree)],
                cwd=identity.checkout,
                capture_output=True,
                check=False,
            )
            _git("worktree", "prune", cwd=identity.checkout)
        shutil.rmtree(scratch_root, ignore_errors=True)

    assert drafting.returncode == 0, (
        f"the drafter exited {drafting.returncode} over cleanup it only had to report"
        f"\n{printed}\n{reported}"
    )
    assert printed == DRAFTED_BODY, (
        f"the drafter printed {printed!r}; a cleanup it could not finish must not change "
        f"the body, and the turn answered {DRAFTED_BODY!r}\n{reported}"
    )
    assert (
        f"the worktree at {worktree} could not be removed from {identity.checkout}" in reported
    ), f"the worktree left registered against the checkout was never reported:\n{reported}"
    assert f"the scratch directory {scratch} could not be removed" in reported, (
        f"the scratch directory holding the drafted prose was never reported:\n{reported}"
    )


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]
