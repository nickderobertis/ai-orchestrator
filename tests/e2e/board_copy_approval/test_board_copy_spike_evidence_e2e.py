"""A finalized plan's spike evidence still resolves once the plan is copied onto the board.

The finalize planner links each task to its spikes' reports, by the document ids they were
written under where the plan was drafted. The board mints every copied record an id of its
own, so on the board copy no document carries those ids. Plans are drafted in `authoring`,
which the tracked document declares private, and a copy out of a private source carries no
origin: the store records the correspondence on the private side as its `onetaskgraph.copies`
link, and a report is the `spike-report` document of the board copy's project whose id the
drafting source's link names. A copy out of a source not declared private is stamped with
`onetaskgraph.origin`, the record it was copied from, and its report is the one whose origin
names the id the task links. So `just check-plan` passes the board copy, each task's report
resolves inside the board copy's own project, and the report and branch rules stand as they did
where the plan was drafted — the branch of a spike its engine retried among them.

Everything is real but GitHub's Projects API, which the loopback board `tests/github_board.py`
serves, and the paid model `reviewed` scripts: `just copy-plan` and `just check-plan`, the
installed plan store and engine, this host's templates, and `orchestrator/spike_plan.py`
read in this process against the store the recipes wrote.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

import boundary_home
import pytest
from github_board import (
    AUTHORING_ROOT_ENV,
    AUTHORING_SOURCE,
    _audit_follow_up_calls,
    _plan_environment,
    _serving_board,
)
from project_fixtures import budgeted, designed, no_budgets, reviewed
from published_tools import LOCKED_INSTALLS_PATH
from test_board_copy_design_approval_e2e import _copied, _on_the_board, _recipe

from orchestrator import design_approval, plan_store, spike_plan
from orchestrator.plan_store import QualifiedProjectId
from orchestrator.root import REPO_ROOT

#: The finalized plan this journey copies, and the repository its tasks change.
PROJECT = "spiked-board"
QUALIFIED = f"{AUTHORING_SOURCE}:{PROJECT}"
REPOSITORY = "github.com/nickderobertis/ai-orchestrator"

#: The store's environment-layer name for the drafting source's declared visibility. The
#: tracked document declares `authoring` private; a journey about the origin a copy out of a
#: source not declared private carries states it public.
AUTHORING_VISIBILITY_ENV = f"ONETASKGRAPH_SOURCES__{AUTHORING_SOURCE.upper()}__VISIBILITY"

CLOSING = (
    "Every claim the dispatch makes about the finished work is true of the tree as it "
    "finally stands."
)


class Linked(NamedTuple):
    """One task of the plan and the spike its `spikes` answer links."""

    node: str
    title: str
    spike: str
    branch: str


#: Two tasks, each building on a spike: the second's spike was retried by the engine, whose
#: retry cut a branch named for itself.
LINKED = (
    Linked("listing", "feat: page the listing", "spike-listing", f"nick/{PROJECT}/spike-listing"),
    Linked("quota", "feat: hold the quota", "spike-quota", f"nick/{PROJECT}/spike-quota-2"),
)


def _environment(root: Path) -> dict[str, str]:
    """The environment a planner renders, reviews and copies the plan of ``root`` in."""
    environment = _plan_environment(root)
    environment["PATH"] = f"{LOCKED_INSTALLS_PATH}{os.pathsep}{environment['PATH']}"
    environment["ONEPIPELINE_TEMPLATE_ROOT"] = str(REPO_ROOT / "templates")
    return environment


def _rendered(
    environment: Mapping[str, str], template: str, *store: str, answers: Mapping[str, object]
) -> None:
    """The engine's `template resolve` piped into one store verb, as a planner renders."""
    path = Path(environment[AUTHORING_ROOT_ENV]) / f"{template}.json"
    path.write_text(json.dumps(answers), encoding="utf-8")
    made = subprocess.run(
        ["bash", "-c", f'onepipeline template resolve {template} --json | onetaskgraph "$@"', "-"]
        + [*store, "--template-loader", "-", "--answers", str(path), "--no-interactive"],
        cwd=REPO_ROOT,
        env=dict(environment),
        text=True,
        capture_output=True,
        check=False,
    )
    assert made.returncode == 0, made.stdout + made.stderr


def _finalized(root: Path, environment: Mapping[str, str]) -> None:
    """The plan as its finalize planner leaves it: tasks linked to reports the spikes wrote."""
    (root / "projects").mkdir(parents=True)
    # llmlint: ignore[tests_mirror_real_usage] The plan store has no project-create verb, and `personas/planner.yaml` has a planner author a plan as one `projects/<project>.md` record before creating its tasks through the store, so this writes it as a planner does.  # noqa: E501
    (root / "projects" / f"{PROJECT}.md").write_text(
        f'---\ntitle: "{PROJECT}"\nstatus: "todo"\nmetadata:\n'
        '  "onepipeline.schema_version": 3\n'
        '  "onepipeline.goal": {"text": "Page the listing within its quota"}\n---\n\nPlan.\n',
        encoding="utf-8",
    )
    for one in LINKED:
        report = spike_plan.report_id(PROJECT, plan_store.NodeId(one.spike))
        _rendered(
            environment,
            "spike-report",
            "document",
            "create",
            AUTHORING_SOURCE,
            "--project",
            PROJECT,
            "--title",
            f"Spike report: {one.spike}",
            "--id",
            report,
            answers={
                "spike": one.spike,
                "branch": one.branch,
                "harness": "`harness.sh` at the branch's root; run `sh harness.sh`.",
                "method": "Twenty pages of the real listing at 2,000 nodes.",
                "candidates": [
                    {
                        "budget": "listing-latency",
                        "measure": "time to the first page",
                        "workload": "2,000 nodes",
                        "achievable": "420 ms",
                        "limits": "5,000 calls an hour",
                        "consumed": "20 calls",
                    }
                ],
                "findings": [],
            },
        )
        _rendered(
            environment,
            "plan-task",
            "task",
            "create",
            AUTHORING_SOURCE,
            "--project",
            PROJECT,
            "--title",
            one.title,
            f"--metadata=onepipeline.id={json.dumps(one.node)}",
            '--metadata=onepipeline.persona="engineer"',
            '--metadata=orchestrator.unit="Listing"',
            "--repository",
            REPOSITORY,
            answers={
                "what": f"Build {one.node} from what its spike measured.",
                "why": "An operator cannot see past the first screen.",
                "acceptance_criteria": [
                    "The route accepts a valid request and rejects an invalid one.",
                    "A request-level test drives the route end to end and covers both paths.",
                    CLOSING,
                ],
                "spikes": [{"spike": one.spike, "report": report, "branch": one.branch}],
            },
        )
    budgeted(AUTHORING_SOURCE, PROJECT, no_budgets([REPOSITORY]), dict(environment))
    reviewed(QUALIFIED)


# llmlint: ignore-block[e2e_not_mocked] GitHub's Projects API is the one boundary doubled, for the reason `tests/github_board.py` gives — driving it writes to the live board this repository plans on — and the paid model `reviewed` scripts is the other; the recipes, the pinned engine and plan store, and the records they write are all real.  # noqa: E501
# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] `board-copy-approval` is this directory's own leaf project, keyed on `boardCopyApprovalWorkspace`, which names the recipes, scripts and package this journey drives.  # noqa: E501
@pytest.mark.parametrize("private", [False, True], ids=["authoring-public", "authoring-private"])
def test_a_finalized_plan_copied_onto_the_board_resolves_and_checks_each_tasks_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, private: bool
) -> None:
    """Each report resolves on the board copy, by its origin or by the drafting source's link.

    The plan's design document is copied beside them and approved on the board copy, the
    approval reading the drafting source's links at most once.
    Drafted where the tracked document declares it, in the private `authoring`, the copy
    carries no `onetaskgraph.origin`: the store records the correspondence on the private
    side, as `onetaskgraph.copies`, and the report resolves through that link
    (`spike_plan.drafted_ids`) instead. Drafted in a source not declared private, the copy
    carries its origin, and the report resolves by that.
    """
    root = tmp_path / "authoring"
    root.mkdir()
    monkeypatch.setenv(AUTHORING_ROOT_ENV, str(root))
    if not private:
        monkeypatch.setenv(AUTHORING_VISIBILITY_ENV, "public")
    # The store's public boundary reads the plan's repository as public through a state root
    # registering it, so the plan may reach the public board.
    boundary_home.answering(monkeypatch, public=(REPOSITORY,))
    environment = _environment(root)
    _finalized(root, environment)
    designed(AUTHORING_SOURCE, PROJECT, [], environment)
    drafted = _recipe(environment, "check-plan", QUALIFIED)
    assert drafted.returncode == 0, drafted.stdout + drafted.stderr

    with _serving_board() as remote:
        on_the_board = {**environment, **remote}
        copied = _recipe(on_the_board, "copy-plan", QUALIFIED, "--to", "plans")
        assert copied.returncode == 0, copied.stdout + copied.stderr
        board = QualifiedProjectId(_copied(copied.stdout)[QUALIFIED])
        checked = _recipe(on_the_board, "check-plan", board)
        audited = dict(on_the_board)
        trace = _audit_follow_up_calls(tmp_path, audited)
        approved = _recipe(audited, "approve-design", board)
        _on_the_board(monkeypatch, remote)
        tasks = plan_store.read_tasks(board)
        documents = plan_store.read_documents(board)
        origins = spike_plan.drafted_ids(board)

    assert checked.returncode == 0, (
        f"`just check-plan` has to pass the board copy {board}, whose reports the board gave "
        f"ids of its own:\n{checked.stdout}{checked.stderr}"
    )
    assert approved.returncode == 0, approved.stdout + approved.stderr
    assert "recorded the approval of" in approved.stdout, approved.stdout
    # What the drafting source holds of the copy is read once for the design document, its
    # reports and the plan's drafted id together: the drafted project always, since the
    # visual report is named by it, and that project's documents only where a copy out of a
    # private source left them without an origin.
    store = [
        entry["argv"][1:]
        for entry in map(json.loads, trace.read_text(encoding="utf-8").splitlines())
        if entry["kind"] == "store"
    ]
    verbs = [tuple(argv[:2]) for argv in store]
    assert verbs.count(("config", "show")) == verbs.count(("project", "list")) == 1, store
    drafted_documents = [
        argv
        for argv in store
        if argv[:2] == ["document", "list"] and argv[argv.index("--source") + 1] == AUTHORING_SOURCE
    ]
    assert len(drafted_documents) == (1 if private else 0), store
    assert {task.node_id for task in tasks} == {one.node for one in LINKED}
    for task in tasks:
        expected = next(one for one in LINKED if one.node == task.node_id)
        (linked,) = spike_plan.evidence(task.content or "")
        assert (linked.spike, linked.branch) == (expected.spike, expected.branch), linked
        # The board gave the report an id of its own, so the task's id names no document
        # there; the store's record of what the copy came from is what finds it.
        assert all(
            str(held.qualified_id).partition(":")[2] != linked.report for held in documents
        ), [held.qualified_id for held in documents]
        report = spike_plan.report_for(linked, documents, origins)
        assert report is not None, (
            f"{task.node_id}'s report {linked.report} resolves to no document of {board}: "
            f"{[(one.qualified_id, one.metadata.get(plan_store.ORIGIN_KEY)) for one in documents]}"
        )
        assert str(report.project) == board.partition(":")[2], report
        assert design_approval.is_spike_report(report), report.metadata
        # The store links each drafted document to its copy whatever the source's visibility;
        # only a copy out of a source declared private goes without an origin.
        assert origins[str(report.qualified_id)] == f"{AUTHORING_SOURCE}:{linked.report}"
        assert report.metadata.get(plan_store.ORIGIN_KEY) == (
            None if private else f"{AUTHORING_SOURCE}:{linked.report}"
        ), report.metadata
        assert f"# Spike `{expected.spike}`" in report.content, report.content
        assert f"`{expected.branch}`" in report.content, report.content
        # The task tells its worker how to find it there.
        assert "the one whose `onetaskgraph.origin`" in (task.content or "")


def _visual_report(environment: Mapping[str, str], report: str | None = None) -> None:
    """The plan's visual spike report, rendered where it is drafted, as that spike writes it.

    Under ``report`` where one is named: the id a report was written under before ids named
    their plan, say.
    """
    _rendered(
        environment,
        "spike-report",
        "document",
        "create",
        AUTHORING_SOURCE,
        "--project",
        PROJECT,
        "--title",
        f"Spike report: {spike_plan.VISUAL_SPIKE}",
        "--id",
        report or spike_plan.visual_report_id(PROJECT),
        answers={
            "spike": spike_plan.VISUAL_SPIKE,
            "branch": f"nick/{PROJECT}/{spike_plan.VISUAL_SPIKE}",
            "harness": "`capture.sh` at the branch's root; run `sh capture.sh`.",
            "method": "Screenshots of the listing before and after, at one viewport.",
            "candidates": [],
            "findings": [],
        },
    )


def test_a_visual_report_copied_out_of_a_private_source_resolves_and_an_unreadable_one_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The board copy's visual report resolves through the drafting source's copy link.

    Found canonically, by the drafted id the report was written under, and when the board
    copy's project selects it by that id; and where the drafting source holds no project
    linking the copy, or its documents or the source itself cannot be read, nothing resolves
    through a link at all, so a copy carrying no origin is left unresolved rather than misread.
    """
    root = tmp_path / "authoring"
    root.mkdir()
    monkeypatch.setenv(AUTHORING_ROOT_ENV, str(root))
    boundary_home.answering(monkeypatch, public=(REPOSITORY,))
    environment = _environment(root)
    _finalized(root, environment)
    _visual_report(environment)
    drafted_visual = f"{AUTHORING_SOURCE}:{spike_plan.visual_report_id(PROJECT)}"

    with _serving_board() as remote:
        on_the_board = {**environment, **remote}
        copied = _recipe(on_the_board, "copy-plan", QUALIFIED, "--to", "plans")
        assert copied.returncode == 0, copied.stdout + copied.stderr
        board = QualifiedProjectId(_copied(copied.stdout)[QUALIFIED])
        _on_the_board(monkeypatch, remote)
        documents = plan_store.read_documents(board)
        canonical = spike_plan.current_visual_report(board, documents)
        selected = subprocess.run(
            [str(plan_store.locked_binary()), "project", "metadata", "set", board]
            + [spike_plan.VISUAL_REPORT_KEY, json.dumps(drafted_visual)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert selected.returncode == 0, selected.stderr
        chosen = spike_plan.current_visual_report(board, documents)

        # The drafting source readable but holding no project whose copy link names the board
        # copy: a fresh root drafted nothing there.
        unlinked_root = tmp_path / "unlinked-authoring"
        unlinked_root.mkdir()
        monkeypatch.setenv(AUTHORING_ROOT_ENV, str(unlinked_root))
        unlinked_source = spike_plan.drafted_ids(board)
        monkeypatch.setenv(AUTHORING_ROOT_ENV, str(root))
        # The drafting source's documents unreadable: a record the store cannot parse.
        (root / "documents" / "broken.md").write_text("---\n: [\n---\n", encoding="utf-8")
        unreadable_documents = spike_plan.drafted_ids(board)
        # The drafting source itself unreadable: its root is gone.
        monkeypatch.setenv(AUTHORING_ROOT_ENV, str(tmp_path / "gone"))
        unreadable_source = spike_plan.drafted_ids(board)

    assert canonical is not None, [one.qualified_id for one in documents]
    assert plan_store.ORIGIN_KEY not in canonical.metadata, canonical.metadata
    assert design_approval.is_spike_report(canonical), canonical.metadata
    assert f"# Spike `{spike_plan.VISUAL_SPIKE}`" in canonical.content, canonical.content
    assert chosen == canonical
    assert unlinked_source == {}
    assert unreadable_documents == {}
    assert unreadable_source == {}
    evidence = spike_plan.Evidence(
        spike_plan.VISUAL_SPIKE,
        spike_plan.visual_report_id(PROJECT),
        spike_plan.Branch(f"nick/{PROJECT}/{spike_plan.VISUAL_SPIKE}"),
    )
    for links, why in (
        (unlinked_source, "no drafted project linking it"),
        (unreadable_source, "its drafting source unreadable"),
    ):
        held = [one for one in documents if spike_plan.report_for(evidence, [one], links)]
        assert held == [], f"a copy carrying no origin resolved with {why}"


def test_a_copys_reports_resolve_by_their_origin_with_the_authoring_source_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The board copy's own id names no plan, so each report's origin names it instead.

    Copied where `authoring` is declared public, rather than private as the tracked document
    declares it, each report carries the `onetaskgraph.origin` it was copied from. With the
    authoring source then unreadable, nothing links the copy to its drafted plan, so the plan's
    id is read off those origins: the evidence check passes each task's qualified report, and the
    visual fallback takes the qualified visual report over a stale one left under the old
    `spike-visual-report`.
    """
    root = tmp_path / "authoring"
    root.mkdir()
    monkeypatch.setenv(AUTHORING_ROOT_ENV, str(root))
    monkeypatch.setenv(AUTHORING_VISIBILITY_ENV, "public")
    boundary_home.answering(monkeypatch, public=(REPOSITORY,))
    environment = _environment(root)
    _finalized(root, environment)
    _visual_report(environment, spike_plan.legacy_report_id(spike_plan.VISUAL_SPIKE))
    _visual_report(environment)
    gone = str(tmp_path / "gone")

    with _serving_board() as remote:
        on_the_board = {**environment, **remote}
        copied = _recipe(on_the_board, "copy-plan", QUALIFIED, "--to", "plans")
        assert copied.returncode == 0, copied.stdout + copied.stderr
        board = QualifiedProjectId(_copied(copied.stdout)[QUALIFIED])
        checked = _recipe({**on_the_board, AUTHORING_ROOT_ENV: gone}, "check-plan", board)
        _on_the_board(monkeypatch, remote)
        monkeypatch.setenv(AUTHORING_ROOT_ENV, gone)
        documents = plan_store.read_documents(board)
        native = spike_plan.plan_native(board)
        visual = spike_plan.current_visual_report(board, documents)

    assert native == board.partition(":")[2] != PROJECT
    assert checked.returncode == 0, (
        f"`just check-plan` has to pass the board copy {board} by its reports' origins with "
        f"the authoring source unreadable:\n{checked.stdout}{checked.stderr}"
    )
    assert visual is not None, [
        (one.qualified_id, one.metadata.get(plan_store.ORIGIN_KEY)) for one in documents
    ]
    assert visual.metadata.get(plan_store.ORIGIN_KEY) == (
        f"{AUTHORING_SOURCE}:{spike_plan.visual_report_id(PROJECT)}"
    ), visual.metadata


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
# llmlint: ignore-end[e2e_not_mocked]
