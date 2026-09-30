"""The comparison identity this suite drops is the one the gate readers read.

Every worker verifies itself by running this suite from inside a dispatch, so the
suite inherits that dispatch's comparison base — and an autouse fixture in
`tests/conftest.py` drops it, because a test's environment is the test's to state.

The fixture holds a copy of which spellings are live, and a copy of a contract is only
sound while something reconciles it: rename a prefix in a reader and the fixture
silently stops dropping it, the suite reads the enclosing dispatch's base again, and
nothing turns red. This is that reconciliation.

The dispatch's oneharness history settings are the second thing the suite drops, and
reconciled the same way: against every history variable the pinned CLI documents.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest
from conftest import COMPARISON_ENV_PREFIXES, DISPATCH_HISTORY_ENV_PREFIX
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: Where the gate-comparison identity is read: the resolver every gate calls, and
#: the hook git hands the whole environment to. Together they are the declaring side
#: of which spellings are live, so the fixture is reconciled against them rather than
#: against a second list somebody has to remember to extend.
COMPARISON_READERS = ("scripts/comparison-base.sh", ".githooks/pre-push")
#: A comparison-identity variable, in whatever namespace exports it. Matched on the
#: two field names rather than on a known prefix, so a third publishing spelling is
#: discovered here instead of being the thing this gate was blind to.
COMPARISON = re.compile(r"\b([A-Z][A-Z0-9]*_COMPARISON_)(?:BASE|REMOTE)\b")


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


#: One history option of `oneharness run --help`: its flag, then its description up to
#: the next option.
HISTORY_OPTION = re.compile(r"^\s+--(?:no-)?history\S*.*?(?=^\s+-|\Z)", re.MULTILINE | re.DOTALL)
#: A variable a oneharness option says it is also settable through.
ONEHARNESS_VARIABLE = re.compile(r"\bONEHARNESS_[A-Z0-9_]+\b")

#: The history settings the engine exports to a dispatch, as a suite run inside one
#: inherits them: recording on, into the dispatch's own directory, under its labels,
#: indexed in its run's pointer file.
DISPATCH_HISTORY = {
    "ONEHARNESS_HISTORY": "1",
    "ONEHARNESS_HISTORY_DIR": "/an/enclosing/dispatch/history",
    "ONEHARNESS_HISTORY_LABELS": "onepipeline.run_id=an-enclosing-run",
    "ONEHARNESS_HISTORY_POINTER_FILE": "/an/enclosing/run/oneharness-sessions.jsonl",
}


def test_every_history_setting_the_pinned_cli_honours_is_one_the_suite_drops(
    oneharness_bin: str,
) -> None:
    """The prefix the fixture drops covers every history variable the pinned CLI reads.

    Read off the CLI's own help, because a history setting renamed outside the prefix is
    one the suite would inherit again with nothing turning red — and each inherited
    turn queues on a host-wide history index rather than failing.
    """
    documented = subprocess.run(  # noqa: S603 - the pinned CLI, asked what it documents
        [oneharness_bin, "run", "--help"],
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert documented.returncode == 0, documented.stderr
    honoured = {
        variable
        for option in HISTORY_OPTION.findall(documented.stdout)
        for variable in ONEHARNESS_VARIABLE.findall(option)
    }

    assert honoured, "the pinned oneharness documents no history variable any more"
    outside = sorted(v for v in honoured if not v.startswith(DISPATCH_HISTORY_ENV_PREFIX))
    assert outside == [], f"history variables the suite would still inherit: {outside}"
    assert honoured == set(DISPATCH_HISTORY), (
        "the polluted run below no longer exports every history variable the CLI honours: "
        f"{sorted(honoured ^ set(DISPATCH_HISTORY))}"
    )


def test_this_process_carries_no_inherited_history_setting() -> None:
    """The fixture's product: no history setting reaches a turn this suite spends."""
    inherited = sorted(key for key in os.environ if key.startswith(DISPATCH_HISTORY_ENV_PREFIX))

    assert inherited == [], f"the suite is running with inherited history settings: {inherited}"


def test_the_fixture_drops_the_history_a_dispatch_really_exports() -> None:
    """Run the suite polluted the way a dispatch's gate is, and prove it comes up clean."""
    probe = f"{__file__}::test_this_process_carries_no_inherited_history_setting"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", probe, "-p", "no:cacheprovider", "-p", "no:xdist"],
        cwd=REPO_ROOT,
        env={**os.environ, **DISPATCH_HISTORY},
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, (
        f"a suite inheriting {sorted(DISPATCH_HISTORY)} did not drop them: "
        f"{result.stdout}{result.stderr}"
    )
