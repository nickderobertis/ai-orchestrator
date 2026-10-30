"""Nothing in `orchestrator/` works out where the plan store places an item, or binds a ticket.

Where a follow-up ticket lands, which item is its own there, and which repository its issue
is created in are the plan store's answers — its `task locate`, through the very path its
copy writes by — and the sources a root board can route to are its `sources route`'s
`reachable`. Every copy of those rules kept here is one more thing to keep in step with each
store release, and a host that read an owner's private ticket as public looked for it on the
public board and filed a duplicate. So this scan refuses, anywhere in the package:

* a call of `sources_route` passing repositories or a classification — asking the store to
  route a set of repositories is deriving a placement the store's own `task locate` answers;
* a read of a source's `routes`, or of its configured `repository`, off the store's
  configuration — the two settings a placement is derived from;
* a write of `board_item`, the retired binding — a subscript or dict-display key, a
  `setdefault`, or a `board_item=` keyword carrying anything but `None`. A record from before
  the binding retired keeps the key only as it was read, carried through a rewrite as one
  mapping nothing composes.

And in the follow-up modules every surviving read of the store's configuration is one that
decides nothing about where a ticket's item is placed, searched or copied: the destination's
`config.owner`, which the owner refusal compares, a source's `visibility`, a board's
`config.status_mapping` and `plugin`, which the comment snapshot reads, and a source's
`cache.enabled`, which decides whether a comment edit expects a version.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from orchestrator.root import REPO_ROOT

#: Every module of the package is this scan's subject, so it runs where prose-reading scans do.
pytestmark = pytest.mark.reads_docs

PACKAGE = REPO_ROOT / "orchestrator"
#: The modules whose every configuration read is held to :data:`ALLOWED_FOLLOW_UP_SETTING`.
FOLLOW_UP_MODULES = frozenset({"follow_up_tickets.py", "follow_up_comments.py"})
#: The retired binding's key.
BINDING = "board_item"
#: A setting path a placement is derived from: a source's routes, or its configured repository.
PLACEMENT_SETTING = re.compile(r"sources\.[^.\s]+\.(?:routes\b|config\.repository\b)")
#: Any setting path of a source, as the store's resolution of its configuration spells it.
SOURCE_SETTING = re.compile(r"sources\.[^.\s]+\.(?P<key>[\w.]*)")
#: The setting paths a follow-up module may read, none of which places, searches or copies.
ALLOWED_FOLLOW_UP_SETTING = re.compile(
    r"(?:config\.owner|config\.status_mapping\.?|plugin|visibility|cache\.enabled)"
)
#: The keyword arguments of `sources_route` that ask it to route a particular item.
ROUTING_ARGUMENTS = frozenset({"repository", "classification"})


def _text(node: ast.AST) -> str | None:
    """A string literal's text, an f-string's with `{}` for each value it interpolates."""
    match node:
        case ast.Constant(value=str(text)):
            return text
        case ast.JoinedStr(values=parts):
            return "".join(part.value if isinstance(part, ast.Constant) else "{}" for part in parts)
        case _:
            return None


def _called(call: ast.Call) -> str | None:
    """The name a call is made through: a bare function's, or a method's attribute."""
    match call.func:
        case ast.Attribute(attr=name) | ast.Name(id=name):
            return name
        case _:
            return None


def _docstrings(tree: ast.Module) -> set[int]:
    """The ids of every docstring and bare string statement, which state rules rather than
    read settings."""
    return {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, (ast.Constant, ast.JoinedStr))
    }


def _binding_names(tree: ast.Module) -> set[str]:
    """Every module-level name bound to the binding's key, as `BINDING_FIELD` is."""
    return {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign) and _text(node.value) == BINDING
        for target in node.targets
        if isinstance(target, ast.Name)
    }


def _names_binding(node: ast.AST, names: set[str]) -> bool:
    return _text(node) == BINDING or (isinstance(node, ast.Name) and node.id in names)


def findings(source: str, *, follow_up: bool = False) -> list[str]:
    """Each place ``source`` derives a placement, writes the binding, or — in a follow-up
    module, with ``follow_up`` — reads a configuration setting it may not."""
    tree = ast.parse(source)
    skipped = _docstrings(tree)
    names = _binding_names(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Call) and _called(node) == "sources_route":
            found.extend(
                f"line {line}: `sources_route` is asked to route an item by `{keyword.arg}`"
                for keyword in node.keywords
                if keyword.arg in ROUTING_ARGUMENTS
            )
        if isinstance(node, ast.Call):
            found.extend(
                f"line {line}: `{keyword.arg}=` carries a value"
                for keyword in node.keywords
                if keyword.arg == BINDING
                and not (isinstance(keyword.value, ast.Constant) and keyword.value.value is None)
            )
            if (
                _called(node) in ("setdefault", "__setitem__")
                and node.args
                and _names_binding(node.args[0], names)
            ):
                found.append(f"line {line}: `{_called(node)}` writes `{BINDING}`")
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.ctx, ast.Store)
            and _names_binding(node.slice, names)
        ):
            found.append(f"line {line}: a subscript writes `{BINDING}`")
        if isinstance(node, ast.Dict):
            found.extend(
                f"line {line}: a dict display writes `{BINDING}`"
                for key in node.keys
                if key is not None and _names_binding(key, names)
            )
        text = _text(node)
        if text is None or id(node) in skipped:
            continue
        if PLACEMENT_SETTING.search(text):
            found.append(f"line {line}: reads the placement setting {text!r}")
        elif follow_up:
            # A `--set` assignment states a setting of a source this module itself stands up,
            # such as the comment snapshot's folder, and reads nothing of the store's.
            found.extend(
                f"line {line}: reads the setting {text!r}, which no follow-up module may"
                for matched in SOURCE_SETTING.finditer(text)
                if not ALLOWED_FOLLOW_UP_SETTING.fullmatch(matched["key"])
                and not text[matched.end() :].startswith("=")
            )
    return sorted(set(found))


def test_no_module_derives_a_placement_or_writes_the_binding() -> None:
    found = {
        str(path.relative_to(REPO_ROOT)): problems
        for path in sorted(PACKAGE.rglob("*.py"))
        if (
            problems := findings(
                path.read_text(encoding="utf-8"), follow_up=path.name in FOLLOW_UP_MODULES
            )
        )
    }

    assert found == {}, found


@pytest.mark.parametrize(
    ("source", "follow_up", "said"),
    [
        (
            "client.sources_route(board, repository=list(repositories))",
            False,
            "asked to route an item by `repository`",
        ),
        (
            'client.sources_route(board, classification="private")',
            False,
            "asked to route an item by `classification`",
        ),
        ('settings.get(f"sources.{board}.routes")', False, "placement setting"),
        ('settings.get("sources.followups.routes.0.to")', False, "placement setting"),
        ('settings.get(f"sources.{routed}.config.repository")', False, "placement setting"),
        ('BINDING_FIELD = "board_item"\nheld[BINDING_FIELD] = item', False, "subscript writes"),
        ('held["board_item"] = item', False, "subscript writes"),
        ('record = {"schema": 13, "board_item": item}', False, "dict display writes"),
        ('held.setdefault("board_item", item)', False, "`setdefault` writes"),
        ("dataclasses.replace(ticket, board_item=item)", False, "`board_item=` carries a value"),
        (
            'settings.get(f"sources.{board}.config.project_number")',
            True,
            "which no follow-up module may",
        ),
    ],
    ids=[
        "route-by-repositories",
        "route-by-classification",
        "routes-read",
        "a-route-entry-read",
        "configured-repository-read",
        "binding-written-by-name",
        "binding-written-by-key",
        "binding-in-a-record",
        "binding-set-by-default",
        "binding-carried-into-a-ticket",
        "follow-up-reads-another-setting",
    ],
)
def test_each_shape_the_scan_refuses_is_refused(source: str, follow_up: bool, said: str) -> None:
    found = findings(source, follow_up=follow_up)

    assert any(said in one for one in found), found


def test_what_places_nothing_and_writes_no_binding_passes() -> None:
    """The family's own answer, a binding read or cleared, a rule stated, and every setting a
    follow-up module keeps, are none of them refused."""
    source = '''
"""Never read `sources.<name>.routes`: ask `sources route` for `reachable`."""
BINDING_FIELD = "board_item"
reachable = client.sources_route(board).reachable
bound = held.get(BINDING_FIELD)
cleared = dataclasses.replace(ticket, board_item=None)
owner = settings.get(f"sources.{board}.config.owner")
private = settings.get(f"sources.{source}.visibility") == "private"
mapping = f"sources.{board}.config.status_mapping."
plugin = settings.get(f"sources.{source}.plugin")
cached = settings.get(f"sources.{destination}.cache.enabled")
stood_up = f"sources.{SNAPSHOT_SOURCE}.config.root={root}"
'''

    assert findings(source, follow_up=True) == []


def test_the_scan_reads_every_module_of_the_package() -> None:
    """The follow-up modules this scan holds closer are modules of the package it walks."""
    walked = {path.name for path in PACKAGE.rglob("*.py")}

    assert walked >= FOLLOW_UP_MODULES, sorted(FOLLOW_UP_MODULES - walked)
    assert Path(PACKAGE / "plan_store.py") in set(PACKAGE.rglob("*.py"))
