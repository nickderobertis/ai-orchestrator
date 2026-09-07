"""A plan is not launched until the user has approved the document it is read as.

The gate is worth nothing unless a launch really refuses, so every claim here is taken
from a real `just orchestrate`: the real recipe, the real `scripts/onepipeline.sh`, the
real `orchestrator-launch-gate`, the real installed `onetaskgraph`, and — where the launch
is *let through* — the real `onepipeline` driver carrying a real run to settlement.

**Nothing here spends a provider turn, and that is a property of the plan rather than of a
stand-in.** Each plan is one `expects_no_diff` node, which the engine settles
deterministically with no dispatch at all, and each launch names `--dag-graph off`, which
is what keeps a monitor from being attached to watch it. So the launches are real end to
end and cost nothing, which is what makes it affordable to launch the same plan four times
across one refusal, one approval, one edit and one re-approval.

**The store is this journey's own**, configured through the environment layer the whole
command sees rather than through a flag only half of it does. A document is removed and
edited here, and both of those against the shared fixture root would be a record vanishing
from under another tier walking it — which refuses that tier's whole walk rather than that
record.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from project_fixtures import helper, reviewed
from published_tools import ONETASKGRAPH_BIN
from waits import timeout as e2e_timeout

from orchestrator.design_approval import (
    PLAN_KIND,
    PLANNING,
    RECORD_KEY,
    STAMP_KIND,
    STAMP_NODES,
)
from orchestrator.plan_store import StoreDocument
from orchestrator.project_store import frontmatter, write_plan_project
from orchestrator.root import REPO_ROOT

#: This suite is its own Nx project, `plan-tooling`; see
#: `tests/plan_tooling/project.json` and the guard in `tests/conftest.py`.

#: The source this journey drafts, approves and launches in — its own, for the reason the
#: module docstring gives. A plain lowercase name because it is spelled into the store's
#: own `ONETASKGRAPH_SOURCES__…` environment layer as well as onto a command line.
SOURCE = "drafting"

#: The source a cleared plan is copied into here, standing in for the `plans` board this
#: repository launches from. A plain lowercase name for the reason :data:`SOURCE` is one.
BOARD = "board"

#: The source each design document is drafted in before it is stored. A design-doc
#: dispatch writes the prose in a directory of its own and then puts it into the plan's
#: store with the store's own `document copy` — the write
#: `tests/plan_tooling/test_plan_flow_e2e.py` scripts that dispatch to make, and
#: the only way a document reaches a plan store at all. Named on the copy's own command
#: line rather than in the environment, so no launch below sees a source it would never
#: see in production.
DRAFT = "drafted"

#: The guard covering every paid identity `ONEHARNESS_BIN_*` cannot reach. Nothing here
#: should dispatch at all, which is exactly why it is worth proving rather than assuming:
#: a launch that started spending turns would otherwise pass while doing it.
PAID_PROVIDER_GUARD = helper("no-paid-provider")

#: A launching session this journey states rather than inherits, and everything else an
#: enclosing dispatch would otherwise decide for it.
LAUNCHING_SESSION = "e2e-approve-design"
INHERITED_ENVIRONMENT = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "ONEPIPELINE_RUN_ID",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)

#: The observer a launch attaches when the caller names none, turned off here. A monitor
#: watching one deterministic node is two agent members' worth of turns for nothing, and
#: `off` is the spelling `onepipeline start` itself ships as its default.
NO_OBSERVER = ("--dag-graph", "off")

#: The one node a planning launch of this journey's store writes, and the whole of what
#: its stamp names. `scripts/plan.sh` writes two; what matters to the gate is that the
#: stamp names what the launch wrote, so one node states that with one fewer launch.
PLANNING_NODE = "handoff"

#: The document each plan here is read as, before anybody has approved it.
DESIGN = (
    "## What\n\nOne node that changes nothing.\n\n"
    "## Why\n\nSomething has to be launchable for this to be a gate at all.\n\n"
    "## Architecture\n\nOne node, settled without a dispatch.\n\n"
    "## Contracts\n\nNone: nothing outside this plan reads it.\n\n"
    "## Acceptance criteria\n\nThe run settles and nothing changed.\n\n"
    "## Planned tasks\n\n"
    "| Task | What it delivers | Depends on | Where it lives |\n"
    "| --- | --- | --- | --- |\n"
    "| handoff | the recorded no-change handoff | none | the store's own location |\n"
)


class Store:
    """One local Markdown source this journey owns outright, and the plans in it."""

    def __init__(self, source: str, root: Path) -> None:
        self.source = source
        self.root = root
        #: Where a document is drafted before the store's own copy verb puts it in. A
        #: directory of this store's own rather than a shared one, because the copy leaves
        #: its origin on the destination and two stores staging under one root would share
        #: one correspondence between them.
        self.drafts = root.parent / f"{root.name}-drafts"

    def plan(
        self,
        native: str,
        *,
        planning: bool = False,
        beyond: Sequence[str] = (),
        stamp: object = None,
    ) -> str:
        """Write one launchable plan and answer its qualified id.

        Every node states `expects_no_diff`, which the engine settles with no dispatch, so
        every launch below is a whole real run that reaches no provider.

        ``planning`` stamps the project the way `scripts/plan.sh` stamps the one a planning
        launch writes: the kind, and the node ids that launch dispatches. ``beyond`` adds
        nodes the stamp does **not** name — which is what a plan a planner wrote into the
        planning project is, and the state that used to inherit the exemption for good.
        The names are this repository's own constants rather than spellings, so a field
        renamed in one place cannot be right in the other. ``stamp`` writes that entry's
        value verbatim instead, which is how a project stamped by an older `just plan` —
        one that named no nodes — is stood up as it really is, and how a stamp of any other
        shape the gate has to answer for is written as a project would really carry it.
        """
        launched = [PLANNING_NODE]
        write_plan_project(
            self.root,
            {
                "schema_version": 3,
                "goal": {"text": "Settle nodes that change nothing"},
                "name": native,
                "tasks": [
                    {
                        "id": node_id,
                        "title": node_id,
                        "expects_no_diff": True,
                        "task": (
                            "## What\n\nRecord that nothing changes.\n\n"
                            "## Why\n\nThe boundary is explicit.\n\n"
                            "## Acceptance criteria\n\n- Nothing changed.\n"
                        ),
                    }
                    for node_id in (*launched, *beyond)
                ],
            },
            native_id=native,
            project_metadata=(
                {
                    PLAN_KIND: stamp
                    if stamp is not None
                    else {STAMP_KIND: PLANNING, STAMP_NODES: launched}
                }
                if planning or stamp is not None
                else None
            ),
        )
        return f"{SOURCE}:{native}"

    def document(self, native: str, content: str = DESIGN, *, named: str = "design") -> Path:
        """Store one document of ``native``'s plan the way the design-doc dispatch does.

        The prose is drafted in :data:`DRAFT`, a source of this store's own, and then put
        into the plan's store with the store's own `document copy`. That is the whole of
        what the design-doc dispatch does to store what it wrote — the prose itself is the
        model's, and it is the one part of that dispatch nothing here spends — so a
        document reaches a plan here through the same verb rather than by a write into the
        store's directory behind its back.

        ``named`` is what distinguishes a second document *of the same project* from a
        document of another one — which is the state the ambiguity refusal is about, and
        which a second plan's document would not reach.

        Answers where the store says the stored record is, read back from the store rather
        than composed, which is the same rule the refusals themselves follow.
        """
        drafted = f"{native}-{named}"
        documents = self.drafts / "documents"
        documents.mkdir(parents=True, exist_ok=True)
        (documents / f"{drafted}.md").write_text(
            frontmatter({"title": f"Design: {native} ({named})", "project": native}, content),
            encoding="utf-8",
        )
        copied = self._stored("document", "copy", f"{DRAFT}:{drafted}", "--to", self.source)
        (one,) = copied["items"]
        shown = self._stored("document", "show", str(one["destination"]))
        (record,) = shown["items"]
        return Path(str(record["item"]["location"]["path"]))

    def _stored(self, *arguments: str) -> dict[str, Any]:
        """One command of the pinned plan-store CLI, with this store's drafts configured.

        The draft source is named here rather than in the environment: it exists for the
        length of this one command, exactly as the directory a dispatch drafts in exists
        for the length of that dispatch, and no launch in this module ever sees it.

        The answer stays ``Any`` at this seam deliberately. It is another program's open
        JSON contract, and a model of it written here would be a second copy of a payload
        this journey reads two fields of — one that would go on agreeing with itself while
        the store's own shape moved. Each caller indexes exactly the field it needs
        instead, which is where a moved shape is caught: the same narrowing
        `orchestrator/plan_store.py` states for the reader beside this.
        """
        answered = subprocess.run(
            [
                str(ONETASKGRAPH_BIN),
                "--set",
                f"sources.{DRAFT}.plugin=local-md",
                "--set",
                f"sources.{DRAFT}.config.root={self.drafts}",
                *arguments,
                "--json",
            ],
            text=True,
            capture_output=True,
            timeout=e2e_timeout(60),
            check=False,
        )
        assert answered.returncode == 0, answered.stdout + answered.stderr
        # Narrowed to a mapping and no further, for the reason the docstring gives: what
        # each field of it is, is the caller's to say at the site that reads it.
        payload: dict[str, Any] = json.loads(answered.stdout)
        return payload

    def edit(self, record: Path, content: str) -> None:
        """Change what a stored document says, where the store says it is.

        What a person does with the path every refusal hands them — *"It is at …: read
        it"* — and the only editing interface a local Markdown store has. The record the
        store keeps around the prose is left alone, because a reader edits the document
        rather than replacing it: re-storing it through the copy verb would take the
        approval record with it, and the refusal that followed would then be about a
        record that had gone rather than about content nobody has read.
        """
        lines = record.read_text(encoding="utf-8").splitlines()
        closing = next(
            index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"
        )
        record.write_text(
            "\n".join([*lines[: closing + 1], "", content.rstrip(), ""]), encoding="utf-8"
        )

    def standing_project(self, native: str, *, titled: str) -> Path:
        """One project this store already holds, under an identifier of its own.

        What a copy onto the board this repository launches from produces is the plan
        under an id the *destination* minted, and a local store copied onto keeps the
        drafted plan's own native id — so the destination's record is stood up here first
        and the copy matched onto it by title, which is the one way a pair of local stores
        reaches the state the board reaches by itself.
        """
        projects = self.root / "projects"
        projects.mkdir(parents=True, exist_ok=True)
        record = projects / f"{native}.md"
        record.write_text(
            frontmatter(
                {"title": titled, "status": "todo", "metadata": {}},
                f"This store's own record of {titled}.",
            ),
            encoding="utf-8",
        )
        return record


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Store]:
    """A local Markdown source configured for this process and every child it spawns.

    `ONETASKGRAPH_DEFAULT_SOURCES` is narrowed to this one source, which keeps the live
    `plans` board out of every read a launch makes here.
    """
    root = tmp_path / "store"
    # Created rather than left to the first write: a `local-md` source canonicalizes its
    # root when it is built, so an absent one is refused as a broken source.
    root.mkdir(parents=True)
    monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{SOURCE.upper()}__PLUGIN", "local-md")
    monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{SOURCE.upper()}__CONFIG__ROOT", str(root))
    monkeypatch.setenv("ONETASKGRAPH_DEFAULT_SOURCES", SOURCE)
    yield Store(SOURCE, root)


@pytest.fixture
def board(store: Store, monkeypatch: pytest.MonkeyPatch) -> Iterator[Store]:
    """A second local Markdown source, standing in for the board a cleared plan is copied to.

    Configured after ``store`` and beside it, which is what widens
    `ONETASKGRAPH_DEFAULT_SOURCES` to the two sources in play: the copy reads the drafting
    one and the launch reads this one, and the live `plans` board stays out of both.
    """
    root = store.root.parent / "board"
    # A `local-md` source canonicalizes its root when it is built, so an absent one is a
    # broken source rather than an empty store.
    root.mkdir(parents=True)
    monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{BOARD.upper()}__PLUGIN", "local-md")
    monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{BOARD.upper()}__CONFIG__ROOT", str(root))
    monkeypatch.setenv("ONETASKGRAPH_DEFAULT_SOURCES", f"{SOURCE},{BOARD}")
    yield Store(BOARD, root)


@pytest.fixture
def runs(tmp_path: Path) -> Path:
    """A run ledger of this journey's own, so nothing here is read as a live run."""
    return tmp_path / "runs"


def _environment(runs: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(extra or {})
    for name in INHERITED_ENVIRONMENT:
        environment.pop(name, None)
    environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    environment["ONEPIPELINE_RUNS_DIR"] = str(runs)
    # llmlint: ignore[e2e_not_mocked] Only the paid providers are guarded against; every
    # recipe, script and CLI below is the real one, and nothing here should dispatch.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    return environment


def _just(
    *arguments: str,
    runs: Path,
    seconds: float = 240,
    extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """One real recipe from this checkout, under this journey's own ledger and store."""
    return subprocess.run(
        ["just", *arguments],
        cwd=REPO_ROOT,
        env=_environment(runs, extra),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
    )


def _launch(project: str, runs: Path) -> subprocess.CompletedProcess[str]:
    return _just("orchestrate", project, *NO_OBSERVER, runs=runs)


def _settled(launched: subprocess.CompletedProcess[str]) -> str:
    """The settlement an attached launch reports, or a failure naming what it said."""
    reported = [line for line in launched.stdout.splitlines() if line.startswith('{"run_id"')]
    assert reported, f"the launch reported no settlement:\n{launched.stdout}\n{launched.stderr}"
    settlement = json.loads(reported[-1])["settlement"]
    assert isinstance(settlement, str)
    return settlement


@pytest.fixture(autouse=True)
def _requires_just() -> None:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")


@pytest.mark.xdist_group("approve-design")
def test_a_plan_is_launched_only_while_its_design_document_is_the_one_approved(
    store: Store, runs: Path
) -> None:
    """The whole gate on one plan, in the order an operator meets it.

    One journey rather than five, because these are five readings of a single act: the
    launch is refused for having no document, refused again for having an unapproved one,
    let through once the approval is recorded, refused again the moment the document is
    edited, and let through again once somebody has read the edit. Split apart, each would
    pay for its own plan and its own launch to reach the state the one before it left.
    """
    native = "approve-design-flow"
    project = store.plan(native)

    missing = _launch(project, runs)
    assert missing.returncode == 1, missing.stdout + missing.stderr
    assert "holds no design document" in missing.stderr, missing.stderr
    assert f"just approve-design {project}" in missing.stderr, missing.stderr
    assert "nothing was dispatched" in missing.stderr, missing.stderr
    assert not runs.exists(), "a refused launch wrote a run onto the ledger"

    # And there is nothing to approve either, said the same way rather than differently.
    nothing = _just("approve-design", project, runs=runs)
    assert nothing.returncode == 1, nothing.stdout + nothing.stderr
    assert "holds no design document" in nothing.stderr, nothing.stderr

    record = store.document(native)

    unapproved = _launch(project, runs)
    assert unapproved.returncode == 1, unapproved.stdout + unapproved.stderr
    assert "carries no approval for what it currently says" in unapproved.stderr, unapproved.stderr
    assert str(record) in unapproved.stderr, (
        f"the refusal does not say where the document is, so the person who has to read "
        f"it is not told where:\n{unapproved.stderr}"
    )
    assert not runs.exists(), "a launch refused for an unapproved document reached the ledger"

    approved = _just("approve-design", project, runs=runs)
    assert approved.returncode == 0, approved.stdout + approved.stderr
    assert "recorded the approval" in approved.stdout, approved.stdout

    # Repeating it is a no-op rather than a second record, so a retried command and a
    # second person running it are both harmless. Read off the record itself, because a
    # command that rewrote it and reported otherwise would pass on its own output.
    written = record.read_bytes()
    again = _just("approve-design", project, runs=runs)
    assert again.returncode == 0, again.stdout + again.stderr
    assert "already carries an approval" in again.stdout, again.stdout
    assert record.read_bytes() == written, "approving unchanged content rewrote the record"

    launched = _launch(project, runs)
    assert launched.returncode == 0, launched.stdout + launched.stderr
    assert _settled(launched) == "complete", launched.stdout
    assert (runs / native).is_dir(), f"the run this launch settled is not on the ledger:\n{runs}"

    # Editing the document afterwards is content nobody has approved, which is the whole
    # reason the record is keyed on the document rather than on the plan.
    store.edit(record, DESIGN.replace("One node that changes nothing", "Something else"))
    edited = _launch(project, runs)
    assert edited.returncode == 1, edited.stdout + edited.stderr
    assert "carries no approval for what it currently says" in edited.stderr, edited.stderr

    reapproved = _just("approve-design", project, runs=runs)
    assert reapproved.returncode == 0, reapproved.stdout + reapproved.stderr
    assert "recorded the approval" in reapproved.stdout, reapproved.stdout
    relaunched = _launch(project, runs)
    assert relaunched.returncode == 0, relaunched.stdout + relaunched.stderr
    assert _settled(relaunched) == "complete", relaunched.stdout


@pytest.mark.xdist_group("approve-design")
def test_the_recipe_refuses_an_argument_that_names_a_project_in_no_store(
    store: Store, runs: Path
) -> None:
    """What a plain project name gets from the real recipe, before any store is read.

    Driven through the recipe rather than the function because that is where the argument
    is untrusted: `just approve-design` is a command line an operator types, and until it
    is held to a shape a bare name reaches `onetaskgraph` as a project id. This journey
    writes no plan at all, so the store has nothing this argument could have named — and
    the refusal still says what a project id is rather than what the store did not find,
    which is the whole of what makes it early.
    """
    refused = _just("approve-design", "approve-design-flow", runs=runs)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "is not a qualified project id" in refused.stderr, refused.stderr
    assert "`<source>:<project>`" in refused.stderr, refused.stderr
    assert "holds no design document" not in refused.stderr, (
        f"the argument reached the store, so the shape is checked after the read rather "
        f"than before it:\n{refused.stderr}"
    )
    assert not list(store.root.iterdir()), f"a refused approval wrote into the store:\n{store.root}"


@pytest.mark.xdist_group("approve-design")
def test_a_planning_project_launches_with_no_design_document_at_all(
    store: Store, runs: Path
) -> None:
    """The one exemption, and the whole of it.

    A planning run's output *is* the plan, so the document it will be reviewed as does not
    exist when it is launched. Proven against a project identical to the one refused above
    but for the marker `scripts/plan.sh` stamps, so what is under test is the marker and
    not the shape: this plan has no document either.

    **The marker is written here rather than by `just plan`, and the other half of that is
    somebody else's journey.** What this isolates is the gate's own reading — that a
    project stating it, and nothing else about it, launches without a document — against a
    project held identical to the one refused above. That a *planning launch* reaches that
    state is `tests/e2e/test_plan_recipe_e2e.py`'s, which reads the marker off the project
    a real `just plan` wrote and whose launch reaching a dispatch at all is the gate
    letting it through. Driving `just plan` here would re-prove that at the cost of a whole
    planning run, and it would stop this being a comparison between two projects that
    differ by one field.

    **The exemption is said rather than passed over**, which is the second claim here: a
    launch that dispatched because nobody had to approve anything reads differently from
    one that dispatched because somebody did. And it lasts exactly as long as there is
    nothing to approve — the document written at the end of this is the design-doc node's
    own output, and the same project is gated the moment it exists.
    """
    native = "approve-design-planning"
    # llmlint: ignore[tests_mirror_real_usage] see the paragraph above this line
    project = store.plan(native, planning=True)
    launched = _launch(project, runs)
    assert launched.returncode == 0, launched.stdout + launched.stderr
    assert _settled(launched) == "complete", launched.stdout
    assert "is the plan a planning launch is writing" in launched.stderr, (
        f"the launch dispatched on the exemption and said nothing, so an exempt plan and "
        f"an approved one are one answer from here:\n{launched.stderr}"
    )

    store.document(native)
    gated = _launch(project, runs)
    assert gated.returncode == 1, gated.stdout + gated.stderr
    assert "carries no approval for what it currently says" in gated.stderr, gated.stderr
    assert "so there is something to read" in gated.stderr, (
        f"the refusal does not say why a planning project is being asked for an approval, "
        f"which reads as the gate mis-firing:\n{gated.stderr}"
    )


@pytest.mark.xdist_group("approve-design")
def test_a_planning_project_holding_the_plan_a_planner_wrote_is_gated_like_any_other(
    store: Store, runs: Path
) -> None:
    """The exemption covers a planning launch, not a project for the rest of its life.

    This is the state the manager hit: the plan a planner writes is stored in the project
    the planning launch created, so the plan's own nodes sit in a project carrying the
    planning stamp. While the exemption was scoped to that stamp, launching them dispatched
    real work across real repositories with the design document approved by nobody — and
    the gate returned the same silence an approved plan gets.

    Both halves of the bound are driven, in the order the project reaches them. First the
    tasks alone: the same project the journey above launches, plus two nodes that launch
    never wrote, is refused with no document in it at all. Then the ordinary refusal for
    want of an approval once the document exists, and the approval that answers it — and
    last, a second document, where the lapse is still named beside the refusal that this
    project holds no one design document.

    The state is stood up here rather than reached through a real `just plan` and a real
    planner, for the reason the journey above gives: what is under test is the gate's own
    reading of one project, and paying for a planning run and a planner that writes into
    its own project would prove the planner rather than the bound.
    """
    native = "approve-design-planning-grown"
    # llmlint: ignore[tests_mirror_real_usage] see the paragraph above this line
    project = store.plan(native, planning=True, beyond=("adopt-the-release", "close-the-gap"))

    strayed = _launch(project, runs)
    assert strayed.returncode == 1, strayed.stdout + strayed.stderr
    assert "2 task(s) that launch never wrote" in strayed.stderr, (
        f"a planning project holding work that launch never wrote kept its exemption:\n"
        f"{strayed.stderr}"
    )
    assert "adopt-the-release, close-the-gap" in strayed.stderr, strayed.stderr
    assert not runs.exists(), "a launch refused for a lapsed exemption reached the ledger"

    store.document(native)
    unapproved = _launch(project, runs)
    assert unapproved.returncode == 1, unapproved.stdout + unapproved.stderr
    assert "carries no approval for what it currently says" in unapproved.stderr, unapproved.stderr
    assert f"just approve-design {project}" in unapproved.stderr, unapproved.stderr
    assert not runs.exists(), "a launch refused for want of an approval reached the ledger"

    approved = _just("approve-design", project, runs=runs)
    assert approved.returncode == 0, approved.stdout + approved.stderr
    launched = _launch(project, runs)
    assert launched.returncode == 0, launched.stdout + launched.stderr
    assert _settled(launched) == "complete", launched.stdout
    assert "is the plan a planning launch is writing" not in launched.stderr, (
        f"the launch went through on the exemption rather than on the approval just "
        f"recorded:\n{launched.stderr}"
    )

    # A second document is a third answer — which of them is the design document cannot be
    # decided — and the lapse is named beside it, so a planning project meeting that answer
    # still says why it was asked at all.
    store.document(native, named="notes")
    ambiguous = _launch(project, runs)
    assert ambiguous.returncode == 1, ambiguous.stdout + ambiguous.stderr
    assert "holds 2 documents" in ambiguous.stderr, ambiguous.stderr
    assert "task(s) that launch never wrote" in ambiguous.stderr, ambiguous.stderr


@pytest.mark.xdist_group("approve-design")
def test_a_planning_stamp_claiming_a_node_the_project_does_not_hold_is_not_exempt(
    store: Store, runs: Path
) -> None:
    """The other direction of the bound, and the one a covering check lets straight through.

    The journey above is a project that has grown past the launch that wrote it. This is
    the same disagreement from the other end: a stamp claiming a node this project does
    not hold describes a launch nothing here can see, so there is nothing to bound the
    exemption to — and because the tasks are checked against what the project holds *now*,
    a project that is a strict subset of its own stamp would go on being exempt as it grew
    into those claims, one dispatched node at a time. Which is the hole this whole gate is
    about, reached without the stamp ever falling behind.

    Two projects, because the second is not a stronger form of the first: this one names
    the direction that ended the exemption and asserts the other is *not* named, and a
    project disagreeing both ways is what a planning project reaches by growing a node its
    stamp does not claim while its stamp claims one that has not arrived. Both directions
    are then said, and said apart — one refusal to the launch, two things to read.

    Stood up here rather than reached through a `just plan`, for the reason the journeys
    above give: what is under test is the gate's own reading of one project's own claim,
    and no release of `just plan` writes a stamp claiming a node it did not write.
    """
    native = "approve-design-planning-overclaimed"
    # llmlint: ignore[tests_mirror_real_usage] see the paragraph above this line
    project = store.plan(
        native, stamp={STAMP_KIND: PLANNING, STAMP_NODES: [PLANNING_NODE, "adopt-the-release"]}
    )

    refused = _launch(project, runs)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "is the plan a planning launch is writing" not in refused.stderr, (
        f"a stamp claiming a node the project does not hold still bought the exemption, so "
        f"the bound is a covering rather than an agreement:\n{refused.stderr}"
    )
    assert "claims 1 node(s) the project does not hold" in refused.stderr, refused.stderr
    assert "adopt-the-release" in refused.stderr, refused.stderr
    assert "task(s) that launch never wrote" not in refused.stderr, (
        f"the lapse reports the direction that did not end the exemption:\n{refused.stderr}"
    )
    assert "holds no design document" in refused.stderr, refused.stderr
    assert not runs.exists(), "a launch refused for an over-claiming stamp reached the ledger"

    # A project disagreeing with its stamp in both directions at once, which is what a
    # planning project reaches by growing a node the stamp does not claim while its stamp
    # claims one that has not arrived. Both are said, and said apart: they are one refusal
    # to the launch and two different things to whoever has to read it.
    both = "approve-design-planning-both-ways"
    # llmlint: ignore[tests_mirror_real_usage] see the paragraph above this journey
    disagreeing = store.plan(
        both,
        beyond=("close-the-gap",),
        stamp={STAMP_KIND: PLANNING, STAMP_NODES: [PLANNING_NODE, "adopt-the-release"]},
    )
    ways = _launch(disagreeing, runs)
    assert ways.returncode == 1, ways.stdout + ways.stderr
    assert "is the plan a planning launch is writing" not in ways.stderr, ways.stderr
    assert "1 task(s) that launch never wrote (close-the-gap)" in ways.stderr, ways.stderr
    assert "claims 1 node(s) the project does not hold (adopt-the-release)" in ways.stderr, (
        f"only one direction of the disagreement reached the operator:\n{ways.stderr}"
    )
    assert not runs.exists(), "a launch refused for a two-way disagreement reached the ledger"

    # And it is the ordinary refusal rather than a project no approval can rescue.
    store.document(native)
    approved = _just("approve-design", project, runs=runs)
    assert approved.returncode == 0, approved.stdout + approved.stderr
    launched = _launch(project, runs)
    assert launched.returncode == 0, launched.stdout + launched.stderr
    assert _settled(launched) == "complete", launched.stdout


@pytest.mark.xdist_group("approve-design")
def test_a_planning_project_whose_tasks_cannot_be_read_is_refused_rather_than_exempted(
    store: Store, runs: Path
) -> None:
    """A bound this cannot check is one that cannot hold, so the store's own words refuse.

    The exemption is the one answer that lets a plan dispatch with nobody having approved
    anything, and half of what bounds it is what the project's own task listing says. A
    listing that cannot be read leaves that half unanswered — so the launch is refused
    naming what the store said, rather than exempted on the readable half alone.

    Driven by making the project's task directory unreadable, which stops the listing
    without stopping the reads around it: the project record still carries its stamp and
    the document listing still answers, so what fails is exactly the read this is about.
    The positive control is the same project a moment later, with the directory readable
    again — which does dispatch on the exemption and says so, so the refusal above is the
    unreadable listing rather than anything else about this plan.
    """
    native = "approve-design-planning-unreadable-tasks"
    # llmlint: ignore[tests_mirror_real_usage] see the paragraph above this line
    project = store.plan(native, planning=True)
    tasks = store.root / "tasks" / native
    assert tasks.is_dir(), f"the plan wrote no task directory to make unreadable:\n{store.root}"

    tasks.chmod(0o000)
    try:
        refused = _launch(project, runs)
    finally:
        tasks.chmod(0o700)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "is the plan a planning launch is writing" not in refused.stderr, (
        f"a planning project whose tasks could not be read was exempted on the half of the "
        f"bound that could:\n{refused.stderr}"
    )
    assert "Permission denied" in refused.stderr, (
        f"the refusal does not carry what the store said, so the person who has to fix it "
        f"is not told what failed:\n{refused.stderr}"
    )
    assert str(tasks) in refused.stderr, refused.stderr
    assert not runs.exists(), "a launch refused for an unreadable task listing reached the ledger"

    launched = _launch(project, runs)
    assert launched.returncode == 0, launched.stdout + launched.stderr
    assert _settled(launched) == "complete", launched.stdout
    assert "is the plan a planning launch is writing" in launched.stderr, (
        f"the same project is not exempt with its tasks readable, so the refusal above was "
        f"about something other than the listing:\n{launched.stderr}"
    )


@pytest.mark.xdist_group("approve-design")
def test_a_project_stamped_by_an_older_planning_launch_is_gated_like_any_other(
    store: Store, runs: Path
) -> None:
    """A stamp naming no nodes bounds the exemption to nothing, so it exempts nothing.

    The shape the stamp had while the exemption was scoped to the project: the bare kind,
    with nothing saying which nodes that launch dispatches. Every project stamped by a
    `just plan` from before this carries it, and the safe direction is the one taken —
    an exemption this cannot bound to a launch is refused rather than granted for the life
    of the project, which is exactly what it was.

    Stood up here rather than reached through an older `just plan`, which this checkout no
    longer has: the stamp is written as that release wrote it, through the same helper the
    current shape is written through.
    """
    native = "approve-design-planning-unbounded"
    # llmlint: ignore[tests_mirror_real_usage] see the paragraph above this line
    project = store.plan(native, stamp=PLANNING)

    refused = _launch(project, runs)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "holds no design document" in refused.stderr, refused.stderr
    assert "is the plan a planning launch is writing" not in refused.stderr, (
        f"a stamp naming no node still bought an exemption:\n{refused.stderr}"
    )
    assert not runs.exists(), "a launch refused for an unbounded stamp reached the ledger"

    store.document(native)
    approved = _just("approve-design", project, runs=runs)
    assert approved.returncode == 0, approved.stdout + approved.stderr
    launched = _launch(project, runs)
    assert launched.returncode == 0, launched.stdout + launched.stderr
    assert _settled(launched) == "complete", launched.stdout


#: Every stamp shape the gate has to answer for and cannot read as a launch, beside the
#: one it can. Each is a project's own claim to the exemption written the way a project
#: would really carry it, and each is answered as no claim at all — which is the direction
#: an unreadable exemption has to be answered in, because the other one dispatches work
#: nobody has approved. The bare :data:`PLANNING` string has a journey of its own above,
#: since it is the shape every project stamped by an older `just plan` carries.
UNREADABLE_STAMPS: tuple[tuple[str, object], ...] = (
    ("another-kind", {STAMP_KIND: "release", STAMP_NODES: [PLANNING_NODE]}),
    ("no-nodes-field", {STAMP_KIND: PLANNING}),
    ("nodes-not-a-sequence", {STAMP_KIND: PLANNING, STAMP_NODES: PLANNING_NODE}),
    ("a-node-that-is-no-id", {STAMP_KIND: PLANNING, STAMP_NODES: [7]}),
    ("a-node-claimed-twice", {STAMP_KIND: PLANNING, STAMP_NODES: [PLANNING_NODE, PLANNING_NODE]}),
    ("no-node-named", {STAMP_KIND: PLANNING, STAMP_NODES: []}),
)


@pytest.mark.xdist_group("approve-design")
def test_a_stamp_this_cannot_read_as_a_launch_is_answered_as_no_claim_to_the_exemption(
    store: Store, runs: Path
) -> None:
    """Every unreadable stamp, against the real gate rather than against a stand-in for it.

    The exemption is the one answer that lets a plan dispatch with nobody having approved
    anything, so what a project *claiming* it in a shape the gate cannot read gets is worth
    proving through the launch an operator makes rather than through a call into the module.
    Six shapes, and the last is the one this bound turns on: a stamp naming no node bounds
    the exemption to nothing, which is not an exemption over an empty project. The one
    before it is the shape a count would accept — this plan holds one task and that stamp
    makes one claim, twice — and no launch dispatches a node twice, so it is a claim to an
    exemption bounded to a launch nothing wrote rather than one naming the node it repeats.

    Two absences carry the claim, and they are different readings. The exemption's own
    sentence is absent, so none of these dispatched on a claim; and the lapse's sentence is
    absent too, so none of them was read as a planning launch whose bound had *ended* —
    which is the answer a readable stamp gets and would leave the shape unproven. The
    positive control is the journey above: the same plan, the same store, the same launch,
    and a stamp this can read, which does dispatch on the exemption and says so.

    Stood up here rather than reached through a `just plan` that wrote one of these, for the
    reason the journeys above give and because no release of it ever wrote four of the five:
    what is under test is the gate's own reading of one project's own claim.
    """
    for name, stamp in UNREADABLE_STAMPS:
        native = f"approve-design-stamp-{name}"
        # llmlint: ignore[tests_mirror_real_usage] see the paragraph above this line
        project = store.plan(native, stamp=stamp)

        refused = _launch(project, runs)
        assert refused.returncode == 1, f"{name}: {refused.stdout}{refused.stderr}"
        assert "holds no design document" in refused.stderr, f"{name}: {refused.stderr}"
        assert "is the plan a planning launch is writing" not in refused.stderr, (
            f"{name}: a stamp the gate cannot read as a launch still bought the "
            f"exemption:\n{refused.stderr}"
        )
        assert "is stamped as the plan a planning launch writes" not in refused.stderr, (
            f"{name}: an unreadable stamp was read as a planning launch whose exemption "
            f"had lapsed, rather than as no claim to one:\n{refused.stderr}"
        )
        assert not runs.exists(), f"{name}: a refused launch reached the ledger"

    # And the refusal is the ordinary one rather than a project no approval can rescue:
    # the last of those shapes launches once somebody has read its design document.
    last = f"{SOURCE}:approve-design-stamp-{UNREADABLE_STAMPS[-1][0]}"
    store.document(f"approve-design-stamp-{UNREADABLE_STAMPS[-1][0]}")
    approved = _just("approve-design", last, runs=runs)
    assert approved.returncode == 0, approved.stdout + approved.stderr
    launched = _launch(last, runs)
    assert launched.returncode == 0, launched.stdout + launched.stderr
    assert _settled(launched) == "complete", launched.stdout


@pytest.mark.xdist_group("approve-design")
def test_a_project_holding_more_than_one_document_is_refused_rather_than_guessed_at(
    store: Store, runs: Path
) -> None:
    """Which of two documents is the design document cannot be decided here.

    An approval recorded against the wrong one of two reads as sound from every side
    afterwards, so both commands refuse and name what they found. The person who wrote the
    second document is the one who can say which is which.
    """
    native = "approve-design-ambiguous"
    project = store.plan(native)
    store.document(native)
    store.document(native, named="notes")

    refused = _just("approve-design", project, runs=runs)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "holds 2 documents" in refused.stderr, refused.stderr
    assert f"{SOURCE}:{native}-design" in refused.stderr, refused.stderr
    assert f"{SOURCE}:{native}-notes" in refused.stderr, refused.stderr

    launched = _launch(project, runs)
    assert launched.returncode == 1, launched.stdout + launched.stderr
    assert "holds 2 documents" in launched.stderr, launched.stderr
    assert not runs.exists(), "an ambiguous project reached the ledger"


@pytest.mark.xdist_group("approve-design")
def test_an_approval_that_could_not_be_written_is_reported_rather_than_claimed(
    store: Store, runs: Path
) -> None:
    """A record the store would not take leaves the plan unapproved, and says so.

    Driven by making the record itself read-only, which stops the write without stopping
    the read before it — so what fails is the write rather than the reading of the
    document it is about.
    """
    native = "approve-design-unwritable"
    project = store.plan(native)
    record = store.document(native)
    record.chmod(0o400)
    try:
        refused = _just("approve-design", project, runs=runs)
    finally:
        record.chmod(0o600)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "approve-design:" in refused.stderr, refused.stderr
    assert "recorded the approval" not in refused.stdout, refused.stdout

    # And the plan is still unapproved, which is the half a report alone could get wrong.
    launched = _launch(project, runs)
    assert launched.returncode == 1, launched.stdout + launched.stderr
    assert "carries no approval for what it currently says" in launched.stderr, launched.stderr


@pytest.mark.xdist_group("approve-design")
def test_a_project_the_store_cannot_answer_for_is_refused_before_anything_is_dispatched(
    store: Store, runs: Path
) -> None:
    """A configured source and an id it does not hold: the launch reads the same project.

    So refusing here loses no launch that would have worked, and it is the difference
    between a named reason and a launch that got half-way.
    """
    refused = _launch(f"{SOURCE}:no-such-project", runs)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "could not be read out of the plan store" in refused.stderr, refused.stderr
    assert not runs.exists(), "a project the store could not answer for reached the ledger"


@pytest.mark.xdist_group("approve-design")
def test_a_plan_store_that_cannot_be_read_at_all_is_its_own_exit_status(
    store: Store, runs: Path
) -> None:
    """Whether the plan has an approved design document is then unknown, not answered.

    Reached the way an operator reaches it — a `ONETASKGRAPH_` name that CLI's own
    configuration layer does not know, which is what every one of them is read as. Its own
    exit status, because a launch that stopped for an unreadable store and one that
    stopped for an unapproved plan owe different next actions.
    """
    project = store.plan("approve-design-unreadable-store")
    store.document("approve-design-unreadable-store")
    assert _just("approve-design", project, runs=runs).returncode == 0

    refused = _just(
        "orchestrate", project, *NO_OBSERVER, runs=runs, extra={"ONETASKGRAPH_NO_SUCH": "1"}
    )
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert "plan store could not be read" in refused.stderr, refused.stderr


@pytest.mark.xdist_group("approve-design")
def test_two_plans_in_one_store_each_keep_their_own_approval(store: Store, runs: Path) -> None:
    """A second approval lands on the second document rather than on the first one's.

    The write goes through the store's own copy verb, which records where each copy came
    from and follows that correspondence next time — so two documents staged under one
    name in that source would share one correspondence, and the second written would land
    on the first one's record. Driven with two plans in one store because that is the only
    place it shows: each one alone passes.
    """
    natives = ("approve-design-first", "approve-design-second")
    projects = [store.plan(native) for native in natives]
    records = [store.document(native) for native in natives]
    for project in projects:
        recorded = _just("approve-design", project, runs=runs)
        assert recorded.returncode == 0, recorded.stdout + recorded.stderr

    for native, record in zip(natives, records, strict=True):
        held = record.read_text(encoding="utf-8")
        assert "orchestrator.design-approval" in held, (
            f"{native}'s document carries no approval, so the write landed elsewhere:\n{held}"
        )
        assert f"Design: {native}" in held, (
            f"{native}'s record was overwritten by another document's:\n{held}"
        )

    for project in projects:
        launched = _launch(project, runs)
        assert launched.returncode == 0, launched.stdout + launched.stderr
        assert _settled(launched) == "complete", launched.stdout


#: The one field of :class:`StoreDocument` the listing answers outside the item payload:
#: a document is addressed by `id` on the envelope and the payload repeats no part of it.
#: Every other field is read from that payload under its own name.
ADDRESSED_AS = {"qualified_id": "id"}

#: What the destination mints for itself rather than taking from the source. Each is
#: asserted below to *differ* across a real copy: a destination that left one alone would
#: make the launch at the end of that journey pass whether or not the approval key
#: excluded it, which is the one way this journey could assert nothing.
MINTED = ("qualified_id", "project", "location")

#: The field compared by whether it carries an approval record at all rather than whole.
#: The destination's own bookkeeping legitimately sits in the metadata map beside the
#: record, and what the record *says* is the launch gate's to decide — which the launch at
#: the end of that journey is what decides.
BY_ITS_APPROVAL_RECORD = ("metadata",)

#: Everything else a store reports about a document — what a copy is defined to leave
#: alone — taken from :class:`StoreDocument` rather than listed here. Derived because the
#: list is not this module's to keep: a hand-written model of the payload beside that
#: record would go on passing while saying nothing about a field added to it, which is
#: exactly what a journey claiming to compare *every* field must not do. An added field
#: lands here, under the strict reading, so it comes due rather than passing unexamined.
CARRIED = tuple(
    field.name
    for field in dataclasses.fields(StoreDocument)
    if field.name not in MINTED + BY_ITS_APPROVAL_RECORD
)

#: Every field of that record, in the three groups the comparison below reads them in.
COMPARED = (*CARRIED, *MINTED, *BY_ITS_APPROVAL_RECORD)


def test_every_field_of_a_stored_document_is_classified_by_the_copy_comparison() -> None:
    """:data:`MINTED` and :data:`BY_ITS_APPROVAL_RECORD` name fields that exist.

    Exhaustiveness holds by construction — :data:`CARRIED` is the complement — so what
    this adds is the other half of the partition: a name in either hand-written group
    that is not a field of :class:`StoreDocument`, a typo or a field since renamed, would
    move a real field into :data:`CARRIED` and go on passing while the group it was meant
    to be in silently emptied.
    """
    record = {field.name for field in dataclasses.fields(StoreDocument)}
    classified = MINTED + BY_ITS_APPROVAL_RECORD
    assert set(classified) <= record, (
        f"{sorted(set(classified) - record)} is classified here and is not a field of "
        f"{StoreDocument.__name__}, whose fields are {sorted(record)}"
    )
    assert len(set(classified)) == len(classified), classified


def _documents(project: str, runs: Path) -> list[dict[str, Any]]:
    """Every document of ``project``, read through `just plans` — the operator's own surface.

    Deliberately not this repository's own reader: what a journey asserts about a stored
    record should be what somebody looking at the store would see, and
    :func:`~orchestrator.plan_store.read_documents` is a party to the copy — `just
    copy-plan` carries a plan's documents over through it — rather than a witness of it.
    What *is* taken from that module is which fields to read, so that this journey and the
    record it is comparing are one model of the payload rather than two.
    """
    listed = _just("plans", "document", "list", "--project", project, "--json", runs=runs)
    assert listed.returncode == 0, listed.stdout + listed.stderr
    return [_held(one) for one in json.loads(listed.stdout)["items"]]


def _held(one: Mapping[str, Any]) -> dict[str, Any]:
    """One listed document as :data:`COMPARED` reads it, refusing a payload short of it.

    Indexed rather than defaulted: a field of :class:`StoreDocument` the store does not
    answer under that name would otherwise read as absent from both sides of the copy and
    compare equal, which is the silence deriving these names exists to end.
    """
    payload = one["item"]
    missing = sorted(name for name in COMPARED if name not in ADDRESSED_AS and name not in payload)
    assert not missing, (
        f"the store answered document {one['id']} without {missing}, which "
        f"{StoreDocument.__name__} declares, so this journey cannot say what a copy did "
        f"to those fields: {payload}"
    )
    return {
        name: one[ADDRESSED_AS[name]] if name in ADDRESSED_AS else payload[name]
        for name in COMPARED
    }


@pytest.mark.xdist_group("approve-design")
def test_an_approval_travels_with_the_document_onto_the_store_the_plan_is_launched_from(
    store: Store, board: Store, runs: Path
) -> None:
    """A plan approved where it was drafted is still approved once it has been copied.

    That is the order this repository's own plans go in — drafted locally, cleared there,
    approved there, copied onto the board, launched from the board — and the approval is
    written into the document's own metadata map precisely so that it survives the middle
    step. Carrying the record is only half of surviving: the destination recomputes the key
    over what arrived, so a key covering anything the destination owns arrives intact and
    no longer matches. It did, and the two ways past it were both wrong — re-running the
    approval against the copy, which records an approval nobody gave, or launching from the
    drafting store, which projects every settlement into a gitignored local directory.

    So the launch at the end is the assertion, and everything before it is the state: the
    copy is real, both stores are real, and the launch is the same real `just orchestrate`
    every refusal above is taken from.
    """
    native = "approve-design-copied"
    drafted = store.plan(native)
    store.document(native)

    # `just copy-plan` refuses a plan no review record covers, so a cleared plan is state
    # this journey has to reach rather than anything it is about. `reviewed` reaches it the
    # way an operator does — the real recipe, the real script, the real `oneharness` CLI
    # and its response schema — and substitutes the paid provider process alone.
    # llmlint: ignore[e2e_not_mocked] see the note above this line
    reviewed(drafted)

    approved = _just("approve-design", drafted, runs=runs)
    assert approved.returncode == 0, approved.stdout + approved.stderr

    # The source record as its own store reports it, read before the copy so that what a
    # copy did to each field is a comparison of two real stores rather than a constant.
    (before,) = _documents(drafted, runs)

    landed_native = f"{native}-on-the-board"
    # The destination holding this plan under an identifier of its own is the *precondition*
    # rather than the interface under test — the copy below and the launch at the end are
    # both the real commands — and no command reaches it here: the board this repository
    # launches from mints that identifier itself, a local store copied onto keeps the
    # drafted plan's native id whatever flags the copy is given, and `project copy` has no
    # rename. Without it the journey would assert nothing, because the two ids would agree
    # and the key would match either way.
    # llmlint: ignore[tests_mirror_real_usage] see the note above this line
    board.standing_project(landed_native, titled=native)
    copied = _just("copy-plan", drafted, "--to", BOARD, "--match-by", "title", runs=runs)
    assert copied.returncode == 0, copied.stdout + copied.stderr

    landed = f"{BOARD}:{landed_native}"
    (after,) = _documents(landed, runs)

    # The condition the whole journey turns on, asserted rather than assumed: the record
    # travelled, and it travelled onto a document of an identifier the destination owns
    # rather than of the plan it was drafted under. A copy that left that identifier alone
    # would make the launch below pass whatever the key covers.
    assert RECORD_KEY in after["metadata"], (
        f"the approval did not travel with the document: {after}"
    )
    assert after["project"] == landed_native, after
    assert after["project"] != native, after

    # What the approval key may be composed of is a claim about which fields a copy holds
    # and which it rewrites, and this is the one place both halves can be read off a real
    # copy of a real record. `tests/test_design_approval.py` argues the key from exactly
    # this partition and states no copy contract of its own, so a copy that began holding
    # an identifier or rewriting the prose fails here — where a copy can be watched —
    # rather than passing there against a model nothing reconciles.
    for name in CARRIED:
        assert after[name] == before[name], (
            f"the copy rewrote {name}, which a key covering it could not survive:\n"
            f"  drafted: {before[name]!r}\n"
            f"  copied:  {after[name]!r}"
        )
    for name in MINTED:
        assert before[name] != after[name], (
            f"the destination did not mint a {name} of its own ({before[name]!r}), so "
            f"this journey would pass whether or not the approval key excluded it"
        )

    launched = _launch(landed, runs)
    assert launched.returncode == 0, (
        f"the copied plan carries the approval recorded where it was drafted and its "
        f"launch was refused anyway:\n{launched.stdout}\n{launched.stderr}"
    )
    assert _settled(launched) == "complete", launched.stdout
