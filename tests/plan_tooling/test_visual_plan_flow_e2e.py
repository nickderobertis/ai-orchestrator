"""A visual plan runs through the real planning flow and reaches approval with its screenshots.

One `just plan` over a plan that changes what a user sees: its draft authors the reserved
`spike-visual`, the spikes stage dispatches it as a lifecycle node in a disposable target
repository, its turn commits a mock and captures each change before and after through the
target's own capture command in headless Chromium, its report carries the four captures as
plan-store assets, the finalize planner and the tail run, the design document restates the
report with the images as its own assets, it is copied onto a second store and approved
there, and the pictures survive the spike's branch and worktree being discarded.

**Everything is real but the paid model's answers**, as `test_plan_flow_e2e.py` stands in for
them: the real `scripts/plan.sh` and `scripts/finish-plan.sh`, the real `onepipeline` driver
dispatching every node, the real `onevcs` over a scratch registry whose target has a real Git
origin, the real plan store and templates, the real review, check, copy, approval and discard
recipes. `tests/e2e/fake_backend.py` answers each turn and runs the commands the scripted
turn issues where the dispatch runs them (`tests/plan_tooling/visual_flow_turns.py` for the
planner and the spike); `tests/e2e/fake_codex.py` answers the reviewer. The target's views are
seeded noise drawn by its committed page, so no image is committed and every capture is a
real rendering, sized like a real screenshot. A host without Chromium fails here, naming it.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] One whole planning flow of four
launches, in `plan-tooling` beside `test_plan_flow_e2e.py`'s, whose leaf key already names every
recipe, script, template and module it drives.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NamedTuple

import plan_root_variable
import pytest
import short_state
from conftest import git
from fake_backend import ENVIRONMENT_KEYS_ENV, PROMPT_LOG_ENV, RUN_ON_MARKER_ENV
from project_fixtures import helper, no_budgets
from published_tools import ONETASKGRAPH_BIN
from scratch_identity import GIT_IDENTITY, Identity, seeded
from test_finish_plan_recipe_e2e import DESIGN_TASK_MARKER, ENGINE_BIN, MERGES_THE_BUDGETS
from test_plan_flow_e2e import INHERITED_ENVIRONMENT, PASSING_VERDICT
from visual_images import decoded_pixels, sizes_like_screenshots
from waits import timeout as e2e_timeout

from orchestrator import spike_plan
from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.xdist_group("visual-plan-flow")

FAKE_BACKEND = helper("fake_backend.py")
FAKE_CODEX = helper("fake_codex.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")
TURNS = Path(__file__).resolve().parent / "visual_flow_turns.py"
RESTATES_THE_REPORT = Path(__file__).resolve().parent / "visual_answers.py"

LAUNCHING_SESSION = "e2e-visual-plan-flow"
RUN = "visual-plan-flow-e2e"
AUTHORING = "authoring"
DESTINATION = "destination"

#: The disposable target: its origin, and the aliases of its two registered checkouts.
TARGET = "github.com/nickderobertis/visual-target"
PUBLICATION = "target"
EXECUTION = "target-isolated"
BASE = "main"

#: The phrases that tell the stand-in which dispatch a turn is: the brief's, which the draft
#: and the finalize planner both carry, and the visual spike's own task.
PLANNER_MARKER = "Show each node's owner in the listing and name the run on the status line."
SPIKE_MARKER = "Capture each expected visual change before and after, mocked, unverified."

#: The plan store's environment layer for a source, and the setting naming its plugin.
INHERITED_SOURCE_SETTINGS = "ONETASKGRAPH_SOURCES__"
PLUGIN_SETTING = "__PLUGIN"

#: What each turn records of the environment it was handed, so a failure says what it saw.
RECORDED_ENVIRONMENT = (
    "ONEPIPELINE_TEMPLATE_ROOT",
    plan_root_variable.plugin_name(),
    plan_root_variable.name(),
    "ONETASKGRAPH_DEFAULT_SOURCES",
)

#: Criteria answering every demand the appendix and the built-in bar make.
CRITERIA = [
    "The listing shows each node's owner and the status line names the run.",
    "A browser-level test drives the listing and the status line end to end.",
    "Every claim the dispatch makes about the finished work is true of the tree as it "
    "finally stands.",
]

#: The target's views, drawn by its committed page from these seeds and sizes alone.
VIEWS = {
    "listing": {"seed": 11, "width": 400, "height": 390, "columns": ["name"]},
    "status": {"seed": 21, "width": 220, "height": 200},
}

#: The page each view is drawn by: a canvas of seeded pseudo-random pixels.
VIEW_PAGE = """<!doctype html>
<html><body style="margin:0;overflow:hidden"><canvas id="view"></canvas><script>
const asked = new URLSearchParams(location.search);
const width = +asked.get("w"), height = +asked.get("h");
let state = +asked.get("seed") >>> 0;
function next() {
  state = (state + 0x6D2B79F5) >>> 0;
  let t = state;
  t = Math.imul(t ^ (t >>> 15), t | 1);
  t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
  return ((t ^ (t >>> 14)) >>> 0) & 255;
}
const canvas = document.getElementById("view");
canvas.width = width; canvas.height = height;
const context = canvas.getContext("2d");
const drawn = context.createImageData(width, height);
for (let i = 0; i < drawn.data.length; i += 4) {
  drawn.data[i] = next(); drawn.data[i + 1] = next(); drawn.data[i + 2] = next();
  drawn.data[i + 3] = 255;
}
context.putImageData(drawn, 0, 0);
</script></body></html>
"""

#: The target's one capture command: every view of `views.json` to `<out>/<view>.png`.
CAPTURE = """#!/bin/sh
set -eu
out=$1
[ -x "${CHROMIUM:-}" ] || { echo "capture: no Chromium at '${CHROMIUM:-}'" >&2; exit 2; }
mkdir -p "$out"
python3 - "$out" <<'PY' | while read -r view width height seed; do
import json, sys
for name, view in json.load(open("shots/views.json")).items():
    print(name, view["width"], view["height"], view["seed"])
PY
    "$CHROMIUM" --headless --no-sandbox --hide-scrollbars --force-device-scale-factor=1 \\
        --window-size="$width,$height" --screenshot="$out/$view.png" \\
        "file://$PWD/shots/view.html?w=$width&h=$height&seed=$seed" >/dev/null 2>&1
done
"""


def _chromium() -> str:
    """The host's Chromium, or a failure naming what was looked for."""
    for name in ("chrome-headless-shell", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or str(Path.home() / ".cache/ms-playwright")
    for pattern in (
        "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell",
        "chromium-*/chrome-linux*/chrome",
    ):
        installed = sorted(glob.glob(os.path.join(root, pattern)))
        if installed:
            return installed[-1]
    pytest.fail(
        f"no Chromium to render the target's views: none of chrome-headless-shell, chromium "
        f"or chromium-browser is on PATH and Playwright installed none under {root}"
    )


def _target(root: Path, sentinels: Path) -> Identity:
    """Register the disposable target: its views, capture command, hook and test command."""
    identity = seeded(root, publication=PUBLICATION, execution=EXECUTION, origin=TARGET)
    tree = identity.publication
    (tree / "shots").mkdir()
    (tree / "shots" / "view.html").write_text(VIEW_PAGE, encoding="utf-8")
    (tree / "shots" / "views.json").write_text(json.dumps(VIEWS, indent=2) + "\n")
    (tree / "shots" / "capture.sh").write_text(CAPTURE, encoding="utf-8")
    (tree / ".gitignore").write_text("shots/out/\n", encoding="utf-8")
    (tree / ".githooks").mkdir()
    (tree / ".githooks" / "pre-push").write_text(
        f"#!/bin/sh\ntouch '{sentinels}/pre-push-ran'\n", encoding="utf-8"
    )
    (tree / "test.sh").write_text(f"#!/bin/sh\ntouch '{sentinels}/tests-ran'\n")
    for executable in ("shots/capture.sh", ".githooks/pre-push", "test.sh"):
        (tree / executable).chmod(0o755)
    git("add", "-A", cwd=tree)
    git(*GIT_IDENTITY, "commit", "-qm", "feat: the views and their capture", cwd=tree)
    subprocess.run(
        ["git", "push", "-q", "origin", BASE],
        cwd=tree,
        env={**os.environ, **identity.environment},
        check=True,
        timeout=e2e_timeout(60),
    )
    subprocess.run(
        ["git", "pull", "-q", "origin", BASE],
        cwd=identity.execution,
        env={**os.environ, **identity.environment},
        check=True,
        timeout=e2e_timeout(60),
    )
    for checkout in (identity.publication, identity.execution):
        git("config", "core.hooksPath", ".githooks", cwd=checkout)
    return identity


def _scenario(plan: str, evidence: Path, chromium: str) -> dict[str, object]:
    return {
        "plan": plan,
        "repository": TARGET,
        "execution": EXECUTION,
        "evidence": str(evidence),
        "chromium": chromium,
        "budgets": no_budgets([TARGET]),
        "task": {
            "what": "Show each node's owner in the listing and name the run on the status line.",
            "why": "An operator cannot tell who to ask about a node.",
            "acceptance_criteria": CRITERIA,
        },
        "spike_task": {
            "what": f"{SPIKE_MARKER} Take each before on the unchanged base, since no "
            "baseline screenshot covers it, and each after on this change.",
            "why": "The design document shows each change before anybody builds it.",
            "acceptance_criteria": [
                "The mock is committed with `sh shots/capture.sh <out>`, the one command that "
                "re-captures its screenshots.",
                "`spike-visual-report` answers `visual_changes` with each pair as its assets.",
            ],
        },
    }


def _design_writer(plan: str, staged: Path) -> list[str]:
    """What the design-doc writer runs: the report restated, each image given as an asset."""
    source, _, native = plan.partition(":")
    staged.write_text(
        json.dumps(
            {
                "what": "Each node's owner in the listing; the run named on the status line.",
                "why": "An operator cannot tell who to ask about a node.",
                "architecture": "One view changes, and one line of output.",
                "units": [
                    {
                        "name": "Views",
                        "repository": "visual-target",
                        "part": "",
                        "summary": "The listing and the status line.",
                        "reversible": [],
                        "decisions": [],
                    }
                ],
                "acceptance_criteria": ["The listing shows owners."],
                "planned_tasks": [
                    {
                        "task": "feat: show each node's owner",
                        "unit": "Views",
                        "delivers": "the column",
                        "depends_on": "none",
                        "location": "the store's own location",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return [
        "bash",
        "-c",
        "set -euo pipefail"
        '; printed=$(uv run python -m orchestrator.plan_budgets "$3:$4")'
        '; python3 "$7" "$6" "$printed"'
        '; mapfile -t held < <(python3 "$8" "$2" "$6" "$3:${9}")'
        '; flags=(); for path in "${held[@]}"; do flags+=(--asset "$path"); done'
        '; "$1" template resolve design-doc --repository "${10}" --json'
        ' | "$2" document create "$3" --project "$4" --title "Design: $4" --id "$5"'
        ' --template-loader - --answers "$6" --no-interactive "${flags[@]}"',
        "store-the-visual-document",
        str(ENGINE_BIN),
        str(ONETASKGRAPH_BIN),
        source,
        native,
        f"{native}-design",
        str(staged),
        str(MERGES_THE_BUDGETS),
        str(RESTATES_THE_REPORT),
        spike_plan.VISUAL_REPORT,
        TARGET,
    ]


class Flow(NamedTuple):
    """The whole flow, and everything read off it before and after the discard."""

    plan: subprocess.CompletedProcess[str]
    approved: subprocess.CompletedProcess[str]
    identity: Identity
    origin: Path
    project: str
    evidence: Path
    sentinels: Path
    spike: dict[str, Any]
    seed_commit: str
    origin_main: str
    reclaimed: subprocess.CompletedProcess[str]
    pruned: subprocess.CompletedProcess[str]
    worktree: Path
    report: dict[str, Any]
    design: dict[str, Any]
    after_report: dict[str, Any]
    after_design: dict[str, Any]
    environment: dict[str, str]


def _just(
    *arguments: str, environment: dict[str, str], seconds: float = 900
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["just", *arguments],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
    )


def _store(environment: dict[str, str], *arguments: str) -> Any:
    ran = subprocess.run(
        [str(ONETASKGRAPH_BIN), *arguments, "--json"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert ran.returncode == 0, (arguments, ran.stdout, ran.stderr)
    return json.loads(ran.stdout)


def _documents(environment: dict[str, str], project: str) -> dict[str, Any]:
    """The design document and the visual report the destination holds, each as it shows them."""
    source, _, native = project.partition(":")
    listed = _store(environment, "document", "list", "--source", source, "--project", native)
    held = {}
    for one in listed["items"]:
        template = (one["item"]["metadata"].get("onetaskgraph.template") or {}).get("template")
        kind = {"onepipeline:design-doc": "design", "onepipeline:spike-report": "report"}.get(
            template
        )
        if kind:
            held[kind] = _store(environment, "document", "show", str(one["id"]))
    assert set(held) == {"design", "report"}, listed
    return held


def _partial_overlays(environment: Mapping[str, str]) -> set[str]:
    """Each inherited setting of a source the environment names no plugin for.

    Such an overlay completes a source this checkout's `onetaskgraph.yaml` declares, as the
    host's `.env` overlays `hellopatient`'s team, and the spike's turn runs in the target's
    worktree, where nothing declares it, so the plan store refuses every command with it
    present, naming that source — `test_plan_root_env.py` shows that refusal standing. The
    launch completes only `authoring`, so this is removed here only until the plan store
    stops refusing an overlay of a source the command never names; every source setting
    that names its plugin is inherited as a launch hands it on.
    """
    settings = [name for name in environment if name.startswith(INHERITED_SOURCE_SETTINGS)]
    plugged = {_source(name) for name in settings if name == _plugin_setting(_source(name))}
    return {name for name in settings if _source(name) not in plugged}


def _source(setting: str) -> str:
    """The source an environment-layer setting belongs to, as the store spells it there."""
    return setting.removeprefix(INHERITED_SOURCE_SETTINGS).split("__", 1)[0]


def _plugin_setting(source: str) -> str:
    return f"{INHERITED_SOURCE_SETTINGS}{source}{PLUGIN_SETTING}"


@pytest.fixture(scope="module")
def flow(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Flow:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    chromium = _chromium()
    tmp_path = tmp_path_factory.mktemp("visual-plan-flow")
    sentinels = tmp_path / "sentinels"
    sentinels.mkdir()
    identity = _target(tmp_path / "identity", sentinels)
    origin = tmp_path / "identity" / "origin.git"
    seed_commit = git("rev-parse", BASE, cwd=origin).strip()
    authoring = tmp_path / "authoring"
    destination = tmp_path / "destination"
    for root in (authoring, destination):
        root.mkdir()
    native = f"test-{os.getpid()}-visual-plan"
    project = f"{AUTHORING}:{native}"
    evidence = tmp_path / "evidence"
    scenario = json.dumps(_scenario(project, evidence, chromium))

    environment = {
        name: value
        for name, value in os.environ.items()
        if name not in _partial_overlays(os.environ) and name not in INHERITED_ENVIRONMENT
    }
    environment.update(identity.environment)
    environment |= {
        "CLAUDE_CODE_SESSION_ID": LAUNCHING_SESSION,
        "ONEVCS_HOME": str(identity.home),
        "ONEPIPELINE_RUNS_DIR": str(tmp_path / "runs"),
        "XDG_STATE_HOME": str(short_state.state_home(tmp_path)),
        "REAL_ONEHARNESS_BIN": oneharness_bin,
        # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
        "ONEAGENTGRAPH_ONEHARNESS_BIN": str(FAKE_BACKEND),
        # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
        "ONEHARNESS_BIN_CODEX": str(FAKE_CODEX),
        # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
        "PATH": f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}",
        "FAKE_CODEX_ANSWERS": json.dumps([PASSING_VERDICT]),
        plan_root_variable.name(): str(authoring),
        "ONETASKGRAPH_SOURCES__DESTINATION__PLUGIN": "local-md",
        "ONETASKGRAPH_SOURCES__DESTINATION__CONFIG__ROOT": str(destination),
        "ONETASKGRAPH_DEFAULT_SOURCES": f"{AUTHORING},{DESTINATION}",
        PROMPT_LOG_ENV: str(tmp_path / "turns.jsonl"),
        ENVIRONMENT_KEYS_ENV: ",".join(RECORDED_ENVIRONMENT),
    }
    turn = [sys.executable, str(TURNS)]
    keyed = tmp_path / "commands.json"
    keyed.write_text(
        json.dumps(
            {
                PLANNER_MARKER: [[*turn, "plan", str(ONETASKGRAPH_BIN), str(ENGINE_BIN), scenario]],
                SPIKE_MARKER: [[*turn, "spike", str(ONETASKGRAPH_BIN), str(ENGINE_BIN), scenario]],
                DESIGN_TASK_MARKER: [_design_writer(project, tmp_path / "design.answers.json")],
            }
        ),
        encoding="utf-8",
    )
    environment[RUN_ON_MARKER_ENV] = str(keyed)
    brief = tmp_path / "owner-column.md"
    brief.write_text(
        f"## What\n{PLANNER_MARKER}\n\nPlan project: {project}\n\n"
        "## Why\nAn operator cannot tell who to ask about a node.\n\n"
        "## Acceptance criteria\n- The plan shows the owner column before it is built.\n",
        encoding="utf-8",
    )
    runs = [RUN, f"{RUN}-spikes", f"{RUN}-finalize", f"{RUN}-design"]
    try:
        planned = _just(
            "plan",
            str(brief),
            "--name",
            RUN,
            "--to",
            DESTINATION,
            environment=environment,
            seconds=2400,
        )
        sizes = evidence / "sizes.json"
        if sizes.is_file():
            # llmlint: ignore-block[tests_hold_no_nonfunctional_thresholds] The bounds are this
            # journey's acceptance criteria for its own inputs, which must be screenshot-sized for
            # an asset to prove anything; they limit no product behavior.
            sizes_like_screenshots(json.loads(sizes.read_text()))
            # llmlint: ignore-end[tests_hold_no_nonfunctional_thresholds]
        failures = evidence / "turn-failures.log"
        said = failures.read_text(encoding="utf-8") if failures.is_file() else ""
        assert not said, f"a scripted turn's command failed:\n{said}"
        assert planned.returncode == 0, f"the flow failed:\n{planned.stdout}\n{planned.stderr}"
        landed = f"{DESTINATION}:{native}"
        approved = _just("approve-design", landed, environment=environment, seconds=300)
        before = _documents(environment, landed)
        result = json.loads((tmp_path / "runs" / f"{RUN}-spikes" / "result.json").read_text())
        (spike,) = [node for node in result["nodes"] if node["id"] == spike_plan.VISUAL_SPIKE]
        opened = [
            json.loads(line)
            for line in (tmp_path / "runs" / f"{RUN}-spikes" / "events.jsonl")
            .read_text()
            .splitlines()
            if '"session-opened"' in line
        ]
        worktree = Path(_worktree(opened))
        reclaimed = _just(
            "reclaim-branch",
            str(spike["branch"]),
            "--repo",
            str(identity.publication),
            "--discard",
            environment=environment,
            seconds=300,
        )
        pruned = subprocess.run(
            ["uv", "run", "onevcs", "pool", "prune", str(identity.publication)],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(300),
            check=False,
        )
        after = _documents(environment, landed)
        return Flow(
            plan=planned,
            approved=approved,
            identity=identity,
            origin=origin,
            project=landed,
            evidence=evidence,
            sentinels=sentinels,
            spike=spike,
            seed_commit=seed_commit,
            origin_main=git("rev-parse", BASE, cwd=origin).strip(),
            reclaimed=reclaimed,
            pruned=pruned,
            worktree=worktree,
            report=before["report"],
            design=before["design"],
            after_report=after["report"],
            after_design=after["design"],
            environment=environment,
        )
    finally:
        for run in runs:
            _just("stop", run, environment=environment, seconds=60)


def _worktree(opened: list[dict[str, Any]]) -> str:
    """The visual spike's session worktree, as the run journalled it when it opened."""
    for event in opened:
        payload = event.get("payload") or event
        if payload.get("worktree"):
            return str(payload["worktree"])
    raise AssertionError(f"the spikes run journalled no session worktree: {opened}")


def _captured(flow: Flow) -> dict[str, bytes]:
    kept = flow.evidence / "captured"
    return {path.name: path.read_bytes() for path in sorted(kept.iterdir())}


def test_the_draft_authors_the_visual_spike_and_it_settles_preserved(flow: Flow) -> None:
    node = flow.spike
    assert (node["status"], node.get("outcome")) == ("done", "preserved"), node
    native = flow.project.partition(":")[2]
    # `<host prefix>/<plan native id>/spike-visual`, and a scratch host configures no prefix.
    assert str(node["branch"]).split("/")[-2:] == [native, spike_plan.VISUAL_SPIKE], node


def test_the_spike_commits_a_mock_and_captures_each_pair_screenshot_sized(flow: Flow) -> None:
    sizes = json.loads((flow.evidence / "sizes.json").read_text())
    assert sorted(sizes) == sorted(_captured(flow)), sizes
    # llmlint: ignore-block[tests_hold_no_nonfunctional_thresholds] The bounds are this journey's
    # acceptance criteria for its own inputs, which must be screenshot-sized for an asset to prove
    # anything; they limit no product behavior.
    sizes_like_screenshots(sizes)
    # llmlint: ignore-end[tests_hold_no_nonfunctional_thresholds]
    base = (flow.evidence / "base.sha").read_text().strip()
    assert base == flow.seed_commit, "the before was not captured on the unchanged base"
    changed = git("diff", "--name-only", base, str(flow.spike["head"]), cwd=flow.origin)
    assert changed.split() == ["shots/views.json"], changed
    captured = _captured(flow)
    for view in VIEWS:
        before, after = captured[f"{view}-before.png"], captured[f"{view}-after.png"]
        assert hashlib.sha256(before).digest() != hashlib.sha256(after).digest(), view
        assert decoded_pixels(before)[2] != decoded_pixels(after)[2], view
        assert decoded_pixels(before)[:2] == decoded_pixels(after)[:2], "not the same view"


def test_the_report_holds_the_four_captures_and_names_two_changes(flow: Flow) -> None:
    held = {asset["name"]: asset for asset in flow.report["assets"]}
    assert set(held) == set(_captured(flow)), held
    (item,) = flow.report["items"]
    content = item["item"]["content"]
    assert content.count("\n### ") == 2, content


def test_nothing_was_verified_and_nothing_landed(flow: Flow) -> None:
    assert not list(flow.sentinels.iterdir()), list(flow.sentinels.iterdir())
    assert flow.origin_main == flow.seed_commit, "the base on the origin moved"
    contains = subprocess.run(
        ["git", "merge-base", "--is-ancestor", str(flow.spike["head"]), BASE],
        cwd=flow.origin,
        capture_output=True,
        check=False,
    )
    assert contains.returncode == 1, "the base on the origin carries the mock's commit"
    # The two would have left a sentinel had anything run them.
    for command in (".githooks/pre-push", "test.sh"):
        subprocess.run([str(flow.identity.publication / command)], check=True)
    assert sorted(p.name for p in flow.sentinels.iterdir()) == ["pre-push-ran", "tests-ran"]


def _pairs_shown(shown: dict[str, Any], captured: dict[str, bytes]) -> None:
    """``shown`` renders both pairs and holds the four captures, byte for byte, at real paths."""
    (item,) = shown["items"]
    content = item["item"]["content"]
    section = content.split("\n## Visual changes\n", 1)[1].split("\n## ", 1)[0]
    for name in captured:
        assert f"](./{name})" in section, (name, section)
    held = {asset["name"]: Path(asset["path"]) for asset in shown["assets"]}
    assert set(held) == set(captured), held
    for name, image in captured.items():
        assert held[name].is_file(), held[name]
        assert held[name].read_bytes() == image, name


def test_the_design_document_restates_the_report_and_is_approved_on_the_copy(flow: Flow) -> None:
    (report,) = flow.report["items"]
    (design,) = flow.design["items"]
    section = design["item"]["content"].split("\n## Visual changes\n", 1)[1].split("\n## ")[0]
    reported = report["item"]["content"].split("\n## Visual changes\n", 1)[1]
    assert section.strip() == reported.strip(), (section, reported)
    _pairs_shown(flow.design, _captured(flow))
    assert flow.approved.returncode == 0, flow.approved.stdout + flow.approved.stderr
    assert "recorded the approval" in flow.approved.stdout, flow.approved.stdout


def test_the_pairs_survive_the_spikes_branch_and_worktree_being_discarded(flow: Flow) -> None:
    assert flow.reclaimed.returncode == 0, flow.reclaimed.stdout + flow.reclaimed.stderr
    assert flow.pruned.returncode == 0, flow.pruned.stdout + flow.pruned.stderr
    branches = git("for-each-ref", "--format=%(refname:short)", "refs/heads", cwd=flow.origin)
    assert str(flow.spike["branch"]) not in branches.split(), branches
    assert not flow.worktree.exists(), f"the spike's worktree {flow.worktree} is still there"
    for shown in (flow.after_design, flow.after_report):
        _pairs_shown(shown, _captured(flow))
