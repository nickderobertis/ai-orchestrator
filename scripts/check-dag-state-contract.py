#!/usr/bin/env python3
"""Fail when a DAG contract restated in Python drifts from its authoritative source.

Several shapes and vocabularies are mirrored across languages and would otherwise
drift silently: renderer node states against ``orchestrator/projection.py``, the
transcript payloads against the pinned ``@oneharness/ui`` declaration, and this
repository's own envelope, HTTP payloads, and SSE event names against
``docs/dag-ui/design.md``. Each side is parsed from its own file so a change to one
without the other fails ``just check``.

Payload reconciliation is asymmetric on purpose — see ``reconcile_shape``.
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


def typed_dict_fields(path: Path, name: str) -> dict[str, bool]:
    """Field name -> required for one Python ``TypedDict`` declaration.

    A field is optional when annotated ``NotRequired[...]`` or when the class opts
    out wholesale with ``total=False``.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        fail(f"read {path.name} {name}: {exc}")
    declarations = [
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name
    ]
    if len(declarations) != 1:
        fail(f"{path.name} must declare exactly one {name}")
    declaration = declarations[0]
    total = not any(
        keyword.arg == "total"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is False
        for keyword in declaration.keywords
    )
    fields: dict[str, bool] = {}
    for statement in declaration.body:
        if not (isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)):
            continue
        annotation = statement.annotation
        not_required = (
            isinstance(annotation, ast.Subscript)
            and isinstance(annotation.value, ast.Name)
            and annotation.value.id == "NotRequired"
        )
        if statement.target.id in fields:
            fail(f"{path.name} {name} must declare unique annotated fields")
        fields[statement.target.id] = total and not not_required
    if not fields:
        fail(f"{path.name} {name} must declare annotated fields")
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


def _properties(path: Path, name: str, body: str) -> dict[str, bool]:
    """Property name -> required, for one TypeScript object body's top-level fields.

    Nested object literals are skipped by tracking brace depth, so an inline
    ``plan: { tasks: ... }`` contributes ``plan`` and not its inner keys.
    """
    fields: dict[str, bool] = {}
    depth = 0
    for line in body.splitlines():
        if depth == 0 and (match := re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)(\??)\s*:", line)):
            if match.group(1) in fields:
                fail(f"{path.name} {name} must declare unique properties")
            fields[match.group(1)] = match.group(2) != "?"
        depth += line.count("{") - line.count("}")
    if not fields:
        fail(f"{path.name} {name} must declare properties")
    return fields


def interface_fields(path: Path, name: str) -> dict[str, bool]:
    """Property name -> required for one TypeScript ``interface`` block.

    ``export`` is optional so the same parser reads the pinned ``.d.ts`` and the
    design contract's fenced TypeScript.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        fail(f"read {path.name} {name}: {exc}")
    matches = re.findall(
        rf"^(?:export\s+)?interface\s+{re.escape(name)}\s*\{{(.*?)\n\}}",
        source,
        flags=re.DOTALL | re.MULTILINE,
    )
    if len(matches) != 1:
        fail(f"{path.name} must declare exactly one {name} interface")
    return _properties(path, name, matches[0])


def nested_object_fields(path: Path, interface: str, prop: str) -> dict[str, bool]:
    """Property name -> required for an inline object literal nested under ``prop``."""
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        fail(f"read {path.name} {interface}.{prop}: {exc}")
    matches = re.findall(
        rf"^(?:export\s+)?interface\s+{re.escape(interface)}\s*\{{.*?"
        rf"^\s+{re.escape(prop)}\??\s*:\s*\{{(.*?)^\s+\}};",
        source,
        flags=re.DOTALL | re.MULTILINE,
    )
    if len(matches) != 1:
        fail(f"{path.name} must declare exactly one {interface}.{prop} object literal")
    return _properties(path, f"{interface}.{prop}", matches[0])


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


def reconcile_shape(
    python_module: Path, python_name: str, contract: Path, contract_fields: dict[str, bool]
) -> None:
    """Fail when a Python payload type and its declared shape cannot both be honored.

    Asymmetric on purpose: an invented Python field would be served to a client that
    never declared it, while an *optional* contract field the server does not populate
    is legal — omitted-when-unavailable is the contract's own rule. A **required**
    contract field must exist in Python or a documented response would be incomplete.

    Optionality is compared in the same direction: a Python field the server may omit
    cannot satisfy a contract field the client is entitled to find. The reverse —
    always populating an optional field — is fine. Field *types* are not compared;
    ``list[RunSummary]`` and ``RunSummary[]`` have no mechanical equivalence without a
    shared IDL, so this gate reconciles the names and optionality that it can check
    exactly rather than approximating the rest.
    """
    declared = typed_dict_fields(python_module, python_name)
    where = f"{python_module.parent.name}/{python_module.name} {python_name}"
    if invented := set(declared) - set(contract_fields):
        fail(
            f"{where} declares {sorted(invented)!r}, which "
            f"{contract.name} does not; add it to the contract or drop it"
        )
    required = {name for name, is_required in contract_fields.items() if is_required}
    if missing := required - set(declared):
        fail(
            f"{where} omits required {contract.name} field(s) {sorted(missing)!r}; "
            "serve them or make them optional in the contract"
        )
    if optional := {name for name in required if not declared[name]}:
        fail(
            f"{where} makes {sorted(optional)!r} optional, but {contract.name} "
            "requires them; always populate them or relax the contract"
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
    read_model = root / "orchestrator/read_model.py"
    pinned = root / "docs/dag-ui/oneharness-ui-contract.d.ts"
    design = root / "docs/dag-ui/design.md"

    # Transcript shapes: the pinned `@oneharness/ui` declaration is authoritative.
    for name in ("ConversationUsage", "ConversationToolEvent", "ConversationTurn", "Conversation"):
        reconcile_shape(conversations, name, pinned, interface_fields(pinned, name))

    # The rename table must target exactly the pinned usage fields.
    reconcile(
        "ConversationUsage rename targets",
        (
            "orchestrator/conversations.py _USAGE_KEYS values",
            dict_values(conversations, "_USAGE_KEYS"),
        ),
        (
            "docs/dag-ui/oneharness-ui-contract.d.ts ConversationUsage",
            list(interface_fields(pinned, "ConversationUsage")),
        ),
    )

    # This repository's own envelope and HTTP payloads: the design contract is
    # authoritative. Attribution is an inline object literal inside DagConversation.
    reconcile_shape(
        conversations, "DagConversation", design, interface_fields(design, "DagConversation")
    )
    reconcile_shape(
        conversations,
        "Attribution",
        design,
        nested_object_fields(design, "DagConversation", "attribution"),
    )
    for name in ("RunLaunch", "RunSummary", "RunList", "Round", "RunDetail"):
        reconcile_shape(read_model, name, design, interface_fields(design, name))

    reconcile(
        "SSE event vocabulary",
        (
            "orchestrator/server.py SseEvent",
            enum_values(root / "orchestrator/server.py", "SseEvent"),
        ),
        ("docs/dag-ui/design.md", design_sse_events(design)),
    )
    print("dag state contract: Python, TypeScript, and the design contract agree")


if __name__ == "__main__":
    main()
