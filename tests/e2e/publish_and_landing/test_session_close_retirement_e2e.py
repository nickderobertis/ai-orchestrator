"""A node's session close keeps its open change request's branch over another's landing.

Every dispatched lifecycle node's session close runs the retirement pass over the branch
it published, in the `onevcs` the engine links — `config/onepipeline.version` decides
which. Branch-keyed verbs record into one `publish-branch-<branch>` stream per branch
*name*, which every repository on a host publishing that name shares, and before onevcs
0.49.0 nothing in it said which repository an event was written for: a close took the
newest landing across it as its own branch's, retired the branch it had just published,
and deleted it from its origin, closing its open change request unmerged. One retirement
pass on this host did that to 18 branches in a day.

So this launches one `change-open` node on alpha, whose branch name beta — another
identity on the same host — also published, and whose shared stream already carries the
landing of beta's merged change request #1. The node publishes and opens alpha's own #1;
its session close runs the linked retirement. The branch must stand on its origin at the
head the change request was opened from, the change request must still be open, beta's
change request must never have been asked about on alpha's behalf, and nothing may have
been closed or deleted. Measured against onepipeline 0.70.0, whose linked onevcs is
0.48.1, this journey fails: that close deletes the branch from alpha's origin on beta's
landing.

What is doubled is the boundary each engine is proven at in its own repository and nothing
above it: the paid model, by `tests/e2e/fake_backend.py` and `tests/e2e/fake_codex.py`,
with the worker's one commit made by real git where the dispatch runs; and the remote
host's decisioning, by `tests/e2e/fake_gh.py` through `ONEVCS_GH`, answering each
repository's change requests separately. The recipe, the driver, the linked `onevcs`, the
push and the origin are real. The one record no installed verb writes any more — the
unlabelled landing in the shared stream — is written as the build before 0.49.0 wrote it,
because it is the input the fix is about.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] One launch of a one-node
run, about thirty seconds, placed beside the other launch journeys of this project — the
publication journeys in `test_redispatch_names_the_published_tip_e2e.py` and
`test_human_readable_branch_e2e.py` — whose Nx key covers the recipes, scripts and configs
it drives; re-homing launch journeys into a project of their own is a change of its own.
llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] Same site: the module
declares no marker-based tier, and the project it sits in is pre-existing.
llmlint: ignore-file[shell_test_tiers_stay_split] A pytest journey over the real `just`
recipe rather than a shell suite, placed beside every other launch journey here.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple, cast

import fake_gh
import pytest
import short_state
from fake_backend import PROMPT_LOG_ENV, RUN_ON_MARKER_ENV
from harness_indirections import established_indirections
from project_fixtures import helper, project_from_plan
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The paid model's stand-ins, and the guard covering the identities neither reaches.
FAKE_BACKEND = helper("fake_backend.py")
FAKE_CODEX = helper("fake_codex.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")
INDIRECTION_CALLER = "tests/e2e/publish_and_landing/test_session_close_retirement_e2e.py"

#: The repository the node works on, and the other one sharing its branch name.
ALPHA = "acme-corp/alpha"
BETA = "acme-corp/beta"
BASE = "main"
#: The registered alias of the checkout a session clones from.
EXECUTION_ALIAS = "execution"
#: A reviewed path for every identity: the change request is opened and kept for review.
RULES = (
    "version: 3\n"
    "trailer_prefix: Orchestrator-\n"
    "rules: []\n"
    "default:\n"
    "  publication: change-open\n"
    "  approvals: required\n"
)
#: The plan's name, which is also the run's.
type RunName = str
#: A node's id within its plan.
type NodeId = str
#: A branch's name, which the two repositories share.
type BranchName = str

#: The scratch host's branch prefix, so the branch the node is cut is known before the
#: launch: `<prefix><plan name>/<node id>`, as `test_human_readable_branch_e2e.py` holds.
BRANCHES = "version: 1\nprefix: scratch-host/\n"
RUN_NAME: RunName = "session-close-retirement"
NODE_ID: NodeId = "shared"
BRANCH: BranchName = f"scratch-host/{RUN_NAME}/{NODE_ID}"
#: The two environment layers that would beat the host file and the default template.
OVERRIDES = ("ONEVCS_BRANCH_PREFIX", "ONEPIPELINE_BRANCH_TEMPLATE")

#: What the node's task carries so the stand-in worker knows it is the one to commit.
TASK_MARKER = "Record alpha's work in alpha.txt, and commit it."
COMMIT_COMMANDS = [
    [
        "sh",
        "-c",
        '[ -f alpha.txt ] || { echo "alpha\'s work" > alpha.txt && git add alpha.txt '
        "&& git -c user.email=test@example.com -c user.name=ai-orchestrator-test "
        "commit -qm 'feat: record alpha'\"'\"'s work'; }",
    ]
]

LAUNCHER_ENVIRONMENT = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)
#: Who the commits `onevcs` makes on a session's behalf are by.
AUTHOR = {
    "GIT_AUTHOR_NAME": "ai-orchestrator-test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "ai-orchestrator-test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}
GIT_IDENTITY = ("-c", "user.email=test@example.com", "-c", "user.name=ai-orchestrator-test")


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


class Repository(NamedTuple):
    """One repository of the substituted host: its bare origin and the two clones of it."""

    #: The bare origin every push really reaches.
    origin: Path
    #: The checkout a node's `repo` names.
    publication: Path
    #: The checkout a session clones from, named by alias.
    execution: Path


def _repository(root: Path, name: str, execution: str) -> Repository:
    """A bare origin and two clones of it.

    A checkout's registered alias is its directory's name, so the execution clone is the
    directory a plan names by alias.
    """
    seed = root / f"{name}-seed"
    _git("init", "-q", "-b", BASE, str(seed), cwd=root)
    (seed / "README.md").write_text("seed\n", encoding="utf-8")
    _git("add", "-A", cwd=seed)
    _git(*GIT_IDENTITY, "commit", "-qm", "feat: seed", cwd=seed)
    origin = root / f"{name}.git"
    _git("clone", "-q", "--bare", str(seed), str(origin), cwd=root)
    publication = root / name
    _git("clone", "-q", str(origin), str(publication), cwd=root)
    _git("clone", "-q", str(origin), str(root / execution), cwd=root)
    return Repository(origin, publication, root / execution)


def _register(checkout: Path, slug: str, environment: dict[str, str]) -> None:
    registered = subprocess.run(
        ["just", "register-repo", str(checkout), "--origin", f"https://github.com/{slug}.git"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert registered.returncode == 0, registered.stdout + registered.stderr


def _beta_landing(home: Path, url: str, commit: str) -> None:
    """The shared stream's landing of beta's change request, as a build before 0.49.0 wrote it.

    No installed verb writes an event carrying no `labels.identity` any more, which is why
    it is written here: it is the record the fix is about.
    """
    token = "publish-branch-" + BRANCH.replace("/", "-")
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    event = {
        "v": 1,
        "ts": now,
        "stream": token,
        "seq": 1,
        "source": "vcs",
        "kind": "change-merged",
        "phase": "review",
        "labels": {"session": token},
        "payload": {"url": url, "sha": commit, "landed_at": now, "landing": commit},
        "artifacts": [],
    }
    streams = home / "streams"
    streams.mkdir(parents=True, exist_ok=True)
    # llmlint: ignore[tests_mirror_real_usage] No installed verb writes an unlabelled shared stream any more; the docstring above says why it is written here.  # noqa: E501 - a directive is one line
    (streams / f"{token}.ndjson").write_text(json.dumps(event) + "\n", encoding="utf-8")


def _plan(root: Path, publication: Path) -> Path:
    plan = root / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "goal": {"text": "Publish alpha's work under a branch name beta shares"},
                "name": RUN_NAME,
                "tasks": [
                    {
                        "id": NODE_ID,
                        "title": "feat: record alpha's work",
                        "repo": str(publication),
                        "execution_checkout": EXECUTION_ALIAS,
                        "persona": "engineer",
                        "task": (
                            f"## What\n\n{TASK_MARKER}\n\n"
                            "## Why\n\nThe retirement its session close runs is the subject.\n\n"
                            "## Acceptance criteria\n\n- alpha.txt is committed.\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return plan


class Journey(NamedTuple):
    """What one launch left: the host's state and calls, the origin, and the run's record."""

    launch: subprocess.CompletedProcess[str]
    origin: Path
    gh_state: Path
    calls: list[list[str]]
    journal: list[dict[str, object]]


@pytest.fixture(scope="module")
def journey(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Iterator[Journey]:
    """Launch the one node for real over a shared stream carrying beta's landing."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    root = tmp_path_factory.mktemp("session-close-retirement")
    home = root / "onevcs"
    home.mkdir()
    (home / "rules.yml").write_text(RULES, encoding="utf-8")
    (home / "branches.yml").write_text(BRANCHES, encoding="utf-8")
    gh_state = root / "gh-state"
    gh_state.mkdir()
    gh_calls = root / "gh-calls.jsonl"
    alpha = _repository(root, "alpha", EXECUTION_ALIAS)
    beta = _repository(root, "beta", "beta-execution")

    environment = dict(os.environ)
    for name in (*LAUNCHER_ENVIRONMENT, *OVERRIDES):
        environment.pop(name, None)
    environment["CLAUDE_CODE_SESSION_ID"] = "e2e-session-close-retirement"
    environment.update(AUTHOR)
    environment["ONEVCS_HOME"] = str(home)
    environment["ONEPIPELINE_RUNS_DIR"] = str(root / "runs")
    environment["XDG_STATE_HOME"] = str(short_state.state_home(root))
    # llmlint: ignore[e2e_not_mocked] Only the remote host's decisioning, at onevcs's seam.
    environment["ONEVCS_GH"] = str(fake_gh.__file__)
    environment["FAKE_GH_STATE"] = str(gh_state)
    environment["FAKE_GH_CALLS"] = str(gh_calls)
    environment["FAKE_GH_ORIGINS"] = json.dumps({ALPHA: str(alpha.origin), BETA: str(beta.origin)})
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment.update(established_indirections(INDIRECTION_CALLER))
    environment[PROMPT_LOG_ENV] = str(root / "turns.jsonl")
    commands = root / "worker-commands.json"
    commands.write_text(json.dumps({TASK_MARKER: COMMIT_COMMANDS}), encoding="utf-8")
    environment[RUN_ON_MARKER_ENV] = str(commands)
    for checkout, slug in (
        (alpha.publication, ALPHA),
        (alpha.execution, ALPHA),
        (beta.publication, BETA),
    ):
        _register(checkout, slug, environment)

    # Beta's #1, merged long ago at a commit beta's base carries, recorded as the landing
    # of the branch name alpha's node is about to publish.
    (beta.publication / "old.txt").write_text("beta's old work\n", encoding="utf-8")
    _git("add", "-A", cwd=beta.publication)
    _git(*GIT_IDENTITY, "commit", "-qm", "feat: beta's old work (#1)", cwd=beta.publication)
    _git("push", "-q", "origin", BASE, cwd=beta.publication)
    merged = _git("rev-parse", "HEAD", cwd=beta.publication)
    fake_gh.hold(gh_state, BETA, number=1, head=BRANCH, head_sha=merged)
    fake_gh.merge(gh_state, BETA, number=1, commit=merged)
    _beta_landing(home, f"https://github.com/{BETA}/pull/1", merged)

    launch = subprocess.run(
        ["just", "orchestrate", project_from_plan(_plan(root, alpha.publication), RUN_NAME)],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(600),
        check=False,
    )
    try:
        journal_path = root / "runs" / RUN_NAME / "events.jsonl"
        assert journal_path.is_file(), f"no run was recorded:\n{launch.stdout}\n{launch.stderr}"
        # `onepipeline` writes this file; its schema is the engine's, and the cast says so.
        journal = [
            cast(dict[str, object], json.loads(line))
            for line in journal_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        calls = [
            json.loads(line)
            for line in (
                gh_calls.read_text(encoding="utf-8") if gh_calls.exists() else ""
            ).splitlines()
            if line.strip()
        ]
        yield Journey(launch, alpha.origin, gh_state, calls, journal)
    finally:
        subprocess.run(
            ["just", "stop", RUN_NAME],
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            timeout=e2e_timeout(60),
            check=False,
        )


def _why(journey: Journey) -> str:
    kinds = [str(event.get("kind")) for event in journey.journal]
    return f"{journey.launch.stdout}\n{journey.launch.stderr}\njournal: {kinds}"


@pytest.mark.xdist_group("session-close-retirement")
def test_the_node_published_alphas_own_change_request_and_its_session_closed(
    journey: Journey,
) -> None:
    """The premise: the node opened alpha's #1 under the shared name, and its session closed."""
    assert journey.launch.returncode == 0, _why(journey)
    opened = json.loads(
        (fake_gh.records(journey.gh_state, ALPHA) / "pr-1.json").read_text(encoding="utf-8")
    )
    assert opened["head"] == BRANCH, opened
    closed = [event for event in journey.journal if event.get("kind") == "session-closed"]
    assert closed, f"no session of the node closed, so no retirement ran:\n{_why(journey)}"


@pytest.mark.xdist_group("session-close-retirement")
def test_the_close_keeps_the_branch_its_own_open_change_request_was_opened_from(
    journey: Journey,
) -> None:
    """Beta's landing retires nothing of alpha's: the branch and its change request stand."""
    tip = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{BRANCH}"],
        cwd=journey.origin,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    ).stdout.strip()
    assert tip, f"the session close deleted {BRANCH} from alpha's origin:\n{_why(journey)}"
    held = json.loads(
        (fake_gh.records(journey.gh_state, ALPHA) / "pr-1.json").read_text(encoding="utf-8")
    )
    assert (held["state"], held["headRefOid"]) == ("OPEN", tip), (
        f"alpha's change request is not open from the head its branch stands at: {held}"
    )
    closing = [
        call
        for call in journey.calls
        if call[:2] in (["pr", "close"], ["pr", "merge"])
        or (call[:1] == ["api"] and any(word in ("DELETE", "--method=DELETE") for word in call))
    ]
    assert not closing, f"the host was asked to close, merge or delete: {closing}"
    # Nothing of beta's was taken up on alpha's behalf: beta's merged #1 was never read.
    beta_reads = [call for call in journey.calls if call[:2] == ["pr", "view"] and BETA in call]
    assert not beta_reads, f"alpha's close asked about beta's change request: {beta_reads}"
