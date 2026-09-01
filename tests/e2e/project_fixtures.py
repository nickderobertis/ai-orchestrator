"""Materialize test-owned local Markdown projects for real onepipeline launches.

Nothing here removes what it wrote. The root is the one `onetaskgraph.yaml` configures
for the `test-fixtures` source, which every concurrent test tier of this repository
reads at once, and a record removed mid-walk refuses the walk rather than disappearing
from it. Reclaiming belongs to `tests/plan_fixture_root.py`, which runs under the
exclusive lock and only against records whose writing process is gone.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from plan_fixture_root import ROOT as _PROJECT_ROOT
from published_tools import ONETASKGRAPH_BIN

from orchestrator.project_store import frontmatter, write_plan_project
from orchestrator.root import REPO_ROOT

_PROJECT_SEQUENCE = itertools.count()

#: Where the suite's shared stand-ins live: this module's own directory, and the only
#: spelling of it that survives a test module moving to another project.
HELPERS = Path(__file__).resolve().parent


def helper(name: str) -> Path:
    """One shared stand-in, refused by name when this checkout does not have it.

    Every caller reaches a stand-in through here rather than deriving it from its own
    `__file__`, because a caller that derives it is one move away from naming a path
    that does not exist — and a *paid provider's* stand-in that does not exist is not a
    stand-in at all. `ONEHARNESS_BIN_CODEX` naming nothing makes oneharness fall through
    to a real identity, and a `PATH` entry that is not a directory guards nothing, so
    the journey spends real turns and passes while doing it. That is not hypothetical:
    moving these two suites into their own project broke exactly this, and the first
    thing that said so was a review verdict no fixture had scripted.
    """
    found = HELPERS / name
    if not found.exists():
        raise AssertionError(
            f"the suite's shared stand-in {name!r} is not at {found}; a journey "
            f"substituting a path this checkout does not have routes its turn to the "
            f"real provider and spends it"
        )
    return found


#: The paid provider's stand-in and the guard covering the identities `ONEHARNESS_BIN_*`
#: cannot reach, so `reviewed` below spends a real review turn without spending money.
_FAKE_CODEX = helper("fake_codex.py")
_PAID_PROVIDER_GUARD = helper("no-paid-provider")

#: What the scripted reviewer answers. One passing verdict, repeated for every task:
#: `tests/e2e/fake_codex.py` reuses its last scripted answer once the list runs out.
_PASSING_VERDICT = json.dumps({"passes": True, "reason": "the criteria prove this node"})

#: Where the review turns this fixture spends keep their harness history, so they do not
#: land in the host's. One directory per test process, created on first use.
_HISTORY = Path(tempfile.mkdtemp(prefix="ai-orchestrator-fixture-history-"))


def local_project(content: str, name: str) -> str:
    """Write one unique authoring project, with its design document approved, and answer its id.

    **The approval is part of what a launchable project is**, which is why it is here and
    not left to each journey to remember. A launch is refused against a project whose
    design document is missing or unapproved, so a fixture that wrote only the plan would
    produce a project no `just orchestrate` could start — and every journey that launches
    one would be asserting about that refusal instead of about what it came to test.

    The document is written the way the project and its tasks are, through this
    repository's own record renderer rather than by hand, and the approval is recorded by
    the real `just approve-design` — the same seam `reviewed` below reaches its own record
    through, so no journey built on this begins from state the exercised interface cannot
    produce. What the recipe *decides* is proven in
    `tests/plan_tooling/test_approve_design_recipe_e2e.py`; what it does here is put a
    fixture project into the one state a launch accepts.
    """
    slug = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    native = f"test-{os.getpid()}-{next(_PROJECT_SEQUENCE)}-{slug}"
    plan = json.loads(content)
    if not isinstance(plan, dict):
        raise ValueError("a plan fixture must be a JSON object")
    plan.setdefault("name", native)
    write_plan_project(_PROJECT_ROOT, plan, native_id=native)
    project = f"test-fixtures:{native}"
    _designed(native)
    approved(project)
    return project


def approved(project: str) -> str:
    """Record the user's approval of ``project``'s design document, and answer its id.

    Through the real recipe rather than in process, and that is not only for realism: the
    approval key is a digest of `config/design-doc-template.md`, so computing one here
    would be the code tier reading prose its cache key deliberately does not cover — and
    the tier's answer does not depend on what that template says, only on the approval
    having been recorded under whatever it currently is.
    """
    recording = subprocess.run(
        ["just", "approve-design", project],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if recording.returncode != 0:
        raise AssertionError(
            f"the fixture could not approve {project} through `just approve-design`: "
            f"{recording.stdout}{recording.stderr}"
        )
    return project


def _designed(native: str) -> None:
    """Write the design document a fixture project is launched against.

    Its title carries the project's own unique native id, and that is load-bearing rather
    than cosmetic: a record is copied over the destination it matches **by title**, and
    this root is shared by every tier of this suite at once, so two documents sharing a
    title would let one project's approval land on another's document.
    """
    documents = _PROJECT_ROOT / "documents"
    documents.mkdir(parents=True, exist_ok=True)
    (documents / f"{native}-design.md").write_text(
        frontmatter(
            {"title": f"Design: {native}", "project": native},
            f"## What\n\nThe fixture plan {native}.\n\n"
            "## Why\n\nA journey needs a project it can launch.\n\n"
            "## Architecture\n\nOne project, its tasks, and this document.\n\n"
            "## Contracts\n\nNone: nothing outside this fixture reads it.\n\n"
            "## Acceptance criteria\n\nThe journey that launched it passes.\n\n"
            "## Planned tasks\n\n"
            "| Task | What it delivers | Depends on | Where it lives |\n"
            "| --- | --- | --- | --- |\n"
            f"| the plan's own tasks | the fixture | none | {_PROJECT_ROOT}/tasks/{native} |\n",
        ),
        encoding="utf-8",
    )


def project_from_plan(plan: Path, name: str | None = None) -> str:
    """Store a JSON plan as a local project through the committed source layout."""
    return local_project(plan.read_text(encoding="utf-8"), name or plan.stem)


def reviewed(project: str) -> str:
    """Review ``project`` through the real `just review-plan`, and answer its id.

    A plan reaching `just check-plan` green is one something has already reviewed, so a
    journey about any *other* refusal has to start from that state. It is reached the
    way an operator reaches it — the real recipe, the real script, the real
    `oneharness` CLI and its response schema — rather than by writing the record
    directly, so no journey built on this begins from state the exercised interface
    cannot produce. Only the paid provider is scripted, at the seam every other journey
    here scripts it at.
    """
    environment = dict(os.environ)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(_FAKE_CODEX)
    # And the identities that seam cannot reach; see `no_paid_provider`.
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{_PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment["FAKE_CODEX_ANSWERS"] = json.dumps([_PASSING_VERDICT])
    environment["XDG_STATE_HOME"] = str(_HISTORY)
    reviewing = subprocess.run(
        ["just", "review-plan", project],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    if reviewing.returncode != 0:
        raise AssertionError(
            f"the fixture could not review {project} through `just review-plan`: "
            f"{reviewing.stdout}{reviewing.stderr}"
        )
    return project


# llmlint: ignore[suppressions_justified] The fixture reconstructs open plan metadata.
def read_project_plan(project: str) -> dict[str, Any]:
    """Read a stored project through onetaskgraph's public JSON command surface."""
    source, native = project.split(":", 1)

    # llmlint: ignore[suppressions_justified] The CLI owns this open JSON schema.
    def read(*arguments: str) -> dict[str, Any]:
        completed = subprocess.run(
            [str(ONETASKGRAPH_BIN), *arguments, "--json"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        payload = json.loads(completed.stdout)
        assert isinstance(payload, dict)
        return payload

    project_item = read("project", "show", project)["items"][0]["item"]
    plan = {
        key.removeprefix("onepipeline."): value
        for key, value in project_item.get("metadata", {}).items()
        if key.startswith("onepipeline.")
    }
    plan.setdefault("name", project_item["title"])
    listed = read("task", "list", "--source", source, "--project", native, "--limit", "1000")[
        "items"
    ]
    ids = {record["id"]: record["item"]["metadata"]["onepipeline.id"] for record in listed}
    # llmlint: ignore[suppressions_justified] Tasks include open round-tripped metadata.
    tasks: list[dict[str, Any]] = []
    for record in listed:
        item = record["item"]
        node = {
            key.removeprefix("onepipeline."): value
            for key, value in item.get("metadata", {}).items()
            if key.startswith("onepipeline.")
        }
        node.update(title=item.get("title"), task=item.get("content"))
        if item.get("repositories"):
            node["repo"] = item["repositories"][0]
        dependencies = [
            ids[edge["to"]["id"]] for edge in read("task", "deps", record["id"])["items"]
        ]
        if dependencies:
            node["deps"] = dependencies
        tasks.append(node)
    plan["tasks"] = tasks
    return plan
