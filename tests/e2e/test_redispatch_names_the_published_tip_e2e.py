"""A publication its host's checks refuse is re-dispatched told where its branch stands.

A worker once fixed both refusals it was handed by amending the two commits its branch had
already pushed, and the publishing push was refused `non-fast-forward` with the correct
tree stranded on the host (https://github.com/nickderobertis/ai-orchestrator/issues/1282).
The engine half of the fix — https://github.com/nickderobertis/onepipeline/pull/569 — names,
in the task a `checks-failed` re-dispatch is handed, the commit the branch stands at on its
remote, and the rule that follows: the repair goes on as new commits on top of it, never as
an amend, a rebase, a squash or a force-push. `config/onepipeline.version` is what puts that
in force here, so this drives it through the adopted engine the way a run on this host meets
it: the real `just orchestrate`, the real `scripts/onepipeline.sh`, the real driver and its
linked `onevcs`, a real publication pushing a real branch into a real origin, and the
re-dispatched worker's own prompt read back.

What is doubled is the boundary each engine is proven at in its own repository and nothing
above it. The paid model is `tests/e2e/fake_backend.py` for the conversation's two sides and
`tests/e2e/fake_codex.py` for the single-sided members that reach a provider binary; the
worker's commit is the one decision of the model's substituted, by
`FAKE_BACKEND_RUN_ON_MARKER`, and it is made by real git where the dispatch runs. The remote
host's decisioning — the change request, and the required check it reports red — is the
`gh` stand-in `onevcs` proves its own publications against, read at the tag
`config/onevcs.version` names out of the checkout `config/onevcs.checkouts` registers for
it, and reached through `ONEVCS_GH`, the variable `onevcs` publishes for exactly this. So the
host answers every call the linked `onevcs` makes in the shapes that release reads, and a
host double written here could not drift from them.

**Which commit the engine can name depends on who committed the work**, and both are
driven. The engine takes the pushed commit from the `commit-preserved` record `onevcs`
writes when it commits a session's worktree at close, so a worker that leaves its change
for the session to commit is re-dispatched told that commit by hash. A worker that commits
its own work — which every dispatch here is told to do — leaves nothing for `onevcs` to
commit, and the adopted engine then says the branch was published at a commit it does not
know, handing it the same rule against whatever commit the remote holds. The second is
held as the adopted release's answer rather than as a goal: an engine that learns to name
that commit fails it here, and the prose that describes it moves with it.

The identity is a scratch one — a bare origin and two clones of it, registered against a
scratch `ONEVCS_HOME` under a GitHub origin so it has a host at all — because a test may
not register or publish from this host's own checkouts.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] The host double is read
out of a registered checkout of `onevcs`, which lives outside this workspace and so
outside every `nx.json` key: the uncached `orchestrator:test-checkouts` tier exists for
exactly that, selected by `reads_checkouts`, and a memo on any narrower edge would replay a
green across the `onevcs` release it must re-read. Its two launches take about thirty
seconds between them.

llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] `reads_checkouts` is the
marker that routes this module to that tier, and a second Nx project would need its own
key over the same checkout outside the workspace.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple, cast

import pytest
import short_state
from fake_backend import JUDGE_CONFIG_NAME, MEMBER_OF_CONFIG, PROMPT_LOG_ENV, RUN_ON_MARKER_ENV
from harness_indirections import established_indirections
from project_fixtures import project_from_plan
from published_tools import PUBLISHED_TOOLS
from registered_checkouts import listed_checkout_paths
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_checkouts

#: The paid model's stand-ins, and the guard covering the identities neither reaches.
FAKE_BACKEND = Path(__file__).resolve().parent / "fake_backend.py"
FAKE_CODEX = Path(__file__).resolve().parent / "fake_codex.py"
PAID_PROVIDER_GUARD = Path(__file__).resolve().parent / "no-paid-provider"
INDIRECTION_CALLER = "tests/e2e/test_redispatch_names_the_published_tip_e2e.py"

#: The `gh` stand-in `onevcs` proves its own publications against, as that repository
#: tracks it, and the variable naming the directory it keeps its host's state in.
HOST_DOUBLE = "crates/onevcs/tests/fixtures/gh"
HOST_STATE_ENV = "ONEVCS_FAKE_GH_STATE"
#: The one required check the host reports, concluded red: `name|status|conclusion|required`,
#: the row format that stand-in reads.
RED_REQUIRED_CHECK = "gate|completed|failure|true\n"

#: The origin the identity is resolved from. A GitHub URL, because a change request is a
#: host's object and `onevcs` has a host only for a hosted identity; the remote git pushes to
#: is the throwaway bare origin on disk.
HOSTED_ORIGIN = "https://github.com/acme-corp/redispatch.git"
#: The registered alias of the checkout a session clones from.
EXECUTION_ALIAS = "execution"
BASE = "main"
RULES = (
    "version: 3\n"
    "trailer_prefix: Orchestrator-\n"
    "rules: []\n"
    "default:\n"
    "  publication: change-auto\n"
    "  approvals: none\n"
)

RUN_NAME = "redispatch-published-tip"
NODE_ID = "service"
#: What the node's task carries so the stand-in worker knows it is the one to change.
TASK_MARKER = "Record one more line of work in progress.txt."
#: The file each attempt appends to, and the subject a worker that commits uses.
WORK_FILE = "progress.txt"
WORK_SUBJECT = "feat: record the work"
#: The two ways a worker leaves its work, and so the two things the engine can know.
LEAVES_IT_UNCOMMITTED = "leaves-it-uncommitted"
COMMITS_IT = "commits-it"
#: Two attempts: the first is refused `checks-failed` and the second is the re-dispatch.
ATTEMPTS = "2"

#: The words the re-dispatch is held to, quoted from the engine's own journey over the
#: same diagnosis so a paraphrase cannot pass here while the task says something else.
PUBLISHED_AT = "This branch is published on its remote at `{commit}`."
ON_TOP = (
    "The repair goes on as new commits on top of `{commit}` — never as an amend, a rebase, "
    "a squash or a force-push of commits already on the remote"
)
#: What the engine says when a publication pushed the branch and nothing recorded at which
#: commit, quoted the same way.
PUBLISHED_UNKNOWN = (
    "This branch was published to its remote, but the commit it stands at there is not known. "
    "The repair goes on as new commits on top of whatever commit the remote holds — never as an "
    "amend, a rebase, a squash or a force-push of commits already on the remote"
)

LAUNCHER_ENVIRONMENT = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)
GIT_IDENTITY = ("-c", "user.email=test@example.com", "-c", "user.name=ai-orchestrator-test")
#: Who the commits `onevcs` makes on a session's behalf are by, so no host git identity is
#: needed: the driver it commits in is started before the suite's per-test identity is.
AUTHOR = {
    "GIT_AUTHOR_NAME": "ai-orchestrator-test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "ai-orchestrator-test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def _git(*arguments: str, cwd: Path) -> str:
    done = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert done.returncode == 0, f"git {' '.join(arguments)}: {done.stderr or done.stdout}"
    return done.stdout.strip()


def _host_double(destination: Path) -> Path:
    """`onevcs`'s own `gh` stand-in at the pinned release, written out as a program.

    A failure rather than a skip when no checkout has it, for the reason
    `tests/test_engine_contracts.py` gives: a journey that answers "no checkout, nothing
    to say" passes while proving nothing.
    """
    pin = next(tool for tool in PUBLISHED_TOOLS if tool.version_file == "onevcs.version")
    tag = f"v{pin.adopted_version}"
    for checkout in listed_checkout_paths():
        if checkout.name.split("__")[-1] != "onevcs" or not (checkout / ".git").exists():
            continue
        shown = subprocess.run(
            ["git", "-C", str(checkout), "show", f"{tag}:{HOST_DOUBLE}"],
            text=True,
            capture_output=True,
            timeout=e2e_timeout(60),
            check=False,
        )
        assert shown.returncode == 0, (
            f"the onevcs checkout at {checkout} cannot show {HOST_DOUBLE} at {tag}: "
            f"{shown.stderr.strip()}. Fetch that tag"
        )
        destination.write_text(shown.stdout, encoding="utf-8")
        destination.chmod(0o755)
        return destination
    raise AssertionError(
        "no registered checkout of onevcs on this host, so the host double its publications "
        "are proven against cannot be read. Clone it into one of the paths "
        "`config/onevcs.checkouts` names"
    )


class World(NamedTuple):
    """A scratch hosted identity whose required check is red, and what watched it."""

    root: Path
    origin: Path
    publication: Path
    host_state: Path
    environment: dict[str, str]


def _world(root: Path, oneharness_bin: str, worker: str) -> World:
    home = root / "onevcs"
    home.mkdir()
    (home / "rules.yml").write_text(RULES, encoding="utf-8")
    seed = root / "seed"
    _git("init", "-q", "-b", BASE, str(seed), cwd=root)
    (seed / WORK_FILE).write_text("seed\n", encoding="utf-8")
    _git("add", "-A", cwd=seed)
    _git(*GIT_IDENTITY, "commit", "-qm", "feat: seed", cwd=seed)
    origin = root / "origin.git"
    _git("clone", "-q", "--bare", str(seed), str(origin), cwd=root)
    publication = root / "publication"
    execution = root / EXECUTION_ALIAS
    _git("clone", "-q", str(origin), str(publication), cwd=root)
    _git("clone", "-q", str(origin), str(execution), cwd=root)

    # The host double is scripted through these state files and nothing else: they are
    # the surface onevcs's own suite drives it by (`World::host_checks`,
    # `World::host_notices_the_push_after`), as `MOCK_STDOUT` scripts the stand-in model.
    # Scripting the remote host's verdict there is the substitution itself, at onevcs's
    # `ONEVCS_GH` seam; the push, the change request, the watch and the re-dispatch above
    # it are all real.
    # llmlint: ignore-block[e2e_not_mocked, tests_mirror_real_usage] see above
    host_state = root / "gh-state"
    host_state.mkdir()
    (host_state / "origin").write_text(str(origin), encoding="utf-8")
    (host_state / "checks.rows").write_text(RED_REQUIRED_CHECK, encoding="utf-8")
    # A host that has processed every push by the time it is asked: left unsaid, the
    # stand-in goes on reporting the head the change request was opened at for ever, and
    # the re-dispatch's publication would watch for a head the host never reports.
    (host_state / "head-noticed-after").write_text("0", encoding="utf-8")
    # llmlint: ignore-end[e2e_not_mocked, tests_mirror_real_usage]

    environment = dict(os.environ)
    for name in LAUNCHER_ENVIRONMENT:
        environment.pop(name, None)
    environment["CLAUDE_CODE_SESSION_ID"] = f"e2e-redispatch-published-tip-{worker}"
    environment.update(AUTHOR)
    environment["ONEVCS_HOME"] = str(home)
    environment["ONEPIPELINE_RUNS_DIR"] = str(root / "runs")
    environment["ONEPIPELINE_PUBLICATION_ATTEMPTS"] = ATTEMPTS
    environment["XDG_STATE_HOME"] = str(short_state.state_home(root))
    # llmlint: ignore[e2e_not_mocked] Only the remote host's decisioning, at onevcs's seam.
    environment["ONEVCS_GH"] = str(_host_double(root / "gh"))
    environment[HOST_STATE_ENV] = str(host_state)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment.update(established_indirections(INDIRECTION_CALLER))
    environment[PROMPT_LOG_ENV] = str(root / "turns.jsonl")
    # The one decision of the model's substituted: the worker writes one more line, and
    # either commits it or leaves it for the session to commit.
    changes = [["sh", "-c", f"echo 'one more line' >> {WORK_FILE}"]]
    if worker == COMMITS_IT:
        changes += [["git", "add", "-A"], ["git", *GIT_IDENTITY, "commit", "-qm", WORK_SUBJECT]]
    commands = root / "worker-commands.json"
    commands.write_text(json.dumps({TASK_MARKER: changes}), encoding="utf-8")
    environment[RUN_ON_MARKER_ENV] = str(commands)

    for checkout in (publication, execution):
        registered = subprocess.run(
            ["just", "register-repo", str(checkout), "--origin", HOSTED_ORIGIN],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(120),
            check=False,
        )
        assert registered.returncode == 0, registered.stdout + registered.stderr
    return World(root, origin, publication, host_state, environment)


def _plan(world: World) -> Path:
    plan = world.root / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "goal": {"text": "Publish a change its host's required check refuses"},
                "name": RUN_NAME,
                "tasks": [
                    {
                        "id": NODE_ID,
                        "title": "feat: record the work",
                        "repo": str(world.publication),
                        "execution_checkout": EXECUTION_ALIAS,
                        "persona": "engineer",
                        "task": (
                            f"## What\n\n{TASK_MARKER}\n\n"
                            "## Why\n\nThe re-dispatch a refusal earns is the subject.\n\n"
                            "## Acceptance criteria\n\n- progress.txt carries one more line.\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return plan


class Journey(NamedTuple):
    worker: str
    world: World
    launch: subprocess.CompletedProcess[str]
    journal: list[dict[str, object]]
    #: Every task a worker of this node was handed, in the order the turns ran.
    worker_tasks: list[str]


@pytest.fixture(scope="module", params=[LEAVES_IT_UNCOMMITTED, COMMITS_IT])
def journey(
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
    oneharness_bin: str,
) -> Iterator[Journey]:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    worker = cast(str, request.param)
    world = _world(tmp_path_factory.mktemp(f"redispatch-{worker}"), oneharness_bin, worker)
    launch = subprocess.run(
        ["just", "orchestrate", project_from_plan(_plan(world), RUN_NAME)],
        cwd=REPO_ROOT,
        env=world.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(600),
        check=False,
    )
    try:
        runs = world.root / "runs"
        journals = sorted(runs.glob("*/events.jsonl"))
        assert journals, f"the launch recorded no run:\n{launch.stdout}\n{launch.stderr}"
        # `onepipeline` writes this file; its schema is the engine's, and the cast says so.
        journal = [
            cast(dict[str, object], json.loads(line))
            for line in journals[0].read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        prompts = Path(world.environment[PROMPT_LOG_ENV])
        turns = [
            json.loads(line)
            for line in (
                prompts.read_text(encoding="utf-8") if prompts.exists() else ""
            ).splitlines()
            if line.strip()
        ]
        worker_tasks = []
        for turn in turns:
            config = turn.get("config") or ""
            member = MEMBER_OF_CONFIG.search(config)
            if member is None or member.group(1) != "worker":
                continue
            if Path(config).name == JUDGE_CONFIG_NAME or TASK_MARKER not in turn["prompt"]:
                continue
            worker_tasks.append(turn["prompt"])
        yield Journey(worker, world, launch, journal, worker_tasks)
    finally:
        subprocess.run(
            ["just", "stop", RUN_NAME],
            cwd=REPO_ROOT,
            env=world.environment,
            capture_output=True,
            timeout=e2e_timeout(60),
            check=False,
        )


def _events(journey: Journey, kind: str) -> list[dict[str, object]]:
    return [event for event in journey.journal if event.get("kind") == kind]


def _why(journey: Journey) -> str:
    kinds = [str(event.get("kind")) for event in journey.journal]
    return f"{journey.launch.stdout}\n{journey.launch.stderr}\njournal: {kinds}"


@pytest.mark.xdist_group("redispatch-published-tip")
def test_the_first_publication_is_refused_by_the_red_check_and_re_dispatched(
    journey: Journey,
) -> None:
    """The run reached the re-dispatch this journey is about, for the reason it names."""
    dispatched = _events(journey, "node-dispatched")
    reasons = [
        str(cast(dict[str, object], event.get("payload", {})).get("reason", ""))
        for event in dispatched
    ]
    assert len(dispatched) == 2 and reasons[1].startswith("checks-failed:"), (
        f"the node was not dispatched, refused checks-failed, and re-dispatched: {reasons}\n"
        f"{_why(journey)}"
    )
    assert len(journey.worker_tasks) == 2, (
        f"the worker was handed {len(journey.worker_tasks)} task(s), not the first and the "
        f"re-dispatch\n{_why(journey)}"
    )


class Published(NamedTuple):
    """The branch the first publication pushed, and the commit the host recorded it at."""

    branch: str
    commit: str


def _published(journey: Journey) -> Published:
    """The branch, and the commit the host recorded it at when the first change opened.

    Read off the host rather than off the engine's own record: the change request the
    first publication opened records the head the origin held when it was opened, which is
    what "published on its remote" has to mean to a worker about to push.
    """
    opened = (journey.world.host_state / "pr-1.env").read_text(encoding="utf-8")
    fields = dict(line.split("=", 1) for line in opened.splitlines() if "=" in line)
    return Published(branch=fields["PR_HEAD"], commit=fields["PR_HEAD_SHA"])


@pytest.mark.xdist_group("redispatch-published-tip")
def test_the_redispatch_is_told_where_its_branch_stands_and_to_grow_it(journey: Journey) -> None:
    """The re-dispatched task says where the remote holds the branch, and to build on it.

    Where `onevcs` committed the worker's change, the engine knows that commit and names it
    by hash; where the worker committed it, the adopted engine says the commit is not known.
    Both carry the rule, and the first attempt was told neither.
    """
    branch, pushed = _published(journey)
    first, redispatched = journey.worker_tasks[0], journey.worker_tasks[1]
    if journey.worker == LEAVES_IT_UNCOMMITTED:
        told = PUBLISHED_AT.format(commit=pushed)
        assert told in redispatched, (
            f"the re-dispatch was not told its branch is published at {pushed}:\n{redispatched}"
        )
        assert ON_TOP.format(commit=pushed) in redispatched, (
            f"the re-dispatch was not told to grow {pushed} with new commits:\n{redispatched}"
        )
    else:
        told = PUBLISHED_UNKNOWN
        assert told in redispatched, (
            f"the re-dispatch was not told its branch was published:\n{redispatched}"
        )
        assert pushed not in redispatched, redispatched
    assert told not in first, first

    # And the worker that followed it did: the branch's first published commit is still in
    # its history on the origin, under the repair that went on top of it.
    tip = _git("rev-parse", f"refs/heads/{branch}", cwd=journey.world.origin)
    assert tip != pushed, "the re-dispatch pushed nothing on top of the published commit"
    _git("merge-base", "--is-ancestor", pushed, tip, cwd=journey.world.origin)
