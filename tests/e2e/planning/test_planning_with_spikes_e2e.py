"""A plan that needs a measurement, planned through its spikes, gated, launched and closed.

One sequence on one bench (`planning_bench`), every step through the real recipe:

1. `just plan <brief> board` drafts a two-task plan and, from `templates/planning/spikes.md.j2`,
   its spikes project with two lifecycle spikes against a scratch identity, which the draft's
   `just check-plan` accepts — the bench runs with `ONEPIPELINE_REQUIRE_RENDERED` removed,
   so that check and every launch read the rendered-only answer this host configures. The planning
   run's `spikes` step starts the spikes run in the same flow; with nothing attested, its
   spikes dispatch while the brief writer, held unfinished, still runs. Released, the
   writer raises one `check-in` naming the brief, the plan and each spike, and no human
   item, while the second spike, still held, runs. Each spike preserves its branch on the
   identity's origin; the spikes project's `finalize` runs behind every spike, and since no
   task's `spikes` answer names one, reworks nothing and leaves every task as drafted; the
   spikes run completes on its own, and the planning run goes on to check, review (attesting
   every task and the project), write the design document, copy it all to the bench's
   `board` source, and wait at `approve-design`.
2. The orchestrate recipe on the board copy is refused by the registered launch check
   naming the missing `design-approval`, and accepted once `just approve-design` records it.
3. Three more launch-gate cases on that copy: its description edited after review, the
   planner graph's judge configuration changed so the bar moves, and its design document
   edited after approval. Each is refused naming the record and its reason, and accepted
   once the review or approval is recorded again.
4. Every launch the gate accepts in 2 and 3 ends with a failed node — every worker turn lost
   at the doubled provider — so the failure hook runs, no success branch fires, and the
   spike branch is still preserved after them. The plan's next launch succeeds, and its
   success hook discards the spike branch (gone from the origin and from `onepipeline
   unpublished`) while a branch preserved by a node of another project is kept: the
   board copy's run is grouped under its authoring plan, whose `<plan>-spikes` project's
   run preserved it. What the hook does with a branch the discard refuses is
   `tests/run_end_hooks/`'s journey.

Human items go to the bench's `humans` local source, the plans to its `authoring` and
`board` local sources, follow-up drafts to an empty local drafts root, and every `onevcs`
record to the scratch identity's own registry — none of them the host's.
"""

from __future__ import annotations

import difflib
import json
import os
import shlex
import shutil
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
from conftest import git
from drafting import (
    DRAFT_MARKER,
    FINALIZE_MARKER,
    SPIKE_MARKER,
    SPIKES,
    brief,
    changed_record,
    drafted_record,
    drafting,
    rework_record,
    spikes_check_record,
)
from planning_bench import (
    BOARD,
    REQUIRE_RENDERED_ENV,
    Bench,
    bench,
    just,
    run,
    runs_of,
    status,
    store,
    store_json,
)
from scratch_identity import GIT_IDENTITY, Identity, onevcs_in, seeded
from waits import timeout as e2e_timeout

pytestmark = [pytest.mark.usefixtures("module_tmpdir"), pytest.mark.xdist_group("planning-spikes")]

#: The alias the scratch identity's execution checkout is registered under.
EXECUTION_ALIAS = "execution"
#: The spikes the draft adds: the first runs unheld, the second is held until released.
SPIKE_IDS = ("spike-health", "spike-load")
#: Every member the spikes template declares, with the spikes the draft adds.
SPIKES_NODES = ["spike-brief", "finalize", *SPIKE_IDS]
#: Every member the planning template declares.
PLANNING_NODES = [
    "refresh",
    "draft",
    "spikes",
    "check",
    "review",
    "design",
    "copy",
    "approve-design",
]
#: The project whose run another branch is preserved under: titled as a plan's spikes project
#: is, so its run's id reads like this plan's spikes run and only its group tells them apart.
OTHER_PROJECT = "authoring:another-plan-spikes"
#: How long the plan recipe may take to return.
SETTLE_SECONDS = 900.0


class Launch(NamedTuple):
    """One orchestrate recipe invocation: its result and the run it started, if any."""

    result: subprocess.CompletedProcess[str]
    run: str | None


class Sequence(NamedTuple):
    """Everything the sequence did, read back before the bench goes."""

    bench: Bench
    identity: Identity
    native: str
    planned: subprocess.CompletedProcess[str]
    spikes_checked: str
    checklist: str
    planning_run: str
    spikes_run: str
    while_briefing: dict[str, object]
    items_while_briefing: list[dict[str, object]]
    while_spiking: dict[str, object]
    spikes_items: list[dict[str, object]]
    spikes_surfaces: list[dict[str, object]]
    planning_items: list[dict[str, object]]
    spikes: dict[str, object]
    #: Each plan task's content as the draft wrote it, and as it stood once planning ended.
    drafted: dict[str, str]
    planned_content: dict[str, str]
    #: What the doubled finalize recorded: the `changed_dependencies` it read, and its rework.
    finalize_changed: str
    finalize_rework: str
    spikes_launch: dict[str, object]
    planning: dict[str, object]
    flow: str
    attestations: dict[str, object]
    spike_branch: str
    unapproved: subprocess.CompletedProcess[str]
    approved: Launch
    description_edited: subprocess.CompletedProcess[str]
    description_reviewed: Launch
    bar_before: str
    bar_after: str
    bar_moved: subprocess.CompletedProcess[str]
    bar_reviewed: Launch
    design_edited: subprocess.CompletedProcess[str]
    failed: Launch
    failed_log: str
    remote_after_failure: frozenset[str]
    unpublished_after_failure: list[dict[str, object]]
    succeeded: Launch
    success_log: str
    remote_after_success: frozenset[str]
    unpublished_after_success: list[dict[str, object]]
    other_branch: str


def _nodes(answer: dict[str, object]) -> dict[str, str]:
    nodes = answer.get("nodes")
    assert isinstance(nodes, list), answer
    return {str(node["id"]): str(node.get("status")) for node in nodes if isinstance(node, dict)}


def _only_run(bench: Bench, suffix: str) -> str:
    found = [one for one in runs_of(bench) if one.endswith(suffix)]
    assert len(found) == 1, f"expected one run ending {suffix!r}, found {runs_of(bench)}"
    return found[0]


def _items(bench: Bench, run_id: str) -> list[dict[str, object]]:
    answered = run(
        bench,
        "scripts/onepipeline.sh",
        "human-items",
        "list",
        "--run",
        run_id,
        "--json",
        seconds=120,
    )
    assert answered.returncode == 0, answered.stdout + answered.stderr
    items = json.loads(answered.stdout)
    assert isinstance(items, list), items
    return items


def _bar(bench: Bench) -> str:
    """The planner member's current review bar, as the review and the launch check read it."""
    answered = run(
        bench,
        ".venv/bin/oneagentgraph",
        "run",
        "graphs/planner.yaml",
        "--judge-only",
        "--member",
        "worker",
        "--bar-fingerprint",
        seconds=120,
    )
    assert answered.returncode == 0, answered.stdout + answered.stderr
    return answered.stdout.strip()


def _launch(bench: Bench, plan: str, environment: dict[str, str] | None = None) -> Launch:
    """The orchestrate recipe over ``plan``, attached to settlement; the run it started."""
    before = set(runs_of(bench))
    on = bench if environment is None else bench._replace(environment=environment)
    result = just(on, "orchestrate", plan, "--dag-graph", "off", seconds=1500)
    started = [one for one in runs_of(bench) if one not in before and not one.startswith(".")]
    main = [one for one in started if not one.endswith("-follow-ups")]
    return Launch(result, main[0] if len(main) == 1 else None)


def _remote_branches(identity: Identity) -> frozenset[str]:
    """Every branch on the identity's origin."""
    listed = git("ls-remote", "--heads", "origin", cwd=identity.publication)
    return frozenset(
        line.split("refs/heads/", 1)[1] for line in listed.splitlines() if "refs/heads/" in line
    )


def _unpublished(bench: Bench) -> list[dict[str, object]]:
    """`onepipeline unpublished --host`'s rows, over the scratch registry."""
    answered = run(
        bench, "scripts/onepipeline.sh", "unpublished", "--host", "--format", "json", seconds=120
    )
    # 7 is the verdict `owed`: rows are listed, which is what is read here.
    assert answered.returncode in (0, 7), answered.stdout + answered.stderr
    rows = json.loads(answered.stdout).get("rows")
    assert isinstance(rows, list), answered.stdout
    return rows


def _another_spikes_run(bench: Bench) -> str:
    """Run another plan's one-command spikes project to completion; answer its run id."""
    body = bench.tmp / "another-spikes.md"
    body.write_text("Measures nothing.\n", encoding="utf-8")
    for created in (
        store(
            bench,
            "project",
            "create",
            "authoring",
            "--id",
            OTHER_PROJECT.split(":")[1],
            "--title",
            "Another plan spikes",
            "--metadata",
            "onepipeline.schema_version=3",
        ),
        store(
            bench,
            "task",
            "create",
            "authoring",
            "--project",
            OTHER_PROJECT,
            "--title",
            "Probe",
            "--body-file",
            str(body),
            "--metadata",
            'onepipeline.id="probe"',
            "--metadata",
            'onepipeline.kind="command"',
            "--metadata",
            'onepipeline.command=["true"]',
        ),
    ):
        assert created.returncode == 0, created.stdout + created.stderr
    started = run(
        bench,
        "scripts/onepipeline.sh",
        "start",
        OTHER_PROJECT,
        "--launch-config",
        "config/onepipeline.workflows.yaml",
        "--dag-graph",
        "off",
    )
    assert started.returncode == 0, started.stdout + started.stderr
    return str(json.loads(started.stdout.strip().splitlines()[-1])["run_id"])


def _preserve_another(bench: Bench, identity: Identity) -> str:
    """Preserve a branch the way a node of another project's run leaves one; answer it."""
    other_run = _another_spikes_run(bench)
    assert other_run.endswith("-spikes"), other_run
    opened = onevcs_in(
        identity,
        "session",
        "open",
        str(identity.publication),
        "--branch-name",
        "another-plan/spike-other",
        "--label",
        f"run={other_run}",
        "--label",
        "node=spike-other",
    )
    assert opened.returncode == 0, opened.stdout + opened.stderr
    session = json.loads(opened.stdout)
    worktree = Path(session["worktree"])
    (worktree / "other.txt").write_text("another plan's measurement\n", encoding="utf-8")
    git("add", "-A", cwd=worktree)
    git(*GIT_IDENTITY, "commit", "-qm", "feat: another plan's spike", cwd=worktree)
    preserved = onevcs_in(
        identity, "preserve", "--repo", str(identity.publication), session["branch"]
    )
    assert preserved.returncode == 0, preserved.stdout + preserved.stderr
    closed = onevcs_in(identity, "session", "close", session["token"])
    assert closed.returncode == 0, closed.stdout + closed.stderr
    return str(session["branch"])


def _surfaces(bench: Bench, run_id: str) -> list[dict[str, object]]:
    """Every planner surface waiting on ``run_id``, read as a manager reads them: `next`."""
    handed: list[dict[str, object]] = []
    for _ in range(20):
        answered = run(bench, "scripts/onepipeline.sh", "next", run_id, seconds=120)
        assert answered.returncode == 0, answered.stdout + answered.stderr
        if not answered.stdout.strip():
            break
        surface = json.loads(answered.stdout).get("surface")
        if not isinstance(surface, dict):
            break
        handed.append(surface)
    return handed


def _until(planning: subprocess.Popen[str], what: str, found: Callable[[], bool]) -> None:
    """Poll ``found`` until it holds, failing by ``what`` if the plan recipe returns first."""
    deadline = time.monotonic() + e2e_timeout(SETTLE_SECONDS)
    while not found():
        assert planning.poll() is None, f"the plan recipe returned before {what}"
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(2)


def _render(bench: Bench, template: str, verb: str, record: str, *answers: str) -> None:
    """Regenerate ``record`` from ``template`` with ``answers``, as a manager edits one."""
    rendered = run(
        bench,
        "bash",
        "-c",
        f"scripts/onepipeline.sh template resolve {template} --json | "
        f".venv/bin/onetaskgraph {verb} render {record} --template-loader - "
        + " ".join(f"--var {shlex.quote(answer)}" for answer in answers)
        + " --no-interactive",
    )
    assert rendered.returncode == 0, rendered.stdout + rendered.stderr


def _hook_log(bench: Bench, run_id: str | None, hook: str) -> str:
    path = bench.runs / str(run_id) / "hooks" / f"{hook}.log"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


@pytest.fixture(scope="module")
def sequence(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Iterator[Sequence]:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp = tmp_path_factory.mktemp("planning-spikes")
    identity = seeded(tmp / "identity", execution=EXECUTION_ALIAS)
    bench_ = bench(
        tmp,
        oneharness_bin,
        "e2e-planning-spikes",
        rendered_only_as_configured=True,
        ONEVCS_HOME=str(identity.home),
        **identity.environment,
    )
    native = f"plan-sp-{os.getpid()}"
    hold = tmp / "hold"
    hold.mkdir()
    drafting(
        bench_,
        native,
        spikes=(str(identity.publication), EXECUTION_ALIAS),
        spike_ids=SPIKE_IDS,
        hold=hold,
    )
    checklist = run(
        bench_,
        "llmlint",
        "rules",
        "-c",
        "config/plan-checklist.llmlint.yml",
        "--files",
        f"projects/{native}.md",
        f"tasks/{native}-task.md",
        "--format",
        "markdown",
        seconds=120,
    )
    assert checklist.returncode == 0, checklist.stdout + checklist.stderr

    out, err = tmp / "plan.out", tmp / "plan.err"
    with out.open("w") as stdout, err.open("w") as stderr:
        planning_process = subprocess.Popen(  # noqa: S603 - the copy's own recipe
            ["just", "plan", brief(bench_, native), BOARD],  # noqa: S607 - `just` as an operator types it, from the bench PATH
            cwd=bench_.checkout,
            env=bench_.environment,
            stdout=stdout,
            stderr=stderr,
            text=True,
        )
        try:
            # The brief writer and the second spike are held: every spike must dispatch
            # while the writer still runs, with nothing attested.
            def briefing() -> bool:
                found = [one for one in runs_of(bench_) if one.endswith("-spikes")]
                if not found:
                    return False
                nodes = _nodes(status(bench_, found[0]))
                return nodes.get("spike-brief") == "running" and all(
                    nodes.get(spike) in ("running", "done") for spike in SPIKE_IDS
                )

            _until(planning_process, "every spike dispatched beside the brief writer", briefing)
            spikes_run = _only_run(bench_, "-spikes")
            while_briefing = status(bench_, spikes_run)
            items_while_briefing = _items(bench_, spikes_run)
            (hold / "spike-brief").touch()
            surfaces: list[dict[str, object]] = []

            def checked_in() -> bool:
                surfaces.extend(_surfaces(bench_, spikes_run))
                return any(one.get("kind") == "check-in" for one in surfaces)

            _until(planning_process, "the brief writer's check-in", checked_in)
            while_spiking = status(bench_, spikes_run)
            (hold / SPIKE_IDS[-1]).touch()
            planning_process.wait(timeout=e2e_timeout(SETTLE_SECONDS))
        finally:
            if planning_process.poll() is None:
                planning_process.kill()
                planning_process.wait()
    planned = subprocess.CompletedProcess(
        planning_process.args, planning_process.returncode, out.read_text(), err.read_text()
    )
    checked_record = spikes_check_record(bench_)
    spikes_checked = checked_record.read_text("utf-8") if checked_record.exists() else ""
    planning_run = _only_run(bench_, "-planning")
    spikes = status(bench_, spikes_run)
    drafted = {node: _read(drafted_record(bench_, node)) for node in ("route", "docs")}
    planned_content = {node: _content(bench_, f"authoring:{native}", node) for node in drafted}
    finalize_changed = _read(changed_record(bench_))
    finalize_rework = _read(rework_record(bench_))
    spikes_items = _items(bench_, spikes_run)
    spikes_surfaces = surfaces + _surfaces(bench_, spikes_run)
    planning_items = _items(bench_, planning_run)
    spikes_launch = json.loads(
        (bench_.runs / spikes_run / "launch.json").read_text(encoding="utf-8")
    )
    planning = status(bench_, planning_run)
    planning_launch = json.loads(
        (bench_.runs / planning_run / "launch.json").read_text(encoding="utf-8")
    )
    flow = str(planning_launch.get("flow") or "")
    plan = f"authoring:{native}"
    attestations = store_json(
        bench_,
        "project",
        "attestation",
        "check",
        plan,
        "--kind",
        "plan-review",
        "--tasks",
        "--bar",
        _bar(bench_),
    )
    spike_branch = next(
        (one for one in _remote_branches(identity) if one.endswith("/spike-health")), ""
    )
    other_branch = _preserve_another(bench_, identity)

    copy = f"{BOARD}:{native}"
    # Every launch the gate accepts before the last ends with a failed node — each worker
    # turn lost at the doubled provider — so none of them succeeds, and the plan's spike
    # branch stays preserved until the one launch that does.
    losing = {
        **bench_.environment,
        "FAKE_BACKEND_OBSERVER_MEMBER": "worker",
        "FAKE_BACKEND_OBSERVER_LOSES": "1",
    }
    unapproved = just(bench_, "orchestrate", copy, "--dag-graph", "off")
    assert just(bench_, "approve-design", copy, planning_run).returncode == 0
    approved = _launch(bench_, copy, losing)

    _render(bench_, "plan-description", "project", copy, "overview=Add a health route; poll it.")
    description_edited = just(bench_, "orchestrate", copy, "--dag-graph", "off")
    assert just(bench_, "review-plan", copy).returncode == 0
    description_reviewed = _launch(bench_, copy, losing)

    bar_before = _bar(bench_)
    graph = bench_.checkout / "graphs" / "planner.yaml"
    graph.write_text(
        graph.read_text(encoding="utf-8").replace("label: plan-checklist", "label: checklist"),
        encoding="utf-8",
    )
    bar_after = _bar(bench_)
    bar_moved = just(bench_, "orchestrate", copy, "--dag-graph", "off")
    assert just(bench_, "review-plan", copy).returncode == 0
    bar_reviewed = _launch(bench_, copy, losing)

    design = next(
        str(one["id"])
        for one in store_json(bench_, "document", "list", "--source", BOARD, "--project", copy)[
            "items"
        ]
        if one["item"]["metadata"]["onetaskgraph.template"]["template"] == "onepipeline:design-doc"
    )
    # A board source stores no template answers, so an edit there answers every one.
    graphed = store(
        bench_, "project", "graph", copy, "--format", "json", "--group-by", "orchestrator.unit"
    )
    assert graphed.returncode == 0, graphed.stdout + graphed.stderr
    _render(
        bench_,
        "design-doc",
        "document",
        design,
        "what=W, polled by operators.",
        "why=Y",
        "architecture=A",
        "units=[]",
        'acceptance_criteria=["Operators can poll the route."]',
        f"graph={graphed.stdout.strip()}",
    )
    design_edited = just(bench_, "orchestrate", copy, "--dag-graph", "off")
    assert just(bench_, "approve-design", copy).returncode == 0
    failed = _launch(bench_, copy, losing)
    failed_log = _hook_log(bench_, failed.run, "failure") + _hook_log(bench_, failed.run, "success")
    remote_after_failure = _remote_branches(identity)
    unpublished_after_failure = _unpublished(bench_)

    # The plan's next launch succeeds, and its success hook discards the spike branch.
    succeeded = _launch(bench_, copy)
    success_log = _hook_log(bench_, succeeded.run, "success")
    remote_after_success = _remote_branches(identity)
    unpublished_after_success = _unpublished(bench_)
    yield Sequence(
        bench_,
        identity,
        native,
        planned,
        spikes_checked,
        checklist.stdout,
        planning_run,
        spikes_run,
        while_briefing,
        items_while_briefing,
        while_spiking,
        spikes_items,
        spikes_surfaces,
        planning_items,
        spikes,
        drafted,
        planned_content,
        finalize_changed,
        finalize_rework,
        spikes_launch,
        planning,
        flow,
        attestations,
        spike_branch,
        unapproved,
        approved,
        description_edited,
        description_reviewed,
        bar_before,
        bar_after,
        bar_moved,
        bar_reviewed,
        design_edited,
        failed,
        failed_log,
        remote_after_failure,
        unpublished_after_failure,
        succeeded,
        success_log,
        remote_after_success,
        unpublished_after_success,
        other_branch,
    )
    for one in runs_of(bench_):
        if not one.startswith("."):
            just(bench_, "stop", one, seconds=120)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _content(bench: Bench, plan: str, node: str) -> str:
    """The content of ``plan``'s task ``node``, as the store shows it, newline-terminated as
    the draft's record of it is."""
    listed = store_json(bench, "task", "list", "--source", "authoring", "--project", plan)
    items = listed.get("items")
    assert isinstance(items, list), listed
    found = [
        one["item"]["content"]
        for one in items
        if isinstance(one, dict) and one["item"]["metadata"].get("onepipeline.id") == node
    ]
    assert len(found) == 1, (node, listed)
    return f"{found[0]}\n"


def _refused(result: subprocess.CompletedProcess[str], *named: str) -> None:
    assert result.returncode != 0, result.stdout + result.stderr
    for text in named:
        assert text in result.stderr, f"{text!r} not in:\n{result.stderr}"


def _accepted(launch: Launch) -> None:
    assert launch.run is not None, launch.result.stdout + launch.result.stderr
    assert "plan-attestation-check" not in launch.result.stderr, launch.result.stderr


def test_the_planning_run_holds_the_planning_templates_members(sequence: Sequence) -> None:
    assert sequence.planned.returncode == 0, sequence.planned.stdout + sequence.planned.stderr
    assert sorted(_nodes(sequence.planning)) == sorted(PLANNING_NODES), sequence.planning


def test_the_drafts_dispatched_task_carries_the_checklist_llmlint_printed(
    sequence: Sequence,
) -> None:
    turns = [
        json.loads(line)
        for line in sequence.bench.turn_log.read_text(encoding="utf-8").splitlines()
    ]
    drafts = [turn for turn in turns if DRAFT_MARKER in str(turn.get("prompt"))]
    assert drafts, "no dispatched turn carried the draft's task"
    rules = sequence.checklist.strip()
    assert "plan_criteria_draft_needs_its_grant" in rules, rules
    assert rules in str(drafts[0]["prompt"]), str(drafts[0]["prompt"])[-2000:]


def test_the_drafted_spikes_project_passes_check_plan_as_this_host_configures_it(
    sequence: Sequence,
) -> None:
    """The draft's `just check-plan` of its spikes project, `spike-brief` member and all, ran
    with `ONEPIPELINE_REQUIRE_RENDERED` absent and was accepted."""
    assert REQUIRE_RENDERED_ENV not in sequence.bench.environment
    checked = sequence.spikes_checked
    assert checked.rstrip().endswith("exit 0"), checked
    assert f"authoring:{sequence.native}-spikes: accepted" in checked, checked


def test_the_spikes_run_starts_in_the_planning_runs_flow(sequence: Sequence) -> None:
    assert sequence.flow, "the planning run's launch record names no flow"
    assert sequence.spikes_launch.get("flow") == sequence.flow, sequence.spikes_launch
    assert sorted(_nodes(sequence.spikes)) == sorted(SPIKES_NODES), sequence.spikes


def test_the_spikes_dispatch_while_the_brief_writer_still_runs(sequence: Sequence) -> None:
    # Nothing in the sequence attests a node: the spikes ran because nothing held them.
    nodes = _nodes(sequence.while_briefing)
    assert nodes["spike-brief"] == "running", nodes
    assert all(nodes[spike] in ("running", "done") for spike in SPIKE_IDS), nodes
    assert sequence.items_while_briefing == [], sequence.items_while_briefing


def test_the_spikes_run_completes_with_nothing_attested_and_no_human_item(
    sequence: Sequence,
) -> None:
    assert all(state == "done" for state in _nodes(sequence.spikes).values()), sequence.spikes
    ending = sequence.spikes.get("ending")
    assert isinstance(ending, dict) and ending.get("kind") == "complete", sequence.spikes
    assert sequence.spikes_items == [], sequence.spikes_items


def test_the_brief_writer_raises_one_check_in_naming_the_brief_and_each_spike(
    sequence: Sequence,
) -> None:
    check_ins = [one for one in sequence.spikes_surfaces if one.get("kind") == "check-in"]
    assert len(check_ins) == 1, sequence.spikes_surfaces
    (check_in,) = check_ins
    assert check_in.get("blocking") is not True, check_in
    message = str(check_in.get("message"))
    assert f"{sequence.native}-spike-brief" in message, message
    assert f"authoring:{sequence.native}" in message, message
    for spike in SPIKE_IDS:
        assert f"- {spike}: {SPIKES[spike].measures}" in message, message
    # It reached the manager while the held spike still ran.
    assert _nodes(sequence.while_spiking)[SPIKE_IDS[-1]] == "running", sequence.while_spiking


def test_approve_design_is_the_planning_runs_one_human_wait(sequence: Sequence) -> None:
    waiting = [node for node, state in _nodes(sequence.planning).items() if state == "waiting"]
    assert waiting == ["approve-design"], sequence.planning
    assert len(sequence.planning_items) == 1, sequence.planning_items


def test_finalize_runs_in_the_spikes_run_after_every_spike(sequence: Sequence) -> None:
    ending = sequence.spikes.get("ending")
    assert isinstance(ending, dict) and ending.get("kind") == "complete", sequence.spikes
    assert _nodes(sequence.spikes)["finalize"] == "done", sequence.spikes
    prompts = [
        str(json.loads(line).get("prompt"))
        for line in sequence.bench.turn_log.read_text(encoding="utf-8").splitlines()
    ]
    spiked = [index for index, prompt in enumerate(prompts) if SPIKE_MARKER in prompt]
    finalized = [index for index, prompt in enumerate(prompts) if FINALIZE_MARKER in prompt]
    assert spiked and finalized, (len(spiked), len(finalized))
    assert min(finalized) > max(spiked), (spiked, finalized)


def test_finalize_reworks_no_task_when_no_task_names_a_spike(sequence: Sequence) -> None:
    """Finalize read every spike as changed, and with no task's `spikes` answer naming one it
    reworked nothing: an empty set of spike answers is never read as a first finalize."""
    changed = sequence.finalize_changed.split()
    assert changed[0] == sequence.spikes_run, sequence.finalize_changed
    assert sorted(json.loads(changed[1])) == sorted(SPIKE_IDS), sequence.finalize_changed
    assert sequence.finalize_rework == "", sequence.finalize_rework
    assert all(sequence.drafted.values()), sequence.drafted
    for node, drafted in sequence.drafted.items():
        planned = sequence.planned_content[node]
        assert "## Spike evidence" not in planned, (node, planned)
        changed_lines = "".join(
            difflib.unified_diff(drafted.splitlines(True), planned.splitlines(True), n=0)
        )
        assert planned == drafted, (node, changed_lines)


def test_the_spike_preserved_its_branch_on_the_origin(sequence: Sequence) -> None:
    assert sequence.spike_branch, "the spike left no branch on the identity's origin"


def test_the_review_step_attested_every_task_and_the_project(sequence: Sequence) -> None:
    nodes = _nodes(sequence.planning)
    assert all(nodes[one] == "done" for one in PLANNING_NODES[:-1]), nodes
    assert nodes["approve-design"] == "waiting", nodes
    tasks = sequence.attestations.get("tasks")
    assert isinstance(tasks, list) and len(tasks) == 2, sequence.attestations
    assert {str(one["state"]) for one in tasks} == {"valid"}, tasks
    assert sequence.attestations.get("state") in (None, "valid"), sequence.attestations


def test_the_design_document_was_copied_to_the_board(sequence: Sequence) -> None:
    copy = f"{BOARD}:{sequence.native}"
    documents = store_json(sequence.bench, "document", "list", "--source", BOARD, "--project", copy)
    templates = [
        one["item"]["metadata"]["onetaskgraph.template"]["template"] for one in documents["items"]
    ]
    assert "onepipeline:design-doc" in templates, documents


def test_an_unapproved_copy_is_refused_naming_the_missing_approval(sequence: Sequence) -> None:
    _refused(sequence.unapproved, "design-approval is absent")
    _accepted(sequence.approved)


def test_an_edited_description_is_refused_until_it_is_re_reviewed(sequence: Sequence) -> None:
    _refused(sequence.description_edited, f"{BOARD}:{sequence.native}: plan-review is void")
    assert "design-approval" not in sequence.description_edited.stderr
    _accepted(sequence.description_reviewed)


def test_a_moved_bar_is_refused_until_the_plan_is_re_reviewed(sequence: Sequence) -> None:
    assert sequence.bar_before != sequence.bar_after
    _refused(sequence.bar_moved, "plan-review is void")
    _accepted(sequence.bar_reviewed)


def test_an_edited_design_document_is_refused_until_it_is_re_approved(sequence: Sequence) -> None:
    _refused(sequence.design_edited, "design-approval is void")
    assert "plan-review" not in sequence.design_edited.stderr
    _accepted(sequence.failed)


def test_a_run_ending_with_a_failed_node_keeps_the_spike_branch(sequence: Sequence) -> None:
    settled = json.loads(sequence.failed.result.stdout.strip().splitlines()[-1])
    assert settled["settlement"] != "complete", settled
    assert "discarded" not in sequence.failed_log, sequence.failed_log
    assert not (sequence.bench.runs / str(sequence.failed.run) / "hooks" / "success.log").exists()
    assert sequence.spike_branch in sequence.remote_after_failure
    assert sequence.spike_branch in {
        str(row.get("branch")) for row in sequence.unpublished_after_failure
    }


def test_a_successful_run_discards_the_spike_branch_and_keeps_another_projects(
    sequence: Sequence,
) -> None:
    settled = json.loads(sequence.succeeded.result.stdout.strip().splitlines()[-1])
    assert settled["settlement"] == "complete", settled
    assert sequence.spike_branch in sequence.success_log, sequence.success_log
    assert "could not be read" not in sequence.success_log, sequence.success_log
    assert sequence.other_branch not in sequence.success_log, sequence.success_log
    assert sequence.spike_branch not in sequence.remote_after_success
    branches = {str(row.get("branch")) for row in sequence.unpublished_after_success}
    assert sequence.spike_branch not in branches, branches
    assert sequence.other_branch in sequence.remote_after_success
    assert sequence.other_branch in branches, branches
