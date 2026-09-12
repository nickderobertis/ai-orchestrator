"""A retry pinned to a preserved branch reaches the work its predecessor stranded.

This is the journey the adopted `onepipeline` pin exists for. A run that stops after
committing leaves its work on a branch and its session open; the retry arrives
carrying the same pin, and whether it can reach that work is decided where
`onepipeline` asks its linked `onevcs` to open the session. Below `onevcs` 0.4.2 the
answer was to cut a second session on the same name, which is refused because the
branch is ahead of its base — so every retry of a stranded node failed before it
dispatched, and four nodes across three runs each needed an out-of-band `just
publish-branch` to recover.

**The pin is the whole of the fix, which is why the journey belongs here rather than
only upstream.** `onepipeline`'s `Cargo.toml` declares `onevcs = "0.4.1"` byte-identically
in v0.7.1 and v0.7.2; only `Cargo.lock` moved. Nothing in this repository's own source
changes when that resolution does, so a check that read this repository alone could not
tell the two apart — the difference is only observable by running a plan through the
binary and watching what it does to a stranded branch. That is what these two journeys
do, against the two releases, so a pin that regressed below the fix fails here by name
instead of stranding the next run's retries.

The contrast is what makes each half mean something. Both legs build the *same*
stranded state and launch the *same* plan; the only variable is the binary. The failing
leg is held to the sibling's own sentence quoted to the commit count, because a leg
asserting on "failed" alone would pass against every other reason a dispatch can die.
The passing leg is held to the work itself reaching `main`, not merely to a run that
did not error: reuse is the point, and a run that quietly cut a fresh session would
settle just as green while leaving the stranded commit exactly where it was.

Everything between the recipe and the model is real: the real `just orchestrate` and
`just repos-apply` recipes, the real `scripts/onepipeline.sh`, the real `onepipeline`
driver and its linked `onevcs`, the real registry and its worktrees, and — on the
adopted leg — the real `oneagentgraph` graphs in `graphs/`.
`tests/e2e/fake_backend.py` stands in for the paid model alone.

The stranded state is built with the released `onevcs` command line, the
same way the sibling's own suite builds it, so these journeys start from state that
tool really writes rather than a shape this file believes it writes.

The prior leg attaches **no** observer or drafting graph, and that is forced rather
than chosen: those documents name `personas/` by path, and since oneagentgraph 0.3.0
a persona is a onejudge config fragment that the 0.2.x release linked below it
refuses outright. Given them, the prior binary never reaches a dispatch and settles
no node at all, which is a finding about a persona shape rather than about a
stranded branch. Their absence cannot move this verdict either way: an observer
graph watches and a drafting graph writes a change request's body, and neither is
consulted where `onepipeline` asks `onevcs` to open a session on a branch that is
ahead of its base — which is the one thing both legs are read for.

The identity is a scratch one — a bare origin and two clones of it, registered against a
scratch `ONEVCS_HOME` — because a test may not register or publish from this host's own
checkouts.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple, NewType, TypedDict, cast

import pytest
from conftest import git
from fake_backend import PROMPT_LOG_ENV
from project_fixtures import project_from_plan
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The stand-in for the paid model, named to `oneagentgraph` as its harness.
FAKE_BACKEND = Path(__file__).resolve().parent / "fake_backend.py"

#: The release whose lockfile still resolves the pre-fix `onevcs`, and so the one that
#: still refuses the retry. Named here rather than derived: it is the *last* release
#: without the fix, a fixed historical fact, and nothing about a future bump changes it.
PRIOR_RELEASE = "0.7.1"

#: The branch a stopped run is made to have left its work on.
STRANDED_BRANCH = "feature/stranded"

#: The subject of the commit that branch carries, and the file it adds. The file is what
#: proves the retry reached the *work* rather than merely the branch name.
STRANDED_SUBJECT = "feat: work a stopped run left behind"
STRANDED_FILE = "stranded.txt"

#: The sibling's own refusal, quoted to the point where it counts the commits.
#: A journey matching "failed" or even "invalid input" alone would pass against every
#: other reason a dispatch can die before it starts.
ALREADY_CARRIES = f'branch "{STRANDED_BRANCH}" already carries 1 commit(s) that main does not'

#: The registered alias of the checkout a session clones from. `onevcs` takes an alias
#: here and refuses a path, so the plan names the alias.
EXECUTION_ALIAS = "execution"

#: A launching session the journey states rather than inherits: this suite runs inside a
#: dispatch whose own harness session would otherwise own the runs it launches.
LAUNCHING_SESSION = "e2e-session-reuse-retry"

#: Every name a launcher identity reaches `scripts/onepipeline.sh` through, so a journey
#: that states one is not also carrying the enclosing dispatch's.
LAUNCHER_ENVIRONMENT = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)

#: The scratch identity's policy: merged in the local checkout, naming no verifier —
#: onevcs 0.11.0 removed the concept, and the scratch repository has no `pre-push` hook
#: for the merge path to find. Publication is not this journey's subject — what is under
#: test is which session a dispatch runs in — but a registered checkout matching no rule
#: fails `just repos-apply` outright.
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

#: The committer this journey's own seed and stranded commits carry. `tests/conftest.py`
#: exports one per test, and the fixtures below are module-scoped, so they are set up
#: before that function-scoped fixture has run.
GIT_IDENTITY = ("-c", "user.email=test@example.com", "-c", "user.name=ai-orchestrator-test")


#: A session's identity, which is the whole of what tells reuse from a fresh cut.
#: Distinguished from an ordinary string because comparing it to one is the assertion
#: these journeys turn on, and a plain `str` would let any other identifier stand in.
SessionToken = NewType("SessionToken", str)


class NodeSettled(TypedDict, total=False):
    """A plan node's terminal verdict, in the fields these journeys read.

    `status` and `outcome` are on every record; `detail` carries the reason a failure
    settled the way it did, and is where the sibling's refusal arrives. Declared
    `total=False` because a run that landed states none of the failure fields and a run
    that was refused states none of the landing ones.
    """

    status: str
    outcome: str
    detail: str
    branch: str
    landing: str


class JournalEvent(TypedDict):
    """One journal record, in the terms these journeys read it.

    `onepipeline` pins the whole record contract and every event carries more than
    this; these are the fields the question needs, stated rather than restated from
    the engine's schema.
    """

    kind: str
    source: str
    labels: dict[str, str]
    payload: dict[str, str]


class Stranded(NamedTuple):
    """A world holding one branch of unpublished work, and nothing driving it."""

    #: The scratch root every path below sits under.
    root: Path
    #: The scratch `ONEVCS_HOME` the registry, the worktrees, and the leases live in.
    home: Path
    #: The bare repository the identity resolves to.
    origin: Path
    #: The registered publication checkout the plan names as its `repo`.
    publication: Path
    #: The session token the abandoned session was opened under. A retry that reuses
    #: the session reports this one; a retry that cut a fresh one reports another.
    token: SessionToken


class Settled(NamedTuple):
    """One settled run: what the launch reported, and every event it appended."""

    returncode: int
    output: str
    journal: list[JournalEvent]


def _seed_identity(root: Path) -> tuple[Path, Path]:
    """A bare origin with one commit on `main`, and the two clones of it to register."""
    origin = root / "origin.git"
    seed = root / "seed"
    git("init", "-q", "--bare", "-b", "main", str(origin))
    git("init", "-q", "-b", "main", str(seed))
    (seed / "README.md").write_text("seed\n", encoding="utf-8")
    git("add", "-A", cwd=seed)
    git(*GIT_IDENTITY, "commit", "-qm", "chore: seed", cwd=seed)
    git("remote", "add", "origin", str(origin), cwd=seed)
    git("push", "-q", "origin", "main", cwd=seed)
    publication = root / "publication"
    execution = root / EXECUTION_ALIAS
    git("clone", "-q", str(origin), str(publication))
    git("clone", "-q", str(origin), str(execution))
    return publication, execution


def _register(root: Path, home: Path, checkouts: tuple[Path, ...]) -> None:
    """Bring a scratch registry up to a scratch configuration, through the real recipe."""
    manifest = root / "onevcs.checkouts"
    manifest.write_text("".join(f"{path}\n" for path in checkouts), encoding="utf-8")
    rules = root / "onevcs.rules.yml"
    rules.write_text(RULES, encoding="utf-8")
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


def _strand(root: Path, home: Path) -> SessionToken:
    """Leave one branch of committed, unpublished work behind a session nobody holds.

    Built with the released `onevcs` command line rather than by arranging git by hand,
    so the registry, the per-run clone, the worktree, and the session record are the
    ones that tool really writes. The process opening the session exits immediately,
    which is what makes the session *stale* rather than live — a launch meeting a live
    one is refused by a different rule, and this journey is about what happens after.
    """
    opened = subprocess.run(
        ["uv", "run", "onevcs", "session", "open", EXECUTION_ALIAS, "--branch", STRANDED_BRANCH],
        cwd=REPO_ROOT,
        env={**os.environ, "ONEVCS_HOME": str(home)},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert opened.returncode == 0, f"onevcs session open failed:\n{opened.stdout}\n{opened.stderr}"
    session = json.loads(opened.stdout.strip())
    worktree = Path(session["worktree"])
    (worktree / STRANDED_FILE).write_text("stranded work\n", encoding="utf-8")
    git("add", "-A", cwd=worktree)
    git(*GIT_IDENTITY, "commit", "-qm", STRANDED_SUBJECT, cwd=worktree)
    # The precondition every assertion below rests on: the branch is ahead of its base
    # by exactly the one commit the refusal counts. Stated here so a setup that stopped
    # producing it fails as a broken world rather than as a passing journey.
    ahead = git("log", "--format=%s", f"origin/main..{STRANDED_BRANCH}", cwd=worktree).split("\n")
    assert [line for line in ahead if line] == [STRANDED_SUBJECT], (
        f"the stranded branch carries {ahead}, not the one commit these journeys are about"
    )
    return SessionToken(session["token"])


def _plan(world: Stranded, name: str) -> Path:
    """A one-node lifecycle plan pinned to the stranded branch.

    The pin is the whole shape under test: it is what a retry of a stranded node
    carries, and what sends the engine to open a session on a branch that already has
    work on it.
    """
    plan = world.root / f"{name}.plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "goal": {"text": "Reach the work a stopped run stranded on its branch"},
                "name": name,
                "tasks": [
                    {
                        "id": "retry",
                        "repo": str(world.publication),
                        "execution_checkout": EXECUTION_ALIAS,
                        "branch": STRANDED_BRANCH,
                        "persona": "engineer",
                        "task": (
                            "## What\nReport the directory you are in, changing nothing.\n\n"
                            "## Why\nThe session this dispatch runs in is the subject; "
                            "the work itself is not.\n\n"
                            "## Acceptance criteria\n- The dispatch starts and reports.\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return plan


def _environment(world: Stranded, oneharness_bin: str) -> dict[str, str]:
    """The environment both legs launch under, so the binary stays the only variable."""
    environment = dict(os.environ)
    for name in LAUNCHER_ENVIRONMENT:
        environment.pop(name, None)
    environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    # Every root a run writes under, so its registry, its worktrees, its ledger, and its
    # graph scratch are all this journey's and none of them the host's.
    environment["ONEVCS_HOME"] = str(world.home)
    environment["ONEPIPELINE_RUNS_DIR"] = str(world.root / "runs")
    environment["XDG_STATE_HOME"] = str(world.root / "state")
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    environment[PROMPT_LOG_ENV] = str(world.root / "turns.jsonl")
    return environment


def _settled(world: Stranded, name: str, command: list[str], oneharness_bin: str) -> Settled:
    """Run one launch to settlement and read the journal it appended."""
    launch = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=_environment(world, oneharness_bin),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(400),
        check=False,
    )
    journal = world.root / "runs" / name / "events.jsonl"
    assert journal.is_file(), (
        f"the launch recorded no journal at {journal}:\n{launch.stdout}\n{launch.stderr}"
    )
    return Settled(
        returncode=launch.returncode,
        output=f"{launch.stdout}\n{launch.stderr}",
        # `onepipeline` writes this file; its schema is stated above rather than
        # validated here, and the cast says which.
        journal=[
            cast(JournalEvent, json.loads(line))
            for line in journal.read_text(encoding="utf-8").splitlines()
        ],
    )


def _world(tmp_path_factory: pytest.TempPathFactory, label: str) -> Stranded:
    """One scratch identity holding one stranded branch."""
    root = tmp_path_factory.mktemp(label)
    home = root / "onevcs"
    home.mkdir()
    publication, execution = _seed_identity(root)
    _register(root, home, (publication, execution))
    return Stranded(
        root=root,
        home=home,
        origin=root / "origin.git",
        publication=publication,
        token=_strand(root, home),
    )


def _node_settled(settled: Settled) -> list[NodeSettled]:
    """Every terminal verdict the run recorded for a plan node."""
    return [
        cast(NodeSettled, event["payload"])
        for event in settled.journal
        if event.get("kind") == "node-settled"
    ]


def _sessions(settled: Settled) -> set[SessionToken]:
    """Every session token the run recorded working in."""
    return {
        SessionToken(event["payload"]["token"])
        for event in settled.journal
        if event.get("kind") == "session-opened" and "token" in event.get("payload", {})
    }


class Journey(NamedTuple):
    """One release's answer to the stranded retry, and the world it answered in."""

    world: Stranded
    settled: Settled


@pytest.fixture(scope="module")
def adopted(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Journey:
    """The adopted release, launched through the real recipe.

    `just orchestrate` rather than the binary directly, because the adopted leg is the
    one that has to hold for this repository's own command surface: the recipe, the
    wrapper it execs, and the graphs it attaches are all part of what a retry here goes
    through.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    world = _world(tmp_path_factory, "session-reuse-adopted")
    name = "session-reuse-adopted"
    return Journey(
        world=world,
        settled=_settled(
            world,
            name,
            ["just", "orchestrate", project_from_plan(_plan(world, name))],
            oneharness_bin,
        ),
    )


@pytest.fixture(scope="module")
def prior_binary(tmp_path_factory: pytest.TempPathFactory) -> str:
    """The last release whose lockfile resolves the pre-fix `onevcs`, from PyPI.

    Installed into a throwaway environment rather than the project's, which stays on the
    adopted pin: the contrast needs both binaries at once, and the adopted one is what
    every other journey in this suite runs against.
    """
    if shutil.which("uv") is None:
        pytest.skip("uv is not installed")
    environment = tmp_path_factory.mktemp("onepipeline-prior")
    created = subprocess.run(
        ["uv", "venv", str(environment / "venv")],
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        check=False,
    )
    assert created.returncode == 0, f"uv venv failed:\n{created.stdout}\n{created.stderr}"
    installed = subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(environment / "venv" / "bin" / "python"),
            f"onepipeline-cli=={PRIOR_RELEASE}",
        ],
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
        check=False,
    )
    assert installed.returncode == 0, (
        f"onepipeline-cli=={PRIOR_RELEASE} did not install:\n{installed.stdout}\n{installed.stderr}"
    )
    binary = environment / "venv" / "bin" / "onepipeline"
    reported = subprocess.run(
        [str(binary), "--version"],
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    # The whole contrast rests on which release this is, so it is read from the binary
    # rather than assumed from the specifier that asked for it.
    assert reported.stdout.strip() == f"onepipeline {PRIOR_RELEASE}", (
        f"the prior-release environment reports {reported.stdout.strip()!r}, "
        f"not onepipeline {PRIOR_RELEASE}"
    )
    return str(binary)


@pytest.fixture(scope="module")
def prior(
    tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str, prior_binary: str
) -> Journey:
    """The same stranded retry, on the release below the fix.

    Driven at the binary rather than through `just orchestrate`, which execs the
    `onepipeline` this checkout pins and so cannot be pointed at another release. It is
    given neither of the graphs `just orchestrate` attaches, because this checkout's
    are written in a persona shape that release refuses; the module header states why
    that cannot move the verdict.
    Everything else — the plan, the stranded state, the registry, the identity — is the
    adopted leg's.
    """
    world = _world(tmp_path_factory, "session-reuse-prior")
    # llmlint: ignore[expensive_tests_stay_behind_their_own_edge] one file rewrite, not a new launch
    _as_the_prior_release_reads(world.home)
    name = "session-reuse-prior"
    return Journey(
        world=world,
        settled=_settled(
            world,
            name,
            [prior_binary, "start", str(_plan(world, name))],
            oneharness_bin,
        ),
    )


#: The registry schema the prior release's linked `onevcs` reads, and the two identity
#: fields that schema carried: `register` inferred both from whether the origin had a
#: host, and this identity's origin is a path.
PRIOR_REGISTRY_VERSION = 5
PRIOR_INFERRED_FIELDS = {"workflow": "local", "repo_type": "single-owner"}


# llmlint: ignore-block[tests_mirror_real_usage] No public interface on this host writes
# this document. The prior release's `onevcs` is a library linked into `prior_binary`,
# which exposes no `register`, and the one `onevcs` CLI installed here writes version 6 —
# so the registry that release reads can only be put back by hand, which is the same edit
# `AGENTS.md` names as the operator's stop-gap for a consumer stranded below 0.21.0.
def _as_the_prior_release_reads(home: Path) -> None:
    """Rewrite the scratch registry into the schema the prior release can read.

    The world is built with the installed `onevcs`, which since 0.21.0 writes registry
    version 6 — the version-5 document minus the two inferred identity fields — and a
    release below that refuses it outright: `declares version 6; this build reads 2 to
    5`. The prior leg is read for what its engine does with a stranded branch, and a
    refusal of the registry would fail it before that question was asked, so the
    document is put back into the shape its own `register` would have written. Only the
    registry: the session record and the worktree the stranding left are read by both.
    """
    registry = home / "registry.json"
    document = json.loads(registry.read_text(encoding="utf-8"))
    document["version"] = PRIOR_REGISTRY_VERSION
    for identity in document["identities"].values():
        identity.update(PRIOR_INFERRED_FIELDS)
    registry.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


# llmlint: ignore-end[tests_mirror_real_usage]


@pytest.mark.xdist_group("session-reuse-retry")
def test_the_adopted_release_takes_up_the_stranded_session(adopted: Journey) -> None:
    """The retry runs in the session the stopped run left, rather than cutting a new one.

    This is the fix itself. A release below it cuts a second session on the same branch
    name and is refused; a release carrying it reports the *same* token, which is the
    only thing that distinguishes reuse from a fresh cut that happened to work.
    """
    worked_in = _sessions(adopted.settled)
    assert worked_in == {adopted.world.token}, (
        f"the retry worked in sessions {sorted(worked_in)}, not in the stranded session "
        f"{adopted.world.token} the stopped run left:\n{adopted.settled.output}"
    )


@pytest.mark.xdist_group("session-reuse-retry")
def test_the_adopted_release_lands_the_work_that_was_stranded(adopted: Journey) -> None:
    """The commit the stopped run left reaches `main`, which is the point of reaching it.

    A run that settled green while cutting a fresh session would leave this file exactly
    where it was, so this is what says the retry reached the *work* and not merely the
    branch name. `main` in the bare origin is read rather than a checkout's, because that
    is what every other clone of the identity would fetch.
    """
    settled = _node_settled(adopted.settled)
    assert [record.get("status") for record in settled] == ["done"], (
        f"the pinned node settled {settled}, not done:\n{adopted.settled.output}"
    )
    landed = git("ls-tree", "--name-only", "main", cwd=adopted.world.origin).split()
    assert STRANDED_FILE in landed, (
        f"main carries {landed}, so the stranded work never landed:\n{adopted.settled.output}"
    )


@pytest.mark.xdist_group("session-reuse-retry")
def test_the_release_below_the_fix_refuses_the_same_retry(prior: Journey) -> None:
    """The prior release fails the same pinned retry, by the sibling's own sentence.

    Quoted to the commit count on purpose: a leg asserting on `failed` alone would pass
    against a harness that never started, a gate that refused, or any other way a
    dispatch can die, and would go on passing if the refusal this journey is about were
    replaced by an unrelated one.
    """
    settled = _node_settled(prior.settled)
    assert [record.get("status") for record in settled] == ["failed"], (
        f"the pinned node settled {settled} on onepipeline {PRIOR_RELEASE}, not failed:"
        f"\n{prior.settled.output}"
    )
    assert ALREADY_CARRIES in settled[0].get("detail", ""), (
        f"onepipeline {PRIOR_RELEASE} refused the retry with {settled[0].get('detail')!r}, "
        f"which is not the stranded-branch refusal this pin was moved for"
    )


@pytest.mark.xdist_group("session-reuse-retry")
def test_the_release_below_the_fix_leaves_the_work_stranded(prior: Journey) -> None:
    """Nothing lands, which is what made each of those nodes an out-of-band recovery.

    The counterpart to the adopted leg's landing assertion. Without it the refusal above
    is only a message; this is the consequence that cost the operator four hand
    recoveries, and it is what a regression would reintroduce.
    """
    landed = git("ls-tree", "--name-only", "main", cwd=prior.world.origin).split()
    assert STRANDED_FILE not in landed, (
        f"main carries {landed} on onepipeline {PRIOR_RELEASE}, so this leg is no longer "
        f"the stranded case the adopted release is contrasted against"
    )
