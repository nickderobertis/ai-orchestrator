"""Per-node and run-wide graph overrides reach the dispatch they name, as documented.

`docs/onejudge-integration.md`'s *Choosing a harness per side* is the method a manager
follows to give one node, or a running run, another oneharness config: a short `extends`
child of the role file, named by a node's `onepipeline.sets`, replaced live with the
`set-node-sets` / `set-run-node-sets` edits, or given launch-wide by a launch config,
`ONEPIPELINE_NODE_SETS` or `--node-set`. Every one of those is the installed engine's
behaviour rather than this host's, so what is proven here is that the documented
spellings, driven through `just orchestrate` and `just channel-reply`, reach the
dispatch they are meant to and no other.

The child config and the edit envelope are read out of that document rather than
restated, so the example a manager copies is the one this journey ran. Each child adds
one `[env] MOCK_STDOUT` to what the document writes — the variable oneharness's mock
responder answers with, which a config's `[env]` hands the provider — so the sentence a
node's transcript carries says which config its agent side ran under.

Only the paid provider is substituted, at the seam every launch journey here substitutes
it: `tests/e2e/fake_backend.py` delegates each turn to the real `oneharness` CLI with
`--mock-harness`, so the config chain is resolved by the real loader.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] Launches of the installed
engine, sharing the fixtures and stand-ins of the code-keyed tier every other launch
journey in this directory sits in.

llmlint: ignore-file[shell_test_tiers_stay_split] These are pytest journeys over the real
`just` recipes rather than a shell suite.

llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] No marker selects a tier
here beyond the xdist group that keeps these launches off the toolchain lock.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, NamedTuple, cast

import pytest
from fake_backend import TURN_GATE_ENV, TURN_GATE_REACHED, TURN_GATE_RELEASED, WORKER_REPLY
from nx_workspace import SHARED_TOOLCHAIN_GROUP
from project_fixtures import project_from_plan
from test_orchestrate_launch_e2e import _environment as _launched_environment
from waits import timeout as e2e_timeout
from waits import until

from orchestrator.root import REPO_ROOT

#: Every launch here blocks on a `just` recipe that blocks on `uv run`, which waits on
#: this checkout's `.venv` lock; `tests/e2e/nx_workspace.py` names that constraint. And
#: the child config and edit envelope are read out of `docs/onejudge-integration.md`, so
#: this module runs in the tier whose key covers that document.
pytestmark = [pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP), pytest.mark.reads_docs]

DOC = REPO_ROOT / "docs" / "onejudge-integration.md"

CHILD_HEADER = "# scratch/oneharness/worker-codex-first.toml"

ROLE_FILE = REPO_ROOT / "oneharness.toml"
DOCUMENTED_EXTENDS = 'extends = "../../oneharness.toml"'

AGENT_CONFIG = "members.worker.agent.oneharness_config"

NODE_SETS_ENV = "ONEPIPELINE_NODE_SETS"
LAUNCH_CONFIG_SCHEMA = 10

SETTLING_SECONDS = 300


def _fenced_blocks(language: str) -> list[str]:
    """Every fenced block of `language` in the document, in order."""
    return re.findall(rf"^```{language}\n(.*?)^```$", DOC.read_text("utf-8"), re.M | re.S)


def documented_child() -> str:
    """The `extends` child the document tells a manager to write."""
    (child,) = [block for block in _fenced_blocks("toml") if block.startswith(CHILD_HEADER)]
    return child


def documented_edits() -> dict[str, Any]:
    """The edit envelope the document tells a manager to send, carrying both ops."""
    (envelope,) = [block for block in _fenced_blocks("json") if '"set-run-node-sets"' in block]
    # `cast` rather than a validating read: the envelope is sent to the engine verbatim,
    # which is what validates it, and the journey asserts on the engine's outcome.
    return cast(dict[str, Any], json.loads(envelope))


def _child(directory: Path, answer: str) -> Path:
    """Write the documented child in `directory`, answering `answer` on every turn.

    The `extends` line is re-pointed at the checkout's own role file from where this copy
    sits, because `extends` resolves against the declaring file's directory; everything
    else the document states is written as it states it.
    """
    directory.mkdir(parents=True, exist_ok=True)
    text = documented_child()
    assert DOCUMENTED_EXTENDS in text, f"the documented child no longer reads {DOCUMENTED_EXTENDS}"
    relative = os.path.relpath(ROLE_FILE, directory)
    text = text.replace(DOCUMENTED_EXTENDS, f"extends = {json.dumps(relative)}")
    text += f"\n[env]\nMOCK_STDOUT = {json.dumps(json.dumps({'result': answer}))}\n"
    written = directory / "worker.toml"
    written.write_text(text, encoding="utf-8")
    return written


def _just(
    *arguments: str,
    environment: dict[str, str],
    seconds: float = SETTLING_SECONDS,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one real recipe from this checkout, never raising on what it answered."""
    return subprocess.run(
        ["just", *arguments],
        cwd=REPO_ROOT,
        env=environment,
        input=stdin,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
    )


def _task(node: str, **fields: object) -> dict[str, object]:
    """One agent node that only reports: the config its turn ran under is the subject."""
    return {
        "id": node,
        "persona": "engineer",
        "task": (
            "## What\nReport.\n\n## Why\nThe answer the provider gives is the subject.\n\n"
            "## Acceptance criteria\n- Reported.\n"
        ),
        **fields,
    }


def _project(tmp_path: Path, run: str, tasks: list[dict[str, object]]) -> str:
    """Store a schema-3 plan — the first carrying `sets` — as a launchable project."""
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "name": run,
                "goal": {"text": "Dispatch nodes under graph overrides"},
                "tasks": tasks,
            }
        ),
        encoding="utf-8",
    )
    return project_from_plan(plan)


class Run(NamedTuple):
    """One launched run and the environment its views are read under."""

    run: str
    environment: dict[str, str]

    def transcript(self, node: str) -> str:
        """What `just transcript` renders for one node's dispatched turns."""
        rendered = _just("transcript", self.run, node, environment=self.environment, seconds=120)
        return rendered.stdout if rendered.returncode == 0 else ""

    def results(self) -> str:
        """What `just results` reports for the run."""
        reported = _just("results", self.run, environment=self.environment, seconds=120)
        return f"{reported.stdout}\n{reported.stderr}"


def _require_just() -> None:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")


PLANNED = "answered under the config the plan's own onepipeline.sets named"
NODE_EDIT = "answered under the config a set-node-sets edit named"
RUN_EDIT = "answered under the config a set-run-node-sets edit named"

FIRST, SECOND, THIRD = "first", "second", "third"


def _outcome(run: Run, runs_root: Path, envelope_id: int) -> dict[str, Any]:
    """The engine's answer to one command envelope, read through the bus."""
    streamed = subprocess.run(
        [
            str(REPO_ROOT / ".venv" / "bin" / "onemessagebus"),
            "subscribe",
            "command-outcomes",
            "--until",
            json.dumps({"field": "id", "equals": envelope_id}),
            "--timeout",
            "120",
            "--config",
            str(REPO_ROOT / "config" / "onemessagebus.yaml"),
            "--transport-dir",
            str(runs_root / run.run / "channel"),
        ],
        cwd=REPO_ROOT,
        env=run.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        check=False,
    )
    assert streamed.returncode == 0, f"no outcome for envelope {envelope_id}:\n{streamed.stderr}"
    # `cast` rather than a validating read: the record is the engine's command outcome,
    # and the members read are the ones asserted.
    return cast(dict[str, Any], json.loads(streamed.stdout.splitlines()[-1])["record"])


def _send(run: Run, envelope: dict[str, Any]) -> int:
    """Send one edit envelope through `just channel-reply`, answering its receipt id."""
    sent = _just(
        "channel-reply",
        run.run,
        environment=run.environment,
        seconds=120,
        stdin=json.dumps(envelope),
    )
    assert sent.returncode == 0, sent.stderr + sent.stdout
    # `cast` rather than a validating read: the receipt is the bus's own line, and the two
    # members read are asserted immediately below.
    receipt = cast(dict[str, Any], json.loads(sent.stdout.splitlines()[-1]))
    assert receipt.get("queue") == "commands", f"the edit was not queued: {sent.stdout}"
    return int(receipt["id"])


def test_plan_sets_and_both_live_edits_reach_the_next_dispatch_and_not_the_running_one(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """A node's own list, then both edits sent while another node's turn is in flight.

    `first` names a child through its task metadata; its worker turn is held at the
    fake backend's gate while the documented envelope replaces `second`'s own list and
    the run-wide list. What each node's transcript answers is the proof: `first`, in
    flight when the edits landed, keeps the config it launched with; `second` composes
    the run list and then its own, so its own wins; `third` has only the run list.
    An edit naming an unknown node is refused first and changes neither list.
    """
    _require_just()
    run_id = "graph-overrides-live"
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment = _launched_environment(tmp_path, oneharness_bin, session=f"e2e-{run_id}")
    gate = tmp_path / "turn-gate"
    gate.mkdir()
    environment[TURN_GATE_ENV] = str(gate)
    planned = _child(tmp_path / "planned", PLANNED)
    node_edit = _child(tmp_path / "node-edit", NODE_EDIT)
    run_edit = _child(tmp_path / "run-edit", RUN_EDIT)
    project = _project(
        tmp_path,
        run_id,
        [
            _task(FIRST, sets=[f"{AGENT_CONFIG}={planned}"]),
            _task(SECOND, deps=[FIRST]),
            _task(THIRD, deps=[FIRST]),
        ],
    )
    launched = _just(
        "orchestrate", project, "--dag-graph", "off", "--detach", environment=environment
    )
    assert launched.returncode == 0, f"the launch failed:\n{launched.stdout}\n{launched.stderr}"
    run = Run(run_id, environment)
    runs_root = Path(environment["ONEPIPELINE_RUNS_DIR"])
    try:
        until(
            f"{FIRST}'s worker turn to reach the gate",
            (gate / TURN_GATE_REACHED).exists,
            seconds=180,
            state=run.results,
        )

        refused = documented_edits()
        refused["commands"] = [{"op": "set-node-sets", "id": "no-such-node", "sets": []}]
        took = _outcome(run, runs_root, _send(run, refused))
        assert took["applied"] is False, f"an edit naming no node was applied: {took}"

        envelope = documented_edits()
        by_op = {command["op"]: command for command in envelope["commands"]}
        assert set(by_op) == {"set-node-sets", "set-run-node-sets"}, envelope
        by_op["set-node-sets"].update(id=SECOND, sets=[f"{AGENT_CONFIG}={node_edit}"])
        by_op["set-run-node-sets"].update(sets=[f"{AGENT_CONFIG}={run_edit}"])
        took = _outcome(run, runs_root, _send(run, envelope))
        assert took["applied"] is True, f"the documented edits were not applied: {took}"

        (gate / TURN_GATE_RELEASED).touch()
        until(
            "every node to answer",
            lambda: all(
                answer in run.transcript(node)
                for node, answer in ((FIRST, PLANNED), (SECOND, NODE_EDIT), (THIRD, RUN_EDIT))
            ),
            seconds=SETTLING_SECONDS,
            state=run.results,
            interval=2.0,
        )
        first = run.transcript(FIRST)
        assert RUN_EDIT not in first and NODE_EDIT not in first, (
            f"the turn in flight when the edits landed ran under an edited config:\n{first}"
        )
        second = run.transcript(SECOND)
        assert RUN_EDIT not in second, (
            f"{SECOND}'s own list did not win over the run-wide list composed before it:\n{second}"
        )
        assert PLANNED not in run.transcript(THIRD), (
            f"{THIRD} answered under {FIRST}'s own list, which is no node's but {FIRST}'s"
        )
    finally:
        (gate / TURN_GATE_RELEASED).touch()
        _just("stop", run_id, environment=environment, seconds=120)


FROM_FILE = "answered under the config the launch config's node_sets named"
FROM_ENV = "answered under the config ONEPIPELINE_NODE_SETS named"
FROM_FLAG = "answered under the config a --node-set flag named"
EVERY_SOURCE = (FROM_FILE, FROM_ENV, FROM_FLAG)


class Sources(NamedTuple):
    """Which launch-wide sources one launch names, and whose answer should win."""

    environment: list[str] | None
    flag: bool
    wins: str | None


PRECEDENCE = {
    "file-alone": Sources(environment=None, flag=False, wins=FROM_FILE),
    "environment-beats-file": Sources(environment=["env"], flag=False, wins=FROM_ENV),
    "flag-beats-environment": Sources(environment=["env"], flag=True, wins=FROM_FLAG),
    "empty-environment-clears-file": Sources(environment=[], flag=False, wins=None),
}


@pytest.mark.parametrize("case", sorted(PRECEDENCE))
def test_launch_wide_node_sets_take_the_flag_then_the_environment_then_the_file(
    case: str, tmp_path: Path, oneharness_bin: str
) -> None:
    """CLI > environment > file, wholesale per list, and `[]` in the environment clears.

    Every launch names the launch config's list; the environment and the flag are added
    per case. The one node's transcript says which list its dispatch composed, and the
    cleared case answers as the graph's own config does — the stand-in's reply.
    """
    _require_just()
    sources = PRECEDENCE[case]
    run_id = f"graph-overrides-{case}"
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment = _launched_environment(tmp_path, oneharness_bin, session=f"e2e-{run_id}")
    from_file = _child(tmp_path / "file", FROM_FILE)
    launch_config = tmp_path / "launch.yaml"
    launch_config.write_text(
        f"schema_version: {LAUNCH_CONFIG_SCHEMA}\n"
        f"node_sets: [{json.dumps(f'{AGENT_CONFIG}={from_file}')}]\n",
        encoding="utf-8",
    )
    if sources.environment is not None:
        listed = [f"{AGENT_CONFIG}={_child(tmp_path / 'env', FROM_ENV)}"]
        environment[NODE_SETS_ENV] = json.dumps(listed if sources.environment else [])
    flags: list[str] = []
    if sources.flag:
        flags = ["--node-set", f"{AGENT_CONFIG}={_child(tmp_path / 'flag', FROM_FLAG)}"]
    project = _project(tmp_path, run_id, [_task("only")])

    launched = _just(
        "orchestrate",
        project,
        "--dag-graph",
        "off",
        "--launch-config",
        str(launch_config),
        *flags,
        environment=environment,
    )
    run = Run(run_id, environment)
    try:
        assert launched.returncode == 0, (
            f"the launch did not settle:\n{launched.stdout}\n{launched.stderr}"
        )
        answered = run.transcript("only")
        expected = sources.wins or WORKER_REPLY
        assert expected in answered, (
            f"{case}: the dispatch did not answer {expected!r}:\n{answered}\n{run.results()}"
        )
        for other in EVERY_SOURCE:
            if other != sources.wins:
                assert other not in answered, (
                    f"{case}: a list that should have lost reached the dispatch: {other!r}"
                )
    finally:
        _just("stop", run_id, environment=environment, seconds=120)


def test_a_malformed_environment_list_is_refused_at_start_naming_the_variable(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The environment list is JSON and nothing else: a delimited list is refused."""
    _require_just()
    run_id = "graph-overrides-malformed"
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment = _launched_environment(tmp_path, oneharness_bin, session=f"e2e-{run_id}")
    environment[NODE_SETS_ENV] = f"{AGENT_CONFIG}=/one.toml,{AGENT_CONFIG}=/two.toml"
    project = _project(tmp_path, run_id, [_task("only")])

    launched = _just(
        "orchestrate", project, "--dag-graph", "off", environment=environment, seconds=120
    )

    assert launched.returncode != 0, f"a malformed {NODE_SETS_ENV} launched:\n{launched.stdout}"
    assert NODE_SETS_ENV in launched.stdout + launched.stderr, (
        f"the refusal did not name {NODE_SETS_ENV}:\n{launched.stdout}\n{launched.stderr}"
    )
    assert WORKER_REPLY not in Run(run_id, environment).transcript("only")
