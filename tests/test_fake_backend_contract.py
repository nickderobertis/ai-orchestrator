"""The drift gate over the e2e protocol double's contract with production code.

onejudge spawns `tests/e2e/fake_backend.py` once per protocol step, so the double
imports only the standard library and restates the few orchestrator constants it
has to speak. That restatement is only safe with a gate: these tests fail when
either side of it moves.
"""

from __future__ import annotations

import ast
import importlib.util
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import ModuleType

import pytest
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


def test_the_double_holds_at_the_rendezvous_the_suite_renders(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The one rendezvous convention has two sides, and only one of them is here.

    `tests/e2e/rendezvous.py` renders the sentinels and the double parses them; the
    double cannot import it, because a spawned protocol step pays for every import.
    A silent rename on either side would leave every journey that holds a node
    racing the assertion it exists to make observable, so the shapes are matched
    against each other here.
    """
    monkeypatch.syspath_prepend(str(FAKE_BACKEND.parent))
    from rendezvous import Rendezvous

    double = _load_double()
    held = Rendezvous.at(tmp_path, "contract")
    task = f"complete-now{held.sentinels(2)}"

    assert double._hold_until_released(task, 0) is False, "an unnamed turn must not hold"
    assert not held.arrived()

    releases: list[bool] = []
    holding = threading.Thread(target=lambda: releases.append(double._hold_until_released(task, 2)))
    holding.start()
    try:
        held.wait(5)
        assert holding.is_alive(), "the named turn returned before the test released it"
    finally:
        held.let_go()
        holding.join(timeout=30)
    assert releases == [True]


#: A child that writes the double's agent-authored change over and over, announcing
#: it has entered the loop so the test's kill lands inside the write rather than
#: inside interpreter startup. Loaded by path, exactly as onejudge spawns the double.
_REWRITE_LOOP = """
import importlib.util, sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("double", sys.argv[1])
double = importlib.util.module_from_spec(spec)
spec.loader.exec_module(double)

target, ready, payload = Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
ready.write_text("ready\\n", encoding="utf-8")
while True:
    double._write_agent_change(target, payload)
"""


def test_a_killed_agent_write_leaves_whole_content_rather_than_an_empty_file(
    tmp_path: Path,
) -> None:
    """A drop that lands mid-write must not destroy what the agent already wrote.

    A cooperative cancellation tears the dispatch tree down while a turn is running,
    and the lifecycle then commits whatever the worktree holds. When the double
    rewrote its change with `write_text`, the file was empty between the truncate and
    the write, so a kill inside that window preserved and published an *empty*
    CHANGE.txt through a `test -f` gate — the whole journey green except for the one
    assertion on content. Reproducing that needs a real kill at an uncontrolled
    moment, so this drives one: every kill point must leave one whole write, and
    against the truncating implementation the empty window is wide enough that these
    rounds catch it.
    """
    seeded = "seeded content\n"
    payload = "change from fake agent\n"
    target = tmp_path / "CHANGE.txt"
    observed: set[str] = set()
    for round_index in range(12):
        target.write_text(seeded, encoding="utf-8")
        ready = tmp_path / f"ready-{round_index}"
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                _REWRITE_LOOP,
                str(FAKE_BACKEND),
                str(target),
                str(ready),
                payload,
            ]
        )
        try:
            guard = time.monotonic() + 30
            while not ready.is_file():
                assert time.monotonic() < guard, "the rewrite loop never started"
                assert child.poll() is None, "the rewrite loop died before it wrote anything"
                time.sleep(0.01)
            time.sleep(0.01 * (round_index + 1))
            child.send_signal(signal.SIGKILL)
        finally:
            child.wait(timeout=30)
        content = target.read_text(encoding="utf-8")
        assert content in {seeded, payload}, (
            f"round {round_index} was killed mid-write and left a torn file: {content!r}"
        )
        observed.add(content)
    assert payload in observed, "no round was killed after a write landed; the kill window missed"
