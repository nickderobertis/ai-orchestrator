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

import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from project_fixtures import helper
from waits import timeout as e2e_timeout

from orchestrator.design_approval import PLAN_KIND, PLANNING
from orchestrator.project_store import frontmatter, write_plan_project
from orchestrator.root import REPO_ROOT

#: This suite is its own Nx project, `plan-tooling`; see
#: `tests/plan_tooling/project.json` and the guard in `tests/conftest.py`.

#: The source this journey drafts, approves and launches in — its own, for the reason the
#: module docstring gives. A plain lowercase name because it is spelled into the store's
#: own `ONETASKGRAPH_SOURCES__…` environment layer as well as onto a command line.
SOURCE = "drafting"

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

    def __init__(self, root: Path) -> None:
        self.root = root

    def plan(self, native: str, *, planning: bool = False) -> str:
        """Write one launchable single-node plan and answer its qualified id.

        The node states `expects_no_diff`, which the engine settles with no dispatch, so
        every launch below is a whole real run that reaches no provider. ``planning``
        stamps the project the way `scripts/plan.sh` stamps the one a planning launch
        writes — through the same call, so a marker renamed in one place cannot be right
        in the other.
        """
        write_plan_project(
            self.root,
            {
                "schema_version": 3,
                "goal": {"text": "Settle one node that changes nothing"},
                "name": native,
                "tasks": [
                    {
                        "id": "handoff",
                        "title": "handoff",
                        "expects_no_diff": True,
                        "task": (
                            "## What\n\nRecord that nothing changes.\n\n"
                            "## Why\n\nThe boundary is explicit.\n\n"
                            "## Acceptance criteria\n\n- Nothing changed.\n"
                        ),
                    }
                ],
            },
            native_id=native,
            project_metadata={PLAN_KIND: PLANNING} if planning else None,
        )
        return f"{SOURCE}:{native}"

    def document(self, native: str, content: str = DESIGN, *, named: str = "design") -> Path:
        """Write one document of ``native``'s plan, as its own record.

        ``named`` is what distinguishes a second document *of the same project* from a
        document of another one — which is the state the ambiguity refusal is about, and
        which a second plan's document would not reach.
        """
        documents = self.root / "documents"
        documents.mkdir(parents=True, exist_ok=True)
        record = documents / f"{native}-{named}.md"
        record.write_text(
            frontmatter({"title": f"Design: {native} ({named})", "project": native}, content),
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
    yield Store(root)


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
    store.document(native, DESIGN.replace("One node that changes nothing", "Something else"))
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
    """
    # llmlint: ignore[tests_mirror_real_usage] see the paragraph above this line
    project = store.plan("approve-design-planning", planning=True)
    launched = _launch(project, runs)
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
