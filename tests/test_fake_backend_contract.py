"""The drift gate over the e2e protocol double's contract with production code.

onejudge spawns `tests/e2e/fake_backend.py` once per protocol step, so the double
imports only the standard library and restates the few orchestrator constants it
has to speak. That restatement is only safe with a gate: these tests fail when
either side of it moves.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from types import ModuleType

from conftest import FAKE_BACKEND

from orchestrator.dispatch import REPORTED_BLOCKER_PREFIX
from orchestrator.scratch import CAPACITY_ERROR_MARKER, DEFAULT_MIN_FREE_BYTES


def _load_double() -> ModuleType:
    """Import the double the way its own subprocess does: by path, alone."""
    spec = importlib.util.spec_from_file_location("fake_backend_under_test", FAKE_BACKEND)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_double_restates_the_orchestrator_constants_it_speaks() -> None:
    double = _load_double()
    assert double.REPORTED_BLOCKER_PREFIX == REPORTED_BLOCKER_PREFIX
    assert double.CAPACITY_ERROR_MARKER == CAPACITY_ERROR_MARKER
    assert double.DEFAULT_MIN_FREE_BYTES == DEFAULT_MIN_FREE_BYTES


def test_double_imports_only_the_standard_library() -> None:
    tree = ast.parse(FAKE_BACKEND.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "a script spawned by path cannot resolve a relative import"
            assert node.module is not None
            roots.add(node.module.split(".")[0])
    assert roots, "the double should still import the standard library it uses"
    outside = sorted(roots - set(sys.stdlib_module_names))
    assert not outside, (
        f"{FAKE_BACKEND.name} is spawned once per protocol step; importing {outside} "
        "charges that cost to every step of every real-onejudge journey"
    )
