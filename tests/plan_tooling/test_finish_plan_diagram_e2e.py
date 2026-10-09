"""`just finish-plan` writes a design document whose Architecture shows a rendered diagram.

A diagram reaches a design document as an image asset, because a Linear board does not
render Mermaid code: the writer draws it with `just render-diagram`, references it as
`![<alt>](./<name>.png)`, and gives the file to the store with `--asset`. The tail's task
holds the writer to every referenced image being an asset of the document, and hands it
the brief's Why as source material to restate as impact rather than as what somebody wants.

The journey runs in a copy of this checkout, because the wrapper runs the engine at its
checkout's own `.venv/bin/onepipeline` and nowhere else. Everything there is real: the
real `scripts/finish-plan.sh`, review turn, check, copy and report, the real plan store at
both ends, the pinned engine's templates and checks, and the real `just render-diagram`
over the locked mermaid-cli and Chromium. **Only the engine's `start` is doubled**, by
`tests/plan_tooling/finish_plan_writer_engine.py`: it reads the writer's task the tail
composed and runs the commands the writer's turn runs, so no driver launches and nothing
reaches a remote — git is held to local transports for the whole flow.
`tests/e2e/fake_codex.py` still stands in for the paid provider of the review turn.

llmlint: ignore-file[e2e_not_mocked, tests_mirror_real_usage] The engine's `start` is
doubled because a real one launches a driver whose launch reads remotes over the network,
which stalled this journey on an unreachable host; every recipe, script, store, template and
render around it is the real one, and the double runs the writer's commands with them.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge, shell_test_tiers_stay_split] The
journey copies the tracked tree, which reads all of it, so it sits in `plan-tooling`'s
whole-workspace `test-docs` target, as `test_plan_spike_flow_e2e.py`'s journeys do.

llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] `reads_docs` chooses between
`plan-tooling`'s own two targets and routes nothing out of the project, as
`tests/plan_tooling/AGENTS.md` states.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from nx_workspace import answering_this_checkouts_origin, copy_checkout
from test_finish_plan_recipe_e2e import (
    DESIGN_RUN_SUFFIX,
    DESIGN_TASK_MARKER,
    DESTINATION,
    MERGES_THE_BUDGETS,
    OK,
    PASSES,
    Bench,
    Drafted,
    RunId,
    StoredTask,
    _bench,
    _brief,
    _draft,
    _staged_answers,
)
from test_plan_flow_e2e import _provisioned
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

pytestmark = [pytest.mark.reads_docs, pytest.mark.xdist_group("finish-plan-diagram")]

#: The stand-in `start` the copied checkout's engine hands that one verb to.
STAND_IN = Path(__file__).resolve().parent / "finish_plan_writer_engine.py"

#: The diagram the stand-in writer draws, and how its Architecture overview shows it.
DIAGRAM = (
    "flowchart LR\n"
    "  route[Listing route] -->|one page and a cursor| view[View]\n"
    "  view -->|the cursor back| route\n"
    "  route --> store[(Node store)]\n"
)
SHOWN = "![How the listing route, the view and the node store hand a page over](./architecture.png)"

#: The criterion the tail's task holds every image the document references to.
HELD_AS_ASSETS = "Every image the document references is held as an asset of the document."

#: The brief's Why as `_brief` writes it, which the tail hands over as source material.
BRIEF_WHY = "The view cannot deep-link until it is settled."


def _declared_page_width() -> int:
    """The page width `scripts/render-diagram.sh` declares, which no PNG it writes exceeds."""
    script = (REPO_ROOT / "scripts" / "render-diagram.sh").read_text(encoding="utf-8")
    found = re.search(r"^PAGE_WIDTH=(\d+)$", script, re.MULTILINE)
    assert found is not None, "scripts/render-diagram.sh declares no PAGE_WIDTH"
    return int(found.group(1))


PAGE_WIDTH = _declared_page_width()


def _checkout(tmp_path: Path) -> Path:
    """A provisioned copy of this checkout whose engine doubles `start` and nothing else.

    The wrapper is a shell script handing every call to the stand-in on its second line,
    followed by the real binary's bytes, which bash never reads: `just check-plan` reads the
    roles a persona name resolves to out of the installed engine's own bytes.
    """
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    copy_checkout(checkout)
    answering_this_checkouts_origin(checkout)
    _provisioned(checkout)
    engine = checkout / ".venv" / "bin" / "onepipeline"
    real = engine.with_name("onepipeline-real")
    engine.rename(real)
    engine.write_bytes(
        f"#!/usr/bin/env bash\nexec {shlex.quote(sys.executable)} "
        f'{shlex.quote(str(STAND_IN))} "$@"\n'.encode()
        + real.read_bytes()
    )
    engine.chmod(0o755)
    return checkout


def _writes_a_document_showing_a_diagram(bench: Bench, drafted: Drafted, checkout: Path) -> Path:
    """Script the commands the design-doc writer runs to show a diagram in Architecture.

    The answers are the model's; drawing the diagram with `just render-diagram`, showing it
    from the Architecture overview and giving the PNG with `--asset` is what its task and
    role tell it to do, run by the doubled `start` as the dispatch runs them, in the copied
    checkout with its own engine and store. Answers where the PNG is drawn.
    """
    drawn = bench.tmp_path / f"diagram-{drafted.project}"
    drawn.mkdir()
    keyed = bench.tmp_path / f"commands-{drafted.project}.json"
    answers = _staged_answers(bench.tmp_path / drafted.project, drafted)
    source, _, project = drafted.qualified.partition(":")
    bin_dir = checkout / ".venv" / "bin"
    keyed.write_text(
        json.dumps(
            {
                DESIGN_TASK_MARKER: [
                    [
                        "bash",
                        "-c",
                        "set -euo pipefail"
                        '; printed=$(uv run python -m orchestrator.plan_budgets "$3:$4")'
                        '; python3 "$8" "$7" "$printed"'
                        '; printf "%s" "${10}" > "$9/architecture.mmd"'
                        '; just --justfile "${12}/justfile" render-diagram'
                        ' "$9/architecture.mmd" "$9/architecture.png"'
                        "; python3 -c 'import json, sys; path, shown = sys.argv[1:]"
                        "; answers = json.load(open(path))"
                        '; answers["architecture"] += "\\n\\n" + shown'
                        '; json.dump(answers, open(path, "w"))\' "$7" "${11}"'
                        '; "$1" template resolve design-doc --json'
                        ' | "$2" document create "$3" --project "$4" --title "$5" --id "$6"'
                        ' --template-loader - --answers "$7" --no-interactive'
                        ' --asset "$9/architecture.png"',
                        "store-the-document-with-its-diagram",
                        str(bin_dir / "onepipeline"),
                        str(bin_dir / "onetaskgraph"),
                        source,
                        project,
                        f"Design: {drafted.project}",
                        drafted.document,
                        str(answers),
                        str(MERGES_THE_BUDGETS),
                        str(drawn),
                        DIAGRAM,
                        SHOWN,
                        str(checkout),
                    ]
                ]
            }
        ),
        encoding="utf-8",
    )
    bench.environment.update(
        {
            "FAKE_ENGINE_REAL": str(bin_dir / "onepipeline-real"),
            "FAKE_ENGINE_STORE": str(bin_dir / "onetaskgraph"),
            "FAKE_ENGINE_WRITER_TURN": str(keyed),
            "FAKE_ENGINE_LOG": str(bench.tmp_path / "starts.jsonl"),
            # The copy is provisioned once; a sync now would reinstall the doubled engine.
            "UV_NO_SYNC": "1",
            # Git may reach no remote: a read that would wait on the network refuses instead.
            "GIT_ALLOW_PROTOCOL": "file",
        }
    )
    return drawn / "architecture.png"


def _run(
    checkout: Path, bench: Bench, command: list[str], seconds: float
) -> subprocess.CompletedProcess[str]:
    """One command in the copied checkout, under this bench's environment."""
    return subprocess.run(
        command,
        cwd=checkout,
        env=bench.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
    )


def _store(checkout: Path, bench: Bench, *arguments: str) -> Any:
    """One command of the copy's pinned plan store, under this bench's sources, as JSON."""
    store = str(checkout / ".venv" / "bin" / "onetaskgraph")
    ran = _run(checkout, bench, [store, *arguments, "--json"], 120)
    assert ran.returncode == 0, (arguments, ran.stdout, ran.stderr)
    return json.loads(ran.stdout)


class Diagrammed(NamedTuple):
    """One whole tail over a plan whose document shows a diagram, and what was read back."""

    drawn: bytes
    design_task: StoredTask
    copied: dict[str, Any]
    starts: list[dict[str, Any]]


RUN = RunId("finish-plan-diagram-e2e")


@pytest.fixture(scope="module")
def diagrammed(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Diagrammed:
    """Drive the tail once, its writer drawing a diagram into the document's Architecture."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp_path = tmp_path_factory.mktemp("finish-plan-diagram")
    checkout = _checkout(tmp_path)
    bench = _bench(tmp_path, oneharness_bin, PASSES)
    drafted = _draft("finish-plan-diagram")
    png = _writes_a_document_showing_a_diagram(bench, drafted, checkout)
    brief = _brief(tmp_path, drafted, "diagram-listing")
    finish = _run(
        checkout,
        bench,
        ["just", "finish-plan", str(brief), "--name", RUN, "--to", DESTINATION],
        600,
    )
    assert finish.returncode == OK, f"the tail failed:\n{finish.stdout}\n{finish.stderr}"
    documents = _store(
        checkout, bench, "document", "list", "--source", DESTINATION, "--project", drafted.project
    )["items"]
    (design,) = [one for one in documents if str(one["id"]).endswith(drafted.document)]
    copied = _store(checkout, bench, "document", "show", str(design["id"]))
    (task,) = _store(
        checkout,
        bench,
        "task",
        "list",
        "--source",
        "authoring",
        "--project",
        f"{RUN}{DESIGN_RUN_SUFFIX}",
    )["items"]
    starts = [
        json.loads(line)
        for line in Path(bench.environment["FAKE_ENGINE_LOG"]).read_text("utf-8").splitlines()
    ]
    return Diagrammed(png.read_bytes(), task["item"], copied, starts)


def test_the_launch_reached_the_doubled_engine_alone_and_ran_the_writers_turn(
    diagrammed: Diagrammed,
) -> None:
    """The tail's one launch is the doubled `start`, which found the writer's task and ran it."""
    assert diagrammed.starts == [
        {
            "project": f"authoring:{RUN}{DESIGN_RUN_SUFFIX}",
            "ran": 1,
            "settlement": "complete",
        }
    ], diagrammed.starts


def _section(content: str, heading: str) -> str:
    return f"\n{content}".split(f"\n{heading}\n", 1)[1].split("\n## ", 1)[0]


def test_the_writers_task_holds_every_referenced_image_to_being_an_asset(
    diagrammed: Diagrammed,
) -> None:
    """The criterion generalizes the visual one: any picture the document shows is held."""
    criteria = _section(diagrammed.design_task["content"], "## Acceptance criteria")
    assert HELD_AS_ASSETS in " ".join(criteria.split()), criteria


def test_the_writers_task_hands_the_briefs_why_over_as_source_material_for_impact(
    diagrammed: Diagrammed,
) -> None:
    """The brief's Why is material to restate as impact, never what somebody wants relayed."""
    content = diagrammed.design_task["content"]
    what = " ".join(_section(content, "## What").split())
    why = " ".join(_section(content, "## Why").split())
    assert (
        "The brief’s own account of why the plan is worth doing, as source material for the "
        "document’s Why. Restate it as the work’s impact on the product, its users, "
        "development and resources, under the template’s guidance; never quote it and never "
        f"attribute it to anyone: {BRIEF_WHY}"
    ) in what, what
    assert "what the plan does for the product, its users, development and resources" in why
    assert BRIEF_WHY not in why, why
    assert "What the user wants" not in content, content


def test_the_copied_document_shows_the_rendered_diagram_and_holds_its_png(
    diagrammed: Diagrammed,
) -> None:
    """The writer did what its task and role say: the copy shows the image and holds it."""
    (item,) = diagrammed.copied["items"]
    architecture = _section(item["item"]["content"], "## Architecture")
    assert f"\n{SHOWN}\n" in f"\n{architecture}\n", architecture
    assert "```mermaid" not in item["item"]["content"]
    (asset,) = diagrammed.copied["assets"]
    assert asset["name"] == "architecture.png", asset
    held = Path(asset["path"]).read_bytes()
    assert held == diagrammed.drawn
    assert held.startswith(b"\x89PNG\r\n\x1a\n"), held[:8]
    width, height = struct.unpack(">II", held[16:24])
    assert 0 < width <= PAGE_WIDTH and height > 0, (width, height)
