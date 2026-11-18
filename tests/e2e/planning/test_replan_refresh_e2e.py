"""A replan starts from its run's current state, and its working copy holds none of the run's.

One bench (`planning_bench`) whose `board` source is configured as this host's `plans` board
is: public, so a copy onto it from the private `authoring` source withholds provenance and
its items carry no `onetaskgraph.origin` (manager ruling c1.0). Through the real recipes:

* **A board-copied plan, through the planning road** (`just plan <brief> board`). Its board
  copy holds newer statuses and a run's settlement, landing and amendment; one authoring task
  holds a stale settlement and amendment of its own. When the draft step starts — read by the
  doubled planner, which records the store's own answers before it does anything — every
  authoring task holds its board task's status and no run-written key, its title, content and
  stored template answers unchanged, matched to its board task through its own copy link. A
  `just copy-plan` afterwards leaves every run-written value on the board as it was and adds
  no item. The draft's own task tells the planner where amendments are read and that
  run-written keys stay out of the plan.
* **A task a live `add` created on that board.** The pinned store cannot copy it back into
  the plan from a board that withholds provenance, so `just refresh-plan` refuses before any
  write, naming it and what a person does next (manager ruling a1).
* **The same import where the store can make it**, onto a private board that keeps
  provenance: the task is brought into the plan holding no run-written key, its dependency on
  an existing task remapped onto the plan, `just check-plan` accepts the plan, and a `just
  copy-plan` updates that same board item in place. Between the import and that copy the
  imported task holds no copy link to the board, so the `onetaskgraph.origin` the store
  stamped on that AUTHORING task is its only link, and a second `just refresh-plan` pairs
  through it: the task takes the board's newer status and keeps its authored title, never
  imported again nor copied over from the board. Ruling c1.0 forbids reading a BOARD item's
  origin, which a public board withholds; it does not forbid this one, and the refresh never
  reads the board's.
* **A board-copied plan whose authoring project holds `onepipeline.driver`.** The refresh
  step fails naming the project's file and the key, and the authoring copy is byte-identical.
* **A plan with no board copy**, its tasks and project holding run-written keys: the refresh
  step settles done and the authoring copy is byte-identical when the draft starts.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, NamedTuple

import pytest
import run_written_keys
from drafting import DESIGN_MARKER, DRAFT_MARKER, design_commands
from github_board import _serving_board
from planning_bench import (
    BOARD,
    Bench,
    bench,
    just,
    local_source,
    mark,
    run,
    runs_of,
    status,
    store,
    store_json,
    store_setting,
)

pytestmark = [pytest.mark.usefixtures("module_tmpdir"), pytest.mark.xdist_group("replan-refresh")]

STORE = ".venv/bin/onetaskgraph"
RESOLVE = "scripts/onepipeline.sh template resolve"
NODES = ("landed", "moving", "amended", "stale")
DONE: dict[str, object] = {"status": "done", "branch": "me/landed", "landing": "landed"}
BOARD_AMENDMENT = "- BOARD-AMENDMENT: the route reports its build commit."
#: A second board, declared private, so a copy onto it keeps provenance and the store can copy
#: a task back from it into the plan.
PRIVATE_BOARD = "keptboard"
#: What the draft's task tells the planner, as `templates/planning/planning-draft.md.j2` words it.
DRAFT_GUIDANCE = (
    "Read each task's `onepipeline.amendment` from that board copy",
    "`onetaskgraph task show <that item>",
    "never restate it in the task's text",
    "are the run's record, never written into the plan",
)


def _sh(bench_: Bench, script: str) -> str:
    done = run(bench_, "bash", "-c", f"set -euo pipefail; {script}", seconds=180)
    assert done.returncode == 0, f"{script}\n{done.stdout}{done.stderr}"
    return done.stdout


def _plan(bench_: Bench, native: str) -> tuple[str, dict[str, str]]:
    """An authoring plan rendered from this host's templates, so its tasks hold answers."""
    goal = json.dumps({"text": "Give the service a health route operators can poll"})
    _sh(
        bench_,
        f"{RESOLVE} plan-description --json | {STORE} project create authoring --id {native} "
        f"--title {shlex.quote('Health route ' + native)} --template-loader - "
        "--var 'overview=Add a health route.' --metadata onepipeline.schema_version=3 "
        f"--metadata {shlex.quote('onepipeline.goal=' + goal)} --no-interactive >/dev/null",
    )
    plan, tasks = f"authoring:{native}", {}
    for node in NODES:
        created = _sh(
            bench_,
            f"{RESOLVE} plan-task --json | {STORE} task create authoring --project {plan} "
            f"--title {shlex.quote('feat: ' + node)} --template-loader - "
            f"--var 'what=Build the {node} part.' --var 'why=Operators need it.' "
            f"--var 'acceptance_criteria=[\"A request-level test drives {node}.\"]' "
            f"--metadata 'onepipeline.id={json.dumps(node)}' "
            "--metadata 'onepipeline.persona=\"engineer\"' "
            "--metadata 'orchestrator.unit=\"API\"' --no-interactive --json",
        )
        tasks[node] = str(json.loads(created)["items"][0]["id"])
    return plan, tasks


def _set(bench_: Bench, kind: str, item: str, values: Mapping[str, object]) -> None:
    for key, value in values.items():
        answered = store(bench_, kind, "metadata", "set", item, key, json.dumps(value))
        assert answered.returncode == 0, answered.stdout + answered.stderr


def _shown(bench_: Bench, kind: str, item: str) -> dict[str, Any]:
    items = store_json(bench_, kind, "show", item)["items"]
    assert isinstance(items, list) and len(items) == 1, items
    found = items[0]["item"]
    assert isinstance(found, dict), items
    return found


def _listed(bench_: Bench, source: str, project: str) -> list[dict[str, Any]]:
    items = store_json(bench_, "task", "list", "--source", source, "--project", project, "--all")[
        "items"
    ]
    assert isinstance(items, list), items
    return items


def _run_written(metadata: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in metadata.items() if k in run_written_keys.RUN_WRITTEN_KEYS}


def _answers(bench_: Bench, task: str) -> str:
    answered = store(bench_, "task", "answers", task, "--json")
    assert answered.returncode == 0, answered.stdout + answered.stderr
    return answered.stdout


def _files(bench_: Bench, plan: str) -> dict[str, str]:
    """The digest of every file the plan store keeps ``plan`` and its tasks in."""
    paths = [str(_shown(bench_, "project", plan)["location"]["path"])]
    paths += [str(one["item"]["location"]["path"]) for one in _listed(bench_, "authoring", plan)]
    return {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in paths}


def _snapshot_at_draft(bench_: Bench, plan: str, into: Path) -> None:
    """Have the doubled planner record the plan as the store answers when its turn starts."""
    into.mkdir()
    lines = [
        f"{STORE} project show {plan} --json --no-interactive > {into}/project.json",
        f"{STORE} task list --source authoring --project {plan} --all --json --no-interactive "
        f"> {into}/tasks.json",
        f"for id in $(jq -r '.items[].id' {into}/tasks.json); do "
        f'{STORE} task answers "$id" --json --no-interactive > "{into}/answers-${{id#*:}}.json" '
        "2>/dev/null || true; done",
        f"jq -r '.items[0].item.location.path' {into}/project.json | xargs sha256sum "
        f"> {into}/files.sha",
        f"jq -r '.items[].item.location.path' {into}/tasks.json | xargs sha256sum "
        f">> {into}/files.sha",
    ]
    mark(bench_, DRAFT_MARKER, *lines)


def _planned(
    bench_: Bench, native: str
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any], str]:
    """`just plan` over the existing plan `authoring:<native>`: its result, and its planning
    run's status and id.

    The run is the planning run this `just plan` added, because its id is made from the
    brief's title rather than from the plan's id, and one bench plans several plans.
    """
    brief = bench_.tmp / f"{native}.brief.md"
    brief.write_text(
        f"# Replan {native}\n\n## What\n\nReplan the health route.\n\n"
        f"Plan project: authoring:{native}\n",
        encoding="utf-8",
    )
    mark(bench_, DESIGN_MARKER, *design_commands(native))
    before = set(runs_of(bench_))
    planned = just(bench_, "plan", str(brief), BOARD)
    (planning,) = [
        one for one in runs_of(bench_) if one not in before and one.endswith("-planning")
    ]
    return planned, status(bench_, planning), planning


def _nodes(answer: dict[str, Any]) -> dict[str, str]:
    return {str(node["id"]): str(node.get("status")) for node in answer["nodes"]}


class Boarded(NamedTuple):
    plan: str
    tasks: dict[str, str]
    board: str
    statuses: dict[str, str]
    titles: dict[str, tuple[str, str]]
    answers: dict[str, str]
    board_keys_before: dict[str, dict[str, Any]]
    planned: subprocess.CompletedProcess[str]
    planning: dict[str, Any]
    at_draft: Path
    board_origins: list[str]
    project_metadata_before: dict[str, Any]
    copied: subprocess.CompletedProcess[str]
    board_items_before: int
    board_items_after: int
    board_keys_after: dict[str, dict[str, Any]]
    draft_prompts: list[str]
    journal: str


class LiveAdded(NamedTuple):
    """A refresh over a board copy holding a task a live `add` created."""

    plan: str
    board_task: str
    ran: subprocess.CompletedProcess[str]
    files_before: dict[str, str]
    files_after: dict[str, str]


class Imported(NamedTuple):
    """A task a live `add` created on a board that keeps provenance, brought into the plan."""

    plan: str
    tasks: dict[str, str]
    board: str
    board_task: str
    ran: subprocess.CompletedProcess[str]
    imported: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    checked: subprocess.CompletedProcess[str]
    copied: subprocess.CompletedProcess[str]
    board_items_before: int
    board_items_after: int
    board_title: str
    refreshed_again: subprocess.CompletedProcess[str]
    imported_after_again: int
    status_after_again: list[str]


class Refused(NamedTuple):
    plan: str
    files_before: dict[str, str]
    files_after: dict[str, str]
    planned: subprocess.CompletedProcess[str]
    planning: dict[str, Any]
    refusal: str


class Unboarded(NamedTuple):
    plan: str
    files_before: dict[str, str]
    at_draft: Path
    planning: dict[str, Any]


class Sequence(NamedTuple):
    bench: Bench
    boarded: Boarded
    live_added: LiveAdded
    imported: Imported
    refused: Refused
    unboarded: Unboarded


def _board_keys(bench_: Bench, board: str) -> dict[str, dict[str, Any]]:
    keys = {
        str(one["id"]): _run_written(one["item"]["metadata"])
        for one in _listed(bench_, BOARD, board)
    }
    keys[board] = _run_written(_shown(bench_, "project", board)["metadata"])
    return keys


def _boarded(bench_: Bench, native: str) -> Boarded:
    plan, tasks = _plan(bench_, native)
    copied = just(bench_, "copy-plan", plan, BOARD)
    assert copied.returncode == 0, copied.stdout + copied.stderr
    board = str(_shown(bench_, "project", plan)["metadata"]["onetaskgraph.copies"][BOARD])
    on_board = {
        node: str(_shown(bench_, "task", task)["metadata"]["onetaskgraph.copies"][BOARD])
        for node, task in tasks.items()
    }
    # What the run wrote since: newer statuses, a settlement and landing, an amendment.
    for node, category in (("landed", "done"), ("moving", "in-progress")):
        moved = store(bench_, "task", "update", on_board[node], "--status", category)
        assert moved.returncode == 0, moved.stdout + moved.stderr
    _set(bench_, "task", on_board["landed"], {"onepipeline.settlement": DONE})
    _set(bench_, "task", on_board["landed"], {"onepipeline.landing": "landed"})
    _set(bench_, "task", on_board["amended"], {"onepipeline.amendment": BOARD_AMENDMENT})
    _set(bench_, "project", board, {"onepipeline.driver": {"session": "a-manager"}})
    # Stale run-written keys on the authoring copy, as a hand once put there.
    _set(
        bench_,
        "task",
        tasks["stale"],
        {"onepipeline.settlement": DONE, "onepipeline.amendment": "- STALE-AMENDMENT"},
    )
    statuses = {
        node: str(_shown(bench_, "task", on_board[node])["status"]["category"]) for node in NODES
    }
    titles = {
        node: (str(found["title"]), str(found["content"]))
        for node, task in tasks.items()
        for found in (_shown(bench_, "task", task),)
    }
    answers = {node: _answers(bench_, task) for node, task in tasks.items()}
    board_keys_before = _board_keys(bench_, board)
    project_metadata_before = _shown(bench_, "project", plan)["metadata"]
    at_draft = bench_.tmp / f"{native}-at-draft"
    _snapshot_at_draft(bench_, plan, at_draft)
    turns_before = _turn_count(bench_)
    planned, planning, planning_run = _planned(bench_, native)
    draft_prompts = _draft_prompts(bench_, turns_before)
    board_items_before = len(_listed(bench_, BOARD, board))
    copied = just(bench_, "copy-plan", plan, BOARD)
    return Boarded(
        plan,
        tasks,
        board,
        statuses,
        titles,
        answers,
        board_keys_before,
        planned,
        planning,
        at_draft,
        [
            str(one["item"]["metadata"]["onetaskgraph.origin"])
            for one in _listed(bench_, BOARD, board)
            if "onetaskgraph.origin" in one["item"]["metadata"]
        ],
        project_metadata_before,
        copied,
        board_items_before,
        len(_listed(bench_, BOARD, board)),
        _board_keys(bench_, board),
        draft_prompts,
        _journal(bench_, planning_run),
    )


def _turn_count(bench_: Bench) -> int:
    if not bench_.turn_log.is_file():
        return 0
    return len(bench_.turn_log.read_text(encoding="utf-8").splitlines())


def _draft_prompts(bench_: Bench, since: int) -> list[str]:
    if not bench_.turn_log.is_file():
        return []
    lines = bench_.turn_log.read_text(encoding="utf-8").splitlines()[since:]
    prompts = [str(json.loads(line).get("prompt")) for line in lines]
    return [prompt for prompt in prompts if DRAFT_MARKER in prompt]


def _live_add(bench_: Bench, destination: str, board: str, depends_on: str) -> str:
    """A task a live `add` created on ``board``, depending on ``depends_on``; answers its id."""
    added = _sh(
        bench_,
        "printf '## What\\n\\nProbe the route.\\n\\n## Acceptance criteria\\n\\n- It answers.\\n' "
        f"> .added.md && {STORE} task create {destination} --project {board} "
        "--title 'feat: probe' --body-file .added.md --metadata 'onepipeline.id=\"probe\"' "
        "--metadata 'onepipeline.persona=\"engineer\"' "
        f"--metadata 'onepipeline.settlement={json.dumps({'status': 'failed'})}' "
        f"--depends-on {depends_on} --no-interactive --json; rm -f .added.md",
    )
    return str(json.loads(added)["items"][0]["id"])


def _copied_to(bench_: Bench, plan: str, destination: str) -> tuple[str, dict[str, str]]:
    """``plan`` copied to ``destination``: the board project and each task's board item."""
    copied = just(bench_, "copy-plan", plan, destination)
    assert copied.returncode == 0, copied.stdout + copied.stderr
    board = str(_shown(bench_, "project", plan)["metadata"]["onetaskgraph.copies"][destination])
    items = {
        str(one["id"]): str(one["item"]["metadata"]["onetaskgraph.copies"][destination])
        for one in _listed(bench_, "authoring", plan)
    }
    return board, items


def _live_added(bench_: Bench, native: str) -> LiveAdded:
    plan, tasks = _plan(bench_, native)
    board, items = _copied_to(bench_, plan, BOARD)
    board_task = _live_add(bench_, BOARD, board, items[tasks["landed"]])
    before = _files(bench_, plan)
    ran = just(bench_, "refresh-plan", plan, BOARD)
    return LiveAdded(plan, board_task, ran, before, _files(bench_, plan))


def _imported(bench_: Bench, native: str) -> Imported:
    plan, tasks = _plan(bench_, native)
    board, items = _copied_to(bench_, plan, PRIVATE_BOARD)
    board_task = _live_add(bench_, PRIVATE_BOARD, board, items[tasks["landed"]])
    ran = just(bench_, "refresh-plan", plan, PRIVATE_BOARD)

    def brought_in() -> list[dict[str, Any]]:
        return [
            one
            for one in _listed(bench_, "authoring", plan)
            if one["item"]["metadata"].get("onetaskgraph.origin") == board_task
        ]

    edges: list[dict[str, Any]] = []
    for one in brought_in():
        found = store_json(bench_, "task", "deps", str(one["id"]))["items"]
        assert isinstance(found, list), found
        edges += [edge for edge in found if edge["from"]["id"] == one["id"]]
        retitled = store(bench_, "task", "update", one["id"], "--title", "feat: probe the route")
        assert retitled.returncode == 0, retitled.stdout + retitled.stderr
    # The run then moves the board task on. Before any copy gives the imported task a copy
    # link, the origin the store stamped on that AUTHORING task is its only link to the board;
    # the second refresh pairs through it, never the board item's origin, so the imported task
    # takes the board's status by a targeted write that keeps its authored title, and is
    # neither brought in a second time nor copied over from the board.
    moved = store(bench_, "task", "update", board_task, "--status", "done")
    assert moved.returncode == 0, moved.stdout + moved.stderr
    again = just(bench_, "refresh-plan", plan, PRIVATE_BOARD)
    imported = brought_in()
    imported_again = len(imported)
    status_again = [
        f"{one['item']['status']['category']} {one['item']['title']}" for one in imported
    ]
    checked = just(bench_, "check-plan", plan)
    before = len(_listed(bench_, PRIVATE_BOARD, board))
    copied = just(bench_, "copy-plan", plan, PRIVATE_BOARD)
    after = _listed(bench_, PRIVATE_BOARD, board)
    title = next((str(one["item"]["title"]) for one in after if one["id"] == board_task), "")
    return Imported(
        plan,
        tasks,
        board,
        board_task,
        ran,
        imported,
        edges,
        checked,
        copied,
        before,
        len(after),
        title,
        again,
        imported_again,
        status_again,
    )


def _refused(bench_: Bench, native: str) -> Refused:
    plan, _ = _plan(bench_, native)
    copied = just(bench_, "copy-plan", plan, BOARD)
    assert copied.returncode == 0, copied.stdout + copied.stderr
    _set(bench_, "project", plan, {"onepipeline.driver": None})
    before = _files(bench_, plan)
    planned, planning, planning_run = _planned(bench_, native)
    return Refused(
        plan, before, _files(bench_, plan), planned, planning, _journal(bench_, planning_run)
    )


def _journal(bench_: Bench, planning: str) -> str:
    """The planning run's journal, where its refresh step's failure is recorded."""
    return (bench_.runs / planning / "events.jsonl").read_text(encoding="utf-8")


def _unboarded(bench_: Bench, native: str) -> Unboarded:
    plan, tasks = _plan(bench_, native)
    _set(bench_, "task", tasks["landed"], {"onepipeline.settlement": DONE})
    _set(bench_, "task", tasks["landed"], {"onepipeline.change_url": "https://example.invalid/1"})
    _set(bench_, "task", tasks["amended"], {"onepipeline.amendment": "- OWN-AMENDMENT"})
    _set(bench_, "project", plan, {"onepipeline.driver": {"session": "a-manager"}})
    before = _files(bench_, plan)
    at_draft = bench_.tmp / f"{native}-at-draft"
    _snapshot_at_draft(bench_, plan, at_draft)
    _, planning, _ = _planned(bench_, native)
    return Unboarded(plan, before, at_draft, planning)


@pytest.fixture(scope="module")
def sequence(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Iterator[Sequence]:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp = tmp_path_factory.mktemp("replan-refresh")
    (tmp / PRIVATE_BOARD).mkdir()
    # A copy onto a public source reads the configured human-task boards too; the loopback
    # server answers them as empty boards under a fixture credential, so nothing reaches GitHub.
    with _serving_board() as remote:
        bench_ = bench(
            tmp,
            oneharness_bin,
            "e2e-replan-refresh",
            # As this host's `plans` board is: public, so a copy onto it withholds provenance.
            **{store_setting(BOARD, "visibility"): "public"},
            **local_source(PRIVATE_BOARD, tmp / PRIVATE_BOARD),
            **remote,
        )
        tag = os.getpid()
        sequence = Sequence(
            bench_,
            _boarded(bench_, f"refreshed-{tag}"),
            _live_added(bench_, f"live-added-{tag}"),
            _imported(bench_, f"imported-{tag}"),
            _refused(bench_, f"driven-{tag}"),
            _unboarded(bench_, f"unboarded-{tag}"),
        )
    yield sequence
    for one in runs_of(bench_):
        if not one.startswith("."):
            just(bench_, "stop", one, seconds=120)


def _at_draft(snapshot: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    project = json.loads((snapshot / "project.json").read_text(encoding="utf-8"))
    tasks = json.loads((snapshot / "tasks.json").read_text(encoding="utf-8"))
    return project["items"][0]["item"], tasks["items"]


def test_the_refresh_runs_first_and_the_draft_starts_from_the_boards_statuses(
    sequence: Sequence,
) -> None:
    boarded = sequence.boarded
    nodes = _nodes(boarded.planning)
    assert nodes["refresh"] == "done" and nodes["draft"] == "done", (
        nodes,
        [line for line in boarded.journal.splitlines() if "refresh" in line][-6:],
    )
    _, tasks = _at_draft(boarded.at_draft)
    held = {str(one["item"]["metadata"].get("onepipeline.id")): one for one in tasks}
    for node in NODES:
        assert held[node]["item"]["status"]["category"] == boarded.statuses[node], node
        title, content = boarded.titles[node]
        assert (held[node]["item"]["title"], held[node]["item"]["content"]) == (title, content)
        answers = boarded.at_draft / f"answers-{held[node]['id'].split(':', 1)[1]}.json"
        assert answers.read_text(encoding="utf-8") == boarded.answers[node], node


def test_no_authoring_task_holds_a_run_written_key_when_the_draft_starts(
    sequence: Sequence,
) -> None:
    project, tasks = _at_draft(sequence.boarded.at_draft)
    assert {str(one["id"]): _run_written(one["item"]["metadata"]) for one in tasks} == {
        str(one["id"]): {} for one in tasks
    }
    assert project["metadata"] == sequence.boarded.project_metadata_before


def test_the_board_withholds_provenance_so_the_match_is_the_authoring_copy_link(
    sequence: Sequence,
) -> None:
    """No board item carries an origin, so the statuses the draft started from were matched
    through each authoring task's own copy link."""
    assert sequence.boarded.board_origins == []


def test_a_copy_back_writes_none_of_the_runs_state_and_adds_no_item(sequence: Sequence) -> None:
    boarded = sequence.boarded
    assert boarded.copied.returncode == 0, boarded.copied.stdout + boarded.copied.stderr
    assert boarded.board_keys_after == boarded.board_keys_before
    assert boarded.board_items_after == boarded.board_items_before


def test_a_live_added_task_the_store_cannot_bring_in_is_refused_before_any_write(
    sequence: Sequence,
) -> None:
    live = sequence.live_added
    stderr = live.ran.stderr
    assert live.ran.returncode == 1, live.ran.stdout + stderr
    assert f"{live.board_task} (feat: probe)" in stderr, stderr
    assert "add each to" in stderr and "drop it from the run" in stderr, stderr
    assert live.files_after == live.files_before


def test_where_the_store_can_bring_it_in_the_task_is_imported_and_remapped(
    sequence: Sequence,
) -> None:
    """The board-only task is imported once and paired from then on through the origin
    the store stamped on the imported AUTHORING task — its only link, since it holds no copy
    link to the board — so a second refresh gives it the board's status by a targeted write,
    keeping its authored title, rather than importing it again or copying the board item over
    it; a BOARD item's origin, which a public board withholds (ruling c1.0), is never what
    pairs it."""
    imported = sequence.imported
    assert imported.ran.returncode == 0, imported.ran.stdout + imported.ran.stderr
    (task,) = imported.imported
    assert task["item"]["metadata"].get("onepipeline.id") == "probe"
    assert _run_written(task["item"]["metadata"]) == {}
    assert [edge["to"]["id"] for edge in imported.edges] == [imported.tasks["landed"]]
    assert imported.checked.returncode == 0, imported.checked.stdout + imported.checked.stderr
    assert imported.copied.returncode == 0, imported.copied.stdout + imported.copied.stderr
    assert imported.board_items_after == imported.board_items_before
    assert imported.board_title == "feat: probe the route"
    again = imported.refreshed_again
    assert again.returncode == 0, again.stdout + again.stderr
    assert imported.imported_after_again == 1
    assert imported.status_after_again == ["done feat: probe the route"]


def test_the_draft_is_told_where_amendments_are_read_and_that_run_keys_stay_out(
    sequence: Sequence,
) -> None:
    prompts = sequence.boarded.draft_prompts
    assert prompts, "no recorded turn carried the draft's task"
    flat = " ".join(" ".join(prompts).split())
    for phrase in DRAFT_GUIDANCE:
        assert " ".join(phrase.split()) in flat, phrase


def test_a_project_holding_a_run_written_key_fails_the_refresh_and_writes_nothing(
    sequence: Sequence,
) -> None:
    refused = sequence.refused
    nodes = _nodes(refused.planning)
    assert nodes["refresh"] == "failed" and nodes["draft"] != "done", nodes
    assert refused.files_after == refused.files_before
    project_file = next(iter(refused.files_before))
    assert project_file in refused.refusal and "onepipeline.driver" in refused.refusal, (
        refused.refusal[-2000:]
    )


def test_a_plan_with_no_board_copy_is_left_byte_identical(sequence: Sequence) -> None:
    unboarded = sequence.unboarded
    assert _nodes(unboarded.planning)["refresh"] == "done", unboarded.planning
    recorded = {
        line.split()[1]: line.split()[0]
        for line in (unboarded.at_draft / "files.sha").read_text(encoding="utf-8").splitlines()
    }
    assert recorded == unboarded.files_before
