"""`just status --json` reads a run an older engine left exactly as one the adopted engine writes.

This host's runs outlive the engine that launched them: a run journalled under one
release is read, adopted and supervised under a later one, and the long multi-repository
runs this host adopts across an engine bump are the ones whose history matters most. Two
shapes are on disk here at once:

* **The shape onepipeline v0.75.0 left** — every member heartbeat journalled as a
  `member-heartbeat` record, a fold checkpoint at a schema the adopted engine no longer
  writes, and a `retry` journalled as an `edit-committed` carrying the replacement's task.
* **The adopted engine's shape** — member liveness kept beside the journal in
  `<run>/liveness.json` and no `member-heartbeat` record in the journal.

Each journey writes one of them into a temporary runs root and reads it through the real
`just status <run> --json`, the recipe a supervisor reads a run's nodes from. It holds that
the reading lists exactly the plan's nodes and the retry's replacement, each at the
settlement its journal records, and that the older run needs no hand migration: the stale
checkpoint beside it is refolded by the reader rather than refused or believed.

llmlint: ignore-file[tool_output_is_signal] the JSON reading is this viewing command's
whole product, so the assertions are on what it printed.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] The task that added this
module places it beside the status journeys of `tests/host_views/`, whose key already holds
everything the status recipe reads — the `justfile`, `scripts/onepipeline.sh`, the engine
pin and `orchestrator/run_reading.py`. The wider inputs that key carries are for the
real-launch journey beside it, and what they cost this module is three `just status` reads
over built run roots and no dispatch.

llmlint: ignore-file[tests_mirror_real_usage] The run roots are composed by
`tests/e2e/probe_run_root.py`, because neither shape can be asked of a launch: the older
one is what a release this host no longer installs wrote, and the real journals under this
host's runs root can hold private content, so none is copied here. Only the records are
composed; the recipe, the wrapper script and the engine reading them are real, and
`tests/test_engine_contracts.py` reconciles every record the builder writes against the
engine's own declaration of it, the old checkpoint against the engine's own golden.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import short_state
from probe_run_root import (
    RECORDED,
    record_liveness,
    record_old_checkpoint,
    run_name,
    settled_run_root,
)
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

# What the recipe leaves in the temporary directory is this module's own and goes with it
# (`tests/short_state.py`).
pytestmark = [pytest.mark.usefixtures("module_tmpdir")]

#: The fold checkpoint schemas the shape v0.75.0 left can carry: 12 is what v0.75.0
#: writes, 13 what v0.77.0 to v0.88 wrote over the same journal records.
OLD_CHECKPOINT_SCHEMAS = (12, 13)


def _status(runs_root: Path, run: str, tmp_path: Path) -> dict[str, object]:
    """`just status <run> --json`, through the real recipe, as one document."""
    environment = dict(os.environ)
    environment["ONEPIPELINE_RUNS_DIR"] = str(runs_root)
    environment["CLAUDE_CODE_SESSION_ID"] = "status-shapes-e2e"
    environment["XDG_STATE_HOME"] = str(short_state.state_home(tmp_path))
    read = subprocess.run(
        ["just", "status", run, "--json"],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=e2e_timeout(180),
        check=False,
    )
    assert read.returncode == 0, f"`just status {run} --json` refused:\n{read.stderr}"
    document = json.loads(read.stdout)
    assert isinstance(document, dict), read.stdout
    return document


def _assert_reads_as_recorded(document: dict[str, object]) -> None:
    nodes = document.get("nodes")
    assert isinstance(nodes, list), document
    read = {node["id"]: node["status"] for node in nodes}
    assert read == RECORDED, (
        "the reading must list exactly the plan's nodes and the retry's replacement, each "
        f"where its journal leaves it: expected {RECORDED}, read {read}"
    )


def _heartbeats(directory: Path) -> int:
    journal = (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()
    return sum(1 for line in journal if json.loads(line)["kind"] == "member-heartbeat")


@pytest.mark.parametrize("schema", OLD_CHECKPOINT_SCHEMAS)
def test_a_run_the_v0_75_0_engine_left_is_read_without_a_hand_migration(
    tmp_path: Path, schema: int
) -> None:
    """Journalled heartbeats, a retry and a stale checkpoint read as the journal records."""
    runs = tmp_path / "runs"
    run = run_name()
    directory = settled_run_root(runs, run, heartbeats=3)
    record_old_checkpoint(directory, schema)
    assert _heartbeats(directory) == 3 * len(RECORDED)
    assert not (directory / "liveness.json").exists()

    _assert_reads_as_recorded(_status(runs, run, tmp_path))

    # The reader refolded the run rather than believing or refusing the older document,
    # which is what reading it without a hand migration means.
    refolded = json.loads((directory / "checkpoint.json").read_text(encoding="utf-8"))
    assert refolded.get("schema_version") != schema, (
        f"the schema-{schema} checkpoint is still beside the journal after the reading, so "
        "the adopted engine neither refolded it nor wrote its own"
    )


def test_a_run_in_the_adopted_engine_s_shape_reads_as_its_journal_records(
    tmp_path: Path,
) -> None:
    """Liveness beside the journal and no journalled heartbeat read the same way."""
    runs = tmp_path / "runs"
    run = run_name()
    directory = settled_run_root(runs, run, heartbeats=0)
    record_liveness(directory)
    assert _heartbeats(directory) == 0

    _assert_reads_as_recorded(_status(runs, run, tmp_path))
