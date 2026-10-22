"""Copying a plan onto the board and approving it there cost what the plan is, not the board.

Every plan this host launches is copied onto the `plans` board and approved there, often
several times while it is revised, and both used to grow with every plan ever filed on that
board: a copy and an approval each walked it. These journeys drive the real `just copy-plan`
and `just approve-design` recipes and the locked `.venv/bin/onetaskgraph` against the loopback
board double filled with other people's plans — once at the `plans` board's scale, 2,000
items, and once at a tenth of it — beside one 10-item plan, and hold three things: the copy
lands the plan whole, the approval is recorded on the copy and admits its launch, and by the
double's own request log neither recipe reads a whole-board page and each sends exactly the
same requests, operation by operation, at either size.

What the approval at the `plans` board's scale costs is printed, one line per store call
it makes: the points the double's price table charges and the GitHub latency the copy-speed
spike measured for each request it logged (`REQUEST_LATENCY_MS`), beside the recipe's real
elapsed time. No budget is held to those figures: they rest on the adopted store's
project-scoped listings and its metadata write, which this repository does not decide.
"""

from __future__ import annotations

import bisect
import json
import random
import struct
import subprocess
import time
import zlib
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

import pytest
from github_board import (
    AUTHORING_ROOT_ENV,
    AUTHORING_SOURCE,
    BOARD,
    PLANS_BOARD_SCALE,
    REQUEST_LATENCY_MS,
    _audit_follow_up_calls,
    _board_cost,
    _GitHubFixture,
    _GraphQLRequest,
    _Issue,
    _request_points,
    _serving_board,
    populate,
    request_latency_ms,
)
from project_fixtures import GraphImage, designed, reviewed
from test_board_copy_design_approval_e2e import (
    CLOSING,
    _copied,
    _launch_gate,
    _location,
    _on_the_board,
    _rendering_environment,
    _through_the_template,
)
from waits import timeout as e2e_timeout

from orchestrator import design_approval
from orchestrator.root import REPO_ROOT

#: The plan these journeys copy: one project, eight tasks rendered from this host's
#: `plan-task` template, the second depending on the first, and one design document.
PROJECT = "board-scale"
QUALIFIED = f"{AUTHORING_SOURCE}:{PROJECT}"
DESIGN = f"{QUALIFIED}-design"
TASKS = 8
#: The board the plan is copied onto, as every plan of this repository is.
BOARD_SOURCE = "plans"
#: The two board sizes a cost is compared across: the `plans` board's, and a tenth of it.
SIZES = (PLANS_BOARD_SCALE, PLANS_BOARD_SCALE // 10)
#: The size of the plan graph image the design document carries, as the budgets' workload
#: states it: about what `just finish-plan` renders for a plan of this many tasks.
IMAGE_BYTES = 104 * 1024
#: How long each design-document row's description runs, so the document is about the 35 KB
#: the budgets' workload states.
ROW_DESCRIPTION = 3_600


class _Node(NamedTuple):
    """One of the plan's tasks: its node id and the title it is filed under."""

    node: str
    title: str


NODES = [_Node(f"part-{index:02d}", f"feat: deliver part {index:02d}") for index in range(TASKS)]


def _graph_image(directory: Path) -> GraphImage:
    """The plan graph the design document opens with: a PNG of :data:`IMAGE_BYTES` or so.

    Generated from a fixed seed, and incompressible, so the store uploads every byte of it
    as it would a rendered graph of that size.
    """

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    side = 188
    noise = random.Random(PROJECT)
    rows = b"".join(b"\x00" + noise.randbytes(3 * side) for _ in range(side))
    image = directory / "plan-dag.png"
    directory.mkdir(parents=True, exist_ok=True)
    image.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", side, side, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )
    return GraphImage(answers={"dag_image": image.name}, assets=[image])


def _authored(tmp_path: Path, root: Path, environment: Mapping[str, str]) -> dict[str, str]:
    """Author :data:`PROJECT` as a planner does: its record, then each task through the template.

    Answers each created task's qualified id by its title.
    """
    (root / "projects").mkdir(parents=True, exist_ok=True)
    # llmlint: ignore[tests_mirror_real_usage] The plan store has no project-create verb, and `personas/planner.yaml` has a planner author a plan as one `projects/<project>.md` record under the source's root before creating its tasks through the store, so this writes it exactly as a planner does.  # noqa: E501
    (root / "projects" / f"{PROJECT}.md").write_text(
        f'---\ntitle: "{PROJECT}"\nstatus: "todo"\nmetadata:\n'
        '  "onepipeline.schema_version": 3\n'
        '  "onepipeline.goal": {"text": "Deliver every part"}\n---\n\n'
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


def _drafted(root: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Author, design and review the plan where it was drafted, leaving it unapproved there.

    Answers the environment its store, the engine and the recipes run in.
    """
    root.mkdir(parents=True)
    # `reviewed` and the in-process reads read the store in this process's environment.
    monkeypatch.setenv(AUTHORING_ROOT_ENV, str(root / "authoring"))
    environment = _rendering_environment(root / "authoring")
    tasks = _authored(root, root / "authoring", environment)
    filler = random.Random(PROJECT)
    rows = [
        {
            "task": title,
            "delivers": f"the {node} route: "
            + " ".join(filler.choice(("pages", "the", "listing", "cursor")) for _ in range(900))[
                :ROW_DESCRIPTION
            ],
            "depends_on": NODES[0].title if node == NODES[1].node else "none",
            "location": _location(environment, tasks[title]) if index == 0 else f"part {index}",
        }
        for index, (node, title) in enumerate(NODES)
    ]
    designed(AUTHORING_SOURCE, PROJECT, rows, environment, **_graph_image(root / "graph"))
    reviewed(QUALIFIED)
    return environment


class _Launch(NamedTuple):
    """One store invocation a recipe made."""

    #: Its argv after the binary.
    argv: list[str]
    #: When it was launched, on the audit's monotonic clock.
    at: float


class _Run(NamedTuple):
    """One recipe run: what it answered, the store calls it made, and what reached the board."""

    ran: subprocess.CompletedProcess[str]
    #: Each store invocation, in launch order.
    calls: list[_Launch]
    #: Every request the double received while the recipe ran, in arrival order.
    requests: list[_GraphQLRequest]
    #: The recipe's real elapsed wall time, in seconds.
    seconds: float


def _run(root: Path, environment: Mapping[str, str], *command: str) -> _Run:
    """Run ``command`` from this checkout under the store-call audit, with a fresh request log."""
    audit = root / f"audit-{len(list(root.glob('audit-*')))}"
    audit.mkdir()
    audited = dict(environment)
    trace = _audit_follow_up_calls(audit, audited)
    _GitHubFixture.requests.clear()
    started = time.monotonic()
    ran = subprocess.run(
        list(command),
        cwd=REPO_ROOT,
        env=audited,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(600),
        check=False,
    )
    seconds = time.monotonic() - started
    requests = list(_GitHubFixture.requests)
    _GitHubFixture.requests.clear()
    lines = trace.read_text(encoding="utf-8").splitlines() if trace.exists() else []
    calls = [
        _Launch(list(entry["argv"][1:]), float(entry["at"]))
        for entry in (json.loads(line) for line in lines)
        if entry["kind"] == "store"
    ]
    return _Run(ran, calls, requests, seconds)


def _by_operation(requests: list[_GraphQLRequest]) -> Counter[str]:
    """How many requests of each operation ``requests`` holds."""
    return Counter(str(request.operation) for request in requests)


def _whole_board(requests: list[_GraphQLRequest]) -> list[str]:
    """Each request in ``requests`` that walks the board or searches it unnarrowed."""
    walked, unnarrowed = _board_cost(requests)
    return walked + unnarrowed


class _StoreCall(NamedTuple):
    """One store invocation of a recipe, and the requests it sent."""

    label: str
    requests: list[_GraphQLRequest]


def _store_calls(run: _Run) -> list[_StoreCall]:
    """The run's requests grouped by the store invocation that sent each one.

    The recipe invokes the store one process at a time, so a request belongs to the last
    launch before the double received it; each call is labelled by its verb and position.
    """
    launches = [call.at for call in run.calls]
    grouped: list[list[_GraphQLRequest]] = [[] for _ in run.calls]
    for request in run.requests:
        index = bisect.bisect_right(launches, request.received) - 1
        assert index >= 0, f"a request reached the board before any store call: {request.query}"
        grouped[index].append(request)
    return [
        _StoreCall(f"{position + 1}. onetaskgraph {' '.join(call.argv[:2])}", requests)
        for position, (call, requests) in enumerate(zip(run.calls, grouped, strict=True))
    ]


class _Scaled(NamedTuple):
    """What copying and approving the plan on a board of one size came to."""

    copy: _Run
    approval: _Run
    gate: subprocess.CompletedProcess[str]
    #: The board project the copy landed the plan at.
    board_project: str
    #: Each copied record's board id by its local id.
    destinations: dict[str, str]
    #: The approval the copied design document records, and the key its content hashes to.
    recorded: design_approval.ApprovalKey | None
    expected: design_approval.ApprovalKey
    #: The issues the copy created and kept, by title.
    landed: dict[str, _Issue]
    #: Every image the board took, by the path it serves it at.
    attachments: dict[str, bytes]
    #: The generated PNG the authored document references, before copying it.
    expected_image: bytes


def _local_bodies(environment: Mapping[str, str]) -> dict[str, str]:
    """Each of the plan's local tasks' bodies, by title, as the store reads them."""
    listed = subprocess.run(
        [
            str(REPO_ROOT / ".venv" / "bin" / "onetaskgraph"),
            "task",
            "list",
            "--source",
            AUTHORING_SOURCE,
            "--project",
            PROJECT,
            "--json",
            "--no-interactive",
        ],
        cwd=REPO_ROOT,
        env=dict(environment),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert listed.returncode == 0, listed.stdout + listed.stderr
    items = json.loads(listed.stdout)["items"]
    return {str(item["item"]["title"]): str(item["item"].get("content") or "") for item in items}


def _copied_and_approved(
    root: Path, monkeypatch: pytest.MonkeyPatch, size: int
) -> tuple[_Scaled, dict[str, str]]:
    """Copy the plan onto a board of ``size`` items and approve it there, through the recipes.

    Answers what both came to, and the plan's local task bodies by title.
    """
    environment = _drafted(root, monkeypatch)
    bodies = _local_bodies(environment)
    with _serving_board() as remote:
        on_the_board = {**environment, **remote}
        populate(BOARD, size)
        assert len(BOARD.issues) == size, len(BOARD.issues)
        copy = _run(root, on_the_board, "just", "copy-plan", QUALIFIED, "--to", BOARD_SOURCE)
        assert copy.ran.returncode == 0, copy.ran.stdout + copy.ran.stderr
        destinations = _copied(copy.ran.stdout)
        board_project = destinations[QUALIFIED]
        approval = _run(root, on_the_board, "just", "approve-design", board_project)
        gate = _launch_gate(on_the_board, board_project)
        _on_the_board(monkeypatch, remote)
        document = design_approval.design_document(board_project)
        scaled = _Scaled(
            copy=copy,
            approval=approval,
            gate=gate,
            board_project=board_project,
            destinations=destinations,
            recorded=design_approval.recorded(document),
            expected=design_approval.approval_key(document, design_approval.resolved_digest()),
            landed={issue.title: issue for issue in BOARD.issues_created_and_kept()},
            attachments=dict(BOARD.attachments),
            expected_image=(root / "graph" / "plan-dag.png").read_bytes(),
        )
    return scaled, bodies


def _the_plan_landed_whole(scaled: _Scaled, bodies: Mapping[str, str]) -> None:
    """The board holds the plan's project, its tasks and its document as the local records are."""
    assert len(scaled.destinations) == 1 + TASKS + 1, scaled.destinations
    project = scaled.landed[PROJECT]
    # A board files a document as an issue whose title the store marks as a document's.
    design = scaled.landed[f"DESIGN: Design: {PROJECT}"]
    for title, body in bodies.items():
        task = scaled.landed[title]
        assert task.parent_id == project.content_id, (title, task.parent_id)
        assert body.strip() and body.strip() in task.body, (title, body, task.body)
    assert sorted(bodies) == sorted(node.title for node in NODES), sorted(bodies)
    first, second = (scaled.landed[node.title] for node in NODES[:2])
    assert second.blocked_by == [first.content_id], second.blocked_by
    assert all(scaled.landed[node.title].blocked_by == [] for node in NODES[2:])
    assert design.parent_id == project.content_id, design.parent_id
    (served,) = [path for path, held in scaled.attachments.items() if held == scaled.expected_image]
    assert served in design.body, (served, design.body[:400])


def _the_approval_admits_the_copy(scaled: _Scaled) -> None:
    """The approval is recorded on the copied document, keyed to its content, and admits it."""
    ran = scaled.approval.ran
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "recorded the approval of" in ran.stdout, ran.stdout
    assert scaled.recorded == scaled.expected, (scaled.recorded, scaled.expected)
    assert scaled.gate.returncode == 0, scaled.gate.stdout + scaled.gate.stderr


def _printed_cost(approval: _Run) -> None:
    """Print what ``approval`` spent, per store call: requests, points and charged latency."""
    latency = sum(request_latency_ms(request) for request in approval.requests) / 1000
    points = sum(_request_points(request) for request in approval.requests)
    print(
        f"approve-design at {PLANS_BOARD_SCALE} items: requests={len(approval.requests)}, "
        f"points={points}, real={approval.seconds:.2f}s, latency added={latency:.2f}s"
    )
    for call in _store_calls(approval):
        print(
            f"  {call.label}: requests={len(call.requests)}, "
            f"points={sum(_request_points(request) for request in call.requests)}, "
            f"latency={sum(request_latency_ms(request) for request in call.requests)}ms, "
            f"operations={[str(request.operation) for request in call.requests]}"
        )
    for kind, charged in REQUEST_LATENCY_MS.items():
        print(f"  latency table: {kind}: {charged}ms")


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] `board-copy-approval` is
# this directory's own leaf project, keyed on `boardCopyApprovalWorkspace`, the edge this rule
# asks for; that key names the recipes, scripts and package these journeys drive, so narrowing
# it would memoize a verdict over a tree never run.
# llmlint: ignore-block[e2e_not_mocked] GitHub's Projects API is the one boundary doubled, for
# the reason the block around `_Board` gives — driving it writes to the live board this
# repository plans on — and the paid model `reviewed` scripts is the other; the recipes, the
# pinned engine and plan store, and the records they write are all real.
def test_copying_and_approving_a_plan_costs_the_same_requests_on_any_size_of_board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The copy and the approval send the same requests at 2,000 items as at 200, and no page.

    At both sizes the copy lands the plan whole and the approval is recorded on the copied
    document and admits the launch. What the approval at the `plans` board's scale cost is
    printed per store call.
    """
    scaled: dict[int, _Scaled] = {}
    for size in SIZES:
        scaled[size], bodies = _copied_and_approved(tmp_path / str(size), monkeypatch, size)
        _the_plan_landed_whole(scaled[size], bodies)
        _the_approval_admits_the_copy(scaled[size])
        for recipe in (scaled[size].copy, scaled[size].approval):
            assert recipe.requests, recipe.ran.args
            assert _whole_board(recipe.requests) == [], (size, recipe.ran.args)

    large, small = (scaled[size] for size in SIZES)
    assert _by_operation(large.copy.requests) == _by_operation(small.copy.requests), (
        _by_operation(large.copy.requests),
        _by_operation(small.copy.requests),
    )
    assert _by_operation(large.approval.requests) == _by_operation(small.approval.requests), (
        _by_operation(large.approval.requests),
        _by_operation(small.approval.requests),
    )

    _printed_cost(large.approval)
    # llmlint: ignore-end[e2e_not_mocked]
    # llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
