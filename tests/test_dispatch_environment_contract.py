"""The environment this suite drops is the environment the scripts read.

Every worker verifies itself by running this suite from inside a dispatch, so the
suite inherits that dispatch's per-side harness choice, its ownership stamp, and
its comparison base — and the autouse fixtures in `tests/conftest.py` drop all
three, because a test's environment is the test's to state.

Those names used to be imported from the modules that declared them. The modules
are gone, so the fixtures hold a copy, and a copy of a contract is only sound while
something reconciles it: rename a per-side variable in the wrapper and the fixture
silently stops dropping it, the suite reads the enclosing dispatch's choice again,
and nothing turns red. This is that reconciliation.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest
from conftest import AGENT_STATUS_DIR_ENV, COMPARISON_ENV_PREFIXES, DISPATCH_SELECTION_ENV

from orchestrator.root import REPO_ROOT

#: The wrapper that resolves a per-side choice into the process-wide variables
#: oneharness itself reads. It is the declaring side of that contract.
AGENT_WRAPPER = "scripts/oneharness-agent.sh"
#: Where the gate-comparison identity is read: the resolver every gate calls, and
#: the hook git hands the whole environment to. Together they are the declaring side
#: of which spellings are live, so the fixture is reconciled against them rather than
#: against a second list somebody has to remember to extend.
COMPARISON_READERS = ("scripts/comparison-base.sh", ".githooks/pre-push")
#: What a per-side selection is spelled with. Narrow on purpose: the wrapper also
#: names configuration directories and the status stamp, which are not selections.
SELECTION = re.compile(
    r"ORCHESTRATOR_(?:WORKER|JUDGE)_(?:HARNESSES|MODEL)|ONEHARNESS_(?:HARNESSES|MODEL)"
)
#: A comparison-identity variable, in whatever namespace exports it. Matched on the
#: two field names rather than on a known prefix, so a third publishing spelling is
#: discovered here instead of being the thing this gate was blind to.
COMPARISON = re.compile(r"\b([A-Z][A-Z0-9]*_COMPARISON_)(?:BASE|REMOTE)\b")


def _wrapper() -> str:
    return (REPO_ROOT / AGENT_WRAPPER).read_text(encoding="utf-8")


def test_the_suite_drops_every_per_side_selection_the_wrapper_resolves() -> None:
    """Both halves: the fixture may not miss one, and may not invent one either."""
    declared = set(SELECTION.findall(_wrapper()))

    assert declared, f"{AGENT_WRAPPER} no longer names a per-side selection at all"
    assert set(DISPATCH_SELECTION_ENV) == declared, (
        "tests/conftest.py's DISPATCH_SELECTION_ENV has drifted from the variables "
        f"{AGENT_WRAPPER} resolves: {sorted(set(DISPATCH_SELECTION_ENV) ^ declared)}"
    )


def test_the_ownership_stamp_the_suite_drops_is_the_one_the_wrapper_branches_on() -> None:
    """With a stamp exported the wrapper streams; without one it takes `--events`."""
    assert AGENT_STATUS_DIR_ENV in _wrapper(), (
        f"{AGENT_WRAPPER} no longer branches on {AGENT_STATUS_DIR_ENV}, so dropping it "
        "in tests/conftest.py isolates nothing"
    )


@pytest.mark.parametrize("reader", COMPARISON_READERS)
def test_the_comparison_identity_the_suite_drops_is_the_one_the_gate_reads(reader: str) -> None:
    """Every spelling a gate reader honours is one the fixture drops, and vice versa.

    A test push that names no base must not arrive carrying the outer branch's, and
    that holds only while the two sets are the same one. The half that used to be
    missing is the direction that fails silently: `onevcs` began exporting a second
    prefix, both readers honour it, and the fixture kept dropping only the first.
    """
    text = (REPO_ROOT / reader).read_text(encoding="utf-8")
    read = set(COMPARISON.findall(text))

    assert read, (
        f"{reader} no longer reads a *_COMPARISON_BASE/REMOTE variable, so the "
        "isolation fixture in tests/conftest.py is dropping something nothing reads"
    )
    assert read <= set(COMPARISON_ENV_PREFIXES), (
        f"{reader} honours a comparison identity tests/conftest.py leaves inherited: "
        f"{sorted(read - set(COMPARISON_ENV_PREFIXES))}"
    )


def _prefixes_the_gate_reads() -> set[str]:
    """Every comparison spelling the two gate readers honour — the declaring side."""
    return {
        prefix
        for reader in COMPARISON_READERS
        for prefix in COMPARISON.findall((REPO_ROOT / reader).read_text(encoding="utf-8"))
    }


def test_the_suite_drops_no_comparison_spelling_no_gate_reads() -> None:
    """The other direction: a prefix nothing reads is isolation theatre, so say so."""
    read = _prefixes_the_gate_reads()

    assert set(COMPARISON_ENV_PREFIXES) == read, (
        "tests/conftest.py's COMPARISON_ENV_PREFIXES has drifted from what "
        f"{', '.join(COMPARISON_READERS)} read: {sorted(set(COMPARISON_ENV_PREFIXES) ^ read)}"
    )


def test_this_process_carries_no_inherited_comparison_identity() -> None:
    """The autouse fixture's own product, measured against what the gate readers honour.

    Grounded on the readers rather than on the fixture's own list, because this is
    what the child below runs: a probe that asked the fixture which names it drops
    would agree with it no matter which ones it forgot.
    """
    inherited = sorted(
        key for key in os.environ if key.startswith(tuple(_prefixes_the_gate_reads()))
    )

    assert inherited == [], (
        f"the suite is running with an inherited comparison identity: {inherited}"
    )


@pytest.mark.parametrize("exported", sorted(f"{p}BASE" for p in _prefixes_the_gate_reads()))
def test_the_fixture_drops_the_identity_a_dispatch_really_exports(exported: str) -> None:
    """Run the suite the way a lifecycle dispatch does: polluted, and prove it comes up clean.

    Asserting on `os.environ` in this process only shows the fixture had nothing to
    do. So the check above is re-run in a child that inherits exactly what the
    publication path exports, through the real conftest, which is the only place the
    removal can be observed happening.
    """
    probe = f"{__file__}::test_this_process_carries_no_inherited_comparison_identity"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", probe, "-p", "no:cacheprovider", "-p", "no:xdist"],
        cwd=REPO_ROOT,
        env={**os.environ, exported: "some-outer-branch"},
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, (
        f"a suite inheriting {exported} did not drop it: {result.stdout}{result.stderr}"
    )
