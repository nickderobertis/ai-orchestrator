"""A plan's budget records, and the Budgets sections a task and a design document render.

The record shapes are `templates/plan-task-budgets.md.j2`'s `budgets` schema and
`templates/plan-description.md.j2`'s `check_runtime_summary`; the design document's section is
`templates/design-doc.md.j2`'s `budgets` block. Everything here goes through what a planner
and a design-doc dispatch run: the pinned engine's `template resolve` piped into the locked
plan store's own rendering — `template render` for a record the store refuses before writing
anything, and `task render` and `document create` in a scratch folder of Markdown for a plan
whose tasks own budgets and whose design document is written from those records. Every
assertion reads what the store rendered or the refusal it printed.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] The approved acceptance
criteria of the task that added this module place these journeys in the planning journey
project (`tests/e2e/planning/`), whose shared fixtures (`project_fixtures`) they reuse. The
module measured about 1.5 s of wall time on this host, single process or across four
workers, so the planning project's wide cache key costs the gate little when it reruns it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import NamedTuple

import pytest
from project_fixtures import designed, owning, write_plan_project
from published_tools import ONETASKGRAPH_BIN

from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.xdist_group("budget-sections")

#: The pinned engine, whose `template resolve` states each host template.
ENGINE = REPO_ROOT / "node_modules" / ".bin" / "onepipeline"
#: The scratch folder of Markdown a plan here is written into.
SOURCE = "budgetsections"
#: The keys a version-3 changed budget's `previous` holds, exactly.
PREVIOUS_KEYS = (
    "name",
    "measure",
    "inner_measure_reason",
    "unit",
    "direction",
    "threshold",
    "workload",
    "source",
    "command",
    "check_runtime_seconds",
)
#: What every task of a fixture plan answers besides its budgets.
TASK_ANSWERS = {
    "what": "Make the service's pages and jobs fast enough to rely on.",
    "why": "Operators wait on these pages and jobs every day.",
    "acceptance_criteria": ["Each budget's command reports within its target."],
}


def _resolved(template: str) -> str:
    """The loader document the pinned engine states for this host's ``template``."""
    resolved = subprocess.run(
        [str(ENGINE), "template", "resolve", template, "--json"]
        + ["--template-root", str(REPO_ROOT / "templates")],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert resolved.returncode == 0, resolved.stderr
    return resolved.stdout


def _rendered(
    template: str, answers: Mapping[str, object], tmp_path: Path
) -> subprocess.CompletedProcess[str]:
    """``answers`` rendered by the locked store through the engine's ``template``."""
    written = tmp_path / f"{template}-answers.json"
    written.write_text(json.dumps(answers), encoding="utf-8")
    return subprocess.run(
        [str(ONETASKGRAPH_BIN), "template", "render", "--template-loader", "-"]
        + ["--answers", str(written), "--no-interactive"],
        cwd=REPO_ROOT,
        input=_resolved(template),
        text=True,
        capture_output=True,
        check=False,
    )


def _budget(
    budget_id: str,
    name: str,
    file_change: str,
    *,
    runtime: float,
    unit: str = "ms",
    direction: str = "max",
    threshold: float = 100,
    source: str = "direct",
    was: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """A version-3 record as a planner writes it; ``was`` overrides `previous` from it.

    A changed budget's `previous` starts as the record's own values and takes ``was``'s,
    so each fixture states only what moved.
    """
    record: dict[str, object] = {
        "schema_version": 3,
        "id": budget_id,
        "name": name,
        "basis": "measured",
        "repository": "github.com/example/service",
        "file": "services/api/budgets.yaml",
        "file_change": file_change,
        "measure": f"{name}, as an operator waits on it",
        "inner_measure_reason": "",
        "unit": unit,
        "direction": direction,
        "threshold": threshold,
        "workload": "200 operators at the busiest hour, 40 requests a second",
        "evidence": "A spike's report measured it at that workload.",
        "command": f"scripts/measure-{budget_id}.sh",
        "source": source,
        "check_runtime_seconds": runtime,
    }
    if was is not None:
        record["previous"] = {key: record[key] for key in PREVIOUS_KEYS} | dict(was)
    return record


def _legacy_changed() -> dict[str, object]:
    """A changed version-2 record, saved before `previous` existed."""
    record = _budget("report-export-size", "Report export size", "change", runtime=8)
    record.update(schema_version=2, unit="MB", threshold=5)
    return record


def _refusal(tmp_path: Path, budgets: list[dict[str, object]]) -> str:
    rendered = _rendered("plan-task", {**TASK_ANSWERS, "budgets": budgets}, tmp_path)
    assert rendered.returncode != 0, rendered.stdout
    return rendered.stderr


def _pointer(pointer: str) -> str:
    return f'the answer to "budgets" does not satisfy its schema at JSON pointer "{pointer}"'


CHANGED = _budget(
    "checkout-latency", "Checkout p95 latency", "change", runtime=2, was={"threshold": 200}
)


def test_a_version_3_changed_budget_without_previous_is_refused_naming_its_pointer(
    tmp_path: Path,
) -> None:
    record = {key: value for key, value in CHANGED.items() if key != "previous"}
    refused = _refusal(tmp_path, [record])
    assert _pointer("/0") in refused and '"previous" is a required property' in refused, refused


@pytest.mark.parametrize("missing", PREVIOUS_KEYS)
def test_a_previous_lacking_any_required_key_is_refused_naming_its_pointer(
    tmp_path: Path, missing: str
) -> None:
    previous = CHANGED["previous"]
    assert isinstance(previous, dict)
    record = CHANGED | {"previous": {k: v for k, v in previous.items() if k != missing}}
    refused = _refusal(tmp_path, [record])
    assert _pointer("/0/previous") in refused, refused
    assert f'"{missing}" is a required property' in refused, refused


def test_a_previous_carrying_another_key_is_refused_naming_its_pointer(tmp_path: Path) -> None:
    previous = CHANGED["previous"]
    assert isinstance(previous, dict)
    refused = _refusal(tmp_path, [CHANGED | {"previous": previous | {"evidence": "a spike"}}])
    assert _pointer("/0/previous") in refused, refused
    assert "'evidence' was unexpected" in refused, refused


def test_a_previous_value_is_held_to_the_records_own_constraint(tmp_path: Path) -> None:
    previous = CHANGED["previous"]
    assert isinstance(previous, dict)
    refused = _refusal(tmp_path, [CHANGED | {"previous": previous | {"direction": "lower"}}])
    assert _pointer("/0/previous/direction") in refused, refused


@pytest.mark.parametrize("file_change", ["add", "none"])
def test_an_added_or_unchanged_version_3_budget_carrying_previous_is_refused(
    tmp_path: Path, file_change: str
) -> None:
    refused = _refusal(tmp_path, [CHANGED | {"file_change": file_change}])
    assert _pointer("/0/previous") in refused, refused
    assert "False schema does not allow" in refused, refused


def test_a_version_2_changed_budget_without_previous_is_accepted_and_renders_as_before(
    tmp_path: Path,
) -> None:
    rendered = _rendered("plan-task", {**TASK_ANSWERS, "budgets": [_legacy_changed()]}, tmp_path)
    assert rendered.returncode == 0, rendered.stderr
    assert VERSION_2_SECTION in rendered.stdout, rendered.stdout


#: A version-2 changed budget's `## Budgets` section exactly as the template rendered it
#: before version 3: its runtime line unlabelled as an added cost and no previous value.
VERSION_2_SECTION = """## Budgets

### Budget report-export-size

- **Name:** Report export size
- **Id:** `report-export-size`
- **Measure:** Report export size, as an operator waits on it
- **Target:** at most 5 MB
- **Basis:** measured
- **Measurement source:** direct
- **Check runtime:** approximately 8 seconds
- **Workload:** 200 operators at the busiest hour, 40 requests a second
- **Evidence:** A spike's report measured it at that workload.
- **Command:** `scripts/measure-report-export-size.sh`
- **Registered in:** `services/api/budgets.yaml` of `github.com/example/service`, \
an entry this task changes
"""

#: A plan's saved plan-level answers, as an `orchestrator.plan-budgets` record holds them.
PLAN_BUDGETS: dict[str, object] = {
    "schema_version": 2,
    "sizing": "200 operators at the busiest hour, each opening the status page and a report.",
    "workload": "200 operators, 40 requests a second, 3 nightly jobs over 2 million rows.",
    "ten_x_summary": "At 10x the checkout's p95 latency rises first.",
    "ten_x": "At 2,000 operators the checkout's p95 latency rises first, which "
    "`checkout-latency` covers.",
    "checklist": [
        {
            "concern": "latency",
            "budget": "checkout-latency",
            "not_applicable": "",
            "summary": "",
            "in_scope": True,
        },
        {
            "concern": "spend",
            "budget": "",
            "not_applicable": "n/a because the plan adds no paid service.",
            "summary": "The plan adds no paid service.",
            "in_scope": True,
        },
        {
            "concern": "image retention",
            "budget": "",
            "not_applicable": "n/a because no image is stored.",
            "summary": "No image is stored by these changes.",
            "in_scope": False,
        },
    ],
    "repo_wide_effects": [],
    "realistic_data": [],
    "spike_findings": [
        {
            "spike": "spike-latency",
            "finding": "The checkout's p95 is 140 ms today.",
            "changed": "Its target tightens to 150 ms.",
            "summary": "The checkout's p95 is 140 ms today, so its target tightens to 150 ms.",
        }
    ],
}
#: What the plan says its expensive checks are.
RUNTIME_SUMMARY = (
    "Status page cold load is the expensive check: its browser journey exists only to feed it."
)


def test_check_runtime_summary_spanning_lines_is_refused_and_its_cap_is_stated(
    tmp_path: Path,
) -> None:
    answers = {"overview": "A plan.", **PLAN_BUDGETS, "check_runtime_summary": "one\ntwo"}
    rendered = _rendered("plan-description", answers, tmp_path)
    assert rendered.returncode != 0, rendered.stdout
    assert 'the answer to "check_runtime_summary" is not a string' in rendered.stderr
    assert "a `string` is one line" in rendered.stderr, rendered.stderr

    listed = subprocess.run(
        [str(ONETASKGRAPH_BIN), "template", "variables", "--template-loader", "-", "--json"],
        cwd=REPO_ROOT,
        input=_resolved("plan-description"),
        text=True,
        capture_output=True,
        check=False,
    )
    assert listed.returncode == 0, listed.stderr
    (declared,) = [
        one
        for one in json.loads(listed.stdout)["variables"]
        if one["name"] == "check_runtime_summary"
    ]
    assert "at most 240 characters" in " ".join(declared["description"].split()), declared


def test_a_saved_plan_record_without_check_runtime_summary_renders_and_one_with_it_shows_it(
    tmp_path: Path,
) -> None:
    saved = _rendered("plan-description", {"overview": "A plan.", **PLAN_BUDGETS}, tmp_path)
    assert saved.returncode == 0, saved.stderr
    assert "### Checklist" in saved.stdout and "Check runtime" not in saved.stdout, saved.stdout

    answers = {"overview": "A plan.", **PLAN_BUDGETS, "check_runtime_summary": RUNTIME_SUMMARY}
    current = _rendered("plan-description", answers, tmp_path)
    assert current.returncode == 0, current.stderr
    checklist = current.stdout.split("### Checklist", 1)[1].split("### Repo-wide effects", 1)[0]
    assert f"**Check runtime.** {RUNTIME_SUMMARY}" in checklist, current.stdout


#: Every budget the plan's tasks own, by the task owning it. Between them the changed
#: version-3 budgets' `previous` values differ in every key `previous` holds.
OWNED: dict[str, list[dict[str, object]]] = {
    "api": [
        _budget("health-latency", "Health route p95 latency", "add", runtime=4, threshold=50),
        # Only its target moves.
        CHANGED | {"threshold": 150},
        # Renamed, and its workload moved.
        _budget(
            "queue-drain",
            "Job queue drain time",
            "change",
            runtime=3,
            unit="seconds",
            threshold=60,
            was={"name": "Queue drain time", "workload": "100 jobs queued at once"},
        ),
    ],
    "worker": [
        # Only fields other than its name and target, its runtime among them.
        _budget(
            "index-build",
            "Search index build time",
            "change",
            runtime=12,
            unit="seconds",
            threshold=300,
            source="telemetry",
            was={
                "measure": "Wall time of the indexer process",
                "inner_measure_reason": "The indexer is timed alone because the job runner "
                "shares its host.",
                "command": "scripts/time-indexer.sh",
                "source": "direct",
                "check_runtime_seconds": 5,
            },
        ),
        # Only its check runtime.
        _budget(
            "nightly-export",
            "Nightly export duration",
            "change",
            runtime=9,
            unit="minutes",
            threshold=20,
            was={"check_runtime_seconds": 6},
        ),
        # Its direction moved.
        _budget(
            "disk-headroom",
            "Free disk headroom",
            "change",
            runtime=1,
            unit="GB",
            direction="min",
            threshold=10,
            was={"direction": "max"},
        ),
        # Its unit, and so its threshold, moved.
        _budget(
            "cache-hits",
            "Cache hit ratio",
            "change",
            runtime=1,
            unit="percent",
            direction="min",
            threshold=90,
            was={"unit": "ratio", "threshold": 0.9},
        ),
    ],
    "web": [
        _budget(
            "status-cold-load",
            "Status page cold load",
            "add",
            runtime=30,
            unit="seconds",
            threshold=2,
        ),
        _legacy_changed(),
        _budget("login-latency", "Login p95 latency", "none", runtime=7, threshold=300),
    ],
}
#: The changed budgets whose name or target moved, plus the version-2 one, in plan order.
ROWS = {
    "checkout-latency": ("Checkout p95 latency", "≤200 ms → ≤150 ms"),
    "queue-drain": ("Queue drain time → Job queue drain time", "≤60 seconds"),
    "disk-headroom": ("Free disk headroom", "≤10 GB → ≥10 GB"),
    "cache-hits": ("Cache hit ratio", "≥0.9 ratio → ≥90 percent"),
    "report-export-size": ("Report export size", "≤5 MB, previous not recorded"),
}
#: The changed version-3 budgets differing in another field, and the fields each names.
OTHERS = {
    "queue-drain": "workload",
    "index-build": "measure, inner measure reason, command, measurement source",
    "nightly-export": "check runtime",
}
#: Added runtimes plus each changed version-3 budget's change in runtime: 4 + 30 + 7 + 3.
TOTAL = 44


class Plan(NamedTuple):
    """The fixture plan's rendered records."""

    #: The design document's `## Budgets` section, up to the next section.
    section: str
    #: Each budget's id, with the link a design-doc writer gives it.
    links: dict[str, str]
    #: Each task's node id, with its rendered `## Budgets` section.
    tasks: dict[str, str]
    #: The design documents of plans owning only unchanged budgets, and lowering a runtime.
    unchanged_only: str
    saving: str
    #: The design document of a plan whose displayed budgets are all version 2.
    unestimated: str


def _environment(root: Path) -> dict[str, str]:
    prefix = f"ONETASKGRAPH_SOURCES__{SOURCE.upper()}__"
    return {
        **os.environ,
        f"{prefix}PLUGIN": "local-md",
        f"{prefix}CONFIG__ROOT": str(root),
        f"{prefix}VISIBILITY": "private",
    }


def _store(environment: Mapping[str, str], *arguments: str) -> dict[str, object]:
    done = subprocess.run(
        [str(ONETASKGRAPH_BIN), *arguments, "--json"],
        cwd=REPO_ROOT,
        env=dict(environment),
        text=True,
        capture_output=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    answer = json.loads(done.stdout)
    assert isinstance(answer, dict)
    return answer


def _item(environment: Mapping[str, str], kind: str, qualified: str) -> dict[str, object]:
    items = _store(environment, kind, "show", qualified)["items"]
    assert isinstance(items, list) and len(items) == 1, items
    item = items[0]["item"]
    assert isinstance(item, dict)
    return item


def _section(content: str) -> str:
    """``content``'s `## Budgets` section, heading included, or `""` when it has none."""
    if "## Budgets\n" not in content:
        return ""
    return "## Budgets\n" + content.split("## Budgets\n", 1)[1].split("\n## ", 1)[0]


def _document(
    root: Path,
    native: str,
    owned: Mapping[str, list[dict[str, object]]],
    plan_budgets: Mapping[str, object],
) -> tuple[str, dict[str, str], dict[str, str]]:
    """Write ``native``'s tasks owning ``owned``, then its design document from their records.

    Each task is rendered with its budgets through the store's own `task render`, as a
    planner renders one; the document's `budgets` answer is each record as the store holds
    it, with its owner, where the store reports that task and the link to its `### Budget
    <id>` heading relative to the document's folder, as a design-doc writer copies them.
    Answers the document's section, every budget's link and every task's section.
    """
    environment = _environment(root)
    plan = {
        "name": native,
        "tasks": [
            {"id": node, "persona": "engineer", "title": f"feat: {node} budgets", "unit": node}
            for node in owned
        ],
    }
    write_plan_project(root, plan, native_id=native, source=SOURCE)
    budgets: list[dict[str, object]] = []
    links: dict[str, str] = {}
    tasks: dict[str, str] = {}
    rows: list[dict[str, str]] = []
    for node, records in owned.items():
        task = owning(SOURCE, native, node, records, environment, TASK_ANSWERS)
        item = _item(environment, "task", task)
        reported = item["location"]
        assert isinstance(reported, dict), reported
        location = str(reported["path"])
        tasks[node] = _section(str(item["content"]))
        rows.append(
            {"task": str(item["title"]), "unit": node, "delivers": "its budgets"}
            | {"depends_on": "none", "location": task}
        )
        for record in records:
            anchor = f"budget-{record['id']}"
            link = f"{os.path.relpath(location, root / 'documents')}#{anchor}"
            links[str(record["id"])] = link
            budgets.append(
                record | {"node": node, "location": location, "anchor": anchor, "link": link}
            )
    designed(
        SOURCE,
        native,
        rows,
        environment,
        {"plan_budgets": dict(plan_budgets), "budgets": budgets},
    )
    document = _item(environment, "document", f"{SOURCE}:{native}-design")
    return _section(str(document["content"])), links, tasks


@pytest.fixture(scope="module")
def plan(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Plan]:
    root = tmp_path_factory.mktemp("budget-sections")
    with_summary = PLAN_BUDGETS | {"check_runtime_summary": RUNTIME_SUMMARY}
    section, links, tasks = _document(root, f"budgets-{os.getpid()}", OWNED, with_summary)
    unchanged = {"web": [record for record in OWNED["web"] if record["file_change"] == "none"]}
    unchanged_only, _, _ = _document(root, f"unchanged-{os.getpid()}", unchanged, with_summary)
    cheaper = {
        "worker": [
            _budget(
                "index-build",
                "Search index build time",
                "change",
                runtime=30,
                unit="seconds",
                threshold=300,
                was={"check_runtime_seconds": 45, "command": "scripts/time-indexer.sh"},
            ),
            # Fractional runtimes, whose sum a float cannot hold exactly.
            _budget("queue-probe", "Queue depth probe", "add", runtime=0.1),
            _budget("lock-probe", "Lock wait probe", "add", runtime=0.2),
        ]
    }
    saving, _, _ = _document(root, f"saving-{os.getpid()}", cheaper, PLAN_BUDGETS)
    saved = {"web": [_legacy_changed()]}
    unestimated, _, _ = _document(root, f"unestimated-{os.getpid()}", saved, PLAN_BUDGETS)
    yield Plan(section, links, tasks, unchanged_only, saving, unestimated)


def _part(section: str, start: str, end: str) -> str:
    """The text of ``section`` from ``start`` up to the next ``end``."""
    assert start in section, section
    return section.split(start, 1)[1].split(end, 1)[0]


def _cells(line: str) -> list[str]:
    """A table row's cells, a `\\|` inside one not splitting it."""
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip())[1:-1]]


def _table(text: str) -> list[list[str]]:
    """Every row of the one table in ``text``, its header and delimiter rows included."""
    return [_cells(line) for line in text.splitlines() if line.startswith("|")]


def _references(section: str) -> dict[str, str]:
    """Every reference-link definition in ``section``, by its label."""
    return dict(re.findall(r"^\[([^\]]+)\]: (\S+)$", section, re.MULTILINE))


def _linked(cell: str, references: Mapping[str, str]) -> tuple[str, str]:
    """A budget cell's text and where it links, reference or inline."""
    found = re.fullmatch(r"\[(.+)\]\[([^\]]+)\]", cell)
    if found is not None:
        return found[1], references[found[2]]
    found = re.fullmatch(r"\[(.+)\]\((\S+)\)", cell)
    assert found is not None, cell
    return found[1], found[2]


def _block(section: str, summary: str) -> str:
    """The body of the collapsed block whose summary matches ``summary``."""
    found = re.search(
        rf"^<details>\n<summary>({summary})</summary>\n\n(.*?)\n\n</details>$",
        section,
        re.MULTILINE | re.DOTALL,
    )
    assert found is not None, section
    return found[2]


def _previous(record: Mapping[str, object]) -> Mapping[str, object]:
    """``record``'s `previous`, or nothing for a budget that carries none."""
    previous = record.get("previous", {})
    assert isinstance(previous, dict)
    return previous


def _names(owned: Mapping[str, list[dict[str, object]]], file_change: str) -> dict[str, str]:
    return {
        str(record["id"]): str(record["name"])
        for records in owned.values()
        for record in records
        if record["file_change"] == file_change
    }


def test_each_budget_appears_in_its_own_group_with_two_columns_and_its_link(plan: Plan) -> None:
    section = plan.section
    references = _references(section)
    added = _table(_part(section, "**Added budgets:**", "\n\n["))
    changed = _table(_part(section, "**Changed budgets:**", "\n\n["))
    covering = _table(_block(section, r"1 existing budget already covers this work"))

    for table in (added, changed, covering):
        assert table[:2] == [["Budget", "Target"], ["---", "---"]], table
        assert all(len(row) == 2 for row in table), table

    assert [_linked(row[0], references) for row in added[2:]] == [
        (name, plan.links[budget]) for budget, name in _names(OWNED, "add").items()
    ]
    assert [row[1] for row in added[2:]] == ["≤50 ms", "≤2 seconds"]
    assert [(*_linked(row[0], references), row[1]) for row in changed[2:]] == [
        (name, plan.links[budget], target) for budget, (name, target) in ROWS.items()
    ]
    assert [(*_linked(row[0], references), row[1]) for row in covering[2:]] == [
        ("Login p95 latency", plan.links["login-latency"], "≤300 ms")
    ]
    # An added or unchanged budget is named once, in its own group; the runtime explanation
    # names an expensive one by name too, which is what it is for.
    for name in [*_names(OWNED, "add").values(), *_names(OWNED, "none").values()]:
        assert section.replace(RUNTIME_SUMMARY, "").count(name) == 1, name
    assert "unchanged" not in section.casefold(), section
    # Each link's fragment is the heading its owning task renders for that budget.
    for node, records in OWNED.items():
        for record in records:
            assert plan.links[str(record["id"])].endswith(f"#budget-{record['id']}")
            assert f"### Budget {record['id']}\n" in plan.tasks[node], record["id"]


def test_other_changes_are_named_without_values_in_one_collapsed_block(plan: Plan) -> None:
    body = _block(plan.section, rf"{len(OTHERS)} budgets changed for other reasons")
    lines = [re.fullmatch(r"- \[(.+)\]\((\S+)\): (.+)", line) for line in body.splitlines()]
    assert all(line is not None for line in lines), body
    names = _names(OWNED, "change")
    assert [(line[1], line[2], line[3]) for line in lines if line is not None] == [
        (names[budget], plan.links[budget], fields) for budget, fields in OTHERS.items()
    ]
    # The renamed budget is both a row and a line; one changed only in its target is no line.
    assert "Job queue drain time" in _part(plan.section, "**Changed budgets:**", "<details>")
    assert "Checkout p95 latency" not in body
    for records in OWNED.values():
        for record in records:
            for key in ("measure", "inner_measure_reason", "workload", "command"):
                value = str(_previous(record).get(key, "")) or "unstated"
                assert value not in plan.section, (record["id"], key)


def test_the_total_runtime_is_shown_once_apart_from_the_tables_with_its_explanation(
    plan: Plan,
) -> None:
    section = plan.section
    total = f"**Check runtime added:** ≈{TOTAL} s, not estimated for Report export size."
    assert f"{total} {RUNTIME_SUMMARY}\n" in section, section
    assert not any(line.startswith("|") and "≈" in line for line in section.splitlines())
    for records in OWNED.values():
        for record in records:
            for runtime in (
                record["check_runtime_seconds"],
                _previous(record).get("check_runtime_seconds"),
            ):
                if runtime is not None:
                    assert f"≈{runtime} s" not in section, record["id"]
    assert "approximately" not in section and "Basis" not in section
    for source in ("direct", "telemetry"):
        assert source not in section.casefold(), source


def test_sizing_ten_x_omissions_and_spike_findings_still_appear(plan: Plan) -> None:
    section = plan.section
    assert section.startswith(f"## Budgets\n\n**Sizing.** {PLAN_BUDGETS['sizing']}\n\n"), section
    assert f"**At 10×.** {PLAN_BUDGETS['ten_x_summary']}\n" in section
    assert "**Not budgeted:**\n\n- spend: The plan adds no paid service.\n" in section
    assert "image retention" not in section
    assert "**Spike findings:**\n\n- The checkout's p95 is 140 ms today" in section
    order = ["**Sizing.**", "**At 10×.**", "**Added budgets:**", "**Changed budgets:**"]
    order += ["changed for other reasons", "**Check runtime added:**", "already covers this work"]
    order += ["**Not budgeted:**", "**Spike findings:**"]
    assert [section.index(part) for part in order] == sorted(section.index(part) for part in order)


def test_a_plan_owning_only_unchanged_budgets_renders_no_budgets_section(plan: Plan) -> None:
    assert plan.unchanged_only == ""


def test_a_changed_budget_lowering_its_runtime_shows_the_saving(plan: Plan) -> None:
    # 0.1 + 0.2 + 30 - 45, shown to one decimal place.
    assert "**Check runtime added:** saves ≈14.7 s.\n" in plan.saving, plan.saving
    assert "**Changed budgets:**" not in plan.saving
    assert re.search(r"^- \[Search index build time\]\(\S+\): command$", plan.saving, re.M)


def test_a_plan_with_no_version_3_figure_shows_no_runtime_figure(plan: Plan) -> None:
    """Budgets saved before version 3 are named as not estimated, never summed to nothing."""
    line = "**Check runtime added:** not estimated for Report export size.\n"
    assert line in plan.unestimated, plan.unestimated
    assert "≈" not in plan.unestimated


def test_each_task_shows_its_changed_budgets_previous_values_beside_the_new_ones(
    plan: Plan,
) -> None:
    worker = plan.tasks["worker"]
    build = _part(worker, "### Budget index-build\n", "### Budget")
    assert "- **Measure:** Search index build time, as an operator waits on it\n" in build
    assert "  - **Before this task:** Wall time of the indexer process\n" in build
    assert "- **Inner measure, because:** none\n  - **Before this task:** The indexer" in build
    assert "- **Measurement source:** telemetry\n  - **Before this task:** direct\n" in build
    assert (
        "- **Check runtime added:** approximately 12 seconds\n"
        "  - **Before this task:** approximately 5 seconds\n"
    ) in build
    assert "- **Command:** `scripts/measure-index-build.sh`\n" in build
    assert "  - **Before this task:** `scripts/time-indexer.sh`\n" in build
    cache = _part(worker, "### Budget cache-hits\n", "### Budget")
    assert (
        "- **Target:** at least 90 percent\n  - **Before this task:** at least 0.9 ratio\n" in cache
    )
    api = plan.tasks["api"]
    drain = _part(api, "### Budget queue-drain\n", "### Budget")
    assert "- **Name:** Job queue drain time\n  - **Before this task:** Queue drain time\n" in drain
    # Added and unchanged budgets carry no previous value, and the version-2 one renders as
    # it always has.
    added = _part(api, "### Budget health-latency\n", "### Budget")
    assert "Before this task" not in added and "**Check runtime added:**" in added
    assert VERSION_2_SECTION.split("\n", 2)[2].rstrip("\n") in plan.tasks["web"]
    assert "Before this task" not in _part(plan.tasks["web"], "### Budget login-latency", "## ")
