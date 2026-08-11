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

import re

import pytest
from conftest import AGENT_STATUS_DIR_ENV, COMPARISON_ENV_PREFIX, DISPATCH_SELECTION_ENV

from orchestrator import REPO_ROOT

#: The wrapper that resolves a per-side choice into the process-wide variables
#: oneharness itself reads. It is the declaring side of that contract.
AGENT_WRAPPER = "scripts/oneharness-agent.sh"
#: Where the gate-comparison identity is read: the resolver every gate calls, and
#: the hook git hands the whole environment to.
COMPARISON_READERS = ("scripts/comparison-base.sh", ".githooks/pre-push")
#: What a per-side selection is spelled with. Narrow on purpose: the wrapper also
#: names configuration directories and the status stamp, which are not selections.
SELECTION = re.compile(
    r"ORCHESTRATOR_(?:WORKER|JUDGE)_(?:HARNESSES|MODEL)|ONEHARNESS_(?:HARNESSES|MODEL)"
)


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
    """A test push that names no base must not arrive carrying the outer branch's."""
    text = (REPO_ROOT / reader).read_text(encoding="utf-8")

    assert COMPARISON_ENV_PREFIX in text, (
        f"{reader} no longer reads a {COMPARISON_ENV_PREFIX}* variable, so the "
        "isolation fixture in tests/conftest.py is dropping something nothing reads"
    )
