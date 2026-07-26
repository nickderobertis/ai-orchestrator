#!/usr/bin/env python3
"""Fail when renderer states or semantic agent roles drift from Python contracts."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path


def fail(message: str) -> None:
    print(f"dag state contract: {message}", file=sys.stderr)
    raise SystemExit(1)


def projection_states(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        fail(f"read authoritative orchestrator/projection.py NodeState: {exc}")
    declarations = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "NodeState"
    ]
    if len(declarations) != 1:
        fail("orchestrator/projection.py must declare exactly one NodeState")
    annotation = declarations[0].value
    if not (
        isinstance(annotation, ast.Subscript)
        and isinstance(annotation.value, ast.Name)
        and annotation.value.id == "Literal"
        and isinstance(annotation.slice, ast.Tuple)
    ):
        fail("orchestrator/projection.py NodeState must remain a string Literal")
    states = [
        item.value
        for item in annotation.slice.elts
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
    ]
    if len(states) != len(annotation.slice.elts) or len(states) != len(set(states)):
        fail("orchestrator/projection.py NodeState must contain unique string states")
    return states


def layout_states(path: Path) -> list[str]:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        fail(f"read packages/dag-layout/src/index.ts DAG_NODE_STATES: {exc}")
    matches = re.findall(
        r"export\s+const\s+DAG_NODE_STATES\s*=\s*\[(.*?)\]\s*as\s+const\s*;",
        source,
        flags=re.DOTALL,
    )
    if len(matches) != 1:
        fail("packages/dag-layout/src/index.ts must declare exactly one DAG_NODE_STATES tuple")
    body = matches[0]
    states = re.findall(r'"([^"]+)"', body)
    remainder = re.sub(r'"[^"]+"\s*,?', "", body)
    if remainder.strip() or not states or len(states) != len(set(states)):
        fail("packages/dag-layout/src/index.ts DAG_NODE_STATES must contain unique string states")
    return states


def literal_values(path: Path, name: str) -> list[str]:
    """Read one authoritative unique string Literal assignment."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        fail(
            f"read authoritative {path.name} {name}: {exc}; restore a valid {path} "
            "from the repository and retry"
        )
    declarations = [
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == name
    ]
    if len(declarations) != 1:
        fail(f"{path.name} must declare exactly one {name}")
    annotation = declarations[0]
    if not (
        isinstance(annotation, ast.Subscript)
        and isinstance(annotation.value, ast.Name)
        and annotation.value.id == "Literal"
    ):
        fail(f"{path.name} {name} must remain a string Literal")
    elements = annotation.slice.elts if isinstance(annotation.slice, ast.Tuple) else []
    values = [
        item.value
        for item in elements
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
    ]
    if len(values) != len(elements) or not values or len(values) != len(set(values)):
        fail(f"{path.name} {name} must contain unique string values")
    return values


def typescript_agent_roles(path: Path) -> list[str]:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        fail(
            f"read TypeScript agent-role contract: {exc}; restore {path} from the "
            "repository and retry"
        )
    matches = re.findall(
        r"export\s+const\s+agentRoleSchema\s*=\s*z\.enum\(\[(.*?)\]\);",
        source,
        flags=re.DOTALL,
    )
    if len(matches) != 1:
        fail("packages/dag-model/src/index.ts must declare exactly one agentRoleSchema")
    return re.findall(r'"([^"]+)"', matches[0])


def documented_agent_roles(path: Path) -> list[str]:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        fail(
            f"read documented agent-role contract: {exc}; restore {path} from the "
            "repository and retry"
        )
    matches = re.findall(r"type AgentRole =\n(.*?);", source, flags=re.DOTALL)
    if len(matches) != 1:
        fail("docs/dag-ui/design.md must declare exactly one AgentRole union")
    return re.findall(r'"([^"]+)"', matches[0])


def main() -> None:
    try:
        root = Path(
            subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        fail(f"resolve the repository checkout and retry: {exc}")
    projection = projection_states(root / "orchestrator/projection.py")
    layout = layout_states(root / "packages/dag-layout/src/index.ts")
    # Unstarted nodes have no projection entry, but renderers need their pending state.
    expected = {"pending", *projection}
    if set(layout) != expected:
        fail(
            "packages/dag-layout/src/index.ts DAG_NODE_STATES "
            f"{sorted(layout)!r} disagrees with orchestrator/projection.py "
            f"NodeState plus pending {sorted(expected)!r}; reconcile the TypeScript "
            "list with the Python projection states while retaining pending"
        )
    roles = literal_values(root / "orchestrator/labels.py", "AgentRole")
    typescript_roles = typescript_agent_roles(root / "packages/dag-model/src/index.ts")
    documented_roles = documented_agent_roles(root / "docs/dag-ui/design.md")
    if not set(roles) == set(typescript_roles) == set(documented_roles):
        fail(
            "semantic agent roles disagree across orchestrator/labels.py, "
            "packages/dag-model/src/index.ts, and docs/dag-ui/design.md; treat the "
            "Python AgentRole Literal as authoritative, then update the TypeScript "
            "agentRoleSchema and documented AgentRole union to contain the same roles"
        )
    print("dag state contract: Python, TypeScript, and documented contracts agree")


if __name__ == "__main__":
    main()
