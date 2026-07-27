#!/usr/bin/env python3
"""Fail when a DAG contract restated in Python drifts from its authoritative source.

Three vocabularies are mirrored across languages and would otherwise drift silently:
renderer node states against ``orchestrator/projection.py``, the read API's usage
field names against the pinned ``@oneharness/ui`` declaration, and the SSE event
names against ``docs/dag-ui/design.md``. Each side is parsed from its own file so a
change to one without the other fails ``just check``.
"""

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


def typed_dict_fields(path: Path, name: str) -> list[str]:
    """The annotated field names of one Python ``TypedDict`` declaration."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        fail(f"read {path.name} {name}: {exc}")
    declarations = [
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name
    ]
    if len(declarations) != 1:
        fail(f"{path.name} must declare exactly one {name}")
    fields = [
        statement.target.id
        for statement in declarations[0].body
        if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
    ]
    if not fields or len(fields) != len(set(fields)):
        fail(f"{path.name} {name} must declare unique annotated fields")
    return fields


def dict_values(path: Path, name: str) -> list[str]:
    """The string values of one module-level ``name = {...}`` mapping."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        fail(f"read {path.name} {name}: {exc}")
    declarations = [
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == name
        and isinstance(node.value, ast.Dict)
    ]
    if len(declarations) != 1:
        fail(f"{path.name} must declare exactly one {name} dict")
    mapping = declarations[0]
    assert isinstance(mapping, ast.Dict)
    values = [item.value for item in mapping.values if isinstance(item, ast.Constant)]
    if len(values) != len(mapping.values) or not all(isinstance(item, str) for item in values):
        fail(f"{path.name} {name} must map to string constants")
    return values


def enum_values(path: Path, name: str) -> list[str]:
    """The string values of one Python ``StrEnum`` declaration."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        fail(f"read {path.name} {name}: {exc}")
    declarations = [
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name
    ]
    if len(declarations) != 1:
        fail(f"{path.name} must declare exactly one {name}")
    values = [
        statement.value.value
        for statement in declarations[0].body
        if isinstance(statement, ast.Assign)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    ]
    if not values or len(values) != len(set(values)):
        fail(f"{path.name} {name} must declare unique string members")
    return values


def interface_fields(path: Path, name: str) -> list[str]:
    """The property names of one TypeScript ``export interface`` block."""
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        fail(f"read {path.name} {name}: {exc}")
    matches = re.findall(
        rf"export\s+interface\s+{re.escape(name)}\s*\{{(.*?)\n\}}", source, flags=re.DOTALL
    )
    if len(matches) != 1:
        fail(f"{path.name} must declare exactly one {name} interface")
    fields = re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\??\s*:", matches[0], flags=re.MULTILINE)
    if not fields or len(fields) != len(set(fields)):
        fail(f"{path.name} {name} must declare unique properties")
    return fields


def design_sse_events(path: Path) -> list[str]:
    """The SSE ``event`` names the design contract fixes, read from its prose list."""
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        fail(f"read {path.name} SSE events: {exc}")
    matches = re.findall(
        r"one of ((?:`[a-z.]+`(?:,\s*|,?\s*or\s*)?)+)\s*\n?in `event`", source, flags=re.DOTALL
    )
    if len(matches) != 1:
        fail(f"{path.name} must fix the SSE event vocabulary in exactly one 'in `event`' sentence")
    events = re.findall(r"`([a-z.]+)`", matches[0])
    if not events or len(events) != len(set(events)):
        fail(f"{path.name} SSE event vocabulary must list unique names")
    return events


def reconcile(what: str, python: tuple[str, list[str]], other: tuple[str, list[str]]) -> None:
    """Fail unless two sides of a mirrored contract name exactly the same members."""
    (python_where, python_names), (other_where, other_names) = python, other
    if set(python_names) != set(other_names):
        fail(
            f"{what}: {python_where} {sorted(python_names)!r} disagrees with "
            f"{other_where} {sorted(other_names)!r}; reconcile them in one change"
        )


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

    # The read API restates two vocabularies whose authoritative definitions live
    # outside Python: `@oneharness/ui`'s usage fields (pinned as a checked-in
    # declaration) and the SSE event names fixed by the design contract.
    conversations = root / "orchestrator/conversations.py"
    pinned = root / "docs/dag-ui/oneharness-ui-contract.d.ts"
    usage_fields = interface_fields(pinned, "ConversationUsage")
    reconcile(
        "ConversationUsage fields",
        (
            "orchestrator/conversations.py ConversationUsage",
            typed_dict_fields(conversations, "ConversationUsage"),
        ),
        ("docs/dag-ui/oneharness-ui-contract.d.ts", usage_fields),
    )
    reconcile(
        "ConversationUsage rename targets",
        (
            "orchestrator/conversations.py _USAGE_KEYS values",
            dict_values(conversations, "_USAGE_KEYS"),
        ),
        ("docs/dag-ui/oneharness-ui-contract.d.ts ConversationUsage", usage_fields),
    )
    reconcile(
        "SSE event vocabulary",
        (
            "orchestrator/server.py SseEvent",
            enum_values(root / "orchestrator/server.py", "SseEvent"),
        ),
        ("docs/dag-ui/design.md", design_sse_events(root / "docs/dag-ui/design.md")),
    )
    print("dag state contract: Python, TypeScript, and the design contract agree")


if __name__ == "__main__":
    main()
