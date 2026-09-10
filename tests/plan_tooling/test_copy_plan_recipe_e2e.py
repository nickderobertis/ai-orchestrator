"""The order a plan of this repository reaches its board in, driven end to end.

A plan is drafted in a local Markdown source, cleared there by `just review-plan`, and
only then copied onto the board it is launched from. No other order works: a review
record is one entry of the task's own Markdown document, so a plan authored on the board
can never carry one, and `just check-plan` refuses it for want of one while `just
review-plan` has nowhere to put one. `just copy-plan` is the step that was missing, and
what it adds is that the ordering is enforced by a command rather than remembered.

Everything below the recipe is real: the real `just` recipes, the real
`orchestrator-copy-plan` and `orchestrator-check-plan`, the real installed
`onetaskgraph` and its own `project copy` verb, the real registered check script, and
real local Markdown stores at both ends. `tests/e2e/fake_codex.py` stands in for the
paid provider alone, through `project_fixtures.reviewed`, at the one seam a single-sided
oneharness turn reaches it through.

**The destination is a second local store and never the live board.** A journey that
wrote to the real `plans` board would be its own rate-limit burst and would leave a
project behind on the store every other run of this repository reads. It is added
through the store's own `ONETASKGRAPH_` environment layer rather than through a `--set`
flag, because that layer is the one the whole command sees — the pre-flight read as well
as the copy — so both halves are answering about one configuration.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import NewType

import pytest
from project_fixtures import approved, local_project, reviewed
from published_tools import ONETASKGRAPH_BIN
from waits import timeout as e2e_timeout

from orchestrator import design_approval, plan_review, plan_store
from orchestrator.criteria_guard import APPENDIX
from orchestrator.project_store import frontmatter
from orchestrator.root import REPO_ROOT

#: This suite is its own Nx project, `plan-tooling`; see
#: `tests/plan_tooling/project.json` and the guard in `tests/conftest.py`.

#: The source a plan is drafted in here — the suite's own local Markdown store, which is
#: what stands in for the gitignored `authoring` root an operator drafts into.
DRAFTED_IN = "test-fixtures"

#: The source a plan is copied into here, standing in for the `plans` board. A plain
#: lowercase name because it is spelled into the store's own `ONETASKGRAPH_SOURCES__…`
#: environment layer as well as onto a command line.
DESTINATION = "destination"

#: The source `just copy-plan` copies onto when the caller names none. Spelled as a
#: literal rather than imported from `orchestrator/plan_copy.py`: importing the constant
#: would make this journey assert that it equals itself, and a default renamed in the
#: module would take the assertion with it. What ties this literal to a source the
#: configuration actually has is `tests/test_plan_source_roots.py`.
THE_BOARD = "plans"

#: Criteria that answer every demand the tracked appendix and the shipped `engineer`
#: bar make, so nothing here is refused for a reason this journey is not about.
STATES_ITS_BAR = (
    "- The route accepts a valid request and rejects an invalid one.\n"
    "- A request-level test drives the route end to end and covers both paths.\n"
    "- Every claim the dispatch makes about the finished work is true of the tree as "
    "it finally stands."
)

#: The same criteria with the end-to-end demand dropped, which is how one task of an
#: already-reviewed plan is edited back into being unreviewed.
EDITED = "- The route accepts every request it is given."


def _task(criteria: str = STATES_ITS_BAR) -> str:
    return (
        "## What\n\nAdd the route and the test that drives it.\n\n"
        "## Why\n\nThe user cannot complete a purchase without it.\n\n"
        f"## Acceptance criteria\n\n{criteria}\n\n"
        f"{(REPO_ROOT / APPENDIX).read_text(encoding='utf-8').strip()}\n"
    )


# Neither of these two is a domain object modelled as a dict: they are one JSON
# **document** built to be serialized on the next line and handed to the store CLI as an
# argument, which is a wire payload rather than a type this suite reasons in. The typed
# models the rule is about are the ones this repository reads records back into —
# `orchestrator/plan_store.py`'s `StoreTask` and its identifier `NewType`s — and this
# journey does use those where it locates a record to edit, in `_document` below. What
# it deliberately does not use them for is asserting on a landed record, for the reason
# `_stored_tasks` gives. Every sibling journey in this directory builds the same payload
# the same way — `tests/plan_tooling/test_check_plan_recipe_e2e.py` alone builds eleven —
# so a typed document here would make one file diverge from the convention, not raise it.
# llmlint: ignore-block[modern_domain_modeling] see the note above this line
def _node(node_id: str) -> dict[str, str]:
    return {
        "id": node_id,
        "persona": "engineer",
        "repo": "https://github.com/nickderobertis/some-service",
        "title": f"feat: add the {node_id}",
        "task": _task(),
    }


def _project(name: str, *node_ids: str) -> str:
    """A drafted, unreviewed plan project in the local store, and its qualified id."""
    return local_project(
        json.dumps(
            {
                "schema_version": 3,
                "goal": {"text": "Deliver the checkout route"},
                "tasks": [_node(one) for one in (node_ids or ("route",))],
            }
        ),
        name,
    )


# llmlint: ignore-end[modern_domain_modeling]


def _document(project: str, node_id: str) -> Path:
    """One task's record on disk, so a journey can edit it the way an operator would."""
    (task,) = [one for one in plan_store.read_tasks(project) if one.node_id == node_id]
    source, _, native = task.qualified_id.partition(":")
    return plan_store.task_document(source, native)


def _just(*arguments: str, seconds: float = 240) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["just", *arguments],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
    )


def _stored_tasks(project: str) -> list[dict[str, object]]:
    """Every task of ``project``, read through `just plans` — the operator's own surface.

    Deliberately not `plan_store.read_tasks`: what a journey asserts about a landed
    record should be what somebody reading the store would see, and this repository's own
    reader is a party to the copy rather than a witness of it.
    """
    source, _, native = project.partition(":")
    listed = _just(
        "plans", "task", "list", "--source", source, "--project", native, "--json", seconds=90
    )
    assert listed.returncode == 0, listed.stdout + listed.stderr
    items = json.loads(listed.stdout)["items"]
    return [one["item"] for one in items]


def _stored_documents(project: str) -> list[dict[str, object]]:
    """Every document of ``project``, read through `just plans` for the reason above."""
    listed = _just("plans", "document", "list", "--project", project, "--json", seconds=90)
    assert listed.returncode == 0, listed.stdout + listed.stderr
    items = json.loads(listed.stdout)["items"]
    return [one["item"] for one in items]


def _records(root: Path) -> list[str]:
    """Every record file the destination store holds, as paths below its root."""
    return sorted(str(one.relative_to(root)) for one in root.rglob("*.md")) if root.is_dir() else []


@pytest.fixture
def destination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A second local Markdown store, configured for this test process and its children.

    Set on this process rather than handed to one subprocess, because both halves of the
    flow have to see it: the recipes spawned here inherit it, and so does the in-process
    read this journey makes of the copied records.

    `ONETASKGRAPH_DEFAULT_SOURCES` is narrowed to the two sources in play, which is what
    lets `just check-plan` read a project of the destination at all — the engine lists a
    project's tasks without naming a source — and which also keeps the live `plans` board
    out of every read this journey makes.
    """
    root = tmp_path / "board"
    # Created rather than left to the first write: a `local-md` source canonicalizes its
    # root when it is built, so an absent one is refused as a broken source rather than
    # populated — which would report a sound copy as a destination that refused it.
    root.mkdir(parents=True)
    monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{DESTINATION.upper()}__PLUGIN", "local-md")
    monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{DESTINATION.upper()}__CONFIG__ROOT", str(root))
    monkeypatch.setenv("ONETASKGRAPH_DEFAULT_SOURCES", f"{DRAFTED_IN},{DESTINATION}")
    yield root


def test_a_plan_is_drafted_locally_cleared_there_copied_up_and_checked(
    destination: Path,
) -> None:
    """The whole flow in the order an operator meets it, on one plan.

    One journey rather than five, because these are five readings of a single act: the
    copy is refused until the plan is cleared, clearing it is what a local store is for,
    a trial run writes nothing, the copy carries the record it was cleared by, and the
    copy is then checkable where it landed. Split apart, each would pay for its own plan
    and its own review turn to reach the same state.
    """
    project = _project("copy-flow")
    _, _, native = project.partition(":")
    copied_id = f"{DESTINATION}:{native}"

    refused = _just("copy-plan", project, "--to", DESTINATION)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "no review record" in refused.stderr, refused.stderr
    assert "route" in refused.stderr, refused.stderr
    assert f"just review-plan {project}" in refused.stderr, refused.stderr
    # And the reason no flag will get past it: the record goes into a file.
    assert "a board is not a directory" in refused.stderr, refused.stderr
    assert _records(destination) == [], "a refused copy wrote to the destination"

    # `reviewed` reaches this record the way an operator does — the real `just
    # review-plan`, the real script, the real `oneharness` CLI and its response schema —
    # and substitutes the paid provider process alone, this suite's one sanctioned double.
    # llmlint: ignore[e2e_not_mocked] see the note above this line
    reviewed(project)

    trial = _just("copy-plan", project, "--to", DESTINATION, "--dry-run")
    assert trial.returncode == 0, trial.stdout + trial.stderr
    assert native in trial.stdout, trial.stdout
    assert _records(destination) == [], "a trial run wrote to the destination"

    copy = _just("copy-plan", project, "--to", DESTINATION)
    assert copy.returncode == 0, copy.stdout + copy.stderr
    # The plan's **document** lands beside its tasks, and that is the store's own
    # `project copy` being composed with rather than trusted: that verb carries a project
    # and its tasks and no document at all, so a plan copied without this arrives with
    # nothing for a person to approve it as and can never be launched.
    assert _records(destination) == [
        f"documents/{native}-design.md",
        f"projects/{native}.md",
        f"tasks/{native}/route.md",
    ], copy.stdout

    # The record travelled — read off the copy through the operator's own store surface
    # rather than through this repository's reader, so what is asserted is what somebody
    # looking at the landed task would see.
    (landed,) = _stored_tasks(copied_id)
    assert plan_review.RECORD_KEY in landed["metadata"], landed["metadata"]

    # So did the approval of the document this plan is read as, and for the same reason:
    # it is an ordinary entry of the record's own metadata map. That is what makes the
    # order work — a plan cleared and approved where it was drafted is still cleared and
    # approved once it reaches the store it is launched from, rather than having to be
    # put in front of a person a second time.
    (document,) = _stored_documents(copied_id)
    assert design_approval.RECORD_KEY in document["metadata"], document["metadata"]

    # And it is a record of the content that *arrived* rather than an opaque token that
    # merely survived the copy: `check-plan` recomputes the digest over the landed task
    # under the bar in force and accepts it, which is the same reading that refused this
    # plan before it was cleared.
    checked = _just("check-plan", copied_id)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "carries a review record" in checked.stdout, checked.stdout

    # And the third ending, which is neither refusal: a project the store cannot read at
    # all is nothing this command judged, so it says so under its own status rather than
    # under the one that means "nothing has reviewed this".
    unreadable = _just("copy-plan", f"no-such-source:{native}", "--to", DESTINATION)
    assert unreadable.returncode == 2, unreadable.stdout + unreadable.stderr
    assert "cannot read project" in unreadable.stderr, unreadable.stderr
    assert "no review record" not in unreadable.stderr, unreadable.stderr


def test_the_copy_and_the_check_name_the_same_unreviewed_tasks(destination: Path) -> None:
    """One question, one answer: both commands ask `plan_review.unreviewed`.

    A second implementation of how a record is keyed would agree here on the day it was
    written and diverge the first time either moved, which is why the copy calls the code
    the check calls rather than restating it. Driven the way the divergence would show:
    a plan cleared whole, then one task edited back out of its record.
    """
    project = _project("copy-agreement", "route", "worker")
    # `reviewed` reaches this record the way an operator does — the real `just
    # review-plan`, the real script, the real `oneharness` CLI and its response schema —
    # and substitutes the paid provider process alone, this suite's one sanctioned double.
    # llmlint: ignore[e2e_not_mocked] see the note above this line
    reviewed(project)
    assert _just("check-plan", project).returncode == 0

    edited = _document(project, "worker")
    edited.write_text(
        edited.read_text(encoding="utf-8").replace(STATES_ITS_BAR.splitlines()[0], EDITED),
        encoding="utf-8",
    )

    checked = _just("check-plan", project)
    assert checked.returncode == 1, checked.stdout + checked.stderr
    assert "no review record" in checked.stderr, checked.stderr

    copy = _just("copy-plan", project, "--to", DESTINATION)
    assert copy.returncode == 1, copy.stdout + copy.stderr
    assert "worker" in copy.stderr, copy.stderr
    assert "route" not in copy.stderr, "the copy named a task the check does not"
    named = _named_by_the_review_gate(checked.stderr)
    assert "worker" in named and "route" not in named, checked.stderr
    assert _records(destination) == [], "an unreviewed task did not stop the copy"


#: What `just check-plan` prefixes its own findings with. Every other line on that
#: descriptor belongs to somebody else: the engine's loader reports what it could not
#: check and why — a repository this host has not registered, for one — and names the
#: nodes it reported it about, which is a different sentence about a different question.
#: Reading the whole stream for a node id conflates the two, and did: the loader grew a
#: policy check whose "could not ask" line names every node in the plan, and an assertion
#: that a task was *not* named as unreviewed started failing on a line that had not said
#: it was.
REVIEW_GATE_PREFIX = "check-plan: scripts/plan-check.sh:"


def _named_by_the_review_gate(reported: str) -> str:
    """The lines `just check-plan`'s own registered check wrote, and nothing else."""
    return "\n".join(line for line in reported.splitlines() if line.startswith(REVIEW_GATE_PREFIX))


def test_a_destination_that_refuses_is_a_different_answer_from_an_unreviewed_plan(
    destination: Path,
) -> None:
    """The two refusals a plan builder branches on carry two exit statuses.

    Read one as the other and "nothing has reviewed this" gets retried as an outage of
    the board — which is the failure the status is here to prevent, so both are taken
    from the same reviewed plan and only the destination differs.
    """
    project = _project("copy-destination")
    # `reviewed` reaches this record the way an operator does — the real `just
    # review-plan`, the real script, the real `oneharness` CLI and its response schema —
    # and substitutes the paid provider process alone, this suite's one sanctioned double.
    # llmlint: ignore[e2e_not_mocked] see the note above this line
    reviewed(project)

    refused = _just("copy-plan", project, "--to", "no-such-source")
    assert refused.returncode == 3, refused.stdout + refused.stderr
    assert "no such source" in refused.stderr.lower() or "no source named" in refused.stderr, (
        refused.stderr
    )
    assert "rather than the plan being unreviewed" in refused.stderr, refused.stderr
    assert _records(destination) == [], "a refused destination wrote to another store"

    accepted = _just("copy-plan", project, "--to", DESTINATION)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr


def test_naming_no_destination_reaches_the_board_this_repository_launches_from(
    destination: Path,
) -> None:
    """The short form an operator actually types resolves to the board, driven for real.

    Only this far, and the limit is the point rather than an omission: the default *is*
    the live GitHub Projects board, the store's configuration layers cannot make that
    source name serve a local directory — the file layer's `github-projects` settings
    survive an environment override of its plugin and the schema then refuses them — and
    a journey that let the copy run would be its own rate-limit burst and would leave a
    project behind on the store every other run of this repository reads.

    So it is driven at the one ending that resolves the destination without asking any
    store to serve it: the refusal a plan nothing has reviewed gets, which names the
    destination the copy would have used. `tests/test_plan_source_roots.py` reconciles
    that name against `onetaskgraph.yaml`, and `tests/test_plan_copy.py` asserts it is
    what the copy is handed.
    """
    project = _project("copy-default")

    refused = _just("copy-plan", project)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert f"copied into {THE_BOARD!r}" in refused.stderr, refused.stderr
    assert _records(destination) == [], "a refused copy wrote to a store"


def test_an_argument_this_recipe_has_no_opinion_about_reaches_the_store_s_copy_verb(
    destination: Path,
) -> None:
    """A pass-through is only proven by an argument that changes what the store does.

    `--dry-run` is proven elsewhere by the absence of a write, which a swallowed argument
    could not fake. This is the other direction: `--match-by` is a *value-bearing* option,
    and what it does is re-establish a correspondence the destination has lost. Drop the
    argument on the way and the store creates a **second** project beside the first rather
    than updating it — so the record count is what says the flag arrived.
    """
    project = _project("copy-passthrough")
    _, _, native = project.partition(":")
    # `reviewed` reaches this record the way an operator does — the real `just
    # review-plan`, the real script, the real `oneharness` CLI and its response schema —
    # and substitutes the paid provider process alone, this suite's one sanctioned double.
    # llmlint: ignore[e2e_not_mocked] see the note above this line
    reviewed(project)
    assert _just("copy-plan", project, "--to", DESTINATION).returncode == 0

    # The correspondence the copy recorded, removed the way losing one looks: the
    # destination's records no longer name where they came from. Written rather than
    # reached for through a command because the store has no verb that loses one — that
    # is what happens when a board is recreated or a record is re-authored, and
    # `--match-by` documents itself as the way to re-establish it. A journey that could
    # not set up this state could not exercise the flag at all.
    # A block rather than a line-scoped directive because the write this is about is the
    # `write_text` several lines down rather than the `for` that opens the loop.
    # llmlint: ignore-block[tests_mirror_real_usage] see the note above this line
    for record in (
        destination / "projects" / f"{native}.md",
        *(destination / "tasks").rglob("*.md"),
    ):
        record.write_text(
            "\n".join(
                line
                for line in record.read_text(encoding="utf-8").split("\n")
                if "onetaskgraph.origin" not in line
            ),
            encoding="utf-8",
        )
    # llmlint: ignore-end[tests_mirror_real_usage]

    again = _just("copy-plan", project, "--to", DESTINATION, "--match-by", "title")
    assert again.returncode == 0, again.stdout + again.stderr
    assert "updated" in again.stdout, again.stdout
    assert _records(destination) == [
        f"documents/{native}-design.md",
        f"projects/{native}.md",
        f"tasks/{native}/route.md",
    ], "the match key never reached the store, so it copied a second project beside the first"


def test_a_plan_that_landed_without_its_document_says_so_rather_than_reporting_a_copy(
    destination: Path,
) -> None:
    """The state an operator has to know about: the plan is there and the document is not.

    A plan on the destination with nothing for a person to approve it as is a plan that
    cannot be launched, and it looks exactly like a complete copy from the destination
    alone. Driven by making the directory the store writes documents into read-only,
    which stops that half and leaves the project copy before it untouched — the ordering
    is what makes the state reachable at all.
    """
    project = _project("copy-partial")
    _, _, native = project.partition(":")
    # `reviewed` reaches this record the way an operator does — the real `just
    # review-plan`, the real script, the real `oneharness` CLI and its response schema —
    # and substitutes the paid provider process alone, this suite's one sanctioned double.
    # llmlint: ignore[e2e_not_mocked] see the note above this line
    reviewed(project)

    documents = destination / "documents"
    documents.mkdir()
    documents.chmod(0o500)
    try:
        copy = _just("copy-plan", project, "--to", DESTINATION)
    finally:
        documents.chmod(0o700)

    assert copy.returncode == 3, copy.stdout + copy.stderr
    assert f"the plan landed in {DESTINATION!r}" in copy.stderr, copy.stderr
    assert "nothing on" in copy.stderr and "to approve this plan as" in copy.stderr, copy.stderr
    assert _records(destination) == [
        f"projects/{native}.md",
        f"tasks/{native}/route.md",
    ], "the project copy did not land, so this is not the partial state it is about"


def test_the_whole_flow_still_lands_from_inside_a_run_that_named_the_store_binary(
    destination: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ONETASKGRAPH_BIN` is the engine's pointer and that CLI's own unknown setting.

    Every `ONETASKGRAPH_` name is read by the store's configuration layer, so a command
    that inherited this one refused `bin` as an unknown field and answered nothing at all
    — a listing, a copy and a `config show` alike. A dispatch of this repository runs
    inside a run that may have set it, so every command here would have reported a
    perfectly readable plan store as unreadable. Driven over the whole flow rather than at
    one call, because each of these spawns the store for itself: the review, the approval
    the fixture records, the pre-flight read, the project copy and the document copy.
    """
    # llmlint: ignore[e2e_not_mocked] The real installed CLI, named the way the engine
    # names it; nothing is substituted, and the point is that this name is present.
    monkeypatch.setenv("ONETASKGRAPH_BIN", str(ONETASKGRAPH_BIN))
    project = _project("copy-under-engine-pointer")
    _, _, native = project.partition(":")
    # `reviewed` reaches this record the way an operator does — the real `just
    # review-plan`, the real script, the real `oneharness` CLI and its response schema —
    # and substitutes the paid provider process alone, this suite's one sanctioned double.
    # llmlint: ignore[e2e_not_mocked] see the note above this line
    reviewed(project)

    copy = _just("copy-plan", project, "--to", DESTINATION)
    assert copy.returncode == 0, copy.stdout + copy.stderr
    assert _records(destination) == [
        f"documents/{native}-design.md",
        f"projects/{native}.md",
        f"tasks/{native}/route.md",
    ], copy.stdout + copy.stderr


#: Named rather than left a bare `str` so it cannot be interchanged with
#: `plan_store.NodeId`, the other end of what a copy rewrites.
ReportedLocation = NewType("ReportedLocation", str)


def _reported_locations(project: str) -> dict[plan_store.NodeId, ReportedLocation]:
    """Each task of ``project`` against the location its own store reports for it.

    Read through the operator's own surface for `_stored_tasks`'s reason, and never
    composed from a root and a name: where a record lives is the store's answer, and a
    path assembled here would name nothing the moment a destination is a board.
    """
    # llmlint: ignore-block[modern_domain_modeling] Transient parsed JSON, narrowed here.
    located: dict[plan_store.NodeId, ReportedLocation] = {}
    for one in _stored_tasks(project):
        metadata, location = one["metadata"], one["location"]
        assert isinstance(metadata, dict) and isinstance(location, dict), one
        node = plan_store.NodeId(str(metadata["onepipeline.id"]))
        located[node] = ReportedLocation(str(location["path"]))
    # llmlint: ignore-end[modern_domain_modeling]
    return located


def _write_design_document_pointing_at(
    project: str, native: str, located: Mapping[plan_store.NodeId, ReportedLocation]
) -> None:
    """Overwrite ``project``'s design document with the planned-tasks table this is about.

    One row per task, whose last column is that task's location **as its own store
    reports it** — which is what `config/design-doc-template.md` asks a design document
    for, and what a reference is: a literal occurrence of the exact location string a
    source reported. Written the way `project_fixtures._designed` writes the document it
    replaces, because the store exposes no verb that edits a document's content in place
    and staging one through a copy is what the command under test is.
    """
    document = design_approval.design_document(project)
    location = document.location
    assert isinstance(location, dict), document
    rows = "\n".join(f"| {node} | `{where}` |" for node, where in sorted(located.items()))
    Path(str(location["path"])).write_text(
        frontmatter(
            {"title": document.title, "project": native},
            "## Planned tasks\n\n| Task | Where it lives |\n| --- | --- |\n" + rows + "\n",
        ),
        encoding="utf-8",
    )


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] `plan-tooling` is a
# leaf project keyed on `planToolingWorkspace`, which is the edge this rule asks for. That
# key names the trees these journeys drive, so narrowing it would memoize a verdict over a
# tree that was never run.
def test_a_copied_design_document_points_its_task_references_at_the_destination(
    destination: Path,
) -> None:
    """A copied document's planned-tasks table locates each task on the destination.

    Those cells used to hold the *drafting* store's absolute paths, dead for the only
    reader the document exists for. Three readings share one copy because each would
    otherwise pay for its own plan and review turn.
    """
    project = _project("copy-references", "route", "worker")
    _, _, native = project.partition(":")
    copied_id = f"{DESTINATION}:{native}"
    drafted = _reported_locations(project)
    _write_design_document_pointing_at(project, native, drafted)
    # The document's authored content moved, so the approval keyed on it is recorded
    # again — the fixture's own seam, and the state a plan reaches its board in.
    approved(project)
    # `reviewed` reaches this record the way an operator does — the real `just
    # review-plan`, the real script, the real `oneharness` CLI and its response schema —
    # and substitutes the paid provider process alone, this suite's one sanctioned double.
    # llmlint: ignore[e2e_not_mocked] see the note above this line
    reviewed(project)

    # A destination holding no counterpart for either task: a trial writes nothing, so
    # there is nothing to point at, the cells are left exactly as they are, and the copy
    # says how many references it could not resolve rather than saying nothing.
    blind = _just("copy-plan", project, "--to", DESTINATION, "--dry-run")
    assert blind.returncode == 0, blind.stdout + blind.stderr
    assert "references: 0 rewritten, 2 unresolved (0 ambiguous)" in blind.stdout, blind.stdout
    assert _records(destination) == [], "a trial run wrote to the destination"

    copy = _just("copy-plan", project, "--to", DESTINATION)
    assert copy.returncode == 0, copy.stdout + copy.stderr
    assert "references: 2 rewritten, 0 unresolved (0 ambiguous)" in copy.stdout, copy.stdout

    # Every cell now holds the location the **destination** reports for that same task,
    # and none of them the drafting store's. Both directions, because a document whose
    # table had simply been dropped would satisfy the first on its own.
    # llmlint: ignore-block[modern_domain_modeling] Transient parsed JSON, narrowed here.
    landed = _reported_locations(copied_id)
    assert set(landed) == set(drafted), landed
    (document,) = _stored_documents(copied_id)
    content = document["content"]
    assert isinstance(content, str), document
    for node, where in landed.items():
        assert f"`{where}`" in content, f"{node} still points somewhere else: {content}"
    for node, where in drafted.items():
        assert where not in content, f"{node} still points at the drafting store: {content}"

    # And a trial run over the destination that copy landed on answers the copy's own
    # figures while writing nothing — proven against a mark only a write would remove.
    location = document["location"]
    assert isinstance(location, dict), document
    marked = Path(str(location["path"]))
    marked.write_text(marked.read_text(encoding="utf-8") + "\nnot rewritten\n", encoding="utf-8")
    # llmlint: ignore-end[modern_domain_modeling]
    trial = _just("copy-plan", project, "--to", DESTINATION, "--dry-run")
    assert trial.returncode == 0, trial.stdout + trial.stderr
    assert "references: 2 rewritten, 0 unresolved (0 ambiguous)" in trial.stdout, trial.stdout
    assert marked.read_text(encoding="utf-8").endswith("not rewritten\n"), (
        "a trial run wrote over the document it was only reporting on"
    )


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
