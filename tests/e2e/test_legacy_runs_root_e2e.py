"""What the adopted read surfaces do with a runs root recorded before the crate adoption.

`tests/fixtures/legacy-runs/` holds three run directories written by this repository's
own pre-adoption implementation, each preserving one shape that was awkward to read: a
`resume` object with `stack_bases`, a lifecycle `steps` node ending in a `kind: human`
step, and a plan whose node id and text carry an unpaired UTF-16 surrogate. They were
added by `fix(read-api): serve the legacy plan shapes and text the contract severed` and
consumed by the read-API journey in `tests/e2e/test_server_e2e.py`, which went away in
84f048e when this repository became a configuration layer over the published CLIs —
leaving the fixtures with no reader at all.

This is the reader they have against the interface that exists now, and it is narrower
than the one they lost. Measured rather than assumed: the adopted `onepipeline`
discovers a run by its `launch.json`, which the pre-adoption shape never wrote, so every
read verb stops there and `just runs` lists nothing. **The published surface has no verb
that imports, migrates, or otherwise makes such a run readable** — `start`, `adopt`,
`runs`, `status`, `results`, `monitor`, `telemetry`, `transcript` and the rest all
require a run the adopted writer produced. So what these fixtures can still be held to
is what an operator upgrading this host actually meets: an old runs root is *inert* —
each run is refused by the name of the file it is missing, and the listing succeeds
reporting none — rather than a crash, a panic, or a directory that silently disappears.

What is *not* held here, and cannot honestly be: the recorded plans and journals
themselves. Reaching them would mean writing `onepipeline`'s internal launch record by
hand, which is a state no planner-facing command can produce, so a journey built on it
would prove the fixture readable against a product that offers no way to read it. The
surrogate and the legacy plan shapes are therefore guarded as repository *content*
below, not as reader behaviour. Restoring behavioural coverage of them needs an upstream
verb for reading a pre-adoption run; until there is one, that gap is real and is stated
here rather than papered over.

The recipes and `onepipeline` are the real ones; nothing is faked, because no model is
reached by a read.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The checked-in fixtures, and the run each directory preserves.
LEGACY_RUNS = REPO_ROOT / "tests" / "fixtures" / "legacy-runs"
LEGACY_RUN_IDS = ("legacy-resume-object", "legacy-steps-node", "legacy-surrogate-text")

#: The run whose recorded plan carries a lone UTF-16 surrogate, and the escape that
#: proves it still does. A reader that decodes strictly fails on this and on nothing else
#: in the fixtures, so it is named rather than left to the parametrized sweep.
SURROGATE_RUN = "legacy-surrogate-text"
LONE_SURROGATE = "\\ud83d"

#: The file the adopted reader discovers a run by. Its absence is what makes a
#: pre-adoption directory invisible, so it is also what the refusals must name.
LAUNCH_RECORD = "launch.json"

#: How the listing reports a root that holds run directories it could not read, as
#: distinct from `no runs recorded` for one that holds nothing at all. The distinction
#: is the recovery: an operator pointed at a directory they can see runs in needs to be
#: told those runs were skipped and why, not that there are none.
UNREADABLE_ROOT = "could be read"


@pytest.fixture
def legacy_root(tmp_path: Path) -> Path:
    """A private copy of the fixtures, so no journey can write to the checked-in ones."""
    root = tmp_path / "runs"
    shutil.copytree(LEGACY_RUNS, root)
    return root


def _environment(runs_root: Path) -> dict[str, str]:
    """The ambient values a planner's read reaches these recipes with."""
    inherited = dict(os.environ)
    inherited["ONEPIPELINE_RUNS_DIR"] = str(runs_root)
    # `scripts/onepipeline.sh` derives this session's identity from the harness variable
    # a real planner session carries; a read that could not name its session would be
    # answering a different question than the one an operator asks.
    inherited["CLAUDE_CODE_SESSION_ID"] = "legacy-runs-root-e2e"
    return inherited


def _just(*arguments: str, runs_root: Path) -> subprocess.CompletedProcess[str]:
    """Run one real recipe against the named runs root."""
    return subprocess.run(
        ["just", *arguments],
        cwd=REPO_ROOT,
        env=_environment(runs_root),
        text=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )


def test_the_fixtures_still_carry_the_shapes_they_were_recorded_for() -> None:
    """The fixtures' content is guarded here, because no reader reaches it any more.

    This is deliberately a statement about the repository rather than about a running
    tool: with the read-API journey gone and no published verb able to open a
    pre-adoption run, a fixture could be hollowed out, or quietly re-acquire the retired
    `done_when`, and every behavioural check in this module would still pass. Naming each
    preserved shape is what makes that a failure.
    """
    for run in LEGACY_RUN_IDS:
        assert (LEGACY_RUNS / run / "events.jsonl").is_file(), f"{run} lost its journal"
        assert (LEGACY_RUNS / run / "round-01" / "plan.json").is_file(), f"{run} lost its plan"

    surrogate = (LEGACY_RUNS / SURROGATE_RUN / "round-01" / "plan.json").read_text(encoding="utf-8")
    assert LONE_SURROGATE in surrogate, (
        f"{SURROGATE_RUN} no longer carries the unpaired surrogate it exists for"
    )

    resume = json.loads(
        (LEGACY_RUNS / "legacy-resume-object" / "round-01" / "plan.json").read_text(
            encoding="utf-8"
        )
    )
    assert "resume" in resume["tasks"][0] and "stack_bases" in resume["tasks"][0], resume

    steps = json.loads(
        (LEGACY_RUNS / "legacy-steps-node" / "round-01" / "plan.json").read_text(encoding="utf-8")
    )
    recorded = steps["tasks"][0]["steps"]
    assert [step.get("kind") for step in recorded] == [None, "human"], recorded
    assert not any("done_when" in step for step in recorded), (
        "the retired node-level `done_when` is back in a recorded plan; the adopted "
        "onepipeline refuses it, so it may not be reintroduced even in a fixture"
    )


def test_a_pre_adoption_runs_root_lists_no_runs_rather_than_failing(legacy_root: Path) -> None:
    """An old runs directory is inert to the listing, not fatal to it.

    This is the first thing an operator does after the upgrade, and the answer has to be
    an answer: the recipe succeeds and reports that it found nothing, rather than exiting
    on the first directory that is not shaped the way the adopted reader expects.

    The adopted release says more than that it found nothing: it distinguishes a root
    holding directories it could not read from an empty one, and names each skipped run
    and the record it lacks. That is the difference between an operator seeing a bare
    "no runs recorded" for a directory that visibly holds three runs — and reasonably
    concluding the listing is broken — and being told which file would make each one
    readable. So the enumeration is asserted, not just the success.
    """
    listed = _just("runs", runs_root=legacy_root)

    assert listed.returncode == 0, listed.stderr
    assert f"no run under {legacy_root} {UNREADABLE_ROOT}" in listed.stdout, listed.stdout
    for run in LEGACY_RUN_IDS:
        assert f"{legacy_root / run}: no {LAUNCH_RECORD}" in listed.stdout, (
            f"the listing passed over {run} without saying why:\n{listed.stdout}"
        )
    # Named as skipped, with the reason — not listed as a run the reader accepted.
    assert listed.stdout.count(f"no {LAUNCH_RECORD}") == len(LEGACY_RUN_IDS), listed.stdout


@pytest.mark.reads_docs
def test_the_listing_does_find_a_run_whose_launch_record_is_present(
    legacy_root: Path, tmp_path: Path
) -> None:
    """The control: the same listing over the same root reports a run it can discover.

    Without this, the empty listing above would be indistinguishable from a recipe that
    reports nothing whatever it is pointed at — and the claim that an old root is *inert*
    would be a claim about a broken command instead. The run it finds is one the adopted
    writer produced, copied in beside the legacy ones, so the difference between them is
    the recorded shape and nothing else.
    """
    adopted = tmp_path / "adopted-run"
    examples = tmp_path / "examples"
    examples.mkdir()
    for records in ("projects", "tasks", "documents"):
        shutil.copytree(REPO_ROOT / "examples" / records, examples / records)
    environment = {
        **_environment(adopted),
        "ONETASKGRAPH_SOURCES__EXAMPLES__CONFIG__ROOT": str(examples),
        "ONEAGENTGRAPH_ONEHARNESS_BIN": str(Path(__file__).resolve().parent / "fake_backend.py"),
        "REAL_ONEHARNESS_BIN": shutil.which("oneharness") or "oneharness",
        "XDG_STATE_HOME": str(tmp_path / "state"),
    }
    launched = subprocess.run(
        ["just", "orchestrate", "examples:scheduler-research", "--detach"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=e2e_timeout(300),
        check=False,
    )
    assert launched.returncode == 0, f"{launched.stdout}\n{launched.stderr}"
    recorded = [run for run in adopted.iterdir() if (run / LAUNCH_RECORD).is_file()]
    assert recorded, f"the launch recorded no run under {adopted}"
    for run in recorded:
        shutil.copytree(run, legacy_root / run.name)

    listed = _just("runs", runs_root=legacy_root)

    try:
        assert listed.returncode == 0, listed.stderr
        for nothing_found in ("no runs recorded", UNREADABLE_ROOT):
            assert nothing_found not in listed.stdout, (
                "the listing reported nothing even for a run the adopted writer produced, "
                f"so the empty legacy listing says nothing about the legacy records:"
                f"\n{listed.stdout}"
            )
        for run in recorded:
            assert run.name in listed.stdout, listed.stdout
    finally:
        _just("stop", recorded[0].name, runs_root=adopted)


@pytest.mark.parametrize("run", LEGACY_RUN_IDS)
def test_a_pre_adoption_run_is_refused_by_the_file_it_is_missing(
    legacy_root: Path, run: str
) -> None:
    """Naming a run recorded before the adoption says which file would make it readable.

    The recovery is the message: a directory that predates `launch.json` is not corrupt
    and not empty, and an operator who is told exactly which record is absent can decide
    whether to keep the run or drop it. A bare "not found" would leave the same directory
    looking like a typo.
    """
    refused = _just("results", run, runs_root=legacy_root)

    assert refused.returncode != 0, refused.stdout
    reported = refused.stderr + refused.stdout
    assert LAUNCH_RECORD in reported, reported
    assert run in reported, reported
