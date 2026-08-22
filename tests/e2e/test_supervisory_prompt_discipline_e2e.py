"""What this host's two supervisory members are really told, read out of real launches.

Three defects in that prose each had a supervisor misinform the operator it exists to
inform: a message body the shell executed before the verb ever saw it, a monitor that
surfaced its own turn preamble as a planner update, and a pacemaker that could not tell
a bounded wait from a hang. Whether a model then writes a *good* finding is the paid
model's business and untestable here. Which prose the model is given is neither, and it
is the half that kept regressing — because the two members are given their prose by two
different mechanisms, and an editor who knows only one of them changes nothing:

* the **monitor** is `graphs/dag-scope.yaml`'s two-party `kind: onejudge` member, so its
  effective system prompt is `config/onejudge.base.yaml` merged with
  `personas/orchestrator.yaml`, and it is readable off a launched run's own turns;
* the **check-in** pacemaker is single-sided `kind: oneharness`, which layers no persona
  at all — its effective prompt is the member's own `task`, so editing
  `personas/check-in.yaml` alone changes nothing the model reads.

Each is therefore read at the seam its own shape reaches a provider through, and only
the paid model is substituted. The pacemaker gets its own launch rather than riding the
first one for a reason the design makes unavoidable: its schedule is resettable, so a
monitor — which raises a planner surface on every turn — restarts its clock before it
can ever come due, and a journey that waited for it on a supervised run would wait
forever. So its member is launched on its own, taken verbatim out of the shipped graph
rather than retyped, which is the only way its own turn is observable at all.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple, cast

import pytest
from fake_backend import AGENT_DELAY_ENV, JUDGE_CONFIG_NAME, PROMPT_LOG_ENV
from harness_indirections import established_indirections
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The stand-in for the paid model at the `oneharness` seam a two-party member reaches
#: it through, and the stand-in one layer lower for the provider a single-sided member's
#: in-library turn spawns. Both, because the two members here are one of each.
FAKE_BACKEND = Path(__file__).resolve().parent / "fake_backend.py"
FAKE_CODEX = Path(__file__).resolve().parent / "fake_codex.py"

#: The directory put ahead of everything on `PATH`, holding a `claude` that refuses the
#: turn — the identities `ONEHARNESS_BIN_*` cannot reach, since that seam keys on a
#: harness id and reaches no variant.
PAID_PROVIDER_GUARD = Path(__file__).resolve().parent / "no-paid-provider"

#: The variable `tests/e2e/fake_codex.py` records a single-sided member's prompt to.
CODEX_PROMPT_LOG_ENV = "FAKE_CODEX_PROMPT_LOG"

#: The shipped example the monitor's launch runs, and the run id `onepipeline` mints
#: from its `name`. A one-node plan: what is under test is the observer graph every
#: launch attaches, not anything the plan's own node does.
SHIPPED_PLAN = "examples/single-node-direct.plan.json"
SHIPPED_RUN = "scheduler-research"

#: How long the stand-in holds the dispatched worker's turn. The monitor's own turns
#: answer before reaching that delay, so this buys a window in which the monitor is
#: certain to have taken one without slowing the watch.
WORKER_HELD_SECONDS = 20

#: The shipped observer graph, and the member of it each half of this journey reads.
DAG_SCOPE_GRAPH = "graphs/dag-scope.yaml"
MONITOR_MEMBER = "monitor"
PACEMAKER_MEMBER = "check-in"

#: Who `harness_indirections` attributes an unresolvable alternate identity to.
INDIRECTION_CALLER = "tests/e2e/test_supervisory_prompt_discipline_e2e.py"

#: The monitor's role, as `personas/orchestrator.yaml` states it. The composed run task
#: says only what the run is, so this is what tells a monitor turn from any other.
WATCH_ROLE = "Actively monitor one executing tracked graph"

#: The task the pacemaker's own launch composes, stated here rather than copied from
#: `onepipeline`: the member replaces whatever it is handed and must interpolate it
#: back in, so any distinctive text proves the expansion.
PACEMAKER_TASK = "onepipeline run `supervisory-prompt-probe`."

#: The safe form a surface message must travel in, as both members are told to write it.
#: The single-quoted heredoc delimiter is the whole of the protection: bash performs no
#: expansion of any kind inside such a body, so no prose the agent writes can be
#: executed. Two delimiters because each member names its own, and neither is
#: interchangeable with a double-quoted argument.
SAFE_MESSAGE_FORM = "--message \"$(cat <<'"
HEREDOC_DELIMITERS = {MONITOR_MEMBER: "<<'FINDING'", PACEMAKER_MEMBER: "<<'UPDATE'"}

#: The reason the instruction has to carry, so a later editor cannot restore the
#: substituting form as a simplification without first contradicting it.
SUBSTITUTION_REASON = "command substitution"

#: The monitor's discipline on what a turn is allowed to say. A run queued twenty-eight
#: planner surfaces of which twenty-four were preambles, burying a worker's blocking
#: question for fifteen minutes, so a turn with nothing to report must produce nothing.
NARRATION_BAN = "never narrate what you are about to do"
SILENT_TURN = "with no prose at all"

#: The pacemaker's discipline on what it may call a hang. Both halves, because the three
#: false escalations in one session split evenly between them: an elapsed time read
#: without the bound it was running against, and an absence of events read as silence
#: while the dispatch's own heartbeat was live.
BOUND_RULE = "compared its elapsed time against a bound you located and can name"
HEARTBEAT_RULE = "absence of events beside a live heartbeat as generation"

#: What this node may not have changed, asserted from the same effective prompts: the
#: monitor's edit allowlist and the pacemaker's prohibition on issuing an edit at all.
#: A prompt edit that quietly widened either would be a change to what these agents may
#: *do*, which is a different decision from what they are told to report.
MONITOR_ALLOWLISTED_OPS = ("`context`", "`retry`", "`cancel`", "`requeue`", "`add`")
MONITOR_ALLOWLIST_CLAUSE = "You may issue exactly these five ops"
PACEMAKER_EDIT_PROHIBITION = "Never send `onepipeline reply`"


class Recorded(NamedTuple):
    """One turn as a stand-in recorded it, narrowed to what this journey reads."""

    config: str
    system: str
    prompt: str


def _environment(tmp_path: Path, oneharness_bin: str) -> dict[str, str]:
    """The environment both launches get, with the paid provider substituted twice."""
    environment = dict(os.environ)
    for name in ("ONEPIPELINE_LAUNCHER", "ONEPIPELINE_LAUNCHER_SESSION", "CODEX_THREAD_ID"):
        environment.pop(name, None)
    environment["CLAUDE_CODE_SESSION_ID"] = "supervisory-prompt-e2e"
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment.update(established_indirections(INDIRECTION_CALLER))
    # Keeps this journey's graph scratch and history out of the host's, so it never
    # reads or reclaims a live dispatch's.
    environment["XDG_STATE_HOME"] = str(tmp_path / "state")
    return environment


def _recorded(log: Path) -> list[Recorded]:
    """Every turn a stand-in recorded, as JSONL it wrote itself.

    Test-owned on both ends — `fake_backend.py` and `fake_codex.py` are this suite's
    files — so this states their schema rather than validating somebody else's.
    """
    if not log.is_file():
        return []
    written = [
        cast(dict[str, str], json.loads(line))
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return [
        Recorded(
            config=turn.get("config") or "",
            system=turn.get("system") or "",
            prompt=turn.get("prompt") or "",
        )
        for turn in written
    ]


def _flat(prompt: str) -> str:
    """One line of the prompt, so an assertion is not a claim about where it wrapped."""
    return " ".join(prompt.split())


@pytest.fixture(scope="module")
def monitor_prompt(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Iterator[str]:
    """The monitor's effective system prompt, off a run launched through the real recipe.

    Everything between `just orchestrate` and the model is real here: the driver, the
    observer graph, `oneagentgraph`, the onejudge conversation, and the base config the
    persona merges into. The worker's turn is held only so the run is certainly still
    alive when the monitor takes its first one.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp_path = tmp_path_factory.mktemp("monitor-prompt")
    environment = _environment(tmp_path, oneharness_bin)
    prompt_log = tmp_path / "prompts.jsonl"
    environment[PROMPT_LOG_ENV] = str(prompt_log)
    environment[AGENT_DELAY_ENV] = str(WORKER_HELD_SECONDS)

    launch = subprocess.run(
        ["just", "orchestrate", SHIPPED_PLAN],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
        check=False,
    )
    try:
        watching = [
            turn
            for turn in _recorded(prompt_log)
            # An agent side, by the one property that separates the two: the judge side
            # is the turn pinned to the judge config.
            if WATCH_ROLE in turn.system and Path(turn.config).name != JUDGE_CONFIG_NAME
        ]
        assert watching, (
            "no turn of this run carried the monitoring role, so nothing here reads an "
            f"effective monitor prompt:\n{launch.stdout}\n{launch.stderr}"
        )
        assert f"/members/{MONITOR_MEMBER}/" in watching[0].config, (
            f"the monitoring role arrived on a member other than `{MONITOR_MEMBER}` of "
            f"{DAG_SCOPE_GRAPH}: {watching[0].config}"
        )
        yield watching[0].system
    finally:
        subprocess.run(
            ["just", "stop", SHIPPED_RUN],
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            timeout=e2e_timeout(60),
            check=False,
        )


def _shipped_pacemaker_member() -> str:
    """The `check-in` member of the shipped graph, verbatim, with its schedule removed.

    Verbatim because retyping the member would prove a copy rather than the document an
    operator's runs actually attach; the schedule goes because it is half an hour, and
    what this fixture needs is the turn rather than the wait. Refs are rewritten from
    `../` to absolute, which is what they already resolve to: `oneagentgraph` resolves a
    relative ref against the directory the document was read from, and this copy is read
    from somewhere else.

    Read with a reader written for this one block rather than with a YAML library — the
    workspace installs none, and `oneagentgraph validate` on the real document is what
    holds it well-formed. Read as text at the same time: a graph the reader below could
    not find its member in would otherwise silently probe an empty document.
    """
    lines = (REPO_ROOT / DAG_SCOPE_GRAPH).read_text(encoding="utf-8").splitlines()
    opened = next(
        (index for index, line in enumerate(lines) if line.strip() == f"{PACEMAKER_MEMBER}:"),
        None,
    )
    assert opened is not None, (
        f"{DAG_SCOPE_GRAPH} declares no `{PACEMAKER_MEMBER}` member, so this journey "
        "is reading a document that has moved on without it"
    )
    indent = len(lines[opened]) - len(lines[opened].lstrip())
    block = [lines[opened]]
    for line in lines[opened + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        block.append(line)
    kept = [line for line in block if not line.strip().startswith("schedule:")]
    resolved = "\n".join(kept).replace("../", f"{REPO_ROOT}/")
    assert "task: |" in resolved, (
        f"the `{PACEMAKER_MEMBER}` member no longer claims its own `task`, which is the "
        "only prose a single-sided member is given"
    )
    return resolved


@pytest.fixture(scope="module")
def pacemaker_prompt(
    tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str
) -> Iterator[str]:
    """The pacemaker's whole effective prompt, off a real run of the shipped member.

    Its own launch, because on a supervised run it never comes due — the monitor resets
    its resettable clock on every turn — and because `oneagentgraph` is the party that
    composes this prompt: the member's `task` with `{task}` expanded, its persona
    contributing nothing, and `oneharness.check-in.toml` selecting the provider. The
    prompt is read out of the member's own report, which is where a library turn records
    what it composed, and is a better witness than a log because it is the producing
    library's own record.
    """
    tmp_path = tmp_path_factory.mktemp("pacemaker-prompt")
    environment = _environment(tmp_path, oneharness_bin)
    environment[CODEX_PROMPT_LOG_ENV] = str(tmp_path / "codex-prompts.jsonl")

    declared = next(
        line
        for line in (REPO_ROOT / DAG_SCOPE_GRAPH).read_text(encoding="utf-8").splitlines()
        if line.startswith("version:")
    )
    graph = tmp_path / "pacemaker-probe.yaml"
    graph.write_text(
        f"{declared}\nname: supervisory-prompt-probe\nmembers:\n{_shipped_pacemaker_member()}\n",
        encoding="utf-8",
    )

    ran = subprocess.run(
        ["oneagentgraph", "run", str(graph), "--task", PACEMAKER_TASK, "--dir", str(tmp_path)],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        check=False,
    )
    assert ran.returncode == 0, (
        f"the shipped `{PACEMAKER_MEMBER}` member did not run to a settlement:\n"
        f"{ran.stdout}\n{ran.stderr}"
    )
    settled = [
        json.loads(line)
        for line in ran.stdout.splitlines()
        if line.startswith("{") and json.loads(line)["kind"] == "member-settled"
    ]
    assert settled, f"the pacemaker member never settled:\n{ran.stdout}\n{ran.stderr}"
    report = json.loads(Path(settled[0]["payload"]["report_path"]).read_text(encoding="utf-8"))
    assert PACEMAKER_TASK in report["prompt"], (
        "the member was given a prompt that never expanded `{task}`, so this is not the "
        f"prompt a launched run composes:\n{report['prompt']}"
    )
    yield cast(str, report["prompt"])


@pytest.mark.xdist_group("supervisory-prompts")
def test_the_monitor_is_told_to_send_a_surface_message_through_a_quoted_heredoc(
    monitor_prompt: str,
) -> None:
    """The monitor's own prose can no longer be executed by the shell that surfaces it.

    `onepipeline surface` takes its text as an argument, so an instruction to pass a
    finding as a command-line word is an instruction to hand agent-authored prose to
    bash: inside double quotes backticks and `$(...)` are command substitution. This
    host measured the consequence — a surface quoting a command name was read back with
    that command replaced by its own output, having spent twenty-five minutes of a
    shared host's CPU on work nobody requested, with the mutation as the only trace.
    """
    flat = _flat(monitor_prompt)

    assert SAFE_MESSAGE_FORM in flat, (
        "the monitor is no longer told to send a surface message through a quoted "
        f"heredoc, so its findings are shell input again:\n{monitor_prompt}"
    )
    assert HEREDOC_DELIMITERS[MONITOR_MEMBER] in flat, (
        "the heredoc the monitor is told to use no longer has a single-quoted "
        f"delimiter, which is the whole of what stops the expansion:\n{monitor_prompt}"
    )
    # Every mention, not merely one: an instruction that also offers the argument form
    # is an instruction to use the argument form, and that is how this came back.
    assert flat.count("--message") == flat.count(SAFE_MESSAGE_FORM), (
        "the monitor's prompt still passes a surface message some other way than "
        f"through the quoted heredoc:\n{monitor_prompt}"
    )
    assert SUBSTITUTION_REASON in flat, (
        "the instruction no longer says why, so a later editor can restore the "
        f"substituting form as a simplification:\n{monitor_prompt}"
    )


@pytest.mark.xdist_group("supervisory-prompts")
def test_the_monitor_is_told_to_report_findings_rather_than_narrate_intent(
    monitor_prompt: str,
) -> None:
    """A turn with nothing to report owes the planner nothing, and is told so.

    The monitor's reply text is raised as a planner update, so a turn spent saying what
    it is about to look at costs a surface and carries no finding. One run queued
    twenty-eight of them, twenty-four content-free, and a worker's blocking question sat
    unread behind that pile for fifteen minutes with the frontier stopped — while `N
    planner update(s) waiting`, the one line a planner may never filter, said only that
    there were twenty-eight.
    """
    flat = _flat(monitor_prompt)

    assert NARRATION_BAN in flat, (
        "the monitor is no longer told to stop narrating what it is about to do, so "
        f"every turn it takes can still cost the planner a surface:\n{monitor_prompt}"
    )
    assert SILENT_TURN in flat, (
        "the monitor is no longer told that a turn with nothing to report emits "
        f"nothing:\n{monitor_prompt}"
    )


@pytest.mark.xdist_group("supervisory-prompts")
def test_the_pacemaker_is_told_to_send_its_update_through_a_quoted_heredoc(
    pacemaker_prompt: str,
) -> None:
    """The same defect, in the member whose prose actually executed a command here.

    Read off the pacemaker's own prompt rather than off `personas/check-in.yaml`,
    because that file reaches this member only as a label: a single-sided
    `kind: oneharness` member layers no persona, so the `task` below is the whole of
    what the model is given and the only copy that can be relied on.
    """
    flat = _flat(pacemaker_prompt)

    assert SAFE_MESSAGE_FORM in flat, (
        "the pacemaker is no longer told to send its update through a quoted heredoc, "
        f"so its update text is shell input again:\n{pacemaker_prompt}"
    )
    assert HEREDOC_DELIMITERS[PACEMAKER_MEMBER] in flat, (
        "the heredoc the pacemaker is told to use no longer has a single-quoted "
        f"delimiter, which is the whole of what stops the expansion:\n{pacemaker_prompt}"
    )
    assert flat.count("--message") == flat.count(SAFE_MESSAGE_FORM), (
        "the pacemaker's prompt still passes its update some other way than through "
        f"the quoted heredoc:\n{pacemaker_prompt}"
    )
    assert SUBSTITUTION_REASON in flat, (
        "the instruction no longer says why, so a later editor can restore the "
        f"substituting form as a simplification:\n{pacemaker_prompt}"
    )


@pytest.mark.xdist_group("supervisory-prompts")
def test_the_pacemaker_is_told_to_measure_a_bound_before_calling_anything_a_hang(
    pacemaker_prompt: str,
) -> None:
    """Elapsed time is not a verdict without the bound it was running against.

    Three escalations from this member in one session were false, and two of them are
    these: a command reported as unable to advance was 3m25s into a 360s bound and
    completed on its own at 360.068s, and "no turn activity for over two minutes" was a
    model mid-generation with a live heartbeat. A supervisor whose alarms are usually
    wrong stops being read, which costs more than the alarm was worth.
    """
    flat = _flat(pacemaker_prompt)

    assert BOUND_RULE in flat, (
        "the pacemaker may call something a hang again without naming the bound it "
        f"measured against:\n{pacemaker_prompt}"
    )
    assert HEARTBEAT_RULE in flat, (
        "the pacemaker is no longer told to read an absence of events beside a live "
        f"heartbeat as generation rather than as silence:\n{pacemaker_prompt}"
    )


@pytest.mark.xdist_group("supervisory-prompts")
def test_neither_members_action_space_moved_with_its_reporting_discipline(
    monitor_prompt: str, pacemaker_prompt: str
) -> None:
    """What these agents may *do* is unchanged by what they were told about reporting.

    The two are separate decisions and only one of them was made here. The monitor's
    five ops are the engine's allowlist restated to the model it bounds, and the
    pacemaker is forbidden the edit verb outright because it takes one finitely
    deadlined turn and exits — an edit it issued would be answered after it had stopped
    watching. A prompt edit that widened either would be invisible in the prose it
    shipped beside.
    """
    flat_monitor = _flat(monitor_prompt)

    assert MONITOR_ALLOWLIST_CLAUSE in flat_monitor, (
        f"the monitor's effective prompt no longer states its op allowlist:\n{monitor_prompt}"
    )
    for operation in MONITOR_ALLOWLISTED_OPS:
        assert operation in flat_monitor, (
            f"the monitor's allowlist no longer offers {operation}, so this change "
            f"moved its action space as well as its reporting:\n{monitor_prompt}"
        )
    for refused in ("`complete`", "`attest`", "`drop`", "`reparent`"):
        assert f"{refused}" in flat_monitor, (
            f"the monitor is no longer told {refused} is not its to issue:\n{monitor_prompt}"
        )
    assert PACEMAKER_EDIT_PROHIBITION in _flat(pacemaker_prompt), (
        "the pacemaker's prohibition on issuing a live edit is gone from the only prose "
        f"it is given:\n{pacemaker_prompt}"
    )
