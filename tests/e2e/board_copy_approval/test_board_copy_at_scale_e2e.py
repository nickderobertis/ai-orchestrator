"""Copying a plan onto the board and approving it there cost what the plan is, not the board.

Every plan this host launches is copied onto the `plans` board and approved there, often
several times while it is revised, and both used to grow with every plan ever filed on that
board: a copy and an approval each walked it. These journeys drive the real `just copy-plan`
and `just approve-design` recipes and the locked `.venv/bin/onetaskgraph` against the loopback
board double filled with other people's plans — once at the `plans` board's scale, 2,000
items, and once at ten times it — beside one 10-item plan, and hold three things: every
copy lands the plan whole — the first, a re-copy after one task's body was regenerated, and a
re-copy of the unchanged plan — the approval is recorded on the copy and admits its launch, and
by the double's own request log neither recipe reads a whole-board page and each sends exactly
the same requests, operation by operation, at either size. The double places each issue a copy
creates only the delay after its create that the feasibility spike measured GitHub taking, as
onetaskgraph's own loopback board does, and prices each request at the points it measured.

What those copies and the approval spend of the board token's allowance is budgeted in the plan
store, whose release registers a points budget for each copy scenario, one for a first copy's
content creations and one for an approval's calls, which
`tests/board_copy_quota_handover/test_board_copy_quota_handover.py` reads at the pin. So what
these journeys hold is that every request a recipe sends the board
comes from the store calls those budgets measure: a copy's `project copy` then `document copy`,
and an approval's four — the project's record by id, its tasks and its documents each listed by
project, and one metadata write guarded on the change token the document listing returned —
with every other store call they make reading only the local `authoring` source or the store's
configuration, and sending the board nothing.

Each recipe is also held to the exact store invocations it makes, by the test-only store-call
audit: a copy reads the plan's record, its tasks, their edges and its documents once each and
then copies it, and an approval reads its board copy once.

The copies run under this checkout's own store declarations — the plan drafted in the
`authoring` source declared `visibility: private`, copied onto `plans` with its cache on and
every public write screened — and by the double's request log each holds the adopted store's
copy fixes: the first copy asks every origin in one batched read-ahead and none alone, no
`document copy` lists its project's members, neither re-copy re-reads a member's edges or
details its members listing carried, and the changed copy's two issue updates go out as one
aliased mutation. Every members listing is held to answering each field the store's query, as
sent, selects.

`plan-approval-seconds` is the approval's real elapsed time plus the GitHub latency the
copy-speed spike measured for each request the double logged (`REQUEST_LATENCY_MS`), recorded
per store call by the journey the uncached `test-host` target runs alone, because a wall clock a
cache replayed would be another moment's; the same journey records each copy's seconds the same
way, under `plan-copy-seconds-first`, `-changed` and `-unchanged`. Beside the
figure the journey records where its seconds went — each store invocation from launch to
reaping, the store's start-up timed by a trivial invocation, the program outside the store, the
recipe's steps outside the program, and the network round trips charged — and the CPU seconds
of the recipe's whole process tree, which no wait on the board inflates.
"""

from __future__ import annotations

import bisect
import dataclasses
import json
import random
import resource
import statistics
import struct
import subprocess
import time
import zlib
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

import budget_telemetry
import pytest
from github_board import (
    AUTHORING_ROOT_ENV,
    AUTHORING_SOURCE,
    BOARD,
    COPY_BATCHES,
    MEASURED_PLACEMENT_SECONDS,
    PLANS_BOARD_SCALE,
    REQUEST_LATENCY_MS,
    _audit_follow_up_calls,
    _board_cost,
    _GitHubFixture,
    _GraphQLRequest,
    _Issue,
    _Operation,
    _request_points,
    _serving_board,
    _write_inputs,
    populate,
    request_latency_ms,
    unanswered_selections,
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

from orchestrator import design_approval, plan_store
from orchestrator.root import REPO_ROOT

#: The plan these journeys copy: one project, eight tasks rendered from this host's
#: `plan-task` template, the second depending on the first, and one design document.
PROJECT = "board-scale"
QUALIFIED = f"{AUTHORING_SOURCE}:{PROJECT}"
DESIGN = f"{QUALIFIED}-design"
TASKS = 8
#: The board the plan is copied onto, as every plan of this repository is.
BOARD_SOURCE = "plans"
#: The required board sizes: ten times the `plans` board's scale, and that scale itself.
SIZES = (PLANS_BOARD_SCALE * 10, PLANS_BOARD_SCALE)
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


def _answers(directory: Path, node: str, what: str) -> Path:
    """The template answers task ``node`` is rendered from, saying ``what`` it delivers."""
    answered = directory / f"{node}-{len(list(directory.glob(f'{node}-*.json')))}.json"
    answered.write_text(
        json.dumps(
            {
                "what": what,
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
    return answered


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
        answered = _answers(tmp_path, node, f"Deliver {node} and the test that drives it.")
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


class _Drafted(NamedTuple):
    """The plan as drafted: the environment its tools run in, and each task's id by title."""

    environment: dict[str, str]
    tasks: dict[str, str]


def _drafted(root: Path, monkeypatch: pytest.MonkeyPatch) -> _Drafted:
    """Author, design and review the plan where it was drafted, leaving it unapproved there."""
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
    return _Drafted(environment, tasks)


#: The task a revision regenerates, as a manager changes a planned task: one with no edge.
REVISED = NODES[3]


def _revised(root: Path, drafted: _Drafted) -> None:
    """Regenerate :data:`REVISED` from changed answers, then review the plan again.

    The way a manager changes a task — `onetaskgraph task render` over the `plan-task`
    template, keeping its id, dependencies and metadata — and the review `just copy-plan`
    requires of every task before it copies one.
    """
    rendered = _through_the_template(
        drafted.environment,
        "render",
        drafted.tasks[REVISED.title],
        "--template-loader",
        "-",
        "--no-interactive",
        "--answers",
        str(_answers(root, REVISED.node, f"Deliver {REVISED.node} paged, and its test.")),
    )
    assert rendered.returncode == 0, rendered.stdout + rendered.stderr
    reviewed(QUALIFIED)


class _Launch(NamedTuple):
    """One store invocation a recipe made."""

    #: Its argv after the binary.
    argv: list[str]
    #: When it was launched, on the audit's monotonic clock.
    at: float
    #: When the interpreter that launched it reaped it, on the same clock.
    ended: float

    @property
    def seconds(self) -> float:
        """How long the invocation ran, from its launch to its reaping."""
        return self.ended - self.at


class _Run(NamedTuple):
    """One recipe run: what it answered, the store calls it made, and what reached the board."""

    ran: subprocess.CompletedProcess[str]
    #: Each store invocation, in launch order.
    calls: list[_Launch]
    #: Every request the double received while the recipe ran, in arrival order.
    requests: list[_GraphQLRequest]
    #: The recipe's real elapsed wall time, in seconds.
    seconds: float
    #: How long the recipe's program ran: its interpreter's life, from the audit's first
    #: record in it to its exit, in seconds.
    program: float
    #: How many Python interpreters the recipe started.
    interpreters: int
    #: The user and system CPU seconds the recipe's whole process tree spent.
    cpu: float

    @property
    def outside_program(self) -> float:
        """The recipe's seconds outside its program: `just`, its shell steps and `uv run`."""
        return self.seconds - self.program

    @property
    def outside_store(self) -> float:
        """The program's own seconds outside every store invocation it made."""
        return self.program - sum(call.seconds for call in self.calls)


def _run(root: Path, environment: Mapping[str, str], *command: str) -> _Run:
    """Run ``command`` from this checkout under the store-call audit, with a fresh request log.

    The CPU seconds are the test process's reaped children's, read before and after: every
    process of the recipe is a descendant its parent waits for, and nothing else this process
    starts runs meanwhile, while the double answering the board is a thread of this process
    and so counts none of its own.
    """
    audit = root / f"audit-{len(list(root.glob('audit-*')))}"
    audit.mkdir()
    audited = dict(environment)
    trace = _audit_follow_up_calls(audit, audited)
    _GitHubFixture.requests.clear()
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
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
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    requests = list(_GitHubFixture.requests)
    _GitHubFixture.requests.clear()
    lines = trace.read_text(encoding="utf-8").splitlines() if trace.exists() else []
    entries = [json.loads(line) for line in lines]
    calls = _launches(entries)
    started_at = {entry["pid"]: entry["at"] for entry in entries if entry["kind"] == "interpreter"}
    exited_at = {
        entry["pid"]: entry["at"] for entry in entries if entry["kind"] == "interpreter-exit"
    }
    # The program is the interpreter that launched the store; a recipe that launched none is
    # timed by every interpreter it started.
    launchers = {entry["pid"] for entry in entries if entry["kind"] == "store"} or set(started_at)
    program = sum(exited_at[pid] - started_at[pid] for pid in launchers)
    cpu = (after.ru_utime + after.ru_stime) - (before.ru_utime + before.ru_stime)
    return _Run(ran, calls, requests, seconds, program, len(started_at), cpu)


def _launches(entries: list[dict[str, object]]) -> list[_Launch]:
    """Each store launch the audit recorded, paired with the reaping its interpreter recorded.

    An interpreter runs one store process at a time, so its launches and its reapings pair in
    order.
    """
    reaped: dict[object, list[float]] = {}
    for entry in entries:
        if entry["kind"] == "store-exit":
            reaped.setdefault(entry["pid"], []).append(float(str(entry["at"])))
    launched = [entry for entry in entries if entry["kind"] == "store"]
    calls = []
    for entry in launched:
        argv = entry["argv"]
        assert isinstance(argv, list), entry
        calls.append(
            _Launch(
                [str(word) for word in argv[1:]],
                float(str(entry["at"])),
                reaped[entry["pid"]].pop(0),
            )
        )
    assert all(not left for left in reaped.values()), reaped
    return calls


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


class _Copied(NamedTuple):
    """One `just copy-plan` of the plan, and the board it left."""

    run: _Run
    #: Each copied record's board id by its local id, as the copy reported it.
    destinations: dict[str, str]
    #: The plan's local task bodies by title, as the copy read them.
    bodies: dict[str, str]
    #: The issues every copy so far created and kept, by title.
    landed: dict[str, _Issue]
    #: Every image the board took, by the path it serves it at.
    attachments: dict[str, bytes]
    #: How many issues every copy so far created, kept or not.
    created: int


class _Scaled(NamedTuple):
    """What copying and approving the plan on a board of one size came to."""

    #: The first copy, then — where the journey revises the plan — the re-copy after one task
    #: was regenerated and the re-copy of the unchanged plan after it.
    copies: list[_Copied]
    approval: _Run
    gate: subprocess.CompletedProcess[str]
    #: The board project the copy landed the plan at.
    board_project: str
    #: The approval the copied design document records, and the key its content hashes to.
    recorded: design_approval.ApprovalKey | None
    expected: design_approval.ApprovalKey
    #: The generated PNG the authored document references, before copying it.
    expected_image: bytes
    #: The change token a listing of the copied plan's documents returned before the approval.
    listed_version: plan_store.DocumentVersion | None


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


def _copy(root: Path, environment: Mapping[str, str]) -> _Copied:
    """`just copy-plan` of the plan onto the board, and what it left there."""
    bodies = _local_bodies(environment)
    run = _run(root, environment, "just", "copy-plan", QUALIFIED, "--to", BOARD_SOURCE)
    assert run.ran.returncode == 0, run.ran.stdout + run.ran.stderr
    return _Copied(
        run=run,
        destinations=_copied(run.ran.stdout),
        bodies=bodies,
        # Copied, because the board's own issues move with every later write.
        landed={
            issue.title: dataclasses.replace(issue, blocked_by=list(issue.blocked_by))
            for issue in BOARD.issues_created_and_kept()
        },
        attachments=dict(BOARD.attachments),
        created=len(BOARD.created),
    )


def _copied_and_approved(
    root: Path, monkeypatch: pytest.MonkeyPatch, size: int, *, revise: bool = True
) -> _Scaled:
    """Copy the plan onto a board of ``size`` items and approve it there, through the recipes.

    The board places each created issue the measured delay after its create. With
    ``revise``, the approved plan then has one task regenerated and is copied again, and
    copied once more unchanged.
    """
    drafted = _drafted(root, monkeypatch)
    with _serving_board(placement=MEASURED_PLACEMENT_SECONDS) as remote:
        on_the_board = {**drafted.environment, **remote}
        _the_store_reads_the_hosts_own_declarations(on_the_board)
        populate(BOARD, size)
        assert len(BOARD.issues) == size, len(BOARD.issues)
        copies = [_copy(root, on_the_board)]
        board_project = copies[0].destinations[QUALIFIED]
        _on_the_board(monkeypatch, remote)
        (listed,) = design_approval.design_documents(
            board_project, plan_store.read_documents(board_project)
        )
        approval = _run(root, on_the_board, "just", "approve-design", board_project)
        gate = _launch_gate(on_the_board, board_project)
        document = design_approval.design_document(board_project)
        if revise:
            monkeypatch.setenv(AUTHORING_ROOT_ENV, drafted.environment[AUTHORING_ROOT_ENV])
            _revised(root, drafted)
            copies.append(_copy(root, on_the_board))
            copies.append(_copy(root, on_the_board))
        return _Scaled(
            copies=copies,
            approval=approval,
            gate=gate,
            board_project=board_project,
            recorded=design_approval.recorded(document),
            expected=design_approval.approval_key(document, design_approval.resolved_digest()),
            expected_image=(root / "graph" / "plan-dag.png").read_bytes(),
            listed_version=listed.version,
        )


def _the_plan_landed_whole(copied: _Copied, scaled: _Scaled) -> None:
    """The board holds the plan's project, its tasks and its document as the local records are.

    Each record once, each task under its project with its own body and its one edge, and the
    document under the project carrying the plan's image at the address the board serves it.
    """
    assert len(copied.destinations) == 1 + TASKS + 1, copied.destinations
    assert copied.destinations == scaled.copies[0].destinations, copied.destinations
    assert len(copied.landed) == 1 + TASKS + 1, sorted(copied.landed)
    project = copied.landed[PROJECT]
    # A board files a document as an issue whose title the store marks as a document's.
    design = copied.landed[f"DESIGN: Design: {PROJECT}"]
    for title, body in copied.bodies.items():
        task = copied.landed[title]
        assert task.parent_id == project.content_id, (title, task.parent_id)
        assert body.strip() and body.strip() in task.body, (title, body, task.body)
    assert sorted(copied.bodies) == sorted(node.title for node in NODES), sorted(copied.bodies)
    first, second = (copied.landed[node.title] for node in NODES[:2])
    assert second.blocked_by == [first.content_id], second.blocked_by
    assert all(copied.landed[node.title].blocked_by == [] for node in NODES[2:])
    assert design.parent_id == project.content_id, design.parent_id
    (served,) = [path for path, held in copied.attachments.items() if held == scaled.expected_image]
    assert served in design.body, (served, design.body[:400])


def _the_revisions_landed_in_place(scaled: _Scaled) -> None:
    """A re-copy updates the plan's own issues: none created, and only the revised body moved."""
    first, changed, unchanged = scaled.copies
    assert changed.created == unchanged.created == first.created, (
        first.created,
        changed.created,
        unchanged.created,
    )
    moved = [
        title
        for title in first.bodies
        if first.bodies[title].strip() != changed.bodies[title].strip()
    ]
    assert moved == [REVISED.title], moved
    assert "paged, and its test" in changed.landed[REVISED.title].body
    assert changed.bodies == unchanged.bodies


def _the_approval_admits_the_copy(scaled: _Scaled) -> None:
    """The approval is recorded on the copied document, keyed to its content, and admits it."""
    ran = scaled.approval.ran
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "recorded the approval of" in ran.stdout, ran.stdout
    assert scaled.recorded == scaled.expected, (scaled.recorded, scaled.expected)
    assert scaled.gate.returncode == 0, scaled.gate.stdout + scaled.gate.stderr


#: The store calls an approval makes to the `plans` board, by verb: its project's record read
#: by id, its tasks and its documents each listed by project, and the one guarded write of its
#: approval record.
APPROVAL_CALLS = (
    ("document", "list"),
    ("project", "show"),
    ("task", "list"),
    ("document", "metadata", "set"),
)

#: The store calls the approval may make beside those, none of which reaches GitHub: the
#: store's own resolved configuration, and the local `authoring` source's projects and
#: documents, read to recognise a copy drafted privately (`spike_plan.drafted_ids`).
LOCAL_CALLS = {
    (None, ("config", "show")),
    (AUTHORING_SOURCE, ("project", "list")),
    (AUTHORING_SOURCE, ("document", "list")),
}


def _flag(argv: list[str], flag: str) -> str | None:
    """The value ``argv`` passes ``flag``, or `None` when it passes none."""
    return argv[argv.index(flag) + 1] if flag in argv else None


def _source(argv: list[str]) -> str | None:
    """The source a store call addresses: its `--source`, else its first qualified id's."""
    named = _flag(argv, "--source")
    if named is not None:
        return named
    qualified = next((word for word in argv if ":" in word and not word.startswith("{")), None)
    return qualified.partition(":")[0] if qualified else None


def _the_approval_makes_only_its_four_calls(scaled: _Scaled) -> None:
    """The approval asks the board four things, guarded on the listing; the rest stays local.

    Each call is told by the source it addresses, so a further call to the `plans` source —
    a document read alone among them — fails here, while the local reads of the `authoring`
    source a privately drafted copy is recognised by are held to send the board nothing.
    """
    calls = scaled.approval.calls
    on_the_board = [call.argv for call in calls if _source(call.argv) == BOARD_SOURCE]
    verbs = [
        tuple(argv[: len(verb)]) for argv, verb in zip(on_the_board, APPROVAL_CALLS, strict=False)
    ]
    assert len(on_the_board) == len(APPROVAL_CALLS), on_the_board
    assert verbs == list(APPROVAL_CALLS), on_the_board
    local = [call.argv for call in calls if _source(call.argv) != BOARD_SOURCE]
    assert all((_source(argv), tuple(argv[:2])) in LOCAL_CALLS for argv in local), local
    assert all(
        call.requests == []
        for call, launch in zip(_store_calls(scaled.approval), calls, strict=True)
        if _source(launch.argv) != BOARD_SOURCE
    ), [(call.label, len(call.requests)) for call in _store_calls(scaled.approval)]
    listings = [argv for argv in on_the_board if argv[1] == "list"]
    assert all(_flag(argv, "--project") for argv in listings), listings
    (write,) = [argv for argv in on_the_board if argv[:3] == ["document", "metadata", "set"]]
    assert scaled.listed_version, "the board's document listing has to carry a change token"
    assert _flag(write, "--expect") == scaled.listed_version, (write, scaled.listed_version)


#: Every store invocation an approval makes, in order, with the source each addresses: its
#: four calls to the board, between which it reads, once, the store's configuration, the local
#: `authoring` source's projects and the drafted plan's documents, to recognise the copy as one
#: drafted privately.
APPROVAL_INVOCATIONS = (
    (("document", "list"), BOARD_SOURCE),
    (("project", "show"), BOARD_SOURCE),
    (("task", "list"), BOARD_SOURCE),
    (("config", "show"), None),
    (("project", "list"), AUTHORING_SOURCE),
    (("document", "list"), AUTHORING_SOURCE),
    (("document", "metadata"), BOARD_SOURCE),
)


def _the_approval_reads_the_plan_once(scaled: _Scaled) -> None:
    """The approval makes exactly :data:`APPROVAL_INVOCATIONS`, reading the board copy once."""
    calls = [call.argv for call in scaled.approval.calls]
    assert [(tuple(argv[:2]), _source(argv)) for argv in calls] == list(APPROVAL_INVOCATIONS), calls


#: The store calls a copy makes to the `plans` board, in order: the plan's project with its
#: tasks, then its document — the calls the store's copy budgets measure.
COPY_CALLS = (("project", "copy"), ("document", "copy"))

#: Every store invocation a copy makes, in order: the plan read once — its project's record by
#: id, its tasks listed by project, their dependency edges as the project's one graph, and its
#: documents listed by project — then the two calls that copy it.
COPY_INVOCATIONS = (
    ("project", "show"),
    ("task", "list"),
    ("project", "graph"),
    ("document", "list"),
    *COPY_CALLS,
)


def _the_copy_reads_the_plan_once(copied: _Copied) -> None:
    """The copy makes exactly :data:`COPY_INVOCATIONS`, each read naming the local plan.

    One read of the project's record, its tasks, their edges and its documents serves the
    review check, the size measurement and the copy alike.
    """
    calls = [call.argv for call in copied.run.calls]
    assert [tuple(argv[:2]) for argv in calls] == list(COPY_INVOCATIONS), calls
    shown, listed, graphed, documents = calls[:4]
    assert shown[2] == graphed[2] == QUALIFIED, (shown, graphed)
    for listing in (listed, documents):
        assert (_flag(listing, "--project"), _flag(listing, "--source")) == (
            PROJECT,
            AUTHORING_SOURCE,
        ), listing


def _the_copy_makes_only_its_two_calls(copied: _Copied) -> None:
    """Every request a copy sends the board comes from its `project copy` or `document copy`.

    A store call reaches the board when it copies to the `plans` source or names it; each
    other call the recipe makes — reads of the local `authoring` source and the store's
    configuration — is held to sending the board nothing, and each request the double logged
    to the call the recipe had launched last when it arrived.
    """
    calls = copied.run.calls

    def on_the_board(argv: list[str]) -> bool:
        return _flag(argv, "--to") == BOARD_SOURCE or _source(argv) == BOARD_SOURCE

    board_calls = [tuple(call.argv[:2]) for call in calls if on_the_board(call.argv)]
    assert board_calls == list(COPY_CALLS), [call.argv for call in calls]
    sent = _store_calls(copied.run)
    assert all(
        call.requests == []
        for call, launch in zip(sent, calls, strict=True)
        if not on_the_board(launch.argv)
    ), [(call.label, len(call.requests)) for call in sent]
    assert sum(len(call.requests) for call in sent) == len(copied.run.requests)


def _store_calls_of(copied: _Copied, verb: tuple[str, str]) -> list[_GraphQLRequest]:
    """Every request the copy's store call ``verb`` sent the board."""
    sent = [
        call.requests
        for call, launch in zip(_store_calls(copied.run), copied.run.calls, strict=True)
        if tuple(launch.argv[:2]) == verb
    ]
    assert len(sent) == 1, (verb, [call.argv for call in copied.run.calls])
    return sent[0]


def _the_store_reads_the_hosts_own_declarations(environment: Mapping[str, str]) -> None:
    """The copies run under this checkout's real declarations, as the store resolves them.

    The plan is drafted in the `authoring` source this host declares `visibility: private`, and
    copied onto `plans` with that board's cache on and every public write screened — the shape
    onetaskgraph's own scenario mirrors, read here off the store rather than restated.
    """
    shown = subprocess.run(
        [str(REPO_ROOT / ".venv" / "bin" / "onetaskgraph"), "config", "show", "--json"],
        cwd=REPO_ROOT,
        env={**environment, "ONETASKGRAPH_INTERACTIVE": "false"},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert shown.returncode == 0, shown.stdout + shown.stderr
    settings = {str(entry["key"]): entry for entry in json.loads(shown.stdout)["settings"]}
    declared = REPO_ROOT / "onetaskgraph.yaml"
    for key, value in (
        (f"sources.{AUTHORING_SOURCE}.visibility", "private"),
        (f"sources.{BOARD_SOURCE}.visibility", "public"),
        (f"sources.{BOARD_SOURCE}.cache.enabled", True),
        (
            "write_policy.check_command",
            ["onevcs", "boundary", "check", "--destination", "public", "--input", "-"],
        ),
    ):
        assert settings[key]["value"] == value, settings.get(key)
        assert settings[key]["origin"] == {"layer": "file", "path": str(declared)}, settings[key]


def _the_first_copy_reads_origins_ahead(first: _Copied) -> None:
    """The first copy asks every origin in the batched read-ahead, and none of them alone.

    Its `project copy` opens with one batched origin read; no single origin lookup follows it
    in either store call, so not one origin that read answered is asked again.
    """
    project = _store_calls_of(first, ("project", "copy"))
    assert project and project[0].operation is _Operation.ORIGIN_LOOKUPS, _by_operation(project)
    single = [r for r in first.run.requests if r.operation is _Operation.ORIGIN_LOOKUP]
    assert single == [], _by_operation(first.run.requests)


def _the_document_copy_pins_no_members(copied: _Copied) -> None:
    """A `document copy` sends no members listing of its project, on any copy."""
    document = _store_calls_of(copied, ("document", "copy"))
    assert document, _by_operation(copied.run.requests)
    assert [r for r in document if r.operation is _Operation.SUB_ISSUES] == [], _by_operation(
        document
    )


#: What a re-copy never sends once its members listing carried every member: a read of one
#: member's edges (the store's `ISSUE_DEPENDS_ON` or `ISSUE_DEPENDENCIES`, both answered here as
#: one operation) or a batched re-read of the members' details.
RE_READS = {_Operation.DEPENDENCIES, _Operation.ISSUE_COPY_DETAILS}


def _the_recopy_trusts_its_listing(copied: _Copied) -> None:
    """A re-copy lists its project's members once, and re-reads no member it listed."""
    sent = _by_operation(copied.run.requests)
    assert sent[str(_Operation.SUB_ISSUES)] == 1, sent
    assert not RE_READS & {r.operation for r in copied.run.requests}, sent


def _the_changed_copy_batches_its_updates(changed: _Copied, unchanged: _Copied) -> None:
    """The changed copy's two issue updates go out as one aliased mutation; unchanged, none.

    One `update<n>` batch sending two slots — the regenerated task's body and its project's
    review record — and no single issue update beside it.
    """
    updates = [r for r in changed.run.requests if r.is_mutation]
    assert [r.operation for r in updates] == [_Operation.UPDATE_ISSUES], _by_operation(updates)
    (part,) = COPY_BATCHES[_Operation.UPDATE_ISSUES]
    assert len(part.sent(updates[0].variables)) == 2, updates[0].variables
    # Each aliased update is one issue write, as a single update would have been.
    written = _write_inputs(_Operation.UPDATE_ISSUE, changed.run.requests)
    assert len(written) == 2, written
    assert changed.landed[REVISED.title].content_id in {update["id"] for update in written}
    assert [r for r in unchanged.run.requests if r.is_mutation] == [], _by_operation(
        unchanged.run.requests
    )


def _the_listings_answer_what_they_select(recipes: list[_Run]) -> None:
    """Every members listing the double answered answers each field the store's query selects.

    Read off the query the installed store sent, so a release selecting a field the double
    leaves out fails here naming it, rather than reading to the store as members to re-read.
    """
    listings = [r for run in recipes for r in run.requests if r.operation is _Operation.SUB_ISSUES]
    assert listings, "no members listing reached the board to check"
    for listing in listings:
        assert isinstance(listing.answered.data, dict), listing.answered
        assert listing.answered.data.get("node"), listing.variables
        omitted = unanswered_selections(listing.query, listing.variables, listing.answered.data)
        assert omitted == [], omitted


def _the_copies_send_only_what_the_store_needs(scaled: _Scaled) -> None:
    """The request-log claims the adopted store's copy fixes make, on this host's double."""
    first, changed, unchanged = scaled.copies
    _the_listings_answer_what_they_select(
        [copied.run for copied in scaled.copies] + [scaled.approval]
    )
    _the_first_copy_reads_origins_ahead(first)
    for copied in scaled.copies:
        _the_document_copy_pins_no_members(copied)
    for copied in (changed, unchanged):
        _the_recopy_trusts_its_listing(copied)
    _the_changed_copy_batches_its_updates(changed, unchanged)


STARTUP_SAMPLES = 5


def _store_startup(root: Path, environment: Mapping[str, str]) -> float:
    """The median seconds of a trivial store invocation, timed as a recipe's are.

    `config show` of this checkout's configuration, launched from an audited interpreter and
    timed from its launch to its reaping, so what it costs is the store's process start-up and
    its loading of the configuration every invocation loads, and nothing a recipe asks of it.
    """
    store = REPO_ROOT / ".venv" / "bin" / "onetaskgraph"
    probe = (
        "import subprocess, sys\n"
        f"for _ in range({STARTUP_SAMPLES}):\n"
        "    subprocess.run([sys.argv[1], 'config', 'show', '--json', '--no-interactive'],"
        " stdout=subprocess.DEVNULL, check=True)\n"
    )
    timed = _run(
        root, environment, str(REPO_ROOT / ".venv" / "bin" / "python"), "-c", probe, str(store)
    )
    assert timed.ran.returncode == 0, timed.ran.stdout + timed.ran.stderr
    assert len(timed.calls) == STARTUP_SAMPLES, timed.calls
    return statistics.median(call.seconds for call in timed.calls)


def _recorded_seconds(budget: str, recipe: _Run, startup: float) -> None:
    """Record ``budget`` for ``recipe``: the real elapsed time and each call's latency, and why.

    The figure is the recipe's real elapsed time plus the GitHub latency charged for each
    request the double logged, per store call. Beside it, counted toward nothing, is where
    those seconds went: each store invocation's own seconds from launch to reaping; the store's
    start-up and loading, as :func:`_store_startup` timed it, once per invocation; the network
    round trips the latency table charges; the program's own seconds outside the store; the
    recipe's steps outside its program; how many store invocations it made; the CPU seconds of
    its whole process tree; and the latency table itself.
    """
    budget_telemetry.record(budget, "real elapsed", recipe.seconds, unit="seconds")
    latencies = []
    for call, launch in zip(_store_calls(recipe), recipe.calls, strict=True):
        latency = sum(request_latency_ms(request) for request in call.requests) / 1000
        latencies.append(latency)
        budget_telemetry.record(
            budget,
            f"added latency of {call.label}",
            latency,
            unit="seconds",
            requests=len(call.requests),
        )
        budget_telemetry.record(
            budget,
            f"seconds of {call.label}",
            launch.seconds,
            unit="seconds",
            counted=False,
            requests=len(call.requests),
            latency=latency,
        )
    detail = {
        "store start-up and loading": startup * len(recipe.calls),
        "network round trips": sum(latencies),
        "host program outside store invocations": recipe.outside_store,
        "host recipe steps outside the program": recipe.outside_program,
    }
    for label, seconds in detail.items():
        budget_telemetry.record(budget, label, seconds, unit="seconds", counted=False)
    budget_telemetry.record(
        budget, "store invocations", len(recipe.calls), unit="invocations", counted=False
    )
    budget_telemetry.record(
        budget, "process-tree CPU", recipe.cpu, unit="CPU seconds", counted=False
    )
    budget_telemetry.record(
        budget,
        "requests and points",
        len(recipe.requests),
        unit="requests",
        counted=False,
        points=sum(_request_points(request) for request in recipe.requests),
    )
    for kind, charged in REQUEST_LATENCY_MS.items():
        budget_telemetry.record(
            budget, f"latency table: {kind}", charged, unit="ms per request", counted=False
        )


def _printed_cost(recipe: _Run, named: str) -> None:
    """Print what ``recipe`` spent, per store call: seconds, requests, points and latency."""
    latency = sum(request_latency_ms(request) for request in recipe.requests) / 1000
    points = sum(_request_points(request) for request in recipe.requests)
    print(
        f"{named} at {PLANS_BOARD_SCALE} items: requests={len(recipe.requests)}, "
        f"points={points}, real={recipe.seconds:.2f}s, latency added={latency:.2f}s, "
        f"cpu={recipe.cpu:.2f}s, invocations={len(recipe.calls)}, "
        f"outside program={recipe.outside_program:.2f}s, "
        f"program outside store={recipe.outside_store:.2f}s"
    )
    for call, launch in zip(_store_calls(recipe), recipe.calls, strict=True):
        print(
            f"  {call.label}: seconds={launch.seconds:.3f}, requests={len(call.requests)}, "
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
    """Every copy and the approval send the same requests at 20,000 items as at 2,000, and no page.

    At both sizes each of the three copies — the first, the re-copy after one task was
    regenerated, and the unchanged re-copy — lands the plan whole through the store's
    `project copy` then `document copy` and nothing else, and the approval is recorded on the
    copied document and admits the launch through exactly its four store calls to the board.
    What the approval at the `plans` board's scale cost is printed per store call.
    """
    scaled: dict[int, _Scaled] = {}
    for size in SIZES:
        scaled[size] = _copied_and_approved(tmp_path / str(size), monkeypatch, size)
        for copied in scaled[size].copies:
            _the_plan_landed_whole(copied, scaled[size])
            _the_copy_makes_only_its_two_calls(copied)
            _the_copy_reads_the_plan_once(copied)
        _the_revisions_landed_in_place(scaled[size])
        _the_copies_send_only_what_the_store_needs(scaled[size])
        _the_approval_admits_the_copy(scaled[size])
        _the_approval_makes_only_its_four_calls(scaled[size])
        _the_approval_reads_the_plan_once(scaled[size])
        recipes = [copied.run for copied in scaled[size].copies] + [scaled[size].approval]
        for recipe in recipes:
            assert recipe.requests, recipe.ran.args
            assert _whole_board(recipe.requests) == [], (size, recipe.ran.args)

    large, small = (scaled[size] for size in SIZES)
    for larger, smaller in zip(large.copies, small.copies, strict=True):
        assert _by_operation(larger.run.requests) == _by_operation(smaller.run.requests), (
            _by_operation(larger.run.requests),
            _by_operation(smaller.run.requests),
        )
    assert _by_operation(large.approval.requests) == _by_operation(small.approval.requests), (
        _by_operation(large.approval.requests),
        _by_operation(small.approval.requests),
    )

    for copied in large.copies:
        _printed_cost(copied.run, "copy-plan")
    _printed_cost(large.approval, "approve-design")
    # llmlint: ignore-end[e2e_not_mocked]
    # llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] The uncached `test-host`
# target runs this journey alone and the `test` target deselects it, because the wall clock it
# records for `plan-approval-seconds` has to be this run's rather than a cache's.
# llmlint: ignore-block[e2e_not_mocked] The same one doubled boundary as the journey above.
# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] The task timing these
# recipes requires their seconds be recorded through the existing uncached
# `board-copy-approval:test-host` target, which selects this one journey while the project's
# cached `test` target deselects it; moving it to a project of its own would replace the target
# that task names, so the split stays this project's until a task asks for that move.
# llmlint: ignore-block[budgets_reuse_gate_telemetry] The task registering this budget
# requires `plan-approval-seconds` to be recorded by the uncached `test-host` target, which
# runs only this journey while `test` deselects it, so its wall clock is this run's and never a
# cache's. The journey above cannot be that target's: it also copies, re-copies and approves at
# 20,000 items, and its `test` target is cached. So this repeats the 2,000-item copies and
# approval to time them, and records nothing the journey above does.
#: What each timed copy is printed as, and the seconds budget it is recorded under, in the order
#: :func:`_copied_and_approved` makes them: the first copy, the re-copy after one task was
#: regenerated, and the unchanged re-copy.
COPY_RUNS = {
    "copy-plan first": "plan-copy-seconds-first",
    "copy-plan changed": "plan-copy-seconds-changed",
    "copy-plan unchanged": "plan-copy-seconds-unchanged",
}


def test_approving_a_plan_at_the_plans_boards_scale_records_the_time_a_person_waits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every copy and the approval at 2,000 items, timed for real, plus each request's latency.

    Nothing sleeps for the added latency: the double answers at once, so each recipe's real
    elapsed time is its process starts, its own steps and any wait the store really sleeps,
    and each request the double logged is charged the spike's latency for its kind. Each copy
    lands the plan reading it once, and the approval admits it; where every second went is
    recorded beside each figure, the store's start-up timed by a trivial invocation of it.
    """
    root = tmp_path / str(PLANS_BOARD_SCALE)
    scaled = _copied_and_approved(root, monkeypatch, PLANS_BOARD_SCALE)
    for copied in scaled.copies:
        _the_plan_landed_whole(copied, scaled)
        _the_copy_reads_the_plan_once(copied)
    _the_revisions_landed_in_place(scaled)
    _the_copies_send_only_what_the_store_needs(scaled)
    _the_approval_admits_the_copy(scaled)
    _the_approval_makes_only_its_four_calls(scaled)
    _the_approval_reads_the_plan_once(scaled)

    startup = _store_startup(root, _rendering_environment(root / "authoring"))
    print(f"store start-up and loading: {startup:.3f}s per invocation")
    for (named, budget), copied in zip(COPY_RUNS.items(), scaled.copies, strict=True):
        _recorded_seconds(budget, copied.run, startup)
        _printed_cost(copied.run, named)
    _recorded_seconds("plan-approval-seconds", scaled.approval, startup)
    _printed_cost(scaled.approval, "plan-approval-seconds")
    # llmlint: ignore-end[budgets_reuse_gate_telemetry]
    # llmlint: ignore-end[e2e_not_mocked]
    # llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
    # llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]
