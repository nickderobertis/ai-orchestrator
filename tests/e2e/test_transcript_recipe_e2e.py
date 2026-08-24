"""What `just transcript` shows an operator of a dispatch that has already settled.

A settled node's evidence does not live in its worktree — that is reaped — but in the
run's own journal, which records every dispatched turn as `turn-activity` events
carrying each tool call *and* the output it returned. Nothing a manager reads by
default shows them: `just channel-next` and `just monitor` default to the `planner`
profile, which omits worker turns. `just transcript` is the read that reaches them,
and this journey is what holds the recipe and this repository's description of it to
what the adopted engine really renders.

The fixture is a real slice of a real run, and the run is the incident: node
`adopt-and-retire-gate` settled `failed` on its judge's verdict that the dispatch had
produced no installed-binary measurement, while its journal held 490 `turn-activity`
events and four of them carried that measurement's output verbatim. One of those four
is the tool call and result kept here.

`onepipeline` is the real one, because a read reaches no model and a doubled engine
would prove nothing about a render. The recipe, the wrapper script, and the shell are
real for the reason every journey here keeps them so.

llmlint: ignore-file[tool_output_is_signal] the rendered transcript is this viewing
command's whole product, so the assertions are on what it printed.
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

#: The recorded run, and the node that dispatched inside it. Both are the real ids, so
#: an operator meeting this journey can go and read the whole run it was cut from.
RECORDED_RUNS = REPO_ROOT / "tests" / "fixtures" / "transcript-runs"
RECORDED_RUN = "adopt-and-retire-gate"
RECORDED_NODE = "adopt-and-retire-gate"

#: A second recording, for the one thing a one-node run cannot show: that the optional
#: node argument narrows rather than being accepted and ignored. Its two nodes are a
#: retry pair, which is the shape a manager most often reads this verb at — two
#: dispatches of one piece of work, only one of which did the thing being looked for.
MULTI_NODE_RUN = "observatory-report-join"
MULTI_NODE_NODES = ("report-transcript-join", "report-transcript-join-2")

#: A distinctive fragment of the tool *call* the fixture keeps — the worker measuring
#: what its own engine binary links. Present in the payload's `detail`.
CALL_FRAGMENT = "LINKED CRATES"

#: A distinctive fragment of the *output* that call returned. It is in the same event,
#: under `output`, and it is the answer the judge said the dispatch never produced.
#: The version in it is the one the *recorded run* measured, and is deliberately not
#: derived from `config/`: this is a fixture of a run that happened, so re-deriving it
#: from today's pins would make the fixture stop being a recording.
RESULT_FRAGMENT = "onevcs-0.11.0"

#: What the renderer does with an output too large to print whole is stated in
#: `AGENTS.md` and gated by `tests/test_engine_contracts.py`, against `onepipeline`'s
#: own `MAX_PAYLOAD_TEXT_BYTES` and the three notes its renderer writes. It is not
#: driven here on purpose: no run this host has recorded produced an output past that
#: ceiling, so reaching one would mean writing synthetic records into a recorded
#: journal — a producer this repository does not have, standing in for one it does.
#: What this journey drives is the fixture as it was recorded.


@pytest.fixture
def runs_root(tmp_path: Path) -> Path:
    """A private copy, so no journey can write into the checked-in recording."""
    root = tmp_path / "runs"
    shutil.copytree(RECORDED_RUNS, root)
    return root


def _transcript(*arguments: str, runs_root: Path) -> subprocess.CompletedProcess[str]:
    """Run the real recipe against the recorded run."""
    environment = dict(os.environ)
    environment["ONEPIPELINE_RUNS_DIR"] = str(runs_root)
    # `scripts/onepipeline.sh` derives the reading session's identity from the harness
    # variable a real manager session carries.
    environment["CLAUDE_CODE_SESSION_ID"] = "transcript-recipe-e2e"
    return subprocess.run(
        ["just", "transcript", *arguments],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=e2e_timeout(120),
        check=False,
    )


def _recorded_outputs(runs_root: Path) -> list[str]:
    """Every tool result the recorded journal carries, as the journal carries it."""
    journal = (runs_root / RECORDED_RUN / "events.jsonl").read_text(encoding="utf-8")
    return [
        event["payload"]["output"]
        for event in (json.loads(line) for line in journal.splitlines() if line.strip())
        if event.get("kind") == "turn-activity" and event["payload"].get("kind") == "tool_result"
    ]


def test_the_recipe_renders_a_settled_dispatchs_turns(runs_root: Path) -> None:
    """The read reaches a dispatch whose worktree is long gone, by run and by node."""
    whole = _transcript(RECORDED_RUN, runs_root=runs_root)

    assert whole.returncode == 0, whole.stderr
    assert RECORDED_NODE in whole.stdout
    assert "turn 1" in whole.stdout
    assert CALL_FRAGMENT in whole.stdout, (
        f"the transcript must render the tool calls a dispatch made; it printed {whole.stdout!r}"
    )

    one_node = _transcript(RECORDED_RUN, RECORDED_NODE, runs_root=runs_root)

    assert one_node.returncode == 0, one_node.stderr
    assert one_node.stdout == whole.stdout


def test_the_node_argument_narrows_the_read_to_one_dispatch(runs_root: Path) -> None:
    """Reading one node of a run is reading one node, not the whole run again.

    The difference an operator feels is a retried node: both dispatches are under one
    run and one branch, and the question is nearly always about one of them.
    """
    first, second = MULTI_NODE_NODES

    whole = _transcript(MULTI_NODE_RUN, runs_root=runs_root)

    assert whole.returncode == 0, whole.stderr
    assert first in whole.stdout and second in whole.stdout

    narrowed = _transcript(MULTI_NODE_RUN, second, runs_root=runs_root)

    assert narrowed.returncode == 0, narrowed.stderr
    assert second in narrowed.stdout
    assert f"{MULTI_NODE_RUN}  {first}\n" not in narrowed.stdout, (
        f"reading node {second!r} rendered node {first!r} as well: {narrowed.stdout!r}"
    )


def test_the_recipe_names_a_run_or_node_it_has_nothing_for(runs_root: Path) -> None:
    """A read that answers nothing says so, rather than printing an empty transcript.

    The distinction matters here more than it usually would: this verb is reached
    precisely when somebody suspects a dispatch left no evidence, and a silent exit 0
    over a mistyped run id would confirm that suspicion falsely.
    """
    no_run = _transcript("no-such-run", runs_root=runs_root)

    assert no_run.returncode != 0
    assert "no-such-run" in no_run.stderr

    no_node = _transcript(RECORDED_RUN, "no-such-node", runs_root=runs_root)

    assert no_node.returncode != 0
    assert "no-such-node" in no_node.stderr
    # And it names what it does have, so the next command is typed rather than guessed.
    assert RECORDED_NODE in no_node.stderr


def test_the_rendered_result_lines_carry_the_output_the_journal_holds(
    runs_root: Path,
) -> None:
    """The measured render on the adopted engine: every call, and the answer it returned.

    This journey used to hold the opposite, and holding it is what made the change
    visible. Through onepipeline 0.11.0 the renderer printed a `turn-activity`
    payload's `detail`, and a `tool_result` payload carries its content under `output`
    and no `detail` at all — so every result rendered as a bare `tool_result` line with
    nothing after it, and a manager reading this verb for a dispatch's evidence saw the
    questions and none of the answers. onepipeline 0.12.1 fixed it and 0.13.0 carries
    that fix; this asserts what the adopted engine really prints for the fixture.

    Held as the equality it is rather than as "not blank", so a later release that
    truncated, elided, or summarized the output fails here rather than passing on a
    line that merely has something after the kind.
    """
    outputs = _recorded_outputs(runs_root)
    assert outputs, "the recorded fixture must carry a tool result for this to be about one"
    assert any(RESULT_FRAGMENT in output for output in outputs), (
        f"the recorded journal must carry {RESULT_FRAGMENT!r} as a tool result's output; "
        "that output is the evidence the dispatch was failed for not producing"
    )

    rendered = _transcript(RECORDED_RUN, runs_root=runs_root)

    assert rendered.returncode == 0, rendered.stderr
    assert RESULT_FRAGMENT in rendered.stdout, (
        "`just transcript` renders no tool outputs. AGENTS.md tells a manager this verb "
        "carries them, and the whole incident behind this fixture is a node failed for "
        f"want of evidence its own journal held: {rendered.stdout!r}"
    )
    result_lines = [
        line.strip().removeprefix("tool_result").strip()
        for line in rendered.stdout.splitlines()
        if line.strip().startswith("tool_result")
    ]
    assert result_lines, "a result the journal has must at least be listed"
    # Whole, not merely present: the journal's own text with control characters
    # replaced by spaces, which is the one transformation the renderer applies.
    expected = {" ".join(output.split()) for output in outputs}
    assert {" ".join(line.split()) for line in result_lines} == expected, (
        "a rendered result line is not the output the journal holds. If the engine "
        f"started bounding or summarizing these, update AGENTS.md in the same change: "
        f"{result_lines!r}"
    )
