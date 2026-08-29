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

import pytest
from nx_workspace import copy_working_tree
from project_fixtures import local_project
from waits import timeout as e2e_timeout

from orchestrator import plan_review, plan_store
from orchestrator.criteria_guard import APPENDIX
from orchestrator.project_store import render_plan_project
from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_docs

#: The paid provider's stand-in, and the guard covering the identities
#: `ONEHARNESS_BIN_*` cannot reach. `just review-plan` spawns the real `oneharness run`,
#: so the provider binary is the seam — exactly as it is for the change-request drafter.
FAKE_CODEX = Path(__file__).resolve().parent / "fake_codex.py"
PAID_PROVIDER_GUARD = Path(__file__).resolve().parent / "no-paid-provider"

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


PASSES = {"passes": True, "reason": "the criteria prove the route and are satisfiable here"}
REFUSES = {"passes": False, "reason": "criterion 2 names a release number rather than a property"}


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
    assert _launches(tmp_path) == 1, "the review did not spend exactly one turn"

    # The record the recipe wrote, read back: the digest is the one this checkout's own
    # key function computes over that task under the bar in force, which is what says
    # the recorded value is the content's rather than an opaque token nothing checks.
    (recorded,) = plan_store.read_tasks(project)
    written = recorded.metadata[plan_review.RECORD_KEY]
    assert isinstance(written, dict), written
    assert written["by"] == plan_review.BY_REVIEW, written
    assert written["key"] == plan_review.review_key(recorded, plan_review.bar_fingerprint())

    accepted = _just("check-plan", project)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert "carries a review record" in accepted.stdout, accepted.stdout
    assert _launches(tmp_path) == 1, "a recorded pass was re-judged rather than replayed"

    again = _just("review-plan", project, environment=environment)
    assert again.returncode == 0, again.stdout + again.stderr
    assert "1 already carried one" in again.stdout, again.stdout
    assert _launches(tmp_path) == 1, "a recorded pass was re-judged rather than replayed"


def test_a_refused_review_records_nothing_and_leaves_the_plan_refused(tmp_path: Path) -> None:
    """Only a pass is recorded, so no failed review can be replayed as one."""
    project = _project("review-refused")

    review = _just("review-plan", project, environment=_reviewing(tmp_path, REFUSES))
    assert review.returncode == 1, review.stdout + review.stderr
    assert "names a release number" in review.stderr, review.stderr
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
    (human,) = [one for one in given if HUMAN_ACTION in one]
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
    Driven the way an operator changes a node's shape, by editing the stored record.
    """
    project = _human_project("review-human-rekinded")
    assert _just("review-plan", project, environment=_reviewing(tmp_path, PASSES)).returncode == 0
    assert _just("check-plan", project).returncode == 0

    document = _document(project, "route")
    written = document.read_text(encoding="utf-8")
    reviewed_as = '  "onepipeline.persona": "engineer"'
    assert reviewed_as in written, written
    document.write_text(
        written.replace(reviewed_as, f'  "onepipeline.kind": "human"\n{reviewed_as}'),
        encoding="utf-8",
    )

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
    assert "1 task(s)" in refused.stderr, refused.stderr
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


def test_a_change_outside_the_authored_content_leaves_the_record_standing(
    tmp_path: Path,
) -> None:
    """What the key covers is the authored content, and a repository is not part of it.

    A node **retargeted at another repository** after its review keeps its record: which
    repository the work lands in is not something the review ruled on. Driven against a
    lifecycle node so that the pair with the two journeys above is exact — the same node
    shape, one change outside the key and two inside it.
    """
    project = _stepped_project("review-unkeyed")
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

    standing = _just("check-plan", project)
    assert standing.returncode == 0, standing.stdout + standing.stderr

    # And a keyed field still invalidates it, so this is the contract rather than a gate
    # that stopped noticing anything at all.
    document.write_text(
        document.read_text(encoding="utf-8").replace(
            'title: "feat: add the checkout route"', 'title: "feat: add a different route"'
        ),
        encoding="utf-8",
    )
    refused = _just("check-plan", project)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "no review record" in refused.stderr, refused.stderr


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


#: Every shape a criterion can name a release in, and the literal each one is refused
#: by. All of them are driven, because they are alternations of one pattern and a shape
#: only the unit tests reach is one a plan can carry into a dispatch. The last two are
#: the prerelease and build suffixes a pinned dependency actually wears, which is the
#: spelling an operator writing the perishable criterion is most likely to reach for.
VERSION_LITERALS = (
    ("- The lockfile resolves the sibling to 0.15.4.", "0.15.4"),
    ("- The pin reads v0.16.", "v0.16"),
    ("- The manifest requires >= 1.2.", ">= 1.2"),
    ("- The lockfile resolves the sibling to 1.2.3-rc.1.", "1.2.3-rc.1"),
    ("- The pin reads 2.0.0+build.5.", "2.0.0+build.5"),
)


@pytest.mark.parametrize(("criterion", "literal"), VERSION_LITERALS, ids=lambda one: str(one))
def test_a_criterion_carrying_a_version_literal_is_refused(
    tmp_path: Path, criterion: str, literal: str
) -> None:
    """The second error's own shape, refused deterministically before any turn is spent.

    That criterion required a lockfile to resolve a sibling to an exact version; the
    sibling published a newer one between the task being written and the node being
    dispatched. A judge reading the number would most likely have passed it — it was
    well-formed, just perishable — which is why this belongs to the deterministic tier.
    """
    refused = _just(
        "check-plan",
        _project(
            f"review-version-{literal}",
            f"{criterion}\n"
            "- A journey drives the resolution end to end.\n"
            "- The dispatch closes with a completion report naming its evidence.",
        ),
    )
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "version literal" in refused.stderr, refused.stderr
    assert literal in refused.stderr, refused.stderr
    assert _launches(tmp_path) == 0, "a deterministic refusal spent a provider turn"


def test_the_property_that_version_stood_in_for_is_accepted(tmp_path: Path) -> None:
    """The correction the refusal asks for, accepted through the same real recipe."""
    project = _project(
        "review-property",
        "- The lockfile resolves the sibling to the newest release its requirement admits.\n"
        "- A journey drives the resolution end to end.\n"
        "- The dispatch closes with a completion report naming its evidence.",
    )
    assert _just("review-plan", project, environment=_reviewing(tmp_path, PASSES)).returncode == 0

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
    property rather than a convention. A dispatch works in a worktree of its own; the
    plan root a closeout writes into is gitignored there exactly as it is here, so a
    record written in it reaches no commit, no branch, and no publication — and there is
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
FAKE_BACKEND = Path(__file__).resolve().parent / "fake_backend.py"

#: Where `just plan` writes the brief's own project, and where the planner below
#: authors its plan. A record is written into it by this repository's code and by
#: nothing a dispatch can reach, which is the whole point; `_its_own_plan_store` below
#: says why the two closeout journeys give it a root of their own.
AUTHORING = "authoring"

#: How `onetaskgraph` names one source's root at its environment layer. The dotted path
#: its configuration document and its `--set` flag use, `__` between segments and the
#: source upper-cased — read off the CLI's own `config show`, which reports the layer
#: and the variable each value came from. It points *this* process's reads at the
#: planning checkout's store; the launch resolves the same directory from its own tree.
AUTHORING_ROOT_VARIABLE = "ONETASKGRAPH_SOURCES__AUTHORING__CONFIG__ROOT"

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
    monkeypatch.setenv(AUTHORING_ROOT_VARIABLE, str(roots[AUTHORING]))
    monkeypatch.setenv("UV_NO_SYNC", "1")
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(REPO_ROOT / ".venv"))
    return checkout


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

    planned = _just(
        "plan",
        str(brief),
        "--name",
        run,
        "--direct",
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

    accepted = _just("check-plan", f"{AUTHORING}:{authored}", cwd=checkout)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr

    (written,) = plan_store.read_tasks(f"{AUTHORING}:{run}")
    assert plan_review.RECORD_KEY not in written.metadata, (
        "the closeout blessed the manager-written brief the launch was made from, "
        "which no planner reviewed"
    )


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

    planned = _just(
        "plan",
        str(brief),
        "--name",
        run,
        "--direct",
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
    refused = _just("check-plan", f"{AUTHORING}:{authored}", cwd=checkout)
    assert refused.returncode == 1, refused.stdout + refused.stderr
