"""Approving, gating, copying and locating a plan read that one plan, never the whole board.

`just approve-design`, the launch gate's design-approval assessment, `just copy-plan` and
`just finish-plan`'s plan-location step each used to list every project of the `plans`
board to find the one plan they were about — a listing that pages the board's search and,
past GitHub's 1,000-result cap, does not even see every project. These journeys drive the
real recipes and the locked `.venv/bin/onetaskgraph` against the loopback board double,
with the plan's copy filed beside other projects, and read which store calls each recipe
made through the store-call audit: the plan's own record is read once by id, the board's
project listing is never walked to find it, and what each recipe answers is unchanged.

The plan-location step's check for a second copy lists only the projects copied from the
plan, with `project list --origin` once per source it looks in, which the board answers by a
query narrowed to that origin; it runs on a board at the `plans` board's scale and is held
to that listing, and to reading no whole-board page, by the double's own request log.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

import pytest
from github_board import (
    AUTHORING_ROOT_ENV,
    AUTHORING_SOURCE,
    BOARD,
    PLANS_BOARD_SCALE,
    _audit_follow_up_calls,
    _board_cost,
    _BoardItemId,
    _GitHubFixture,
    _GraphQLRequest,
    _Issue,
    _IssueNodeId,
    _serving_board,
    populate,
)
from project_fixtures import designed, graph_image, reviewed
from test_board_copy_design_approval_e2e import (
    CLOSING,
    _copied,
    _drafted_in_a_public_source,
    _location,
    _on_the_board,
    _recipe,
    _rendering_environment,
    _through_the_template,
)
from waits import timeout as e2e_timeout

from orchestrator import design_approval, plan_store
from orchestrator.root import REPO_ROOT

#: The plan these journeys copy: ten tasks rendered from this host's `plan-task` template,
#: the second depending on the first so its design document opens with the plan's graph.
PROJECT = "board-reads"
QUALIFIED = f"{AUTHORING_SOURCE}:{PROJECT}"
TASKS = 10
#: The board source the plan is copied onto, as every plan of this repository is.
BOARD_SOURCE = "plans"
#: A local stand-in for the source the board's route names, so the plan-location step's
#: look into the routed source reaches a directory rather than the production workspace.
ROUTED_STAND_IN = "teamstandin"
#: The variable declaring that stand-in's visibility, which nothing declares by default.
ROUTED_VISIBILITY_ENV = f"ONETASKGRAPH_SOURCES__{ROUTED_STAND_IN.upper()}__VISIBILITY"
#: How many projects an operator filed on the board itself sit beside the plan's copy, on
#: top of the one the double always holds.
OTHER_PROJECTS = 4
#: The first of those, by the qualified id the board names it under.
OTHER_PLAN = f"{BOARD_SOURCE}:I_other_plan_0"
#: A board project nothing holds, as a link to a copy since deleted names.
DANGLING = f"{BOARD_SOURCE}:I_deleted_copy"
#: A link kept under the board naming a project of the source its route names instead.
ELSEWHERE = f"{ROUTED_STAND_IN}:board-reads"
#: A link kept under the board that is no qualified id at all.
MALFORMED = "I_unqualified_copy"


class _Node(NamedTuple):
    """One of the plan's tasks: its node id and the title it is filed under."""

    node: str
    title: str


NODES = [_Node(f"step-{index:02d}", f"feat: deliver step {index:02d}") for index in range(TASKS)]


def _authored(tmp_path: Path, root: Path, environment: Mapping[str, str]) -> dict[str, str]:
    """Author :data:`PROJECT` as a planner does: its record, then each task through the template.

    Answers each created task's qualified id by its title.
    """
    (root / "projects").mkdir(parents=True, exist_ok=True)
    # llmlint: ignore[tests_mirror_real_usage] The plan store has no project-create verb, and `personas/planner.yaml` has a planner author a plan as one `projects/<project>.md` record under the source's root before creating its tasks through the store, so this writes it exactly as a planner does.  # noqa: E501
    (root / "projects" / f"{PROJECT}.md").write_text(
        f'---\ntitle: "{PROJECT}"\nstatus: "todo"\nmetadata:\n'
        '  "onepipeline.schema_version": 3\n'
        '  "onepipeline.goal": {"text": "Deliver every step"}\n---\n\n'
        f"Execution plan {PROJECT}.\n",
        encoding="utf-8",
    )
    created: dict[str, str] = {}
    for node, title in NODES:
        answered = tmp_path / f"{node}.json"
        answered.write_text(
            json.dumps(
                {
                    "what": f"Deliver {node} and the test that drives it.",
                    "why": "An operator cannot see past the first screen of nodes.",
                    "acceptance_criteria": [
                        f"The route serves {node} and rejects an invalid request.",
                        "A request-level test drives the route end to end and covers both.",
                        CLOSING,
                    ],
                }
            ),
            encoding="utf-8",
        )
        depends = [f"--depends-on={created[NODES[0].title]}"] if node == NODES[1].node else []
        made = _through_the_template(
            environment,
            "create",
            AUTHORING_SOURCE,
            "--template-loader",
            "-",
            "--no-interactive",
            "--project",
            PROJECT,
            "--title",
            title,
            "--answers",
            str(answered),
            f"--metadata=onepipeline.id={json.dumps(node)}",
            '--metadata=onepipeline.persona="engineer"',
            '--metadata=orchestrator.unit="Listing"',
            *depends,
        )
        assert made.returncode == 0, made.stdout + made.stderr
        created[title] = made.stdout.strip()
    return created


def _drafted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Author, design and review the plan where it was drafted, leaving it unapproved there.

    Answers the environment its store, the engine and the recipes run in. The routed
    source the board names is a local stand-in under ``tmp_path``.
    """
    root = tmp_path / "authoring"
    root.mkdir()
    # `reviewed` and the in-process reads read the store in this process's environment.
    monkeypatch.setenv(AUTHORING_ROOT_ENV, str(root))
    environment = _rendering_environment(root)
    routed = tmp_path / ROUTED_STAND_IN
    routed.mkdir()
    # llmlint: ignore[e2e_not_mocked] A `local-md` source stands in for the routed source the board's route names, because no check may reach the production Linear workspace and the double serves none; `tests/plan_tooling/plan_flow/test_linear_routed_plans_e2e.py` stands it in the same way, and `tests/test_plan_source_roots.py` holds the tracked route.  # noqa: E501
    stand_in = {
        f"ONETASKGRAPH_SOURCES__{ROUTED_STAND_IN.upper()}__PLUGIN": "local-md",
        f"ONETASKGRAPH_SOURCES__{ROUTED_STAND_IN.upper()}__CONFIG__ROOT": str(routed),
        "ONETASKGRAPH_SOURCES__PLANS__ROUTES__0__TO": ROUTED_STAND_IN,
        "ONETASKGRAPH_SOURCES__PLANS__ROUTES__0__REPOSITORIES": "github.com/example-private/*",
    }
    environment.update(stand_in)
    for name, value in stand_in.items():
        monkeypatch.setenv(name, value)
    tasks = _authored(tmp_path, root, environment)
    rows = [
        {
            "task": title,
            "delivers": f"the {node} route",
            "depends_on": NODES[0].title if node == NODES[1].node else "none",
            "location": _location(environment, tasks[title]) if index == 0 else f"step {index}",
        }
        for index, (node, title) in enumerate(NODES)
    ]
    designed(AUTHORING_SOURCE, PROJECT, rows, environment, **graph_image(tmp_path / "graph"))
    reviewed(QUALIFIED)
    return environment


def _beside_other_projects() -> None:
    """File :data:`OTHER_PROJECTS` plans an operator wrote on the board, each with a task.

    An issue is a project on the board when it has a sub-issue, so each is a parent and
    its one child, as the plan the double always holds is.
    """
    for index in range(OTHER_PROJECTS):
        parent = _Issue(
            item_id=_BoardItemId(f"PVTI_other_plan_{index}"),
            content_id=_IssueNodeId(f"I_other_plan_{index}"),
            number=900 + 2 * index,
            title=f"Another plan {index}",
            body="A plan somebody else filed on the board.",
            sub_issues=1,
        )
        child = _Issue(
            item_id=_BoardItemId(f"PVTI_other_task_{index}"),
            content_id=_IssueNodeId(f"I_other_task_{index}"),
            number=901 + 2 * index,
            title=f"Another task {index}",
            body="Its one sub-issue.",
            parent_id=parent.content_id,
        )
        for issue in (parent, child):
            issue.board = BOARD
            BOARD.issues.append(issue)


class _Audited(NamedTuple):
    """One recipe run under the store-call audit: what it answered and what it asked."""

    ran: subprocess.CompletedProcess[str]
    #: Each store invocation the recipe's Python made, as its argv after the binary.
    calls: list[list[str]]
    #: Every request the board double received while the recipe ran.
    requests: list[_GraphQLRequest]


def _audited(tmp_path: Path, environment: Mapping[str, str], *command: str) -> _Audited:
    """Run ``command`` from this checkout with every store call its Python makes recorded."""
    audit = tmp_path / f"audit-{len(list(tmp_path.glob('audit-*')))}"
    audit.mkdir()
    audited = dict(environment)
    trace = _audit_follow_up_calls(audit, audited)
    _GitHubFixture.requests.clear()
    ran = subprocess.run(
        list(command),
        cwd=REPO_ROOT,
        env=audited,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(600),
        check=False,
    )
    requests = list(_GitHubFixture.requests)
    lines = trace.read_text(encoding="utf-8").splitlines() if trace.exists() else []
    calls = [
        list(entry["argv"][1:])
        for entry in (json.loads(line) for line in lines)
        if entry["kind"] == "store"
    ]
    return _Audited(ran, calls, requests)


def _verb(call: list[str]) -> tuple[str, ...]:
    """The store verb ``call`` ran, without its arguments."""
    return tuple(call[:2])


def _sources(call: list[str]) -> list[str]:
    """Every source ``call`` names with `--source`."""
    return [value for flag, value in zip(call, call[1:], strict=False) if flag == "--source"]


def _listings(calls: list[list[str]]) -> list[list[str]]:
    """The `project list` calls among ``calls``."""
    return [call for call in calls if _verb(call) == ("project", "list")]


def _origins(call: list[str]) -> list[str]:
    """Every origin ``call`` names with `--origin`."""
    return [value for flag, value in zip(call, call[1:], strict=False) if flag == "--origin"]


def _narrowed_to_the_plan(located: _Audited) -> None:
    """Every project listing ``located`` made names the plan's origin, and no request paged.

    A listing without `--origin` is one the board answers by paging every project it holds,
    and a board walk or an unnarrowed search is a whole-board page whatever asked for it.
    """
    for call in _listings(located.calls):
        assert _origins(call) == [QUALIFIED], located.calls
    assert _board_cost(located.requests) == ([], []), _board_cost(located.requests)


def _shown(calls: list[list[str]], project: str) -> int:
    """How many times ``calls`` read ``project``'s record with `project show`."""
    return sum(1 for call in calls if _verb(call) == ("project", "show") and project in call)


def _copied_onto_the_board(environment: Mapping[str, str], *extra: str) -> str:
    """Copy the plan onto the board through the real recipe, and answer its board project."""
    copied = _recipe(environment, "copy-plan", QUALIFIED, "--to", BOARD_SOURCE, *extra)
    assert copied.returncode == 0, copied.stdout + copied.stderr
    return _copied(copied.stdout)[QUALIFIED]


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] `board-copy-approval` is
# this directory's own leaf project, keyed on `boardCopyApprovalWorkspace`, the edge this rule
# asks for; that key names the recipes, scripts and package these journeys drive, so narrowing
# it would memoize a verdict over a tree never run.
# llmlint: ignore-block[e2e_not_mocked] GitHub's Projects API is the one boundary doubled, for
# the reason the block around `_Board` gives — driving it writes to the live board this
# repository plans on — and the paid model `reviewed` scripts is the other; the recipes, the
# pinned engine and plan store, and the records they write are all real.
def test_approving_a_board_copy_reads_its_one_record_and_lists_no_board_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`just approve-design` on a board copy reads that copy's record once, by id.

    The board holds other plans beside the copy, so a listing that walked the board to find
    this one would be seen in the audit; the approval is recorded on the board copy as it
    always was.
    """
    environment = _drafted(tmp_path, monkeypatch)
    with _serving_board() as remote:
        on_the_board = {**environment, **remote}
        _beside_other_projects()
        populate(BOARD, PLANS_BOARD_SCALE)
        board_project = _copied_onto_the_board(on_the_board)

        approved = _audited(tmp_path, on_the_board, "just", "approve-design", board_project)
        _on_the_board(monkeypatch, remote)
        recorded = design_approval.recorded(design_approval.design_document(board_project))

    assert approved.ran.returncode == 0, approved.ran.stdout + approved.ran.stderr
    assert "recorded the approval of" in approved.ran.stdout, approved.ran.stdout
    assert recorded is not None
    board_listings = [call for call in _listings(approved.calls) if BOARD_SOURCE in _sources(call)]
    assert board_listings == [], approved.calls
    assert _shown(approved.calls, board_project) == 1, approved.calls


def test_the_launch_gate_reads_the_plans_one_record_and_still_refuses_a_changed_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate's design-approval assessment reads the plan's record once, and decides as before.

    An approved board copy is admitted; once its design document changes on the board after
    the approval, the same plan is refused, naming it. Neither read lists the board.
    """
    environment = _drafted(tmp_path, monkeypatch)
    with _serving_board() as remote:
        on_the_board = {**environment, **remote}
        _beside_other_projects()
        populate(BOARD, PLANS_BOARD_SCALE)
        board_project = _copied_onto_the_board(on_the_board)
        approved = _recipe(on_the_board, "approve-design", board_project)
        assert approved.returncode == 0, approved.stdout + approved.stderr

        admitted = _audited(
            tmp_path, on_the_board, "uv", "run", "orchestrator-launch-gate", board_project
        )
        # The design document regenerated where it was drafted with one answer changed, and
        # the plan copied up again: the board copy now says something nobody approved.
        regenerated = subprocess.run(
            [
                "bash",
                "-c",
                "set -euo pipefail; onepipeline template resolve design-doc --json | "
                'onetaskgraph document render "$1" --template-loader - --no-interactive '
                '--var "why=The approved reasons no longer hold."',
                "regenerate",
                f"{QUALIFIED}-design",
            ],
            cwd=REPO_ROOT,
            env=on_the_board,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(120),
            check=False,
        )
        assert regenerated.returncode == 0, regenerated.stdout + regenerated.stderr
        assert _copied_onto_the_board(on_the_board) == board_project
        refused = _audited(
            tmp_path, on_the_board, "uv", "run", "orchestrator-launch-gate", board_project
        )

    assert admitted.ran.returncode == 0, admitted.ran.stdout + admitted.ran.stderr
    assert refused.ran.returncode == 1, refused.ran.stdout + refused.ran.stderr
    said = " ".join(refused.ran.stderr.split())
    assert f"{board_project}'s design document" in said, said
    assert "carries no approval for what it currently says" in said, said
    for gated in (admitted, refused):
        board_listings = [call for call in _listings(gated.calls) if BOARD_SOURCE in _sources(call)]
        assert board_listings == [], gated.calls
        assert _shown(gated.calls, board_project) == 1, gated.calls


def test_copying_the_plan_reads_each_local_record_once_and_copies_it_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`just copy-plan` reads the plan's record, its tasks and its documents once each.

    Its pre-flight and the copy that follows share what was read, and the copy reports
    every record of the plan landing on the board as it always did.
    """
    environment = _drafted(tmp_path, monkeypatch)
    with _serving_board() as remote:
        on_the_board = {**environment, **remote}
        _beside_other_projects()
        populate(BOARD, PLANS_BOARD_SCALE)
        copied = _audited(
            tmp_path, on_the_board, "just", "copy-plan", QUALIFIED, "--to", BOARD_SOURCE
        )

    assert copied.ran.returncode == 0, copied.ran.stdout + copied.ran.stderr
    landed = _copied(copied.ran.stdout)
    assert landed[QUALIFIED].startswith(f"{BOARD_SOURCE}:"), landed
    assert landed[f"{AUTHORING_SOURCE}:{PROJECT}-design"].startswith(f"{BOARD_SOURCE}:"), landed
    assert len(landed) == 1 + TASKS + 1, landed
    copying = [_verb(call) for call in copied.calls].index(("project", "copy"))
    before = copied.calls[:copying]
    assert _shown(before, QUALIFIED) == 1, before
    assert _shown(copied.calls, QUALIFIED) == 1, copied.calls
    listed = [
        _verb(call)
        for call in copied.calls
        if _verb(call) in {("task", "list"), ("document", "list")}
    ]
    assert sorted(listed) == [("document", "list"), ("task", "list")], copied.calls
    walked = [call[2] for call in before if _verb(call) == ("task", "deps")]
    assert len(walked) == TASKS and len(set(walked)) == TASKS, before
    assert _listings(copied.calls) == [], copied.calls


def test_recopy_preserves_board_workflow_and_creates_new_tasks_from_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Extending a copied plan retains a settlement and a person's project status."""
    environment = _drafted(tmp_path, monkeypatch)
    with _serving_board() as remote:
        environment.update(remote)
        first = _recipe(environment, "copy-plan", QUALIFIED, "--to", BOARD_SOURCE)
        assert first.returncode == 0, first.stdout + first.stderr
        destinations = _copied(first.stdout)
        settled = next(issue for issue in BOARD.issues if issue.title == NODES[0].title)
        finished = _through_the_template(
            environment, "update", f"plans:{settled.content_id}", "--status", "done"
        )
        assert finished.returncode == 0, finished.stdout + finished.stderr
        project = next(
            issue
            for issue in BOARD.issues
            if destinations[QUALIFIED] == f"plans:{issue.content_id}"
        )
        changed = _audited(
            tmp_path,
            environment,
            str(REPO_ROOT / ".venv/bin/onetaskgraph"),
            "project",
            "status",
            "set",
            destinations[QUALIFIED],
            "in-progress",
        )
        assert changed.ran.returncode == 0, changed.ran.stdout + changed.ran.stderr
        added = _through_the_template(
            environment,
            "create",
            AUTHORING_SOURCE,
            "--project",
            PROJECT,
            "--title",
            "feat: extend the copied plan",
            "--template-loader",
            "-",
            "--answers",
            str(tmp_path / "step-00.json"),
            '--metadata=onepipeline.id="extension"',
            '--metadata=onepipeline.persona="engineer"',
            '--metadata=orchestrator.unit="Listing"',
        )
        assert added.returncode == 0, added.stdout + added.stderr
        reviewed(QUALIFIED)
        copied = _audited(
            tmp_path, environment, "just", "copy-plan", QUALIFIED, "--to", BOARD_SOURCE
        )
        assert copied.ran.returncode == 0, copied.ran.stdout + copied.ran.stderr
        assert settled.status == "Done"
        assert settled.state == "CLOSED"
        assert settled.state_reason == "COMPLETED"
        assert project.status == "In Progress"
        extension = next(
            issue for issue in BOARD.issues if issue.title == "feat: extend the copied plan"
        )
        assert extension.status == "Todo"
        assert extension.state == "OPEN"
        copy_call = next(call for call in copied.calls if _verb(call) == ("project", "copy"))
        assert copy_call[copy_call.index("--status-policy") + 1] == "preserve-destination"


def _located(tmp_path: Path, environment: Mapping[str, str]) -> _Audited:
    """`scripts/finish-plan.sh`'s plan-location step for the plan, as that script runs it."""
    return _audited(
        tmp_path,
        environment,
        "uv",
        "run",
        "orchestrator-plan-locations",
        QUALIFIED,
        "--in",
        BOARD_SOURCE,
    )


def test_the_plan_location_step_finds_the_copy_by_its_link_and_lists_only_for_a_second(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a real copy, the step names the board copy as the home, read by the store's link.

    On a board at the `plans` board's scale, the plan's own record is read for the link, the
    copy's record is read by id, and the only project listings are the two-copy check's,
    one for the board and one for the source its route names, each narrowed to the plan's
    origin, so no request pages the board.
    """
    environment = _drafted(tmp_path, monkeypatch)
    with _serving_board() as remote:
        on_the_board = {**environment, **remote}
        _beside_other_projects()
        populate(BOARD, PLANS_BOARD_SCALE)
        board_project = _copied_onto_the_board(on_the_board)
        located = _located(tmp_path, on_the_board)
        copy = next(issue for issue in BOARD.issues if board_project == f"plans:{issue.content_id}")
        assert copy.origin is None, "a private source's copy unexpectedly carried an origin"
        # llmlint: ignore[tests_mirror_real_usage] The origin is a board field a person can edit in GitHub; changing the double's field exercises that external edit through the real plan-location command and store read, as the refusal journey below does.  # noqa: E501
        copy.origin = "authoring:somebody-elses-plan"
        conflicting = _located(tmp_path, on_the_board)

    assert conflicting.ran.returncode == 2, conflicting.ran.stdout + conflicting.ran.stderr
    assert f"copy link names {board_project}" in conflicting.ran.stderr, conflicting.ran.stderr
    assert "does not record as copied from" in conflicting.ran.stderr, conflicting.ran.stderr
    assert "holds the plan at" not in conflicting.ran.stdout, conflicting.ran.stdout
    assert located.ran.returncode == 0, located.ran.stdout + located.ran.stderr
    reported = located.ran.stdout
    assert f"plan-locations: {BOARD_SOURCE} holds the plan at https://github.com/" in reported, (
        reported
    )
    assert f"plan-locations: its home is {board_project};" in reported, reported
    assert "its tasks routed to" not in reported, reported
    assert _shown(located.calls, QUALIFIED) == 1, located.calls
    assert _shown(located.calls, board_project) >= 1, located.calls
    listed = sorted(source for call in _listings(located.calls) for source in _sources(call))
    assert listed == sorted([BOARD_SOURCE, ROUTED_STAND_IN]), located.calls
    assert all(len(_sources(call)) == 1 for call in _listings(located.calls)), located.calls
    _narrowed_to_the_plan(located)


def test_the_plan_location_step_refuses_an_unstamped_copy_in_a_source_declared_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A copy between two private sources keeps its origin, so one carrying none is refused.

    The store withholds a private plan's origin only from a destination not declared
    private, so in the routed source declared private a copy the plan's link names but no
    origin ties to the plan is not this plan's copy, though the same unstamped copy on the
    public board is.
    """
    monkeypatch.setenv(ROUTED_VISIBILITY_ENV, "private")
    environment = {**_drafted(tmp_path, monkeypatch), ROUTED_VISIBILITY_ENV: "private"}
    with _serving_board() as remote:
        on_the_board = {**environment, **remote}
        copied = _recipe(on_the_board, "copy-plan", QUALIFIED, "--to", ROUTED_STAND_IN)
        assert copied.returncode == 0, copied.stdout + copied.stderr
        routed_project = _copied(copied.stdout)[QUALIFIED]
        stamped = _located(tmp_path, on_the_board)
        record = tmp_path / ROUTED_STAND_IN / "projects" / f"{routed_project.partition(':')[2]}.md"
        held = record.read_text(encoding="utf-8")
        assert f"{plan_store.ORIGIN_KEY}: {QUALIFIED}" in held, held
        # llmlint: ignore[tests_mirror_real_usage] The origin is the store's own `onetaskgraph.origin` key, which no store verb clears (`project metadata set` refuses the `onetaskgraph.` namespace), and a `local-md` project is the Markdown file a person edits by hand, so a copy left carrying no origin is reached the way a person leaves it: by editing that file.  # noqa: E501
        record.write_text(
            "\n".join(line for line in held.splitlines() if plan_store.ORIGIN_KEY not in line)
            + "\n",
            encoding="utf-8",
        )
        unstamped = _located(tmp_path, on_the_board)

    assert stamped.ran.returncode == 0, stamped.ran.stdout + stamped.ran.stderr
    assert f"plan-locations: its home is {routed_project};" in stamped.ran.stdout, stamped.ran
    assert unstamped.ran.returncode == 2, unstamped.ran.stdout + unstamped.ran.stderr
    assert f"copy link names {routed_project}" in unstamped.ran.stderr, unstamped.ran.stderr
    assert "does not record as copied from" in unstamped.ran.stderr, unstamped.ran.stderr
    assert "holds the plan at" not in unstamped.ran.stdout, unstamped.ran.stdout


def test_the_plan_location_step_refuses_a_plan_it_cannot_point_a_reviewer_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No copy, a link to no copy of this plan on the board, and two copies: each refused.

    Each refusal names the plan, and none reports a location a reviewer would be sent to.
    The dangling link is the one a copy since deleted from the board leaves behind. The
    plan is drafted in a source not declared private, because what tells a link left naming
    somebody else's project, and a second copy, is the origin a copy out of such a source
    carries.
    """
    _drafted_in_a_public_source(monkeypatch)
    environment = _drafted(tmp_path, monkeypatch)
    with _serving_board() as remote:
        on_the_board = {**environment, **remote}
        _beside_other_projects()
        populate(BOARD, PLANS_BOARD_SCALE)
        uncopied = _located(tmp_path, on_the_board)

        board_project = _copied_onto_the_board(on_the_board)
        record = tmp_path / "authoring" / "projects" / f"{PROJECT}.md"
        linked = record.read_text(encoding="utf-8")
        assert board_project in linked, linked
        # The plan's link repointed at a plan somebody else filed on the board, which the
        # store records as copied from nothing.
        # llmlint: ignore[tests_mirror_real_usage] The link is the store's own `onetaskgraph.copies` key, which no store verb sets (`project metadata set` refuses the `onetaskgraph.` namespace), and an authoring project is the Markdown file a planner edits by hand, so a link left naming another project is reached the way a person leaves it: by editing that file.  # noqa: E501
        record.write_text(linked.replace(board_project, OTHER_PLAN), encoding="utf-8")
        unstamped = _located(tmp_path, on_the_board)
        # And at a board project that does not exist, as a copy since deleted leaves it.
        # llmlint: ignore[tests_mirror_real_usage] As above: no store verb writes the store's own copy link, so the dangling link a deleted copy leaves is written into the hand-edited authoring file.  # noqa: E501
        record.write_text(linked.replace(board_project, DANGLING), encoding="utf-8")
        dangling = _located(tmp_path, on_the_board)
        # And, kept under the board, at a project of another source, then at no qualified
        # id at all: neither is where the copy reached on the board, so neither is read.
        # llmlint: ignore[tests_mirror_real_usage] As above: no store verb writes the store's own copy link, so a link naming another source's project is written into the hand-edited authoring file.  # noqa: E501
        record.write_text(linked.replace(board_project, ELSEWHERE), encoding="utf-8")
        elsewhere = _located(tmp_path, on_the_board)
        # llmlint: ignore[tests_mirror_real_usage] As above, for a link that is no qualified id.  # noqa: E501
        record.write_text(linked.replace(board_project, MALFORMED), encoding="utf-8")
        malformed = _located(tmp_path, on_the_board)
        # A second copy made while nothing tied the plan to the first: no link on the plan,
        # and no origin on the first copy's board rows for the store to find it by — even
        # `--recreate` finds a copy by that origin and repoints the link at it, so the store
        # itself never makes a second one. Both are then stamped as copied from the plan,
        # with the plan's link left naming the first.
        unlinked = "\n".join(
            line for line in linked.splitlines() if plan_store.COPIES_KEY not in line
        )
        assert unlinked != linked.rstrip("\n"), linked
        # llmlint: ignore-block[tests_mirror_real_usage] Two stamped copies are a state only
        # a person outside the store produces — the store follows a copy's origin to the copy
        # it already made, `--recreate` included — so the link is dropped from the
        # hand-edited authoring file, and each first-copy row's origin is cleared and
        # restored on the double's issue, the board field a person edits in GitHub's own
        # interface; the copy between is the real recipe.
        record.write_text(unlinked + "\n", encoding="utf-8")
        first = [(issue, issue.origin) for issue in BOARD.issues if issue.origin is not None]
        for issue, _ in first:
            issue.origin = None
        second = _copied_onto_the_board(on_the_board)
        for issue, origin in first:
            issue.origin = origin
        record.write_text(linked, encoding="utf-8")
        # llmlint: ignore-end[tests_mirror_real_usage]
        twice = _located(tmp_path, on_the_board)

    for refused in (uncopied, unstamped, dangling, elsewhere, malformed, twice):
        assert refused.ran.returncode == 2, refused.ran.stdout + refused.ran.stderr
        _narrowed_to_the_plan(refused)
        assert QUALIFIED in refused.ran.stderr, refused.ran.stderr
        assert "holds the plan at" not in refused.ran.stdout, refused.ran.stdout
    assert "holds no project the store records as copied from" in uncopied.ran.stderr, (
        uncopied.ran.stderr
    )
    assert f"copy link names {OTHER_PLAN}" in unstamped.ran.stderr, unstamped.ran.stderr
    assert "does not record as copied from" in unstamped.ran.stderr, unstamped.ran.stderr
    assert f"copy link names {DANGLING}" in dangling.ran.stderr, dangling.ran.stderr
    assert "the store could not read" in dangling.ran.stderr, dangling.ran.stderr
    for unread, named in ((elsewhere, ELSEWHERE), (malformed, MALFORMED)):
        said = unread.ran.stderr
        assert f"copy link for source '{BOARD_SOURCE}' names '{named}'" in said, said
        assert "no project of that source" in said, said
        assert not [call for call in unread.calls if named in call], unread.calls
    assert second not in {board_project, DANGLING}, (second, board_project)
    assert board_project in twice.ran.stderr and second in twice.ran.stderr, twice.ran.stderr
    assert "is not this command's to guess" in twice.ran.stderr, twice.ran.stderr
    # llmlint: ignore-end[e2e_not_mocked]
    # llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
