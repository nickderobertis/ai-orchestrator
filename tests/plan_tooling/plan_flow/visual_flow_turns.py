"""The commands a visual plan's planner and visual spike run, as their scripted turns run them.

`tests/plan_tooling/plan_flow/test_visual_plan_flow_e2e.py` stands in for the paid model's
answers and for nothing downstream of them: each turn's decision is scripted here, and every
command it issues is the real one, run by `tests/e2e/fake_backend.py` where the dispatch
runs it.

* ``plan`` is the draft planner's turn: through the plan store's own command line it writes
  the plan's project, its one task rendered from `plan-task`, its plan-level budget answers,
  and the spikes project holding the reserved visual spike, a lifecycle node in the target
  repository declaring `preserve` and a `max_turns` of 40. A planner sent back, and the
  finalize planner whose task carries the same brief, find the plan written and write
  nothing, since nothing a spike reported changes it.
* ``spike`` is the visual spike's turn, in its session worktree: it commits the minimal mock
  — the views' seeds and an owner column — runs the target's committed capture command on
  the unchanged base for each "before", since the target holds no baseline screenshots, and
  on its own change for each "after", checks every capture is screenshot-sized before any is
  given to the store, and writes `<plan>-spike-visual-report` with each image given as `--asset`.
  It runs no test and no hook.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from visual_images import decoded_pixels

from orchestrator import spike_plan

COMMITTER = ["-c", "user.email=test@example.com", "-c", "user.name=ai-orchestrator-test"]


@dataclass(frozen=True)
class ViewChange:
    """One target view the mock changes, and the report's entry for it."""

    view: str
    name: str
    description: str

    def image(self, side: str) -> str:
        """The asset name of this view's ``before`` or ``after`` capture."""
        return f"{self.view}-{side}.png"


@dataclass(frozen=True)
class PlannedTask:
    """One task the draft planner writes: where, under what title, answered how, placed how."""

    project: str
    title: str
    answers: object
    node: str
    placing: tuple[str, ...] = ()


#: The target's two views and what the mock changes about each, in the report's order.
CHANGES = (
    ViewChange(
        view="listing",
        name="Node listing",
        description="The node listing gains a column naming each node's owner.",
    ),
    ViewChange(
        view="status",
        name="Run status line",
        description="The CLI's status line names the run instead of its id.",
    ),
)


#: Where a turn says why it failed: a dispatch's stderr reaches no record a journey reads.
FAILURES = "turn-failures.log"
_failures: Path | None = None


def _failed(said: str) -> SystemExit:
    if _failures is not None:
        with _failures.open("a", encoding="utf-8") as log:
            log.write(said + "\n")
    return SystemExit(said)


def _run(command: list[str], *, cwd: Path | None = None, stdin: str | None = None) -> str:
    done = subprocess.run(
        command, cwd=cwd, input=stdin, text=True, capture_output=True, check=False
    )
    if done.returncode != 0:
        raise _failed(f"visual turn: {command} failed:\n{done.stdout}{done.stderr}")
    return done.stdout


def _answers(answers: Mapping[str, object]) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as written:
        json.dump(answers, written)
    return written.name


def plan(store: str, engine: str, scenario: dict[str, object]) -> None:
    """Write the plan and its spikes project, once."""
    source, _, native = str(scenario["plan"]).partition(":")
    spikes = f"{native}-spikes"
    listed = json.loads(
        _run([store, "task", "list", "--source", source, "--project", native, "--json"])
    )
    if listed["items"]:
        if scenario.get("revision"):
            old = json.loads(
                _run([store, "task", "list", "--source", source, "--project", spikes, "--json"])
            )["items"][0]
            node = old["item"]["metadata"].get("onepipeline.id")
            if node != "spike-visual-revision":
                task = str(old["id"])
                spike_answers = scenario["spike_task"]
                assert isinstance(spike_answers, dict)
                _run(
                    [
                        store,
                        "task",
                        "metadata",
                        "set",
                        task,
                        "onepipeline.id",
                        json.dumps("spike-visual-revision"),
                    ]
                )
                _run(
                    [
                        store,
                        "task",
                        "render",
                        task,
                        "--template-loader",
                        "-",
                        "--answers",
                        _answers(spike_answers),
                        "--no-interactive",
                    ],
                    stdin=_run([engine, "template", "resolve", "plan-task", "--json"]),
                )
                _run([store, "task", "status", "set", task, "todo"])
        return
    for held, goal in ((native, "Show who owns each node"), (spikes, "Picture it first")):
        _run(
            [store, "project", "create", source, "--id", held, "--title", held]
            + ["--metadata", "onepipeline.schema_version=3"]
            + ["--metadata", f"onepipeline.goal={json.dumps({'text': goal})}", "--json"]
        )
    plan_task = _run([engine, "template", "resolve", "plan-task", "--json"])
    tasks = (
        PlannedTask(
            project=native,
            title="feat: show each node's owner",
            answers=scenario["task"],
            node='"build-the-column"',
        ),
        PlannedTask(
            project=spikes,
            title="feat(spikes): picture the owner column",
            answers=scenario["spike_task"],
            node=f'"{spike_plan.VISUAL_SPIKE}"',
            placing=(
                'onepipeline.publish="preserve"',
                "onepipeline.max_turns=40",
                f'onepipeline.execution_checkout="{scenario["execution"]}"',
            ),
        ),
    )
    for planned in tasks:
        assert isinstance(planned.answers, dict)
        metadata = [
            f"onepipeline.id={planned.node}",
            'onepipeline.persona="engineer"',
            'orchestrator.unit="Views"',
            *planned.placing,
        ]
        _run(
            [store, "task", "create", source, "--template-loader", "-", "--no-interactive"]
            + ["--project", planned.project, "--title", planned.title]
            + ["--answers", _answers(planned.answers)]
            + ["--repository", str(scenario["repository"])]
            + [flag for one in metadata for flag in ("--metadata", one)]
            + ["--json"],
            stdin=plan_task,
        )
    budgets = scenario["budgets"]
    assert isinstance(budgets, dict)
    _run(
        [store, "project", "create", source, "--id", native, "--title", native]
        + ["--template-loader", "-", "--answers", _answers(budgets)]
        + ["--metadata", f"orchestrator.plan-budgets={json.dumps(budgets)}"]
        + ["--no-interactive", "--json"],
        stdin=_run([engine, "template", "resolve", "plan-description", "--json"]),
    )


def _capture(tree: Path, out: Path, chromium: str, view: str = "") -> None:
    """Run the target's committed capture command from ``tree`` into ``out``."""
    out.mkdir(parents=True, exist_ok=True)
    done = subprocess.run(
        ["sh", "shots/capture.sh", str(out), view],
        cwd=tree,
        env={**os.environ, "CHROMIUM": chromium},
        text=True,
        capture_output=True,
        check=False,
    )
    if done.returncode != 0:
        raise _failed(f"visual turn: the capture in {tree} failed:\n{done.stderr}")


def spike(store: str, engine: str, scenario: dict[str, object]) -> None:
    """Commit the mock, capture each pair, check the sizes, and write the report."""
    worktree = Path.cwd()
    evidence = Path(str(scenario["evidence"]))
    evidence.mkdir(parents=True, exist_ok=True)
    based = evidence / "base.sha"
    if not based.exists():
        based.write_text(_run(["git", "rev-parse", "HEAD"], cwd=worktree).strip())
        views = json.loads((worktree / "shots" / "views.json").read_text(encoding="utf-8"))
        for view in views.values():
            view["seed"] += 1000
        views["listing"]["columns"] = ["name", "owner"]
        (worktree / "shots" / "views.json").write_text(json.dumps(views, indent=2) + "\n")
        _run(["git", "add", "-A"], cwd=worktree)
        _run(["git", *COMMITTER, "commit", "-qm", "feat: mock the owner column"], cwd=worktree)
    base = based.read_text().strip()
    captured = worktree / "shots" / "out"
    if captured.exists():
        shutil.rmtree(captured)
    chromium = str(scenario["chromium"])
    with tempfile.TemporaryDirectory() as unchanged:
        archive = subprocess.run(
            ["git", "archive", base], cwd=worktree, capture_output=True, check=True
        )
        with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tree:
            tree.extractall(unchanged, filter="data")
        _capture(Path(unchanged), captured / "before", chromium)
    _capture(worktree, captured / "after", chromium)
    images: dict[str, Path] = {}
    for change in CHANGES:
        for side in ("before", "after"):
            images[change.image(side)] = captured / side / f"{change.view}.png"
    sizes = {name: path.stat().st_size for name, path in images.items()}
    (evidence / "sizes.json").write_text(json.dumps(sizes))
    for path in images.values():
        assert list(decoded_pixels(path.read_bytes())[:2]) == scenario["dimensions"]
    kept = evidence / "captured"
    kept.mkdir(exist_ok=True)
    for name, path in images.items():
        shutil.copyfile(path, kept / name)
    report = {
        "spike": spike_plan.VISUAL_SPIKE,
        "branch": _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=worktree).strip(),
        "harness": "`shots/capture.sh`; run `sh shots/capture.sh <out>` to re-capture.",
        "method": "Each view mocked over seeded fixture data, captured in headless Chromium.",
        "candidates": [],
        "findings": [],
        "visual_changes": [
            {
                "name": change.name,
                "description": change.description,
                "before": change.image("before"),
                "after": change.image("after"),
            }
            for change in CHANGES
        ],
    }
    named_assets = evidence / "assets"
    named_assets.mkdir(exist_ok=True)
    for name, path in images.items():
        shutil.copyfile(path, named_assets / name)
    source, _, native = str(scenario["plan"]).partition(":")
    _run(
        [store, "document", "create", source, "--project", native, "--title", "Visual spike"]
        + ["--id", spike_plan.visual_report_id(native), "--answers", _answers(report)]
        + ["--template-loader", "-", "--no-interactive", "--json"]
        + [flag for name in images for flag in ("--asset", str(named_assets / name))],
        stdin=_run([engine, "template", "resolve", "spike-report", "--json"]),
    )


def revision(store: str, engine: str, scenario: dict[str, object]) -> None:
    """Change one view, preserving the baseline report's other answers and asset bytes."""
    source, _, native = str(scenario["plan"]).partition(":")
    baseline = f"{source}:{spike_plan.visual_report_id(native)}"
    report = json.loads(_run([store, "document", "answers", baseline, "--json"]))
    shown = json.loads(_run([store, "document", "show", baseline, "--json"]))
    images = {asset["name"]: Path(asset["path"]) for asset in shown["assets"]}
    worktree = Path.cwd()
    views_path = worktree / "shots" / "views.json"
    views = json.loads(views_path.read_text())
    views["listing"]["seed"] += 2000
    views["listing"]["columns"] = ["name", "owner", "team"]
    views_path.write_text(json.dumps(views, indent=2) + "\n")
    _run(["git", "add", "-A"])
    _run(["git", *COMMITTER, "commit", "-qm", "feat: mock the owner's team"])
    evidence = Path(str(scenario["evidence"]))
    captured = evidence / "revision-captured"
    _capture(worktree, captured, str(scenario["chromium"]), "listing")
    changed = evidence / "listing-after.png"
    shutil.copyfile(captured / "listing.png", changed)
    images["listing-after.png"] = changed
    report["visual_changes"][0]["description"] = "The listing names each owner's team."
    report["spike"] = "spike-visual-revision"
    report["branch"] = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).strip()
    selected = f"{native}-spike-visual-revision-report"
    _run(
        [
            store,
            "document",
            "create",
            source,
            "--project",
            native,
            "--title",
            "Revised visual evidence",
            "--id",
            selected,
            "--answers",
            _answers(report),
            "--template-loader",
            "-",
            "--no-interactive",
        ]
        + [flag for path in images.values() for flag in ("--asset", str(path))],
        stdin=_run([engine, "template", "resolve", "spike-report", "--json"]),
    )
    _run(
        [
            store,
            "project",
            "metadata",
            "set",
            str(scenario["plan"]),
            spike_plan.VISUAL_REPORT_KEY,
            json.dumps(f"{source}:{selected}"),
        ]
    )


def main(argv: list[str]) -> int:
    global _failures
    turn, store, engine, scenario = argv[0], argv[1], argv[2], json.loads(argv[3])
    evidence = Path(str(scenario["evidence"]))
    evidence.mkdir(parents=True, exist_ok=True)
    _failures = evidence / FAILURES
    {"plan": plan, "spike": revision if scenario.get("revision") else spike}[turn](
        store, engine, scenario
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
