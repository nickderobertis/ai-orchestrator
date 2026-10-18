"""`just finish-plan` quotes a visual plan's before-and-after screenshots to its writer.

A plan whose project holds its visual spike's report, `<plan>-spike-visual-report`, changes what a
user sees, and the design document a person approves it as shows each change as a pair of
images. The writer does not compose that answer: the tail's task quotes the report's
`visual_changes` and, for each image, where the store holds it, and holds the document to
restating the one and holding the other as its own assets.

Everything below the recipe is the real one, as `test_finish_plan_recipe_e2e.py` drives it:
the real `scripts/finish-plan.sh`, the real review turn, check, launch, copy and report, the
real plan store at both ends and the pinned engine's templates. `tests/e2e/fake_codex.py`
stands in for the paid provider alone, and the writer's turn runs the commands its task
names: the report's answers and assets read out of the store, and the document rendered and
stored with each image given as `--asset`. The images are generated here, never committed:
seeded pseudo-random pixels sized like real screenshots, and checked to be before the store
is handed any of them.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] `plan-tooling-plan-flow` is
the leaf project keyed on `planToolingPlanFlowWorkspace`, the edge this rule asks for,
beside `test_finish_plan_recipe_e2e.py`'s journeys over the same tail; that key names the
recipes, scripts, templates and package these journeys drive, so narrowing it would memoize
a verdict over a tree never run.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from fake_backend import ENVIRONMENT_KEYS_ENV, PROMPT_LOG_ENV, RUN_ON_MARKER_ENV
from published_tools import ONETASKGRAPH_BIN
from test_finish_plan_recipe_e2e import (
    COPIES_THE_ROWS,
    DESIGN_RUN_SUFFIX,
    DESIGN_TASK_MARKER,
    DESTINATION,
    ENGINE_BIN,
    JUDGE_ARTIFACTS_ENV,
    MERGES_THE_BUDGETS,
    OK,
    PASSES,
    UNRUNNABLE,
    Bench,
    Drafted,
    RunId,
    StoredTask,
    _bench,
    _brief,
    _design_task,
    _draft,
    _just,
    _landed,
    _staged_answers,
    _stop,
)
from visual_images import LARGE, sized_like_screenshots, write_noise_png
from waits import timeout as e2e_timeout

from orchestrator import spike_plan
from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.xdist_group("finish-plan-visual")

#: The stand-in writer's step that restates the report into its answers.
RESTATES_THE_REPORT = Path(__file__).resolve().parent / "visual_answers.py"

#: The two changes the visual spike reported, each a pair of images the report holds.
CHANGES = [
    {
        "name": "Node listing",
        "description": "The listing gains a column naming each node's owner.",
        "before": "listing-before.png",
        "after": "listing-after.png",
    },
    {
        "name": "Run status line",
        "description": "The CLI's status line names the run instead of its id.",
        "before": "status-before.png",
        "after": "status-after.png",
    },
]


def _store(bench: Bench, *arguments: str, stdin: str | None = None) -> Any:
    """One command of the pinned plan store, under this bench's sources, as its JSON answer."""
    ran = subprocess.run(
        [str(ONETASKGRAPH_BIN), *arguments, "--json"],
        cwd=REPO_ROOT,
        env=bench.environment,
        input=stdin,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert ran.returncode == 0, (arguments, ran.stdout, ran.stderr)
    return json.loads(ran.stdout)


def _loader(name: str) -> str:
    """The pinned engine's loader for ``name``, resolved from this checkout's templates."""
    resolved = subprocess.run(
        [str(ENGINE_BIN), "template", "resolve", name, "--json"]
        + ["--template-root", str(REPO_ROOT / "templates")],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert resolved.returncode == 0, resolved.stderr
    return resolved.stdout


def _report(
    bench: Bench,
    drafted: Drafted,
    images: dict[str, bytes],
    *,
    changes: list[dict[str, str]] = CHANGES,
    report_id: str | None = None,
) -> str:
    """Store the visual spike's report in the plan's project, each image an asset of it."""
    source, _, project = drafted.qualified.partition(":")
    report_id = report_id or spike_plan.visual_report_id(project)
    directory = bench.tmp_path / f"report-{drafted.project}-{report_id}"
    directory.mkdir()
    for name, image in images.items():
        (directory / name).write_bytes(image)
    answers = directory / "answers.json"
    answers.write_text(json.dumps(_report_answers(drafted.project, changes)), encoding="utf-8")
    assets = [flag for name in images for flag in ("--asset", str(directory / name))]
    created = _store(
        bench,
        *("document", "create", source, "--project", project, "--title", "Visual spike"),
        *("--id", report_id, "--answers", str(answers)),
        *("--template-loader", "-", "--no-interactive"),
        *assets,
        stdin=_loader("spike-report"),
    )
    (item,) = created["items"]
    return str(item["id"])


def _report_answers(project: str, changes: list[dict[str, str]]) -> dict[str, object]:
    """The spike-report template's answers for the visual spike of ``project``."""
    return {
        "spike": spike_plan.VISUAL_SPIKE,
        "branch": f"nick/{project}/{spike_plan.VISUAL_SPIKE}",
        "harness": "`shots/capture.sh`; run `./shots/capture.sh before after`.",
        "method": "The listing and the status line, mocked over fixture data.",
        "candidates": [],
        "findings": [],
        "visual_changes": changes,
    }


def _images(tmp_path: Path) -> dict[str, bytes]:
    """The four screenshots, each distinct and screenshot-sized, one of about 500 KB."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    images = {
        name: write_noise_png(tmp_path / name, seed, LARGE if seed == 0 else (220, 210 + seed))
        for seed, name in enumerate(
            side for change in CHANGES for side in (change["before"], change["after"])
        )
    }
    # llmlint: ignore-block[tests_hold_no_nonfunctional_thresholds] The bounds are this journey's
    # acceptance criteria for its own inputs, which must be screenshot-sized for an asset to prove
    # anything; they limit no product behavior.
    sized_like_screenshots(images)
    # llmlint: ignore-end[tests_hold_no_nonfunctional_thresholds]
    assert len({hashlib.sha256(image).hexdigest() for image in images.values()}) == 4
    return images


def _writes_the_visual_document(
    bench: Bench, drafted: Drafted, report: str, *, revision: bool = False
) -> None:
    """Script the commands the design-doc writer runs for a visual plan.

    The answers are the model's; restating the report's `visual_changes` and giving each of
    its images with `--asset` is what the task tells it to do, run here as the store's own
    command line where the dispatch runs it.
    """
    keyed = bench.tmp_path / f"commands-{drafted.project}.json"
    answers = _staged_answers(bench.tmp_path / drafted.project, drafted)
    source, _, project = drafted.qualified.partition(":")
    keyed.write_text(
        json.dumps(
            {
                DESIGN_TASK_MARKER: [
                    [
                        "bash",
                        "-c",
                        "set -euo pipefail"
                        '; printed=$(uv run python -m orchestrator.plan_budgets "$3:$4")'
                        '; python3 "$9" "$7" "$printed"'
                        '; python3 "${12}" "$7" "$2" >/dev/null'
                        + (
                            '; mapfile -t held < <(python3 "${10}" "$2" "$7" "${11}" "$3:$6")'
                            if revision
                            else '; mapfile -t held < <(python3 "${10}" "$2" "$7" "${11}")'
                        )
                        + '; flags=(); for path in "${held[@]}"; do flags+=(--asset "$path"); done'
                        '; "$1" template resolve design-doc --json'
                        + (
                            ' | "$2" document render "$3:$6"'
                            if revision
                            else ' | "$2" document create "$3" --project "$4"'
                            ' --title "$5" --id "$6"'
                        )
                        + ' --template-loader - --answers "$7" --no-interactive "${flags[@]}"',
                        "store-the-visual-document",
                        str(ENGINE_BIN),
                        str(ONETASKGRAPH_BIN),
                        source,
                        project,
                        f"Design: {drafted.project}",
                        drafted.document,
                        str(answers),
                        "",
                        str(MERGES_THE_BUDGETS),
                        str(RESTATES_THE_REPORT),
                        report,
                        str(COPIES_THE_ROWS),
                    ]
                ]
            }
        ),
        encoding="utf-8",
    )
    bench.environment[RUN_ON_MARKER_ENV] = str(keyed)
    bench.environment[PROMPT_LOG_ENV] = str(bench.tmp_path / f"turns-{drafted.project}.jsonl")
    bench.environment[ENVIRONMENT_KEYS_ENV] = JUDGE_ARTIFACTS_ENV


class Visual(NamedTuple):
    """One whole tail over a visual plan, and what was read back off it."""

    finish: subprocess.CompletedProcess[str]
    drafted: Drafted
    report: str
    images: dict[str, bytes]
    report_assets: dict[str, str]
    design_task: StoredTask
    copied: dict[str, Any]


RUN = RunId("finish-plan-visual-e2e")


@pytest.fixture(scope="module")
def visual(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Visual:
    """Drive the tail once over a plan holding its visual spike's report with two changes."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp_path = tmp_path_factory.mktemp("finish-plan-visual")
    bench = _bench(tmp_path, oneharness_bin, PASSES)
    drafted = _draft("finish-plan-visual")
    images = _images(tmp_path / "captured")
    report = _report(bench, drafted, images)
    shown = _store(bench, "document", "show", report)
    report_assets = {str(asset["name"]): str(asset["path"]) for asset in shown["assets"]}
    _writes_the_visual_document(bench, drafted, report)
    brief = _brief(tmp_path, drafted, "visual-listing")
    try:
        finish = _just(
            "finish-plan",
            str(brief),
            "--name",
            RUN,
            "--to",
            DESTINATION,
            environment=bench.environment,
        )
        assert finish.returncode == OK, f"the tail failed:\n{finish.stdout}\n{finish.stderr}"
        documents = _store(
            bench, "document", "list", "--source", DESTINATION, "--project", drafted.project
        )["items"]
        (design,) = [one for one in documents if str(one["id"]).endswith(drafted.document)]
        copied = _store(bench, "document", "show", str(design["id"]))
        return Visual(
            finish, drafted, report, images, report_assets, _design_task(bench, RUN), copied
        )
    finally:
        _stop(bench, f"{RUN}{DESIGN_RUN_SUFFIX}")


def test_the_writers_task_quotes_the_reports_visual_changes_and_where_each_image_is(
    visual: Visual,
) -> None:
    """The report's answer, entry for entry, and the store's path for every image it names."""
    content = visual.design_task["content"]
    quoted = json.dumps(CHANGES, ensure_ascii=False, indent=2)
    assert f"```json\n{quoted}\n```" in content, content
    assert f"`{visual.report}`" in content, content
    for name, path in visual.report_assets.items():
        assert f"- `{name}`: `{path}`" in content, (name, content)
    assert "`--asset <path>`" in content, content
    criteria = content.split("## Acceptance criteria", 1)[1].split("\n## ", 1)[0]
    assert (
        f"The document’s `visual_changes` answer restates the `visual_changes` answer of "
        f"`{visual.report}`, entry for entry and in its order, and every image it references "
        f"is held as an asset of the document."
    ) in " ".join(criteria.split()), criteria


def test_the_document_on_the_destination_shows_each_pair_and_holds_its_images(
    visual: Visual,
) -> None:
    """The writer did what its task quoted: the copy carries the section and the four images."""
    (item,) = visual.copied["items"]
    content = item["item"]["content"]
    assert "\n## Visual changes\n" in content, content
    for change in CHANGES:
        assert f"![Before: {change['name']}](./{change['before']})" in content, content
        assert f"![After: {change['name']}](./{change['after']})" in content, content
    held = {str(asset["name"]): asset for asset in visual.copied["assets"]}
    assert set(held) == set(visual.images), held
    for name, image in visual.images.items():
        assert held[name]["sha256"] == hashlib.sha256(image).hexdigest(), name
        assert Path(held[name]["path"]).read_bytes() == image, name


def test_a_visual_report_naming_no_change_is_refused_before_anything_launches(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """A visual spike that showed nothing gives the writer nothing to restate, so the tail stops."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    bench = _bench(tmp_path, oneharness_bin, PASSES)
    drafted = _draft("finish-plan-visual-empty")
    report = _report(bench, drafted, {}, changes=[])
    run = RunId("finish-plan-visual-empty-e2e")
    brief = _brief(tmp_path, drafted, "visual-empty")
    try:
        finish = _just(
            "finish-plan",
            str(brief),
            "--name",
            run,
            "--to",
            DESTINATION,
            environment=bench.environment,
        )
    finally:
        _stop(bench, f"{run}{DESIGN_RUN_SUFFIX}")
    assert finish.returncode == UNRUNNABLE, finish.stderr
    assert f"{report} answers no visual_changes" in finish.stderr, finish.stderr
    assert "launching run" not in finish.stderr, finish.stderr
    assert not (bench.runs / f"{run}{DESIGN_RUN_SUFFIX}").exists()


def test_the_visual_report_question_refuses_a_store_that_cannot_be_read(tmp_path: Path) -> None:
    """The tail asks the real store whether a plan is visual; a store that cannot answer stops it.

    `scripts/finish-plan.sh` reads the visual report's id from `orchestrator.spike_plan`, so a
    store that cannot list the plan's documents must say so and print no id, rather than
    answering "no visual report" and letting a visual plan through with no section. The store
    here is the real pinned one over a `local-md` source whose root does not exist, which it
    refuses as a broken source.
    """
    environment = dict(os.environ)
    environment["ONETASKGRAPH_SOURCES__BROKEN__PLUGIN"] = "local-md"
    environment["ONETASKGRAPH_SOURCES__BROKEN__CONFIG__ROOT"] = str(tmp_path / "absent")
    asked = subprocess.run(
        [sys.executable, "-m", "orchestrator.spike_plan", "visual-report", "broken:plan"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    # `spike_plan`'s own refusal status, which the tail turns into its own `UNRUNNABLE`.
    assert asked.returncode == 2, (asked.stdout, asked.stderr)
    assert asked.stdout == "", asked.stdout
    assert "spike_plan: the documents of broken:plan could not be read: " in asked.stderr
    assert "source broken could not answer" in asked.stderr, asked.stderr


def _visual_report_of(
    project: str, environment: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Ask `orchestrator.spike_plan` which visual report ``project`` holds, as the tail asks it."""
    return subprocess.run(
        [sys.executable, "-m", "orchestrator.spike_plan", "visual-report", project],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )


def test_a_copy_whose_destination_reminted_the_report_is_found_and_an_imposter_is_not(
    tmp_path: Path,
) -> None:
    """On a copy, the visual report is the spike report its origin names, whatever id it holds.

    The destination already holds a document under `plan-spike-visual-report` that no spike-report
    template rendered, so the real store mints the copied report a different id there and
    records where it came from as `onetaskgraph.origin`. The imposter is no visual report —
    a plan holding only it is not visual — and the reminted copy is the one the tail names.
    """
    drafting, board = tmp_path / "drafting", tmp_path / "board"
    drafting.mkdir()
    board.mkdir()
    environment = dict(os.environ)
    for name, root in (("DRAFTING", drafting), ("BOARD", board)):
        environment[f"ONETASKGRAPH_SOURCES__{name}__PLUGIN"] = "local-md"
        environment[f"ONETASKGRAPH_SOURCES__{name}__CONFIG__ROOT"] = str(root)
    environment["ONETASKGRAPH_DEFAULT_SOURCES"] = "drafting,board"

    def store(*arguments: str, stdin: str | None = None) -> Any:
        ran = subprocess.run(
            [str(ONETASKGRAPH_BIN), *arguments, "--json"],
            cwd=REPO_ROOT,
            env=environment,
            input=stdin,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(120),
            check=False,
        )
        assert ran.returncode == 0, (arguments, ran.stdout, ran.stderr)
        return json.loads(ran.stdout)

    for source in ("drafting", "board"):
        store("project", "create", source, "--id", "plan", "--title", "Plan")

    body = tmp_path / "imposter.md"
    body.write_text("Notes that happen to sit under the visual report's id.\n", encoding="utf-8")
    store(
        *("document", "create", "board", "--project", "plan", "--title", "Notes"),
        *("--id", spike_plan.visual_report_id("plan"), "--body-file", str(body)),
    )
    only_the_imposter = _visual_report_of("board:plan", environment)
    assert only_the_imposter.returncode == 0, only_the_imposter.stderr
    assert only_the_imposter.stdout == "", only_the_imposter.stdout

    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps(_report_answers("plan", CHANGES[:1])), encoding="utf-8")
    image = tmp_path / CHANGES[0]["before"]
    write_noise_png(image, 0, (220, 210))
    after = tmp_path / CHANGES[0]["after"]
    write_noise_png(after, 1, (220, 211))
    store(
        *("document", "create", "drafting", "--project", "plan", "--title", "Visual spike"),
        *("--id", spike_plan.visual_report_id("plan"), "--answers", str(answers)),
        *("--template-loader", "-", "--no-interactive"),
        *("--asset", str(image), "--asset", str(after)),
        stdin=_loader("spike-report"),
    )
    (copied,) = store(
        "document", "copy", f"drafting:{spike_plan.visual_report_id('plan')}", "--to", "board"
    )["items"]
    reminted = str(copied["destination"])
    assert reminted != f"board:{spike_plan.visual_report_id('plan')}", copied

    found = _visual_report_of("board:plan", environment)
    assert found.returncode == 0, found.stderr
    assert found.stdout == f"{reminted}\n", found.stdout

    store(
        "project",
        "metadata",
        "set",
        "board:plan",
        spike_plan.VISUAL_REPORT_KEY,
        json.dumps(f"drafting:{spike_plan.visual_report_id('plan')}"),
    )
    explicit = _visual_report_of("board:plan", environment)
    assert explicit.returncode == 0, explicit.stderr
    assert explicit.stdout == f"{reminted}\n"


def test_finish_quotes_selected_revision_and_refuses_invalid_selections(
    tmp_path: Path,
    oneharness_bin: str,
) -> None:
    """The real tail updates a design from complete current evidence and fails closed."""
    bench = _bench(tmp_path, oneharness_bin, PASSES)
    drafted = _draft("finish-selected-visual")
    images = _images(tmp_path / "images")
    original = _report(bench, drafted, images)
    _writes_the_visual_document(bench, drafted, original)
    brief = _brief(tmp_path, drafted, "selected-revision")
    run = RunId("finish-selected-visual-initial")
    revision_run = RunId("finish-selected-visual-revision")
    try:
        initial = _just(
            "finish-plan",
            str(brief),
            "--name",
            run,
            "--to",
            DESTINATION,
            environment=bench.environment,
        )
        assert initial.returncode == OK, initial.stdout + initial.stderr
        before = _store(bench, "document", "show", original)
        design_id = f"{drafted.qualified.partition(':')[0]}:{drafted.document}"
        design_before = _store(bench, "document", "show", design_id)
        revised = [dict(change) for change in CHANGES]
        revised[0]["description"] = "The listing names each owner's team."
        updated = dict(images)
        updated[revised[0]["after"]] = write_noise_png(tmp_path / "changed.png", 92, LARGE)
        selected = _report(
            bench,
            drafted,
            updated,
            changes=revised,
            report_id=f"{drafted.project}-spike-visual-revision-report",
        )
        _store(
            bench,
            "project",
            "metadata",
            "set",
            drafted.qualified,
            spike_plan.VISUAL_REPORT_KEY,
            json.dumps(selected),
        )
        _writes_the_visual_document(bench, drafted, selected, revision=True)
        finished = _just(
            "finish-plan",
            str(brief),
            "--name",
            revision_run,
            "--to",
            DESTINATION,
            environment=bench.environment,
        )
        assert finished.returncode == OK, finished.stdout + finished.stderr
        task = _design_task(bench, revision_run)
        assert json.dumps(revised, ensure_ascii=False, indent=2) in task["content"]
        assert f"`{selected}`" in task["content"]
        design = _store(bench, "document", "show", design_id)
        assert design["items"][0]["id"] == design_before["items"][0]["id"]
        assert {
            asset["name"]: Path(asset["path"]).read_bytes() for asset in design["assets"]
        } == updated
        assert _store(bench, "document", "show", original) == before
        other_plan = _draft("finish-selection-other-plan")
        other_report = _report(bench, other_plan, images, report_id="other-plan-report")
        for index, value in enumerate(
            (None, 4, " ", "native", "source:missing", other_report, design_id)
        ):
            _store(
                bench,
                "project",
                "metadata",
                "set",
                drafted.qualified,
                spike_plan.VISUAL_REPORT_KEY,
                json.dumps(value),
            )
            invalid_run = RunId(f"finish-invalid-selection-{index}")
            refused = _just(
                "finish-plan",
                str(brief),
                "--name",
                invalid_run,
                "--to",
                DESTINATION,
                environment=bench.environment,
            )
            assert refused.returncode == UNRUNNABLE, refused.stdout + refused.stderr
            assert spike_plan.VISUAL_REPORT_KEY in refused.stderr
            assert not (bench.runs / f"{invalid_run}{DESIGN_RUN_SUFFIX}").exists()
            assert _store(bench, "document", "show", design_id) == design
        _store(
            bench,
            "project",
            "metadata",
            "set",
            drafted.qualified,
            spike_plan.VISUAL_REPORT_KEY,
            json.dumps(selected),
        )
        source = selected.partition(":")[0]
        (duplicate,) = _store(bench, "document", "copy", selected, "--to", source)["items"]
        assert duplicate["destination"] != selected, duplicate
        duplicated = _store(bench, "document", "show", str(duplicate["destination"]))
        duplicate_path = Path(duplicated["items"][0]["item"]["location"]["path"])
        try:
            ambiguous = _just(
                "finish-plan",
                str(brief),
                "--name",
                "finish-ambiguous-selection",
                "--to",
                DESTINATION,
                environment=bench.environment,
            )
            assert ambiguous.returncode == UNRUNNABLE, ambiguous.stdout + ambiguous.stderr
            assert "ambiguous selection" in ambiguous.stderr
            assert not (bench.runs / f"finish-ambiguous-selection{DESIGN_RUN_SUFFIX}").exists()
        finally:
            duplicate_path.unlink()
    finally:
        for name in (run, revision_run):
            _stop(bench, f"{name}{DESIGN_RUN_SUFFIX}")


def test_plans_in_one_source_each_quote_their_own_visual_report_and_an_old_id_one_is_read(
    tmp_path: Path,
    oneharness_bin: str,
) -> None:
    """Two plans' visual spikes share a topic and a source; each tail quotes its plan's report.

    The canonical report is `<plan native id>-spike-visual-report`, so the two plans drafted in
    the one `test-fixtures` root hold two documents, and each design writer is quoted its own
    plan's changes — the first plan's even while it also holds a stale report under the old
    `spike-visual-report`. A plan whose visual spike reported only under that old id, before
    ids named their plan, is quoted that report and approved against it. The old id is one
    document for the whole flat source, so the stale one is the old-id plan's only once that
    plan writes its own over it.
    """
    bench = _bench(tmp_path, oneharness_bin, PASSES)
    images = _images(tmp_path / "images")
    legacy = spike_plan.legacy_report_id(spike_plan.VISUAL_SPIKE)

    def changes_of(name: str) -> list[dict[str, str]]:
        return [change | {"description": f"{change['description']} ({name})"} for change in CHANGES]

    def tail(drafted: Drafted, report: str) -> str:
        run = RunId(f"{drafted.project}-tail")
        _writes_the_visual_document(bench, drafted, report)
        brief = _brief(tmp_path, drafted, drafted.project)
        try:
            finished = _just(
                "finish-plan",
                str(brief),
                "--name",
                run,
                "--to",
                DESTINATION,
                environment=bench.environment,
            )
            assert finished.returncode == OK, finished.stdout + finished.stderr
            return _design_task(bench, run)["content"]
        finally:
            _stop(bench, f"{run}{DESIGN_RUN_SUFFIX}")

    first, second = _draft("finish-collide-first"), _draft("finish-collide-second")
    reports = {
        drafted.project: _report(bench, drafted, images, changes=changes_of(drafted.project))
        for drafted in (first, second)
    }
    stale = _report(bench, first, images, changes=changes_of("stale"), report_id=legacy)
    for drafted in (first, second):
        assert reports[drafted.project].partition(":")[2] == spike_plan.visual_report_id(
            drafted.project
        )
    assert reports[first.project] != reports[second.project]

    for drafted, other in ((first, second), (second, first)):
        content = tail(drafted, reports[drafted.project])
        quoted = json.dumps(changes_of(drafted.project), ensure_ascii=False, indent=2)
        assert quoted in content, content
        assert f"`{reports[drafted.project]}`" in content, content
        assert f"`{reports[other.project]}`" not in content, content
        assert f"`{stale}`" not in content and "(stale)" not in content, content
    approved = _just("approve-design", _landed(bench, first), environment=bench.environment)
    assert approved.returncode == OK, approved.stdout + approved.stderr

    old = _draft("finish-old-visual-id")
    old_report = _report(bench, old, images, changes=changes_of(old.project), report_id=legacy)
    assert old_report.partition(":")[2] == legacy
    content = tail(old, old_report)
    assert json.dumps(changes_of(old.project), ensure_ascii=False, indent=2) in content, content
    assert f"`{old_report}`" in content, content
    approved = _just("approve-design", _landed(bench, old), environment=bench.environment)
    assert approved.returncode == OK, approved.stdout + approved.stderr
