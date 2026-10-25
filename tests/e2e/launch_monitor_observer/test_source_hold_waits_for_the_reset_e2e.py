"""A node edged into a rate-limited private tracker is held until the provider's reset.

The adopted engine holds a node on a tracker its task's edges name and, where the tracker
answered rate limited with a reset, reads it again only once that reset has passed, rather
than on a fixed interval (onepipeline's `docs/contract-divergences.md`, section 131). This
journey launches a plan through `just orchestrate` whose `edged` task carries an opaque edge
into a loopback Linear stand-in answering rate limited with a reset a few seconds ahead, and
shows the node held on that source with the reset, the stand-in receiving nothing between the
launch's read and the reset, and the node released and dispatched once the stand-in answers.

The plan has two nodes, on the planner's ruling: under that engine a node is released only
once every prerequisite its read resolves is one of its dependencies in the run's graph, so
the opaque edge reaches a private copy of `prerequisite`, which `edged` depends on. Everything
is real — the recipes, the engine and its linked plan store — and only the paid provider and
the tracker's HTTP transport are doubled, the provider where every launch journey here doubles
it. Every name is synthetic.
"""

# The same answers `tests/e2e/launch_monitor_observer/test_parked_run_is_driven_e2e.py` gives,
# for the same reason: this launches the installed engine through the recipes, so it sits in
# the code-keyed tier every such launch here is in and shares that tier's fixtures.
# llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] see above
# llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] see above
# llmlint: ignore-file[shell_test_tiers_stay_split] see above

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import onetaskgraph_user_layer
import plan_fixture_source
import pytest
from linear_board import linear_board
from project_fixtures import project_from_plan
from test_adopted_engine_reads_an_adopted_run_e2e import _just, _until
from test_orchestrate_launch_e2e import CandidatePlan, _node
from test_orchestrate_launch_e2e import _environment as _launched_environment

from orchestrator.run_reading import RunId

# What the recipes this journey runs leave in the temporary directory is this module's own
# and goes with it (`tests/short_state.py`).
pytestmark = [pytest.mark.usefixtures("module_tmpdir")]

#: A launching session this journey states rather than inherits, so the run is its own to
#: `just stop`.
LAUNCHING_SESSION = "e2e-source-hold-waits-for-the-reset"

#: The private Linear tracker the user layer declares, and the credential it names: under the
#: `FAKE_` prefix `scripts/dispatch-env.sh` keeps for a test double's variables, because the
#: launch drops every name its tables do not list before the driver starts.
TRACKER = "synthetictracker"
TRACKER_KEY_ENV = "FAKE_SYNTHETIC_LINEAR_API_KEY"
#: The workspace the stand-in names itself as, and the designator an opaque edge into it is
#: recorded under: Linear's plugin prefix and the SHA-256 of the plugin and that identifier
#: (onetaskgraph's `docs/metadata.md`, "The opaque far end").
WORKSPACE = "synthetic-workspace"
DESIGNATOR = "linear/" + hashlib.sha256(f"linear\n{WORKSPACE}".encode()).hexdigest()
#: The tracker's item the edge reaches: a private copy of the plan's `prerequisite`.
ITEM = "synthetic-issue-1"
#: How far ahead of the stand-in's first request its reset falls.
RESET_AHEAD_MS = 8_000

_PLAN: CandidatePlan = {
    "schema_version": 2,
    "tasks": [
        _node(id="prerequisite"),
        _node(id="edged", deps=["prerequisite"]),
    ],
}


def _layer(endpoint: str) -> dict[str, Any]:
    """The user layer: the private tracker, at the stand-in's loopback endpoint."""
    return {
        "sources": {
            TRACKER: {
                "visibility": "private",
                "plugin": "linear",
                "config": {"api_key_env": TRACKER_KEY_ENV, "team": "SYN", "endpoint": endpoint},
            }
        }
    }


def _edge_into_the_tracker(project: str) -> None:
    """Record, beside `edged`'s own dependency, its opaque edge to the tracker's item."""
    native = project.split(":", 1)[1]
    task = plan_fixture_source.root() / "tasks" / native / "edged.md"
    written = task.read_text(encoding="utf-8")
    own = f'depends_on: ["{native}/prerequisite"]'
    assert own in written, written
    opaque = json.dumps({"designator": DESIGNATOR, "id": ITEM, "kind": "blocks", "item": "task"})
    task.write_text(
        written.replace(own, f'depends_on: ["{native}/prerequisite", {opaque}]'),
        encoding="utf-8",
    )


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def test_a_node_edged_into_a_rate_limited_tracker_waits_for_its_reset(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """Held on the tracker with its reset, asked nothing before it, then released and run."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment = _launched_environment(tmp_path, oneharness_bin, session=LAUNCHING_SESSION)
    plan = tmp_path / "source-hold.plan.json"
    plan.write_text(json.dumps(_PLAN), encoding="utf-8")
    project = project_from_plan(plan)
    _edge_into_the_tracker(project)
    run = RunId(project.split(":", 1)[1])

    # llmlint: ignore[e2e_not_mocked] Only the tracker's HTTP transport is substituted.
    with linear_board() as board:
        board.create_issue(
            ITEM,
            "chore: prerequisite, kept privately",
            'Kept privately.\n\n<!-- onetaskgraph.metadata `{"onepipeline.id":"prerequisite"}` -->',
        )
        home = onetaskgraph_user_layer.installed(tmp_path / "config-home", _layer(board.endpoint))
        environment[onetaskgraph_user_layer.XDG_CONFIG_HOME] = str(home)
        environment[TRACKER_KEY_ENV] = "loopback-not-a-production-credential"
        board.rate_limit_for(RESET_AHEAD_MS)
        try:
            launched = _just(
                "orchestrate", project, "--detach", "--dag-graph", "off", environment=environment
            )
            said = launched.stdout + launched.stderr
            assert launched.returncode == 0, f"the launch failed:\n{said}"
            launch_reads = board.arrivals()
            reset_at = board.reset_at()
            assert launch_reads, f"the launch never asked the tracker:\n{said}"
            assert reset_at is not None, launch_reads
            assert all(at < reset_at for at in launch_reads), (launch_reads, reset_at)

            # Held on the tracker, rate limited, until the reset the stand-in named.
            _until("edged's source-wait", lambda: _surface(run, environment) is not None)
            surface = _surface(run, environment)
            assert surface is not None
            named = re.search(
                rf"source '{TRACKER}', declared private, is rate limited.*?'{TRACKER}' is read "
                r"again at (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) UTC, the reset the provider gave",
                surface,
            )
            assert named is not None, surface
            read_again = datetime.strptime(named[1], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
            assert reset_at // 1_000 <= read_again.timestamp() <= reset_at // 1_000 + 1, (
                named[1],
                reset_at,
            )

            _until("the reset to pass", lambda: _now_ms() >= reset_at, seconds=60)
            _until(
                "edged to be released and dispatched",
                lambda: "edged" in _records(run, environment, "node-dispatched"),
            )
            # Read off the records' own times, so a slow host reading the stream late cannot
            # mistake a dispatch it saw after the reset for one that happened after it.
            unheld = _records(run, environment, "node-unheld")
            dispatched = _records(run, environment, "node-dispatched")
            assert "edged" in unheld, unheld
            assert unheld["edged"] >= reset_at, (unheld, reset_at)
            assert dispatched["edged"] >= reset_at, (dispatched, reset_at)
            asked = board.arrivals()[len(launch_reads) :]
            assert asked, "the tracker was never read again"
            assert all(at >= reset_at for at in asked), (asked, reset_at)
        finally:
            _just("stop", run, environment=environment, seconds=120)


def _monitored(run: str, environment: dict[str, str]) -> list[str]:
    """Every line the run's merged stream renders, read through the real `just monitor`."""
    streamed = _just("monitor", run, "--all", environment=environment, seconds=120)
    assert streamed.returncode == 0, streamed.stdout + streamed.stderr
    return streamed.stdout.splitlines()


def _records(run: str, environment: dict[str, str], kind: str) -> dict[str, int]:
    """Each node the stream renders a ``kind`` record for, at its first such record's time.

    The time is epoch milliseconds, read off the line's leading timestamp.
    """
    nodes: dict[str, int] = {}
    for line in _monitored(run, environment):
        fields = line.split()
        if len(fields) >= 3 and fields[1].startswith("graph:") and fields[2] == kind:
            at = datetime.strptime(fields[0], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
            nodes.setdefault(fields[1].removeprefix("graph:"), int(at.timestamp() * 1_000))
    return nodes


def _surface(run: str, environment: dict[str, str]) -> str | None:
    """The message of the last `source-wait` surface queued about `edged`, if any."""
    queued = [
        line
        for line in _monitored(run, environment)
        if "graph:edged" in line and 'kind="source-wait"' in line
    ]
    return queued[-1] if queued else None
