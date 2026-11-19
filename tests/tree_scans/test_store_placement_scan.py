"""Nothing in `orchestrator/` works out where the plan store places an item, or binds a ticket.

Where an item lands, which item is its own there, and which repository its issue is created in
are the plan store's answers — its `task locate`, through the very path its
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
"""

from __future__ import annotations

import ast
import re

import pytest

from orchestrator.root import REPO_ROOT

#: Every module of the package is this scan's subject, so it runs where prose-reading scans do.
pytestmark = pytest.mark.reads_docs

PACKAGE = REPO_ROOT / "orchestrator"
#: The retired binding's key.
BINDING = "board_item"
#: A setting path a placement is derived from: a source's routes, or its configured repository.
PLACEMENT_SETTING = re.compile(r"sources\.[^.\s]+\.(?:routes\b|config\.repository\b)")
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


def findings(source: str) -> list[str]:
    """Each place ``source`` derives a placement or writes the binding."""
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
    return sorted(set(found))


def test_no_module_derives_a_placement_or_writes_the_binding() -> None:
    found = {
        str(path.relative_to(REPO_ROOT)): problems
        for path in sorted(PACKAGE.rglob("*.py"))
        if (problems := findings(path.read_text(encoding="utf-8")))
    }

    assert found == {}, found


@pytest.mark.parametrize(
    ("source", "said"),
    [
        (
            "client.sources_route(board, repository=list(repositories))",
            "asked to route an item by `repository`",
        ),
        (
            'client.sources_route(board, classification="private")',
            "asked to route an item by `classification`",
        ),
        ('settings.get(f"sources.{board}.routes")', "placement setting"),
        ('settings.get("sources.followups.routes.0.to")', "placement setting"),
        ('settings.get(f"sources.{routed}.config.repository")', "placement setting"),
        ('BINDING_FIELD = "board_item"\nheld[BINDING_FIELD] = item', "subscript writes"),
        ('held["board_item"] = item', "subscript writes"),
        ('record = {"schema": 13, "board_item": item}', "dict display writes"),
        ('held.setdefault("board_item", item)', "`setdefault` writes"),
        ("dataclasses.replace(ticket, board_item=item)", "`board_item=` carries a value"),
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
    ],
)
def test_each_shape_the_scan_refuses_is_refused(source: str, said: str) -> None:
    found = findings(source)

    assert any(said in one for one in found), found


def test_what_places_nothing_and_writes_no_binding_passes() -> None:
    """The family's own answer, a binding read or cleared, a rule stated, and a setting that
    places nothing, are none of them refused."""
    source = '''
"""Never read `sources.<name>.routes`: ask `sources route` for `reachable`."""
BINDING_FIELD = "board_item"
reachable = client.sources_route(board).reachable
bound = held.get(BINDING_FIELD)
cleared = dataclasses.replace(ticket, board_item=None)
private = settings.get(f"sources.{source}.visibility") == "private"
mapping = f"sources.{board}.config.status_mapping."
plugin = settings.get(f"sources.{source}.plugin")
cached = settings.get(f"sources.{destination}.cache.enabled")
stood_up = f"sources.{SNAPSHOT_SOURCE}.config.root={root}"
'''

    assert findings(source) == []


def test_the_scan_reads_every_module_of_the_package() -> None:
    """A top-level module and a nested one are both modules the scan walks."""
    walked = set(PACKAGE.rglob("*.py"))
    assert {PACKAGE / "record_shapes.py", PACKAGE / "budgets" / "design_doc_dag.py"} <= walked
