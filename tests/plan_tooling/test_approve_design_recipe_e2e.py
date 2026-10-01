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
import yaml
from project_fixtures import ONEVCS_HOME, helper, register_stand_in, reviewed
from published_tools import ONETASKGRAPH_BIN
from waits import timeout as e2e_timeout

from orchestrator import design_chain
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

#: The source a document written by hand is drafted in before the store's own `document
#: copy` puts it into a plan's store — the way a document reached a plan before documents
#: were renderings, and the one shape here that records no rendering at all. Named on the
#: copy's own command line rather than in the environment, so no launch below sees it.
DRAFT = "drafted"

#: The pinned engine, whose `template resolve design-doc` states the template every
#: document here is rendered from, exactly as a design-doc dispatch pipes it.
ENGINE = REPO_ROOT / ".venv" / "bin" / "onepipeline"

#: The provenance key the store records a rendering under, and what a design-doc
#: rendering records there.
PROVENANCE = "onetaskgraph.template"
DESIGN_REFERENCE = "onepipeline:design-doc"

#: The regenerate every refusal of a document that is not the rendering in force names.
REGENERATE = "onepipeline template resolve design-doc --json | onetaskgraph document render"

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

#: The answers each plan's document here is rendered from, before anybody has approved it.
DESIGN: Mapping[str, object] = {
    "what": "One node that changes nothing.",
    "why": "Something has to be launchable for this to be a gate at all.",
    "architecture": "One node, settled without a dispatch.",
    "units": [
        {
            "name": "Handoff",
            "repository": "ai-orchestrator",
            "part": "",
            "summary": "Nothing outside this plan reads it.",
            "reversible": [],
            "decisions": [],
        }
    ],
    "acceptance_criteria": ["The run settles and nothing changed."],
    "planned_tasks": [
        {
            "task": "handoff",
            "unit": "Handoff",
            "delivers": "the recorded no-change handoff",
            "depends_on": "none",
            "location": "the store's own location",
        }
    ],
}

#: The same document's answers as the design-doc template this host shipped before its
#: per-unit architecture: a `contracts` list where `units` now stands, and table rows
#: naming no unit. A document still holding these is one rendered before that change.
RETIRED_DESIGN: Mapping[str, object] = {
    "what": "One node that changes nothing.",
    "why": "Something has to be launchable for this to be a gate at all.",
    "architecture": "One node, settled without a dispatch.",
    "contracts": ["None: nothing outside this plan reads it."],
    "acceptance_criteria": ["The run settles and nothing changed."],
    "planned_tasks": [
        {
            "task": "handoff",
            "delivers": "the recorded no-change handoff",
            "depends_on": "none",
            "location": "the store's own location",
        }
    ],
}

#: That template's text, as it stood before the per-unit architecture landed: the
#: front matter and body this host shipped, cut down to what renders `RETIRED_DESIGN`.
RETIRED_TEMPLATE = """---
onetaskgraph_template: 1
description: The design document a plan is read and approved as.
variables:
  what: {description: What, type: text, required: true}
  why: {description: Why, type: text, required: true}
  architecture: {description: Architecture, type: text, required: true}
  contracts: {description: Contracts, type: list, items: string, required: true}
  acceptance_criteria: {description: Criteria, type: list, items: string, required: true}
  planned_tasks: {description: Tasks, type: list, items: object, required: true}
---
## What

{{ what }}

## Why

{{ why }}

## Architecture

{{ architecture }}

## Contracts

{% for contract in contracts %}
- {{ contract }}
{% endfor %}

## Acceptance criteria

{% for criterion in acceptance_criteria %}
- {{ criterion }}
{% endfor %}

## Planned tasks

| Task | What it delivers | Depends on | Where it lives |
| --- | --- | --- | --- |
{% for row in planned_tasks %}
| {{ row.task }} | {{ row.delivers }} | {{ row.depends_on }} | {{ row.location }} |
{% endfor %}
"""

#: The repositories a plan's tasks name below, as the normalized origins a task record
#: holds: the one a single-repository plan names throughout, and a second for a plan that
#: names several. Neither is a repository anything here fetches; each is registered as a
#: stand-in checkout where a journey needs the engine to resolve it.
SERVICE = "git.example.invalid/journey/service"
CLIENT = "git.example.invalid/journey/client"

#: Answers to this host's `plan-task` template, which renders a document here only to be
#: one that records another template's provenance.
PLAN_TASK_ANSWERS: Mapping[str, object] = {
    "what": "One node that changes nothing.",
    "why": "Something has to be launchable for this to be a gate at all.",
    "acceptance_criteria": ["The run settles and nothing changed."],
}

#: The same document written by hand, as prose rather than as a rendering.
HAND_WRITTEN = (
    "## What\n\nOne node that changes nothing.\n\n"
    "## Why\n\nSomething has to be launchable for this to be a gate at all.\n\n"
    "## Architecture\n\nOne node, settled without a dispatch.\n\n"
    "## Contracts\n\n- None: nothing outside this plan reads it.\n\n"
    "## Acceptance criteria\n\n- The run settles and nothing changed.\n\n"
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
        repositories: Sequence[str] = (),
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
        ``repositories`` names, node by node in that order, the one repository each task's
        record names; a node past its end names none.
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
                        **({"repo": repositories[index]} if index < len(repositories) else {}),
                        "task": (
                            "## What\n\nRecord that nothing changes.\n\n"
                            "## Why\n\nThe boundary is explicit.\n\n"
                            "## Acceptance criteria\n\n- Nothing changed.\n"
                        ),
                    }
                    for index, node_id in enumerate((*launched, *beyond))
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

    def document(
        self,
        native: str,
        answers: Mapping[str, object] = DESIGN,
        *,
        named: str = "design",
        template_root: Path = REPO_ROOT / "templates",
        template: str = "design-doc",
        repository: str | None = None,
    ) -> Path:
        """Render one document of ``native``'s plan the way the design-doc dispatch does.

        The answers are the model's, and the one part of that dispatch nothing here spends;
        the rendering is the pinned engine's `template resolve design-doc` piped into the
        store's own `document create`, which is what a design-doc dispatch runs to store
        what it wrote. ``template_root`` is the host root that resolve reads, which is this
        checkout's own unless a journey renders from a template that is no longer in force,
        and ``template`` the registered name resolved, which is `design-doc` unless a
        journey renders the document from another template altogether. ``repository`` is
        the origin the resolve names, as the writer's task names it for a plan whose tasks
        all name that one repository.

        ``named`` is what distinguishes a second document *of the same project* from a
        document of another one — which is the state the ambiguity refusal is about, and
        which a second plan's document would not reach.

        Answers where the store says the stored record is, read back from the store rather
        than composed, which is the same rule the refusals themselves follow.
        """
        written = self.drafts.parent / f"{self.root.name}-{native}-{named}.answers.json"
        written.write_text(json.dumps(dict(answers)), encoding="utf-8")
        created = self._piped(
            "document",
            "create",
            self.source,
            "--project",
            native,
            "--title",
            f"Design: {native} ({named})",
            "--id",
            f"{native}-{named}",
            "--answers",
            str(written),
            template_root=template_root,
            template=template,
            resolving=design_chain.resolve_arguments(repository),
        )
        (record,) = created["items"]
        return Path(str(record["item"]["location"]["path"]))

    def regenerate(
        self, native: str, *answered: str, named: str = "design", answers: Path | None = None
    ) -> dict[str, Any]:
        """Regenerate a stored document in place from its stored answers, overlaid by ``answered``.

        The repair every refusal of an unrendered document names: the pinned resolve piped
        into the store's own `document render`, each ``NAME=VALUE`` an answer changed, and
        ``answers`` a file of answers supplied whole, for a document that holds none.
        """
        changed = [flag for answer in answered for flag in ("--var", answer)]
        supplied = ["--answers", str(answers)] if answers is not None else []
        return self._piped(
            "document", "render", f"{self.source}:{native}-{named}", *changed, *supplied
        )

    def listed(self) -> list[dict[str, Any]]:
        """Every document this store holds, as the store's own `document list` answers."""
        held: list[dict[str, Any]] = self._stored("document", "list", "--source", self.source)[
            "items"
        ]
        return held

    def regenerating(self, qualified: str, *supplied: str) -> subprocess.CompletedProcess[str]:
        """The regenerate a refusal names, run on ``qualified`` and answered whatever it says.

        :meth:`regenerate` holds the store to succeeding; this is the same pinned resolve
        piped into `document render` for a journey that has to read a refusal of it too.
        """
        resolved = subprocess.run(
            [str(ENGINE), "template", "resolve", "design-doc", "--json"]
            + ["--template-root", str(REPO_ROOT / "templates")],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(60),
            check=False,
        )
        assert resolved.returncode == 0, resolved.stderr
        return subprocess.run(
            [str(ONETASKGRAPH_BIN), "document", "render", qualified, *supplied]
            + ["--template-loader", "-", "--no-interactive", "--json"],
            input=resolved.stdout,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(60),
            check=False,
        )

    def hand_written(self, native: str, content: str = HAND_WRITTEN) -> Path:
        """Put a document written by hand into ``native``'s plan, recording no rendering.

        Drafted in :data:`DRAFT` and put into the plan's store with the store's own
        `document copy`, which is how a document reached a plan before documents were
        renderings — so it is what a document with no provenance really looks like.
        """
        drafted = f"{native}-design"
        documents = self.drafts / "documents"
        documents.mkdir(parents=True, exist_ok=True)
        (documents / f"{drafted}.md").write_text(
            frontmatter({"title": f"Design: {native} (design)", "project": native}, content),
            encoding="utf-8",
        )
        copied = self._stored("document", "copy", f"{DRAFT}:{drafted}", "--to", self.source)
        (one,) = copied["items"]
        shown = self._stored("document", "show", str(one["destination"]))
        (record,) = shown["items"]
        return Path(str(record["item"]["location"]["path"]))

    def _piped(
        self,
        *arguments: str,
        template_root: Path = REPO_ROOT / "templates",
        template: str = "design-doc",
        resolving: Sequence[str] = (),
    ) -> dict[str, Any]:
        """The pinned `template resolve <template>`, piped into one pinned store command."""
        resolved = subprocess.run(
            [str(ENGINE), "template", "resolve", template, *resolving, "--json"]
            + ["--template-root", str(template_root)],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(60),
            check=False,
        )
        assert resolved.returncode == 0, resolved.stderr
        answered = subprocess.run(
            [str(ONETASKGRAPH_BIN), *arguments]
            + ["--template-loader", "-", "--no-interactive", "--json"],
            input=resolved.stdout,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(60),
            check=False,
        )
        assert answered.returncode == 0, answered.stdout + answered.stderr
        # Narrowed to a mapping and no further, for the reason `_stored` gives.
        payload: dict[str, Any] = json.loads(answered.stdout)
        return payload

    def metadata(self, record: Path) -> dict[str, Any]:
        """What the store says the document at ``record`` holds under `metadata`.

        Read back through the store rather than off the file, because that is the reader
        the launch check is: a write the store could not parse would still look right in
        a diff.
        """
        listed = self._stored("document", "list", "--source", self.source)
        for one in listed["items"]:
            if one["item"]["location"]["path"] == str(record):
                held: dict[str, Any] = one["item"]["metadata"]
                return held
        raise AssertionError(f"{record} is not a document the store lists:\n{listed}")

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

    def edit(self, record: Path, said: str, instead: str) -> None:
        """Change what a stored document says, where the store says it is.

        What a person does with the path every refusal hands them — *"It is at …: read
        it"* — and the only editing interface a local Markdown store has: the first place
        the body says ``said`` is made to say ``instead``. The record the store keeps
        around the prose — its metadata, and the answers it stores after the body — is left
        alone, because a reader edits the document rather than replacing it.
        """
        text = record.read_text(encoding="utf-8")
        assert said in text, f"{record} does not say {said!r}:\n{text}"
        record.write_text(text.replace(said, instead, 1), encoding="utf-8")

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

    # The store recorded which template rendered the document; the approval has to leave
    # that standing, because it is what the approval is keyed on.
    stored = store.metadata(record)
    assert stored[PROVENANCE]["template"] == DESIGN_REFERENCE, stored
    assert RECORD_KEY not in stored, stored
    rendered = record.read_bytes()

    approved = _just("approve-design", project, runs=runs)
    assert approved.returncode == 0, approved.stdout + approved.stderr
    assert "recorded the approval" in approved.stdout, approved.stdout
    # One entry landed and every other byte of the document is as the store rendered it,
    # the provenance included.
    assert RECORD_KEY in store.metadata(record)
    assert store.metadata(record)[PROVENANCE] == stored[PROVENANCE]
    entry = f'  "{RECORD_KEY}": '.encode()
    kept = [
        line for line in record.read_bytes().splitlines(keepends=True) if not line.startswith(entry)
    ]
    assert len(kept) == rendered.count(b"\n"), record.read_text(encoding="utf-8")
    assert b"".join(kept) == rendered, "the approval rewrote more than its one entry"

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

    # Regenerating it with unchanged answers and an unchanged template renders the same
    # document, so the approval still holds and the plan still launches.
    unchanged = store.regenerate(native)
    assert unchanged["changed"] is False, unchanged
    kept_launch = _launch(project, runs)
    assert kept_launch.returncode == 0, kept_launch.stdout + kept_launch.stderr

    # Editing the body by hand afterwards leaves a document that is not the rendering its
    # provenance records: both the recipe and the launch refuse it, naming the regenerate.
    store.edit(record, "One node that changes nothing.", "Something else.")
    edited = _launch(project, runs)
    assert edited.returncode == 1, edited.stdout + edited.stderr
    assert "was edited after it was rendered" in edited.stderr, edited.stderr
    assert REGENERATE in edited.stderr, edited.stderr
    unapprovable = _just("approve-design", project, runs=runs)
    assert unapprovable.returncode == 1, unapprovable.stdout + unapprovable.stderr
    assert "was edited after it was rendered" in unapprovable.stderr, unapprovable.stderr
    assert REGENERATE in unapprovable.stderr, unapprovable.stderr

    # Changing an answer and regenerating is content nobody has approved, which is the
    # whole reason the record is keyed on the rendering rather than on the plan.
    changed = store.regenerate(native, "what=Something else.")
    assert changed["changed"] is True, changed
    regenerated = _launch(project, runs)
    assert regenerated.returncode == 1, regenerated.stdout + regenerated.stderr
    assert "carries no approval for what it currently says" in regenerated.stderr, (
        regenerated.stderr
    )

    reapproved = _just("approve-design", project, runs=runs)
    assert reapproved.returncode == 0, reapproved.stdout + reapproved.stderr
    assert "recorded the approval" in reapproved.stdout, reapproved.stdout
    relaunched = _launch(project, runs)
    assert relaunched.returncode == 0, relaunched.stdout + relaunched.stderr
    assert _settled(relaunched) == "complete", relaunched.stdout

    # Renaming the document is a change to what a person read too, though no body byte
    # moves: the title is in the key, so the launch is refused until it is approved again.
    _retitled(record, f"Design: {native}, renamed")
    retitled = _launch(project, runs)
    assert retitled.returncode == 1, retitled.stdout + retitled.stderr
    assert "carries no approval for what it currently says" in retitled.stderr, retitled.stderr
    renamed = _just("approve-design", project, runs=runs)
    assert renamed.returncode == 0, renamed.stdout + renamed.stderr
    assert "recorded the approval" in renamed.stdout, renamed.stdout


def _retitled(record: Path, title: str) -> None:
    """Rename the document at ``record`` the one way a local store offers: its front matter.

    The store has no verb that retitles a document, so this is the edit a person makes to
    the file every refusal points them at, read and written as the YAML it is, leaving the
    body and the provenance as the store rendered them.
    """
    text = record.read_text(encoding="utf-8")
    _, front, body = text.split("---\n", 2)
    matter = yaml.safe_load(front)
    matter["title"] = title
    record.write_text(
        f"---\n{yaml.safe_dump(matter, sort_keys=False, allow_unicode=True)}---\n{body}",
        encoding="utf-8",
    )


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] `plan-tooling` is the leaf
# project keyed on `planToolingWorkspace`, the edge this rule asks for, and every other gate
# journey of this module already runs behind it. That key names the recipes, scripts and
# package these launches drive, so narrowing it would memoize a verdict over a tree never run.
def _without_its_body_digest(record: Path) -> None:
    """Drop the body digest from the provenance ``record``'s front matter holds, and nothing else.

    Read and written as the YAML the front matter is, so it holds whichever spelling the
    store wrote the provenance mapping in; the body below the front matter is left as the
    store rendered it.
    """
    text = record.read_text(encoding="utf-8")
    _, front, body = text.split("---\n", 2)
    matter = yaml.safe_load(front)
    del matter["metadata"][PROVENANCE]["body_digest"]
    record.write_text(
        f"---\n{yaml.safe_dump(matter, sort_keys=False, allow_unicode=True)}---\n{body}",
        encoding="utf-8",
    )


def _moved_template_root(tmp_path: Path) -> Path:
    """A host root whose `design-doc` template differs from this checkout's by one line.

    Rendering from it is what a document rendered *before* the template changed looks like
    from here: its provenance names `onepipeline:design-doc`, under a chain digest that is
    not the one this checkout resolves now.
    """
    root = tmp_path / "moved-templates"
    shutil.copytree(REPO_ROOT / "templates", root)
    template = root / "design-doc.md.j2"
    template.write_text(
        template.read_text(encoding="utf-8").replace("## Why\n", "## Why\n\n<!-- moved -->\n"),
        encoding="utf-8",
    )
    return root


@pytest.mark.xdist_group("approve-design")
@pytest.mark.parametrize(
    "shape",
    [
        "no provenance",
        "incomplete provenance",
        "another template's rendering",
        "a template changed after rendering",
    ],
)
def test_a_document_that_is_not_the_rendering_in_force_is_refused_by_the_recipe_and_the_launch(
    store: Store, runs: Path, tmp_path: Path, shape: str
) -> None:
    """None can be approved, and none can be launched, and each names the repair that works.

    A document written by hand records no rendering at all, and one whose provenance lost a
    field records no rendering this can read; one rendered from another
    registered template records that template's reference; one rendered from a template
    since changed records a chain digest the pinned engine no longer resolves. The hand
    edit of a rendering is the third shape, driven in the journey above.

    The named regenerate is then run as the refusal words it — from the stored answers, or
    from the design-doc answers where the document holds none of its own — and the document
    it leaves is approved and launched, so the repair each refusal names is one that works.
    """
    native = f"approve-design-{shape.split()[0]}-{len(shape)}"
    project = store.plan(native)
    match shape:
        case "no provenance":
            store.hand_written(native)
            said = "records no rendering of the design-doc template"
        case "incomplete provenance":
            _without_its_body_digest(store.document(native))
            said = "records no rendering of the design-doc template"
        case "another template's rendering":
            store.document(native, PLAN_TASK_ANSWERS, template="plan-task")
            said = "is a rendering of onepipeline:plan-task, not of onepipeline:design-doc"
        case _:
            store.document(native, template_root=_moved_template_root(tmp_path))
            said = "written against a template no longer in force"

    recorded = _just("approve-design", project, runs=runs)
    assert recorded.returncode == 1, recorded.stdout + recorded.stderr
    assert said in recorded.stderr, recorded.stderr
    assert REGENERATE in recorded.stderr, recorded.stderr

    launched = _launch(project, runs)
    assert launched.returncode == 1, launched.stdout + launched.stderr
    assert said in launched.stderr, launched.stderr
    assert REGENERATE in launched.stderr, launched.stderr
    assert "nothing was dispatched" in launched.stderr, launched.stderr
    assert not runs.exists(), "a refused launch wrote a run onto the ledger"

    # Each repair as its refusal words it: a document holding no stored answers a
    # regenerate can trust is regenerated from the design-doc answers supplied whole; a
    # rendering of another template is replaced by the store's `document create` naming its
    # id, which is what `store.document` runs; one rendered from a template since changed is
    # regenerated from the answers it holds.
    match shape:
        case "no provenance" | "incomplete provenance":
            assert f"{REGENERATE} " in recorded.stderr and "--answers" in recorded.stderr, (
                recorded.stderr
            )
            supplied = tmp_path / f"{native}.answers.json"
            supplied.write_text(json.dumps(dict(DESIGN)), encoding="utf-8")
            store.regenerate(native, answers=supplied)
        case "another template's rendering":
            assert "onetaskgraph document create" in recorded.stderr, recorded.stderr
            assert f"--id {native}-design --template-loader -" in recorded.stderr, recorded.stderr
            store.document(native)
        case _:
            store.regenerate(native)
    repaired = _just("approve-design", project, runs=runs)
    assert repaired.returncode == 0, repaired.stdout + repaired.stderr
    assert "recorded the approval" in repaired.stdout, repaired.stdout
    relaunched = _launch(project, runs)
    assert relaunched.returncode == 0, relaunched.stdout + relaunched.stderr
    assert _settled(relaunched) == "complete", relaunched.stdout


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


@pytest.mark.xdist_group("approve-design")
@pytest.mark.parametrize("shape", ["a hand edit", "a template changed after rendering"])
def test_a_copied_document_that_is_not_the_rendering_in_force_is_repaired_from_supplied_answers(
    store: Store, board: Store, runs: Path, tmp_path: Path, shape: str
) -> None:
    """On the board a plan is launched from, the regenerate is handed the answers whole.

    The store keeps a rendering's answers where it was drafted and never copies them, so a
    copy of a design document holds none: regenerating it from its stored answers is
    refused by the store for want of every required one. Each refusal of a stale or edited
    document therefore names the regenerate with the answers supplied as well, and this
    runs both as the refusal words them on a real copy — the one from stored answers
    refused, the one from supplied answers repairing the copy so it is approved and
    launched from the board.
    """
    native = f"approve-design-copied-{shape.split()[1]}"
    drafted = store.plan(native)
    if shape == "a hand edit":
        store.document(native)
        said = "was edited after it was rendered"
    else:
        store.document(native, template_root=_moved_template_root(tmp_path))
        said = "written against a template no longer in force"
    # llmlint: ignore[e2e_not_mocked] `reviewed` substitutes the paid provider process alone,
    # as the journey above says: `just copy-plan` refuses a plan no review record covers.
    reviewed(drafted)
    copied = _just("copy-plan", drafted, "--to", BOARD, runs=runs)
    assert copied.returncode == 0, copied.stdout + copied.stderr

    landed = f"{BOARD}:{native}"
    listed = board.listed()
    (document,) = listed
    if shape == "a hand edit":
        board.edit(
            Path(document["item"]["location"]["path"]), "One node that changes nothing.", "Else."
        )

    refused = _just("approve-design", landed, runs=runs)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert said in refused.stderr, refused.stderr
    assert f"{REGENERATE} {document['id']} " in refused.stderr, refused.stderr
    assert "--answers" in refused.stderr, refused.stderr

    # The copy holds no answers of its own, so the regenerate from stored answers alone
    # cannot repair it — which is why the refusal names the supplied one beside it.
    unanswered = board.regenerating(str(document["id"]))
    assert unanswered.returncode != 0, unanswered.stdout + unanswered.stderr
    assert "supply every required answer" in unanswered.stderr, unanswered.stderr

    supplied = tmp_path / f"{native}.answers.json"
    supplied.write_text(json.dumps(dict(DESIGN)), encoding="utf-8")
    repaired = board.regenerating(str(document["id"]), "--answers", str(supplied))
    assert repaired.returncode == 0, repaired.stdout + repaired.stderr

    approved = _just("approve-design", landed, runs=runs)
    assert approved.returncode == 0, approved.stdout + approved.stderr
    assert "recorded the approval" in approved.stdout, approved.stdout
    launched = _launch(landed, runs)
    assert launched.returncode == 0, launched.stdout + launched.stderr
    assert _settled(launched) == "complete", launched.stdout


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


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] `plan-tooling` is the leaf
# project keyed on `planToolingWorkspace`, the edge this rule asks for, and every other journey
# of this module already runs behind it. That key names the recipes, scripts, templates and
# package these journeys drive, so narrowing it would memoize a verdict over a tree never run.
def _registered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, origin: str, layer: str | None = None
) -> Path:
    """Register a stand-in checkout for ``origin``, carrying ``layer`` as its repository layer.

    In a registry of this journey's own, which every recipe below inherits, so the engine
    resolves ``origin`` to this checkout exactly as it resolves a registered repository's
    origin on a host. ``layer`` is the text of its `.onepipeline/templates/design-doc.md.j2`,
    or ``None`` for a checkout carrying no layer of its own. Answers the checkout.
    """
    home = tmp_path / "onevcs"
    monkeypatch.setenv(ONEVCS_HOME, str(home))
    checkout = register_stand_in(home, origin, tmp_path / origin.replace("/", "-"))
    if layer is not None:
        _layered(checkout, layer)
    return checkout


def _layered(checkout: Path, layer: str) -> None:
    """Write ``layer`` as ``checkout``'s repository layer of the design-doc template."""
    path = checkout / ".onepipeline" / "templates" / "design-doc.md.j2"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(layer, encoding="utf-8")


#: A repository layer overriding one guidance block of the host template, as a repository
#: overrides the bar its own documents are written to.
LAYER = (
    '{% extends "onepipeline/host/design-doc.md.j2" %}\n'
    "{% block what_guidance %}{# WHAT. Say what changes for the service's callers. #}"
    "{% endblock %}\n"
)


@pytest.mark.xdist_group("approve-design")
@pytest.mark.parametrize("plan", ["one repository", "several repositories", "no repository"])
def test_every_repair_names_the_resolve_command_the_rule_gives_for_the_plan(
    store: Store, runs: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, plan: str
) -> None:
    """A refusal's regenerate resolves the chain the plan's document is rendered through.

    A plan whose tasks all name one repository is resolved through that repository's
    layer, so its repair names `--repository <that origin>`; a plan whose tasks name
    several, or none, is resolved through the working directory's, so its repair names no
    repository. Each document is edited by hand after it was rendered, which is the
    refusal whose repair is a regenerate.
    """
    native = f"approve-design-rule-{plan.split()[0]}"
    match plan:
        case "one repository":
            _registered(tmp_path, monkeypatch, SERVICE)
            project = store.plan(native, beyond=["second"], repositories=[SERVICE, SERVICE])
            repository: str | None = SERVICE
        case "several repositories":
            project = store.plan(native, beyond=["second"], repositories=[SERVICE, CLIENT])
            repository = None
        case _:
            project = store.plan(native, beyond=["second"])
            repository = None
    record = store.document(native, repository=repository)
    store.edit(record, "One node that changes nothing.", "Something else.")

    refused = _just("approve-design", project, runs=runs)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "was edited after it was rendered" in refused.stderr, refused.stderr
    named = design_chain.resolve_command(repository)
    assert f"`{named} | onetaskgraph document render {project}-design " in refused.stderr, (
        refused.stderr
    )
    if repository is None:
        assert "--repository" not in refused.stderr, refused.stderr


@pytest.mark.xdist_group("approve-design")
def test_a_document_whose_stored_answers_no_longer_fit_is_sent_to_the_writer(
    store: Store, runs: Path, tmp_path: Path
) -> None:
    """A document rendered before the per-unit architecture is regenerated by the writer.

    Its stored answers name a variable the template in force no longer declares, so the
    store refuses to regenerate it in place; the repair names the design-document writer,
    and never the in-place `document render` the store would refuse.
    """
    native = "approve-design-retired"
    project = store.plan(native)
    retired = tmp_path / "retired-templates"
    shutil.copytree(REPO_ROOT / "templates", retired)
    (retired / "design-doc.md.j2").write_text(RETIRED_TEMPLATE, encoding="utf-8")
    store.document(native, RETIRED_DESIGN, template_root=retired)

    refused = _just("approve-design", project, runs=runs)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "do not fit the variables the template in force declares" in refused.stderr, (
        refused.stderr
    )
    assert "just finish-plan <brief>" in refused.stderr, refused.stderr
    assert design_chain.resolve_command(None) in refused.stderr, refused.stderr
    assert "document render" not in refused.stderr, refused.stderr

    # The regenerate in place the refusal declines to name is the one the store refuses.
    unfit = store.regenerating(f"{project}-design")
    assert unfit.returncode != 0, unfit.stdout + unfit.stderr

    # What the writer does instead: answer the template's current variables afresh and
    # store the document under the id it already holds, which the store replaces whole —
    # retired answers and all — so the plan's document is one a person can approve.
    store.document(native)
    approved = _just("approve-design", project, runs=runs)
    assert approved.returncode == 0, approved.stdout + approved.stderr
    assert "recorded the approval" in approved.stdout, approved.stdout


@pytest.mark.xdist_group("approve-design")
def test_a_plan_in_one_layered_repository_is_approved_on_the_chain_it_was_rendered_through(
    store: Store, runs: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The approval keys on the digest the rendering recorded, and a moved layer unapproves it.

    The plan's tasks all name one repository whose checkout carries a layer, so the writer
    renders through `--repository <that origin>` and the store records that chain's digest.
    The approval resolves the chain by the same rule, so the digest it keys on is the one
    the provenance records and the approval is granted; changing the layer moves the chain,
    and the document approved under the previous one is no longer approved.
    """
    checkout = _registered(tmp_path, monkeypatch, SERVICE, LAYER)
    native = "approve-design-layered"
    project = store.plan(native, beyond=["second"], repositories=[SERVICE, SERVICE])
    record = store.document(native, repository=SERVICE)

    resolved = subprocess.run(
        [str(ENGINE), "template", "resolve", "design-doc"]
        + [*design_chain.resolve_arguments(SERVICE), "--json"]
        + ["--template-root", str(REPO_ROOT / "templates")],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=True,
    )
    chain = json.loads(resolved.stdout)
    assert chain["layer"] == "repository", chain
    assert store.metadata(record)[PROVENANCE]["digest"] == chain["digest"]

    approved = _just("approve-design", project, runs=runs)
    assert approved.returncode == 0, approved.stdout + approved.stderr
    assert "recorded the approval" in approved.stdout, approved.stdout
    held = _just("approve-design", project, runs=runs)
    assert "already carries an approval" in held.stdout, held.stdout + held.stderr

    _layered(checkout, LAYER.replace("callers", "operators"))
    stale = _just("approve-design", project, runs=runs)
    assert stale.returncode == 1, stale.stdout + stale.stderr
    assert "written against a template no longer in force" in stale.stderr, stale.stderr
    assert design_chain.resolve_command(SERVICE) in stale.stderr, stale.stderr


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
