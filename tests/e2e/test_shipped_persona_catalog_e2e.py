"""Which persona names `oneagentgraph` builds in, measured rather than remembered.

`personas/README.md` and `AGENTS.md` both tell a planner that a plan node's `persona`
is a *name* resolved against roles compiled into `oneagentgraph`, that exactly five
are shipped, and that anything else — `orchestrator`, `check-in`, a slash-qualified
repo-specific name — is read as a path relative to `graphs/` and fails a dispatch.
That claim decides how every node in every plan is written, and it is the kind of
claim that rots silently: the roles live in somebody else's crate, a release adds one,
and the prose goes on describing the set that shipped a year ago. Two independent
accounts of it have already disagreed in this repository's own history, each citing a
measurement, which is what this file exists to stop.

So the set is resolved from the pinned binary rather than restated. Both journeys
below drive the real `oneagentgraph` and the real `onepipeline`, and neither spends a
provider turn: the first never launches one, and the second dies in config validation
before a harness starts. That is not an accident of the fixture — it *is* the finding,
because an unresolvable persona failing early is exactly what makes a mistyped node
cheap.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import plan_root_variable
import pytest
from waits import timeout as e2e_timeout

from orchestrator.project_store import write_plan_project
from orchestrator.root import REPO_ROOT

#: The roles this repository's prose says `oneagentgraph` compiles in. Stated here as
#: the claim under test, so a release that adds or drops one fails with a diff between
#: this tuple and what the binary answered rather than somewhere downstream.
SHIPPED_ROLES = ("docs-writer", "engineer", "planner", "researcher", "reviewer")

#: Names this repository uses that are deliberately NOT built in. `orchestrator` and
#: `check-in` are `graphs/dag-scope.yaml`'s members and `pr-author` is
#: `graphs/pr-author.yaml`'s, and every one of them is named there BY PATH because of
#: what this file measures. `crozier/crozier-corpus` is the repo-specific catalog
#: entry, and `no-such-role` is the control: whatever happens to an invented name is
#: what happens to these.
NOT_SHIPPED = ("orchestrator", "check-in", "pr-author", "crozier/crozier-corpus")
CONTROL_NAME = "no-such-role"

#: The graph schema that admits a member-facing `personas` catalog directory, which is
#: the lever the first journey pulls. Below it the key is refused outright.
CATALOG_SCHEMA_VERSION = 6

#: How `oneagentgraph` refuses a catalog file whose name a shipped role already claims.
#: This is the whole measurement: the refusal happens if and only if the crate ships
#: that name, so a name it accepts beside the catalog is a name it does not ship.
COLLIDES = "and one this crate ships"

#: The persona a catalog entry needs to be valid at all, in the 0.3.0 shape — a role
#: and a review bar, because what is under test is the name, not the content. Written
#: as `system_prompt` rather than the retired `agent:` block: that block is refused
#: outright, so a probe still carrying it would fail every journey here for the one
#: reason none of them is about.
PROBE_PERSONA = "system_prompt: |\n  probe\nuser:\n  persona: |\n    probe\n"


def _catalog_graph(root: Path, name: str) -> Path:
    """A one-member graph whose own persona catalog holds exactly `name`.

    The member must NAME the persona: `oneagentgraph` resolves a member's persona
    rather than sweeping the catalog, so a graph that only declares the directory
    validates whatever is in it.
    """
    catalog = root / "catalog"
    catalog.mkdir(parents=True, exist_ok=True)
    for stale in catalog.glob("*.yaml"):
        stale.unlink()
    # A slash-qualified name is a nested file, exactly as `personas/` stores one.
    entry = catalog / f"{name}.yaml"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(PROBE_PERSONA, encoding="utf-8")

    graph = root / "catalog-probe.yaml"
    graph.write_text(
        f"version: {CATALOG_SCHEMA_VERSION}\n"
        "name: catalog-probe\n"
        "personas: catalog\n"
        "members:\n"
        "  worker:\n"
        "    kind: oneharness\n"
        f"    oneharness_config: {REPO_ROOT / 'oneharness.toml'}\n"
        f"    persona: {name}\n"
        "    task: probe\n",
        encoding="utf-8",
    )
    return graph


def _validated(graph: Path) -> subprocess.CompletedProcess[str]:
    """Read the graph with the same reader a launch reads it with."""
    return subprocess.run(
        ["oneagentgraph", "validate", str(graph)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )


@pytest.mark.parametrize("name", SHIPPED_ROLES)
def test_a_shipped_role_collides_with_a_catalog_entry_of_the_same_name(
    tmp_path: Path, name: str
) -> None:
    """Each of the five is refused beside a catalog file claiming its name.

    `oneagentgraph` will not decide for an operator which of two same-named personas a
    member runs, so a collision is a hard refusal — and a refusal is therefore proof
    that the crate ships that name. This is the positive half of the measurement; the
    negative half is the test below, and neither means anything without the other.
    """
    validated = _validated(_catalog_graph(tmp_path, name))

    assert validated.returncode != 0, (
        f"a catalog entry named {name!r} was accepted, so `oneagentgraph` no longer "
        f"ships that role:\n{validated.stdout}\n{validated.stderr}"
    )
    reported = validated.stdout + validated.stderr
    assert COLLIDES in reported, (
        f"{name!r} was refused for some reason other than colliding with a shipped "
        f"role, so this proves nothing about the shipped set:\n{reported}"
    )


@pytest.mark.parametrize("name", (*NOT_SHIPPED, CONTROL_NAME))
def test_a_name_this_crate_does_not_ship_is_left_to_the_catalog(tmp_path: Path, name: str) -> None:
    """`orchestrator`, `check-in`, and `pr-author` behave exactly like an invented name.

    The control is the point. Each of these validates beside a catalog entry claiming
    it, which under the rule the test above establishes means nothing is compiled in
    under that name — and `no-such-role` is here to show that "validates" is what a
    name the crate has never heard of does, not a special case for this repository's.
    """
    validated = _validated(_catalog_graph(tmp_path, name))

    assert validated.returncode == 0, (
        f"{name!r} did not validate beside a catalog entry claiming it; if this is a "
        f"collision, the crate has started shipping that role:\n"
        f"{validated.stdout}\n{validated.stderr}"
    )


def test_a_plan_node_naming_an_unshipped_persona_fails_before_a_harness_starts(
    tmp_path: Path,
) -> None:
    """The same finding at the boundary a planner actually meets it.

    The catalog journeys above measure the crate. This one measures the consequence
    the prose promises: a plan node whose `persona` is `orchestrator` does not quietly
    dispatch some built-in role, it settles `failed` naming the path
    `graphs/orchestrator` that the name was read as. That distinction is what
    `personas/README.md` sends a planner to `../personas/orchestrator.yaml` for.

    It is also why a mistyped persona is cheap: the dispatch dies in `oneagentgraph`'s
    config validation, so no harness is launched and no provider turn is billed. The
    launch directory is a throwaway git repository holding this checkout's real
    `graphs/` and configs, because `onepipeline` resolves the node-scope graph against
    the directory a run is launched from.
    """
    launch_dir = tmp_path / "launch"
    launch_dir.mkdir()
    for linked in (
        "graphs",
        "config",
        "personas",
        "oneharness.toml",
        "oneharness.judge.toml",
        "onetaskgraph.yaml",
    ):
        (launch_dir / linked).symlink_to(REPO_ROOT / linked)
    for git in (["init", "-q", "."], ["commit", "-q", "--allow-empty", "-m", "probe"]):
        subprocess.run(
            ["git", *git], cwd=launch_dir, check=True, capture_output=True, timeout=e2e_timeout(60)
        )

    write_plan_project(
        launch_dir / ".plans",
        {
            "schema_version": 3,
            "goal": {"text": "Measure how an unshipped persona name resolves"},
            "name": "persona-probe",
            "tasks": [
                {
                    "id": "probe",
                    "persona": "orchestrator",
                    "task": (
                        "## What\nNothing: this node is never expected to start.\n\n"
                        "## Why\nIts persona is the measurement.\n\n"
                        "## Acceptance criteria\n- Unreachable."
                    ),
                }
            ],
        },
    )

    environment = dict(os.environ)
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")
    environment["XDG_STATE_HOME"] = str(tmp_path / "state")
    # The probe project is written into this launch directory's own `.plans`, and
    # `onetaskgraph.yaml` roots the `authoring` source there *relatively* — so the root
    # is stated here, over whatever one this process was given, or the launch reads a
    # source rooted somewhere else entirely and never finds the project.
    environment[plan_root_variable.name()] = str(launch_dir / ".plans")
    environment["ONEPIPELINE_LAUNCHER"] = "pytest"
    environment["ONEPIPELINE_LAUNCHER_SESSION"] = "persona-catalog-e2e"

    launched = subprocess.run(
        ["onepipeline", "start", "authoring:persona-probe"],
        cwd=launch_dir,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
        check=False,
    )
    # An attached launch returns when the run settles, and 3 is the settlement this
    # run has: the graph did not complete and nothing is driving it any more, because
    # its only node failed. What matters is that the launcher itself ran — a refusal
    # of the plan, or a crash, exits differently and carries no node result to read.
    assert launched.returncode in (0, 3), launched.stdout + launched.stderr

    results = subprocess.run(
        ["onepipeline", "results", "persona-probe"],
        cwd=launch_dir,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    reported = results.stdout + results.stderr

    assert "failed" in reported, f"the node did not fail:\n{reported}"
    assert f"cannot read {launch_dir / 'graphs' / 'orchestrator'}" in reported, (
        "`orchestrator` was not resolved as a path relative to `graphs/`; if it "
        "dispatched instead, the crate now ships that role and `personas/README.md`, "
        f"`AGENTS.md`, and `graphs/dag-scope.yaml` all need revisiting:\n{reported}"
    )
