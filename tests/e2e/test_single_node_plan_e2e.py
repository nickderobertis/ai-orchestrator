"""The two shipped one-node example plans, each run to completion by the planner.

One subtask is a plan file holding one node — there is no other way to dispatch —
so the examples an operator copies for that are the ones this drives, through the
real `just orchestrate` an operator launches them with. Both shapes are here
because they reach different machinery: the direct node dispatches onejudge at a
directory, the lifecycle node clones, works in an isolated worktree, verifies with
its gate, and merges.

The shipped files are read rather than retyped, and exactly two kinds of value are
substituted into them: the repository a lifecycle node publishes to (the example
names a repository nobody but its author has) and the task text (the example's
prose asks for real work; here it carries the deterministic backend's directives).
Everything else — the node id, persona, title, `done_when`, the goal, the schema
version — is the file as shipped, so an example that stops being runnable fails
here rather than on an operator's first attempt.
"""

# llmlint: ignore-file[e2e_not_mocked] Only the paid model backend is a double — onejudge's
# own `command` provider pointed at tests/e2e/fake_backend.py, the suite-wide invariant, and
# the one external dependency the free gate cannot run. `just orchestrate`, onejudge, the
# channel, the ledger, the run journal, the clone, the gate, and the merge are all real.
# llmlint: ignore-file[live_tier_compiles_and_requires_credential] the onejudge_bin fixture fails
# fast unless the real adopted onejudge CLI is on PATH, and this journey drives it as a real
# subprocess; per the documented suite invariant only the paid model backend is faked via
# onejudge's own command provider (fake_backend.py) — the one external dependency the free gate
# cannot run — so no model credential is required by design.

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import yaml
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator import BASE_CONFIG, REPO_ROOT, gitops
from orchestrator.registry import Registry

FAKE_BACKEND = REPO_ROOT / "tests" / "e2e" / "fake_backend.py"
EXAMPLES = REPO_ROOT / "examples"
SETTLE_TIMEOUT = 120
#: Every way the executor journals that a node stopped running. `node-failed` is
#: here so a broken example fails on its recorded status in seconds rather than by
#: waiting out the settle guard, which reads as a hang and says nothing.
TERMINAL_KINDS = ("node-settled", "node-failed")


def _wait_for(path: Path, predicate, timeout: float = 30) -> None:
    wait_deadline = deadline(timeout)
    while time.monotonic() < wait_deadline:
        if path.is_file() and predicate(path.read_text(encoding="utf-8")):
            return
        time.sleep(0.02)
    detail = path.read_text(encoding="utf-8") if path.is_file() else "<absent>"
    raise AssertionError(f"condition did not appear in {path}: {detail}")


def _settled(events: Path, node: str) -> dict[str, Any]:
    """The record saying ``node`` stopped running, once it has been journaled."""

    def satisfied(text: str) -> bool:
        return _find_terminal(text, node) is not None

    _wait_for(events, satisfied, SETTLE_TIMEOUT)
    record = _find_terminal(events.read_text(encoding="utf-8"), node)
    assert record is not None
    return record


def _find_terminal(text: str, node: str) -> dict[str, Any] | None:
    lines = text.splitlines()
    if lines and not text.endswith("\n"):
        lines.pop()  # the journal's one documented torn trailing line
    for line in lines:
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("kind") in TERMINAL_KINDS and record.get("node") == node:
            return dict(record)
    return None


@contextmanager
def _reaped(run_dir: Path) -> Iterator[None]:
    """End the detached launch however this journey leaves it.

    A launch outlives this process by design and its channel relay waits a day for a
    planner reply, so a journey that failed before answering the boundary would leave
    a real supervisory process competing with the rest of the suite for the host.
    """
    try:
        yield
    finally:
        status = run_dir / "orchestrator" / "status.json"
        if status.is_file():
            pid = json.loads(status.read_text(encoding="utf-8"))["pid"]
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(pid, signal.SIGKILL)


def _base_config(tmp_path: Path) -> Path:
    base = yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8"))
    base["provider"] = {"kind": "command", "command": [sys.executable, str(FAKE_BACKEND)]}
    path = tmp_path / "base.yaml"
    path.write_text(yaml.safe_dump(base), encoding="utf-8")
    return path


def _launch(tmp_path: Path, plan: Path, runs: Path, onejudge_bin: str) -> str:
    """Launch the plan the way an operator does, and return its run id."""
    launched = subprocess.run(
        [
            "just",
            "orchestrate",
            "--detach",
            str(plan),
            "--runs-dir",
            str(runs),
            "--base",
            str(_base_config(tmp_path)),
            "--onejudge-bin",
            onejudge_bin,
            "--skill-command",
            sys.executable,
            str(FAKE_BACKEND),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
        timeout=e2e_timeout(120),
    )
    return str(json.loads(launched.stdout)["run_id"])


#: The surfaces a completion verdict answers. Anything else a round raises is an
#: update the planner reads on the way to one of these.
BOUNDARY_KINDS = ("milestone", "closeout")


#: `Any`-valued for the same reason `_example` and `_settled` are: a surface is the
#: channel's own JSON, whose shape is the contract under test rather than one this
#: journey may restate. Modelling it here would assert against this file's idea of a
#: surface instead of the one `just channel-next` actually printed.
def _next_surface(run_id: str, runs: Path) -> dict[str, Any]:
    surfaced = subprocess.run(
        [
            "just",
            "channel-next",
            run_id,
            "--runs-dir",
            str(runs),
            "--timeout",
            str(e2e_timeout(60)),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return dict(json.loads(surfaced.stdout or "{}").get("surface") or {})


def _await_boundary(run_id: str, runs: Path) -> dict[str, Any]:
    """Read surfaces until the round's own boundary, which is what a verdict answers.

    Returns the surface verbatim, `Any`-valued for the reason `_next_surface` gives.

    A node that settles with an assessment has it surfaced as a *non-blocking*
    proposal, and the round does not wait on one — so whether it or the boundary
    reaches the channel first is a matter of when the pump's service thread runs,
    and under load it is the proposal. Reading exactly one surface and requiring it
    to be the boundary therefore fails on a healthy run; worse, replying `completion`
    to the proposal would spend the verdict on a surface nothing was waiting for and
    leave the boundary unanswered. A planner reads past updates to the question.
    """
    wait = deadline(SETTLE_TIMEOUT)
    while time.monotonic() < wait:
        surface = _next_surface(run_id, runs)
        if surface.get("kind") in BOUNDARY_KINDS:
            return surface
        assert surface, f"{run_id} raised no surface before its boundary"
        # Only a non-blocking update may precede the boundary. A blocking one is the
        # round asking something this journey did not expect, and is a real failure.
        assert surface.get("blocking") is False, surface
    raise AssertionError(f"{run_id} never reached a planner boundary")


def _complete(run_id: str, runs: Path, run_dir: Path) -> None:
    """Answer the planner boundary and wait for the orchestrator's own report."""
    _await_boundary(run_id, runs)
    subprocess.run(
        ["just", "channel-reply", run_id, "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        input=json.dumps({"completion": True, "reason": "the one node settled"}),
        text=True,
        capture_output=True,
        check=True,
        timeout=e2e_timeout(30),
    )
    _wait_for(run_dir / "orchestrator" / "report.json", lambda text: bool(text.strip()), 60)


def _example(name: str) -> dict[str, Any]:
    example = json.loads((EXAMPLES / name).read_text(encoding="utf-8"))
    assert len(example["tasks"]) == 1, f"{name} is the one-node form"
    return example


def _write(plan_path: Path, example: dict[str, Any]) -> Path:
    plan_path.write_text(json.dumps(example), encoding="utf-8")
    return plan_path


def test_the_shipped_one_node_direct_example_runs_to_completion(
    tmp_path: Path, onejudge_bin: str
) -> None:
    """The plan an operator copies for one direct dispatch, end to end."""
    example = _example("single-node-direct.plan.json")
    node = example["tasks"][0]
    target = tmp_path / "target"
    target.mkdir()
    node["project_dir"] = str(target)
    node["task"] = f"{node['task']}\n\ncomplete-now"
    runs = tmp_path / "runs"

    run_id = _launch(tmp_path, _write(tmp_path / "direct.plan.json", example), runs, onejudge_bin)
    run_dir = runs / run_id
    with _reaped(run_dir):
        settled = _settled(run_dir / "events.jsonl", node["id"])

        assert settled["kind"] == "node-settled", settled
        assert settled["detail"]["status"] == "done", settled
        _complete(run_id, runs, run_dir)


def test_the_shipped_one_node_lifecycle_example_runs_to_completion(
    tmp_path: Path, onejudge_bin: str, bare_origin
) -> None:
    """The plan an operator copies for one workstream: clone, gate, and merge.

    The example publishes to a repository only its author has, so the node's `repo`
    is redirected at a real local origin registered here — which is also what makes
    the merge observable, since a local single-owner identity merges into its own
    base rather than opening a PR nobody can approve in a test.
    """
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")
    example = _example("single-node-lifecycle.plan.json")
    node = example["tasks"][0]
    node["repo"] = str(canonical)
    node["repo_type"] = "single-owner"
    node["task"] = f"{node['task']}\n\ncomplete-now write-change"
    node["verify_cmd"] = ["test", "-f", "CHANGE.txt"]
    runs = tmp_path / "runs"

    plan = _write(tmp_path / "lifecycle.plan.json", example)
    run_id = _launch(tmp_path, plan, runs, onejudge_bin)
    run_dir = runs / run_id
    with _reaped(run_dir):
        settled = _settled(run_dir / "events.jsonl", node["id"])

        assert settled["kind"] == "node-settled", settled
        assert settled["detail"]["status"] == "done", settled
        assert settled["detail"]["result"]["outcome"] == "merged", settled
        # The merge really landed: the gate ran in the worktree and the base moved.
        change = (canonical / "CHANGE.txt").read_text(encoding="utf-8")
        assert change == "change from fake agent\n"
        _complete(run_id, runs, run_dir)
