"""Every SDK this repository imports is typed in the installed environment.

`[tool.mypy]` runs strict over `orchestrator/`, which is only a claim about the adapter
code while the packages it adapts are typed: a package installed without `py.typed` and
without a stub package is read as `Any` at the import, and every call through it is
checked against nothing. That is how the plan-store adapter was first adopted — behind
an `import-untyped` ignore, with the SDK's models and signatures invisible to the checker
until the marker shipped upstream and the ignore came off. This module is what keeps
that from happening quietly again: the next SDK adopted without its types fails here by
name rather than arriving under an ignore nobody reads twice.

PEP 561 gives a package two ways to be typed, and mypy reads both: the package's own
annotations, announced by a `py.typed` marker in its directory, or a **stub-only
distribution** installing a `<module>-stubs` package beside it, which mypy prefers to
the package itself. An SDK that ships its own types is held to the marker; one typed
through stubs — `pyyaml`, through `types-pyyaml` — is held to the stub package being
installed, because that package is what the checker reads and a lock entry for it is
not.

Two properties, and where each is read from. Whether a package is typed is read off the
**installed** distribution, through `importlib`, because that is the package mypy reads
— `uv.lock` says what a release would install and the tree says nothing. Which packages
count is derived from the tree's own imports: every top-level module `orchestrator/` and
`tests/` import that `importlib.metadata` attributes to a distribution `pyproject.toml`
pins directly. `TYPED_SDKS` restates that set so a reader sees it without running the
derivation, and the two are reconciled against each other here, so an SDK imported
without being listed fails the gate rather than escaping it.
"""

from __future__ import annotations

import ast
import importlib.metadata
import importlib.resources
import re
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

import pytest

from orchestrator.root import REPO_ROOT

#: The importable SDK modules this repository adapts, each from a distribution
#: `pyproject.toml` pins. Reconciled against what the tree imports below, so this is a
#: statement to read rather than a list to maintain by hand.
TYPED_SDKS = frozenset({"onejudge_sdk", "onetaskgraph_sdk", "yaml"})

#: The trees whose imports decide which distributions count as SDKs here.
IMPORTING_TREES = ("orchestrator", "tests")

#: The one tree held free of import ignores: the checked package.
CHECKED_PACKAGE = "orchestrator"

#: PEP 561's marker: its presence in the package directory is what tells mypy the
#: package's own annotations are to be read.
MARKER = "py.typed"

#: PEP 561's other mechanism: a stub-only distribution installs its stubs under the
#: module's name with this suffix, and mypy reads that package before the module's own.
STUB_SUFFIX = "-stubs"

#: A `# type: ignore` directive, with the error codes it names when it names any. mypy
#: reads the directive off the physical line, so this is matched per line rather than
#: per statement.
IGNORE_DIRECTIVE = re.compile(r"#\s*type:\s*ignore(?:\[(?P<codes>[^\]]*)\])?")

#: The code mypy raises at an import of a package that ships no marker. A directive
#: naming it, or naming no code at all, is what silences that finding.
UNTYPED_IMPORT_CODE = "import-untyped"


def _normalized(distribution: str) -> str:
    """A distribution name as PEP 503 compares it: case-folded, runs of `-_.` as one dash."""
    return re.sub(r"[-_.]+", "-", distribution).lower()


def pinned_distributions(pyproject: Path = REPO_ROOT / "pyproject.toml") -> frozenset[str]:
    """The distributions `[project].dependencies` pins, by normalized name."""
    with pyproject.open("rb") as handle:
        project = tomllib.load(handle)["project"]
    names: set[str] = set()
    for requirement in project["dependencies"]:
        match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
        assert match is not None, f"{pyproject} pins a requirement with no name: {requirement!r}"
        names.add(_normalized(match.group(1)))
    return frozenset(names)


def _python_files(trees: Iterable[Path]) -> Iterable[Path]:
    for tree in trees:
        yield from sorted(tree.rglob("*.py"))


def _imports(module: ast.Module) -> Iterable[ast.Import | ast.ImportFrom]:
    for node in ast.walk(module):
        if isinstance(node, ast.Import | ast.ImportFrom):
            yield node


def _top_level_modules(node: ast.Import | ast.ImportFrom) -> Iterable[str]:
    """The top-level module names one absolute import statement names."""
    match node:
        case ast.Import(names=aliases):
            for alias in aliases:
                yield alias.name.split(".", 1)[0]
        case ast.ImportFrom(level=0, module=str(module)):
            yield module.split(".", 1)[0]


def imported_sdks(trees: Iterable[Path], pinned: frozenset[str]) -> frozenset[str]:
    """The top-level modules ``trees`` import that a distribution in ``pinned`` provides.

    Read through `importlib.metadata.packages_distributions`, which answers off the
    installed environment: a module of a package nothing installed maps to no
    distribution and is not an SDK adopted here, whatever the tree says about it.
    """
    provided = importlib.metadata.packages_distributions()
    found: set[str] = set()
    for path in _python_files(trees):
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in _imports(module):
            for name in _top_level_modules(node):
                distributions = {_normalized(each) for each in provided.get(name, ())}
                if distributions & pinned:
                    found.add(name)
    return frozenset(found)


def stub_distributions(
    module: str, provided: Mapping[str, Sequence[str]] | None = None
) -> list[str]:
    """The installed distributions shipping a stub-only package for ``module``.

    Read through `importlib.metadata.packages_distributions`, the same map the SDK set
    is derived from, so the answer is the installed environment's: a stub distribution
    the lock names and nothing installed types nothing.
    """
    if provided is None:
        provided = importlib.metadata.packages_distributions()
    return sorted(provided.get(f"{module}{STUB_SUFFIX}", ()))


def untyped_import_ignores(tree: Path) -> list[str]:
    """Every `type: ignore` over an import under ``tree`` that would silence `import-untyped`.

    A directive naming the code and a directive naming none are both that: a bare
    ignore silences every code at its line. Each finding is rendered `path:line: text`
    so a failure points at the directive rather than at the module.
    """
    findings: list[str] = []
    for path in _python_files([tree]):
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        for node in _imports(ast.parse(source, filename=str(path))):
            for number in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                line = lines[number - 1]
                directive = IGNORE_DIRECTIVE.search(line)
                if directive is None:
                    continue
                codes = directive.group("codes")
                named = {code.strip() for code in codes.split(",")} if codes is not None else set()
                if codes is None or UNTYPED_IMPORT_CODE in named:
                    findings.append(f"{path.relative_to(tree.parent)}:{number}: {line.strip()}")
    return findings


def test_the_declared_sdks_are_the_ones_the_tree_imports() -> None:
    """`TYPED_SDKS` is held to the derivation, in both directions.

    A module imported from a pinned distribution and not listed is an SDK adopted
    without this gate reading it; one listed and no longer imported is a claim about a
    package the tree stopped adapting.
    """
    derived = imported_sdks((REPO_ROOT / tree for tree in IMPORTING_TREES), pinned_distributions())
    assert derived == TYPED_SDKS, (
        f"the tree imports {sorted(derived)} from distributions pyproject.toml pins, and "
        f"TYPED_SDKS declares {sorted(TYPED_SDKS)}; list every SDK the tree adapts here so "
        f"its installed package is held to shipping {MARKER}"
    )


@pytest.mark.parametrize("module", sorted(TYPED_SDKS))
def test_the_installed_sdk_is_typed(module: str) -> None:
    """The package mypy reads carries `py.typed`, or a stub package for it is installed.

    Both are read off the installed environment: the marker off the package's own
    directory, the stubs off the distribution map that attributes `<module>-stubs` to
    whatever installed it.
    """
    distributions = importlib.metadata.packages_distributions().get(module, [])
    package = importlib.resources.files(module)
    assert package.joinpath(MARKER).is_file() or stub_distributions(module), (
        f"the installed {module} package (from {', '.join(distributions) or 'no distribution'}) "
        f"ships no {MARKER} and no {module}{STUB_SUFFIX} package is installed, so mypy reads "
        f"every import of it as Any; adopt a release that ships the marker or a stub "
        f"distribution for it, and re-sync the environment from uv.lock before reading this "
        f"again"
    )


def test_the_stub_lookup_reads_the_stub_package_off_the_distribution_map() -> None:
    """A `<module>-stubs` entry is a stub distribution for the module; nothing else is.

    Driven over a scratch map beside the real one, so the lookup is held to the name
    PEP 561 gives a stub package rather than to what this environment happens to hold.
    """
    scratch = {"example": ["example"], "example-stubs": ["types-example"], "other": ["other"]}
    assert stub_distributions("example", scratch) == ["types-example"]
    assert stub_distributions("other", scratch) == []
    assert stub_distributions("missing", scratch) == []


def test_no_untyped_import_ignore_stands_over_an_import_of_the_checked_package() -> None:
    """No import under `orchestrator/` is silenced against `import-untyped`.

    The directive this repository once carried over its SDK import is the shape a
    package adopted without its marker arrives under; with the marker gate above, the
    only way such an import passes mypy is this directive, and it is refused by line.
    """
    assert untyped_import_ignores(REPO_ROOT / CHECKED_PACKAGE) == [], (
        f"an import under {CHECKED_PACKAGE}/ is silenced against {UNTYPED_IMPORT_CODE}; "
        f"adopt a release of that package that ships {MARKER} instead of ignoring the import"
    )


@pytest.mark.parametrize(
    ("line", "found"),
    [
        ("from example_sdk import Client  # type: ignore[import-untyped]", True),
        ("from example_sdk import Client  # type: ignore[import-untyped, attr-defined]", True),
        ("from example_sdk import Client  # type: ignore", True),
        ("from example_sdk import Client  # type: ignore[attr-defined]", False),
        ("from example_sdk import Client  # a comment that is not a directive", False),
    ],
)
def test_the_ignore_scan_reads_the_directive_off_the_import_line(
    tmp_path: Path, line: str, found: bool
) -> None:
    """The scan refuses exactly the directives that silence the untyped-import finding.

    Driven over a scratch package rather than the checked one, because the checked one
    is held clean above and a scan that could never find anything would hold it to
    nothing.
    """
    package = tmp_path / "scratch_package"
    package.mkdir()
    (package / "adapter.py").write_text(f"{line}\n\nCLIENT = Client\n", encoding="utf-8")
    findings = untyped_import_ignores(package)
    assert (findings != []) is found, findings
    if found:
        assert findings == [f"scratch_package/adapter.py:1: {line}"]


def test_the_sdk_set_is_derived_from_imports_of_pinned_distributions(tmp_path: Path) -> None:
    """An import of a pinned distribution's module is an SDK here; anything else is not.

    Driven over a scratch tree naming a real installed module beside a stdlib one and a
    local one, so the derivation is held to what `packages_distributions` and the pin
    list answer rather than to a list of names.
    """
    tree = tmp_path / "scratch_tree"
    tree.mkdir()
    (tree / "uses.py").write_text(
        "import json\nimport pydantic\nfrom onetaskgraph_sdk import Client\n"
        "from . import sibling\nfrom orchestrator import root\n",
        encoding="utf-8",
    )
    assert imported_sdks([tree], frozenset({"onetaskgraph-sdk"})) == {"onetaskgraph_sdk"}
    # `pydantic` is installed and imported, and is an SDK here only once it is pinned.
    assert imported_sdks([tree], frozenset({"onetaskgraph-sdk", "pydantic"})) == {
        "onetaskgraph_sdk",
        "pydantic",
    }
    assert imported_sdks([tree], frozenset()) == frozenset()
