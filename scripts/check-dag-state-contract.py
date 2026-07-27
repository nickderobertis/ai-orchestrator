#!/usr/bin/env python3
"""Fail when a DAG contract restated in Python drifts from its authoritative source.

Several shapes and vocabularies are mirrored across languages and would otherwise
drift silently: renderer node states against ``orchestrator/projection.py``, the
transcript payloads against the pinned ``@oneharness/ui`` declaration, and this
repository's own envelope, HTTP payloads, SSE event names, and documented network
defaults against ``docs/dag-ui/design.md``, and the semantic agent roles across
``orchestrator/labels.py``, the ``dag-model`` enum, the design contract, and the
judge's history-label config. Each side is parsed from its own file so a change to
one without the other fails ``just check``.

Payload reconciliation is asymmetric on purpose — see ``reconcile_shape``.
"""

# llmlint: ignore-file[contracts_have_one_source_or_a_drift_gate] This file *is* the
# drift gate. It reconciles everything with an exact cross-language meaning: field
# names, optionality, and the closed value vocabularies (node states, SSE events,
# launcher kinds). Structural field types are deliberately out of scope — deciding
# that `list[RunSummary]` and `RunSummary[]`, or `TimingRecord` and `Timing`, are the
# same type needs a shared IDL both sides generate from, which is a project of its
# own rather than a check this script can make without guessing. Comparing them by
# an approximate normalizer would report drift that is not drift, and the resulting
# suppressions would erode the exact checks above.

from __future__ import annotations

import ast
import re
import subprocess
import sys
import tomllib
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
        match statement:
            case ast.AnnAssign(
                target=ast.Name(id=field),
                annotation=ast.Subscript(value=ast.Name(id="NotRequired")),
            ):
                required = False
            case ast.AnnAssign(target=ast.Name(id=field)):
                required = total
            case _:
                continue
        if field in fields:
            fail(f"{path.name} {name} must declare unique annotated fields")
        fields[field] = required
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


def union_members(path: Path, interface: str, prop: str) -> list[str]:
    """The string members of a TypeScript ``a | b | c`` union on one interface property."""
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        fail(f"read {path.name} {interface}.{prop}: {exc}")
    matches = re.findall(
        rf"^(?:export\s+)?interface\s+{re.escape(interface)}\s*\{{.*?"
        rf"^\s+{re.escape(prop)}\??\s*:\s*((?:\"[^\"]+\"\s*\|?\s*)+);",
        source,
        flags=re.DOTALL | re.MULTILINE,
    )
    if len(matches) != 1:
        fail(f"{path.name} must declare exactly one {interface}.{prop} string union")
    members = re.findall(r'"([^"]+)"', matches[0])
    if not members or len(members) != len(set(members)):
        fail(f"{path.name} {interface}.{prop} union must list unique members")
    return members


def module_number(path: Path, name: str) -> float:
    """The value of one module-level numeric constant."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        fail(f"read {path.name} {name}: {exc}")
    values = [
        node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == name
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, int | float)
        and not isinstance(node.value.value, bool)
    ]
    if len(values) != 1:
        fail(f"{path.name} must declare exactly one numeric {name}")
    return float(values[0])


def documented_number(path: Path, what: str, pattern: str) -> float:
    """The single number the contract states for ``what``, via a capturing ``pattern``.

    Every occurrence must agree: a document that states one default in prose and a
    different one in its example has already drifted from itself.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        fail(f"read {path.name} {what}: {exc}")
    found = {float(match) for match in re.findall(pattern, source, flags=re.DOTALL)}
    if len(found) != 1:
        fail(
            f"{path.name} must state exactly one {what}; found {sorted(found)!r}. "
            "Reconcile the prose and the example with each other first."
        )
    return found.pop()


def reconcile_number(what: str, python: tuple[str, float], other: tuple[str, float]) -> None:
    """Fail unless a Python default and its documented value are the same number."""
    (python_where, python_value), (other_where, other_value) = python, other
    if python_value != other_value:
        fail(
            f"{what}: {python_where} is {python_value:g} but "
            f"{other_where} says {other_value:g}; reconcile them in one change"
        )


def frozenset_members(path: Path, name: str) -> list[str]:
    """The string members of a module-level ``name: frozenset[str] = frozenset({...})``."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        fail(f"read {path.name} {name}: {exc}")
    calls = [
        node.value
        for node in tree.body
        if isinstance(node, ast.AnnAssign | ast.Assign)
        and name
        in {
            target.id
            for target in ([node.target] if isinstance(node, ast.AnnAssign) else node.targets)
            if isinstance(target, ast.Name)
        }
    ]
    if len(calls) != 1 or not isinstance(calls[0], ast.Call) or not calls[0].args:
        fail(f"{path.name} must declare exactly one {name} frozenset")
    argument = calls[0].args[0]
    if not isinstance(argument, ast.Set):
        fail(f"{path.name} {name} must be built from a set literal")
    members = [
        item.value
        for item in argument.elts
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
    ]
    if len(members) != len(argument.elts) or len(members) != len(set(members)):
        fail(f"{path.name} {name} must contain unique string members")
    return members


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
        fail(
            f"{path.name} must declare exactly one {name}; restore one authoritative "
            f"`{name} = Literal[...]` assignment and remove duplicates"
        )
    annotation = declarations[0]
    if not (
        isinstance(annotation, ast.Subscript)
        and isinstance(annotation.value, ast.Name)
        and annotation.value.id == "Literal"
    ):
        fail(
            f"{path.name} {name} must remain a string Literal; replace its value "
            f'with `{name} = Literal["role", ...]`'
        )
    elements = annotation.slice.elts if isinstance(annotation.slice, ast.Tuple) else []
    values = [
        item.value
        for item in elements
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
    ]
    if len(values) != len(elements) or not values or len(values) != len(set(values)):
        fail(
            f"{path.name} {name} must contain unique string values; remove duplicates "
            "and replace non-string entries before retrying"
        )
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
        fail(
            "packages/dag-model/src/index.ts must declare exactly one agentRoleSchema; "
            "restore one exported `z.enum([...])` declaration and remove duplicates"
        )
    body = matches[0]
    roles = re.findall(r'"([^"]+)"', body)
    remainder = re.sub(r'"[^"]+"\s*,?', "", body)
    if remainder.strip() or not roles or len(roles) != len(set(roles)):
        fail(
            "packages/dag-model/src/index.ts agentRoleSchema must contain unique string "
            "roles; remove duplicates or non-string entries and restore any missing roles"
        )
    return roles


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
        fail(
            "docs/dag-ui/design.md must declare exactly one AgentRole union; restore "
            "one `type AgentRole = ...;` block and remove duplicates"
        )
    body = matches[0]
    roles = re.findall(r'"([^"]+)"', body)
    remainder = re.sub(r'\s*\|\s*"[^"]+"', "", body)
    if remainder.strip() or not roles or len(roles) != len(set(roles)):
        fail(
            "docs/dag-ui/design.md AgentRole must contain unique string union members; "
            "remove duplicates or malformed members and restore any missing roles"
        )
    return roles


def judge_agent_role(path: Path) -> str:
    try:
        config = tomllib.loads(path.read_text(encoding="utf-8"))
        history_labels = config["history_labels"]
        role = history_labels["agent_role"]
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        fail(
            "read oneharness.judge.toml history_labels.agent_role: "
            f"{exc}; restore the judge history-label configuration and retry"
        )
    if not isinstance(role, str) or not role:
        fail("oneharness.judge.toml history_labels.agent_role must be a non-empty string")
    return role


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

    # The out-of-repo provenance record: its shape, its own schema version, and the
    # narrower launcher set that actually gets a record written.
    launch = root / "orchestrator/launch.py"
    reconcile_shape(
        launch, "LaunchProvenance", design, interface_fields(design, "LaunchProvenance")
    )
    reconcile_number(
        "provenance schema version",
        (
            "orchestrator/launch.py PROVENANCE_SCHEMA_VERSION",
            module_number(launch, "PROVENANCE_SCHEMA_VERSION"),
        ),
        (
            "docs/dag-ui/design.md LaunchProvenance",
            documented_number(
                design,
                "provenance schema version",
                r"interface LaunchProvenance \{\n  schema_version: (\d+)",
            ),
        ),
    )
    reconcile(
        "recorded launcher vocabulary",
        ("orchestrator/launch.py KNOWN_LAUNCHERS", frozenset_members(launch, "KNOWN_LAUNCHERS")),
        (
            "docs/dag-ui/design.md LaunchProvenance.launcher",
            union_members(design, "LaunchProvenance", "launcher"),
        ),
    )

    reconcile(
        "launcher vocabulary",
        (
            "orchestrator/launch.py LAUNCHER_KINDS",
            frozenset_members(root / "orchestrator/launch.py", "LAUNCHER_KINDS"),
        ),
        (
            "docs/dag-ui/design.md RunLaunch.launcher",
            union_members(design, "RunLaunch", "launcher"),
        ),
    )
    # The embedded telemetry schema version: Python owns it, the contract restates it,
    # and the dag-model schema pins it as a literal. Main bumped 6 -> 7 while this
    # contract still said 6, which is exactly the drift this reconciles.
    reconcile_number(
        "telemetry schema version",
        (
            "orchestrator/telemetry.py TELEMETRY_SCHEMA_VERSION",
            module_number(root / "orchestrator/telemetry.py", "TELEMETRY_SCHEMA_VERSION"),
        ),
        (
            "docs/dag-ui/design.md",
            documented_number(
                design, "telemetry schema version", r"telemetry_schema_version:? (\d+)"
            ),
        ),
    )
    reconcile_number(
        "telemetry schema version",
        (
            "orchestrator/telemetry.py TELEMETRY_SCHEMA_VERSION",
            module_number(root / "orchestrator/telemetry.py", "TELEMETRY_SCHEMA_VERSION"),
        ),
        (
            "packages/dag-model/src/index.ts",
            documented_number(
                root / "packages/dag-model/src/index.ts",
                "telemetry schema version",
                r"telemetry_schema_version: z\.literal\((\d+)\)",
            ),
        ),
    )

    # Network defaults the contract states in prose and the server states in code.
    server = root / "orchestrator/server.py"
    reconcile_number(
        "default port",
        ("orchestrator/server.py DEFAULT_PORT", module_number(server, "DEFAULT_PORT")),
        (
            "docs/dag-ui/design.md",
            documented_number(design, "default port", r"127\.0\.0\.1:(\d+)"),
        ),
    )
    reconcile_number(
        "SSE heartbeat interval",
        (
            "orchestrator/server.py DEFAULT_HEARTBEAT_INTERVAL",
            module_number(server, "DEFAULT_HEARTBEAT_INTERVAL"),
        ),
        (
            "docs/dag-ui/design.md",
            documented_number(
                design,
                "SSE heartbeat interval",
                r"heartbeat comments\s+at least every (\d+) second",
            ),
        ),
    )

    reconcile(
        "SSE event vocabulary",
        (
            "orchestrator/server.py SseEvent",
            enum_values(root / "orchestrator/server.py", "SseEvent"),
        ),
        ("docs/dag-ui/design.md", design_sse_events(design)),
    )

    # Semantic agent roles and the judge's configured role, reconciled across the
    # Python Literal, the dag-model enum, the design contract, and the judge config.
    roles = literal_values(root / "orchestrator/labels.py", "AgentRole")
    typescript_roles = typescript_agent_roles(root / "packages/dag-model/src/index.ts")
    documented_roles = documented_agent_roles(root / "docs/dag-ui/design.md")
    configured_judge_role = judge_agent_role(root / "oneharness.judge.toml")
    if not set(roles) == set(typescript_roles) == set(documented_roles):
        fail(
            "semantic agent roles disagree across orchestrator/labels.py, "
            "packages/dag-model/src/index.ts, and docs/dag-ui/design.md; treat the "
            "Python AgentRole Literal as authoritative, then update the TypeScript "
            "agentRoleSchema and documented AgentRole union to contain the same roles"
        )
    if configured_judge_role != "judge" or configured_judge_role not in roles:
        fail(
            "oneharness.judge.toml history_labels.agent_role "
            f"{configured_judge_role!r} disagrees with the authoritative Python "
            'AgentRole judge member; restore `agent_role = "judge"` and ensure '
            "orchestrator/labels.py retains the `judge` role"
        )
    print("dag state contract: Python, TypeScript, docs, and judge config agree")


if __name__ == "__main__":
    main()
