"""Two record shapes the plan store writes, and this repository could not read back.

Both of these are the same mistake with two faces: a reader here written against what an
*author* types into a record rather than against what the *store* answers with. Each stood
between a finished plan and the operator who has to approve and launch it, and each is
driven below through the command that meets it, over records the store itself rendered.

**A design document carrying labels could not be read at all.** The store's one canonical
output shape is `{id, name, color}` — the frozen plugin contract every plugin constructs —
while the `LabelInput` sugar an author types admits a bare string as well and is normalised
away on read. The reader demanded the sugar, so a document carrying any label was refused
as *"labels that are not a list of strings"*, and `just approve-design` and `just copy-plan`
both reach that reader.

**A task record holding a sequence-valued metadata entry could not carry a review record.**
YAML lets a block sequence stand at its own key's indent rather than below it and the
store's renderer takes that option, so `onepipeline.steps:` opens a block whose `- id: …`
items then sit level with the entries around them. The writer placed a line by indentation
alone and refused, every time on a `- id: …` line — each such record a task
`just review-plan` could not record a pass for and `just check-plan` then refused for
carrying no review record. What that cost is a share of the store rather than a fixed
count, and it moves as records are added: asked of this host's `.plans/tasks` while this
was written, the writer refused 45 of 451 records before this change and 0 of the same
451 after.

**Neither shape is written here, which is why each journey makes the store write it.** This
repository's own renderer emits a JSON flow sequence and a document nobody labelled, so a
fixture written through it would be in neither shape and would prove nothing. Each journey
puts the record through the store's own copy verb instead — `project copy` for the task and
`document copy` for the document — which is the path the real records reached these shapes
by, and re-renders them the store's way.

Everything below the recipe is real: the real recipes, the real scripts, the real
`orchestrator` package, the real installed `onetaskgraph`, and — for the review — the real
`oneharness` CLI and its response schema. `tests/e2e/fake_codex.py` stands in for the paid
provider alone.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import short_state
from project_fixtures import helper
from published_tools import ONETASKGRAPH_BIN
from waits import timeout as e2e_timeout

from orchestrator import design_approval, plan_review, plan_store
from orchestrator.criteria_guard import APPENDIX
from orchestrator.project_store import frontmatter, write_plan_project
from orchestrator.root import REPO_ROOT

#: This suite is its own Nx project, `plan-tooling`; see `tests/plan_tooling/project.json`
#: and the guard in `tests/conftest.py`.

#: The source each plan is authored in, and the one it is copied *out of*. Plain lowercase
#: because it is spelled into the store's own `ONETASKGRAPH_SOURCES__…` environment layer
#: as well as onto a command line.
DRAFTING = "drafting"

#: The source each plan is copied *into*, and the one every command below reads. The copy
#: is what re-renders a record the store's own way, so this is the source that holds the
#: shapes these journeys are about.
STORED = "stored"

#: The paid provider's stand-in, and the guard covering the identities `ONEHARNESS_BIN_*`
#: cannot reach. `just review-plan` spawns a real `oneharness run`, so the provider binary
#: is the seam; `just approve-design` should reach no provider at all, which is worth
#: guarding rather than assuming.
FAKE_CODEX = helper("fake_codex.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")

#: The reviewer's verdict, scripted. A pass carries no findings, because a finding *is* a
#: refused criterion and the verdict schema admits a pass only where it names none.
PASSES = {"passes": True, "findings": []}

#: Criteria answering every demand the tracked appendix and the shipped `engineer` bar
#: make, so nothing below is refused for a reason these journeys are not about.
STATES_ITS_BAR = (
    "- The route accepts a valid request and rejects an invalid one.\n"
    "- A request-level test drives the route end to end and covers both paths.\n"
    "- The dispatch closes with a completion report naming the evidence it verified."
)

#: The one document a plan is read as. Its shape is `config/design-doc-template.md`'s; what
#: matters here is only that it is a document, so it is the shortest one that is.
DESIGN = (
    "## What\n\nOne route, and the test that drives it.\n\n"
    "## Why\n\nThe user cannot complete a purchase without it.\n\n"
    "## Architecture\n\nOne route on the service that is already there.\n\n"
    "## Contracts\n\nThe route's request and response shape.\n\n"
    "## Acceptance criteria\n\nA request reaches the route and is answered.\n\n"
    "## Planned tasks\n\n"
    "| Task | What it delivers | Depends on | Where it lives |\n"
    "| --- | --- | --- | --- |\n"
    "| landing | the route and its test | none | the store's own location |\n"
)


def _step(step_id: str, what: str) -> dict[str, str]:
    """One step of a lifecycle node — the shape whose sequence rendering is in question.

    A stepped node states its prose per step rather than in `task`, so these are the
    criteria the review reads and the record it writes is keyed on.
    """
    return {
        "id": step_id,
        "persona": "engineer",
        "task": (
            f"## What\n\n{what}\n\n"
            "## Why\n\nThe user cannot complete a purchase without it.\n\n"
            f"## Acceptance criteria\n\n{STATES_ITS_BAR}\n\n"
            f"{(REPO_ROOT / APPENDIX).read_text(encoding='utf-8').strip()}\n"
        ),
    }


#: The plan every journey here authors: one lifecycle node running two steps on one
#: branch, which is the node shape whose `steps` the store renders as a block sequence.
STEPPED_PLAN: dict[str, object] = {
    "schema_version": 3,
    "goal": {"text": "Deliver the checkout route"},
    "tasks": [
        {
            "id": "landing",
            "title": "feat: add the checkout route",
            # No `persona` beside `steps`: a stepped node takes its persona, task and turn
            # budget from the steps, and the engine's loader refuses one that states both.
            "repo": "https://github.com/nickderobertis/some-service",
            "steps": [
                _step("build", "Add the route."),
                _step("prove", "Drive the route from a request-level test."),
            ],
        }
    ],
}


class Store:
    """The two local Markdown sources these journeys own, and the copy between them."""

    def __init__(self, drafts: Path, stored: Path) -> None:
        self.drafts = drafts
        self.stored = stored

    def plan(self, native: str) -> str:
        """Author :data:`STEPPED_PLAN` and copy it into :data:`STORED`; answer its id there.

        The copy is the point rather than plumbing: this repository's renderer writes
        `"onepipeline.steps": [ … ]` as a JSON flow sequence, which the writer under test
        has always been able to edit around, and the store's own renderer writes it as a
        block sequence at the parent key's indent, which is the shape it could not.
        """
        write_plan_project(self.drafts, {"name": native, **STEPPED_PLAN}, native_id=native)
        self.copy("project", f"{DRAFTING}:{native}")
        return f"{STORED}:{native}"

    def document(self, native: str, labels: list[object]) -> str:
        """Store one document of ``native``'s plan carrying ``labels``; answer its id.

        Written through this repository's own record renderer and put into the plan's
        store with the store's own `document copy`, which is the whole of what a
        design-doc dispatch does to store what it wrote — and, here, what normalises
        whatever an author typed into the one shape the store answers with.
        """
        documents = self.drafts / "documents"
        documents.mkdir(parents=True, exist_ok=True)
        drafted = f"{native}-design"
        (documents / f"{drafted}.md").write_text(
            frontmatter(
                {"title": f"Design: {native}", "project": native, "labels": labels}, DESIGN
            ),
            encoding="utf-8",
        )
        copied = self.copy("document", f"{DRAFTING}:{drafted}")
        (one,) = copied["items"]
        return str(one["destination"])

    def copy(self, kind: str, qualified: str) -> dict[str, Any]:
        """One record carried from :data:`DRAFTING` into :data:`STORED` by the store itself."""
        return self.ask(kind, "copy", qualified, "--to", STORED)

    def ask(self, *arguments: str) -> dict[str, Any]:
        """One command of the pinned plan-store CLI over these journeys' own sources.

        The answer stays ``Any`` at this seam deliberately: it is another program's open
        JSON contract, and a model of it written here would be a second copy of a payload
        each caller reads two fields of. Each caller indexes what it needs instead, which
        is where a moved shape is caught.

        A partial answer is refused here rather than at each caller: a source that could
        not be read reports the reason in `errors` and the command answers `0` anyway, so
        a listing that quietly lost a record would otherwise read as a record that was
        never there. The copy verb reports no such field, and an answer without one is not
        narrowed into having one.
        """
        answered = subprocess.run(
            [str(ONETASKGRAPH_BIN), *arguments, "--json"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(60),
            check=False,
        )
        assert answered.returncode == 0, answered.stdout + answered.stderr
        payload: dict[str, Any] = json.loads(answered.stdout)
        assert not payload.get("errors"), payload["errors"]
        return payload

    def metadata(self, kind: str, qualified: str) -> dict[str, Any]:
        """What the store says one stored record's `metadata` map holds.

        Read back through the store rather than off the file, because that is the reader
        every other consumer of these records is: a write the store cannot parse would
        still look right in a diff.
        """
        (record,) = self.ask(kind, "show", qualified)["items"]
        held: dict[str, Any] = record["item"]["metadata"]
        return held

    def labels(self, qualified: str) -> list[Any]:
        """What the store says one stored document's labels are, in its own shape."""
        (record,) = self.ask("document", "show", qualified)["items"]
        held: list[Any] = record["item"]["labels"]
        return held


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Store]:
    """Two local Markdown sources configured for this process and every child it spawns.

    `ONETASKGRAPH_DEFAULT_SOURCES` is narrowed to the two, which keeps the live `plans`
    board and this repository's shared fixture root out of every read made here — and
    keeps every write these journeys make inside a root they own outright.
    """
    roots = {DRAFTING: tmp_path / "drafts", STORED: tmp_path / "stored"}
    for source, root in roots.items():
        # Created rather than left to the first write: a `local-md` source canonicalizes
        # its root when it is built, so an absent one is refused as a broken source.
        root.mkdir(parents=True)
        monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{source.upper()}__PLUGIN", "local-md")
        monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{source.upper()}__CONFIG__ROOT", str(root))
    monkeypatch.setenv("ONETASKGRAPH_DEFAULT_SOURCES", f"{DRAFTING},{STORED}")
    yield Store(roots[DRAFTING], roots[STORED])


def _environment(tmp_path: Path, *answers: object) -> dict[str, str]:
    """The environment one recipe runs in, with the paid provider scripted and guarded."""
    environment = dict(os.environ)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # And the identities that seam cannot reach; see `no_paid_provider`.
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment["FAKE_CODEX_ANSWERS"] = json.dumps([json.dumps(one) for one in answers])
    environment["FAKE_CODEX_ATTEMPT_LOG"] = str(tmp_path / "launches")
    # Keeps these journeys' harness history out of the host's.
    environment["XDG_STATE_HOME"] = str(short_state.state_home(tmp_path))
    return environment


def _just(
    *arguments: str, environment: dict[str, str], seconds: float = 240
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


@pytest.fixture(autouse=True)
def _requires_just() -> None:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")


def _without_the_review_entry(written: bytes) -> bytes:
    """``written`` with the one line a review record occupies in a rendered record taken out.

    A metadata write lands as exactly one `metadata` line, so one line is what may differ
    between the file before and after; exactly one has to be there, or the record was
    either not written or written as something other than one entry.
    """
    kept = [
        line
        for line in written.splitlines(keepends=True)
        if not line.startswith(f'  "{plan_review.RECORD_KEY}": '.encode())
    ]
    assert len(kept) == written.count(b"\n") - 1, written.decode("utf-8")
    return b"".join(kept)


@pytest.mark.xdist_group("plan-store-record-shapes")
def test_a_review_is_recorded_on_a_record_whose_metadata_holds_a_block_sequence(
    store: Store, tmp_path: Path
) -> None:
    """`just review-plan` clears a stepped node the store rendered, and edits nothing else.

    The whole of the defect and the whole of the guarantee in one journey, because they
    are readings of one act: the record has to still parse, the sequence entry has to
    still be there whole, every other entry has to still be there, and the review record
    has to be readable by the gate that demanded it.
    """
    project = store.plan("stepped-review")

    # The shape this is about, taken from the record the store wrote rather than assumed:
    # the sequence's items stand at their own key's indent, level with the entries around
    # them, which is what no rule keyed on indentation alone could place.
    _, _, native = project.partition(":")
    record = plan_store.task_document(STORED, f"{native}/landing")
    body = record.read_text(encoding="utf-8")
    assert "\n  onepipeline.steps:\n  - id: build\n" in body, body
    plan_record = plan_store.source_root(STORED) / "projects" / f"{native}.md"
    untouched = {path: path.read_bytes() for path in (record, plan_record)}

    before = store.metadata("task", f"{STORED}:{native}/landing")
    assert [step["id"] for step in before["onepipeline.steps"]] == ["build", "prove"]
    assert plan_review.RECORD_KEY not in before

    environment = _environment(tmp_path, PASSES)
    reviewed = _just("review-plan", project, environment=environment)
    assert reviewed.returncode == 0, reviewed.stdout + reviewed.stderr

    # Byte for byte, each record the review wrote is the file the store rendered plus
    # its one review entry: the task's and the plan's, the two records the verbs write.
    for path, held in untouched.items():
        assert _without_the_review_entry(path.read_bytes()) == held, path

    after = store.metadata("task", f"{STORED}:{native}/landing")
    # The sequence entry survives whole — both items, in order, with the block-scalar
    # prose each of them states. An edit that dropped a step would leave a record that
    # still parsed and still carried a pass.
    assert [step["id"] for step in after["onepipeline.steps"]] == ["build", "prove"]
    assert after["onepipeline.steps"] == before["onepipeline.steps"]
    # And so does every other entry of the block, which is the other half of "narrow".
    assert {key: after[key] for key in before} == before
    # The record itself is the one the gate reads, so it is read through the gate.
    assert plan_review.RECORD_KEY in after
    checked = _just("check-plan", project, environment=environment)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "every task carries a review record" in checked.stdout, checked.stdout


@pytest.mark.xdist_group("plan-store-record-shapes")
@pytest.mark.parametrize(
    ("authored", "expected"),
    [
        pytest.param(["design", "second"], ["design", "second"], id="bare-names"),
        pytest.param(
            [{"id": "design", "name": "design", "color": "ff0000"}], ["design"], id="mappings"
        ),
    ],
)
def test_a_design_document_carrying_labels_is_approved_and_keeps_them(
    store: Store, tmp_path: Path, authored: list[object], expected: list[str]
) -> None:
    """`just approve-design` reads a labelled document back, records on it, and keeps them.

    Both label shapes an author may write, because the store normalises them to one on the
    way out and the reader has to meet that one however the record was typed — and because
    a reader that took only the canonical mapping would be the same mistake pointing the
    other way.

    The recipe is the whole round trip: it reads the document through the reader that
    refused, writes the approval through the writer that renders the labels back, and the
    second run reads its own write. So an approval that stuck and labels that came back
    are the reader, the writer and the round trip together.
    """
    native = "labelled-design"
    project = store.plan(native)
    document = store.document(native, authored)

    # The shape this is about, read out of the store rather than assumed: whatever was
    # authored, the store answers with its one canonical `{id, name, color}` label.
    stored = store.labels(document)
    assert [label["name"] for label in stored] == expected, stored

    environment = _environment(tmp_path)
    approved = _just("approve-design", project, environment=environment)
    assert approved.returncode == 0, approved.stdout + approved.stderr
    assert "recorded the approval" in approved.stdout, approved.stdout

    # The record the write left behind is read back through the store, which is what says
    # the labels were written in a shape the store still parses rather than merely a shape
    # this repository would accept from itself.
    (record,) = store.ask("document", "show", document)["items"]
    assert [label["name"] for label in record["item"]["labels"]] == expected
    assert design_approval.RECORD_KEY in record["item"]["metadata"]

    # And the reader this repository reads that record with answers the same names, which
    # closes the round trip: what it read, it wrote, and it reads its own write back.
    (read,) = [
        one for one in plan_store.read_documents(project) if str(one.qualified_id) == document
    ]
    assert read.labels == expected

    # Repeating the recipe reads the record its own write left behind, so it is where a
    # written shape the reader cannot take would surface — as a read refusal rather than
    # as the no-op an already-approved document gets.
    again = _just("approve-design", project, environment=environment)
    assert again.returncode == 0, again.stdout + again.stderr
    assert "already carries an approval" in again.stdout, again.stdout
