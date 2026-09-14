"""Real-CLI regression coverage for oneharness timeouts: the kill, and who gets one.

Two things are proven here against the real CLI. The first is oneharness's own
process-tree termination, which only a real harness process can show. The second is
this repository's per-member deadline seam: the monitor's agent side watches for as
long as the run lasts inside one turn and must have NO deadline, while the
`check-in` pacemaker beside it must keep one. Both are read from the effective
configuration oneharness itself reports for the file `graphs/dag-scope.yaml` names,
so a re-shared config or a dropped setting fails here rather than silently killing
a watch at the pacemaker's deadline again.

The same read covers each side's **identity chain**, for the sides whose chain is the
reason they have a config at all. `graphs/design-doc.yaml`'s two are that case: this
host pairs Claude on the side that works with Codex on the side that supervises
everywhere else, and that one role reverses it — so a chain quietly restored to the
ordinary order would leave the graph, the personas catalog and the prose all describing
a reversal the files no longer carry.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

TIMEOUT_HARNESS = REPO_ROOT / "tests" / "e2e" / "timeout_harness.py"
DAG_SCOPE_GRAPH = REPO_ROOT / "graphs" / "dag-scope.yaml"
NODE_SCOPE_GRAPH = REPO_ROOT / "graphs" / "node-scope.yaml"

#: The dag-scope member that watches the run. It drives nothing — the engine does
#: that — so what it needs from its config is room to keep watching.
MONITOR_MEMBER = "monitor"

#: The pacemaker's per-turn deadline, asserted as the exact number
#: `oneharness.check-in.toml` states. A real value rather than a range: the point of
#: the seam is that this member has a *finite* deadline the monitor does not, and
#: a bound that only had to be "some number" would pass just as well at the release
#: default nobody chose. It is a wedged-turn backstop set clear of an honest survey,
#: not a work budget — the file says why, and what it is not.
PACEMAKER_DEADLINE_SECONDS = 240

#: The drafting graph and its one member, whose config is the third copy of the
#: supervisory routing. It is separate for the same reason the pacemaker's is: a
#: drafter sits between a passed gate and a publication, so an unbounded turn would
#: hold a verified branch unpublished for the life of the run.
PR_AUTHOR_GRAPH = REPO_ROOT / "graphs" / "pr-author.yaml"
PR_AUTHOR_MEMBER = "pr-author"

#: The drafter's per-turn deadline, as `oneharness.pr-author.toml` states it. Exact
#: for the same reason the pacemaker's is: what matters is that it is *finite* and
#: chosen, not that it is some number. Doubled when the drafter started from the
#: worker's description and the worker's transcript rather than the diff alone; the
#: measurement behind the number is beside it in that file.
DRAFTER_DEADLINE_SECONDS = 600

#: The plan reviewer's config, which is the fourth copy of the supervisory routing.
#: It is named here rather than read out of a graph because nothing dispatches it: it
#: is a command of this repository, run by `just review-plan`, and
#: `orchestrator/plan_review.py` is what names it. Separate for the same reason the
#: other two copies are, and for one more of its own — a reviewer sits between an
#: operator and a launch, and its `schema_file` is the verdict a review record is
#: written from.
PLAN_REVIEW_CONFIG = REPO_ROOT / "oneharness.plan-review.toml"

#: The reviewer's per-turn deadline, as that file states it. Exact for the same reason
#: the other two are: what matters is that it is finite and chosen.
REVIEWER_DEADLINE_SECONDS = 300

#: The design-doc role's own graph, and the member whose two sides it re-pairs. Its
#: configs are the fifth and sixth copies of this routing, and the only pair on this host
#: whose identity orders are the reverse of the ordinary one: Codex leads the side that
#: writes the document, the Claude subscriptions lead the side that reviews it. Separate
#: files for the reason every copy is separate — the sides that share an identity order
#: do not share a deadline — and separate from each *other* for the reversal itself.
DESIGN_DOC_GRAPH = REPO_ROOT / "graphs" / "design-doc.yaml"
DESIGN_DOC_MEMBER = "worker"

#: Their per-turn deadlines, as those two files state them. Exact for the same reason the
#: three above are, and different from each other because the turns are not the same
#: size: the writer reads a whole plan and composes a document, the reviewer reads one
#: document and answers. Both are backstops against a wedged turn rather than budgets.
DESIGN_DOC_WRITER_DEADLINE_SECONDS = 900
DESIGN_DOC_REVIEWER_DEADLINE_SECONDS = 600

#: The identity order each of those two sides must resolve. Held here as well as in
#: `tests/e2e/test_design_doc_graph_e2e.py`, and deliberately: that journey asks whether
#: the graph still routes each side to the file carrying its order, and this one asks
#: what the whole set of this host's turn configs resolves — a chain dropped from either
#: file is a lost quota, which is the property every config in the map below shares.
DESIGN_DOC_WRITER_CHAIN = [
    "codex:primary",
    "codex:alternate",
    "claude-code:alternate",
    "claude-code:alternate2",
    "claude-code:primary",
]
DESIGN_DOC_REVIEWER_CHAIN = [
    "claude-code:alternate",
    "claude-code:alternate2",
    "codex:primary",
    "codex:alternate",
    "claude-code:primary",
]

#: The follow-up agent's graph and its one single-sided member, whose config is the judge's
#: routing verbatim — chain order and every identity's model — with three chosen
#: differences: a finite deadline, because it runs after settlement with nothing watching
#: it; streaming, so its turns reach the views; and no mask on the board credential, since
#: its whole deliverable is the `followups` board. `oneharness.follow-up.toml` says why.
FOLLOW_UP_GRAPH = REPO_ROOT / "graphs" / "follow-up.yaml"
FOLLOW_UP_MEMBER = "worker"
FOLLOW_UP_DEADLINE_SECONDS = 3600

#: The credential that config alone leaves unmasked, and the one field besides the three
#: above it may differ from the judge's in: the history labels that say which side ran.
BOARD_CREDENTIAL = "GH_PROJECTS_TOKEN"
FOLLOW_UP_DIFFERENCES = {"timeout", "stream", "history_labels", "harness"}


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
    match node:
        case dict():
            return {key: _without_sources(value) for key, value in node.items() if key != "source"}
        case list():
            return [_without_sources(value) for value in node]
        case _:
            return node


def _unmasked(node: Any, name: str) -> Any:
    """``node`` with ``name`` taken out of every `unset_env` list it holds."""
    match node:
        case {"unset_env": {"value": list(masked)}, **rest}:
            kept = [one for one in masked if one != name]
            return {**_unmasked(rest, name), "unset_env": {"value": kept}}
        case dict():
            return {key: _unmasked(value, name) for key, value in node.items()}
        case _:
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


def test_every_side_resolves_its_intended_effective_deadline(
    oneharness_bin: str,
) -> None:
    """Prove all ten turn configs together from oneharness's effective values."""
    monitor = _named_config(DAG_SCOPE_GRAPH, MONITOR_MEMBER, "agent.oneharness_config")
    pacemaker = _named_config(DAG_SCOPE_GRAPH, "check-in", "oneharness_config")
    drafter = _named_config(PR_AUTHOR_GRAPH, PR_AUTHOR_MEMBER, "oneharness_config")
    writer = _named_config(DESIGN_DOC_GRAPH, DESIGN_DOC_MEMBER, "agent.oneharness_config")
    design_doc_reviewer = _named_config(
        DESIGN_DOC_GRAPH, DESIGN_DOC_MEMBER, "judge.oneharness_config"
    )
    follow_up = _named_config(FOLLOW_UP_GRAPH, FOLLOW_UP_MEMBER, "oneharness_config")
    own = {monitor, pacemaker, drafter, PLAN_REVIEW_CONFIG, writer, design_doc_reviewer, follow_up}
    assert len(own) == 7, (
        f"the monitor ({monitor}), the check-in pacemaker ({pacemaker}), the "
        f"pr-author drafter ({drafter}), the plan reviewer ({PLAN_REVIEW_CONFIG}), the "
        f"design-doc writer ({writer}), the design-doc reviewer "
        f"({design_doc_reviewer}) and the follow-up agent ({follow_up}) must each name "
        "their own oneharness config; re-sharing one is what gives a scheduled member no "
        "deadline, which fails silently"
    )

    configs = {
        "worker": _named_config(NODE_SCOPE_GRAPH, "worker", "agent.oneharness_config"),
        "judge": _named_config(NODE_SCOPE_GRAPH, "worker", "judge.oneharness_config"),
        "llmlint": REPO_ROOT / "oneharness.llmlint.toml",
        "monitor": monitor,
        "pacemaker": pacemaker,
        "drafter": drafter,
        "reviewer": PLAN_REVIEW_CONFIG,
        "design-doc writer": writer,
        "design-doc reviewer": design_doc_reviewer,
        "follow-up": follow_up,
    }
    effective = {
        side: _effective_config(oneharness_bin, config) for side, config in configs.items()
    }

    for side in ("worker", "judge", "llmlint"):
        assert effective[side]["timeout"] == {"value": None, "source": None}, (
            f"{side} must inherit oneharness 0.7's unbounded default"
        )
    assert effective["monitor"]["timeout"] == {
        "value": 0,
        "source": str(monitor),
    }, "the monitor's explicit timeout = 0 must continue to mean no deadline"
    assert effective["pacemaker"]["timeout"] == {
        "value": PACEMAKER_DEADLINE_SECONDS,
        "source": str(pacemaker),
    }, (
        f"the check-in pacemaker must retain its explicit finite "
        f"{PACEMAKER_DEADLINE_SECONDS}-second deadline"
    )
    assert effective["drafter"]["timeout"] == {
        "value": DRAFTER_DEADLINE_SECONDS,
        "source": str(drafter),
    }, (
        f"the pr-author drafter must retain its explicit finite "
        f"{DRAFTER_DEADLINE_SECONDS}-second deadline: it runs between a passed gate "
        "and a publication, so an unbounded turn holds a verified branch unpublished"
    )
    # The drafter is the one side here that answers under a response schema, and
    # oneharness validates a structured answer against the complete response — so a
    # `schema_file` member that also streams is refused outright. Holding both
    # together is what keeps a later "turn streaming back on for visibility" edit
    # from breaking the graph rather than only changing it.
    assert effective["drafter"]["stream"]["value"] is False, (
        "oneharness.pr-author.toml must keep `stream = false`: a schema run does not "
        "stream, and oneagentgraph refuses a member declaring both"
    )
    assert effective["drafter"]["schema_file"]["value"], (
        "oneharness.pr-author.toml must name the schema a drafted body is validated "
        "against; without it a turn's answer reaches publication unchecked"
    )
    assert effective["reviewer"]["timeout"] == {
        "value": REVIEWER_DEADLINE_SECONDS,
        "source": str(PLAN_REVIEW_CONFIG),
    }, (
        f"the plan reviewer must retain its explicit finite "
        f"{REVIEWER_DEADLINE_SECONDS}-second deadline: it runs between an operator and "
        "a launch, so an unbounded turn holds a plan unlaunchable indefinitely"
    )
    # The reviewer answers under a response schema too, and for the harder reason: a
    # record is written from that verdict, so an answer nothing validated would be
    # recorded as a pass. Holding both together keeps a later "turn streaming back on
    # for visibility" edit from breaking the command rather than only changing it.
    assert effective["reviewer"]["stream"]["value"] is False, (
        "oneharness.plan-review.toml must keep `stream = false`: a schema run does not "
        "stream, and oneharness refuses a run declaring both"
    )
    assert effective["reviewer"]["schema_file"]["value"], (
        "oneharness.plan-review.toml must name the schema a verdict is validated "
        "against; without it an unvalidated answer is recorded as a review"
    )

    for side, deadline_seconds, chain in (
        ("design-doc writer", DESIGN_DOC_WRITER_DEADLINE_SECONDS, DESIGN_DOC_WRITER_CHAIN),
        ("design-doc reviewer", DESIGN_DOC_REVIEWER_DEADLINE_SECONDS, DESIGN_DOC_REVIEWER_CHAIN),
    ):
        assert effective[side]["timeout"] == {
            "value": deadline_seconds,
            "source": str(configs[side]),
        }, (
            f"the {side} must retain its explicit finite {deadline_seconds}-second "
            "deadline: this role is a bounded job that reads its input, produces one "
            "document and stops, so an unbounded turn can only ever mean a wedged one"
        )
        assert effective[side]["harnesses"]["value"] == chain, (
            f"the {side} resolves "
            f"{effective[side]['harnesses']['value']}, not {chain}; this one role pairs "
            "Codex on the side that writes with Claude on the side that reviews — the "
            "reverse of every other pairing here — and every chain names all five "
            "identities with the primary Claude subscription last, so a dropped one is a "
            "quota this host loses entirely"
        )

    assert effective["follow-up"]["timeout"] == {
        "value": FOLLOW_UP_DEADLINE_SECONDS,
        "source": str(follow_up),
    }, (
        f"the follow-up agent must retain its explicit finite {FOLLOW_UP_DEADLINE_SECONDS}-"
        "second deadline: it runs after settlement with nothing watching it, so an unbounded "
        "turn can only ever mean a wedged verification left alive"
    )
    assert effective["follow-up"]["stream"]["value"] is True, (
        "oneharness.follow-up.toml must stream, so the follow-up agent's turns reach the views"
    )
    assert effective["follow-up"]["harnesses"]["value"] == effective["judge"]["harnesses"]["value"]
    follow_up_routing = _without_sources(effective["follow-up"])
    judge_routing = _without_sources(effective["judge"])
    differing = {
        field
        for field in follow_up_routing
        if field != "config_files" and follow_up_routing[field] != judge_routing[field]
    }
    assert differing <= FOLLOW_UP_DIFFERENCES, (
        "oneharness.follow-up.toml must be oneharness.judge.toml's routing with only the "
        f"deadline, streaming, labels and board-credential masks changed; these also differ: "
        f"{sorted(differing - FOLLOW_UP_DIFFERENCES)}"
    )
    assert _unmasked(judge_routing["harness"], BOARD_CREDENTIAL) == follow_up_routing["harness"], (
        "every identity of oneharness.follow-up.toml must carry oneharness.judge.toml's model, "
        "environment and masks exactly, less the mask on the board credential alone"
    )
    assert BOARD_CREDENTIAL not in json.dumps(follow_up_routing["harness"]), (
        "an identity of oneharness.follow-up.toml still masks the board credential, which "
        "the follow-up agent copies its tickets onto the board with"
    )

    # The split duplicated a routing, so hold the copy to one intended difference.
    # Anything else that drifts here is a pacemaker quietly authenticating, billing,
    # or reporting differently from the process it reports on.
    differing = {
        field
        for field in _without_sources(effective["monitor"])
        if field != "config_files"
        and _without_sources(effective["monitor"])[field]
        != _without_sources(effective["pacemaker"])[field]
    }
    assert differing == {"timeout"}, (
        f"oneharness.check-in.toml must be oneharness.orchestrator.toml's routing with "
        f"only the deadline changed; these also differ: {sorted(differing - {'timeout'})}"
    )


def test_bypass_sides_do_not_trip_the_approval_wait_safety_deadline(
    oneharness_bin: str,
) -> None:
    """Prove worker and monitor bypass modes cannot prompt headlessly.

    oneharness 0.7 retains a separate 120-second approval-wait safety deadline for
    prompt-capable headless modes. `bypass` must remain clean or the release's new
    unbounded turn default would not actually reach these live paths.
    """
    monitor = _named_config(DAG_SCOPE_GRAPH, MONITOR_MEMBER, "agent.oneharness_config")
    worker = _named_config(NODE_SCOPE_GRAPH, "worker", "agent.oneharness_config")
    assert _member_fields(DAG_SCOPE_GRAPH)[MONITOR_MEMBER].get("mode") == "bypass"
    assert _member_fields(NODE_SCOPE_GRAPH)["worker"].get("mode") == "bypass"

    catalogue = subprocess.run(
        [oneharness_bin, "list"], text=True, capture_output=True, timeout=e2e_timeout(30)
    )
    assert catalogue.returncode == 0, catalogue.stderr
    listed = json.loads(catalogue.stdout)
    headless = {
        harness["id"]: {mode["mode"]: mode["headless"] for mode in harness["modes"]}
        for harness in (listed["harnesses"] if isinstance(listed, dict) else listed)
    }

    chains = [
        _effective_config(oneharness_bin, config)["harnesses"]["value"]
        for config in (monitor, worker)
    ]
    families = {identity.split(":", 1)[0] for chain in chains for identity in chain}
    assert families, "worker and monitor configs name no harnesses"
    for family in sorted(families):
        assert headless.get(family, {}).get("bypass") == "clean", (
            f"{family} can block on an approval prompt in bypass mode, so removing the "
            f"deadline in {monitor.name} would make that wait unbounded"
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
