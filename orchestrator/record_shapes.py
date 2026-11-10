"""The shapes a plan-store record here is read and named by, with no writer of a plan.

A plan's project and task records are created by the pinned store itself —
`orchestrator/plan_records.py` drives its `project create` and `task create` — so what is
left here is what several readers share about a record's shape: the qualified-id and
normalized-origin parsers, the cross-DAG reference, the reserved metadata prefix and the
local Markdown layout's directory names. :func:`frontmatter` remains the renderer the
follow-up drafts and tickets write their own records through, until those move onto the
store as well.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import NamedTuple, NotRequired, TypedDict


class PlanNode(TypedDict):
    """One generated node before the store creates it as a task."""

    id: str
    task: NotRequired[str]
    title: NotRequired[str]
    deps: NotRequired[list[str]]
    repo: NotRequired[str]
    delivers: NotRequired[list[str]]
    unit: NotRequired[str]


class PlanDocument(TypedDict):
    """The stable portion of onepipeline's generated plan contract."""

    name: str
    tasks: list[PlanNode]


#: What every field of the plan itself is namespaced under in a record's metadata. A
#: caller's own project metadata is written beside those and may never name one: the two
#: are merged, the caller's would win, and a project would then state a plan field that
#: no plan declared.
ENGINE_PREFIX = "onepipeline."

#: The directories of a local-md root a record sits in: `<root>/projects/<project>.md`,
#: `<root>/tasks/…` and `<root>/documents/<document>.md`. The store owns the layout; these
#: names are what `orchestrator/plan_store.py` resolves a record's path and its root by, and
#: what `orchestrator/plan_budgets.py` names a design document's directory by before the
#: document exists.
PROJECTS_DIRECTORY = "projects"
TASKS_DIRECTORY = "tasks"
DOCUMENTS_DIRECTORY = "documents"

#: The shape of a normalized origin — `host/owner/name` — which is the one value a task
#: record's top-level `repositories` list can hold. The scheme and a `.git` suffix are
#: what a clone URL carries beyond it, so both are dropped before the shape is asked.
#: `onevcs` files a hosted repository under exactly this key, and its `resolve` answers
#: an identity's `origin` in it, so what this recognizes is what that CLI would answer.
#: Reconciled against both owners rather than trusted:
#: `tests/plan_tooling/check_and_review/test_check_plan_recipe_e2e.py` registers a real
#: hosted scratch identity, writes a record through this shape, and reads the node's `repo`
#: back out of the installed engine's own loader equal to the origin the installed `onevcs`
#: answers. Each segment is held to the characters a host name, an owner and a repository
#: name are made of, because the value is promoted into a record other programs then read:
#: whitespace, a query string or a control character in a segment is not an origin.
_HOSTED_ORIGIN = re.compile(r"[A-Za-z0-9.-]+/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+")
_CLONE_SCHEME = re.compile(r"^https?://")

#: The reserved metadata key a record's cross-DAG dependencies travel on. A store's own
#: dependency edges can only name records of the same project, so a wait on another
#: run's node is written here, which is where the engine's loader reads it from.
CROSS_DAG_DEPS = f"{ENGINE_PREFIX}deps"

#: The one spelling of a wait-only dependency on another run's node, as the node-shapes
#: section of `docs/orchestration.md` states it. That section is the shape's one source
#: and this is the one place this repository parses it — every reader of a stored
#: reference takes the list already partitioned — so the two are held together by
#: `tests/test_record_shapes.py`, which reads the spelling back out of that section and
#: shows this reader accepting exactly the form it states.
CROSS_DAG_REFERENCE_SHAPE = "run:<run_id>#<node_id>"
#: Each half is anything but whitespace and the separator, and both are non-empty: a
#: reference is promoted verbatim into a record other programs read, so an empty run or
#: node, a space, or a second `#` is refused where it is written rather than by whichever
#: loader cannot resolve it.
_CROSS_DAG_REFERENCE = re.compile(r"^run:([^\s#]+)#([^\s#]+)$")


class QualifiedId(NamedTuple):
    """One qualified `<source>:<native>` id, as its two halves.

    Named rather than positional because both halves are read by name at every call site —
    which source answers for the id, and what that source calls the record — and a bare
    pair leaves each caller re-deciding which index is which.
    """

    source: str
    native: str


def qualified_id(value: str) -> QualifiedId | None:
    """``value`` split into the source and native halves of a qualified id, or `None`.

    The one parser for that shape in this repository, and the reason it sits in this module
    rather than beside its first caller: `plan_store.qualified` refuses a project id that is
    not one, and the writers of a `delivers` entry refuse a ticket id that is not one, and
    this is the module they share. A second encoding of the same shape is a contract that
    drifts.

    A `delivers` entry is promoted into a record another program then resolves, so an
    unqualified or empty one is refused where it is written rather than by whichever later
    reader cannot find what it names.
    """
    source, separator, native = value.partition(":")
    return QualifiedId(source, native) if separator and source and native else None


def hosted_origin(repo: str) -> str | None:
    """``repo`` as the normalized origin `repositories` can hold, or ``None``.

    ``None`` for every other spelling of a repository — a registered alias, an absolute
    path, an scp-like clone URL — because none of them is a value that list holds: the
    engine reads a node's ``repo`` from that list's first entry and reserves the
    ``onepipeline.repo`` key for an identity a normalized origin cannot name, which is a
    local checkout `onevcs` knows by its path. This is the one place that shape is
    decided, and :mod:`orchestrator.publication_guard` asks it of what `onevcs` answers
    so the record a plan writes and the record `just check-plan` refuses agree.
    """
    normalized = _CLONE_SCHEME.sub("", repo).removesuffix(".git")
    return normalized if _HOSTED_ORIGIN.fullmatch(normalized) else None


def cross_dag_reference(dependency: str) -> str | None:
    """``dependency`` when it spells a well-formed cross-DAG reference, else ``None``.

    Well-formed is the documented shape and nothing looser: `run:` then a non-empty run
    id, one `#`, then a non-empty node id, with no whitespace in either half.
    """
    return dependency if _CROSS_DAG_REFERENCE.fullmatch(dependency) else None


def frontmatter(fields: Mapping[str, object], body: str) -> str:
    """One local Markdown record: its frontmatter fields, then its body.

    The follow-up drafts and tickets are the records still written this way; a plan's are
    the store's own. A mapping-valued field — a record's `metadata` — is written as a block
    of one JSON key and one JSON value per line.
    """
    lines = ["---"]
    for key, value in fields.items():
        if isinstance(value, dict):
            lines.append(f"{key}:")
            lines.extend(
                f"  {json.dumps(name)}: {json.dumps(held)}" for name, held in value.items()
            )
        else:
            lines.append(f"{key}: {json.dumps(value)}")
    return "\n".join([*lines, "---", "", body.rstrip(), ""])
