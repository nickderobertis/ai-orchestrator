"""`just check-plan` refuses a plan a launch would spend dispatches failing.

This is the whole seam an operator touches, driven for real: the recipe, the console
script it delegates to, this checkout's own `config/onejudge.base.yaml`, the tracked
`config/dispatch-appendix.md`, and the real `onepipeline` binary whose linked
`oneagentgraph` ships the role a node's `persona` name resolves to. Nothing is
doubled — there is nothing here to double, because the guard spends no provider turn
and launches nothing.

Both directions are driven, because only the pair means anything. A plan whose nodes
state the bar they will be judged against passes and says how many it read; one whose
node omits a demand the appendix it carries makes is refused, with a non-zero exit
and the reason on stderr, before a single node is scheduled. The refused shape here
is the one that actually happened: criteria that were complete about the work and
silent about proving it end to end, under a role whose bar demands exactly that.

These journeys read the tracked appendix, so they belong to the tier keyed on this
repository's prose: editing that file changes what this recipe accepts.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
from waits import timeout as e2e_timeout

from orchestrator.criteria_guard import APPENDIX, OUT_OF_DISPATCH
from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_docs


def _task(criteria: str) -> str:
    """A node's task in the shape every plan here writes, carrying the real appendix."""
    return (
        "## What\n\nAdd the route and the test that drives it.\n\n"
        "## Why\n\nThe user cannot complete a purchase without it.\n\n"
        f"## Acceptance criteria\n\n{criteria}\n\n"
        f"{(REPO_ROOT / APPENDIX).read_text(encoding='utf-8').strip()}\n"
    )


#: Criteria that answer every demand the appendix and the built-in `engineer` bar
#: make, stated as properties of the finished tree rather than as commands.
STATES_ITS_BAR = (
    "- The route accepts a valid request and rejects an invalid one.\n"
    "- A request-level test drives the route end to end and covers both paths.\n"
    "- The dispatch closes with a completion report naming the evidence it verified."
)

#: The same node with the end-to-end criterion dropped. This is the shape that was
#: dispatched, finished, gate-green, and failed anyway — the bar demanded proof end to
#: end, the criteria never said so, and the judge supplied its own reading.
OMITS_A_DEMAND = (
    "- The route accepts a valid request and rejects an invalid one.\n"
    "- The dispatch closes with a completion report naming the evidence it verified."
)


def _plan(root: Path, criteria: str) -> Path:
    written = root / "plan.json"
    written.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "goal": {"text": "Deliver the checkout route"},
                "name": "check-plan-e2e",
                "tasks": [
                    {
                        "id": "route",
                        "persona": "engineer",
                        "repo": "https://github.com/nickderobertis/some-service",
                        "title": "feat: add the checkout route",
                        "task": _task(criteria),
                    },
                    {"id": "approve", "kind": "human", "task": "Approve the release."},
                ],
            }
        ),
        encoding="utf-8",
    )
    return written


def _check_plan(plan: Path) -> subprocess.CompletedProcess[str]:
    """Run the real recipe from this checkout."""
    return subprocess.run(
        ["just", "check-plan", str(plan)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )


def test_a_plan_whose_node_states_its_bar_is_accepted(tmp_path: Path) -> None:
    """The accepting half, and the count that says the human node was not checked.

    A `kind: human` node carries an action a person performs rather than a task a
    judge reads, so counting it would be counting a dispatch that never happens.
    """
    checked = _check_plan(_plan(tmp_path, STATES_ITS_BAR))

    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "1 dispatched node(s)" in checked.stdout, checked.stdout


def test_a_task_with_no_acceptance_criteria_at_all_is_refused(tmp_path: Path) -> None:
    """A node with no criteria has no bar of its own, so it is judged entirely on the
    role's — which is the whole failure this guard exists to catch, at its extreme."""
    plan = _plan(tmp_path, STATES_ITS_BAR)
    document = json.loads(plan.read_text(encoding="utf-8"))
    node = document["tasks"][0]
    node["task"] = node["task"].replace("## Acceptance criteria", "## Notes")
    plan.write_text(json.dumps(document), encoding="utf-8")

    refused = _check_plan(plan)

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "Acceptance criteria" in refused.stderr, refused.stderr


def test_a_plan_of_human_actions_alone_is_accepted_and_says_it_checked_nothing(
    tmp_path: Path,
) -> None:
    """A plan can be all coordination, and the count is what says so.

    Reporting it as checked without saying how many nodes were read would let a plan
    whose nodes were all skipped for the wrong reason look exactly like a clean one.
    """
    plan = tmp_path / "human.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "name": "check-plan-human-e2e",
                "tasks": [{"id": "approve", "kind": "human", "task": "Approve the release."}],
            }
        ),
        encoding="utf-8",
    )

    checked = _check_plan(plan)

    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "0 dispatched node(s)" in checked.stdout, checked.stdout


def test_a_plan_whose_node_omits_a_demand_it_will_be_held_to_is_refused(tmp_path: Path) -> None:
    """The refusal, at the seam and for the reason it exists.

    The message has to carry both halves for the plan's author to act on it: which
    demand went unanswered, and where it is made — here the `engineer` role compiled
    into the `oneagentgraph` `onepipeline` links, which is the bar in force and not
    anything in this repository's `personas/`.
    """
    refused = _check_plan(_plan(tmp_path, OMITS_A_DEMAND))

    assert refused.returncode == 1, refused.stdout + refused.stderr
    reported = refused.stderr
    assert "route:" in reported, reported
    assert "proof end to end" in reported, reported
    assert "engineer" in reported and "onepipeline" in reported, reported
    assert "State it as a criterion" in reported, reported


@pytest.mark.parametrize("phrase", OUT_OF_DISPATCH)
def test_a_criterion_resting_on_work_the_dispatch_cannot_do_is_refused(
    tmp_path: Path, phrase: str
) -> None:
    """Every phrase naming state that only exists after the worker settles.

    Parametrized over the guard's own list rather than over a copy of it, so a phrase
    added there is a case here — otherwise the one that goes unexercised is exactly
    the one nobody thought to write down twice. A node was failed against `The branch
    publishes.` with its branch finished and waiting: publication is the lifecycle's,
    and no worker can reach it from inside its own dispatch.
    """
    refused = _check_plan(_plan(tmp_path, f"{STATES_ITS_BAR}\n- {phrase.capitalize()}."))

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert phrase in refused.stderr, refused.stderr
    assert "the dispatch cannot do" in refused.stderr, refused.stderr


@pytest.mark.parametrize(
    ("criterion", "reason"),
    (
        pytest.param("- The route behaves as described above.", "reconstruct", id="deferred"),
        pytest.param(
            "- The response repeats the exact wording of the spec.",
            "particular string",
            id="wording",
        ),
        pytest.param("- `just gate` is green.", "`just` invocation", id="just"),
        pytest.param("- The suite passes && the gate is green.", "chained shell", id="chained"),
        pytest.param("- `pytest tests/` passes.", "shell invocation", id="shell"),
    ),
)
def test_a_criterion_that_names_a_procedure_rather_than_a_property_is_refused(
    tmp_path: Path, criterion: str, reason: str
) -> None:
    """The other class of refusal, at the seam an operator meets it.

    A judge reads a criterion literally, so it cannot accept an equivalent route for a
    command string and it reconstructs a deferred one as a wording demand. Each
    criterion is added beside a complete set, so what is refused is the criterion
    itself and not an omission.
    """
    refused = _check_plan(_plan(tmp_path, f"{STATES_ITS_BAR}\n{criterion}"))

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert reason in refused.stderr, refused.stderr


def test_a_lifecycle_node_is_read_through_its_steps_rather_than_as_one_node(
    tmp_path: Path,
) -> None:
    """The other plan shape an operator writes, and the count that proves it was walked.

    A lifecycle node runs several steps in sequence on one branch, so each step is a
    dispatch and the node above them is not. A reader that checked the node instead
    would find no `task` on it and refuse a plan that is fine.
    """
    plan = _plan(tmp_path, STATES_ITS_BAR)
    document = json.loads(plan.read_text(encoding="utf-8"))
    node = document["tasks"][0]
    document["tasks"][0] = {
        "id": "service",
        "repo": node["repo"],
        "title": node["title"],
        "steps": [
            {"id": "implement", "persona": "engineer", "task": node["task"]},
            {"id": "document", "persona": "docs-writer", "task": node["task"]},
        ],
    }
    plan.write_text(json.dumps(document), encoding="utf-8")

    checked = _check_plan(plan)

    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "2 dispatched node(s)" in checked.stdout, checked.stdout


def test_a_node_that_never_says_what_it_reports_is_refused(tmp_path: Path) -> None:
    """The second demand nobody wrote down, refused at the seam.

    A branch was failed for never having "provided a final verified completion
    report" — a demand in neither its task nor the shared clause. The bar and the
    appendix both ask a worker to report; a node whose criteria never say what it
    reports leaves the judge to decide what that meant.
    """
    silent = "\n".join(
        line for line in STATES_ITS_BAR.splitlines() if "completion report" not in line
    )

    refused = _check_plan(_plan(tmp_path, silent))

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "a completion report" in refused.stderr, refused.stderr


def test_a_node_naming_a_persona_no_dispatch_could_resolve_is_refused(tmp_path: Path) -> None:
    """`persona: orchestrator` is read as `graphs/orchestrator`, and settles `failed`.

    A dispatch discovers that in `oneagentgraph`'s config validation, after the node
    has been scheduled. The recipe discovers it for free, before the launch.
    """
    plan = _plan(tmp_path, STATES_ITS_BAR)
    document = json.loads(plan.read_text(encoding="utf-8"))
    document["tasks"][0]["persona"] = "orchestrator"
    plan.write_text(json.dumps(document), encoding="utf-8")

    refused = _check_plan(plan)

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "graphs/orchestrator" in refused.stderr, refused.stderr


def _with_node(plan: Path, **fields: object) -> Path:
    """The same plan with its dispatched node's fields replaced or removed."""
    document = json.loads(plan.read_text(encoding="utf-8"))
    for key, value in fields.items():
        if value is None:
            document["tasks"][0].pop(key, None)
        else:
            document["tasks"][0][key] = value
    plan.write_text(json.dumps(document), encoding="utf-8")
    return plan


@pytest.mark.parametrize(
    "persona",
    (
        pytest.param(None, id="no-persona"),
        pytest.param("../personas/orchestrator.yaml", id="by-path"),
    ),
)
def test_every_way_a_node_names_its_bar_resolves_through_the_recipe(
    tmp_path: Path, persona: str | None
) -> None:
    """The two resolutions besides a shipped name, driven where an operator uses them.

    A node with no `persona` is left with the base config's generic contract, and one
    naming a path relative to `graphs/` gets that file's — the only shape in
    `personas/` a dispatch reads. Neither resolution may make the guard fall over or
    stop refusing: a plan that omits a demand is still refused under both, and which
    source the demand came from is what the shipped-name journey above asserts.
    """
    accepted = _check_plan(_with_node(_plan(tmp_path, STATES_ITS_BAR), persona=persona))
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert "1 dispatched node(s)" in accepted.stdout, accepted.stdout

    refused = _check_plan(_with_node(_plan(tmp_path, OMITS_A_DEMAND), persona=persona))
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "proof end to end" in refused.stderr, refused.stderr


@pytest.mark.parametrize(
    ("fields", "reason"),
    (
        pytest.param({"persona": "../../elsewhere.yaml"}, "outside this checkout", id="escaped"),
        pytest.param({"persona": 7}, "`persona` is int", id="persona-not-a-name"),
        pytest.param({"id": []}, "states `id` as list", id="id-not-a-string"),
        pytest.param({"task": None}, "states no `task` string", id="no-task"),
        pytest.param({"kind": "review"}, "`kind` is 'review'", id="unknown-kind"),
        pytest.param({"steps": 3}, "`steps` is int", id="steps-not-a-list"),
        pytest.param({"steps": ["one"]}, "`steps[0]` is str", id="step-not-an-object"),
    ),
)
def test_a_node_whose_own_fields_are_wrong_is_refused_by_the_field(
    tmp_path: Path, fields: dict[str, object], reason: str
) -> None:
    """A plan is a document some other tool wrote, so its shape is untrusted here too.

    Each of these reaches the operator as a diagnostic naming the field, rather than
    as whatever exception reaching into the wrong shape happened to produce.
    """
    refused = _check_plan(_with_node(_plan(tmp_path, STATES_ITS_BAR), **fields))

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert reason in refused.stderr, refused.stderr


class Drafted(NamedTuple):
    """A persona file in this checkout, and the ref a plan node names it by."""

    file: Path
    #: Relative to `graphs/`, which is where `oneagentgraph` reads a ref from.
    ref: str


@pytest.fixture
def drafted_persona(request: pytest.FixtureRequest) -> Iterator[Drafted]:
    """A persona file inside this checkout, named the way a node would name one.

    A ref is read relative to `graphs/` in the launch directory and refused when it
    lands outside the checkout, so a journey about a persona *file* has to put one
    here. `scratch/personas/` is where `personas/README.md` says a draft persona
    lives while it is being proven, and it is gitignored, so the tree stays clean.
    """
    drafts = REPO_ROOT / "scratch" / "personas"
    drafts.mkdir(parents=True, exist_ok=True)
    written = drafts / f"{request.node.name}.yaml"
    try:
        yield Drafted(written, str(Path("..") / written.relative_to(REPO_ROOT)))
    finally:
        written.unlink(missing_ok=True)


def test_a_persona_file_stating_no_review_bar_is_refused(
    tmp_path: Path, drafted_persona: Drafted
) -> None:
    """A file naming neither review field is inert, and a plan's author cannot see that.

    The node is reviewed against the base config's generic contract instead, so
    nothing downstream would report that the file the plan named was never read.
    """
    drafted_persona.file.write_text(
        "name: inert\nsystem_prompt: |\n  A role and nothing else.\n", encoding="utf-8"
    )

    refused = _check_plan(_with_node(_plan(tmp_path, STATES_ITS_BAR), persona=drafted_persona.ref))

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "states neither `user.persona` nor `user.done_when`" in refused.stderr, refused.stderr


def test_a_persona_that_replaces_the_shared_bar_is_the_only_bar_in_force(
    tmp_path: Path, drafted_persona: Drafted
) -> None:
    """`user.done_when_replaces_base` drops the shared clause, and the guard follows it.

    Proven by what the refusal names: the demand comes from this file, and the node's
    criteria answer everything the shared clause would have asked for — so a guard
    still composing the base bar would have accepted.
    """
    drafted_persona.file.write_text(
        "name: standalone\nuser:\n  persona: |\n    Only mine.\n"
        "  done_when: 'the change is proven end to end by a soak run'\n"
        "  done_when_replaces_base: true\n",
        encoding="utf-8",
    )
    silent = "\n".join(line for line in STATES_ITS_BAR.splitlines() if "end to end" not in line)

    refused = _check_plan(_with_node(_plan(tmp_path, silent), persona=drafted_persona.ref))

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "scratch/personas" in refused.stderr, refused.stderr
    assert "proof end to end" in refused.stderr, refused.stderr


def test_a_task_rebuilt_from_a_stale_appendix_is_refused(tmp_path: Path) -> None:
    """The copy-paste failure this promotion exists to end, driven through the recipe.

    Every task carries the appendix by copy, so a builder cloned before an appendix
    fix silently reintroduces the wording that fix removed — which is exactly how the
    complete-gate contradiction outlived being noticed.
    """
    plan = _plan(tmp_path, STATES_ITS_BAR)
    document = json.loads(plan.read_text(encoding="utf-8"))
    document["tasks"][0]["task"] = document["tasks"][0]["task"].replace(
        "complete_gate", "just bootstrap && just gate"
    )
    plan.write_text(json.dumps(document), encoding="utf-8")

    refused = _check_plan(plan)

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "current operational appendix" in refused.stderr, refused.stderr


@pytest.mark.parametrize(
    ("document", "reason"),
    (
        ("{not json", "not JSON"),
        ('["a plan is not a list"]', "not an object"),
        ('{"name": "probe"}', "states no `tasks`"),
        ('{"tasks": {"probe": {}}}', "`tasks` is dict, not a list"),
    ),
)
def test_a_plan_whose_document_is_wrong_says_which_way(
    tmp_path: Path, document: str, reason: str
) -> None:
    """A malformed plan is a purposeful diagnostic rather than a traceback."""
    written = tmp_path / "malformed.json"
    written.write_text(document, encoding="utf-8")

    refused = _check_plan(written)

    assert refused.returncode != 0, refused.stdout + refused.stderr
    assert reason in refused.stderr, refused.stderr


def test_a_plan_that_cannot_be_read_is_not_reported_as_a_refusal(tmp_path: Path) -> None:
    """Exit 2, because nothing was judged — a distinction a plan builder branches on."""
    unreadable = _check_plan(tmp_path / "absent.json")

    assert unreadable.returncode == 2, unreadable.stdout + unreadable.stderr
    assert "cannot read" in unreadable.stderr, unreadable.stderr
    assert "just orchestrate" in unreadable.stderr, unreadable.stderr
