"""A plan whose criteria nothing has reviewed does not reach a dispatch.

Two plans shipped here whose criteria nobody read. Both were single-node plans an
operator wrote and launched with no planner, so `personas/planner.yaml`'s judge — the
thing that exists to catch exactly this — never saw them, and both produced finished,
gate-green work that a judge then rejected for satisfying the repository instead of the
criterion.

These journeys drive the two commands that close that, for real, against real local
plan projects: `just check-plan`, which is deterministic and free and now refuses a
task carrying no review record for what it currently says, and `just review-plan`,
which spends the judged turn and records a pass. Everything below the recipe is real —
the real scripts, the real `orchestrator-review-plan`, the real `oneharness` CLI, the
real `oneharness.plan-review.toml` chain and its response schema, and the real local
Markdown store. `tests/e2e/fake_codex.py` stands in for the paid provider alone, at the
one seam a single-sided oneharness turn reaches it through.

Both directions of every property are driven, because only the pair means anything: a
record that is never invalidated would be a rubber stamp, and a check that never
accepted one would be an outage.

These journeys carry the tracked operational appendix — a plan node's task has to, or
`just check-plan` refuses it before it ever reaches the review gate — so they belong to
the tier keyed on this repository's prose.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import plan_root_variable
import pytest
from nx_workspace import answering_this_checkouts_origin, copy_working_tree
from project_fixtures import helper, local_project
from waits import timeout as e2e_timeout

from orchestrator import plan_review, plan_store
from orchestrator.criteria_guard import APPENDIX
from orchestrator.project_store import render_plan_project
from orchestrator.root import REPO_ROOT

#: This suite is its own Nx project, `plan-tooling`, rather than a marker tier of the
#: orchestrator project: every journey here spawns the installed `onepipeline`, the
#: `just` recipes, the registered check script and — through `just review-plan` — a real
#: `oneharness run`, which is a different cost from the Python suite beside it and is
#: answered by a different set of files. `tests/plan_tooling/project.json` names that
#: set as `planToolingWorkspace`, and `tests/conftest.py` holds these tests to it.

#: The paid provider's stand-in, and the guard covering the identities
#: `ONEHARNESS_BIN_*` cannot reach. `just review-plan` spawns the real `oneharness run`,
#: so the provider binary is the seam — exactly as it is for the change-request drafter.
FAKE_CODEX = helper("fake_codex.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")

#: Criteria that answer every demand the tracked appendix and the shipped `engineer`
#: bar make, so the only thing left for a journey here to measure is the review record.
STATES_ITS_BAR = (
    "- The route accepts a valid request and rejects an invalid one.\n"
    "- A request-level test drives the route end to end and covers both paths.\n"
    "- The dispatch closes with a completion report naming the evidence it verified."
)


def _task(criteria: str = STATES_ITS_BAR) -> str:
    return (
        "## What\n\nAdd the route and the test that drives it.\n\n"
        "## Why\n\nThe user cannot complete a purchase without it.\n\n"
        f"## Acceptance criteria\n\n{criteria}\n\n"
        f"{(REPO_ROOT / APPENDIX).read_text(encoding='utf-8').strip()}\n"
    )


def _node(node_id: str, criteria: str = STATES_ITS_BAR) -> dict[str, str]:
    return {
        "id": node_id,
        "persona": "engineer",
        "repo": "https://github.com/nickderobertis/some-service",
        "title": f"feat: add the {node_id}",
        "task": _task(criteria),
    }


def _project(name: str, criteria: str = STATES_ITS_BAR, *nodes: str) -> str:
    """An unreviewed plan project, single-node by default — the shape both errors had."""
    return local_project(
        json.dumps(
            {
                "schema_version": 3,
                "goal": {"text": "Deliver the checkout route"},
                "tasks": [_node("route", criteria), *(_node(one) for one in nodes)],
            }
        ),
        name,
    )


def _record_of(project: str, node_id: str) -> object:
    """One task's review record, or ``None`` when it carries none."""
    for task in plan_store.read_tasks(project):
        if task.node_id == node_id:
            return task.metadata.get(plan_review.RECORD_KEY)
    raise AssertionError(f"{project} has no task {node_id!r}")


def _plan_record_of(project: str) -> object:
    """The plan-level review record on ``project``'s own record, or ``None`` for none."""
    metadata = plan_store.project_record(project).get("metadata")
    return metadata.get(plan_review.RECORD_KEY) if isinstance(metadata, dict) else None


def _document(project: str, node_id: str | None = None) -> Path:
    """One task record on disk — the only one, or the one ``node_id`` names."""
    source, _ = plan_store.qualified(project)
    tasks = plan_store.read_tasks(project)
    (task,) = tasks if node_id is None else [one for one in tasks if one.node_id == node_id]
    _, _, native = task.qualified_id.partition(":")
    return plan_store.task_document(source, native)


def _reviewing(tmp_path: Path, *answers: object) -> dict[str, str]:
    """The environment one `just review-plan` runs in, with the paid provider scripted."""
    environment = dict(os.environ)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # And the identities that seam cannot reach; see `no_paid_provider`.
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment["FAKE_CODEX_ANSWERS"] = json.dumps([json.dumps(one) for one in answers])
    environment["FAKE_CODEX_ATTEMPT_LOG"] = str(tmp_path / "launches")
    # Keeps this journey's harness history out of the host's.
    environment["XDG_STATE_HOME"] = str(tmp_path / "state")
    return environment


def _launches(tmp_path: Path) -> int:
    """How many provider turns this journey's commands actually spent."""
    log = tmp_path / "launches"
    return len(log.read_text(encoding="utf-8").splitlines()) if log.is_file() else 0


def _just(
    *arguments: str,
    environment: dict[str, str] | None = None,
    seconds: float = 180,
    cwd: Path = REPO_ROOT,
):
    return subprocess.run(
        ["just", *arguments],
        cwd=cwd,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
    )


PASSES = {"passes": True, "findings": []}

#: A refusal carrying **two** findings, because one is the shape this contract was
#: widened away from: a reviewer that saw two defects and could report one sent its
#: author back for a second judged turn to be told the second. A fixture carrying one
#: finding would pass under either contract and prove nothing.
REFUSES = {
    "passes": False,
    "findings": [
        {
            "criterion": "the lockfile resolves the sibling to 1.2.3",
            "why": "it names a release number rather than the property that number stands for",
        },
        {
            "criterion": "the branch publishes",
            "why": "publication happens after the worker settles, so no dispatch reaches it",
        },
    ],
}


def test_an_unreviewed_plan_is_refused_and_a_reviewed_one_is_accepted(tmp_path: Path) -> None:
    """The whole seam, in the order an operator meets it, on one plan.

    One journey rather than four, because these are four readings of a single act: the
    refusal names what to run, running it records a pass, the pass is authoritative, and
    the acceptance spends no further provider turn. Split apart, each would pay for its
    own project and its own judged turn to re-reach the same state.
    """
    project = _project("review-gate")

    refused = _just("check-plan", project)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "no review record" in refused.stderr, refused.stderr
    assert "route" in refused.stderr, refused.stderr
    assert f"just review-plan {project}" in refused.stderr, refused.stderr
    # And what that command can do about it, because it cannot do it everywhere: a
    # record is an entry of the task's own Markdown document, so the board this
    # repository plans against carries none and `review-plan` there only refuses.
    assert "only for a plan held in a local Markdown store" in refused.stderr, refused.stderr
    assert _launches(tmp_path) == 0, "the deterministic check spent a provider turn"

    environment = _reviewing(tmp_path, PASSES)
    review = _just("review-plan", project, environment=environment)
    assert review.returncode == 0, review.stdout + review.stderr
    assert "recorded a review of 1 task(s)" in review.stdout, review.stdout
    assert "the plan as a whole was reviewed and recorded" in review.stdout, review.stdout
    assert _launches(tmp_path) == 2, "the review did not spend one turn per task plus one"

    # The records the recipe wrote, read back: each digest is the one this checkout's
    # own key function computes — over that task under the bar in force, and over the
    # plan whole under the plan bar — which is what says the recorded value is the
    # content's rather than an opaque token nothing checks.
    (recorded,) = plan_store.read_tasks(project)
    written = recorded.metadata[plan_review.RECORD_KEY]
    assert isinstance(written, dict), written
    assert written["by"] == plan_review.BY_REVIEW, written
    assert written["key"] == plan_review.review_key(recorded, plan_review.bar_fingerprint())
    whole = _plan_record_of(project)
    assert isinstance(whole, dict), whole
    assert whole["by"] == plan_review.BY_REVIEW, whole
    assert whole["key"] == plan_review.plan_key(
        plan_store.read_plan(project, [recorded]), plan_review.plan_bar_fingerprint()
    )

    accepted = _just("check-plan", project)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert "carries a review record" in accepted.stdout, accepted.stdout
    assert _launches(tmp_path) == 2, "a recorded pass was re-judged rather than replayed"

    again = _just("review-plan", project, environment=environment)
    assert again.returncode == 0, again.stdout + again.stderr
    assert "1 already carried one" in again.stdout, again.stdout
    assert "already carried a record for its current content" in again.stdout, again.stdout
    assert _launches(tmp_path) == 2, "a recorded pass was re-judged rather than replayed"


def test_a_refused_review_shows_every_finding_and_records_nothing(tmp_path: Path) -> None:
    """Only a pass is recorded — and everything the one verdict found is shown.

    Both halves through the real recipe, the real review module, the real `oneharness
    run` and the real verdict schema, with the paid provider the only thing stood in
    for: the reviewer answers one verdict carrying two findings, and both reach the
    operator on their own lines, so the author corrects both before paying for another
    turn.
    """
    project = _project("review-refused")

    review = _just("review-plan", project, environment=_reviewing(tmp_path, REFUSES))
    assert review.returncode == 1, review.stdout + review.stderr
    for finding in REFUSES["findings"]:
        line = f"route: {finding['criterion']} — {finding['why']}"
        assert line in review.stderr, review.stderr
    assert "2 criterion(s) across 1 task(s)" in review.stderr, review.stderr
    assert "nothing was recorded" in review.stderr, review.stderr
    assert _launches(tmp_path) == 1, (
        "two findings cost two turns, or a plan-level turn was spent beside a refused task"
    )
    assert _plan_record_of(project) is None, "a refused task's plan earned a plan-level record"

    still = _just("check-plan", project)
    assert still.returncode == 1, still.stdout + still.stderr
    assert "no review record" in still.stderr, still.stderr


#: The two answers the verdict schema exists to refuse, and the reason it is one schema
#: rather than two fields nobody compares: a finding **is** a refused criterion, so a
#: refusal naming none stops this content while saying nothing an author can correct,
#: and a pass carrying one would clear a task whose own reviewer refused criteria of it.
DISAGREES_WITH_ITSELF = (
    pytest.param({"passes": False, "findings": []}, id="refuses-nothing"),
    pytest.param(
        {"passes": True, "findings": [{"criterion": "the route works", "why": "it is vague"}]},
        id="passes-with-a-finding",
    ),
    pytest.param(
        {"passes": True, "findings": [], "reason": "and some commentary besides"},
        id="undeclared-field",
    ),
    pytest.param(
        {"passes": False, "findings": [{"criterion": "", "why": "it is vague"}]},
        id="finding-naming-nothing",
    ),
    pytest.param(
        {"passes": False, "findings": [{"criterion": "   ", "why": "it is vague"}]},
        id="finding-named-in-whitespace",
    ),
    pytest.param(
        {"passes": False, "findings": [{"criterion": "the route works", "why": " \t "}]},
        id="reason-given-in-whitespace",
    ),
    pytest.param(
        {
            "passes": False,
            "findings": [{"criterion": "the route works", "why": "vague", "severity": "high"}],
        },
        id="undeclared-field-inside-a-finding",
    ),
    pytest.param(
        {"passes": False, "findings": ["it is vague"]}, id="finding-that-is-not-an-object"
    ),
    pytest.param(
        {"passes": False, "findings": [{"criterion": "the route works"}]},
        id="finding-without-a-why",
    ),
    pytest.param(
        {"passes": False, "findings": [{"criterion": "the route works", "why": 7}]},
        id="finding-whose-why-is-not-a-string",
    ),
    pytest.param({"passes": False, "findings": "it is vague"}, id="findings-that-is-not-a-list"),
    pytest.param({"passes": "no", "findings": []}, id="outcome-that-is-not-a-boolean"),
    pytest.param({"findings": []}, id="no-outcome-at-all"),
)


@pytest.mark.parametrize("answer", DISAGREES_WITH_ITSELF)
def test_an_answer_the_schema_does_not_admit_records_nothing(
    tmp_path: Path, answer: dict[str, object]
) -> None:
    """Driven through the real oneharness, which is what enforces the schema.

    The failure direction is always "not reviewed", never "reviewed and passed": the
    turn is re-prompted, no candidate answers a verdict the schema admits, the command
    says so and exits non-zero, and the task stays refused for carrying no record.
    """
    project = _project("review-disagrees")

    review = _just("review-plan", project, environment=_reviewing(tmp_path, answer))
    assert review.returncode == 2, review.stdout + review.stderr
    assert "no candidate answered the review with a verdict" in review.stderr, review.stderr
    assert "nothing was recorded" in review.stderr, review.stderr

    still = _just("check-plan", project)
    assert still.returncode == 1, still.stdout + still.stderr
    assert "no review record" in still.stderr, still.stderr


def test_editing_the_authored_prose_invalidates_the_record(tmp_path: Path) -> None:
    """The key covers the body prose, so the tweaked-a-reviewed-plan case is caught too.

    This is also the second half of what covering exactly the authored content buys: a
    settlement write-back that overwrote authored prose invalidates the record rather
    than leaving a pass standing over content nobody reviewed.
    """
    project = _project("review-edited")
    assert _just("review-plan", project, environment=_reviewing(tmp_path, PASSES)).returncode == 0
    assert _just("check-plan", project).returncode == 0

    document = _document(project)
    document.write_text(
        document.read_text(encoding="utf-8").replace(
            "- The route accepts a valid request and rejects an invalid one.",
            "- The route accepts every request it is given.",
        ),
        encoding="utf-8",
    )

    refused = _just("check-plan", project)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "no review record" in refused.stderr, refused.stderr


def test_editing_the_persona_invalidates_the_record(tmp_path: Path) -> None:
    """The key covers the persona, because the persona decides the bar the node is judged
    against: criteria that prove a node under one role can be silent about what another
    demands, so a record granted under one persona says nothing about the node under the
    next. Driven the way an operator retargets a node — by editing the stored record.
    """
    project = _project("review-persona")
    assert _just("review-plan", project, environment=_reviewing(tmp_path, PASSES)).returncode == 0
    assert _just("check-plan", project).returncode == 0

    document = _document(project)
    written = document.read_text(encoding="utf-8")
    reviewed_as = '"onepipeline.persona": "engineer"'
    assert reviewed_as in written, written
    document.write_text(
        written.replace(reviewed_as, '"onepipeline.persona": "docs-writer"'), encoding="utf-8"
    )

    refused = _just("check-plan", project)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "no review record" in refused.stderr, refused.stderr
    assert "route" in refused.stderr, refused.stderr


#: The action a `kind: human` node carries. A human node names something an external
#: person performs, so it states that action and no acceptance criteria at all — which
#: is the one property the review bar otherwise demands of everything it reads.
HUMAN_ACTION = "Merge the change request once its required checks have gone green."


def _human_project(name: str) -> str:
    """One agent node beside one `kind: human` node — the shape a plan with a gate has."""
    return local_project(
        json.dumps(
            {
                "schema_version": 3,
                "goal": {"text": "Deliver the checkout route"},
                "tasks": [
                    _node("route"),
                    {"id": "merge", "kind": "human", "title": "chore: merge", "task": HUMAN_ACTION},
                ],
            }
        ),
        name,
    )


def test_a_human_node_is_reviewed_as_the_shape_it_is(tmp_path: Path) -> None:
    """A plan carrying a human action can be reviewed, and so can reach `check-plan` green.

    `check-plan` demands a record for **every** task while `review-plan` was shown only a
    task's prose, its title, its deps and its persona — never which shape of node it is.
    A human node states an action rather than criteria, so a reviewer reading it under a
    bar about acceptance criteria refuses it for the one property its shape forbids, and
    the plan is then refused for want of the record that refusal could not write. That
    made every plan carrying this repository's own documented human node shape
    unlaunchable through its own pre-launch check, whatever its author wrote.
    """
    project = _human_project("review-human")
    prompts = tmp_path / "prompts.jsonl"
    environment = _reviewing(tmp_path, PASSES)
    environment["FAKE_CODEX_PROMPT_LOG"] = str(prompts)

    review = _just("review-plan", project, environment=environment)
    assert review.returncode == 0, review.stdout + review.stderr
    assert "recorded a review of 2 task(s)" in review.stdout, review.stdout

    # The turn that read the human action was told which shape it was reading, and told
    # what to hold that shape to — both halves, because the field alone is a value the
    # bar says nothing about, and the paragraph alone is advice about a shape the
    # reviewer cannot see this task is.
    given = [
        json.loads(line)["prompt"] for line in prompts.read_text(encoding="utf-8").splitlines()
    ]
    # The plan-level turn is handed every node's task too, so the human action reaches
    # two prompts; the one under question is the task's own.
    (human,) = [
        one
        for one in given
        if HUMAN_ACTION in one and not one.startswith(plan_review.PLAN_REVIEW_PROMPT)
    ]
    assert '"kind": "human"' in human, human
    assert 'A task whose `kind` is "human"' in human, human

    accepted = _just("check-plan", project)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert "1 dispatched node(s)" in accepted.stdout, accepted.stdout


def test_turning_a_dispatched_node_into_a_human_one_invalidates_its_record(
    tmp_path: Path,
) -> None:
    """The key covers the kind, because the kind decides which question was asked of it.

    A pass granted over criteria a worker will be judged against says nothing about the
    same prose once nobody is dispatched from it — and the reverse is the reading that
    costs: an action a reviewer cleared as an external step would otherwise keep its
    record while becoming a task a judge holds to acceptance criteria it does not have.
    Driven the way an operator changes a node's shape, by editing the stored record. The
    edit replaces the persona rather than standing a `kind` beside it, because the engine's
    own loader refuses a node carrying both — "a human node has no dispatch, so no persona
    or turn budget" — and refuses a human node that still names a repository, so a plan
    holding those fields together is one no project can reach. Isolating `kind` from
    `persona` in the key is therefore `tests/test_plan_review.py`'s to do; what a real
    project can show is that the shape change invalidates the record at all.
    """
    project = _human_project("review-human-rekinded")
    assert _just("review-plan", project, environment=_reviewing(tmp_path, PASSES)).returncode == 0
    assert _just("check-plan", project).returncode == 0

    document = _document(project, "route")
    written = document.read_text(encoding="utf-8")
    reviewed_as = '  "onepipeline.persona": "engineer"'
    assert reviewed_as in written, written
    rekinded = "\n".join(
        line
        for line in written.replace(reviewed_as, '  "onepipeline.kind": "human"').splitlines()
        if not line.startswith("repositories:")
    )
    document.write_text(f"{rekinded}\n", encoding="utf-8")

    refused = _just("check-plan", project)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "no review record" in refused.stderr, refused.stderr
    assert "route" in refused.stderr, refused.stderr


def _dependent_project(name: str) -> str:
    """Three nodes, one of which names a real prerequisite among the other two."""
    return local_project(
        json.dumps(
            {
                "schema_version": 3,
                "goal": {"text": "Deliver the checkout route"},
                "tasks": [
                    _node("route"),
                    _node("docs"),
                    _node("gate") | {"deps": ["route"]},
                ],
            }
        ),
        name,
    )


def test_rewiring_a_dependency_invalidates_only_the_record_of_the_node_it_moved(
    tmp_path: Path,
) -> None:
    """The key covers the dependencies, and covers which ones rather than how many.

    `deps` names what a node may assume has already happened, so criteria reviewed
    against one prerequisite were never read against another. The rewire here keeps the
    count and changes the edge, which is what says the key is over the dependencies
    themselves; and the two nodes that did not move keep their records, which is what
    says the refusal is per task rather than per plan.
    """
    project = _dependent_project("review-deps")
    assert _just("review-plan", project, environment=_reviewing(tmp_path, PASSES)).returncode == 0
    assert _just("check-plan", project).returncode == 0
    standing = {node: _record_of(project, node) for node in ("route", "docs")}

    document = _document(project, "gate")
    written = document.read_text(encoding="utf-8")
    assert written.count('/route"]') == 1, written
    document.write_text(written.replace('/route"]', '/docs"]'), encoding="utf-8")

    refused = _just("check-plan", project)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "no review record" in refused.stderr, refused.stderr
    # One refusal, against the node that moved: the two whose edges did not move are
    # named nowhere, which is what says the record is per task rather than per plan.
    assert refused.stderr.count("no review record") == 1, refused.stderr
    assert "gate" in refused.stderr, refused.stderr
    assert {node: _record_of(project, node) for node in ("route", "docs")} == standing


#: The second step's own criteria, distinct from the first's so a journey editing one
#: step's prose is editing exactly that step. Both answer every demand the appendix and
#: the shipped bars make, so the only thing left for these journeys to measure is the
#: review record.
DOCUMENTS_ITS_BAR = (
    "- The route's behaviour is written up where its callers will find it.\n"
    "- A journey drives the documented example end to end.\n"
    "- The dispatch closes with a completion report naming the evidence it verified."
)


def _stepped_project(name: str) -> str:
    """A lifecycle plan project: one node whose prose and persona live in its steps.

    Two steps rather than one, because a stepped node's authored content is per step and
    a key covering only the first would pass every journey a single-step node can drive.
    """
    return local_project(
        json.dumps(
            {
                "schema_version": 3,
                "goal": {"text": "Deliver the checkout route"},
                "tasks": [
                    {
                        "id": "route",
                        "repo": "https://github.com/nickderobertis/some-service",
                        "title": "feat: add the checkout route",
                        "steps": [
                            {"id": "implement", "persona": "engineer", "task": _task()},
                            {
                                "id": "document",
                                "persona": "docs-writer",
                                "task": _task(DOCUMENTS_ITS_BAR),
                            },
                        ],
                    }
                ],
            }
        ),
        name,
    )


def test_editing_a_lifecycle_steps_criteria_invalidates_the_record(tmp_path: Path) -> None:
    """A lifecycle node states its criteria once per `steps` entry, and they are keyed.

    This was an acknowledged hole: a stepped node states its prose and its persona per
    step rather than in `task` and `persona`, so the whole of its authored content could
    live somewhere the key never looked — and a standing record then covered criteria
    nobody had read, which is the one thing this gate exists to prevent. The edit is
    made to the **second** step, so a key that reached only the first fails here.
    """
    project = _stepped_project("review-step-criteria")
    assert _just("review-plan", project, environment=_reviewing(tmp_path, PASSES)).returncode == 0
    assert _just("check-plan", project).returncode == 0

    document = _document(project)
    written = document.read_text(encoding="utf-8")
    reviewed_as = "- The route's behaviour is written up where its callers will find it."
    assert written.count(reviewed_as) == 1, written
    document.write_text(
        written.replace(reviewed_as, "- The route is documented to whatever depth suits."),
        encoding="utf-8",
    )

    refused = _just("check-plan", project)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "no review record" in refused.stderr, refused.stderr
    assert "route" in refused.stderr, refused.stderr


def test_editing_a_lifecycle_steps_persona_invalidates_the_record(tmp_path: Path) -> None:
    """The other half of a step's authored content: the bar that step is judged under.

    A step names the persona whose review bar its own criteria are read against, so
    criteria reviewed under one say nothing about the same step under another — and this
    lived in the same place the key never looked.
    """
    project = _stepped_project("review-step-persona")
    assert _just("review-plan", project, environment=_reviewing(tmp_path, PASSES)).returncode == 0
    assert _just("check-plan", project).returncode == 0

    document = _document(project)
    written = document.read_text(encoding="utf-8")
    reviewed_as = '"persona": "docs-writer"'
    assert written.count(reviewed_as) == 1, written
    document.write_text(written.replace(reviewed_as, '"persona": "reviewer"'), encoding="utf-8")

    refused = _just("check-plan", project)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "no review record" in refused.stderr, refused.stderr
    assert "route" in refused.stderr, refused.stderr


def test_declaring_that_a_node_expects_no_diff_invalidates_its_record(tmp_path: Path) -> None:
    """The key covers that field, because it decides which question the reviewer was asked.

    A node whose work is an external side effect changes no file, and the engine settles
    the empty branch that leaves `failed` as `empty-branch` unless the node declares
    this. So the judged turn asks whether criteria describing no-change work are declared
    that way — which makes a pass granted while the field was absent a pass over a
    different question from the one the node now poses. Driven the way an operator adds the
    field: by editing the stored record.

    The edit **replaces** the persona rather than standing the field beside it, because
    the engine's own loader refuses a node carrying both — "expects_no_diff settles
    without a dispatch, so it takes no persona or turn budget" — so a project holding the
    two together is one no launch and no check can read at all. Isolating this field from
    the persona in the key is therefore `tests/test_plan_review.py`'s to do; what a real
    project can show is that declaring it invalidates the record.
    """
    project = _project("review-no-diff")
    assert _just("review-plan", project, environment=_reviewing(tmp_path, PASSES)).returncode == 0
    assert _just("check-plan", project).returncode == 0

    document = _document(project)
    written = document.read_text(encoding="utf-8")
    reviewed_as = '  "onepipeline.persona": "engineer"'
    assert reviewed_as in written, written
    document.write_text(
        written.replace(reviewed_as, f'  "{plan_review.EXPECTS_NO_DIFF}": true'),
        encoding="utf-8",
    )

    refused = _just("check-plan", project)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "no review record" in refused.stderr, refused.stderr
    assert "route" in refused.stderr, refused.stderr


#: Two edits to one criterion's line: one that re-spaces it and one that rewords it. The
#: pair is what the key is held to — a record that survived the rewording would be the
#: whole of what this gate exists to prevent, and one that died on the re-spacing charges
#: a judged turn for a change no reviewer could have ruled on.
RESPACED = (
    "- The route accepts a valid request and rejects an invalid one.",
    "  - The route  accepts a valid request and rejects an invalid one.",
)
REWORDED = (
    "- The route accepts a valid request and rejects an invalid one.",
    "- The route accepts every request it is given.",
)


@pytest.mark.parametrize(
    ("edit", "holds"),
    (
        pytest.param(RESPACED, True, id="re-spaced-and-re-indented"),
        pytest.param(REWORDED, False, id="reworded"),
    ),
)
def test_the_record_is_keyed_on_what_a_criterion_demands_rather_than_on_its_bytes(
    tmp_path: Path, edit: tuple[str, str], holds: bool
) -> None:
    """Both directions through the real recipes, because only the pair means anything.

    The key is over what a task demands. Re-indenting a criterion and closing up the
    spaces inside it alter no demand a reviewer read, and charging a judged turn for one is
    this gate costing something for nothing — which the two tiers that used to refuse each
    other's wording made routine. A reworded criterion is a different demand and still
    invalidates the record, which is the half no saving may be bought at.
    """
    project = _project(f"review-bytes-{'held' if holds else 'moved'}")
    assert _just("review-plan", project, environment=_reviewing(tmp_path, PASSES)).returncode == 0
    assert _just("check-plan", project).returncode == 0

    document = _document(project)
    written = document.read_text(encoding="utf-8")
    before, after = edit
    assert written.count(before) == 1, written
    document.write_text(written.replace(before, after), encoding="utf-8")

    checked = _just("check-plan", project)
    assert checked.returncode == (0 if holds else 1), checked.stdout + checked.stderr
    if not holds:
        assert "no review record" in checked.stderr, checked.stderr


def test_retargeting_a_node_at_another_repository_invalidates_its_record(
    tmp_path: Path,
) -> None:
    """The key covers the repository, which **reverses** what this gate once decided.

    A node retargeted at another repository used to keep its record, because which
    repository the work landed in was nothing the review ruled on. It is now the first
    thing the pin-path question turns on — a node of this host's own repository is asked
    to name the pin it moves and one outside it is not — so a retargeted task is a
    different question, and both its own record and the plan's fall. Driven against a
    lifecycle node, the way an operator retargets one: by editing the stored record.
    """
    project = _stepped_project("review-retargeted")
    assert _just("review-plan", project, environment=_reviewing(tmp_path, PASSES)).returncode == 0
    assert _just("check-plan", project).returncode == 0

    document = _document(project)
    written = document.read_text(encoding="utf-8")
    assert "github.com/nickderobertis/some-service" in written, written
    document.write_text(
        written.replace(
            "github.com/nickderobertis/some-service",
            "github.com/nickderobertis/somewhere-else",
        ),
        encoding="utf-8",
    )

    refused = _just("check-plan", project)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "no review record" in refused.stderr, refused.stderr
    assert "route" in refused.stderr, refused.stderr
    assert "no plan-level review record" in refused.stderr, refused.stderr


def test_a_settled_node_keeps_the_review_of_its_own_content(tmp_path: Path) -> None:
    """A dispatch must not invalidate the review of the content it was dispatched from.

    The engine projects each settlement back onto the plan it was launched from, so a
    key over the whole record would go stale the first time a node ran — and this gate
    would then refuse every plan that had ever been launched.
    """
    project = _project("review-settled")
    assert _just("review-plan", project, environment=_reviewing(tmp_path, PASSES)).returncode == 0

    document = _document(project)
    document.write_text(
        document.read_text(encoding="utf-8").replace('status: "todo"', 'status: "done"'),
        encoding="utf-8",
    )

    accepted = _just("check-plan", project)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr


#: A sentence of `REVIEW_PROMPT` the prompt half moves, and what it rewords it to. The
#: pair is stated here rather than inside the edit so the journey can check the first
#: against the constant in force: a prompt reworded upstream would otherwise leave this
#: journey replacing nothing and passing because it changed the bar in no way at all.
PROMPT_SENTENCE = "Do not rewrite the task and do not judge it on style."
PROMPT_REWORDED = "Do not rewrite the task, and judge it on nothing but its criteria."


def _move_the_bars_files(checkout: Path) -> None:
    """Move the half of the bar that is files: `personas/planner.yaml`, one byte on."""
    bar = checkout / plan_review.BAR_FILES[0]
    bar.write_text(
        bar.read_text(encoding="utf-8") + "\n# A reviewer's bar, one byte further on.\n",
        encoding="utf-8",
    )


def _move_the_bars_prompt(checkout: Path) -> None:
    """Move the half of the bar that is the question: `REVIEW_PROMPT`, reworded.

    Edited in the copy's own source, because that is what the copy's `just check-plan`
    imports — the same thing that makes the files half answer from the copy's tree.
    """
    assert PROMPT_SENTENCE in plan_review.REVIEW_PROMPT, (
        f"{PROMPT_SENTENCE!r} is no longer a sentence of REVIEW_PROMPT, so this journey "
        "would move the prompt in no way at all; reword it to match the constant"
    )
    module = checkout / "orchestrator" / "plan_review.py"
    source = module.read_text(encoding="utf-8")
    module.write_text(source.replace(PROMPT_SENTENCE, PROMPT_REWORDED, 1), encoding="utf-8")


#: Both inputs `plan_review.bar_fingerprint` digests, and how each is moved in a copy of
#: this checkout. Both are driven because they reach the key by different routes — the
#: files are read from the checkout root, the prompt is a constant of the package — so a
#: regression dropping either from the digest leaves a stale pass standing while the
#: journey that moves only the other one goes on passing.
BAR_HALVES = (
    ("the-files-it-is", _move_the_bars_files),
    ("the-question-it-asks", _move_the_bars_prompt),
)


#: What a journey building a copy of this checkout reads: everything git tracks, since
#: that is what it copies and hands a real tool. So these three stay in the
#: whole-workspace tier rather than joining the narrow key this project's other journeys
#: are memoized on — a key that dropped a path one of them copies would replay a verdict
#: for a tree it never ran against.
COPIES_THE_TRACKED_TREE = pytest.mark.reads_docs


@COPIES_THE_TRACKED_TREE
@pytest.mark.parametrize(("half", "move"), BAR_HALVES, ids=[named for named, _ in BAR_HALVES])
def test_moving_the_review_bar_invalidates_every_record_granted_under_it(
    tmp_path: Path, half: str, move: Callable[[Path], None]
) -> None:
    """A record does not outlive the bar it was granted under, driven for real.

    The bar is the files it is — `personas/planner.yaml` and the verdict schema — and
    the question `REVIEW_PROMPT` asks above them, and moving any part of it has to
    invalidate every standing record: the same property `scripts/llmlint-fingerprint.sh`
    gives the judged lint tier, for the same reason a content-only key would leave a
    stale pass valid after the judge configuration moved. Both halves are driven rather
    than one, because they are digested by different routes — a journey moving only the
    files goes on passing while the prompt has silently fallen out of the key.

    Moved in a **copy** of this checkout rather than in this one, which concurrent test
    tiers are reading. Everything else is real, and deliberately so: the copy is a whole
    checkout with its own recipe, its own script and its own installed command, reading
    the same records through the same configured store — so what answers here is the
    command surface an operator uses, over state only that surface produced.
    """
    project = _project(f"review-bar-{half}")
    assert _just("review-plan", project, environment=_reviewing(tmp_path, PASSES)).returncode == 0
    accepted = _just("check-plan", project)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr

    moved = tmp_path / "checkout-with-a-moved-bar"
    moved.mkdir()
    copy_working_tree(moved)
    # The copy answers the same origin as this checkout, so what it refuses below is
    # the moved bar and not a host that reads as another repository.
    answering_this_checkouts_origin(moved)
    move(moved)

    refused = subprocess.run(
        ["just", "check-plan", project],
        cwd=moved,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        check=False,
    )
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "no review record" in refused.stderr, refused.stderr
    assert f"just review-plan {project}" in refused.stderr, refused.stderr

    # And this checkout, whose bar did not move, still accepts it — so what the copy
    # refused is the moved bar rather than anything about the record itself.
    still = _just("check-plan", project)
    assert still.returncode == 0, still.stdout + still.stderr


#: A criterion pinning an exact release, and the one pinning the property that number
#: stood in for. Both are here because the pair is the point: this host used to refuse the
#: first deterministically and prescribe the second, and the two tiers then refused each
#: other — one review's own remedy was to pin the immutable version, which the
#: deterministic rule refused outright.
VERSION_LITERAL = "- The lockfile resolves the sibling to 0.15.4."
THE_PROPERTY_IT_STOOD_FOR = (
    "- The lockfile resolves the sibling to the newest release its requirement admits."
)


@pytest.mark.parametrize(
    ("criterion", "name"),
    (
        pytest.param(VERSION_LITERAL, "literal", id="a-version-literal"),
        pytest.param(THE_PROPERTY_IT_STOOD_FOR, "property", id="the-property-it-stood-for"),
    ),
)
def test_whether_a_number_is_the_right_number_is_the_judged_turns_to_decide(
    tmp_path: Path, criterion: str, name: str
) -> None:
    """Both wordings reach the reviewer, and the reviewer is the only thing that reads them.

    The deterministic rule refused the first and was the only tier asking, which is how a
    review came to prescribe a remedy the check beside it refused. So what is driven here
    is the whole seam in the order an operator meets it: `just check-plan` refuses
    neither, the judged turn is *shown* the criterion and the question about perishable
    facts, and the recorded pass then clears the plan.

    The reviewer's own verdict is scripted, because what a real model answers about a
    number is not this repository's to assert — what is this repository's is that the
    question and the criterion both reach the turn that decides.
    """
    project = _project(
        f"review-number-{name}",
        f"{criterion}\n"
        "- A journey drives the resolution end to end.\n"
        "- Every claim the dispatch makes about the finished work is true of the tree as "
        "it finally stands.",
    )
    prompts = tmp_path / "prompts.jsonl"
    environment = _reviewing(tmp_path, PASSES)
    environment["FAKE_CODEX_PROMPT_LOG"] = str(prompts)

    review = _just("review-plan", project, environment=environment)
    assert review.returncode == 0, review.stdout + review.stderr
    assert _launches(tmp_path) == 2, "the judged turn was not spent on this criterion"

    turn, _whole = _prompts(prompts)
    assert criterion.removeprefix("- ") in turn, turn
    assert "This turn is the only thing that asks about a version literal" in turn, turn

    accepted = _just("check-plan", project)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr


def test_no_argument_of_either_command_writes_a_record_without_a_pass(tmp_path: Path) -> None:
    """There is no flag, and a rejected one is refused rather than quietly ignored.

    The escape hatch is the thing this gate most has to not have: it would be reached
    under exactly the time pressure that produced both unreviewed plans. So the two
    commands take a project and nothing else, and a plan stays refused whatever is
    passed beside it.
    """
    project = _project("review-no-escape")
    for attempt in (["--force"], ["--skip-review"], ["--no-verify"]):
        refused = _just("check-plan", project, *attempt)
        assert refused.returncode != 0, refused.stdout + refused.stderr
        review = _just("review-plan", project, *attempt, environment=_reviewing(tmp_path, PASSES))
        assert review.returncode != 0, review.stdout + review.stderr
    assert _just("check-plan", project).returncode == 1
    assert _launches(tmp_path) == 0, "a rejected invocation spent a provider turn"


def test_a_review_that_could_not_run_records_nothing_and_names_the_next_action(
    tmp_path: Path,
) -> None:
    """Both exit-2 paths: nothing to review, and a turn that answered no verdict.

    The failure direction of this whole command is one-way — "not reviewed", never
    "reviewed and passed" — so a review that could not run has to leave the plan
    refused, and say what to do about it rather than only what went wrong.
    """
    absent = _just("review-plan", "test-fixtures:no-such-plan", environment=_reviewing(tmp_path))
    assert absent.returncode == 2, absent.stdout + absent.stderr
    assert "just check-plan test-fixtures:no-such-plan" in absent.stderr, absent.stderr

    project = _project("review-unanswerable")
    # The provider answers, is billed, and never produces the object the schema
    # declares — which oneharness re-prompts and then reports as invalid.
    unanswered = _just(
        "review-plan", project, environment=_reviewing(tmp_path, "a sentence, not a verdict")
    )
    assert unanswered.returncode == 2, unanswered.stdout + unanswered.stderr
    assert "no candidate answered" in unanswered.stderr, unanswered.stderr
    assert "oneharness detect" in unanswered.stderr, unanswered.stderr
    assert _launches(tmp_path) > 0, "the unanswerable review never reached a provider"

    still = _just("check-plan", project)
    assert still.returncode == 1, still.stdout + still.stderr
    assert "no review record" in still.stderr, still.stderr


def test_a_review_that_stops_partway_keeps_the_passes_it_already_granted(
    tmp_path: Path,
) -> None:
    """A per-task review records per task, and the diagnostic says so rather than "nothing".

    Each pass is written as it is granted, so a plan whose second task cannot be
    reviewed keeps the record its first task earned. A message claiming nothing was
    recorded would send its reader looking for state that is there, and — worse —
    reading a plan as wholly unreviewed when half of it is.
    """
    project = _project("review-partial", STATES_ITS_BAR, "listing")
    stopped = _just(
        "review-plan",
        project,
        environment=_reviewing(tmp_path, PASSES, "a sentence, not a verdict"),
    )

    assert stopped.returncode == 2, stopped.stdout + stopped.stderr
    assert "1 task(s) were reviewed and recorded before that" in stopped.stderr, stopped.stderr
    assert "those records stand" in stopped.stderr, stopped.stderr

    # Which of the two was reviewed first is the store's ordering to decide, so the
    # claim is the one that matters: exactly one pass survived, and it is not the task
    # the diagnostic names as left unreviewed.
    records = {node: _record_of(project, node) for node in ("route", "listing")}
    granted = [node for node, record in records.items() if isinstance(record, dict)]
    assert len(granted) == 1, f"the granted pass was lost or over-granted: {records}"
    unreviewed = next(node for node in records if node not in granted)
    assert f"beginning at {unreviewed}" in stopped.stderr, stopped.stderr

    # And the plan is still refused, because one of its tasks carries no record.
    refused = _just("check-plan", project)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert unreviewed in refused.stderr, refused.stderr


def test_a_pass_that_cannot_be_recorded_leaves_the_plan_refused(tmp_path: Path) -> None:
    """A review the store will not accept is a review that did not happen.

    Granting the pass and writing it down are one step from an operator's side: this
    plan is not reviewed, the command says why, and running it again picks up where it
    stopped. What must never happen is the opposite — a pass reported and not written,
    or written and not reported.
    """
    project = _project("review-unwritable")
    directory = _document(project).parent
    before = directory.stat().st_mode
    directory.chmod(0o500)
    try:
        refused = _just("review-plan", project, environment=_reviewing(tmp_path, PASSES))
        assert refused.returncode == 2, refused.stdout + refused.stderr
        assert "0 task(s) were reviewed and recorded before that" in refused.stderr, refused.stderr
    finally:
        directory.chmod(before)

    still = _just("check-plan", project)
    assert still.returncode == 1, still.stdout + still.stderr
    assert "no review record" in still.stderr, still.stderr


def test_a_store_no_record_can_be_written_into_is_refused_before_a_turn_is_spent(
    tmp_path: Path,
) -> None:
    """`just review-plan` on the board refuses up front, and pays no provider for it.

    A record is an entry of a task's own Markdown document, so the `github-projects`
    board this repository plans against has nowhere to keep one. Reaching that at the
    write would mean paying for a verdict first and then reporting a plan no further run
    of the command can advance, which reads as a transient failure and is not one — so
    the refusal is measured together with the turn count that says nothing was spent.

    Driven against the real `plans` source of this checkout's own `onetaskgraph.yaml`,
    which is what makes it a statement about the store this repository actually plans
    against rather than about a fixture shaped like one. It needs no credential and
    touches no board: the refusal is decided by the plugin the configuration names.
    """
    refused = _just("review-plan", "plans:anything", environment=_reviewing(tmp_path))
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert "github-projects" in refused.stderr, refused.stderr
    assert "no run of this command can record one" in refused.stderr, refused.stderr
    assert _launches(tmp_path) == 0, "a provider turn was spent on a review nothing could record"


def test_the_plan_store_this_gate_writes_into_is_not_tracked() -> None:
    """No branch a dispatched worker produces can carry a review record into this checkout.

    This is what makes "a record is written only by this repository's own code" a
    property rather than a convention. A lifecycle dispatch works in a worktree of its
    own, and a planner works in the launching checkout under a note forbidding it to
    commit; the plan root a closeout writes into is gitignored in either, so a record
    written in it reaches no commit, no branch, and no publication — and there is
    no recipe, flag, or documented step by which a dispatched agent writes one either.
    """
    for source in plan_review.PLAN_SOURCES:
        root = plan_store.source_root(source)
        assert root.is_relative_to(REPO_ROOT), f"{source} is rooted outside this checkout"
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", str(root.relative_to(REPO_ROOT)) + "/"],
            cwd=REPO_ROOT,
            check=False,
            timeout=e2e_timeout(30),
        )
        assert ignored.returncode == 0, (
            f"{source} is rooted at a tracked path, so a dispatched worker's branch "
            f"could carry a review record back into this checkout"
        )


#: The stand-in for the paid model at the seam a dispatched two-party member reaches
#: it through, which is where a planning run's own worker turn arrives.
FAKE_BACKEND = helper("fake_backend.py")

#: Where `just plan` writes the brief's own project, and where the planner below
#: authors its plan. A record is written into it by this repository's code and by
#: nothing a dispatch can reach, which is the whole point; `_its_own_plan_store` below
#: says why the two closeout journeys give it a root of their own.
AUTHORING = "authoring"

#: Everything an enclosing dispatch would otherwise decide for this launch.
INHERITED = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "ONEPIPELINE_RUN_ID",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)

BRIEF = """## What
Decide whether the paginated listing's cursor is an opaque token or a node id.

Plan project: authoring:cursor-shape

## Why
The browser view cannot deep-link to a page until that is settled.

## Acceptance criteria
- The cursor's shape and its type are stated.
"""


def _authored_records(native: str) -> dict[str, str]:
    """The plan records a dispatched planner authors, as absolute paths and content."""
    root = plan_store.source_root(AUTHORING)
    rendered = render_plan_project(
        {
            "schema_version": 3,
            "name": native,
            "goal": {"text": "Deliver the checkout route"},
            "tasks": [
                {
                    "id": "route",
                    "persona": "engineer",
                    "repo": "https://github.com/nickderobertis/some-service",
                    "title": "feat: add the checkout route",
                    "task": _task(),
                }
            ],
        },
        native_id=native,
    )
    return {str(root / relative): content for relative, content in rendered.items()}


def _a_checkout_of_its_own(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A whole checkout for one planning launch, whose plan store nothing else writes to.

    A closeout records every plan project that *appeared* while its run was in flight,
    and cannot narrow that to its own run — `orchestrator/plan_review.py` says why: a
    planner's plan is its deliverable rather than its argument, so nothing tells this
    host which project a dispatched planner authored. In this repository's own suite
    that is the normal condition rather than a corner case. Nx runs `test`, `test-docs`,
    `test-recipes` and `test-checkouts` as separate processes at the same time, and
    journeys in several of them drive `just plan` against the one tracked `.plans/`
    root, so a peer's window routinely spans this journey's authoring.

    That is what failed a publication gate on 2026-08-29: the record the assertion below
    found carried `by: planning-closeout` at a timestamp inside this launch's own
    lifetime, and was written by *another* launch's closeout. An `xdist_group` cannot
    reach that, because the peer is in a different pytest process — which is why
    serializing these two journeys, which is what this replaced, looked like a fix and
    was not.

    So each of these journeys plans in a copy of this checkout, the way the moved-bar
    journey above already checks a plan in one. It is the copy rather than a redirected
    root because `scripts/plan.sh` writes the brief to `.plans` relative to the tree it
    runs in: only a tree of its own gives the script, the launch and the closeout one
    store that no peer shares. Everything stays real — the recipe, the script, the
    launch, the closeout and the local Markdown plugin — and `uv` is pointed at the
    environment this checkout already has, the wiring every journey copying this tree
    uses. `monkeypatch` points this process's own reads at that store, so
    `plan_store.source_root` here answers the root the launch writes to.
    """
    checkout = tmp_path / "planning-checkout"
    checkout.mkdir()
    copy_working_tree(checkout)
    # The closeout's records are keyed under a bar that names this host's own
    # repository, read off the checkout's `origin`; the copy answers this checkout's.
    answering_this_checkouts_origin(checkout)
    # `scripts/plan.sh` takes its interpreter from the tree it runs in and falls back to
    # whatever `python3` is on PATH, which need not be the pinned one.
    (checkout / ".venv").symlink_to(REPO_ROOT / ".venv", target_is_directory=True)
    # Each source's own configured root, under the copy: read from the store rather than
    # restated, so a root moved in `onetaskgraph.yaml` moves this with it.
    roots = {
        source: checkout / plan_store.source_root(source).relative_to(REPO_ROOT)
        for source in plan_review.PLAN_SOURCES
    }
    for root in roots.values():
        root.mkdir(exist_ok=True)
    # The name a planning launch itself exports this root under, read from the one
    # place that composes it rather than spelled again here: it points *this*
    # process's reads at the planning checkout's store, and the launch below resolves
    # the same directory from its own tree and leaves this value alone.
    monkeypatch.setenv(plan_root_variable.name(), str(roots[AUTHORING]))
    monkeypatch.setenv("UV_NO_SYNC", "1")
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(REPO_ROOT / ".venv"))
    return checkout


@COPIES_THE_TRACKED_TREE
def test_a_planning_run_that_settled_records_what_it_authored_and_nothing_else(
    tmp_path: Path, oneharness_bin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`just plan`'s closeout records the planner's own output without a second turn.

    A planning run's output is a plan, and that plan's judge is
    `personas/planner.yaml`'s — the same bar `just review-plan` spends a turn on. So a
    task the run authored is recorded at closeout rather than re-reviewed, and the
    brief the *operator* wrote is not: nothing reviewed that, and `just check-plan` is
    right to go on refusing it.
    """
    checkout = _a_checkout_of_its_own(tmp_path, monkeypatch)
    run = f"plan-closeout-e2e-{os.getpid()}"
    authored = f"{run}-authored"
    brief = tmp_path / "brief.md"
    brief.write_text(BRIEF, encoding="utf-8")
    instruction = tmp_path / "author-plan.json"
    instruction.write_text(json.dumps(_authored_records(authored)), encoding="utf-8")

    environment = _reviewing(tmp_path, PASSES)
    for name in INHERITED:
        environment.pop(name, None)
    environment["CLAUDE_CODE_SESSION_ID"] = "e2e-plan-review-closeout"
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    # The stand-in for the paid model performs the one action a real planner performs:
    # it writes its plan into the store. A stand-in that only reported would leave the
    # closeout an empty diff to measure. Everything above the provider stays real.
    # llmlint: ignore[tests_mirror_real_usage] see the note above this line
    environment["FAKE_BACKEND_AUTHOR_PLAN"] = str(instruction)

    # `--no-design-doc`, so this launch is the planner and its closeout and nothing after
    # them. The tail is about the plan the planner writes, and what it would do first is
    # review the project the *brief* names — which this stand-in does not author, because
    # what is under test here is which projects the closeout speaks for.
    planned = _just(
        "plan",
        str(brief),
        "--name",
        run,
        "--no-design-doc",
        environment=environment,
        seconds=600,
        cwd=checkout,
    )
    assert planned.returncode == 0, planned.stdout + planned.stderr
    assert not instruction.exists(), "the dispatched planner authored nothing"

    (task,) = plan_store.read_tasks(f"{AUTHORING}:{authored}")
    record = task.metadata.get(plan_review.RECORD_KEY)
    assert isinstance(record, dict), f"the closeout recorded nothing: {task.metadata}"
    assert record["by"] == plan_review.BY_PLANNING, record
    assert record["key"] == plan_review.review_key(task, plan_review.bar_fingerprint())
    # And the plan whole, on the project, by the same closeout: the planner's own judge
    # reviewed the plan whole, so no `just review-plan` turn is owed for it either.
    whole = _plan_record_of(f"{AUTHORING}:{authored}")
    assert isinstance(whole, dict), "the closeout recorded no plan-level pass"
    assert whole["by"] == plan_review.BY_PLANNING, whole
    assert whole["key"] == plan_review.plan_key(
        plan_store.read_plan(f"{AUTHORING}:{authored}", [task]),
        plan_review.plan_bar_fingerprint(),
    )
    assert _launches(tmp_path) == 0, "the closeout spent a provider turn"

    accepted = _just("check-plan", f"{AUTHORING}:{authored}", cwd=checkout)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr

    # Every node of the generated project, rather than the one it used to have: a
    # planning launch writes the planner node and a `design-doc` node beside it, and the
    # brief is the task of both — so a closeout that blessed either would be blessing
    # content no planner reviewed.
    for written in plan_store.read_tasks(f"{AUTHORING}:{run}"):
        assert plan_review.RECORD_KEY not in written.metadata, (
            f"the closeout blessed {written.node_id!r}, whose task is the manager-written "
            "brief the launch was made from, which no planner reviewed"
        )


@COPIES_THE_TRACKED_TREE
def test_a_planning_run_that_did_not_settle_records_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that failed leaves its plan unrecorded, however much of it was authored.

    The closeout is the planner's judge standing in for a review turn, so it may only
    speak for a run that actually finished. Here the planner authors its plan and then
    its own turn dies — the stand-in for the paid model is not reachable — which is a
    run whose plan nothing reviewed at all.
    """
    checkout = _a_checkout_of_its_own(tmp_path, monkeypatch)
    run = f"plan-unsettled-e2e-{os.getpid()}"
    authored = f"{run}-authored"
    brief = tmp_path / "brief.md"
    brief.write_text(BRIEF, encoding="utf-8")
    instruction = tmp_path / "author-plan.json"
    instruction.write_text(json.dumps(_authored_records(authored)), encoding="utf-8")

    environment = _reviewing(tmp_path, PASSES)
    for name in INHERITED:
        environment.pop(name, None)
    environment["CLAUDE_CODE_SESSION_ID"] = "e2e-plan-review-unsettled"
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    # The stand-in for the paid model performs the one action a real planner performs:
    # it writes its plan into the store. Without it this journey would have nothing to
    # prove was left unrecorded when the run failed.
    # llmlint: ignore[tests_mirror_real_usage] see the note above this line
    environment["FAKE_BACKEND_AUTHOR_PLAN"] = str(instruction)
    # Deliberately no REAL_ONEHARNESS_BIN: the stand-in authors the plan and then
    # cannot deliver the turn, so the node fails and the run does not settle.
    environment.pop("REAL_ONEHARNESS_BIN", None)

    # `--no-design-doc`, so this launch is the planner and its closeout and nothing after
    # them. The tail is about the plan the planner writes, and what it would do first is
    # review the project the *brief* names — which this stand-in does not author, because
    # what is under test here is which projects the closeout speaks for.
    planned = _just(
        "plan",
        str(brief),
        "--name",
        run,
        "--no-design-doc",
        environment=environment,
        seconds=600,
        cwd=checkout,
    )
    assert planned.returncode != 0, planned.stdout + planned.stderr
    assert not instruction.exists(), "the dispatched planner authored nothing"

    (task,) = plan_store.read_tasks(f"{AUTHORING}:{authored}")
    assert plan_review.RECORD_KEY not in task.metadata, (
        "the closeout spoke for a planning run that never settled"
    )
    assert _plan_record_of(f"{AUTHORING}:{authored}") is None, (
        "the closeout recorded a plan-level pass for a planning run that never settled"
    )
    refused = _just("check-plan", f"{AUTHORING}:{authored}", cwd=checkout)
    assert refused.returncode == 1, refused.stdout + refused.stderr


#: A node whose whole job is adopting a named release. The number is not standing in
#: for a property here — it *is* the property, and asking for "the property that version
#: stands in for" leaves criteria that cannot say which release was adopted.
ADOPTS_A_RELEASE = (
    "- The engine pin names the release whose lockfile resolves the linked fix, and the "
    "installed binary reports that same release.\n"
    "- A journey reads the pin and the installed binary and holds them together.\n"
    "- The dispatch closes with a completion report naming its evidence."
)

#: A node pinned to a commit sha. A sha never moves, so it is the opposite of a
#: perishable fact — it is the anchor a measurement is worth anything against.
PINS_A_COMMIT = (
    "- The measurement names the commit `ed68466c` it was taken over, so a later reader "
    "can retake it against the same tree.\n"
    "- A journey drives the measurement end to end.\n"
    "- The dispatch closes with a completion report naming its evidence."
)

#: A criterion no repository state could falsify. It reads as satisfied whatever the
#: dispatch does, so it can never be what stops bad work — and it occupies the place a
#: criterion that would have.
DECORATIVE = (
    "- The implementation is of good quality and fits the codebase.\n"
    "- A journey drives the change end to end.\n"
    "- The dispatch closes with a completion report naming its evidence."
)

#: What a judge refusing on the falsifiability question answers.
REFUSES_AS_DECORATIVE = {
    "passes": False,
    "findings": [
        {
            "criterion": "the implementation is of good quality and fits the codebase",
            "why": "it names no repository state that could make it fail",
        }
    ],
}


def _prompts(path: Path) -> list[str]:
    """Every prompt the scripted provider was given, whitespace-normalized, in order.

    Normalized because a prompt is prose wrapped at whatever column its source is
    written to: an assertion on a phrase that happens to straddle a line break fails
    for the wrapping rather than for the words, and the wrapping is not the contract.
    """
    return [
        " ".join(json.loads(line)["prompt"].split())
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


def _reviewed_prompt(tmp_path: Path, project: str, *answers: object) -> tuple[str, object]:
    """The prompt one real `just review-plan` delivered, beside what the recipe did.

    The prompt is read out of the provider's own log rather than composed here, so what
    is asserted is the instruction the reviewer was handed through the real recipe, the
    real script, the real `oneharness run` and its response schema — not a string this
    test rebuilt. Only the paid model is scripted, which is the one thing a journey
    cannot drive: what a model decides is neither deterministic nor free, so what these
    journeys own is the bar it is given and what this repository does with its answer.
    """
    log = tmp_path / "prompts.jsonl"
    environment = _reviewing(tmp_path, *answers)
    environment["FAKE_CODEX_PROMPT_LOG"] = str(log)
    reviewed = _just("review-plan", project, environment=environment)
    # The task's own prompt is the first turn; a plan-level one follows it only once
    # every task has passed, and `_plan_prompt_given` is how a journey reads that one.
    prompt, *_ = _prompts(log)
    return prompt, reviewed


def _plan_prompt_given(tmp_path: Path) -> str:
    """The one plan-level prompt a `_reviewed_prompt` run delivered, or fail naming why."""
    whole = [one for one in _prompts(tmp_path / "prompts.jsonl") if _flat_plan_prompt() in one]
    assert len(whole) == 1, f"expected exactly one plan-level turn, found {len(whole)}"
    return whole[0]


def _flat_plan_prompt() -> str:
    return " ".join(plan_review.PLAN_REVIEW_PROMPT.split())


def test_the_reviewer_is_told_a_release_that_is_the_tasks_subject_is_not_perishable(
    tmp_path: Path,
) -> None:
    """The exemption, in the reviewer's own instructions, over the task it is for.

    Getting one two-node plan past this reviewer took fourteen rounds, all on one
    criterion: the newest release was refused as perishable, a floor as too weak, a
    concrete floor as perishable again, and an immutable commit sha passed at round
    eleven and was refused at round fourteen. Every accommodation removed an anchor, so
    the criteria ended vaguer than the planner wrote them — in order to satisfy a check
    whose whole purpose is precision.
    """
    prompt, reviewed = _reviewed_prompt(
        tmp_path, _project("review-adoption", ADOPTS_A_RELEASE), PASSES
    )

    assert reviewed.returncode == 0, reviewed.stdout + reviewed.stderr
    assert "release or version that is the subject of the task" in prompt, prompt
    assert "is the property itself rather than a stand-in for one" in prompt, prompt
    assert "there is nothing behind it to ask for instead" in prompt, prompt
    # And it is that task the exemption was delivered over, rather than some other one.
    assert "The engine pin names the release" in prompt, prompt


def test_the_reviewer_is_told_an_immutable_anchor_is_not_a_perishable_fact(
    tmp_path: Path,
) -> None:
    """The other half: a commit sha is a fixed point, so pinning to one is not the error."""
    prompt, reviewed = _reviewed_prompt(tmp_path, _project("review-anchor", PINS_A_COMMIT), PASSES)

    assert reviewed.returncode == 0, reviewed.stdout + reviewed.stderr
    assert "immutable anchor" in prompt, prompt
    assert "a commit sha, a tag, a release already published" in prompt, prompt
    assert "is exactly what a criterion should pin to" in prompt, prompt
    assert "ed68466c" in prompt, prompt


def test_the_reviewer_is_asked_what_state_would_falsify_each_criterion(
    tmp_path: Path,
) -> None:
    """The question the manager loop makes and this bar did not, and what a refusal does.

    A criterion nothing could falsify is decorative: it reads as satisfied whatever the
    dispatch does, so it cannot be what stops bad work — and it is the shape that let a
    criterion naming a boolean literal the shipped code negated go through. Driven with
    the provider refusing on that ground, because what this repository owns is the
    question asked and what is done with the answer: the reason reaches the operator,
    and nothing is recorded.
    """
    project = _project("review-decorative", DECORATIVE)

    prompt, refused = _reviewed_prompt(tmp_path, project, REFUSES_AS_DECORATIVE)

    assert "name to yourself the fixture, input, or repository state" in prompt, prompt
    assert "decorative" in prompt, prompt
    assert "Refuse a criterion you cannot falsify that way" in prompt, prompt
    assert "it can never be what stops bad work" in prompt, prompt
    assert refused.returncode == 1, refused.stdout + refused.stderr
    (finding,) = REFUSES_AS_DECORATIVE["findings"]
    assert f"{finding['criterion']} — {finding['why']}" in refused.stderr, refused.stderr
    assert _record_of(project, "route") is None, "a refusal recorded a pass"


#: The adopting node of this host's own repository, in the shape the release-adoption
#: bar asks for: it waits `published` on the producer, names no `consumes` — the host
#: override names the wheel — and its criteria name the `config/<pin>.version` the
#: installed engine wheel governs, with no version of its own.
ADOPTS_THE_ENGINE = (
    "- `config/onepipeline.version` names the engine release whose lockfile resolves the "
    "linked fix, and the installed `onepipeline` reports that same release.\n"
    "- A journey reads the pin and the installed binary and holds them together.\n"
    "- The dispatch closes with a completion report naming its evidence."
)

#: The same node with the pin's path taken out and nothing else changed: the criteria
#: still read as adopting the release, and nothing deterministic refuses them.
ADOPTS_NAMING_NO_PIN = (
    "- The installed `onepipeline` reports the engine release whose lockfile resolves the "
    "linked fix.\n"
    "- A journey reads the installed binary and holds it to that release.\n"
    "- The dispatch closes with a completion report naming its evidence."
)

#: The same node pinning the release it waits for by number — the second answer the
#: worker would follow over the engine's rendered references.
PINS_THE_AWAITED_RELEASE = (
    "- `config/onepipeline.version` names 0.29.0, the engine release carrying the fix.\n"
    "- A journey reads the pin and the installed binary and holds them together.\n"
    "- The dispatch closes with a completion report naming its evidence."
)

#: The producer this host installs a wheel from, and this repository, as the origins
#: their task records name.
ENGINE = "https://github.com/nickderobertis/onepipeline"
THIS_REPOSITORY = "https://github.com/nickderobertis/ai-orchestrator"

#: What the goal needs: the producer's change in force on this host.
NEEDS_THE_FIX_HERE = "Put the engine's session-open fix in force on this host"


def _adoption_project(name: str, criteria: str | None = ADOPTS_THE_ENGINE) -> str:
    """A producer node and, unless ``criteria`` is ``None``, a node of this repository
    adopting its release: `adoption: published`, no `consumes`, depending on it.

    The adopting node's id sorts before the producer's, which is the order the store
    lists them and so the order the review spends its turns in; the journeys that
    script a per-task refusal for it rely on that and assert which node the refusal
    landed on.
    """
    adopter = {
        "id": "adopt",
        "persona": "engineer",
        "repo": THIS_REPOSITORY,
        "title": "feat: adopt the engine release carrying the fix",
        "task": _task(criteria or ""),
        "deps": ["engine"],
        "adoption": "published",
    }
    return local_project(
        json.dumps(
            {
                "schema_version": 3,
                "goal": {"text": NEEDS_THE_FIX_HERE},
                "tasks": [
                    *([adopter] if criteria is not None else []),
                    {
                        "id": "engine",
                        "persona": "engineer",
                        "repo": ENGINE,
                        "title": "feat: open a session without racing the registry",
                        "task": _task(),
                    },
                ],
            }
        ),
        name,
    )


#: What a plan-level reviewer refusing the plan for the adoption it omits answers.
REFUSES_THE_PLAN = {
    "passes": False,
    "findings": [
        {
            "criterion": "the plan",
            "why": "no node of this repository adopts the engine release the fix lands in",
        }
    ],
}

#: What a per-task reviewer refusing the adopting node's criteria answers: one for the
#: criterion that should carry the pin's path, one for the one pinning a number.
REFUSES_NAMING_NO_PIN = {
    "passes": False,
    "findings": [
        {
            "criterion": "the installed onepipeline reports the engine release",
            "why": "it names no `config/<pin>.version`, so it adopts nothing a dispatch runs",
        }
    ],
}
REFUSES_THE_PINNED_NUMBER = {
    "passes": False,
    "findings": [
        {
            "criterion": "config/onepipeline.version names 0.29.0",
            "why": "the engine renders the released version into the task when the hold "
            "releases, so a number here is a second answer the worker follows",
        }
    ],
}


def test_a_plan_that_adopts_the_release_its_goal_needs_is_recorded_whole(
    tmp_path: Path,
) -> None:
    """One turn per task plus one for the plan, and the plan-level pass on the project.

    The whole seam over the plan this bar exists for: a producer's fix, and a node of
    this repository adopting the release that carries it. Both records are read back
    through the store, `just check-plan` then accepts the plan, and the plan-level
    prompt the provider was handed — read out of its own log — carries everything the
    reviewer needs to answer the two questions and the questions themselves.
    """
    project = _adoption_project("review-adopts")
    prompts = tmp_path / "prompts.jsonl"
    environment = _reviewing(tmp_path, PASSES)
    environment["FAKE_CODEX_PROMPT_LOG"] = str(prompts)

    review = _just("review-plan", project, environment=environment)
    assert review.returncode == 0, review.stdout + review.stderr
    assert "recorded a review of 2 task(s)" in review.stdout, review.stdout
    assert "the plan as a whole was reviewed and recorded" in review.stdout, review.stdout
    assert _launches(tmp_path) == 3, "the review did not spend one turn per task plus one"
    for node in ("adopt", "engine"):
        assert isinstance(_record_of(project, node), dict), node
    whole = _plan_record_of(project)
    assert isinstance(whole, dict), whole
    assert whole["by"] == plan_review.BY_REVIEW, whole
    plan, records = plan_store.read_project(project)
    assert whole["key"] == plan_review.plan_key(plan, plan_review.plan_bar_fingerprint())

    accepted = _just("check-plan", project)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert _launches(tmp_path) == 3, "the check spent a provider turn"

    # The plan-level prompt, as the provider was actually given it.
    turn = _plan_prompt_given(tmp_path)
    bar = " ".join((REPO_ROOT / plan_review.BAR_FILES[0]).read_text(encoding="utf-8").split())
    assert bar in turn, "the bar is missing from the plan-level prompt"
    assert NEEDS_THE_FIX_HERE in turn, turn
    for node in ("adopt", "engine"):
        assert f"### Node `{node}`" in turn, turn
    for shown in (
        '"title": "feat: adopt the engine release carrying the fix"',
        '"repo": "github.com/nickderobertis/ai-orchestrator"',
        '"repo": "github.com/nickderobertis/onepipeline"',
        '"deps": [ "engine" ]',
        '"adoption": "published"',
        '"consumes": null',
        "resolves the linked fix, and the installed `onepipeline` reports that same release",
        "Add the route and the test that drives it",
    ):
        assert shown in turn, shown
    assert " ".join(plan_review.RUNGS.split()) in turn, turn
    assert "`config/onepipeline.version` pins and which every dispatched node runs" in turn
    for question in (
        "does the goal need a change in one of the producers the table names",
        "is there a node of this host's own repository that adopts that producer's release",
        "touches a producer for a reason this host does not consume",
        "reaches a dispatch only through `config/onepipeline.version`, via an `onepipeline` "
        "node that links the crate",
    ):
        assert question in turn, question


def test_a_plan_omitting_the_adoption_its_goal_needs_is_refused_whole(tmp_path: Path) -> None:
    """The adopting node removed, a scripted plan-level refusal naming `the plan`.

    The task's own pass stands and the project carries no plan-level record; the reason
    reaches the operator; and `just check-plan` refuses the plan naming the project
    record and the command that records one.
    """
    project = _adoption_project("review-omits-adoption", criteria=None)

    review = _just(
        "review-plan", project, environment=_reviewing(tmp_path, PASSES, REFUSES_THE_PLAN)
    )
    assert review.returncode == 1, review.stdout + review.stderr
    (finding,) = REFUSES_THE_PLAN["findings"]
    assert f"review-plan: {finding['criterion']} — {finding['why']}" in review.stderr, review.stderr
    assert "no plan-level record was written" in review.stderr, review.stderr
    assert _launches(tmp_path) == 2, "one task and one plan should cost two turns"
    assert isinstance(_record_of(project, "engine"), dict), "the task's own pass was lost"
    assert _plan_record_of(project) is None, "a refusal recorded a plan-level pass"

    refused = _just("check-plan", project)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "no plan-level review record" in refused.stderr, refused.stderr
    assert "the project record" in refused.stderr, refused.stderr
    assert f"just review-plan {project}" in refused.stderr, refused.stderr
    assert "no review record" not in refused.stderr, "a recorded task was refused"


def test_the_task_reviewer_is_handed_every_fact_the_pin_path_question_turns_on(
    tmp_path: Path,
) -> None:
    """The per-task prompt for a `published` node, read back from the provider's log.

    Its header carries the repository, adoption, consumes and merge policy; this host's
    own repository is stated as the origin that header is compared with; the table
    names the pin each wheel governs; and both halves of the question are asked. And a
    scripted refusal on a criterion pinning the awaited release by number reaches the
    operator and records nothing — for the task or the plan.
    """
    project = _adoption_project("review-pinned-number", PINS_THE_AWAITED_RELEASE)
    prompt, refused = _reviewed_prompt(tmp_path, project, REFUSES_THE_PINNED_NUMBER)

    for shown in (
        '"repo": "github.com/nickderobertis/ai-orchestrator"',
        '"adoption": "published"',
        '"consumes": null',
        '"merge_policy": null',
        "## This host's own repository",
        f"`{plan_review.host_repository()}` — the origin the task's `repo` is compared with",
        "`config/onepipeline.version` pins and which every dispatched node runs",
        "**It names the pin it moves.**",
        "must name, in its `## Acceptance criteria`, the `config/<pin>.version` the table's "
        "row gives",
        "**It names no version of its own.**",
        "naming which version, commit or branch of the dependency to pin",
        "`config/onepipeline.version` names 0.29.0",
    ):
        assert shown in prompt, shown
    assert plan_review.host_repository() == "github.com/nickderobertis/ai-orchestrator"

    assert refused.returncode == 1, refused.stdout + refused.stderr
    (finding,) = REFUSES_THE_PINNED_NUMBER["findings"]
    assert f"{finding['criterion']} — {finding['why']}" in refused.stderr, refused.stderr
    assert _record_of(project, "adopt") is None, "a refusal recorded a pass"
    assert _plan_record_of(project) is None, "a plan-level turn was spent beside a refusal"


def test_an_adopting_node_naming_no_pin_is_the_judged_turns_to_refuse(tmp_path: Path) -> None:
    """The first fixture with the pin's path removed from the adopting node and nothing else.

    Nothing deterministic refuses that plan for the missing path: the question is the
    judged turn's, by meaning. So the scripted per-task refusal naming that node's
    criterion is what leaves it without a record, no plan-level turn is spent, and `just
    check-plan` refuses the plan naming the task. The prompt that turn was handed, read
    back from the provider's log, carried every fact the question turns on — the
    repository, the adoption, this host's own origin, the table row for the engine wheel
    — and the criteria without the path, so the judge decided with nothing missing.
    """
    project = _adoption_project("review-no-pin", ADOPTS_NAMING_NO_PIN)
    prompts = tmp_path / "prompts.jsonl"
    # `adopt` is reviewed first (the store lists by id), so the refusal is its answer and
    # the pass is `engine`'s; the records below say which node each landed on.
    environment = _reviewing(tmp_path, REFUSES_NAMING_NO_PIN, PASSES)
    environment["FAKE_CODEX_PROMPT_LOG"] = str(prompts)

    review = _just("review-plan", project, environment=environment)
    assert review.returncode == 1, review.stdout + review.stderr
    (finding,) = REFUSES_NAMING_NO_PIN["findings"]
    assert f"adopt: {finding['criterion']} — {finding['why']}" in review.stderr, review.stderr
    assert _launches(tmp_path) == 2, "a plan-level turn was spent beside a refused task"
    assert _record_of(project, "adopt") is None, "the refused node earned a record"
    assert isinstance(_record_of(project, "engine"), dict), "the passing node lost its record"
    assert _plan_record_of(project) is None

    refused = _just("check-plan", project)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "adopt" in refused.stderr, refused.stderr
    assert "no review record" in refused.stderr, refused.stderr
    assert f"just review-plan {project}" in refused.stderr, refused.stderr
    assert "config/onepipeline.version" not in refused.stderr, (
        "something deterministic refused the plan for the missing path"
    )

    (turn,) = [one for one in _prompts(prompts) if "holds it to that release" in one]
    for shown in (
        '"repo": "github.com/nickderobertis/ai-orchestrator"',
        '"adoption": "published"',
        "`github.com/nickderobertis/ai-orchestrator` — the origin the task's `repo`",
        "github.com/nickderobertis/onepipeline releases its `pypi` target as "
        "`pypi:onepipeline-cli`, which `config/onepipeline.version` pins",
        "The installed `onepipeline` reports the engine release whose lockfile resolves "
        "the linked fix",
    ):
        assert shown in turn, shown
    assert "config/onepipeline.version` names the engine release" not in turn, (
        "the adopting node's criteria still carried the path"
    )


def test_editing_the_goal_invalidates_the_plan_level_record_and_no_tasks(
    tmp_path: Path,
) -> None:
    """The plan key covers the goal; the task keys do not, so their records stand.

    And the other way about: editing an adopting node's `adoption` invalidates that
    task's record and the plan's, while a settlement write-back's `status` leaves both.
    """
    project = _adoption_project("review-goal-edited")
    assert _just("review-plan", project, environment=_reviewing(tmp_path, PASSES)).returncode == 0
    assert _just("check-plan", project).returncode == 0
    standing = {node: _record_of(project, node) for node in ("adopt", "engine")}

    source, native = plan_store.qualified(project)
    record = plan_store.project_document(source, native)
    written = record.read_text(encoding="utf-8")
    assert NEEDS_THE_FIX_HERE in written, written
    record.write_text(
        written.replace(NEEDS_THE_FIX_HERE, "Put a different fix in force"), encoding="utf-8"
    )

    refused = _just("check-plan", project)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "no plan-level review record" in refused.stderr, refused.stderr
    assert "no review record" not in refused.stderr, "a task's record fell with the goal"
    assert {node: _record_of(project, node) for node in ("adopt", "engine")} == standing

    # A settlement write-back moves neither record.
    record.write_text(written, encoding="utf-8")
    task = _document(project, "adopt")
    task.write_text(
        task.read_text(encoding="utf-8").replace('status: "todo"', 'status: "done"'),
        encoding="utf-8",
    )
    accepted = _just("check-plan", project)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr

    # The adopting node's `adoption` moves its own record and the plan's, and the
    # producer's record stands.
    reviewed_as = '"onepipeline.adoption": "published"'
    assert reviewed_as in task.read_text(encoding="utf-8")
    task.write_text(
        task.read_text(encoding="utf-8").replace(reviewed_as, '"onepipeline.adoption": "fast"'),
        encoding="utf-8",
    )
    refused = _just("check-plan", project)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert refused.stderr.count("no review record") == 1, refused.stderr
    assert "adopt" in refused.stderr, refused.stderr
    assert "no plan-level review record" in refused.stderr, refused.stderr
    assert _record_of(project, "engine") == standing["engine"]


def test_the_record_the_review_wrote_is_the_record_the_spawned_check_reads(
    tmp_path: Path,
) -> None:
    """One key over the store's plan and the document the engine hands the check.

    `just review-plan` keys what the store answers and writes the record; `onepipeline
    plan check` then hands `scripts/plan-check.sh` the loaded document, which is where
    `just check-plan` reads the record back. So the check is driven the way the verb
    drives it — on the document the verb really wrote, captured rather than rebuilt,
    with the project named the way the wrapper names it — and it refuses nothing, which
    is what the two shapes hashing alike looks like from outside.
    """
    project = _adoption_project("review-one-key")
    assert _just("review-plan", project, environment=_reviewing(tmp_path, PASSES)).returncode == 0
    recorder = tmp_path / "record-check.sh"
    captured = tmp_path / "captured.json"
    recorder.write_text(
        f'#!/usr/bin/env sh\ncat > "{captured}"\necho \'{{"refusals": []}}\'\n',
        encoding="utf-8",
    )
    recorder.chmod(0o755)
    read = subprocess.run(
        ["uv", "run", "onepipeline", "plan", "check", project, "--check", str(recorder)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert captured.is_file(), read.stdout + read.stderr

    answered = subprocess.run(
        [str(REPO_ROOT / "scripts" / "plan-check.sh")],
        cwd=REPO_ROOT,
        input=captured.read_text(encoding="utf-8"),
        env=os.environ | {"ORCHESTRATOR_PLAN_CHECK_PROJECT": project},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert answered.returncode == 0, answered.stdout + answered.stderr
    assert json.loads(answered.stdout)["refusals"] == [], answered.stdout
