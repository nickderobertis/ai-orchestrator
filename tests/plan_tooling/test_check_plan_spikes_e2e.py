"""`just check-plan` holds a plan to the spike convention, through the real recipe.

A plan's spikes live in its `<plan>-spikes` project, stamped `spikes`; every node there is a
lifecycle node named `spike-<topic>` that keeps its branch with `onepipeline.publish:
"preserve"`, and no other project names a node that way. A task building on a spike links
its report — a document of the plan's own project — and its branch through the `plan-task`
template's `spikes` answer. The check refuses each break of that convention, naming the node
and the rule, and passes a well-formed spikes project and a task linking a report that is
there.

Everything is real: the plans written through this repository's own record renderer into
this process's own fixture source, a dependent task's body rendered by the pinned plan
store from the pinned engine's `template resolve plan-task`, the spike reports and budgets
documents rendered through `template resolve` piped into the pinned store's `document
create`, the review recorded by the real `just review-plan` with only the paid provider
doubled, and then the real `just check-plan`, the engine's `plan check` and the registered
check it spawns.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] These journeys sit in
`plan-tooling` beside the check-plan journeys they extend, and drive the same surface those
do — the real recipes, the registered check, the installed engine and store — under the
inputs `planToolingWorkspace` names, which `tests/plan_tooling/AGENTS.md` states as that
project's split. A project of their own would be keyed on those same inputs and add only a
target.
"""

from __future__ import annotations

import itertools
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import NamedTuple

import plan_fixture_source
import pytest
from project_fixtures import budgeted, no_budgets, reviewed
from published_tools import ONETASKGRAPH_BIN
from waits import timeout as e2e_timeout

from orchestrator import design_approval, plan_store
from orchestrator.criteria_guard import APPENDIX
from orchestrator.project_store import write_plan_project
from orchestrator.root import REPO_ROOT

_SEQUENCE = itertools.count()

#: The pinned engine, which states each template as a loader document.
ENGINE = REPO_ROOT / ".venv" / "bin" / "onepipeline"

#: The one repository the plans here change and measure.
REPOSITORY = "github.com/nickderobertis/some-service"

#: The node of the plan that builds on its spike.
NODE = "listing"

#: Criteria answering every demand the appendix and the built-in bar make, so a task carrying
#: them is refused by nothing but the spike convention.
CRITERIA = [
    "The listing pages at 2,000 nodes within the latency the spike measured as achievable.",
    "A request-level test drives the listing end to end and covers the last page.",
    "Every claim the dispatch makes about the finished work is true of the tree as it "
    "finally stands.",
]


class Spike(NamedTuple):
    """One spike of a plan: its node id, its report's document id and its branch."""

    spike: str
    report: str
    branch: str


def _unique(stem: str) -> str:
    """A name no other journey in this process writes, since documents share one directory."""
    return f"{stem}-{os.getpid()}-{next(_SEQUENCE)}"


def _loader(name: str) -> str:
    resolved = subprocess.run(
        [str(ENGINE), "template", "resolve", name, "--json"],
        env={**os.environ, "ONEPIPELINE_TEMPLATE_ROOT": str(REPO_ROOT / "templates")},
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert resolved.returncode == 0, resolved.stderr
    return resolved.stdout


def _rendered_task(spikes: list[Spike]) -> str:
    """The body the `plan-task` template renders for the dependent task."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8") as loader:
        loader.write(_loader("plan-task"))
        loader.flush()
        answers: dict[str, object] = {
            "what": "Page the node listing.",
            "why": "An operator cannot see past the first screen.",
            "acceptance_criteria": CRITERIA,
            "spikes": [spike._asdict() for spike in spikes],
        }
        rendered = plan_store.sdk(
            plan_store.client().template_render(template_loader=loader.name, answers=answers)
        )
    return rendered.body


def _hand_task() -> str:
    """A spike's task: the criteria and the operational appendix, and nothing else of note."""
    criteria = "\n".join(f"- {criterion}" for criterion in CRITERIA)
    return (
        "## What\n\nMeasure the listing at 2,000 nodes.\n\n## Why\n\nThe budget needs a "
        f"number.\n\n## Acceptance criteria\n\n{criteria}\n\n"
        f"{(REPO_ROOT / APPENDIX).read_text(encoding='utf-8').strip()}\n"
    )


def _report(native: str, spike: Spike) -> None:
    """Render ``spike``'s report into the plan ``native``, the way a spike writes it."""
    answers = {
        "spike": spike.spike,
        "branch": spike.branch,
        "harness": "`spikes/listing.py`; run `uv run spikes/listing.py`.",
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
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8") as written:
        json.dump(answers, written)
        written.flush()
        created = subprocess.run(
            [str(ONETASKGRAPH_BIN), "document", "create", plan_fixture_source.SOURCE]
            + ["--project", native, "--title", f"Spike report: {spike.spike}"]
            + ["--id", spike.report, "--template-loader", "-", "--answers", written.name]
            + ["--no-interactive"],
            cwd=REPO_ROOT,
            input=_loader("spike-report"),
            text=True,
            capture_output=True,
            check=False,
        )
    assert created.returncode == 0, created.stderr


def _plan(
    nodes: list[dict[str, object]], *, stamp: list[str] | None = None, budgets: bool = True
) -> tuple[str, str]:
    """Write a reviewed plan of ``nodes`` and answer its native and qualified ids.

    A ``stamp`` makes it a spikes project naming those nodes; every other plan carries a
    budgets document needing no budget, so nothing but the spike convention refuses it.
    """
    native = _unique("spikes-check")
    write_plan_project(
        plan_fixture_source.root(),
        {
            "schema_version": 3,
            "goal": {"text": "Page the node listing"},
            "name": native,
            "tasks": nodes,
        },
        native_id=native,
        project_metadata=None
        if stamp is None
        else {
            design_approval.PLAN_KIND: {
                design_approval.STAMP_KIND: design_approval.SPIKES,
                design_approval.STAMP_NODES: stamp,
            }
        },
    )
    if budgets:
        budgeted(plan_fixture_source.SOURCE, native, no_budgets([REPOSITORY]))
    return native, f"{plan_fixture_source.SOURCE}:{native}"


def _spike_node(node: str, **fields: object) -> dict[str, object]:
    """One spike node, well formed unless ``fields`` says otherwise."""
    shaped: dict[str, object] = {
        "id": node,
        "persona": "engineer",
        "repo": f"https://{REPOSITORY}",
        "title": f"chore: measure {node}",
        "publish": "preserve",
        "task": _hand_task(),
    }
    shaped.update(fields)
    return {name: value for name, value in shaped.items() if value is not None}


def _dependent(spikes: list[Spike], node: str = NODE) -> dict[str, object]:
    return {
        "id": node,
        "persona": "engineer",
        "repo": f"https://{REPOSITORY}",
        "title": "feat: page the node listing",
        "task": _rendered_task(spikes),
    }


def _check(project: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["just", "check-plan", project],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        check=False,
    )


def _refused(checked: subprocess.CompletedProcess[str], node: str, rule: str) -> None:
    assert checked.returncode == 1, checked.stdout + checked.stderr
    assert any(node in line and rule in line for line in checked.stderr.splitlines()), (
        f"no refusal of {node} for {rule!r}:\n{checked.stderr}"
    )


def _spike(native: str, topic: str) -> Spike:
    spike = _unique(f"spike-{topic}")
    return Spike(spike, f"{spike}-report", f"nick/{native}/{spike}")


def test_a_well_formed_spikes_project_passes_with_no_budgets_document() -> None:
    first, second = _unique("spike-listing"), _unique("spike-quota")
    _, project = _plan(
        [_spike_node(first), _spike_node(second)], stamp=[first, second], budgets=False
    )

    checked = _check(reviewed(project))

    assert checked.returncode == 0, checked.stdout + checked.stderr


def test_a_node_of_a_spikes_project_not_named_as_a_spike_is_refused() -> None:
    spike = _unique("spike-listing")
    _, project = _plan(
        [_spike_node(spike), _spike_node("measure-quota")],
        stamp=[spike, "measure-quota"],
        budgets=False,
    )

    _refused(_check(reviewed(project)), "measure-quota", "rename it `spike-<topic>`")


def test_a_spike_that_is_not_a_lifecycle_node_is_refused() -> None:
    spike = _unique("spike-listing")
    _, project = _plan(
        [_spike_node(spike, repo=None, title=None, publish=None)], stamp=[spike], budgets=False
    )

    checked = _check(reviewed(project))

    _refused(checked, spike, "a spike is a lifecycle node in the repository it measures")


def test_a_spike_that_does_not_keep_its_branch_is_refused() -> None:
    spike = _unique("spike-listing")
    _, project = _plan([_spike_node(spike, publish=None)], stamp=[spike], budgets=False)

    _refused(_check(reviewed(project)), spike, 'declares `onepipeline.publish: "preserve"`')


def test_a_spike_named_node_outside_a_spikes_project_is_refused() -> None:
    spike = _unique("spike-listing")
    _, project = _plan([_spike_node(spike, publish=None)])

    _refused(_check(reviewed(project)), spike, "belongs only in a project stamped `spikes`")


def test_a_task_linking_a_report_its_plan_holds_and_that_spikes_branch_passes() -> None:
    native = _unique("spikes-check")
    spike = _spike(native, "listing")
    _, project = _plan([_dependent([spike])])
    _report(project.partition(":")[2], spike)

    checked = _check(reviewed(project))

    assert checked.returncode == 0, checked.stdout + checked.stderr


def test_a_task_linking_a_report_its_plan_does_not_hold_is_refused() -> None:
    native = _unique("spikes-check")
    spike = _spike(native, "listing")
    _, project = _plan([_dependent([spike])])

    _refused(
        _check(reviewed(project)),
        NODE,
        f"links the report `{spike.report}` of `{spike.spike}`, which is not that spike's "
        "report among this plan's documents",
    )


@pytest.mark.parametrize(
    "branch",
    ["nick/some-plan/spike-other", "nick/some-plan/not-a-spike"],
    ids=["another-spike", "no-spike"],
)
def test_a_task_linking_a_branch_that_is_not_its_spikes_is_refused(branch: str) -> None:
    native = _unique("spikes-check")
    spike = _spike(native, "listing")._replace(branch=branch)
    _, project = _plan([_dependent([spike])])
    _report(project.partition(":")[2], spike)

    _refused(
        _check(reviewed(project)),
        NODE,
        f"names the branch `{branch}` for `{spike.spike}`, and a spike's branch ends in "
        "`/spike-<topic>` for that spike",
    )


def test_a_task_linking_the_branch_a_retry_of_its_spike_cut_passes() -> None:
    native = _unique("spikes-check")
    spike = _spike(native, "listing")
    retried = spike._replace(branch=f"{spike.branch}-2")
    _, project = _plan([_dependent([retried])])
    _report(project.partition(":")[2], retried)

    checked = _check(reviewed(project))

    assert checked.returncode == 0, checked.stdout + checked.stderr


def test_a_retry_branch_the_plans_own_spike_of_that_name_could_own_is_refused() -> None:
    """`spike-x-2` is both a retry of `spike-x` and a spike of its own: refused, naming both."""
    native = _unique("spikes-check")
    spike = _spike(native, "listing")
    namesake = Spike(f"{spike.spike}-2", f"{spike.spike}-2-report", f"{spike.branch}-2")
    _, project = _plan([_dependent([spike._replace(branch=namesake.branch)])])
    _report(project.partition(":")[2], spike)
    _report(project.partition(":")[2], namesake)

    _refused(
        _check(reviewed(project)),
        NODE,
        f"could be a retry of `{spike.spike}` or the branch of the plan's own spike "
        f"`{namesake.spike}`",
    )


def test_an_evidence_entry_the_template_does_not_render_is_refused() -> None:
    native = _unique("spikes-check")
    spike = _spike(native, "listing")
    task = _dependent([spike])
    body = str(task["task"])
    entry = body.index("\n- `")
    task["task"] = body[:entry] + "\n- `spike-quota`: see the quota report" + body[entry:]
    _, project = _plan([task])
    _report(project.partition(":")[2], spike)

    _refused(_check(reviewed(project)), NODE, "'- `spike-quota`: see the quota report'")


def test_a_document_by_the_reports_name_that_no_spike_wrote_is_refused(tmp_path: Path) -> None:
    """The id alone is not a report: it is one rendered from the `spike-report` template."""
    native = _unique("spikes-check")
    spike = _spike(native, "listing")
    _, project = _plan([_dependent([spike])])
    hand = tmp_path / "report.md"
    hand.write_text("# A note somebody wrote\n", encoding="utf-8")
    created = subprocess.run(
        [str(ONETASKGRAPH_BIN), "document", "create", plan_fixture_source.SOURCE]
        + ["--project", project.partition(":")[2], "--title", "notes", "--id", spike.report]
        + ["--body-file", str(hand), "--no-interactive"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert created.returncode == 0, created.stderr

    _refused(
        _check(reviewed(project)),
        NODE,
        f"links the report `{spike.report}` of `{spike.spike}`, which is not that spike's report",
    )


def test_a_task_linking_another_spikes_report_is_refused() -> None:
    native = _unique("spikes-check")
    spike, other = _spike(native, "listing"), _spike(native, "quota")
    _, project = _plan([_dependent([spike._replace(report=other.report)])])
    _report(project.partition(":")[2], spike)
    _report(project.partition(":")[2], other)

    _refused(
        _check(reviewed(project)),
        NODE,
        f"links the report `{other.report}` of `{spike.spike}`, which is not that spike's report",
    )


def _gate(project: str) -> subprocess.CompletedProcess[str]:
    """The design-approval gate `scripts/onepipeline.sh` runs before every `start`."""
    return subprocess.run(
        ["uv", "run", "orchestrator-launch-gate", project],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )


def test_the_launch_gate_exempts_a_spikes_project_for_exactly_its_stamped_spikes(
    tmp_path: Path,
) -> None:
    """Exempt while it holds exactly the stamped spikes and no design document; gated after."""
    first, second = _unique("spike-listing"), _unique("spike-quota")
    native, project = _plan(
        [_spike_node(first), _spike_node(second)], stamp=[first, second], budgets=False
    )
    exempt = _gate(project)
    _, grown_project = _plan(
        [_spike_node(first), _spike_node(second), _spike_node("build-listing", publish=None)],
        stamp=[first, second],
        budgets=False,
    )
    hand = tmp_path / "design.md"
    hand.write_text("# Design\n", encoding="utf-8")
    created = subprocess.run(
        [str(ONETASKGRAPH_BIN), "document", "create", plan_fixture_source.SOURCE]
        + ["--project", native, "--title", "Design", "--id", f"{native}-design"]
        + ["--body-file", str(hand), "--no-interactive"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert created.returncode == 0, created.stderr
    documented = _gate(project)
    beyond = _gate(grown_project)

    assert exempt.returncode == 0, exempt.stdout + exempt.stderr
    assert "is the spikes a planning flow launches before it finalizes" in exempt.stderr
    assert documented.returncode != 0, documented.stdout + documented.stderr
    assert "it holds 1 design document(s)" in documented.stderr, documented.stderr
    assert beyond.returncode != 0, beyond.stdout + beyond.stderr
    assert "1 task(s) that launch never wrote (build-listing)" in beyond.stderr, beyond.stderr
