"""`config/budgets-migration.yaml` names every plan the real stores hold that has no budgets.

The list is the one source of which plans predate the budgets requirement, taken when the
requirement landed. What its entries must look like is held in `tests/test_plan_budgets.py`;
what is held here is the comparison with outside state: every plan project the `authoring`
source and the `plans` board hold today that carries no budgets document, and that is not
an exempt plan kind on the bound `orchestrator/design_approval.py` states, is one the list
names — because `just check-plan` refuses every such plan it does not.

That state lives outside the workspace — the `authoring` root is gitignored, the board is a
GitHub project — so this belongs to the uncached tier. The `authoring` source read is the
root this checkout's `onetaskgraph.yaml` configures, which `tests/conftest.py` otherwise
points every test process away from: in the checkout plans are authored in, that is the
host's own. Its result is not any one change's bar: a plan written without a budgets
document after the requirement landed fails it until that plan gains one, which is the
requirement working.
"""

from __future__ import annotations

import os
from collections import defaultdict
from itertools import chain

import plan_root_variable
import pytest
from plan_sources import read_plan_sources

from orchestrator import design_approval, plan_budgets, plan_store
from orchestrator.root import REPO_ROOT

#: The two stores the list was taken over.
SOURCES = ("authoring", "plans")

#: The credential the board is read with, which a host without it cannot read the board by.
BOARD_TOKEN = "GH_PROJECTS_TOKEN"


def _budgeted(source: str) -> set[str]:
    """Every project of ``source`` holding a document rendered from `plan-budgets`, by id."""
    listed = plan_store.every_page(
        f"the documents of {source!r}", plan_store.client().document_list, source=[source]
    )
    held: dict[str, list[str]] = defaultdict(list)
    for document in chain.from_iterable(page.items for page in listed):
        provenance = (document.item.metadata or {}).get(design_approval.PROVENANCE)
        project = document.item.project.model_dump() if document.item.project else None
        if (
            isinstance(provenance, dict)
            and provenance.get("template") == plan_budgets.TEMPLATE_REFERENCE
            and project
        ):
            held[f"{source}:{project}"].append(document.id.model_dump())
    return set(held)


def _exempt(project: plan_store.StoreProject) -> bool:
    """Whether ``project`` is an exempt plan kind on the bound the plan check applies."""
    stamp = project.metadata.get(design_approval.PLAN_KIND)
    if not isinstance(stamp, dict) or stamp.get(design_approval.STAMP_KIND) not in (
        design_approval.EXEMPT_KINDS
    ):
        return False
    # A project whose tasks the store cannot read cannot be shown to hold exactly the nodes its
    # stamp names, so it is no exempt launch: the check refuses it, and the list must name it.
    try:
        nodes = {task.node_id for task in plan_store.read_tasks(str(project.qualified_id))}
    except OSError:
        return False
    return plan_budgets.exempt_kind(str(project.qualified_id), set(nodes)) is not None


# llmlint: ignore[shell_test_tiers_stay_split, test_tiers_split_by_project_not_by_marker] `reads_checkouts` is this repository's uncached tier, `orchestrator:test-checkouts`, defined in `pyproject.toml`'s markers for exactly this — a check of configuration against state outside the workspace and so outside every cache key — and the task that adds this list names that tier as where its comparison runs. A project of its own would be one more uncached target keyed on nothing.  # noqa: E501
@pytest.mark.reads_checkouts
@pytest.mark.parametrize("source", SOURCES)
def test_the_list_names_every_plan_the_store_holds_that_carries_no_budgets(
    source: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = read_plan_sources((REPO_ROOT / "onetaskgraph.yaml").read_text(encoding="utf-8"))
    authoring = configured["authoring"].root
    assert authoring is not None, "the authoring source configures no root"
    monkeypatch.setenv(plan_root_variable.name(), str(REPO_ROOT / authoring))
    if source == "authoring" and not (REPO_ROOT / authoring).is_dir():
        # The root is gitignored, so a fresh worktree — the one a publishing push gates in
        # among them — has none, and the store refuses a root that does not exist: there is
        # no store here to compare the list with, which is not the list being incomplete.
        pytest.skip(
            f"this checkout has no {authoring!r} root, so it is not the checkout plans are "
            f"authored in; run this from the checkout that holds the plans"
        )
    if source == "plans" and not os.environ.get(BOARD_TOKEN):
        pytest.skip(
            f"{BOARD_TOKEN} is not set, so the {source!r} board cannot be read on this host; "
            f"export it, as `just plans` does from this checkout's .env, to compare the list "
            f"with the board"
        )
    projects = plan_store.read_projects(source)
    if not projects:
        # A store holding no plan is not the one the list was taken over — run from a
        # worktree, this checkout's `authoring` root is its own empty copy — and comparing
        # the list with nothing would pass without having compared anything.
        pytest.skip(
            f"the {source!r} store this checkout resolves holds no plan project, so it is "
            f"not the store plans are authored in and the list was compared with nothing; "
            f"run this from the checkout that holds the plans"
        )
    budgeted = _budgeted(source)
    listed = {entry.project for entry in plan_budgets.migration_entries()}

    unlisted = sorted(
        str(project.qualified_id)
        for project in projects
        if str(project.qualified_id) not in budgeted
        and str(project.qualified_id) not in listed
        and not _exempt(project)
    )

    assert not unlisted, (
        f"{len(unlisted)} plan project(s) of {source!r} carry no budgets document, are no "
        f"exempt plan kind, and are not named in config/budgets-migration.yaml, so `just "
        f"check-plan` refuses each: give each a `<project>-budgets` document, or — for a "
        f"plan that predates the requirement — add it to the list with its reason:\n"
        + "\n".join(f"  - {project}" for project in unlisted)
    )
