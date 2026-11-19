"""The two shapes this package's own readers name a plan node and a repository by.

`orchestrator/host_adoption_check.py` and `orchestrator/budgets/design_doc_dag.py` read a
plan the engine loaded, never the plan store itself, so what they share is a node's id and
the normalized origin a task record's `repositories` list holds — and nothing else.
"""

from __future__ import annotations

import re
from typing import NewType

#: A plan node's id, as the engine's loaded plan names it.
NodeId = NewType("NodeId", str)

#: The shape of a normalized origin — `host/owner/name` — which is the one value a task
#: record's top-level `repositories` list can hold. The scheme and a `.git` suffix are
#: what a clone URL carries beyond it, so both are dropped before the shape is asked.
#: `onevcs` files a hosted repository under exactly this key, and its `resolve` answers
#: an identity's `origin` in it, so what this recognizes is what that CLI would answer.
#: Each segment is held to the characters a host name, an owner and a repository
#: name are made of, because the value is promoted into a record other programs then read:
#: whitespace, a query string or a control character in a segment is not an origin.
_HOSTED_ORIGIN = re.compile(r"[A-Za-z0-9.-]+/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+")
_CLONE_SCHEME = re.compile(r"^https?://")


def hosted_origin(repo: str) -> str | None:
    """``repo`` as the normalized origin `repositories` can hold, or ``None``.

    ``None`` for every other spelling of a repository — a registered alias, an absolute
    path, an scp-like clone URL — because none of them is a value that list holds: the
    engine reads a node's ``repo`` from that list's first entry and reserves the
    ``onepipeline.repo`` key for an identity a normalized origin cannot name, which is a
    local checkout `onevcs` knows by its path. This is the one place that shape is
    decided, and `orchestrator/host_adoption_check.py` asks it of what `onevcs` answers.
    """
    normalized = _CLONE_SCHEME.sub("", repo).removesuffix(".git")
    return normalized if _HOSTED_ORIGIN.fullmatch(normalized) else None
