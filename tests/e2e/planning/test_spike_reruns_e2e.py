"""A plan's spikes re-run exactly when their definitions change, and stale evidence is refused.

One sequence on one bench (`planning_bench`), every step through the real recipe, only the
paid model doubled:

1. `just plan <brief> board` drafts a two-task plan and its spikes project with two lifecycle
   spikes against a scratch identity, `spike-health` and `spike-visual`, whose definition
   names three visual entries. Each spike runs once; the visual one writes its report with an
   image per entry. The doubled finalize names a spike in each task's `spikes` answer, and the
   doubled design writer restates the visual report in the design document copied to the
   board.
2. The manager regenerates `spike-visual` — one entry changed, one added, one removed — and
   the task built on it, then runs `just finish-plan`. A fresh spikes run dispatches only
   that spike, continuing its preserved branch; its worker, told so by the dispatch appendix,
   re-renders its report giving only the changed images. `finalize` runs in its own run, and
   the board's design document carries the refreshed entries beside the unchanged ones, byte
   for byte.
3. `just finish-plan` with nothing edited starts no spikes run and holds no `finalize`.
4. Only the task built on `spike-visual` is regenerated: the flow's `check` step refuses it
   naming the spike and both next steps, until `just spike-unaffected` records why.
5. The orchestrate recipe on the board copy is not refused by the spike-evidence check while
   the records are valid, and is refused, naming the same next steps, once the board copy's
   task is edited.
6. `spike-health` is regenerated and, while its re-run is under way, regenerated again: the
   `spikes` step fails naming it and leaves its record at `launched`.
7. A second plan whose spike ran the way spikes ran before these records were kept — settled
   `done` with no record — is re-finished: the spike is backfilled, named, and not run again.

Plans go to the bench's `authoring` and `board` local sources, human items to its `humans`
source, and every `onevcs` record to the scratch identity's own registry.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import struct
import subprocess
import time
import zlib
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
from conftest import git
from drafting import (
    DESIGN_MARKER,
    FINALIZE_MARKER,
    RESOLVE,
    SPIKE_CRITERIA,
    SPIKE_WHY,
    SPIKES,
    STORE,
    brief,
    design_commands,
    drafting,
    plan_title,
    spike_what,
)
from planning_bench import (
    BOARD,
    Bench,
    bench,
    just,
    mark,
    run,
    runs_of,
    status,
    store,
    store_json,
)
from scratch_identity import Identity, seeded
from waits import timeout as e2e_timeout

pytestmark = [pytest.mark.usefixtures("module_tmpdir"), pytest.mark.xdist_group("spike-reruns")]

#: The alias the scratch identity's execution checkout is registered under.
EXECUTION_ALIAS = "execution"
HEALTH, VISUAL = "spike-health", "spike-visual"
#: The docs task builds on both spikes, so a re-finish editing one shows `rework` naming only it.
DOCS_SPIKES = (HEALTH, VISUAL)
#: The dispatch appendix's report-update rule, as a re-run spike's task must carry it.
REPORT_RULE = "A spike whose report already exists updates that report."
PRESERVE_RULE = "keep every other entry's answers and images exactly as they are"
#: How long a recipe returning at the design approval may take.
SETTLE_SECONDS = 1500.0


class Entry(NamedTuple):
    """One visual change the visual spike captures: its heading, words and image."""

    name: str
    description: str
    image: str


HEADER = Entry("Header", "The page's header names the service.", "header.png")
GREEN_BADGE = Entry("Badge", "A green badge shows the route is up.", "badge.png")
FOOTER = Entry("Footer", "The footer shows the version.", "footer.png")
BLUE_BADGE = Entry("Badge", "A blue badge shows the route is up.", "badge.png")
LEGEND = Entry("Legend", "A legend explains the badge.", "legend.png")
#: The visual spike's definition before and after the design review changed it.
FIRST_DEFINITION = "Capture the header, a green badge and the footer."
SECOND_DEFINITION = "Capture the header, a blue badge and a legend."
FIRST_ENTRIES = (HEADER, GREEN_BADGE, FOOTER)
SECOND_ENTRIES = (HEADER, BLUE_BADGE, LEGEND)
#: The docs task's `what` after each of the manager's regenerations of it.
DOCS_WITH_BLUE = "Do docs, showing the blue badge."
DOCS_REWORDED = "Do docs, showing the blue badge and its legend."
DOCS_ON_BOARD = "docs: document every badge"
UNAFFECTED_REASON = "Only the wording changed; the page it documents is the same."


def _png(seed: int) -> bytes:
    """A small, valid PNG whose pixels ``seed`` decides."""
    rows = b"".join(
        b"\x00" + bytes((seed * 37 + x * 13 + y * 7) % 256 for x in range(16)) for y in range(16)
    )

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = struct.pack(">I", zlib.crc32(kind + data))
        return struct.pack(">I", len(data)) + kind + data + crc

    header = struct.pack(">IIBBBBB", 16, 16, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def _images(root: Path, entries: tuple[Entry, ...], seed: int) -> list[Path]:
    """Each entry's image, written under ``root`` with its own pixels."""
    root.mkdir(parents=True)
    written = []
    for offset, entry in enumerate(entries):
        path = root / entry.image
        path.write_bytes(_png(seed + offset))
        written.append(path)
    return written


def _changes(entries: tuple[Entry, ...]) -> str:
    return json.dumps(
        [{"name": e.name, "description": e.description, "after": e.image} for e in entries]
    )


def _counting(bench: Bench, spike: str) -> str:
    """A command recording, in the bench, that ``spike``'s worker was dispatched in this run."""
    return f'echo "$ONEPIPELINE_RUN_ID" >> {shlex.quote(str(bench.tmp / f"dispatched-{spike}"))}'


def _commit(spike: str) -> str:
    """Commit one measurement on the spike's branch, distinct for each run."""
    return (
        f'mkdir -p spikes && echo "$ONEPIPELINE_RUN_ID" > spikes/{spike}.txt && git add spikes '
        f"&& git -c user.email=t@example.com -c user.name=t commit -qm 'feat: measure {spike}'"
    )


def _visual_commands(
    bench: Bench, native: str, entries: tuple[Entry, ...], assets: list[Path]
) -> list[str]:
    """The visual spike worker: commit, then write its report, or re-render the one it wrote
    with the whole merged answers and only ``assets`` given, as the appendix directs."""
    report = f"authoring:{native}-{VISUAL}-report"
    given = " ".join(f"--asset {shlex.quote(str(path))}" for path in assets)
    return [
        _counting(bench, VISUAL),
        _commit(VISUAL),
        "branch=$(git rev-parse --abbrev-ref HEAD)",
        f"cd {shlex.quote(str(bench.checkout))}",
        f'answers=(--var spike={VISUAL} --var "branch=$branch" '
        f"--var 'harness=spikes/{VISUAL}.txt' --var 'method=Mocked captures.' "
        f"--var 'candidates=[]' --var 'findings=[]' "
        f"--var {shlex.quote('visual_changes=' + _changes(entries))})",
        f"if {STORE} document show {report} --no-interactive >/dev/null 2>&1; "
        f"then {RESOLVE} spike-report --json | {STORE} document render {report} "
        f'--template-loader - "${{answers[@]}}" {given} --no-interactive; '
        f"else {RESOLVE} spike-report --json | {STORE} document create authoring "
        f"--project authoring:{native} --id {native}-{VISUAL}-report "
        f"--title 'Spike: {VISUAL}' --template-loader - "
        f'"${{answers[@]}}" {given} --no-interactive; fi',
    ]


def _health_commands(bench: Bench, hold: Path | None = None) -> list[str]:
    held = [] if hold is None else [f"until [ -e {shlex.quote(str(hold))} ]; do sleep 1; done"]
    return [_counting(bench, HEALTH), *held, _commit(HEALTH)]


def _spikes_answer(native: str, *spikes: str) -> str:
    return json.dumps(
        [
            {"spike": spike, "report": f"{native}-{spike}-report", "branch": f"{native}/{spike}"}
            for spike in spikes
        ]
    )


def _task_answers(node: str, what: str, native: str, *spikes: str) -> list[str]:
    """Every answer of one plan task as the draft and finalize render it."""
    return [
        f"what={what}",
        "why=Operators need it.",
        f'acceptance_criteria=["A request-level test drives {node} end to end."]',
        f"spikes={_spikes_answer(native, *spikes)}",
    ]


def _spike_answers(definition: str) -> list[str]:
    """Every answer of the visual or health spike task, none of which mentions a report."""
    return [f"what={definition}", f"why={SPIKE_WHY}", f"acceptance_criteria={SPIKE_CRITERIA}"]


def _finalize_commands(native: str) -> list[str]:
    """The first finalize: regenerate each task naming the spike its report bears on."""
    plan = f"authoring:{native}"
    commands = []
    for node, spikes in (("route", (HEALTH,)), ("docs", DOCS_SPIKES)):
        answers = " ".join(
            f"--var {shlex.quote(one)}"
            for one in _task_answers(node, f"Do {node}.", native, *spikes)
        )
        commands.append(
            f"id=$({STORE} task list --source authoring --project {plan} --json --no-interactive "
            f'| jq -r \'.items[] | select(.item.metadata["onepipeline.id"] == "{node}")'
            " | .id'); "
            f'{RESOLVE} plan-task --json | {STORE} task render "$id" --template-loader - '
            f"{answers} --no-interactive"
        )
    return commands


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


def _items(answer: dict[str, object]) -> list[dict[str, object]]:
    items = answer.get("items")
    assert isinstance(items, list), answer
    return items


def _metadata(item: dict[str, object]) -> dict[str, object]:
    inner = item["item"]
    assert isinstance(inner, dict), item
    found = inner["metadata"]
    assert isinstance(found, dict), item
    return found


def _task_id(bench: Bench, project: str, node: str) -> str:
    listed = store_json(
        bench, "task", "list", "--source", project.split(":")[0], "--project", project
    )
    found = [
        str(item["id"]) for item in _items(listed) if _metadata(item).get("onepipeline.id") == node
    ]
    assert len(found) == 1, (project, node, listed)
    return found[0]


def _settlement(bench: Bench, task: str) -> object:
    return _metadata(_items(store_json(bench, "task", "show", task))[0]).get(
        "onepipeline.settlement"
    )


def _record(bench: Bench, task: str, kind: str, bar: str | None = None) -> dict[str, object]:
    flags = ["--bar", bar] if bar else []
    return store_json(bench, "task", "attestation", "check", task, "--kind", kind, *flags)


def _nodes(answer: dict[str, object]) -> dict[str, str]:
    nodes = answer.get("nodes")
    assert isinstance(nodes, list), answer
    return {str(node["id"]): str(node.get("status")) for node in nodes if isinstance(node, dict)}


def _command_log(bench: Bench, run_id: str, node: str) -> str:
    """What a command step printed, as the engine keeps it under the run."""
    path = bench.runs / run_id / "commands" / f"{node}.log"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _of_kind(run_id: str, suffix: str) -> bool:
    """Whether ``run_id`` is a run of a project titled ending ``suffix``: the engine names a
    project's later runs with a `-<n>` after it."""
    return re.search(rf"{re.escape(suffix)}(?:-\d+)?$", run_id) is not None


def _new_runs(bench: Bench, before: set[str], suffix: str) -> list[str]:
    return sorted(one for one in runs_of(bench) if one not in before and _of_kind(one, suffix))


def _dispatched(bench: Bench, spike: str) -> list[str]:
    """The run of each dispatch the doubled worker of ``spike`` recorded, in order."""
    path = bench.tmp / f"dispatched-{spike}"
    return path.read_text(encoding="utf-8").split() if path.is_file() else []


class Branch(NamedTuple):
    """A branch the identity's origin keeps: its name and the commit at its head."""

    name: str
    head: str


def _branch_head(identity: Identity, spike: str) -> Branch:
    """The one branch the identity's origin keeps for ``spike``."""
    listed = git("ls-remote", "--heads", "origin", cwd=identity.publication)
    found = [
        Branch(line.split("refs/heads/", 1)[1], line.split()[0])
        for line in listed.splitlines()
        if line.endswith(f"/{spike}")
    ]
    assert len(found) == 1, listed
    return found[0]


class Shown(NamedTuple):
    """A document read back through the store: its content and each asset's bytes, by name."""

    content: str
    assets: dict[str, bytes]


def _document(bench: Bench, document: str) -> Shown:
    shown = store_json(bench, "document", "show", document)
    items = _items(shown)
    assert len(items) == 1, shown
    listed = shown.get("assets", [])
    assert isinstance(listed, list), shown
    assets = {str(asset["name"]): Path(asset["path"]).read_bytes() for asset in listed}
    inner = items[0]["item"]
    assert isinstance(inner, dict), shown
    return Shown(str(inner["content"]), assets)


def _template(provenance: object) -> str:
    """The template a record's `onetaskgraph.template` provenance names, or nothing."""
    return str(provenance.get("template")) if isinstance(provenance, dict) else ""


def _design_on_board(bench: Bench, native: str) -> Shown:
    listed = store_json(
        bench, "document", "list", "--source", BOARD, "--project", f"{BOARD}:{native}"
    )
    designs = [
        str(one["id"])
        for one in _items(listed)
        if _template(_metadata(one).get("onetaskgraph.template")) == "onepipeline:design-doc"
    ]
    assert len(designs) == 1, listed
    return _document(bench, designs[0])


def _section(content: str, name: str) -> str:
    """One `### <name>` entry of a document's Visual changes, up to the next heading."""
    found = re.search(rf"^### {re.escape(name)}\n(.*?)(?=^#|\Z)", content, re.MULTILINE | re.DOTALL)
    return found.group(1).strip() if found else ""


def _finishing_members(bench: Bench, native: str) -> list[str]:
    """The node ids of the newest finishing project `just finish-plan` created for ``native``."""
    projects = store_json(bench, "project", "list", "--source", "authoring")
    finishing = sorted(
        str(one["id"])
        for one in _items(projects)
        if str(one["id"]).startswith(f"authoring:{native}-finishing-")
    )
    assert finishing, projects
    listed = store_json(bench, "task", "list", "--source", "authoring", "--project", finishing[-1])
    return sorted(str(_metadata(item)["onepipeline.id"]) for item in _items(listed))


def _until(process: subprocess.Popen[str], what: str, found: Callable[[], bool]) -> None:
    deadline = time.monotonic() + e2e_timeout(SETTLE_SECONDS)
    while not found():
        assert process.poll() is None, f"the recipe returned before {what}"
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(2)


class Refinish(NamedTuple):
    """One `just finish-plan` and what it left."""

    result: subprocess.CompletedProcess[str]
    run: str
    finishing: dict[str, str]
    members: list[str]
    spikes_runs: list[str]
    finalize_runs: list[str]


def _refinished(
    bench: Bench, native: str, before: set[str], result: subprocess.CompletedProcess[str]
) -> Refinish:
    started = _new_runs(bench, before, "-finishing")
    assert len(started) == 1, (runs_of(bench), result.stdout, result.stderr)
    return Refinish(
        result,
        started[0],
        _nodes(status(bench, started[0])),
        _finishing_members(bench, native),
        _new_runs(bench, before, "-spikes"),
        _new_runs(bench, before, "-finalize"),
    )


def _refinish(bench: Bench, native: str) -> Refinish:
    """`just finish-plan` over ``native``, and what it left."""
    before = set(runs_of(bench))
    result = just(bench, "finish-plan", f"authoring:{native}", BOARD, seconds=SETTLE_SECONDS)
    return _refinished(bench, native, before, result)


def _legacy_plan(bench: Bench, identity: Identity, native: str) -> list[str]:
    """A plan whose one spike ran the way spikes ran before these records were kept: its
    spikes project started directly, settling the spike `done` with no record. Answers the
    spikes run it started."""
    plan = f"authoring:{native}"
    goal = json.dumps({"text": "Poll the legacy route"})
    task_answers = " ".join(
        f"--var {shlex.quote(one)}" for one in _task_answers("route", "Do route.", native, HEALTH)
    )
    spike_answers = " ".join(
        f"--var {shlex.quote(one)}" for one in _spike_answers(spike_what(HEALTH))
    )
    for command in (
        f"{RESOLVE} plan-description --json | {STORE} project create authoring --id {native} "
        f"--title {shlex.quote(plan_title(native))} --template-loader - "
        "--var 'overview=A plan whose spike ran before spike records.' "
        "--metadata onepipeline.schema_version=3 "
        f"--metadata {shlex.quote('onepipeline.goal=' + goal)} --no-interactive",
        f"{RESOLVE} plan-task --json | {STORE} task create authoring --project {plan} "
        f"--title 'feat: add the legacy route' --template-loader - {task_answers} "
        "--metadata 'onepipeline.id=\"route\"' --metadata 'onepipeline.persona=\"engineer\"' "
        "--metadata 'orchestrator.unit=\"API\"' --no-interactive",
        f"{STORE} project create authoring --id {native}-spikes "
        f"--title {shlex.quote(plan_title(native) + ' spikes')} "
        "--metadata onepipeline.schema_version=3 --no-interactive",
        f"{RESOLVE} plan-task --json | {STORE} task create authoring --project {plan}-spikes "
        f"--title 'spike: {HEALTH}' --template-loader - {spike_answers} "
        f"--metadata 'onepipeline.id=\"{HEALTH}\"' --metadata 'onepipeline.persona=\"engineer\"' "
        f"--metadata 'onepipeline.repo={json.dumps(str(identity.publication))}' "
        f"--metadata 'onepipeline.execution_checkout={json.dumps(EXECUTION_ALIAS)}' "
        "--metadata 'onepipeline.publish=\"preserve\"' --no-interactive",
    ):
        created = run(bench, "bash", "-c", command)
        assert created.returncode == 0, command + created.stdout + created.stderr
    before = set(runs_of(bench))
    started = run(
        bench,
        "scripts/onepipeline.sh",
        "start",
        f"{plan}-spikes",
        "--launch-config",
        "config/onepipeline.workflows.yaml",
        "--dag-graph",
        "off",
        "--label",
        "kind=spikes",
        "--branch-template",
        f"{native}/{{{{ node.id }}}}",
        seconds=SETTLE_SECONDS,
    )
    assert started.returncode == 0, started.stdout + started.stderr
    spikes_runs = _new_runs(bench, before, "-spikes")
    assert len(spikes_runs) == 1, runs_of(bench)
    assert _nodes(status(bench, spikes_runs[0])) == {HEALTH: "done"}, status(bench, spikes_runs[0])
    return spikes_runs


class Sequence(NamedTuple):
    """Everything the sequence did, read back before the bench goes."""

    native: str
    planned: subprocess.CompletedProcess[str]
    first_spikes_run: str
    first_spikes: dict[str, str]
    first_records: dict[str, dict[str, object]]
    first_evidence: dict[str, object]
    first_branch: Branch
    first_design: Shown
    visual_settlement_before: object
    edited: Refinish
    second_spikes: dict[str, str]
    rework: str
    finalize_run: dict[str, str]
    second_branch: Branch
    branch_descends: bool
    second_records: dict[str, dict[str, object]]
    visual_settlement_after: object
    visual_prompts: list[str]
    second_design: Shown
    unchanged: Refinish
    unchanged_spikes_log: str
    refused: Refinish
    refused_check_log: str
    docs_task: str
    unaffected: subprocess.CompletedProcess[str]
    accepted: Refinish
    docs_evidence: dict[str, object]
    launch_valid: subprocess.CompletedProcess[str]
    launch_edited: subprocess.CompletedProcess[str]
    board_answers: subprocess.CompletedProcess[str]
    midrun: subprocess.CompletedProcess[str]
    midrun_finishing: list[str]
    midrun_record: dict[str, object]
    legacy: Refinish
    legacy_spikes_log: str
    legacy_record: dict[str, object]
    legacy_spikes_runs: list[str]
    legacy_rework: subprocess.CompletedProcess[str]
    dispatched_health: list[str]
    dispatched_visual: list[str]
    dispatched_finalize: list[str]


@pytest.fixture(scope="module")
def sequence(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Iterator[Sequence]:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp = tmp_path_factory.mktemp("spike-reruns")
    identity = seeded(tmp / "identity", execution=EXECUTION_ALIAS)
    bench_ = bench(
        tmp,
        oneharness_bin,
        "e2e-spike-reruns",
        rendered_only_as_configured=True,
        ONEVCS_HOME=str(identity.home),
        **identity.environment,
    )
    native = f"plan-rr-{os.getpid()}"
    plan = f"authoring:{native}"
    first_images = _images(tmp / "images" / "first", FIRST_ENTRIES, 1)
    second_images = _images(tmp / "images" / "second", (BLUE_BADGE, LEGEND), 50)

    # 1. The first plan: each spike runs once, and finalize names them on the tasks.
    drafting(
        bench_,
        native,
        spikes=(str(identity.publication), EXECUTION_ALIAS),
        spike_ids=(HEALTH, VISUAL),
        definitions={VISUAL: FIRST_DEFINITION},
    )
    mark(bench_, SPIKES[HEALTH].marker, *_health_commands(bench_))
    mark(
        bench_,
        SPIKES[VISUAL].marker,
        *_visual_commands(bench_, native, FIRST_ENTRIES, first_images),
    )
    mark(bench_, FINALIZE_MARKER, *_finalize_commands(native))
    before = set(runs_of(bench_))
    planned = just(bench_, "plan", brief(bench_, native), BOARD, seconds=SETTLE_SECONDS)
    first_spikes_run = _new_runs(bench_, before, "-spikes")
    assert len(first_spikes_run) == 1, (runs_of(bench_), planned.stdout, planned.stderr)
    health = _task_id(bench_, f"{plan}-spikes", HEALTH)
    visual = _task_id(bench_, f"{plan}-spikes", VISUAL)
    docs = _task_id(bench_, plan, "docs")
    first_records = {
        HEALTH: _record(bench_, health, "spike-definition", "ran"),
        VISUAL: _record(bench_, visual, "spike-definition", "ran"),
    }
    first_evidence = store_json(
        bench_, "project", "attestation", "check", plan, "--kind", "spike-evidence", "--tasks"
    )
    first_branch = _branch_head(identity, VISUAL)
    first_design = _design_on_board(bench_, native)
    visual_settlement_before = _settlement(bench_, visual)

    # 2. The design review changed what the user sees: the manager regenerates the visual
    # spike and the task built on it, then re-finishes the plan.
    mark(
        bench_,
        SPIKES[VISUAL].marker,
        *_visual_commands(bench_, native, SECOND_ENTRIES, second_images),
    )
    # The re-finish's finalize reads which tasks to rework as its task tells a planner to.
    rework = tmp / "rework.txt"
    mark(
        bench_,
        FINALIZE_MARKER,
        _counting(bench_, "finalize"),
        f"scripts/spike-records.sh rework {plan} > {shlex.quote(str(rework))}",
    )
    _render(
        bench_, "plan-task", "task", visual, *_spike_answers(spike_what(VISUAL, SECOND_DEFINITION))
    )
    _render(
        bench_,
        "plan-task",
        "task",
        docs,
        *_task_answers("docs", DOCS_WITH_BLUE, native, *DOCS_SPIKES),
    )
    turns_before = len(bench_.turn_log.read_text(encoding="utf-8").splitlines())
    edited = _refinish(bench_, native)
    assert len(edited.spikes_runs) == 1, (edited.spikes_runs, edited.result.stderr)
    assert len(edited.finalize_runs) == 1, (edited.finalize_runs, edited.result.stderr)
    finalize_run = _nodes(status(bench_, edited.finalize_runs[0]))
    second_spikes = _nodes(status(bench_, edited.spikes_runs[0]))
    second_branch = _branch_head(identity, VISUAL)
    git("fetch", "-q", "origin", cwd=identity.publication)
    ancestry = ["git", "merge-base", "--is-ancestor", first_branch.head, second_branch.head]
    descends = (
        # Exit 1 is the answer "not an ancestor", which the conftest `git` helper would raise on.
        subprocess.run(ancestry, cwd=identity.publication, check=False).returncode == 0
    )
    second_records = {
        HEALTH: _record(bench_, health, "spike-definition", "ran"),
        VISUAL: _record(bench_, visual, "spike-definition", "ran"),
    }
    visual_settlement_after = _settlement(bench_, visual)
    prompts = [
        str(json.loads(line).get("prompt"))
        for line in bench_.turn_log.read_text(encoding="utf-8").splitlines()[turns_before:]
    ]
    visual_prompts = [one for one in prompts if SECOND_DEFINITION in one]
    second_design = _design_on_board(bench_, native)

    # 3. Nothing edited: no spikes run, no finalize.
    unchanged = _refinish(bench_, native)
    unchanged_spikes_log = _command_log(bench_, unchanged.run, "spikes")

    # 4. Only the task built on the visual spike changes: the check refuses it until the
    # manager records why its spike's evidence still holds.
    _render(
        bench_,
        "plan-task",
        "task",
        docs,
        *_task_answers("docs", DOCS_REWORDED, native, *DOCS_SPIKES),
    )
    refused = _refinish(bench_, native)
    refused_check_log = _command_log(bench_, refused.run, "check")
    unaffected = just(bench_, "spike-unaffected", docs, "--reason", UNAFFECTED_REASON)
    accepted = _refinish(bench_, native)
    docs_evidence = _record(bench_, docs, "spike-evidence")

    # 5. The launch gate over the board copy: valid records pass the spike-evidence check, and
    # an edit to the copy's task built on a spike is refused by it.
    copy = f"{BOARD}:{native}"
    launch_valid = just(bench_, "orchestrate", copy, "--dag-graph", "off")
    # Retitled rather than regenerated, so the copy keeps no template answers of its own, as a
    # board copy keeps none, and the check names the spikes from its authoring origin's.
    board_docs = _task_id(bench_, copy, "docs")
    retitled = store(bench_, "task", "update", board_docs, "--title", DOCS_ON_BOARD)
    assert retitled.returncode == 0, retitled.stdout + retitled.stderr
    board_answers = store(bench_, "task", "answers", board_docs, "--json")
    launch_edited = just(bench_, "orchestrate", copy, "--dag-graph", "off")

    # 6. A spike edited while its re-run is under way.
    hold = tmp / "release-health"
    mark(bench_, SPIKES[HEALTH].marker, *_health_commands(bench_, hold))
    _render(
        bench_,
        "plan-task",
        "task",
        health,
        *_spike_answers(spike_what(HEALTH, "Measure p99 as well.")),
    )
    before = set(runs_of(bench_))
    out, err = tmp / "midrun.out", tmp / "midrun.err"
    with out.open("w") as stdout, err.open("w") as stderr:
        process = subprocess.Popen(  # noqa: S603 - the copy's own recipe
            ["just", "finish-plan", plan, BOARD],  # noqa: S607 - `just` as an operator types it
            cwd=bench_.checkout,
            env=bench_.environment,
            stdout=stdout,
            stderr=stderr,
            text=True,
        )
        try:

            def running() -> bool:
                found = _new_runs(bench_, before, "-spikes")
                # A run's directory exists before its launch record, which status reads.
                if not found or not (bench_.runs / found[0] / "launch.json").is_file():
                    return False
                return _nodes(status(bench_, found[0])).get(HEALTH) == "running"

            _until(process, "the edited spike running", running)
            _render(
                bench_,
                "plan-task",
                "task",
                health,
                *_spike_answers(spike_what(HEALTH, "Measure p99 and p999.")),
            )
            hold.touch()
            process.wait(timeout=e2e_timeout(SETTLE_SECONDS))
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
    midrun = subprocess.CompletedProcess(
        process.args, process.returncode, out.read_text(), err.read_text()
    )
    midrun_finishing = _new_runs(bench_, before, "-finishing")
    midrun_record = _record(bench_, health, "spike-definition")

    # 7. A plan whose spike ran before spike records were kept.
    legacy_native = f"plan-lg-{os.getpid()}"
    mark(bench_, SPIKES[HEALTH].marker, *_health_commands(bench_))
    mark(bench_, DESIGN_MARKER, *design_commands(legacy_native))
    legacy_spikes_runs = _legacy_plan(bench_, identity, legacy_native)
    # Its task names the spike and holds no evidence yet, so `rework` lists it as unrecorded.
    legacy_rework = run(
        bench_,
        "scripts/plan-store.sh",
        "scripts/spike-records.sh",
        "rework",
        f"authoring:{legacy_native}",
    )
    legacy = _refinish(bench_, legacy_native)
    legacy_spikes_log = _command_log(bench_, legacy.run, "spikes")
    legacy_record = _record(
        bench_,
        _task_id(bench_, f"authoring:{legacy_native}-spikes", HEALTH),
        "spike-definition",
        "ran",
    )

    yield Sequence(
        native,
        planned,
        first_spikes_run[0],
        _nodes(status(bench_, first_spikes_run[0])),
        first_records,
        first_evidence,
        first_branch,
        first_design,
        visual_settlement_before,
        edited,
        second_spikes,
        rework.read_text(encoding="utf-8") if rework.is_file() else "",
        finalize_run,
        second_branch,
        descends,
        second_records,
        visual_settlement_after,
        visual_prompts,
        second_design,
        unchanged,
        unchanged_spikes_log,
        refused,
        refused_check_log,
        docs,
        unaffected,
        accepted,
        docs_evidence,
        launch_valid,
        launch_edited,
        board_answers,
        midrun,
        midrun_finishing,
        midrun_record,
        legacy,
        legacy_spikes_log,
        legacy_record,
        legacy_spikes_runs,
        legacy_rework,
        _dispatched(bench_, HEALTH),
        _dispatched(bench_, VISUAL),
        _dispatched(bench_, "finalize"),
    )
    for one in runs_of(bench_):
        if not one.startswith(".") and one != "human-items":
            just(bench_, "stop", one, seconds=120)


def _valid(record: dict[str, object], bar: str | None = None) -> None:
    assert record.get("state") == "valid", record
    held = record.get("record")
    assert isinstance(held, dict), record
    if bar is not None:
        assert held["bar"] == bar, record


def _held(record: dict[str, object], key: str) -> object:
    held = record.get("record")
    assert isinstance(held, dict), record
    return held.get(key)


def test_a_first_plan_runs_each_spike_once_and_records_it(sequence: Sequence) -> None:
    assert sequence.planned.returncode == 0, sequence.planned.stdout + sequence.planned.stderr
    assert sequence.first_spikes == {"spike-brief": "done", HEALTH: "done", VISUAL: "done"}
    for record in sequence.first_records.values():
        _valid(record, "ran")
    for dispatched in (sequence.dispatched_health, sequence.dispatched_visual):
        assert dispatched.count(sequence.first_spikes_run) == 1, dispatched


def test_every_task_built_on_a_spike_holds_valid_evidence(sequence: Sequence) -> None:
    tasks = sequence.first_evidence.get("tasks")
    assert isinstance(tasks, list) and len(tasks) == 2, sequence.first_evidence
    assert all(task["state"] == "valid" for task in tasks), tasks


def test_a_later_engine_write_back_leaves_a_spike_record_valid(sequence: Sequence) -> None:
    """The re-run spike's `launched` record is written before its run, whose settlement the
    engine then writes back onto the task; the step promotes it to `ran` only while it is
    still valid, which the note naming that run shows it was."""
    assert sequence.visual_settlement_before != sequence.visual_settlement_after, (
        "the second spikes run wrote no new settlement back onto spike-visual"
    )
    record = sequence.second_records[VISUAL]
    _valid(record, "ran")
    assert _held(record, "note") == f"spikes run {sequence.edited.spikes_runs[0]}", record


def test_a_refinish_reruns_only_the_edited_spike_on_its_branch(sequence: Sequence) -> None:
    edited = sequence.edited
    assert edited.result.returncode == 0, edited.result.stdout + edited.result.stderr
    assert sequence.second_spikes == {"spike-brief": "done", HEALTH: "done", VISUAL: "done"}
    second_run = edited.spikes_runs[0]
    assert sequence.dispatched_visual.count(second_run) == 1, sequence.dispatched_visual
    assert second_run not in sequence.dispatched_health, sequence.dispatched_health
    assert sequence.second_branch.name == sequence.first_branch.name, (
        sequence.first_branch,
        sequence.second_branch,
    )
    assert sequence.second_branch.head != sequence.first_branch.head, sequence.second_branch
    assert sequence.branch_descends, (sequence.first_branch, sequence.second_branch)
    _valid(sequence.second_records[VISUAL], "ran")


def test_a_refinish_with_an_edited_spike_finalizes_and_reaches_the_approval(
    sequence: Sequence,
) -> None:
    """Finalize runs once, in its own run before the finishing run, which holds none."""
    edited = sequence.edited
    finalized = edited.finalize_runs[0]
    assert sequence.dispatched_finalize == [finalized], sequence.dispatched_finalize
    assert sequence.finalize_run == {"finalize": "done"}, sequence.finalize_run
    reworked = sequence.rework.splitlines()
    assert len(reworked) == 1, sequence.rework
    # The docs task names both spikes; only the edited one is behind its fingerprint moving.
    assert reworked[0].startswith(f"{sequence.docs_task} (docs): its spikes {VISUAL} changed"), (
        sequence.rework
    )
    assert "finalize" not in edited.members, edited.members
    waiting = {node: state for node, state in edited.finishing.items() if state != "done"}
    assert waiting == {"approve-design": "waiting"}, edited.finishing


def test_the_rerun_spike_was_told_to_update_its_report(sequence: Sequence) -> None:
    assert sequence.visual_prompts, "the re-run visual spike's worker received no task"
    task = re.sub(r"\s+", " ", sequence.visual_prompts[0])
    assert REPORT_RULE in task and PRESERVE_RULE in task, task
    assert all("report" not in answer.lower() for answer in _spike_answers(SECOND_DEFINITION))


def test_the_board_design_document_reads_the_refreshed_report(sequence: Sequence) -> None:
    first, second = sequence.first_design, sequence.second_design
    assert _section(second.content, HEADER.name) == _section(first.content, HEADER.name) != ""
    assert second.assets[HEADER.image] == first.assets[HEADER.image]
    assert BLUE_BADGE.description in _section(second.content, BLUE_BADGE.name)
    assert GREEN_BADGE.description in _section(first.content, GREEN_BADGE.name)
    assert GREEN_BADGE.description not in second.content
    assert second.assets[BLUE_BADGE.image] != first.assets[GREEN_BADGE.image]
    assert LEGEND.description in _section(second.content, LEGEND.name)
    assert LEGEND.image in second.assets and LEGEND.image not in first.assets
    assert FOOTER.description in first.content and FOOTER.image in first.assets
    assert FOOTER.description not in second.content and FOOTER.image not in second.assets


def test_a_refinish_with_nothing_edited_starts_no_spikes_and_no_finalize(
    sequence: Sequence,
) -> None:
    unchanged = sequence.unchanged
    assert unchanged.result.returncode == 0, unchanged.result.stdout + unchanged.result.stderr
    assert unchanged.spikes_runs == [], unchanged.spikes_runs
    assert unchanged.finalize_runs == [], unchanged.finalize_runs
    assert "finalize" not in unchanged.members, unchanged.members
    assert "no stale spike" in sequence.unchanged_spikes_log, sequence.unchanged_spikes_log


def test_an_edit_to_a_task_alone_is_refused_by_the_check_naming_both_next_steps(
    sequence: Sequence,
) -> None:
    refused = sequence.refused
    assert refused.finishing.get("check") == "failed", refused.finishing
    log = re.sub(r"\s+", " ", sequence.refused_check_log)
    for named in (
        sequence.docs_task,
        VISUAL,
        f"just finish-plan authoring:{sequence.native}",
        f"just spike-unaffected {sequence.docs_task}",
    ):
        assert named in log, (named, sequence.refused_check_log)


def test_spike_unaffected_records_the_reason_and_the_check_passes(sequence: Sequence) -> None:
    unaffected = sequence.unaffected
    assert unaffected.returncode == 0, unaffected.stdout + unaffected.stderr
    assert sequence.accepted.finishing.get("check") == "done", sequence.accepted.finishing
    _valid(sequence.docs_evidence)
    assert _held(sequence.docs_evidence, "note") == UNAFFECTED_REASON, sequence.docs_evidence


def test_the_launch_gate_refuses_an_edited_board_task_and_passes_a_valid_one(
    sequence: Sequence,
) -> None:
    valid = sequence.launch_valid
    assert "spike-evidence-check" not in valid.stdout + valid.stderr, valid.stdout + valid.stderr
    edited = sequence.launch_edited
    assert edited.returncode != 0, edited.stdout + edited.stderr
    # The copy holds no answers, so the spike the refusal names came from its origin's.
    assert "no-stored-answers" in sequence.board_answers.stdout, sequence.board_answers
    text = re.sub(r"\s+", " ", edited.stdout + edited.stderr)
    for named in (
        sequence.docs_task,
        VISUAL,
        f"just finish-plan authoring:{sequence.native}",
        f"just spike-unaffected {sequence.docs_task}",
    ):
        assert named in text, (named, edited.stdout + edited.stderr)


def test_a_spike_edited_while_it_runs_fails_the_step_and_stays_launched(
    sequence: Sequence,
) -> None:
    """The stale spikes re-run first in the flow, so their failure stops it before any
    finishing run starts."""
    midrun = sequence.midrun
    output = midrun.stdout + midrun.stderr
    assert midrun.returncode != 0, output
    assert f"edited while it ran: {HEALTH}" in output, output
    assert sequence.midrun_finishing == [], sequence.midrun_finishing
    assert _held(sequence.midrun_record, "bar") == "launched", sequence.midrun_record
    assert sequence.midrun_record["reason"] == "content-moved", sequence.midrun_record


def test_a_legacy_spike_is_backfilled_and_not_run_again(sequence: Sequence) -> None:
    legacy = sequence.legacy
    assert f"backfilled {HEALTH}" in sequence.legacy_spikes_log, sequence.legacy_spikes_log
    assert legacy.spikes_runs == [], legacy.spikes_runs
    assert len(sequence.legacy_spikes_runs) == 1, sequence.legacy_spikes_runs
    _valid(sequence.legacy_record, "ran")


def test_rework_lists_a_task_with_no_evidence_from_the_spikes_it_names(
    sequence: Sequence,
) -> None:
    listed = sequence.legacy_rework
    assert listed.returncode == 0, listed.stdout + listed.stderr
    lines = listed.stdout.splitlines()
    assert len(lines) == 1, listed.stdout
    assert lines[0].endswith(f" (route): no evidence is recorded from its spikes {HEALTH}"), lines
