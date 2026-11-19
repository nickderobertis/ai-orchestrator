"""The planning road's `copy` step publishes the design document's board URL, and the engine
quotes it in the approval question it raises.

The `copy` step of `templates/planning/` runs `just copy-plan <plan> <destination>`. Told
`ONEPIPELINE_STEP_OUTPUTS` by the engine, the recipe writes `{"design_url": <url>}` there: the
location the plan store's copy recorded, in the design document's own
`onetaskgraph.copy_locations`, for the board copy of that document. The board is the loopback
GitHub Projects board `tests/github_board.py` serves as the `plans` source, with no real
credential, so the location the store records is an issue URL, as on this host's real board.

1. The recipe, run as the step runs it, publishes exactly the URL the copy recorded.
2. Over a plan with no design document, the copy records no design-document location: the
   recipe fails naming that, and publishes nothing.
3. A two-node plan — the `copy` step and the `approve-design` node carrying the item the real
   `finishing` template renders — launched through the adopted engine, raises the question
   with the placeholder replaced by that same URL.

Only the paid model is doubled, as on every planning bench; nothing here dispatches an agent.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from collections.abc import Iterator
from typing import Any, NamedTuple

import onetaskgraph_user_layer
import pytest
from drafting import design_commands
from github_board import _serving_board
from planning_bench import Bench, bench, just, run, runs_of, store_json

pytestmark = [pytest.mark.usefixtures("module_tmpdir"), pytest.mark.xdist_group("design-url")]

PLANS = "plans"
STORE = ".venv/bin/onetaskgraph"
RESOLVE = "scripts/onepipeline.sh template resolve"
DESIGN_TEMPLATE = "onepipeline:design-doc"
TITLE = "Health route approval"


def _sh(bench_: Bench, script: str) -> str:
    done = run(bench_, "bash", "-c", f"set -euo pipefail; {script}")
    assert done.returncode == 0, f"{script[:400]}\n{done.stdout[-2000:]}{done.stderr[-2000:]}"
    return done.stdout


def _items(answer: dict[str, object]) -> list[dict[str, Any]]:
    """The records a plan-store list answered, refused by name when it answered no list."""
    items = answer.get("items")
    assert isinstance(items, list), answer
    return items


def _plan(bench_: Bench, native: str) -> str:
    """A one-task authoring plan `authoring:<native>`, rendered from the engine's templates."""
    plan = f"authoring:{native}"
    _sh(
        bench_,
        f"{RESOLVE} plan-description --json | {STORE} project create authoring --id {native} "
        f"--title {shlex.quote(TITLE)} --template-loader - --var 'overview=Add a health route.' "
        "--metadata onepipeline.schema_version=3 --no-interactive > /dev/null; "
        f"{RESOLVE} plan-task --json | {STORE} task create authoring --project {plan} "
        "--title 'feat: add the health route' --template-loader - --var 'what=Do route.' "
        "--var 'why=Operators need it.' "
        "--var 'acceptance_criteria=[\"A request-level test drives the route.\"]' "
        "--metadata 'onepipeline.id=\"route\"' --metadata 'onepipeline.persona=\"engineer\"' "
        "--metadata 'orchestrator.unit=\"API\"' --no-interactive > /dev/null",
    )
    return plan


def _design_location(bench_: Bench, plan: str) -> str:
    """The URL the plan store's copy recorded for the board copy of ``plan``'s design document."""
    documents = store_json(bench_, "document", "list", "--source", "authoring", "--project", plan)
    items = _items(documents)
    (design,) = [
        one
        for one in items
        if one["item"]["metadata"]["onetaskgraph.template"]["template"] == DESIGN_TEMPLATE
    ]
    return str(design["item"]["metadata"]["onetaskgraph.copy_locations"][PLANS]["location"]["url"])


def _as_step(bench_: Bench, plan: str, outputs: str) -> subprocess.CompletedProcess[str]:
    """`just copy-plan` as the engine runs the `copy` step: told where to publish."""
    told = bench_._replace(environment={**bench_.environment, "ONEPIPELINE_STEP_OUTPUTS": outputs})
    return just(told, "copy-plan", plan, PLANS)


def _approval_item(bench_: Bench, plan: str, native: str) -> dict[str, Any]:
    """The `approve-design` item the real `finishing` template renders for ``plan``."""
    project = f"{native}-finishing-template"
    _sh(
        bench_,
        f"{STORE} project create authoring --id {project} --title 'Finishing' "
        "--template templates/planning/finishing.md.j2 --metadata onepipeline.schema_version=3 "
        f"--var plan={plan} --var native={native} --var plan_title={shlex.quote(TITLE)} "
        f"--var brief_text=B --var destination={PLANS} --no-interactive > /dev/null",
    )
    members = store_json(
        bench_, "task", "list", "--source", "authoring", "--project", f"authoring:{project}"
    )
    items = _items(members)
    (metadata,) = [
        one["item"]["metadata"]
        for one in items
        if one["item"]["metadata"].get("onepipeline.id") == "approve-design"
    ]
    item: dict[str, Any] = metadata["onepipeline.human_item"]
    return item


def _two_node_plan(bench_: Bench, plan: str, native: str, item: dict[str, Any]) -> str:
    """`authoring:<native>-approval`: the `copy` step over ``plan`` and the approval node."""
    project = f"authoring:{native}-approval"
    copy = json.dumps(["just", "copy-plan", plan, PLANS])
    raised = json.dumps(item)
    stdout = _sh(
        bench_,
        "printf 'One step of the planning road.\\n' > .step.md; "
        f"{STORE} project create authoring --id {native}-approval --title 'Approval' "
        "--body-file .step.md --metadata onepipeline.schema_version=3 --no-interactive "
        "> /dev/null; "
        f"copy=$({STORE} task create authoring --project {project} --title 'Copy the plan' "
        "--body-file .step.md --metadata 'onepipeline.id=\"copy\"' "
        "--metadata 'onepipeline.kind=\"command\"' "
        f"--metadata {shlex.quote('onepipeline.command=' + copy)} --no-interactive --json "
        "| jq -r '.items[0].id'); "
        f"{STORE} task create authoring --project {project} --title 'Approve the design' "
        "--body-file .step.md --metadata 'onepipeline.id=\"approve-design\"' "
        "--metadata 'onepipeline.kind=\"human\"' "
        f"--metadata {shlex.quote('onepipeline.human_item=' + raised)} "
        '--depends-on "$copy" --no-interactive > /dev/null; rm -f .step.md; echo "$copy"',
    )
    assert stdout.strip(), stdout
    return project


class Journey(NamedTuple):
    url: str
    published: subprocess.CompletedProcess[str]
    outputs: str
    refused: subprocess.CompletedProcess[str]
    refused_outputs_exist: bool
    launched: subprocess.CompletedProcess[str]
    run_id: str
    nodes: dict[str, str]
    raised: list[dict[str, Any]]


@pytest.fixture(scope="module")
def journey(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Iterator[Journey]:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp = tmp_path_factory.mktemp("design-url")
    home = onetaskgraph_user_layer.installed(tmp / "config-home", {"sources": {}})
    with _serving_board() as remote:
        bench_ = bench(
            tmp,
            oneharness_bin,
            "e2e-design-url",
            **remote,
            XDG_CONFIG_HOME=str(home),
            ONETASKGRAPH_SOURCES__PLANS__CONFIG__PACING__MIN_MUTATION_INTERVAL_MS="0",
        )
        native = f"design-url-{os.getpid()}"
        plan = _plan(bench_, native)
        for line in design_commands(native):
            _sh(bench_, line)

        outputs = tmp / "copy-outputs.json"
        published = _as_step(bench_, plan, str(outputs))
        url = _design_location(bench_, plan)
        written = outputs.read_text(encoding="utf-8") if outputs.is_file() else ""

        bare = _plan(bench_, f"{native}-bare")
        refused_outputs = tmp / "bare-outputs.json"
        refused = _as_step(bench_, bare, str(refused_outputs))

        item = _approval_item(bench_, plan, native)
        project = _two_node_plan(bench_, plan, native, item)
        launched = run(
            bench_,
            *("scripts/onepipeline.sh", "start", project),
            *("--launch-config", "config/onepipeline.workflows.yaml", "--dag-graph", "off"),
        )
        found = [one for one in runs_of(bench_) if "approval" in one.lower()]
        assert len(found) == 1, (runs_of(bench_), launched.stdout[-2000:], launched.stderr[-3000:])
        (run_id,) = found
        status = run(bench_, "scripts/onepipeline.sh", "status", run_id, "--json")
        assert status.returncode == 0, status.stdout + status.stderr
        nodes = {
            str(node["id"]): str(node.get("status")) for node in json.loads(status.stdout)["nodes"]
        }
        listed = run(
            bench_, "scripts/onepipeline.sh", "human-items", "list", "--run", run_id, "--json"
        )
        assert listed.returncode == 0, listed.stdout + listed.stderr
        raised = [entry["item"] for entry in json.loads(listed.stdout)]
        just(bench_, "stop", run_id, seconds=120)
    yield Journey(
        url,
        published,
        written,
        refused,
        refused_outputs.exists(),
        launched,
        run_id,
        nodes,
        raised,
    )


def test_the_copy_step_publishes_the_url_the_copy_recorded(journey: Journey) -> None:
    published = journey.published
    assert published.returncode == 0, published.stdout + published.stderr
    assert journey.url.startswith("https://"), journey.url
    assert json.loads(journey.outputs) == {"design_url": journey.url}


def test_a_copy_recording_no_design_document_location_fails_the_step_naming_it(
    journey: Journey,
) -> None:
    refused = journey.refused
    assert refused.returncode != 0, refused.stdout + refused.stderr
    assert "recorded no design-document location" in refused.stderr, refused.stderr
    assert "holds no design document" in refused.stderr, refused.stderr
    assert not journey.refused_outputs_exist


def test_the_engine_raises_the_approval_question_linking_that_url(journey: Journey) -> None:
    launched = journey.launched
    assert journey.nodes == {"copy": "done", "approve-design": "waiting"}, (
        journey.nodes,
        launched.stdout[-2000:],
        launched.stderr[-3000:],
    )
    (question,) = journey.raised
    assert question["kind"] == "question", question
    body = json.dumps(question)
    assert f"[Read the design document]({journey.url})" in body, body
    assert "${steps." not in body, body
    assert [choice["id"] for choice in question["choices"]] == ["approve"], question
