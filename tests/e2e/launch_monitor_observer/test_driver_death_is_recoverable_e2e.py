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

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] The project's key is the
edge that covers what this reads rather than a missing one: the subject is a sentence in
`docs/orchestration.md`, so the key names that document, and an edit to it re-runs this
rather than replaying a verdict taken before the claim moved. What it costs is two `just`
views over one built run root and no dispatch at all.

llmlint: ignore-file[shell_test_tiers_stay_split] These are pytest journeys rather than a
shell test suite, and what they cost is the two views named above.

llmlint: ignore-file[tests_mirror_real_usage] No real launch can be asked for the pairing
under test: its driver is alive by construction, and a recorded one has no live dispatch
left. The recipes, the engine and the live process are all real; only the record they read
is composed, and that it resembles what a launch writes is held by
`tests/host_views/test_status_and_host_views_e2e.py`'s real-launch journey over the same builder.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from probe_run_root import Probe, run_name, run_root
from project_fixtures import helper
from waits import timeout as e2e_timeout

from orchestrator.gate_impact import count_leftovers
from orchestrator.root import REPO_ROOT

# What the recipes these journeys run leave in the temporary directory, by design or
# from a published tool they run, is this module's own and goes with it
# (`tests/short_state.py`).
pytestmark = [pytest.mark.usefixtures("module_tmpdir")]

#: The guard refusing every paid provider binary a view would otherwise start.
PAID_PROVIDER_GUARD = helper("no-paid-provider")

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


def _view(
    recipe: str,
    *arguments: str,
    runs_root: Path,
    temporary: Path | None = None,
    guarded: bool = True,
    behind: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """One of the two views, through the real recipe.

    Under ``temporary`` when one is named, and with the binaries in ``behind`` on `PATH`
    after the guard — or, with ``guarded`` off, in its place.
    """
    environment = dict(os.environ)
    if temporary is not None:
        environment.update(dict.fromkeys(("TMPDIR", "TMP", "TEMP"), str(temporary)))
    environment["ONEPIPELINE_RUNS_DIR"] = str(runs_root)
    environment["CLAUDE_CODE_SESSION_ID"] = "driver-death-e2e"
    # `status` reads every provider's health for its host report, which would start the
    # real provider binaries; the guard answers in their place.
    first = ([PAID_PROVIDER_GUARD] if guarded else []) + ([behind] if behind else [])
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = os.pathsep.join([*map(str, first), environment["PATH"]])
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
        assert [line for line in rendered.splitlines() if line.startswith("  free space: ")], (
            f"'{SECTION}' sends a supervisor to the free-space line before diagnosing a dead "
            f"driver, and `just {view}` printed none: {rendered!r}"
        )


#: The provider binaries the engine's health probe starts, which `PROVIDER_STAND_IN`
#: answers for behind the guard.
PROBED_PROVIDERS = ("claude", "codex", "cursor-agent")

#: A provider binary that records each call by the name it was invoked as and leaves in
#: `TMPDIR` what the real one does there on every call: `cursor-agent`'s log directory —
#: a session log and the `latest.log` link to it — and claude-code's own directory.
#: Exits 1, so it reads as an unhealthy provider and never as a turn.
PROVIDER_STAND_IN = """import os, sys

invoked = os.path.basename(sys.argv[0])
with open(os.environ["PROVIDER_STAND_IN_CALLS"], "a", encoding="utf-8") as calls:
    calls.write(invoked + "\\n")
temporary = os.environ.get("TMPDIR") or "/tmp"
if invoked == "cursor-agent":
    logs = os.path.join(temporary, f"cursor-agent-logs-{os.getuid()}")
    os.makedirs(logs, exist_ok=True)
    session = os.path.join(logs, f"session-{os.getpid()}.log")
    with open(session, "a", encoding="utf-8") as log:
        log.write("about\\n")
    latest = os.path.join(logs, "latest.log")
    if os.path.lexists(latest):
        os.remove(latest)
    os.symlink(session, latest)
elif invoked == "claude":
    os.makedirs(os.path.join(temporary, f"claude-{os.getuid()}"), exist_ok=True)
raise SystemExit(1)
"""


def _provider_stand_ins(directory: Path) -> Path:
    """A directory holding `PROVIDER_STAND_IN` under every name in `PROBED_PROVIDERS`."""
    directory.mkdir()
    program = directory / "provider_stand_in.py"
    program.write_text(f"#!{sys.executable}\n{PROVIDER_STAND_IN}", encoding="utf-8")
    program.chmod(0o755)
    for name in PROBED_PROVIDERS:
        (directory / name).symlink_to(program.name)
    return directory


@pytest.mark.parametrize("guarded", [False, True], ids=["unguarded", "guarded"])
def test_the_status_views_provider_probe_leaves_nothing_in_the_temporary_directory(
    undriven: Probe, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, guarded: bool
) -> None:
    """The host report probes every provider binary, and the guard answers for each.

    `just status` and `just host` start each binary the engine's provider health probe
    knows, `cursor-agent` among them, and a real one writes into `TMPDIR` on every call:
    that is what the pre-push gate's `gate-leftovers` budget refused a passing push for.
    Stand-ins that record their calls and write what the real ones write sit on `PATH`
    behind the guard. Unguarded, they prove the probe reaches `cursor-agent` and that the
    gate's own count sees what it leaves; guarded, none may be reached and the directory
    the views were handed must hold nothing, uv's environment lock excepted by the
    budget's ruling.
    """
    stand_ins = _provider_stand_ins(tmp_path / "providers")
    calls = tmp_path / "provider-calls"
    calls.touch()
    monkeypatch.setenv("PROVIDER_STAND_IN_CALLS", str(calls))
    temporary = tmp_path / "views-tmp"
    temporary.mkdir()

    for recipe, arguments in (("status", (undriven.run,)), ("host", ())):
        viewed = _view(
            recipe,
            *arguments,
            runs_root=undriven.root,
            temporary=temporary,
            guarded=guarded,
            behind=stand_ins,
        )
        assert viewed.returncode == 0, viewed.stderr

    reached = set(calls.read_text(encoding="utf-8").split())
    left = [entry.path for entry in count_leftovers(temporary, []).top]
    if not guarded:
        assert "cursor-agent" in reached, (
            f"the views' provider probe no longer starts cursor-agent (it reached {reached}), "
            "so the guarded case below proves nothing about it"
        )
        assert f"cursor-agent-logs-{os.getuid()}" in left, left
        return
    assert not reached, (
        f"the views' provider probe reached {sorted(reached)} past the guard; give "
        "`tests/e2e/no-paid-provider/` an entry for each"
    )
    assert not left, f"the views left {left} in TMPDIR"
