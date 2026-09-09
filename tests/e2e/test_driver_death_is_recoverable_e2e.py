"""A dispatch outlives its driver, compared against the engine that decides it.

`docs/orchestration.md` tells a supervisor to read `DRIVER DEAD` beside live dispatches
as a run to adopt rather than as work to redo. That is a statement about what the
installed engine does, and a statement of that shape either names the check that re-takes
it or is a paragraph describing a build nobody runs — so this is that check. It builds the
one pairing no recorded run can carry, for the reason `tests/e2e/probe_run_root.py` gives.

What it deliberately does *not* assert is the wording of either verdict beyond the two
words the document quotes. The engine owns that prose; what this repository's guidance
depends on is that the run reads as undriven while the dispatch reads as live, and that is
what fails here when it stops being true.

llmlint: ignore-file[tool_output_is_signal] the rendered views are these viewing
commands' whole product, so the assertions are on what they printed.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] The whole-workspace tier
is the edge that covers what this reads rather than a missing one: the subject is a
sentence in `docs/orchestration.md`, so that document is an input, and a memo under any
narrower key would replay a verdict taken before the claim moved. What it costs is two
`just` views over one built run root and no dispatch at all.

llmlint: ignore-file[shell_test_tiers_stay_split] These are pytest journeys rather than a
shell test suite, and what they cost is the two views named above.

llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] `reads_docs` is not a
marker chosen over a project here — `tests/conftest.py` *requires* it of a test that opens
this checkout's prose and refuses the test without. A project of this module's own would
still have to declare that read, and would then be a second project keyed on the same
whole workspace.

llmlint: ignore-file[tests_mirror_real_usage] No real launch can be asked for the pairing
under test: its driver is alive by construction, and a recorded one has no live dispatch
left. The recipes, the engine and the live process are all real; only the record they read
is composed, and that it resembles what a launch writes is held by
`tests/e2e/test_supervision_readings_e2e.py`'s real-launch journey over the same builder.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from probe_run_root import Probe, run_name, run_root
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_docs

#: The document that carries the statement, and the section it belongs to.
GUIDANCE = REPO_ROOT / "docs" / "orchestration.md"
SECTION = "### Adopting a run whose driver died"

#: The claim, as that section states it. Held here so that moving the sentence brings
#: this check due rather than leaving it asserting an engine behaviour nothing reads.
#: Matched with runs of whitespace collapsed, because the document wraps its lines and
#: where a sentence happens to break is not a thing this gate has an opinion about.
CLAIM = (
    "**A dispatch outlives its driver, so a dead-driver verdict beside live dispatches "
    "is a recoverable state rather than lost work.**"
)

#: The verdict the engine gives a run whose launch process is gone, which the section
#: names outright and tells a supervisor not to read alone.
DEAD = "DRIVER DEAD"

#: What the host view says of a dispatch it cannot prove is running. Its absence is how
#: this check reads "the dispatch is still live" out of a view that says nothing when
#: everything is in order.
UNPROVEN = "UNPROVEN"


@pytest.fixture
def undriven(tmp_path: Path) -> Iterator[Probe]:
    """A run nothing is driving, whose dispatch registry names a live process.

    The run id is minted per journey: these run four at a time on a host that is also
    running live dispatches, and `just host` answers about the whole machine.
    """
    root = tmp_path / "runs"
    run = run_name()
    # Started here, so it is this journey's own process to signal and nobody else's.
    # It does nothing; what both views read of it is that it is there.
    dispatch = subprocess.Popen(["sleep", str(int(e2e_timeout(300)))])
    try:
        run_root(root, run, dispatch_pid=dispatch.pid)
        yield Probe(root=root, run=run)
    finally:
        dispatch.kill()
        dispatch.wait(timeout=e2e_timeout(30))


def _view(recipe: str, *arguments: str, runs_root: Path) -> subprocess.CompletedProcess[str]:
    """One of the two views, through the real recipe."""
    environment = dict(os.environ)
    environment["ONEPIPELINE_RUNS_DIR"] = str(runs_root)
    environment["CLAUDE_CODE_SESSION_ID"] = "driver-death-e2e"
    return subprocess.run(
        ["just", recipe, *arguments],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=e2e_timeout(180),
        check=False,
    )


def test_the_adoption_section_states_that_a_dispatch_outlives_its_driver() -> None:
    """The statement this check is about is in the section a manager reads it from."""
    guidance = GUIDANCE.read_text(encoding="utf-8")
    section = guidance[guidance.index(SECTION) :].split("\n## ", 1)[0]
    unwrapped = re.sub(r"\s+", " ", section)

    assert CLAIM in unwrapped, (
        f"{GUIDANCE.name}'s '{SECTION}' no longer carries the claim this journey "
        "compares against the installed engine, so one of the two moved without the "
        "other"
    )
    assert __name__.rsplit(".", 1)[-1] in unwrapped, (
        "the section has to name the check that re-takes its claim; a statement about "
        "somebody else's software with no check behind it goes stale silently"
    )


def test_the_run_reads_undriven_while_the_dispatch_it_made_reads_live(undriven: Probe) -> None:
    """Both halves at once, which is the whole of what the section tells a manager."""
    status = _view("status", undriven.run, runs_root=undriven.root)
    host = _view("host", runs_root=undriven.root)

    assert status.returncode == 0, status.stderr
    assert host.returncode == 0, host.stderr

    assert DEAD in status.stdout, (
        "a run whose launch process is gone reads as one nothing is driving; the status "
        f"view printed {status.stdout!r}"
    )

    rows = [line for line in host.stdout.splitlines() if undriven.run in line]
    assert len(rows) == 1, (
        "the dispatch that outlived the driver has to be on the host view, which is "
        f"where a supervisor confirms the work is still running: {host.stdout!r}"
    )
    assert UNPROVEN not in rows[0], (
        "the host view reports this dispatch as live rather than as one it cannot "
        f"prove, which is what makes the dead-driver verdict recoverable: {rows[0]!r}"
    )


def test_the_disk_reading_that_parts_a_crash_from_a_full_disk_is_on_both_views(
    undriven: Probe,
) -> None:
    """The section sends a supervisor to one line, and both views have to carry it."""
    status = _view("status", undriven.run, runs_root=undriven.root)
    host = _view("host", runs_root=undriven.root)

    for view, rendered in (("status", status.stdout), ("host", host.stdout)):
        assert [line for line in rendered.splitlines() if line.startswith("  disk")], (
            f"'{SECTION}' sends a supervisor to the disk line before diagnosing a dead "
            f"driver, and `just {view}` printed none: {rendered!r}"
        )
