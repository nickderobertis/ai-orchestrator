"""The export grants a person sees in a design and approves for launch.

Patterns remain opaque strings here; the engine owns their glob grammar.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import TypedDict


class Edge(TypedDict):
    """An export edge's identity and ordered grants, without store-owned identifiers."""

    source: str
    to: str
    accepted_opaque: list[str]


def edges(record: Mapping[str, object]) -> list[Edge]:
    """Read grants at the plan-store boundary, rejecting malformed declarations."""
    metadata = record.get("metadata", {})
    raw = metadata.get("onepipeline.export_edges", []) if isinstance(metadata, Mapping) else []
    if not isinstance(raw, list):
        raise OSError("onepipeline.export_edges must be a list")
    result: list[Edge] = []
    for index, entry in enumerate(raw, 1):
        if not isinstance(entry, dict):
            raise OSError(f"export edge {index} must be an object")
        source, target = entry.get("from"), entry.get("to")
        patterns = entry.get("accepted_opaque", [])
        if not isinstance(source, str) or not isinstance(target, str):
            raise OSError(f"export edge {index} must name from and to")
        if not isinstance(patterns, list) or not all(isinstance(p, str) for p in patterns):
            raise OSError(f"export edge {source} -> {target}: accepted_opaque must be strings")
        result.append(Edge(source=source, to=target, accepted_opaque=patterns))
    return result


def digest(grants: list[Edge]) -> str:
    """Canonical JSON over (from, to, patterns), in plan order, including empty edges."""
    data = [(edge["source"], edge["to"], edge["accepted_opaque"]) for edge in grants]
    canonical = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def has_patterns(grants: list[Edge]) -> bool:
    """Whether any edge needs a grant; empty lists preserve the legacy approval shape."""
    return any(edge["accepted_opaque"] for edge in grants)


def names(grants: list[Edge]) -> str:
    """The current edge names a refusal asks a person to re-approve, even after removal."""
    return ", ".join(f"{edge['source']} -> {edge['to']}" for edge in grants)


def shown(content: str, grants: list[Edge]) -> None:
    """Hold the visible design section to the launch plan before recording an approval."""
    if not has_patterns(grants):
        grants = []
    heading = "## Accepted opaque content\n"
    section = content.split(heading, 1)[1].split("\n## ", 1)[0] if heading in content else ""
    valid = not section and not grants
    if section:
        try:
            literal = section.split("```json\n", 1)[1].split("\n```", 1)[0]
            valid = json.loads(literal) == grants and bool(grants)
        except (ValueError, IndexError):
            valid = False
    if not valid:
        raise OSError(
            f"Accepted opaque content on export edge(s) {names(grants) or 'previously declared'} "
            "differs from the design document; regenerate the design and re-approve it with "
            "`just approve-design <source>:<project>`"
        )
