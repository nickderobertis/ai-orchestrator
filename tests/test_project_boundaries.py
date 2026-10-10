"""Every edge of the Nx project graph keeps to the boundary rules `tests/AGENTS.md` states.

The tags every `project.json` carries — `type:tests`, `type:test-support`, a `scope:*` —
say what kind of project each is, and a dependency edge is only sound between some kinds:
a test-support unit that depended on a project collecting tests would put that project's
journeys in every key the unit reaches, and a test project depending on another would make
an edit to one select the other. Nothing in Nx reads tags on its own, so this does.

The rules are read from the table in `tests/AGENTS.md`, where a contributor adding a project
reads them, so they are stated once; the edges are read from the project graph Nx itself
builds — every `implicitDependencies` entry and every edge Nx infers — rather than from the
declarations alone, so an edge a plugin or a source import adds is held to the same rules.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from nx_workspace import (
    OFFLINE_INSTALLS,
    TOOLCHAIN_WRITER_MARKS,
    WORKSPACE_INSTALL_MARKS,
    isolated_python_root,
)

from orchestrator.root import REPO_ROOT

# The rules are this repository's prose and the graph is read off the whole tree, so the
# check belongs to the whole-workspace tier. The wrapper heals the installs of the checkout
# it runs in, so it runs in a copy whose toolchain is its own: a writer.
# llmlint: ignore[shell_test_tiers_stay_split, test_tiers_split_by_project_not_by_marker] The
# check's subject is every project's tags and edges, so it reads the whole tree and belongs to
# the whole-workspace tier the marker names; its one host-tool call is a single `nx graph` read
# through this checkout's wrapper, which is the graph the rules are about.
pytestmark = [pytest.mark.reads_docs, *WORKSPACE_INSTALL_MARKS, *TOOLCHAIN_WRITER_MARKS]

#: Where the rules are stated, and the heading their table sits under.
RULES_DOCUMENT = REPO_ROOT / "tests" / "AGENTS.md"
RULES_HEADING = "## Project boundaries"
#: One row of that table: the tag a project carries, and the tags it may depend on.
RULE_ROW = re.compile(r"^\| `([^`]+)` \| (.+?) \|$")
NOTHING = "nothing"


@dataclass(frozen=True)
class Rule:
    """A project carrying ``tag`` may depend only on projects carrying one of ``allowed``."""

    tag: str
    allowed: frozenset[str]

    def describe(self) -> str:
        targets = ", ".join(f"`{tag}`" for tag in sorted(self.allowed)) or NOTHING
        return f"a project tagged `{self.tag}` may depend only on projects tagged {targets}"


def rules(document: str) -> tuple[Rule, ...]:
    """The boundary rules the table under :data:`RULES_HEADING` states."""
    _, heading, section = document.partition(RULES_HEADING)
    assert heading, f"{RULES_DOCUMENT} has no {RULES_HEADING!r} section"
    found: list[Rule] = []
    for line in section.split("\n## ", 1)[0].splitlines():
        row = RULE_ROW.match(line.strip())
        if row is None:
            continue
        allowed = row.group(2).strip()
        found.append(
            Rule(
                tag=row.group(1),
                allowed=frozenset()
                if allowed == NOTHING
                else frozenset(re.findall(r"`([^`]+)`", allowed)),
            )
        )
    assert found, f"the {RULES_HEADING!r} section of {RULES_DOCUMENT} states no rule"
    return tuple(found)


@dataclass(frozen=True)
class Graph:
    """Each project's tags, and each dependency edge, as Nx reports them."""

    tags: dict[str, frozenset[str]]
    edges: tuple[tuple[str, str], ...]

    @classmethod
    def read(cls, written: dict) -> Graph:
        nodes = written["graph"]["nodes"]
        return cls(
            tags={name: frozenset(node["data"].get("tags", [])) for name, node in nodes.items()},
            edges=tuple(
                sorted(
                    (source, edge["target"])
                    for source, listed in written["graph"]["dependencies"].items()
                    for edge in listed
                )
            ),
        )


def violations(graph: Graph, stated: tuple[Rule, ...]) -> list[str]:
    """Every project no rule governs, and every edge a rule its source falls under forbids."""
    found: list[str] = []
    for project, tags in sorted(graph.tags.items()):
        if not any(rule.tag in tags for rule in stated):
            found.append(
                f"project {project} carries {sorted(tags)}, which no rule in "
                f"{RULES_DOCUMENT.relative_to(REPO_ROOT)} governs; tag it as one of "
                f"{sorted(rule.tag for rule in stated)}"
            )
    for source, target in graph.edges:
        for rule in stated:
            if rule.tag in graph.tags[source] and not rule.allowed & graph.tags[target]:
                found.append(
                    f"project {source} depends on {target} (tagged "
                    f"{sorted(graph.tags[target])}), which the rule '{rule.describe()}' forbids"
                )
    return found


def _nx_graph(checkout: Path, written: Path) -> dict:
    """The project graph this tree's own Nx builds, through the repository's wrapper.

    Run in ``checkout``, an `isolated_python_root` copy of this working tree whose
    `node_modules` and `.venv` are both its own, because the wrapper heals both installs
    of the checkout it runs in; the graph it builds there is this tree's. Both heals run
    offline, against the lockfiles this checkout's own installs already resolved.
    """
    result = subprocess.run(
        ["./scripts/nx.sh", "graph", f"--file={written}"],
        cwd=checkout,
        env={**os.environ, **OFFLINE_INSTALLS, "NX_DAEMON": "false"},
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(written.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def graph(tmp_path_factory: pytest.TempPathFactory) -> dict:
    checkout = isolated_python_root(tmp_path_factory.mktemp("nx-graph-checkout") / "checkout")
    return _nx_graph(checkout, tmp_path_factory.mktemp("nx-graph") / "graph.json")


@pytest.fixture(scope="module")
def stated() -> tuple[Rule, ...]:
    return rules(RULES_DOCUMENT.read_text(encoding="utf-8"))


def test_every_edge_of_the_project_graph_keeps_to_the_stated_boundaries(
    graph: dict, stated: tuple[Rule, ...]
) -> None:
    read = Graph.read(graph)
    # A graph with no edges would pass any rule, so the read has to have found the
    # graph this repository has: test projects depending on units.
    assert read.edges and any(
        "type:tests" in read.tags[source] and "type:test-support" in read.tags[target]
        for source, target in read.edges
    ), read.edges[:5]
    assert not violations(read, stated), "\n".join(violations(read, stated))


def test_a_forbidden_edge_is_refused_naming_the_project_the_edge_and_the_rule(
    graph: dict, stated: tuple[Rule, ...]
) -> None:
    """One edge added to the real graph — a unit depending on the journeys' project — and
    one project carrying no tag a rule names, each refused by name."""
    read = Graph.read(graph)
    unit, journeys = "support-waits", "orchestrator-e2e"
    assert "type:test-support" in read.tags[unit] and "type:tests" in read.tags[journeys]
    forbidden = Graph(
        tags={**read.tags, "untagged-probe": frozenset({"lang:python"})},
        edges=(*read.edges, (unit, journeys)),
    )

    refused = violations(forbidden, stated)

    assert refused == [
        "project untagged-probe carries ['lang:python'], which no rule in tests/AGENTS.md "
        "governs; tag it as one of " + str(sorted(rule.tag for rule in stated)),
        f"project {unit} depends on {journeys} (tagged "
        f"{sorted(read.tags[journeys])}), which the rule 'a project tagged "
        "`type:test-support` may depend only on projects tagged `type:test-support`' forbids",
    ], refused
