"""A design document copied onto the `plans` board is approved there, through the real recipes.

The user reads every plan as its design document on the board and records their approval
on that board copy, because the board copy is the artifact they judged. The plan store's
copy rewrites the document's references to the plan's tasks — each authoring location
becomes the task's board issue URL — and a store before onetaskgraph 0.2.53 left the
recorded `body_digest` describing the pre-copy rendering, so `just approve-design` refused
every board copy as edited after it was rendered. These journeys hold both halves of the
adoption: a copy made the way `just finish-plan` makes it is approved on the board and
accepted by the launch gate, and a copy carrying the stale digest an earlier store left is
refused naming the re-copy that repairs it rather than an edit nobody made.

Everything is real but GitHub's Projects API, which is the loopback double
`tests/github_board.py` serves, and the paid model `reviewed` scripts: the recipes, the
installed plan store and engine, and this host's `design-doc` template as it resolves now.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

import pytest
from github_board import (
    AUTHORING_ROOT_ENV,
    AUTHORING_SOURCE,
    BOARD,
    _Issue,
    _plan_environment,
    _serving_board,
)
from project_fixtures import designed, reviewed
from published_tools import ONETASKGRAPH_BIN
from waits import timeout as e2e_timeout

from orchestrator import design_approval, plan_check, plan_store
from orchestrator.root import REPO_ROOT

#: The plan these journeys author: two tasks rendered from this host's `plan-task`
#: template, the second depending on the first, with criteria that answer what `just
#: review-plan` and `just copy-plan`'s pre-flight read.
RENDERED_PROJECT = "board-approval"
RENDERED_QUALIFIED = f"{AUTHORING_SOURCE}:{RENDERED_PROJECT}"


class _RenderedNode(NamedTuple):
    """One of the plan's tasks: its node id and the title it is filed under."""

    node: str
    title: str


RENDERED_FIRST = _RenderedNode("page-the-listing", "feat: page the node listing")
RENDERED_SECOND = _RenderedNode("follow-the-cursor", "feat: follow the cursor from the view")
CLOSING = (
    "Every claim the dispatch makes about the finished work is true of the tree as it "
    "finally stands."
)
RENDERED_ANSWERS: dict[str, dict[str, object]] = {
    RENDERED_FIRST.node: {
        "what": "Add the paginated listing and the test that drives it.",
        "why": "An operator cannot see past the first screen of nodes.",
        "acceptance_criteria": [
            "The route accepts a valid request and rejects an invalid one.",
            "A request-level test drives the route end to end and covers both paths.",
            CLOSING,
        ],
    },
    RENDERED_SECOND.node: {
        "what": "Follow the stated cursor from the browser view.",
        "why": "The listing's cursor is worth nothing until something reads it.",
        "acceptance_criteria": [
            "The view pages on the stated cursor and reports a rejected one.",
            "A browser-level test drives both of those paths end to end.",
            CLOSING,
        ],
    },
}


def _rendering_environment(root: Path) -> dict[str, str]:
    """The environment a planner renders, reviews and copies a plan of ``root`` in.

    Without the suite's opt-out for hand-written fixture plans, because this plan is a
    rendering; the locked installs lead the search path, as they do in a dispatch, and the
    engine is pointed at this checkout's template root, as the launch wrapper points it.
    """
    environment = _plan_environment(root)
    environment.pop(plan_check.REQUIRE_RENDERED_ENV, None)
    environment["PATH"] = f"{REPO_ROOT / '.venv' / 'bin'}{os.pathsep}{environment['PATH']}"
    environment["ONEPIPELINE_TEMPLATE_ROOT"] = str(REPO_ROOT / "templates")
    return environment


def _through_the_template(
    environment: Mapping[str, str], *arguments: str
) -> subprocess.CompletedProcess[str]:
    """`onepipeline template resolve plan-task --json` piped into one `onetaskgraph task` verb."""
    return subprocess.run(
        [
            "bash",
            "-c",
            "set -euo pipefail; "
            'onepipeline template resolve plan-task --json | onetaskgraph task "$@"',
            "through-the-template",
            *arguments,
        ],
        cwd=REPO_ROOT,
        env=dict(environment),
        text=True,
        capture_output=True,
        check=False,
    )


def _recipe(environment: Mapping[str, str], *arguments: str) -> subprocess.CompletedProcess[str]:
    """One of this checkout's recipes, as an operator runs it."""
    return subprocess.run(
        ["just", *arguments],
        cwd=REPO_ROOT,
        env=dict(environment),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(600),
        check=False,
    )


def _copied(reported: str) -> dict[str, str]:
    """Each copied record's destination id by its source id, off `just copy-plan`'s report."""
    return {
        str(entry["source"]): str(entry["destination"])
        for entry in (json.loads(line) for line in reported.splitlines() if line.startswith("{"))
        if entry.get("destination")
    }


#: The design document `designed` renders for :data:`RENDERED_PROJECT`, by its qualified id.
DESIGN = f"{AUTHORING_SOURCE}:{RENDERED_PROJECT}-design"


def _authored(tmp_path: Path, root: Path, environment: Mapping[str, str]) -> dict[str, str]:
    """Author :data:`RENDERED_PROJECT` as a planner does, answering each task's id by its title.

    The project record is written as a planner writes it and each task is created through
    the engine's `plan-task` template, as a planner does; the design document is left to
    the journey, because what it locates is the point.
    """
    (root / "projects").mkdir(parents=True, exist_ok=True)
    # llmlint: ignore[tests_mirror_real_usage] The plan store has no project-create verb, and `personas/planner.yaml` has a planner author a plan as one `projects/<project>.md` record under the source's root before creating its tasks through the store, so this writes it exactly as a planner does.  # noqa: E501
    (root / "projects" / f"{RENDERED_PROJECT}.md").write_text(
        f'---\ntitle: "{RENDERED_PROJECT}"\nstatus: "todo"\nmetadata:\n'
        '  "onepipeline.schema_version": 3\n'
        '  "onepipeline.goal": {"text": "Page the node listing and follow its cursor"}\n---\n\n'
        f"Execution plan {RENDERED_PROJECT}.\n",
        encoding="utf-8",
    )
    created: dict[str, str] = {}
    for node, title in (RENDERED_FIRST, RENDERED_SECOND):
        answered = tmp_path / f"{node}.json"
        answered.write_text(json.dumps(RENDERED_ANSWERS[node]), encoding="utf-8")
        depends = [f"--depends-on={created[RENDERED_FIRST.title]}"] if created else []
        made = _through_the_template(
            environment,
            "create",
            AUTHORING_SOURCE,
            "--template-loader",
            "-",
            "--no-interactive",
            "--project",
            RENDERED_PROJECT,
            "--title",
            title,
            "--answers",
            str(answered),
            f"--metadata=onepipeline.id={json.dumps(node)}",
            '--metadata=onepipeline.persona="engineer"',
            *depends,
        )
        assert made.returncode == 0, made.stdout + made.stderr
        created[title] = made.stdout.strip()
    return created


def _location(environment: Mapping[str, str], qualified: str) -> str:
    """Where the authoring store reports ``qualified`` is, as `task show` answers it."""
    shown = subprocess.run(
        [str(ONETASKGRAPH_BIN), "task", "show", qualified, "--json"],
        cwd=REPO_ROOT,
        env=dict(environment),
        text=True,
        capture_output=True,
        check=False,
    )
    assert shown.returncode == 0, shown.stdout + shown.stderr
    location = json.loads(shown.stdout)["items"][0]["item"]["location"]["path"]
    assert isinstance(location, str) and location, shown.stdout
    return location


class _Drafted(NamedTuple):
    """The plan :func:`_drafted_for_the_board` leaves ready to copy where it was drafted."""

    #: The environment the authoring store, the engine and the recipes run in.
    environment: dict[str, str]
    #: Where the authoring store reports the first task is, which the document names.
    located: str
    #: The authored task ids, by title.
    tasks: dict[str, str]


def _drafted_for_the_board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, approve: bool = True
) -> _Drafted:
    """Author, design and review the plan where it was drafted, and approve it there by default.

    Answers the authoring environment, the first task's authoring location, and the
    authored task ids by title. The design document's Planned tasks table names the first
    task by the location the authoring store reports for it, the way the writer's table
    locates a task, so the copy has a reference to rewrite. `approve=False` leaves the
    authoring copy unapproved, as `just finish-plan` leaves it, so the board copy is the
    only one carrying an approval.
    """
    root = tmp_path / "authoring"
    root.mkdir()
    # `reviewed` and the in-process gate read the store in this process's environment.
    monkeypatch.setenv(AUTHORING_ROOT_ENV, str(root))
    environment = _rendering_environment(root)
    tasks = _authored(tmp_path, root, environment)
    located = _location(environment, tasks[RENDERED_FIRST.title])
    designed(
        AUTHORING_SOURCE,
        RENDERED_PROJECT,
        [
            {
                "task": RENDERED_FIRST.title,
                "delivers": "the paginated listing",
                "depends_on": "none",
                "location": located,
            },
            {
                "task": RENDERED_SECOND.title,
                "delivers": "the view that follows the cursor",
                "depends_on": RENDERED_FIRST.title,
                "location": "after the listing",
            },
        ],
        environment,
    )
    reviewed(RENDERED_QUALIFIED)
    if approve:
        approved = _recipe(environment, "approve-design", RENDERED_QUALIFIED)
        assert approved.returncode == 0, approved.stdout + approved.stderr
    return _Drafted(environment, located, tasks)


def _launch_gate(environment: Mapping[str, str], project: str) -> subprocess.CompletedProcess[str]:
    """The design-approval gate `scripts/onepipeline.sh` runs before every `start`, on ``project``.

    Its real entry point, handed the arguments a launch of ``project`` hands it.
    """
    return subprocess.run(
        ["uv", "run", "orchestrator-launch-gate", project],
        cwd=REPO_ROOT,
        env=dict(environment),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )


def _issue_titled(title: str) -> _Issue:
    (issue,) = [one for one in BOARD.issues_created_and_kept() if one.title == title]
    return issue


def _on_the_board(monkeypatch: pytest.MonkeyPatch, remote: Mapping[str, str]) -> None:
    """Point this process's own store reads at the served board, as the recipes' are."""
    for name, value in remote.items():
        monkeypatch.setenv(name, value)


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] `board-copy-approval` is
# this directory's own leaf project, keyed on `boardCopyApprovalWorkspace`, the edge this rule
# asks for; that key names the recipes, scripts and package these journeys drive, so narrowing
# it would memoize a verdict over a tree never run.
# llmlint: ignore-block[e2e_not_mocked] GitHub's Projects API is the one boundary doubled,
# for the reason the block around `_Board` gives — driving it writes to the live board this
# repository plans on — and the paid model `reviewed` scripts is the other; the recipes, the
# pinned engine and plan store, and the records they write are all real.
def test_a_design_document_copied_onto_the_board_is_approved_there_and_launchable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`just copy-plan` then `just approve-design` on the board copy, and the gate accepts it.

    The copy's content names the task's board issue URL where the authoring copy named its
    location, so the board copy is not the bytes the writer rendered — and the adopted store
    records the digest of what it wrote, which is what lets the approval be recorded there.
    """
    environment, located, _ = _drafted_for_the_board(tmp_path, monkeypatch)
    with _serving_board() as remote:
        on_the_board = {**environment, **remote}
        copied = _recipe(on_the_board, "copy-plan", RENDERED_QUALIFIED, "--to", "plans")
        assert copied.returncode == 0, copied.stdout + copied.stderr
        destinations = _copied(copied.stdout)
        board_project = destinations[RENDERED_QUALIFIED]
        issue_url = str(_issue_titled(RENDERED_FIRST.title).content()["url"])

        _on_the_board(monkeypatch, remote)
        document = design_approval.design_document(board_project)
        approved = _recipe(on_the_board, "approve-design", board_project)
        gated = _launch_gate(on_the_board, board_project)
        recorded = design_approval.recorded(design_approval.design_document(board_project))

    assert str(document.qualified_id) == destinations[DESIGN], (document, destinations)
    assert issue_url in document.content, (
        f"the board copy has to name the first task's board issue {issue_url} where the "
        f"authoring copy named its location:\n{document.content}"
    )
    assert located not in document.content, (
        f"the board copy still names the authoring location {located}:\n{document.content}"
    )
    assert approved.returncode == 0, approved.stdout + approved.stderr
    assert "recorded the approval of" in approved.stdout, approved.stdout
    assert gated.returncode == 0, (
        f"the launch gate has to accept {board_project} once its board copy is approved, "
        f"and answered {gated.stdout}{gated.stderr}"
    )
    assert recorded is not None
    # llmlint: ignore-end[e2e_not_mocked]
    # llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


#: Where the plan store keeps a rendering's provenance on a document, beside its content.
PROVENANCE = design_approval.PROVENANCE


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] `board-copy-approval` is
# this directory's own leaf project, keyed on `boardCopyApprovalWorkspace`, the edge this rule
# asks for; that key names the recipes, scripts and package these journeys drive, so narrowing
# it would memoize a verdict over a tree never run.
# llmlint: ignore-block[e2e_not_mocked] The same one doubled boundary as the journey above.
# llmlint: ignore-block[tests_mirror_real_usage] The stale digest is what onetaskgraph before
# 0.2.53 wrote onto every board copy whose references it rewrote, and no adopted release can
# write it any more, so it is put on the double's issue exactly where that store left it: the
# copy is otherwise the adopted store's own, through the real recipe.
def test_a_board_copy_carrying_an_earlier_stores_stale_digest_is_refused_naming_the_re_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A copy whose content does not hash to its recorded digest gets no approval, and says why.

    What an earlier plan store left on the board: references rewritten, digest naming the
    authoring rendering. `just approve-design` exits non-zero naming that cause and the
    `just copy-plan` that re-records it beside the regenerate for a hand edit, and nothing
    is recorded on the copy.
    """
    environment, _, _ = _drafted_for_the_board(tmp_path, monkeypatch)
    authored = plan_store.read_documents(RENDERED_QUALIFIED)[0]
    stale = authored.metadata[PROVENANCE]
    assert isinstance(stale, dict)
    with _serving_board() as remote:
        on_the_board = {**environment, **remote}
        copied = _recipe(on_the_board, "copy-plan", RENDERED_QUALIFIED, "--to", "plans")
        assert copied.returncode == 0, copied.stdout + copied.stderr
        board_project = _copied(copied.stdout)[RENDERED_QUALIFIED]

        _on_the_board(monkeypatch, remote)
        restamped = design_approval.design_document(board_project).metadata[PROVENANCE]
        assert isinstance(restamped, dict)
        (issue,) = [
            one for one in BOARD.issues_created_and_kept() if restamped["body_digest"] in one.body
        ]
        issue.body = issue.body.replace(restamped["body_digest"], str(stale["body_digest"]))
        document = design_approval.design_document(board_project)

        refused = _recipe(on_the_board, "approve-design", board_project)
        after = design_approval.design_document(board_project)
        gated = _launch_gate(on_the_board, board_project)

    held = "sha256:" + hashlib.sha256(document.content.encode("utf-8")).hexdigest()
    assert document.metadata[PROVENANCE] == {**restamped, "body_digest": stale["body_digest"]}
    assert held != stale["body_digest"], "the stale copy has to be one a check can tell apart"
    assert refused.returncode != 0, refused.stdout + refused.stderr
    said = " ".join(refused.stderr.split())
    assert "earlier plan store" in said and "without re-recording its digest" in said, said
    assert f"a copy of {DESIGN}" in said, said
    assert f"`just copy-plan {AUTHORING_SOURCE}:<project>`" in said, said
    assert "re-records it" in said and "regenerate it with" in said, said
    # The record the authoring copy's approval left travels with every copy, by design; what
    # the refusal owes is that nothing was written over it and that it approves nothing here.
    record = design_approval.RECORD_KEY
    assert after.metadata.get(record) == document.metadata.get(record), after.metadata
    assert gated.returncode == 1, gated.stdout + gated.stderr
    assert "earlier plan store" in " ".join(gated.stderr.split()), gated.stderr
    # llmlint: ignore-end[e2e_not_mocked, tests_mirror_real_usage]
    # llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] `board-copy-approval` is
# this directory's own leaf project, keyed on `boardCopyApprovalWorkspace`, the edge this rule
# asks for; that key names the recipes, scripts and package these journeys drive, so narrowing
# it would memoize a verdict over a tree never run.
# llmlint: ignore-block[e2e_not_mocked] The same one doubled boundary as the journeys above.
def test_an_approval_recorded_on_the_board_copy_survives_a_re_copy_of_the_unchanged_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`just copy-plan` again after `just approve-design` on the board leaves the plan launchable.

    The approval is metadata only the board copy holds, and a store before onetaskgraph
    0.3.10 replaced a destination's metadata with the source's on every re-copy, so
    re-running `just finish-plan` over a plan nobody changed deleted the approval the user
    gave and the launch refused it. The adopted store keeps it. The authoring copy is left
    unapproved, as `just finish-plan` leaves it: a key both copies hold is the source's on
    a re-copy, so an approval recorded where the plan was drafted would replace the board's.
    """
    environment, _, _ = _drafted_for_the_board(tmp_path, monkeypatch, approve=False)
    with _serving_board() as remote:
        on_the_board = {**environment, **remote}
        copied = _recipe(on_the_board, "copy-plan", RENDERED_QUALIFIED, "--to", "plans")
        assert copied.returncode == 0, copied.stdout + copied.stderr
        board_project = _copied(copied.stdout)[RENDERED_QUALIFIED]

        _on_the_board(monkeypatch, remote)
        approved = _recipe(on_the_board, "approve-design", board_project)
        assert approved.returncode == 0, approved.stdout + approved.stderr
        approval = design_approval.recorded(design_approval.design_document(board_project))
        before = design_approval.design_document(board_project).content

        recopied = _recipe(on_the_board, "copy-plan", RENDERED_QUALIFIED, "--to", "plans")
        after = design_approval.design_document(board_project)
        gated = _launch_gate(on_the_board, board_project)

    assert recopied.returncode == 0, recopied.stdout + recopied.stderr
    assert _copied(recopied.stdout)[RENDERED_QUALIFIED] == board_project, recopied.stdout
    assert after.content == before, "the re-copied plan has to be the unchanged one"
    assert approval is not None
    assert design_approval.recorded(after) == approval, (
        f"the re-copy has to keep the approval recorded on {board_project}'s board copy; "
        f"it now carries {after.metadata.get(design_approval.RECORD_KEY)!r}"
    )
    assert gated.returncode == 0, (
        f"the launch gate has to accept {board_project} after a re-copy of the unchanged "
        f"plan, and answered {gated.stdout}{gated.stderr}"
    )
    # llmlint: ignore-end[e2e_not_mocked]
    # llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
