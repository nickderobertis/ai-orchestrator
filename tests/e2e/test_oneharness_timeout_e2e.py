"""Real-CLI regression coverage for oneharness timeouts: the kill, and who gets one.

Two things are proven here against the real CLI. The first is oneharness's own
process-tree termination, which only a real harness process can show. The second is
this repository's per-member deadline seam: the orchestrator's agent side runs a
whole round inside one turn and must have NO deadline, while the `check-in`
pacemaker beside it must keep one. Both are read from the effective configuration
oneharness itself reports for the file `graphs/dag-scope.yaml` names, so a re-shared
config or a dropped setting fails here rather than silently killing rounds at 120
seconds again.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

TIMEOUT_HARNESS = REPO_ROOT / "tests" / "e2e" / "timeout_harness.py"
DAG_SCOPE_GRAPH = REPO_ROOT / "graphs" / "dag-scope.yaml"
NODE_SCOPE_GRAPH = REPO_ROOT / "graphs" / "node-scope.yaml"
#: oneharness's built-in per-turn deadline, in seconds. An absent `timeout` key
#: still resolves to this in the adopted release — deliberately upstream, so callers
#: relying on it as a backstop keep it — which is exactly why the one side that must
#: not have a deadline states `timeout = 0` rather than leaving the key out.
RELEASE_DEFAULT_TIMEOUT = 120


def _member_fields(graph: Path) -> dict[str, dict[str, str]]:
    """Map each graph member to its own scalar settings, nested side keys dotted.

    An indentation scan rather than a YAML dependency: these are this repository's
    own two graphs, flat two-level mappings, and the point of reading them at all is
    that the *wiring* decides which file each side loads. A test that restated the
    paths instead would keep passing after someone pointed both members back at one
    config, which is the failure this file exists to prevent.
    """
    fields: dict[str, dict[str, str]] = {}
    member: str | None = None
    side: str | None = None
    in_members = False
    for raw in graph.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        key, _, value = stripped.partition(":")
        value = value.strip()
        if indent == 0:
            in_members = stripped == "members:"
            member = side = None
        elif not in_members:
            continue
        elif indent == 2:
            member, side = key, None
            fields[member] = {}
        elif indent == 4 and member is not None:
            if value:
                fields[member][key] = value
                side = None
            else:
                side = key
        elif indent == 6 and member is not None and side is not None and value:
            fields[member][f"{side}.{key}"] = value
    assert fields, f"{graph} declares no members"
    return fields


def _named_config(graph: Path, member: str, field: str) -> Path:
    """Resolve one member's `oneharness_config`, relative to the graph's directory."""
    fields = _member_fields(graph)
    assert member in fields, f"{graph} no longer declares a `{member}` member"
    named = fields[member].get(field)
    assert named is not None, f"{graph}'s `{member}` member no longer names `{field}`"
    return (graph.parent / named).resolve()


def _effective_config(oneharness_bin: str, config: Path) -> dict[str, Any]:
    """Ask oneharness what a run loading exactly `config` would actually use.

    `ONEHARNESS_*` is dropped for the same reason the run below drops it: every
    worker verifies itself by running this suite from inside a dispatch, and an
    inherited `ONEHARNESS_TIMEOUT` beats every file, so the suite would be reading
    the enclosing dispatch's deadline instead of the one under test.
    """
    env = {key: value for key, value in os.environ.items() if not key.startswith("ONEHARNESS_")}
    proc = subprocess.run(
        [oneharness_bin, "config", "--config", str(config), "--compact"],
        env=env,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
    )
    assert proc.returncode == 0, proc.stderr
    resolved: dict[str, Any] = json.loads(proc.stdout)
    return resolved


def _without_sources(node: Any) -> Any:
    """Strip every `source` annotation so two effective configs compare by value.

    The annotations name the file each value came from, so two byte-identical
    routings read as different everywhere until they are dropped.
    """
    if isinstance(node, dict):
        return {key: _without_sources(value) for key, value in node.items() if key != "source"}
    if isinstance(node, list):
        return [_without_sources(value) for value in node]
    return node


def _assert_descendant_stopped(tick_file: Path) -> None:
    """Prove the fixture existed and cannot keep working after CLI return."""
    witness_deadline = deadline(2)
    while time.monotonic() < witness_deadline:
        witnessed = tick_file.stat().st_size if tick_file.exists() else 0
        if witnessed:
            break
        time.sleep(0.02)
    else:
        raise AssertionError("TERM-ignoring descendant never wrote its durable tick witness")

    time.sleep(0.3)
    assert tick_file.stat().st_size == witnessed, (
        "TERM-ignoring descendant kept ticking after oneharness returned"
    )


def test_timeout_kills_process_tree_and_preserves_real_partial_telemetry(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """Intentionally drive the real harness process tree; the mock cannot prove termination."""
    tick_file = tmp_path / "descendant.ticks"
    history_dir = tmp_path / "history"
    env = {key: value for key, value in os.environ.items() if not key.startswith("ONEHARNESS_")}
    env["TIMEOUT_HARNESS_TICK_FILE"] = str(tick_file)

    started = time.monotonic()
    proc = subprocess.run(
        [
            oneharness_bin,
            "run",
            "--harness",
            "opencode",
            "--prompt",
            "capture timeout evidence",
            "--timeout",
            "1",
            "--bin",
            f"opencode={TIMEOUT_HARNESS}",
            "--history",
            "--history-dir",
            str(history_dir),
            "--no-config",
            "--compact",
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(7),
    )
    elapsed = time.monotonic() - started

    assert proc.returncode == 1, proc.stderr
    assert elapsed >= 0.9, f"timeout returned before its configured deadline: {elapsed:.3f}s"
    assert elapsed < 3, f"timeout did not return near its deadline: {elapsed:.3f}s"
    # The public CLI report is intentionally schemaless here: this cross-version
    # boundary test probes additive nested fields without duplicating its contract.
    report: dict[str, Any] = json.loads(proc.stdout)
    result = report["results"][0]
    assert result["status"] == "timeout"
    assert result["exit_code"] is None
    assert result["text"] == "partial answer"
    assert result["text_source"] == "json:opencode-parts"
    assert result["usage"]["input_tokens"] == 12
    assert result["usage"]["output_tokens"] == 3
    assert result["usage"]["cache_read_tokens"] == 9
    assert result["usage"]["cache_write_tokens"] == 4
    assert result["usage"]["cost_usd"] == 0.01
    assert result["session_id"] == "ses-timeout"
    assert result["events_source"] == "json:opencode-parts"
    assert result["events"] == [
        {
            "kind": "tool_call",
            "name": "bash",
            "input": {"command": "echo hi"},
            "output": "hi",
            "index": 0,
            "tool_call_id": None,
            "started_at": None,
            "finished_at": None,
            "duration_ms": None,
            "status": None,
        }
    ]
    assert "native child stderr" in result["stderr"]
    assert result["stdout"].endswith('{"type":"incomplete"')

    # A timed-out transcript has no complete provider timing trace. Since oneharness
    # 0.6.7, failed runs are still durable: their history record carries the honest
    # partial timing and failure evidence instead of disappearing from diagnostics.
    history_file = Path(report["history_file"])
    assert history_file.exists()
    records = [
        entry
        for line in history_file.read_text(encoding="utf-8").splitlines()
        if (entry := json.loads(line))["type"] == "run"
    ]
    assert len(records) == 1
    assert records[0]["status"] == "timeout"
    # Status is the timeout signal; a candidate that ran but timed out carries no
    # launch-classification failure_kind in the normalized history contract.
    assert records[0]["failure_kind"] is None
    assert records[0]["error"]
    assert records[0]["started_at"]
    assert records[0]["finished_at"] is None
    assert "could not write history record" not in proc.stderr

    _assert_descendant_stopped(tick_file)


def test_the_orchestrator_agent_has_no_deadline_and_the_pacemaker_keeps_one(
    oneharness_bin: str,
) -> None:
    """The whole point of the split: one round-long turn, one bounded pacemaker."""
    orchestrator = _named_config(DAG_SCOPE_GRAPH, "orchestrator", "agent.oneharness_config")
    pacemaker = _named_config(DAG_SCOPE_GRAPH, "check-in", "oneharness_config")
    assert orchestrator != pacemaker, (
        "the orchestrator's agent side and the check-in pacemaker share "
        f"{orchestrator}; re-sharing one config is what gives a wedged pacemaker no "
        "deadline, which fails silently — give each member its own oneharness config"
    )

    driving = _effective_config(oneharness_bin, orchestrator)
    reporting = _effective_config(oneharness_bin, pacemaker)

    # Value AND source: a `0` that came from anywhere but this file would mean the
    # committed configuration is not what a run resolves.
    assert driving["timeout"] == {"value": 0, "source": str(orchestrator)}, (
        "the orchestrator's agent side must resolve to no deadline; one of its turns "
        "runs a whole round through `just run-plan` against a 28800s round budget, and "
        f"an absent key still resolves to {RELEASE_DEFAULT_TIMEOUT}s"
    )
    assert reporting["timeout"]["source"] == str(pacemaker)
    pacemaker_deadline = reporting["timeout"]["value"]
    assert isinstance(pacemaker_deadline, int) and pacemaker_deadline > 0, (
        "the check-in pacemaker must keep a finite deadline: it is scheduled every "
        "1800s, and a wedged turn that never dies costs every tick after it"
    )

    # The split duplicated a routing, so hold the copy to one intended difference.
    # Anything else that drifts here is a pacemaker quietly authenticating, billing,
    # or reporting differently from the process it reports on.
    differing = {
        field
        for field in _without_sources(driving)
        if field != "config_files"
        and _without_sources(driving)[field] != _without_sources(reporting)[field]
    }
    assert differing == {"timeout"}, (
        f"oneharness.check-in.toml must be oneharness.orchestrator.toml's routing with "
        f"only the deadline changed; these also differ: {sorted(differing - {'timeout'})}"
    )


@pytest.mark.parametrize(
    ("graph", "member", "field"),
    [
        (DAG_SCOPE_GRAPH, "orchestrator", "judge.oneharness_config"),
        (NODE_SCOPE_GRAPH, "worker", "agent.oneharness_config"),
        (NODE_SCOPE_GRAPH, "worker", "judge.oneharness_config"),
    ],
)
def test_every_other_side_still_takes_the_release_default_deadline(
    graph: Path, member: str, field: str, oneharness_bin: str
) -> None:
    """The judge side and every worker dispatch keep the backstop they have today.

    They are on oneharness's built-in default, not a value this repository states, and
    the seam above deliberately did not move them. Pinning `source` as well as the
    number is what makes that a fact rather than a coincidence: a `timeout` added to
    one of those files, or a release that moves its own default, fails here.
    """
    resolved = _effective_config(oneharness_bin, _named_config(graph, member, field))
    assert resolved["timeout"] == {"value": RELEASE_DEFAULT_TIMEOUT, "source": "default"}


def test_the_orchestrator_side_can_never_block_on_an_approval_prompt(
    oneharness_bin: str,
) -> None:
    """`timeout = 0` gives up oneharness's backstop against an unbounded approval wait.

    Nothing is traded away only while this side cannot be asked to approve anything,
    and that is a property of the mode plus the harnesses the chain names — so it is
    checked against both rather than assumed from `bypass` sounding safe.
    """
    orchestrator = _named_config(DAG_SCOPE_GRAPH, "orchestrator", "agent.oneharness_config")
    assert _member_fields(DAG_SCOPE_GRAPH)["orchestrator"].get("mode") == "bypass"

    catalogue = subprocess.run(
        [oneharness_bin, "list"], text=True, capture_output=True, timeout=e2e_timeout(30)
    )
    assert catalogue.returncode == 0, catalogue.stderr
    listed = json.loads(catalogue.stdout)
    headless = {
        harness["id"]: {mode["mode"]: mode["headless"] for mode in harness["modes"]}
        for harness in (listed["harnesses"] if isinstance(listed, dict) else listed)
    }

    chain = _effective_config(oneharness_bin, orchestrator)["harnesses"]["value"]
    families = {identity.split(":", 1)[0] for identity in chain}
    assert families, f"{orchestrator} names no harnesses"
    for family in sorted(families):
        assert headless.get(family, {}).get("bypass") == "clean", (
            f"{family} can block on an approval prompt in bypass mode, so removing the "
            f"deadline in {orchestrator.name} would make that wait unbounded"
        )


def test_a_config_timeout_of_zero_really_removes_the_deadline(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """Hold the adopted release to the spelling this repository's fix depends on.

    `timeout = 0` meaning "no deadline" is a per-release fact, and the effective
    configuration above would keep reporting `0` just as happily if a later release
    made that value mean "kill immediately" — leaving every round dead on arrival with
    a green suite. So the same TERM-ignoring fixture a 1-second deadline demonstrably
    kills above is run under a config whose only content is the zero, and it has to
    survive to its own natural end.
    """
    zero = tmp_path / "no-deadline.toml"
    zero.write_text("timeout = 0\n", encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if not key.startswith("ONEHARNESS_")}
    env["TIMEOUT_HARNESS_TICK_FILE"] = str(tmp_path / "descendant.ticks")

    started = time.monotonic()
    proc = subprocess.run(
        [
            oneharness_bin,
            "run",
            "--config",
            str(zero),
            "--harness",
            "opencode",
            "--prompt",
            "outlive a deadline this repository does not want",
            "--bin",
            f"opencode={TIMEOUT_HARNESS}",
            "--compact",
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
    )
    elapsed = time.monotonic() - started

    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)["results"][0]
    assert result["status"] == "ok", result
    # The fixture ticks for about five seconds; the sibling test above shows a
    # 1-second deadline kills it inside one. Surviving well past that is what says
    # the zero disabled the kill rather than merely being accepted.
    assert elapsed >= 3, f"the fixture did not outlive a short deadline: {elapsed:.3f}s"
