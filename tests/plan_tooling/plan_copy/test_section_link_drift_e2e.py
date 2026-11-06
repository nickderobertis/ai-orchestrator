"""The drift journey between this host's budget anchor and the plan store's slug rule.

A design document's budget link is written here: `orchestrator/plan_budgets.py`'s
`budget_anchor` is its fragment. The heading it names is written by `plan-task`, and what
makes the pair a link is the slug rule the plan store's README states under *Section links*
and its copy applies. Neither repository can see the other move, so this journey holds the
two sides to each other through everything real: the host's own templates, the real
`python -m orchestrator.plan_budgets`, and the installed `onetaskgraph`'s own `project copy`
and `document copy` between two temporary local Markdown sources. It doubles nothing.

The slug rule is restated below from that README, because a copy between two folders of
Markdown carries a fragment as written and reports no miss: the rule is what can disagree
with `budget_anchor`, so this journey must not read its expected fragment from it.
`tests/test_engine_contracts.py` holds that restatement to the adopted store's README.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import boundary_home
import plan_root_variable
import pytest
from project_fixtures import budgeted, designed, no_budgets, owning
from published_tools import ONETASKGRAPH_BIN
from waits import timeout as e2e_timeout

from orchestrator.project_store import write_plan_project
from orchestrator.root import REPO_ROOT

#: The source a plan is copied into, standing in for wherever a plan's copy lands.
DESTINATION = "sectionlinks"
REPOSITORY = "github.com/nickderobertis/some-service"
NODE = "route"
BUDGET_ID = "route-latency"
#: The heading `templates/plan-task-budgets.md.j2` renders for a schema-2 budget.
HEADING = f"Budget {BUDGET_ID}"
TASK_ANSWERS: dict[str, object] = {
    "what": "Add the checkout route.",
    "why": "A buyer cannot check out.",
    "acceptance_criteria": ["The route accepts a valid request and rejects an invalid one."],
}
BUDGET: dict[str, object] = {
    "schema_version": 2,
    "source": "direct",
    "check_runtime_seconds": 1,
    "id": BUDGET_ID,
    "name": "Route response time",
    "basis": "measured",
    "repository": REPOSITORY,
    "file": "apps/api/budgets.yaml",
    "file_change": "add",
    "measure": "time to the route's response at the client",
    "inner_measure_reason": "",
    "unit": "ms",
    "direction": "max",
    "threshold": 250,
    "workload": "500 requests a minute",
    "evidence": "spike-route measured 90 ms",
    "command": "bun run measure:route",
}


@pytest.fixture(autouse=True)
def _boundary_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    boundary_home.answering(monkeypatch)


@pytest.fixture
def destination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A second temporary local Markdown source, seen by this process and its children.

    Declared private as the plan's temporary `authoring` source is, so this journey's copy
    is about section links and never about the public boundary, and narrowed to the two
    sources in play so no read reaches a live board.
    """
    root = tmp_path / "copied"
    root.mkdir()
    prefix = f"ONETASKGRAPH_SOURCES__{DESTINATION.upper()}"
    monkeypatch.setenv(f"{prefix}__VISIBILITY", "private")
    monkeypatch.setenv(f"{prefix}__PLUGIN", "local-md")
    monkeypatch.setenv(f"{prefix}__CONFIG__ROOT", str(root))
    monkeypatch.setenv(
        "ONETASKGRAPH_DEFAULT_SOURCES", f"{plan_root_variable.source()},{DESTINATION}"
    )
    return root


def _heading_slugs(markdown: str) -> dict[str, str]:
    """Each heading of ``markdown`` against its slug, by the rule the store's README states.

    Lowercased; every character not a letter, a digit, a space, `-` or `_` removed; each
    space a `-`; and the n-th repeat of a slug in one record suffixed `-<n>`. Stated here
    rather than through `budget_anchor`, which is the side this journey checks it against.
    """
    seen: dict[str, int] = {}
    slugs: dict[str, str] = {}
    for heading in re.findall(r"^#{1,6} (.+?)\s*$", markdown, re.MULTILINE):
        slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        repeat = seen.get(slug, 0)
        seen[slug] = repeat + 1
        slugs.setdefault(heading, f"{slug}-{repeat}" if repeat else slug)
    return slugs


def _store(*arguments: str) -> str:
    """One installed plan-store verb's standard output, refused loudly."""
    done = subprocess.run(
        [str(ONETASKGRAPH_BIN), *arguments],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert done.returncode == 0, f"onetaskgraph {arguments}: {done.stdout}{done.stderr}"
    return done.stdout


def _planned(name: str) -> tuple[str, dict[str, Any]]:
    """Render a budgeted plan in `authoring`; answers its native id and the writer's answers.

    The task regenerated from `plan-task` with the schema-2 budget, then the writer's answers
    exactly as `python -m orchestrator.plan_budgets` prints them.
    """
    source = plan_root_variable.source()
    native = f"section-link-drift-{os.getpid()}-{name}"
    root = Path(os.environ[plan_root_variable.name()])
    write_plan_project(
        root,
        {
            "schema_version": 3,
            "name": native,
            "goal": {"text": "Deliver the checkout route"},
            "tasks": [
                {
                    "id": NODE,
                    "persona": "engineer",
                    "repo": f"https://{REPOSITORY}",
                    "title": "feat: add the route",
                    "task": "## What\n\nAdd the route.\n",
                    "unit": "Fixture",
                }
            ],
        },
        native_id=native,
    )
    owning(source, native, NODE, [BUDGET], answers=TASK_ANSWERS)
    plan = no_budgets([REPOSITORY])
    checklist = plan["checklist"]
    assert isinstance(checklist, list)
    checklist[0] = {"concern": "latency", "budget": BUDGET_ID, "not_applicable": "", "summary": ""}
    plan.update(schema_version=2, repo_wide_effects=[])
    plan["checklist"] = [{**entry, "in_scope": True} for entry in checklist]
    budgeted(source, native, plan)
    printed = subprocess.run(
        [sys.executable, "-m", "orchestrator.plan_budgets", f"{source}:{native}"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert printed.returncode == 0, printed.stderr
    answers: dict[str, Any] = json.loads(printed.stdout)
    return native, answers


def _copied(native: str, answers: Mapping[str, Any]) -> tuple[Path, str]:
    """Design ``native`` from ``answers`` and copy the plan: the copied document and content."""
    source = plan_root_variable.source()
    designed(
        source,
        native,
        [
            {
                "task": entry["node"],
                "delivers": "the route",
                "depends_on": "none",
                "location": entry["location"],
            }
            for entry in answers["budgets"]
        ],
        budget_answers=answers,
    )
    _store("project", "copy", f"{source}:{native}", "--to", DESTINATION)
    _store("document", "copy", f"{source}:{native}-design", "--to", DESTINATION)
    listed = _store("document", "list", "--project", f"{DESTINATION}:{native}", "--json")
    (document,) = json.loads(listed)["items"]
    location = document["item"]["location"]
    return Path(str(location["path"])), str(document["item"]["content"])


def _copied_task(native: str) -> Path:
    """The file the destination reports for the copied task."""
    listed = _store("task", "list", "--source", DESTINATION, "--project", native, "--json")
    (task,) = json.loads(listed)["items"]
    return Path(str(task["item"]["location"]["path"]))


def _budget_link(content: str) -> tuple[str, str]:
    """The copied document's one reference link definition, as its path and fragment."""
    (link,) = re.findall(r"^\[1\]: (\S+)$", content, re.MULTILINE)
    target, _, fragment = link.partition("#")
    return target, fragment


# llmlint: ignore[expensive_tests_stay_behind_their_own_edge] This journey sits beside this project's other plan-copy journeys and reads what they read, the templates and the plan store, which `tests/plan_tooling/AGENTS.md` states as this project's split; a project of its own would be keyed on those inputs too and add only a target.  # noqa: E501
def test_a_copied_budget_link_lands_on_its_copied_task_s_budget_heading(
    destination: Path,
) -> None:
    """The anchor this host writes is the slug the store's rule gives the heading it names."""
    native, answers = _planned("landed")
    document, content = _copied(native, answers)

    target, fragment = _budget_link(content)
    assert not Path(target).is_absolute(), target
    task = _copied_task(native)
    assert task.is_relative_to(destination), task
    assert (document.parent / target).resolve() == task.resolve(), content
    slugs = _heading_slugs(task.read_text(encoding="utf-8"))
    assert HEADING in slugs, slugs
    assert fragment == slugs[HEADING], (fragment, slugs)
    assert "<a " not in content, content
