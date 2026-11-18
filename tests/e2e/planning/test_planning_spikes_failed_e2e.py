"""A spikes run that ends unsuccessfully stops the planning run before it finalizes anything.

`just plan` drafts a plan whose one spike names an execution checkout nothing registered, so
the spike cannot be dispatched and the spikes run — which nothing approves or attests — ends
without completing. The planning run's `spikes` step then fails, and nothing
after it runs: no finalize, check, review, design document or board copy.

Human items, plans and follow-up drafts stay in the bench's own local sources, and every
`onevcs` record in the scratch identity's registry.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from typing import NamedTuple

import pytest
from drafting import brief, drafting
from planning_bench import BOARD, Bench, bench, just, runs_of, status, store_json
from scratch_identity import seeded
from waits import timeout as e2e_timeout

pytestmark = [
    pytest.mark.usefixtures("module_tmpdir"),
    pytest.mark.xdist_group("planning-spikes-failed"),
]

#: How long the plan recipe may take to return.
SETTLE_SECONDS = 900.0
#: The steps after `spikes`, none of which may run once it fails.
AFTER_SPIKES = ("finalize", "spike-evidence", "check", "review", "design", "copy", "approve-design")


class Failed(NamedTuple):
    bench: Bench
    native: str
    planned: subprocess.CompletedProcess[str]
    spikes: dict[str, object]
    planning: dict[str, object]


def _nodes(answer: dict[str, object]) -> dict[str, str]:
    nodes = answer.get("nodes")
    assert isinstance(nodes, list), answer
    return {str(node["id"]): str(node.get("status")) for node in nodes if isinstance(node, dict)}


@pytest.fixture(scope="module")
def failed(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Iterator[Failed]:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp = tmp_path_factory.mktemp("planning-spikes-failed")
    identity = seeded(tmp / "identity")
    bench_ = bench(
        tmp,
        oneharness_bin,
        "e2e-planning-spikes-failed",
        ONEVCS_HOME=str(identity.home),
        **identity.environment,
    )
    native = f"plan-sf-{os.getpid()}"
    drafting(bench_, native, spikes=(str(identity.publication), "no-checkout-registered-so"))
    out, err = tmp / "plan.out", tmp / "plan.err"
    with out.open("w") as stdout, err.open("w") as stderr:
        planning = subprocess.Popen(  # noqa: S603 - the copy's own recipe
            ["just", "plan", brief(bench_, native), BOARD],  # noqa: S607 - as an operator types it
            cwd=bench_.checkout,
            env=bench_.environment,
            stdout=stdout,
            stderr=stderr,
            text=True,
        )
        try:
            planning.wait(timeout=e2e_timeout(SETTLE_SECONDS))
        finally:
            if planning.poll() is None:
                planning.kill()
                planning.wait()
    planned = subprocess.CompletedProcess(
        planning.args, planning.returncode, out.read_text(), err.read_text()
    )
    (planning_run,) = [one for one in runs_of(bench_) if one.endswith("-planning")]
    (spikes_run,) = [one for one in runs_of(bench_) if one.endswith("-spikes")]
    yield Failed(bench_, native, planned, status(bench_, spikes_run), status(bench_, planning_run))
    for one in runs_of(bench_):
        if not one.startswith("."):
            just(bench_, "stop", one, seconds=120)


def test_the_spikes_run_ends_without_completing(failed: Failed) -> None:
    ending = failed.spikes.get("ending")
    assert isinstance(ending, dict) and ending.get("kind") != "complete", failed.spikes
    assert _nodes(failed.spikes)["spike-health"] != "done", failed.spikes
    # No approval step held the spike: the template's one member is the brief writer.
    assert sorted(_nodes(failed.spikes)) == ["spike-brief", "spike-health"], failed.spikes


def test_the_spikes_step_fails_and_nothing_after_it_runs(failed: Failed) -> None:
    assert failed.planned.returncode != 0, failed.planned.stdout + failed.planned.stderr
    nodes = _nodes(failed.planning)
    assert nodes["draft"] == "done", nodes
    assert nodes["spikes"] == "failed", nodes
    assert not [step for step in AFTER_SPIKES if nodes[step] in ("done", "running")], nodes


def test_no_design_document_or_board_copy_was_made(failed: Failed) -> None:
    documents = store_json(
        failed.bench,
        "document",
        "list",
        "--source",
        "authoring",
        "--project",
        f"authoring:{failed.native}",
    )
    templates = [
        one["item"]["metadata"].get("onetaskgraph.template", {}).get("template")
        for one in documents["items"]
    ]
    assert "onepipeline:design-doc" not in templates, json.dumps(documents)[:800]
    copies = store_json(failed.bench, "project", "list", "--source", BOARD)
    assert not copies.get("items"), copies
