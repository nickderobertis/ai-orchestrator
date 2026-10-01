"""A design document too long for the board is refused by `just copy-plan`, driven end to end.

A plan's design document becomes an issue on the `plans` board, whose bodies GitHub caps
at `task_body.BODY_LIMIT` characters, and it is copied there *after* the plan's project
and tasks — so a refusal from the board would leave the plan landed with nothing a person
can approve it as. `just copy-plan` measures each document before the store writes
anything, under the thresholds `just check-plan` holds a task's body to.

Everything below the recipe is real: the real `just copy-plan`, the real
`orchestrator-copy-plan`, the real installed `onetaskgraph` writing and copying real local
Markdown stores at both ends, the document rendered from this host's own `design-doc`
template by the pinned engine's `template resolve` piped into the store's `document
create` — the road a design-doc dispatch takes — and the plan cleared by the real `just
review-plan`. `tests/e2e/fake_codex.py` stands in for the paid review provider alone,
through `project_fixtures.reviewed`.

The destination is a second local store standing in for the board, added through the
store's own `ONETASKGRAPH_` environment layer for the reason
`tests/plan_tooling/test_copy_plan_recipe_e2e.py` gives.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import plan_fixture_source
import pytest
from project_fixtures import reviewed
from published_tools import ONETASKGRAPH_BIN
from waits import timeout as e2e_timeout

from orchestrator import plan_store, task_body
from orchestrator.criteria_guard import APPENDIX
from orchestrator.project_store import write_plan_project
from orchestrator.root import REPO_ROOT

#: The source a plan is drafted in here: this process's own `test-fixtures` store.
DRAFTED_IN = plan_fixture_source.SOURCE

#: The source standing in for the `plans` board, spelled into the store's environment
#: layer as well as onto the command line, so a plain lowercase name.
DESTINATION = "destination"

#: Criteria every check on the review path accepts, so nothing is refused for a reason
#: this journey is not about.
STATES_ITS_BAR = (
    "- The route accepts a valid request and rejects an invalid one.\n"
    "- A request-level test drives the route end to end and covers both paths.\n"
    "- Every claim the dispatch makes about the finished work is true of the tree as "
    "it finally stands."
)

#: The sentence a unit's summary is padded with to bring the document to a threshold.
SENTENCE = "The route validates the request before it reaches the payment service. "


def _just(*arguments: str, seconds: float = 240) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["just", *arguments],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
    )


def _records(root: Path) -> list[str]:
    """Every record file the destination store holds, as paths below its root."""
    return sorted(str(one.relative_to(root)) for one in root.rglob("*.md")) if root.is_dir() else []


@pytest.fixture
def destination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A second local Markdown store, configured for this process and every recipe it runs.

    Created before the first write, because a `local-md` source canonicalizes its root when
    it is built and refuses an absent one; and `ONETASKGRAPH_DEFAULT_SOURCES` is narrowed
    to the two sources in play, which keeps the live `plans` board out of every read here.
    """
    root = tmp_path / "board"
    root.mkdir(parents=True)
    monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{DESTINATION.upper()}__PLUGIN", "local-md")
    monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{DESTINATION.upper()}__CONFIG__ROOT", str(root))
    monkeypatch.setenv("ONETASKGRAPH_DEFAULT_SOURCES", f"{DRAFTED_IN},{DESTINATION}")
    yield root


def _answers(native: str, padding: int) -> dict[str, object]:
    """Answers in the template's own shape, one unit's summary padded by ``padding`` characters.

    Every variable the `design-doc` template's front matter declares, each in the shape it
    states — the `units` list and its nested `reversible` and `decisions` among them — so
    the body measured is one the template really renders, not a hand-written stand-in.
    """
    summary = "One route behind the checkout page. " + SENTENCE * (padding // len(SENTENCE) + 1)
    return {
        "what": "A checkout route that takes a purchase from the cart to the payment service.",
        "why": "The user cannot complete a purchase without it.",
        "architecture": "One new route in the service, called by the existing checkout page.",
        "units": [
            {
                "name": "checkout route",
                "repository": "some-service",
                "part": "routes/checkout",
                "summary": summary,
                "reversible": [{"title": "Route name", "text": "Renaming it is one edit."}],
                "decisions": [
                    {
                        "name": "Request shape",
                        "justification": "every client of the route would have to change",
                        "summary": "The route takes the cart id and nothing else.",
                        "artifact": '```json\n{"cart_id": "string"}\n```',
                    }
                ],
            }
        ],
        "acceptance_criteria": ["A purchase completes from the checkout page."],
        "planned_tasks": [
            {
                "task": "feat: add the route",
                "unit": "checkout route",
                "delivers": "the route and its test",
                "depends_on": "none",
                "location": f"{DRAFTED_IN}:{native}/route",
            }
        ],
    }


def _designed(native: str, padding: int, scratch: Path) -> None:
    """Render ``native``'s design document from this host's template, as a dispatch does."""
    resolved = subprocess.run(
        [str(REPO_ROOT / "scripts" / "onepipeline.sh"), "template", "resolve", "design-doc"]
        + ["--json"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert resolved.returncode == 0, resolved.stderr
    answers = scratch / f"{native}-answers.json"
    answers.write_text(json.dumps(_answers(native, padding)), encoding="utf-8")
    created = subprocess.run(
        [str(ONETASKGRAPH_BIN), "document", "create", DRAFTED_IN]
        + ["--project", native, "--title", f"Design: {native}", "--id", f"{native}-design"]
        + ["--template-loader", "-", "--answers", str(answers), "--no-interactive"],
        cwd=REPO_ROOT,
        input=resolved.stdout,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert created.returncode == 0, created.stderr


def _cleared(name: str, padding: int, scratch: Path) -> str:
    """A plan of one task, its design document padded by ``padding``, and the plan reviewed.

    Written with the store layout's own writer and the document rendered here rather than
    through `project_fixtures.local_project`, because the document's size is what this
    journey turns and that helper's answers are fixed. The document is not approved:
    `just copy-plan` asks for no approval, and an approval would only lengthen
    the map measured, which `tests/test_task_body.py` already holds is part of the body.
    """
    native = f"test-{os.getpid()}-{name}"
    write_plan_project(
        plan_fixture_source.root(),
        {
            "schema_version": 3,
            "name": native,
            "goal": {"text": "Deliver the checkout route"},
            "tasks": [
                {
                    "id": "route",
                    "persona": "engineer",
                    "repo": "https://github.com/nickderobertis/some-service",
                    "title": "feat: add the route",
                    "task": (
                        "## What\n\nAdd the route and the test that drives it.\n\n"
                        "## Why\n\nThe user cannot complete a purchase without it.\n\n"
                        f"## Acceptance criteria\n\n{STATES_ITS_BAR}\n\n"
                        f"{(REPO_ROOT / APPENDIX).read_text(encoding='utf-8').strip()}\n"
                    ),
                }
            ],
        },
        native_id=native,
    )
    project = f"{DRAFTED_IN}:{native}"
    _designed(native, padding, scratch)
    # `reviewed` reaches these records the way an operator does — the real `just
    # review-plan`, its script, the real `oneharness` CLI and its response schema — and
    # substitutes the paid provider process alone, this suite's one sanctioned double.
    # llmlint: ignore[e2e_not_mocked] see the note above this line
    reviewed(project)
    return project


def _measured(project: str) -> int:
    """The one document's composed size, read back out of the store it was drafted in."""
    (body,) = task_body.document_bodies(plan_store.read_documents(project))
    return body.size


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] `plan-tooling` is the leaf
# project keyed on `planToolingWorkspace`, the edge this rule asks for, and every other journey
# of this module already runs behind it. That key names the recipes, scripts, templates and
# package these journeys drive, so narrowing it would memoize a verdict over a tree never run.
def test_a_design_document_the_board_would_refuse_is_refused_before_anything_is_copied(
    destination: Path, tmp_path: Path
) -> None:
    """Refused under its own status, naming the document and its size, and nothing written.

    The destination is asserted empty — no project, no task, no document — because the
    point of refusing here is that the plan never lands on the board without the document
    a person approves it as.
    """
    project = _cleared("oversized-document", task_body.BODY_LIMIT + 2_000, tmp_path)
    size = _measured(project)
    assert size > task_body.BODY_LIMIT, size

    refused = _just("copy-plan", project, "--to", DESTINATION)

    assert refused.returncode == 4, refused.stdout + refused.stderr
    assert f"{project}-design: its composed issue body measures {size:,} characters" in (
        refused.stderr
    ), refused.stderr
    assert f"{task_body.BODY_LIMIT:,}-character limit" in refused.stderr, refused.stderr
    assert task_body.DOCUMENT_UNMEASURED in refused.stderr, refused.stderr
    assert f"nothing was copied into {DESTINATION!r}" in refused.stderr, refused.stderr
    assert refused.stdout == "", refused.stdout
    assert _records(destination) == [], "a plan with an oversized document was copied"


def test_a_design_document_past_the_warning_threshold_is_warned_about_and_copied(
    destination: Path, tmp_path: Path
) -> None:
    """Warned about on stderr, then copied whole, the document beside the plan."""
    project = _cleared("warned-document", task_body.WARN_FROM + 2_000, tmp_path)
    size = _measured(project)
    assert task_body.WARN_FROM <= size <= task_body.BODY_LIMIT, size
    _, _, native = project.partition(":")

    copied = _just("copy-plan", project, "--to", DESTINATION)

    assert copied.returncode == 0, copied.stdout + copied.stderr
    assert (
        f"copy-plan: warning: {project}-design: its composed issue body measures {size:,} "
        f"characters, {100 * size // task_body.BODY_LIMIT}% of the "
        f"{task_body.BODY_LIMIT:,}-character limit"
    ) in copied.stderr, copied.stderr
    assert _records(destination) == [
        f"documents/{native}-design.md",
        f"projects/{native}.md",
        f"tasks/{native}/route.md",
    ], copied.stdout + copied.stderr


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
