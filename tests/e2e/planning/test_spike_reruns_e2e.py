"""A plan's spikes re-run exactly when their tasks change, and finalize behind them.

One sequence on one bench (`planning_bench`), every step through the real recipe, only the
paid model doubled:

1. `just plan <brief> board` drafts a two-task plan and its spikes project with two lifecycle
   spikes against a scratch identity, `spike-health` and `spike-visual`, whose definition
   names three visual entries, and the edge from the spikes project's `finalize` onto each.
   Each spike runs once, the visual one writing its report with an image per entry, and then
   `finalize` runs in the same spikes run: every spike is among its `changed_dependencies`,
   so the doubled finalize reworks every task whose drafted `spikes` answer names one. The
   doubled design writer restates the visual report in the design document copied to the
   board. The first `just finish-plan` after it, with nothing edited,
   dispatches nothing in the spikes project at all.
2. The manager regenerates `spike-visual` — one entry changed, one added, one removed — and
   the task built on it, then runs `just finish-plan`. Its `spikes` step launches the spikes
   project again; the engine redoes that spike alone among the spikes, continuing its
   preserved branch, and its worker, told so by the dispatch appendix, re-renders its report
   giving only the changed images. `finalize` is redone behind it, reads that spike alone in
   its `changed_dependencies` and reworks exactly the task built on it, leaving the other
   task's content as it was, and the board's design document carries the refreshed entries
   beside the unchanged ones, byte for byte.
3. A task built on a spike is reworded and nothing in the spikes project is edited: `just
   finish-plan` dispatches nothing in the spikes project, reworks nothing, and its `check`
   passes — an edit to a task is never refused for its spikes.
4. `spike-health` is regenerated and, while its re-run is under way, regenerated again: the
   re-finish completes on the run it started.
5. The next `just finish-plan` re-runs `spike-health` for the edit made while it ran, and its
   worker asks its manager a blocking question: the `spikes` step waits through the answer,
   the run continues with no `adopt`, and `finalize` is redone behind it.
6. `spike-visual` is regenerated again and the re-finish is interrupted: the spikes run is
   stopped while `finalize` runs, after the spike re-ran. The next `just finish-plan` runs
   `finalize` alone, which still reads that spike as changed and reworks exactly the task
   built on it.

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
    FINALIZE_MARKER,
    RESOLVE,
    SPIKE_BRIEF_MARKER,
    SPIKE_CRITERIA,
    SPIKE_WHY,
    SPIKES,
    STORE,
    brief,
    drafting,
    finalize_commands,
    spike_what,
    spikes_answer,
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
    store_json,
)
from scratch_identity import Identity, seeded
from waits import timeout as e2e_timeout

pytestmark = [pytest.mark.usefixtures("module_tmpdir"), pytest.mark.xdist_group("spike-reruns")]

#: The alias the scratch identity's execution checkout is registered under.
EXECUTION_ALIAS = "execution"
HEALTH, VISUAL = "spike-health", "spike-visual"
#: The spikes each plan task builds on, as the draft answers them before finalize runs: the
#: route task names the health spike, and the docs task names both.
TASK_SPIKES = {"route": (HEALTH,), "docs": (HEALTH, VISUAL)}
#: The dispatch appendix's report-update rule, as a re-run spike's task must carry it.
REPORT_RULE = "A spike whose report already exists updates that report."
PRESERVE_RULE = "keep every other entry's answers and images exactly as they are"
#: How long a recipe returning at the design approval may take.
SETTLE_SECONDS = 1500.0
#: What the health spike's worker asks its manager, and what the manager rules.
QUESTION = "Should the health spike measure p999 as well as p99?"
ANSWER = "Measure p999 as well."


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
THIRD_DEFINITION = "Capture the header, a blue badge, a legend and the badge's hover."
FIRST_ENTRIES = (HEADER, GREEN_BADGE, FOOTER)
SECOND_ENTRIES = (HEADER, BLUE_BADGE, LEGEND)
#: The docs task's `what` after each of the manager's regenerations of it.
DOCS_WITH_BLUE = "Do docs, showing the blue badge."
DOCS_REWORDED = "Do docs, showing the blue badge and its legend."


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


def _counting(bench: Bench, worker: str) -> str:
    """A command recording, in the bench, that ``worker`` was dispatched in this run."""
    return f'echo "$ONEPIPELINE_RUN_ID" >> {shlex.quote(str(bench.tmp / f"dispatched-{worker}"))}'


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


def _health_commands(bench: Bench, hold: Path | None = None, *, asks: bool = False) -> list[str]:
    """The health spike worker: commit, after ``hold`` exists where given, and after asking
    its manager ``QUESTION`` through `$ORCHESTRATOR_ASK_MANAGER` where ``asks``."""
    held = [] if hold is None else [f"until [ -e {shlex.quote(str(hold))} ]; do sleep 1; done"]
    answered = shlex.quote(str(bench.tmp / "answered"))
    asked = [f'"$ORCHESTRATOR_ASK_MANAGER" {shlex.quote(QUESTION)} > {answered}'] if asks else []
    return [_counting(bench, HEALTH), *held, *asked, _commit(HEALTH)]


def _task_answers(node: str, what: str, native: str, *spikes: str) -> list[str]:
    """Every answer of one plan task as the draft and finalize render it."""
    return [
        f"what={what}",
        "why=Operators need it.",
        f'acceptance_criteria=["A request-level test drives {node} end to end."]',
        f"spikes={spikes_answer(native, *spikes)}",
    ]


def _spike_answers(definition: str) -> list[str]:
    """Every answer of the visual or health spike task, none of which mentions a report."""
    return [f"what={definition}", f"why={SPIKE_WHY}", f"acceptance_criteria={SPIKE_CRITERIA}"]


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


def _content(bench: Bench, task: str) -> str:
    """``task``'s content as the store shows it."""
    shown = store_json(bench, "task", "show", task)
    items = _items(shown)
    assert len(items) == 1, shown
    inner = items[0]["item"]
    assert isinstance(inner, dict), shown
    return str(inner["content"])


def _turns(bench: Bench) -> int:
    return len(bench.turn_log.read_text(encoding="utf-8").splitlines())


def _prompts_since(bench: Bench, turns: int) -> list[str]:
    """The prompt of every turn the doubled model answered after the first ``turns``."""
    return [
        str(json.loads(line).get("prompt"))
        for line in bench.turn_log.read_text(encoding="utf-8").splitlines()[turns:]
    ]


def _spikes_named(bench: Bench, task: str) -> list[str]:
    """The spike of each entry of ``task``'s `spikes` answer, as the store prints it."""
    answers = store_json(bench, "task", "answers", task)
    entries = answers.get("spikes")
    assert isinstance(entries, list), answers
    return [str(entry["spike"]) for entry in entries if isinstance(entry, dict)]


def _nodes(answer: dict[str, object]) -> dict[str, str]:
    nodes = answer.get("nodes")
    assert isinstance(nodes, list), answer
    return {str(node["id"]): str(node.get("status")) for node in nodes if isinstance(node, dict)}


def _of_kind(run_id: str, suffix: str) -> bool:
    """Whether ``run_id`` is a run of a project titled ending ``suffix``: the engine names a
    project's later runs with a `-<n>` after it."""
    return re.search(rf"{re.escape(suffix)}(?:-\d+)?$", run_id) is not None


def _new_runs(bench: Bench, before: set[str], suffix: str) -> list[str]:
    return sorted(one for one in runs_of(bench) if one not in before and _of_kind(one, suffix))


def _dispatched(bench: Bench, worker: str) -> list[str]:
    """The run of each dispatch the doubled ``worker`` recorded, in order."""
    path = bench.tmp / f"dispatched-{worker}"
    return path.read_text(encoding="utf-8").split() if path.is_file() else []


def _by_run(bench: Bench, name: str) -> dict[str, list[str]]:
    """The doubled finalize's ``name`` record: what it wrote in each run, by run."""
    path = bench.tmp / name
    found: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines() if path.is_file() else []:
        run_id, written = line.split(" ", 1)
        found.setdefault(run_id, []).append(written)
    return found


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
    """One entry of a document's Visual changes, in either form the template writes it.

    A featured change is a `### <name>` heading, read up to the next heading or the line
    introducing the collapsed rest; any other change is a `<details>` block under its name,
    read up to its own closing line, past the `Before` block nested inside it.
    """
    flags = re.MULTILINE | re.DOTALL
    featured = re.search(rf"^### {re.escape(name)}\n(.*?)(?=^#|^\*\*\d+ more |\Z)", content, flags)
    if featured:
        return featured.group(1).strip()
    collapsed = re.search(
        rf"^<details>\n<summary>{re.escape(name)}</summary>\n(.*?)^</details>$"
        r"(?=\s*\Z|\s*^#|\s*^<details>\n<summary>(?!Before</summary>))",
        content,
        flags,
    )
    return collapsed.group(1).strip() if collapsed else ""


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
    spikes_run: str
    spikes: dict[str, str]
    spikes_launch: dict[str, object]


def _refinished(
    bench: Bench, native: str, before: set[str], result: subprocess.CompletedProcess[str]
) -> Refinish:
    started = _new_runs(bench, before, "-finishing")
    assert len(started) == 1, (runs_of(bench), result.stdout, result.stderr)
    spikes_runs = _new_runs(bench, before, "-spikes")
    assert len(spikes_runs) == 1, (spikes_runs, result.stdout, result.stderr)
    return Refinish(
        result,
        started[0],
        _nodes(status(bench, started[0])),
        _finishing_members(bench, native),
        spikes_runs[0],
        _nodes(status(bench, spikes_runs[0])),
        json.loads((bench.runs / spikes_runs[0] / "launch.json").read_text(encoding="utf-8")),
    )


def _refinish(bench: Bench, native: str) -> Refinish:
    """`just finish-plan` over ``native``, and what it left."""
    before = set(runs_of(bench))
    result = just(bench, "finish-plan", f"authoring:{native}", BOARD, seconds=SETTLE_SECONDS)
    return _refinished(bench, native, before, result)


class Started(NamedTuple):
    """A recipe started beside the journey, and the files its two streams go to."""

    process: subprocess.Popen[str]
    out: Path
    err: Path


def _popen(bench: Bench, name: str, *argv: str) -> Started:
    """One recipe started beside the journey, its streams going to files of the bench's."""
    out, err = bench.tmp / f"{name}.out", bench.tmp / f"{name}.err"
    process = subprocess.Popen(  # noqa: S603 - the copy's own recipe
        list(argv),
        cwd=bench.checkout,
        env=bench.environment,
        stdout=out.open("w"),
        stderr=err.open("w"),
        text=True,
    )
    return Started(process, out, err)


def _finished(started: Started) -> subprocess.CompletedProcess[str]:
    process = started.process
    try:
        process.wait(timeout=e2e_timeout(SETTLE_SECONDS))
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    return subprocess.CompletedProcess(
        process.args, process.returncode, started.out.read_text(), started.err.read_text()
    )


def _spikes_run_started(bench: Bench, before: set[str]) -> str | None:
    """The spikes run a recipe started since ``before``, once its launch record exists."""
    found = _new_runs(bench, before, "-spikes")
    # A run's directory exists before its launch record, which status reads.
    if not found or not (bench.runs / found[0] / "launch.json").is_file():
        return None
    return found[0]


class Answered(NamedTuple):
    """The manager's side of the health spike's question: what it read and what it sent."""

    question: str
    reply: subprocess.CompletedProcess[str]


def _answer_the_question(
    bench: Bench, process: subprocess.Popen[str], before: set[str]
) -> Answered:
    """Play the manager: read the spikes run's channel until its blocking question waits,
    then answer it bound by its correlation, as `just channel-next` and `just channel-reply
    --correlation` do."""
    asked: dict[str, object] = {}

    def waiting() -> bool:
        spikes_run = _spikes_run_started(bench, before)
        if spikes_run is None:
            return False
        handed = just(bench, "channel-next", spikes_run, seconds=120)
        assert handed.returncode == 0, handed.stdout + handed.stderr
        if not handed.stdout.strip():
            return False
        surface = json.loads(handed.stdout).get("surface")
        if isinstance(surface, dict) and surface.get("blocking") and surface.get("correlation"):
            asked.update(surface, run=spikes_run)
            return True
        return False

    _until(process, "the health spike's blocking question", waiting)
    envelope = json.dumps({"version": 4, "completion": True, "message": ANSWER})
    reply = run(
        bench,
        "just",
        "channel-reply",
        str(asked["run"]),
        "--correlation",
        str(asked["correlation"]),
        input_text=envelope,
        seconds=120,
    )
    return Answered(str(asked.get("message")), reply)


class Sequence(NamedTuple):
    """Everything the sequence did, read back before the bench goes."""

    native: str
    tasks: dict[str, str]
    planned: subprocess.CompletedProcess[str]
    first_spikes_run: str
    first_spikes: dict[str, str]
    spike_tasks: dict[str, str]
    first_answers: dict[str, list[str]]
    first_branch: Branch
    first_design: Shown
    finalize_edges: dict[str, object]
    fresh: Refinish
    fresh_prompts: list[str]
    edited: Refinish
    route_before_edit: str
    route_after_edit: str
    docs_before_edit: str
    docs_after_edit: str
    second_branch: Branch
    branch_descends: bool
    visual_prompts: list[str]
    second_design: Shown
    unchanged: Refinish
    midrun: Refinish
    midrun_running: str
    asked: Refinish
    answered: Answered
    answer_received: str
    interrupted: Refinish
    resumed: Refinish
    rework: dict[str, list[str]]
    changed: dict[str, list[str]]
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

    # 1. The first plan, drafted with each task's spikes: each spike runs once, then finalize.
    drafting(
        bench_,
        native,
        spikes=(str(identity.publication), EXECUTION_ALIAS),
        spike_ids=(HEALTH, VISUAL),
        definitions={VISUAL: FIRST_DEFINITION},
        task_spikes=TASK_SPIKES,
    )
    mark(bench_, SPIKES[HEALTH].marker, *_health_commands(bench_))
    mark(
        bench_,
        SPIKES[VISUAL].marker,
        *_visual_commands(bench_, native, FIRST_ENTRIES, first_images),
    )
    before = set(runs_of(bench_))
    planned = just(bench_, "plan", brief(bench_, native), BOARD, seconds=SETTLE_SECONDS)
    first_spikes_run = _new_runs(bench_, before, "-spikes")
    assert len(first_spikes_run) == 1, (runs_of(bench_), planned.stdout, planned.stderr)
    tasks = {node: _task_id(bench_, plan, node) for node in TASK_SPIKES}
    first_answers = {node: _spikes_named(bench_, task) for node, task in tasks.items()}
    health = _task_id(bench_, f"{plan}-spikes", HEALTH)
    visual = _task_id(bench_, f"{plan}-spikes", VISUAL)
    finalize_edges = store_json(bench_, "task", "deps", f"{plan}-spikes-finalize")
    first_branch = _branch_head(identity, VISUAL)
    first_design = _design_on_board(bench_, native)

    # 1b. The first re-finish, with nothing edited, dispatches nothing in the spikes project.
    turns_before = _turns(bench_)
    fresh = _refinish(bench_, native)
    fresh_prompts = _prompts_since(bench_, turns_before)

    # 2. The design review changed what the user sees: the manager regenerates the visual
    # spike and the task built on it, then re-finishes the plan.
    mark(
        bench_,
        SPIKES[VISUAL].marker,
        *_visual_commands(bench_, native, SECOND_ENTRIES, second_images),
    )
    _render(
        bench_, "plan-task", "task", visual, *_spike_answers(spike_what(VISUAL, SECOND_DEFINITION))
    )
    _render(
        bench_,
        "plan-task",
        "task",
        tasks["docs"],
        *_task_answers("docs", DOCS_WITH_BLUE, native, *TASK_SPIKES["docs"]),
    )
    route_before_edit = _content(bench_, tasks["route"])
    docs_before_edit = _content(bench_, tasks["docs"])
    turns_before = _turns(bench_)
    edited = _refinish(bench_, native)
    route_after_edit = _content(bench_, tasks["route"])
    docs_after_edit = _content(bench_, tasks["docs"])
    second_branch = _branch_head(identity, VISUAL)
    git("fetch", "-q", "origin", cwd=identity.publication)
    ancestry = ["git", "merge-base", "--is-ancestor", first_branch.head, second_branch.head]
    descends = (
        # Exit 1 is the answer "not an ancestor", which the conftest `git` helper would raise on.
        subprocess.run(ancestry, cwd=identity.publication, check=False).returncode == 0
    )
    visual_prompts = [
        one for one in _prompts_since(bench_, turns_before) if SECOND_DEFINITION in one
    ]
    second_design = _design_on_board(bench_, native)

    # 3. Only a task built on a spike is reworded: nothing runs in the spikes project.
    _render(
        bench_,
        "plan-task",
        "task",
        tasks["docs"],
        *_task_answers("docs", DOCS_REWORDED, native, *TASK_SPIKES["docs"]),
    )
    unchanged = _refinish(bench_, native)

    # 4. A spike edited while its re-run is under way.
    hold = tmp / "release-health"
    mark(bench_, SPIKES[HEALTH].marker, *_health_commands(bench_, hold))
    _render(
        bench_, "plan-task", "task", health, *_spike_answers(spike_what(HEALTH, "Measure p99."))
    )
    before = set(runs_of(bench_))
    started = _popen(bench_, "midrun", "just", "finish-plan", plan, BOARD)
    running: dict[str, str] = {}

    def health_running() -> bool:
        spikes_run = _spikes_run_started(bench_, before)
        if spikes_run is None or _nodes(status(bench_, spikes_run)).get(HEALTH) != "running":
            return False
        running["run"] = spikes_run
        return True

    try:
        _until(started.process, "the edited spike running", health_running)
        _render(
            bench_,
            "plan-task",
            "task",
            health,
            *_spike_answers(spike_what(HEALTH, "Measure p99 and p999.")),
        )
        hold.touch()
    finally:
        midrun = _refinished(bench_, native, before, _finished(started))

    # 5. The next re-finish re-runs that spike, whose worker asks its manager a question.
    mark(bench_, SPIKES[HEALTH].marker, *_health_commands(bench_, asks=True))
    before = set(runs_of(bench_))
    started = _popen(bench_, "asked", "just", "finish-plan", plan, BOARD)
    try:
        answered = _answer_the_question(bench_, started.process, before)
    finally:
        asked = _refinished(bench_, native, before, _finished(started))
    received = tmp / "answered"

    # 6. The visual spike is regenerated and its re-finish is interrupted: the spikes run is
    # stopped while finalize runs, after the spike re-ran; the next re-finish runs finalize.
    finalize_hold = tmp / "release-finalize"
    mark(bench_, FINALIZE_MARKER, *finalize_commands(bench_, native, finalize_hold))
    _render(
        bench_, "plan-task", "task", visual, *_spike_answers(spike_what(VISUAL, THIRD_DEFINITION))
    )
    before = set(runs_of(bench_))
    started = _popen(bench_, "interrupted", "just", "finish-plan", plan, BOARD)
    stopping: dict[str, str] = {}

    def finalize_running() -> bool:
        spikes_run = _spikes_run_started(bench_, before)
        if spikes_run is None or spikes_run not in _dispatched(bench_, "finalize"):
            return False
        stopping["run"] = spikes_run
        return True

    try:
        _until(started.process, "finalize running behind the edited spike", finalize_running)
        stopped = just(bench_, "stop", stopping["run"], seconds=120)
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr
    finally:
        finalize_hold.touch()
        interrupted = _refinished(bench_, native, before, _finished(started))
    mark(bench_, FINALIZE_MARKER, *finalize_commands(bench_, native))
    resumed = _refinish(bench_, native)

    yield Sequence(
        native,
        tasks,
        planned,
        first_spikes_run[0],
        _nodes(status(bench_, first_spikes_run[0])),
        {HEALTH: health, VISUAL: visual},
        first_answers,
        first_branch,
        first_design,
        finalize_edges,
        fresh,
        fresh_prompts,
        edited,
        route_before_edit,
        route_after_edit,
        docs_before_edit,
        docs_after_edit,
        second_branch,
        descends,
        visual_prompts,
        second_design,
        unchanged,
        midrun,
        running.get("run", ""),
        asked,
        answered,
        received.read_text(encoding="utf-8") if received.is_file() else "",
        interrupted,
        resumed,
        _by_run(bench_, "rework"),
        _by_run(bench_, "changed"),
        _dispatched(bench_, HEALTH),
        _dispatched(bench_, VISUAL),
        _dispatched(bench_, "finalize"),
    )
    for one in runs_of(bench_):
        if not one.startswith(".") and one != "human-items":
            just(bench_, "stop", one, seconds=120)


#: Every node a spikes run of this plan holds.
SPIKES_NODES = {"spike-brief": "done", "finalize": "done", HEALTH: "done", VISUAL: "done"}


def test_a_first_plan_runs_each_spike_once_then_finalize(sequence: Sequence) -> None:
    assert sequence.planned.returncode == 0, sequence.planned.stdout + sequence.planned.stderr
    assert sequence.first_spikes == SPIKES_NODES, sequence.first_spikes
    first = sequence.first_spikes_run
    for dispatched in (sequence.dispatched_health, sequence.dispatched_visual):
        assert dispatched.count(first) == 1, dispatched
    assert sequence.dispatched_finalize.count(first) == 1, sequence.dispatched_finalize
    assert _changed(sequence, first) == [HEALTH, VISUAL], sequence.changed


def _changed(sequence: Sequence, run_id: str) -> list[str]:
    """The `changed_dependencies` the doubled finalize read in ``run_id``, sorted."""
    read = sequence.changed.get(run_id, [])
    assert len(read) == 1, (run_id, sequence.changed)
    found = json.loads(read[0])
    assert isinstance(found, list), read
    return sorted(str(one) for one in found)


def _redone(refinish: Refinish) -> list[object]:
    redone = refinish.spikes_launch.get("redone") or []
    assert isinstance(redone, list), refinish.spikes_launch
    return sorted(redone, key=lambda one: str(one.get("node")) if isinstance(one, dict) else "")


def test_the_first_refinish_with_nothing_edited_dispatches_nothing(sequence: Sequence) -> None:
    fresh = sequence.fresh
    assert fresh.result.returncode == 0, fresh.result.stdout + fresh.result.stderr
    assert fresh.spikes == SPIKES_NODES, fresh.spikes
    assert _redone(fresh) == [], fresh.spikes_launch
    for dispatched in (
        sequence.dispatched_health,
        sequence.dispatched_visual,
        sequence.dispatched_finalize,
    ):
        assert fresh.spikes_run not in dispatched, (fresh.spikes_run, dispatched)
    assert not [one for one in sequence.fresh_prompts if SPIKE_BRIEF_MARKER in one]
    assert fresh.spikes_run not in sequence.rework, sequence.rework


def test_finalize_depends_on_every_spike_and_not_on_the_brief_writer(sequence: Sequence) -> None:
    edges = json.dumps(sequence.finalize_edges)
    for spike in sequence.spike_tasks.values():
        assert f'"{spike}"' in edges, (spike, edges)
    assert f"authoring:{sequence.native}-spikes-brief" not in edges, edges


def test_the_first_finalize_reworks_every_task_the_draft_built_on_a_spike(
    sequence: Sequence,
) -> None:
    # Every spike ran, and the draft answered both tasks as built on one, so both were reworked.
    reworked = sorted(sequence.rework.get(sequence.first_spikes_run, []))
    assert reworked == sorted(TASK_SPIKES), sequence.rework
    named = {node: list(spikes) for node, spikes in TASK_SPIKES.items()}
    assert sequence.first_answers == named, sequence.first_answers


def test_a_refinish_reruns_only_the_edited_spike_on_its_branch(sequence: Sequence) -> None:
    edited = sequence.edited
    assert edited.result.returncode == 0, edited.result.stdout + edited.result.stderr
    assert edited.spikes == SPIKES_NODES, edited.spikes
    second_run = edited.spikes_run
    assert sequence.dispatched_visual.count(second_run) == 1, sequence.dispatched_visual
    assert second_run not in sequence.dispatched_health, sequence.dispatched_health
    assert _redone(edited) == [
        {"node": "finalize", "reason": "dependent", "of": VISUAL},
        {"node": VISUAL, "reason": "changed"},
    ], edited.spikes_launch
    assert sequence.second_branch.name == sequence.first_branch.name, (
        sequence.first_branch,
        sequence.second_branch,
    )
    assert sequence.second_branch.head != sequence.first_branch.head, sequence.second_branch
    assert sequence.branch_descends, (sequence.first_branch, sequence.second_branch)


def test_finalize_runs_behind_the_rerun_spike_and_reworks_exactly_the_task_built_on_it(
    sequence: Sequence,
) -> None:
    second_run = sequence.edited.spikes_run
    assert sequence.dispatched_finalize.count(second_run) == 1, sequence.dispatched_finalize
    assert _changed(sequence, second_run) == [VISUAL], sequence.changed
    assert sequence.rework.get(second_run) == ["docs"], sequence.rework
    assert second_run in sequence.docs_after_edit, sequence.docs_after_edit
    assert second_run not in sequence.docs_before_edit, sequence.docs_before_edit
    assert sequence.route_after_edit == sequence.route_before_edit


def test_the_finishing_run_holds_no_finalize_and_reaches_the_approval(sequence: Sequence) -> None:
    edited = sequence.edited
    assert "finalize" not in edited.members and "spike-evidence" not in edited.members, (
        edited.members
    )
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


def test_a_refinish_with_no_spike_edited_runs_nothing_and_reworks_nothing(
    sequence: Sequence,
) -> None:
    unchanged = sequence.unchanged
    assert unchanged.result.returncode == 0, unchanged.result.stdout + unchanged.result.stderr
    assert unchanged.spikes == SPIKES_NODES, unchanged.spikes
    quiet = unchanged.spikes_run
    for dispatched in (
        sequence.dispatched_health,
        sequence.dispatched_visual,
        sequence.dispatched_finalize,
    ):
        assert quiet not in dispatched, (quiet, dispatched)
    assert quiet not in sequence.rework, sequence.rework
    assert not unchanged.spikes_launch.get("redone"), unchanged.spikes_launch


def test_an_edit_to_a_task_built_on_spikes_is_not_refused(sequence: Sequence) -> None:
    assert sequence.unchanged.finishing.get("check") == "done", sequence.unchanged.finishing


def test_a_spike_edited_while_it_runs_finishes_that_run(sequence: Sequence) -> None:
    midrun = sequence.midrun
    assert midrun.result.returncode == 0, midrun.result.stdout + midrun.result.stderr
    assert sequence.midrun_running == midrun.spikes_run, (sequence.midrun_running, midrun)
    assert midrun.spikes == SPIKES_NODES, midrun.spikes
    assert sequence.dispatched_health.count(midrun.spikes_run) == 1, sequence.dispatched_health
    assert midrun.spikes_run not in sequence.dispatched_visual, sequence.dispatched_visual


def test_a_spike_edited_while_it_ran_reruns_on_the_next_refinish(sequence: Sequence) -> None:
    asked = sequence.asked
    assert asked.result.returncode == 0, asked.result.stdout + asked.result.stderr
    assert asked.spikes == SPIKES_NODES, asked.spikes
    assert sequence.dispatched_health.count(asked.spikes_run) == 1, sequence.dispatched_health
    assert asked.spikes_run not in sequence.dispatched_visual, sequence.dispatched_visual
    redone = asked.spikes_launch.get("redone")
    assert redone == [
        {"node": "finalize", "reason": "dependent", "of": HEALTH},
        {"node": HEALTH, "reason": "changed"},
    ], redone
    assert sequence.dispatched_finalize.count(asked.spikes_run) == 1, sequence.dispatched_finalize
    assert _changed(sequence, asked.spikes_run) == [HEALTH], sequence.changed
    assert sorted(sequence.rework.get(asked.spikes_run, [])) == ["docs", "route"], sequence.rework


def test_the_spikes_step_waits_through_a_spikes_question_with_no_adopt(
    sequence: Sequence,
) -> None:
    """The health worker's blocking question held its run until the manager answered it; the
    step's one engine watch waited through it, and the run went on with nobody adopting it."""
    answered = sequence.answered
    assert QUESTION in answered.question, answered.question
    assert answered.reply.returncode == 0, answered.reply.stdout + answered.reply.stderr
    assert ANSWER in sequence.answer_received, sequence.answer_received
    assert sequence.asked.spikes_launch.get("adoptions") == 0, sequence.asked.spikes_launch
    assert sequence.asked.finishing.get("spikes") == "done", sequence.asked.finishing


def test_an_interrupted_refinish_ends_before_finalize_settles(sequence: Sequence) -> None:
    interrupted = sequence.interrupted
    assert interrupted.result.returncode != 0, interrupted.result.stdout
    assert sequence.dispatched_visual.count(interrupted.spikes_run) == 1
    assert interrupted.spikes.get(VISUAL) == "done", interrupted.spikes
    assert interrupted.spikes.get("finalize") != "done", interrupted.spikes
    assert interrupted.finishing.get("spikes") != "done", interrupted.finishing


def test_the_next_refinish_runs_finalize_over_the_spike_the_interrupted_one_reran(
    sequence: Sequence,
) -> None:
    resumed = sequence.resumed
    assert resumed.result.returncode == 0, resumed.result.stdout + resumed.result.stderr
    assert resumed.spikes == SPIKES_NODES, resumed.spikes
    for dispatched in (sequence.dispatched_health, sequence.dispatched_visual):
        assert resumed.spikes_run not in dispatched, dispatched
    assert sequence.dispatched_finalize.count(resumed.spikes_run) == 1
    assert _changed(sequence, resumed.spikes_run) == [VISUAL], sequence.changed
    assert sequence.rework.get(resumed.spikes_run) == ["docs"], sequence.rework
