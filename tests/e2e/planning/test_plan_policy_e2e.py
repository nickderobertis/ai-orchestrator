"""This host's plan policy is data: the checklist, the templates' schemas and one check.

Every case runs on one bench (`planning_bench`), a copy of this checkout, through what a
manager or the planning road runs — the `check-plan` and `review-plan` recipes, and the plan
store rendering the `plan-task` and `plan-description` templates over fixture answers — and
each case's verdict is traced to the piece of configuration that states it:

* **The judged rules** of `config/plan-checklist.llmlint.yml`. The paid model behind every
  judge is doubled at its harness boundary (`judging_codex.py`), recording every input it
  receives and refusing a rule only where that rule's name and description head a section
  of its input *and* the fixture text the rule judges is in the file the review handed it.
  So a refusal here shows the rule as configured, and the fixture as the real review wrote
  it, reached the judge together — and a copy of the checklist with the rule removed lets
  the same fixture pass.
* **The template schemas**: a budget record or a plan-level answer of the wrong shape is
  refused while the store renders it, naming the JSON pointer.
* **Release adoption**: the engine's loader asks the `onevcs` it links (a scratch registry
  here, holding this repository's identity, two releasing producers and one other
  repository), whose release override declares this repository's `installs` as
  `config/onevcs.releases.yml` does: a wait on a target this host does not install is
  refused `uninstalled`, a `published` wait elsewhere taking a default this host installs
  `default-taken`, and both are accepted once `consumes` names an installed target.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
import yaml
from conftest import git
from judging_codex import JudgeInput
from planning_bench import Bench, bench, judge_inputs, judge_only_turns, just, run
from scratch_identity import GIT_IDENTITY, PLANNING_FLOW_ORIGIN, rules_for_hosted, seeded
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

pytestmark = [pytest.mark.usefixtures("module_tmpdir"), pytest.mark.xdist_group("plan-policy")]

CHECKLIST = Path("config") / "plan-checklist.llmlint.yml"

STATE_PROPERTIES = "plan_criteria_state_properties"
DRAFT_GRANT = "plan_criteria_draft_needs_its_grant"
NAMES_UNIT = "plan_task_names_its_unit"
BUDGET_OWNERS = "plan_checklist_budgets_have_owners"

#: A criterion naming a procedure — an exact command line to run — in place of a property.
PROCEDURE = "Run `just gate` and then `just lint-llm-diff main` before committing."
#: A criterion about the worker's own draft change request.
DRAFT = "The worker's own draft change request is open and its description lists the route."
#: The task's own grant of that draft, written in its `## Additional info`.
GRANT = "This task's change request may be published early: the worker opens it as a draft."
#: What the draft criterion is judged against: the criterion with nothing of the task's own
#: between it and the host's operational notes, which is where a grant would stand.
UNGRANTED = f"- {DRAFT}\n\n## Additional info\n\n### Operational notes for this host"
#: The metadata key a task names its unit in, as the review's front matter spells it.
UNIT_KEY = '"orchestrator.unit"'
#: A budget the description's checklist names that no task's record owns.
ORPHAN = "orphan-health-latency"

#: What the doubled judge refuses each rule over, and nothing else.
REFUSES = {
    STATE_PROPERTIES: PROCEDURE,
    DRAFT_GRANT: UNGRANTED,
    NAMES_UNIT: {"absent": UNIT_KEY},
    BUDGET_OWNERS: ORPHAN,
}

#: A criterion every rule holds.
PROPERTY = "A request-level test drives the health route and reads its JSON body."

BUDGET = {
    "schema_version": 2,
    "id": "health-route-latency",
    "name": "Health route latency",
    "basis": "measured",
    "repository": "github.com/example/service",
    "file": "services/api/budgets.yaml",
    "file_change": "add",
    "measure": "p95 latency of the health route as a poller sees it",
    "inner_measure_reason": "",
    "unit": "ms",
    "direction": "max",
    "threshold": 50,
    "workload": "100 requests per second for 60 seconds against one instance",
    "evidence": "spike-health report: p95 12 ms at that workload",
    "command": "scripts/health-latency.sh",
    "source": "direct",
    "check_runtime_seconds": 70,
}


def _plan_budgets(budget: str) -> dict[str, object]:
    """A description's plan-level budget answers, its checklist naming ``budget``."""
    return {
        "schema_version": 2,
        "sizing": "One poller per instance at 100 requests per second.",
        "workload": "100 requests per second, 20 instances, 60-second polls.",
        "ten_x_summary": "The health route's p95 latency rises first.",
        "ten_x": "At 1000 requests per second its p95 grows, which `health-route-latency` covers.",
        "checklist": [
            {
                "concern": "latency",
                "budget": budget,
                "not_applicable": "",
                "summary": "",
                "in_scope": True,
            }
        ],
        "repo_wide_effects": [],
        "realistic_data": [],
        "spike_findings": [],
    }


STORE = ".venv/bin/onetaskgraph"
RESOLVE = "scripts/onepipeline.sh template resolve"


def _rendered(
    bench_: Bench, template: str, verb: str, **answers: object
) -> subprocess.CompletedProcess[str]:
    """``template`` resolved by the engine, rendered by the store's ``verb`` as a planner does."""
    words = [STORE, *shlex.split(verb), "--template-loader", "-", "--no-interactive", "--json"]
    for name, value in answers.items():
        flag, _, key = name.partition("__")
        if flag == "meta":
            words += ["--metadata", f"{key}={json.dumps(value)}"]
        else:
            words += ["--var", f"{flag}={value if isinstance(value, str) else json.dumps(value)}"]
    command = f"{RESOLVE} {template} --json | {shlex.join(words)}"
    return run(bench_, "bash", "-c", command, seconds=120)


def _id(created: subprocess.CompletedProcess[str]) -> str:
    assert created.returncode == 0, created.stdout + created.stderr
    return str(json.loads(created.stdout)["items"][0]["id"])


def _project(bench_: Bench, native: str, *, budgets: dict[str, object] | None = None) -> str:
    """A plan project in `authoring`, its description rendered from `plan-description`."""
    answers: dict[str, object] = {"overview": f"Give the service a health route ({native})."}
    metadata: dict[str, object] = {
        "meta__onepipeline.schema_version": 3,
        "meta__onepipeline.goal": {"text": "Give the service a health route operators can poll"},
    }
    if budgets is not None:
        answers |= budgets
        metadata["meta__orchestrator.plan-budgets"] = budgets
    verb = f"project create authoring --id {native} --title {native} --classification private"
    return _id(_rendered(bench_, "plan-description", verb, **answers, **metadata))


def _task(
    bench_: Bench,
    plan: str,
    node: str,
    criteria: list[str],
    *,
    unit: str | None = "API",
    budgets: list[dict[str, object]] | None = None,
    additional_info: str = "",
    repository: str | None = None,
    depends_on: str | None = None,
    fields: dict[str, object] | None = None,
) -> subprocess.CompletedProcess[str]:
    """One task of ``plan``, rendered from `plan-task` with its node fields as metadata."""
    answers: dict[str, object] = {
        "what": f"Do {node}.",
        "why": "Operators cannot tell whether the service is up.",
        "acceptance_criteria": criteria,
        "meta__onepipeline.id": node,
        "meta__onepipeline.persona": "engineer",
    }
    if additional_info:
        answers["additional_info"] = additional_info
    if unit is not None:
        answers["meta__orchestrator.unit"] = unit
    if budgets is not None:
        answers["budgets"] = budgets
        answers["meta__orchestrator.budgets"] = budgets
    for key, value in (fields or {}).items():
        answers[f"meta__onepipeline.{key}"] = value
    verb = f"task create authoring --project {plan} --title {shlex.quote('feat: ' + node)}"
    if repository is not None:
        verb += f" --repository {repository}"
    if depends_on is not None:
        verb += f" --depends-on {depends_on}"
    return _rendered(bench_, "plan-task", verb, **answers)


#: What the releasing producer declares — the engine's own three artifacts, of which this host
#: installs the npm package alone — and it names no default target.
DECLARED = (
    ("crate", "crate:onepipeline"),
    ("pypi", "pypi:onepipeline-cli"),
    ("npm", "npm:onepipeline-cli"),
)
PRODUCER = "library"
#: A second releasing producer, whose override rule gives it the default target `npm` —
#: an artifact this repository's `installs` holds — as the engine's own rule does.
DEFAULTED = "tool"
#: A repository other than this one, whose node may take a producer's default only by naming
#: what it consumes.
OTHER = "other"
#: What the scratch override says this repository installs: the npm package alone, as
#: `config/onevcs.releases.yml`'s `installs` holds `npm:onepipeline-cli` and no crate.
INSTALLS = "[npm:onepipeline-cli]"


def _declaration() -> str:
    rows = "".join(
        f'\n[[target]]\nid = "{artifact}"\nname = "{name}"\n'
        f'what = "The {name} target, as a scratch producer declares it."\n'
        "published_by = \"Nothing: a journey's stand-in for a producer's declaration.\"\n"
        for name, artifact in DECLARED
    )
    return f'schema_version = 3\nprobe = "scripts/release-probe.sh"\n{rows}'


def _local(root: Path, name: str, declared: bool) -> Path:
    """A local checkout ``name`` cloned from a bare origin, its base declaring :data:`DECLARED`
    when ``declared``; answers the checkout's resolved path."""
    origin, seed = root / f"{name}-origin.git", root / f"{name}-seed"
    git("init", "-q", "--bare", "-b", "main", str(origin))
    git("init", "-q", "-b", "main", str(seed))
    if declared:
        (seed / "release-targets.toml").write_text(_declaration(), encoding="utf-8")
    else:
        (seed / "README.md").write_text(f"# {name}\n", encoding="utf-8")
    git("add", "-A", cwd=seed)
    git(*GIT_IDENTITY, "commit", "-qm", "chore: seed", cwd=seed)
    git("remote", "add", "origin", str(origin), cwd=seed)
    git("push", "-q", "origin", "main", cwd=seed)
    git("clone", "-q", str(origin), str(root / name))
    return (root / name).resolve()


def _registry(root: Path) -> dict[str, str]:
    """A scratch `onevcs` registry: this repository's identity, two producers and another.

    This repository is seeded under its own hosted origin, so a node naming it resolves to
    the identity this checkout's `origin` resolves to; each producer is a local checkout
    whose base declares :data:`DECLARED`, and :data:`OTHER` one declaring nothing. All are
    registered through the real `just repos-apply`, with a release override declaring
    this repository's :data:`INSTALLS` and :data:`DEFAULTED`'s default target, and no
    adoption rung: every adoption is the `fast` default unless a node states its own.
    Answers the environment that reaches it.
    """
    identity = seeded(root, origin=PLANNING_FLOW_ORIGIN)
    checkouts = [
        _local(root, name, declared)
        for name, declared in ((PRODUCER, True), (DEFAULTED, True), (OTHER, False))
    ]
    manifest = root / "all.checkouts"
    manifest.write_text(
        "".join(f"{one}\n" for one in (identity.publication, identity.execution, *checkouts)),
        encoding="utf-8",
    )
    rules = root / "all.rules.yml"
    rules.write_text(
        rules_for_hosted("github.com", "nickderobertis").replace(
            "default:\n",
            '  - match: {path: "*"}\n    publication: local-direct\n    approvals: none\n'
            "    visibility: private\ndefault:\n",
        ),
        encoding="utf-8",
    )
    releases = root / "all.releases.yml"
    releases.write_text(
        "version: 1\ndefault:\n  adoption: fast\nrepositories:\n"
        "  - match: {host: github.com, owner: nickderobertis, name: ai-orchestrator}\n"
        f"    installs: {INSTALLS}\n"
        f'  - match: {{path: "{checkouts[1]}"}}\n'
        "    default_target: npm\n",
        encoding="utf-8",
    )
    config_home = root / "xdg-config"
    config_home.mkdir(exist_ok=True)
    environment = {**identity.environment, "ONEVCS_HOME": str(identity.home)}
    applied = subprocess.run(
        ["just", "repos-apply", "--checkouts", str(manifest), "--rules", str(rules)]
        + ["--releases", str(releases)],
        cwd=REPO_ROOT,
        env={**os.environ, **environment, "XDG_CONFIG_HOME": str(config_home)},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert applied.returncode == 0, applied.stdout + applied.stderr
    return environment


def _release_plan(
    bench_: Bench,
    native: str,
    *,
    producer: str = PRODUCER,
    elsewhere: bool = False,
    **consumer: object,
) -> str:
    """A plan of ``producer``'s release and a node behind it: of this repository, or of
    :data:`OTHER` when ``elsewhere``."""
    plan = _project(bench_, native)
    released = _id(_task(bench_, plan, "producer", [PROPERTY], fields={"repo": producer}))
    _id(
        _task(
            bench_,
            plan,
            "consumer",
            [PROPERTY],
            repository=None if elsewhere else PLANNING_FLOW_ORIGIN,
            depends_on=released,
            fields={"repo": OTHER, **consumer} if elsewhere else consumer,
        )
    )
    return plan


class Review(NamedTuple):
    """One `just review-plan`, every input the doubled judge received during it, and every
    judge-only reviewer turn it ran."""

    ran: subprocess.CompletedProcess[str]
    inputs: list[JudgeInput]
    reviewer: list[str]


class Sequence(NamedTuple):
    bench: Bench
    rules: dict[str, str]
    #: The valid plan's two tasks, `docs` depending on `route`, by their qualified ids.
    route: str
    docs: str
    valid: Review
    procedure: Review
    ungranted: Review
    granted: Review
    no_unit: Review
    orphan: Review
    procedure_without_rule: Review
    removed_rule_present: bool
    budget_missing: subprocess.CompletedProcess[str]
    budget_mistyped: subprocess.CompletedProcess[str]
    answer_misshapen: subprocess.CompletedProcess[str]
    crate: subprocess.CompletedProcess[str]
    crate_consuming_npm: subprocess.CompletedProcess[str]
    taken: subprocess.CompletedProcess[str]
    taken_consuming_npm: subprocess.CompletedProcess[str]
    fast: subprocess.CompletedProcess[str]
    no_target: subprocess.CompletedProcess[str]


def _reviewed(bench_: Bench, plan: str) -> Review:
    before, turns = len(judge_inputs(bench_)), len(judge_only_turns(bench_))
    ran = just(bench_, "review-plan", plan)
    reviewer = [str(turn.get("prompt")) for turn in judge_only_turns(bench_)[turns:]]
    return Review(ran, judge_inputs(bench_)[before:], reviewer)


def _rules(checkout: Path) -> dict[str, str]:
    """Each plan-checklist rule's description, as the checkout's checklist states it."""
    config = yaml.safe_load((checkout / CHECKLIST).read_text(encoding="utf-8"))
    return {rule["name"]: rule["description"] for rule in config["rules"] if "description" in rule}


def _without(checkout: Path, name: str) -> None:
    """The checkout's checklist with rule ``name``'s entry cut out, every other line kept."""
    path = checkout / CHECKLIST
    text = path.read_text(encoding="utf-8")
    start = text.index(f"  - name: {name}\n")
    end = text.index("  - name: ", start + 1)
    path.write_text(text[:start] + text[end:], encoding="utf-8")


@pytest.fixture(scope="module")
def sequence(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Iterator[Sequence]:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp = tmp_path_factory.mktemp("plan-policy")
    registry = _registry(tmp / "registry")
    bench_ = bench(
        tmp,
        oneharness_bin,
        "e2e-plan-policy",
        FAKE_PLANNING_JUDGE_REFUSES=json.dumps(REFUSES),
        **registry,
    )
    rules = _rules(bench_.checkout)
    tag = os.getpid()

    valid = _project(bench_, f"valid-{tag}", budgets=_plan_budgets(str(BUDGET["id"])))
    route = _id(_task(bench_, valid, "route", [PROPERTY], budgets=[BUDGET]))
    docs = _id(_task(bench_, valid, "docs", [PROPERTY], unit="Docs", budgets=[], depends_on=route))

    procedure = _project(bench_, f"procedure-{tag}")
    _id(_task(bench_, procedure, "route", [PROPERTY, PROCEDURE]))
    ungranted = _project(bench_, f"ungranted-{tag}")
    _id(_task(bench_, ungranted, "route", [PROPERTY, DRAFT]))
    granted = _project(bench_, f"granted-{tag}")
    _id(_task(bench_, granted, "route", [PROPERTY, DRAFT], additional_info=GRANT))
    no_unit = _project(bench_, f"no-unit-{tag}")
    _id(_task(bench_, no_unit, "route", [PROPERTY], unit=None))
    orphan = _project(bench_, f"orphan-{tag}", budgets=_plan_budgets(ORPHAN))
    _id(_task(bench_, orphan, "route", [PROPERTY], budgets=[BUDGET]))

    reviews = {
        plan: _reviewed(bench_, plan)
        for plan in (valid, procedure, ungranted, granted, no_unit, orphan)
    }
    _without(bench_.checkout, STATE_PROPERTIES)
    removed_rule_present = STATE_PROPERTIES in _rules(bench_.checkout)
    procedure_without_rule = _reviewed(bench_, procedure)

    rendering = _project(bench_, f"rendering-{tag}")
    missing = {key: value for key, value in BUDGET.items() if key != "basis"}
    budget_missing = _task(bench_, rendering, "missing", [PROPERTY], budgets=[missing])
    budget_mistyped = _task(
        bench_, rendering, "mistyped", [PROPERTY], budgets=[BUDGET | {"threshold": "fifty"}]
    )
    misshapen = _plan_budgets(str(BUDGET["id"]))
    misshapen["checklist"] = [
        {"concern": "latency", "budget": str(BUDGET["id"]), "not_applicable": "", "summary": 7}
    ]
    answer_misshapen = _rendered(
        bench_,
        "plan-description",
        f"project create authoring --id misshapen-{tag} --title misshapen-{tag}",
        overview="A plan whose checklist is the wrong shape.",
        **misshapen,
    )

    crate = just(
        bench_,
        "check-plan",
        _release_plan(
            bench_,
            f"crate-{tag}",
            adoption="published",
            merge_policy="change-open",
            consumes={"producer": "crate"},
        ),
    )
    crate_consuming_npm = just(
        bench_,
        "check-plan",
        _release_plan(
            bench_,
            f"crate-npm-{tag}",
            adoption="published",
            merge_policy="change-open",
            consumes={"producer": "npm"},
        ),
    )
    taken = just(
        bench_,
        "check-plan",
        _release_plan(
            bench_, f"taken-{tag}", producer=DEFAULTED, elsewhere=True, adoption="published"
        ),
    )
    taken_consuming_npm = just(
        bench_,
        "check-plan",
        _release_plan(
            bench_,
            f"taken-npm-{tag}",
            producer=DEFAULTED,
            elsewhere=True,
            adoption="published",
            merge_policy="change-open",
            consumes={"producer": "npm"},
        ),
    )
    fast = just(bench_, "check-plan", _release_plan(bench_, f"fast-{tag}", adoption="fast"))
    no_target = just(
        bench_, "check-plan", _release_plan(bench_, f"no-target-{tag}", adoption="published")
    )
    yield Sequence(
        bench_,
        rules,
        route,
        docs,
        reviews[valid],
        reviews[procedure],
        reviews[ungranted],
        reviews[granted],
        reviews[no_unit],
        reviews[orphan],
        procedure_without_rule,
        removed_rule_present,
        budget_missing,
        budget_mistyped,
        answer_misshapen,
        crate,
        crate_consuming_npm,
        taken,
        taken_consuming_npm,
        fast,
        no_target,
    )


def _flat(text: str) -> str:
    return " ".join(text.split())


def _judged(review: Review, rule: str, description: str) -> list[JudgeInput]:
    """Every llmlint batch of ``review`` whose input carries ``rule`` with ``description``."""
    return [
        one
        for one in review.inputs
        if one.get("llmlint")
        and f"\n### {rule}\n" in str(one["prompt"])
        and _flat(description) in _flat(str(one["prompt"]))
    ]


def _read(batch: JudgeInput) -> dict[str, str]:
    return batch["read"]


def _refused_through(sequence: Sequence, review: Review, rule: str, text: str, kind: str) -> None:
    """``review`` was refused by ``rule``, reaching the judge with ``text`` in a ``kind`` file."""
    assert review.ran.returncode == 1, review.ran.stdout + review.ran.stderr
    assert rule in review.ran.stderr, review.ran.stderr
    batches = _judged(review, rule, sequence.rules[rule])
    assert batches, f"{rule} never reached the judge with its configured description"
    carrying = [
        path
        for batch in batches
        for path, content in _read(batch).items()
        if path.startswith(f"{kind}/") and text in content
    ]
    assert carrying, f"the {kind} file {rule} judges never carried {text!r}"


def test_a_valid_plan_with_budgets_and_units_passes_the_review(sequence: Sequence) -> None:
    valid = sequence.valid
    assert valid.ran.returncode == 0, valid.ran.stdout + valid.ran.stderr
    for rule in (STATE_PROPERTIES, DRAFT_GRANT, NAMES_UNIT, BUDGET_OWNERS):
        assert rule not in valid.ran.stderr.replace(f"{rule} holds", ""), valid.ran.stderr
    budgets = _judged(valid, BUDGET_OWNERS, sequence.rules[BUDGET_OWNERS])
    descriptions = [
        text
        for batch in budgets
        for path, text in _read(batch).items()
        if path.startswith("projects/")
    ]
    assert descriptions and all(str(BUDGET["id"]) in text for text in descriptions), descriptions
    tasks = [
        text
        for batch in _judged(valid, NAMES_UNIT, sequence.rules[NAMES_UNIT])
        for path, text in _read(batch).items()
        if path.startswith("tasks/")
    ]
    assert len(tasks) == 2 and all(UNIT_KEY in text for text in tasks), tasks


def _front(text: str) -> dict[str, object]:
    """The JSON front matter the review wrote over a judged record."""
    head, _, rest = text.partition("\n")
    assert head == "---", text[:200]
    matter = json.loads(rest.partition("\n---\n")[0])
    assert isinstance(matter, dict), matter
    return matter


def test_the_review_shows_each_task_its_dependency_edges(sequence: Sequence) -> None:
    """The store's graph reaches both judges: the description lists every task's
    `depends_on`, and each task's own judged file carries its own."""
    valid, route, docs = sequence.valid, sequence.route, sequence.docs
    assert valid.ran.returncode == 0, valid.ran.stdout + valid.ran.stderr
    expected = {route: [], docs: [route]}
    files = [item for one in valid.inputs if one.get("llmlint") for item in _read(one).items()]
    descriptions = [_front(text) for path, text in files if path.startswith("projects/")]
    assert descriptions, "no description reached the plan-checklist judge"
    for matter in descriptions:
        tasks = matter["tasks"]
        assert isinstance(tasks, list), matter
        assert {str(task["id"]): task["depends_on"] for task in tasks} == expected, tasks
    judged_tasks = {
        str(_front(text)["title"]): _front(text)["depends_on"]
        for path, text in files
        if path.startswith("tasks/")
    }
    assert judged_tasks == {"feat: route": [], "feat: docs": [route]}, judged_tasks

    # The reviewer judge reads the same material, as the compact JSON the review wrote: each
    # task's own front matter, and the description's entry for every task.
    def shown(task: str) -> str:
        return '"depends_on":' + json.dumps(expected[task], separators=(",", ":"))

    title = {route: "feat: route", docs: "feat: docs"}
    for task in (route, docs):
        own = f'"title":"{title[task]}",{shown(task)}'
        listed = f'"id":"{task}",{own}'
        for needle in (own, listed):
            assert any(needle in turn for turn in valid.reviewer), (
                needle,
                [turn[-400:] for turn in valid.reviewer],
            )


def test_a_criterion_naming_a_procedure_is_refused_by_the_rule_stating_it(
    sequence: Sequence,
) -> None:
    _refused_through(sequence, sequence.procedure, STATE_PROPERTIES, PROCEDURE, "tasks")


def test_a_draft_criterion_without_its_grant_is_refused_and_admitted_with_it(
    sequence: Sequence,
) -> None:
    _refused_through(sequence, sequence.ungranted, DRAFT_GRANT, UNGRANTED, "tasks")
    granted = sequence.granted
    assert granted.ran.returncode == 0, granted.ran.stdout + granted.ran.stderr
    batches = _judged(granted, DRAFT_GRANT, sequence.rules[DRAFT_GRANT])
    read = [
        text
        for batch in batches
        for path, text in _read(batch).items()
        if path.startswith("tasks/")
    ]
    assert read and all(DRAFT in text and GRANT in text for text in read), read


def test_a_task_naming_no_unit_is_refused_by_the_rule_stating_it(sequence: Sequence) -> None:
    review = sequence.no_unit
    assert review.ran.returncode == 1, review.ran.stdout + review.ran.stderr
    assert NAMES_UNIT in review.ran.stderr, review.ran.stderr
    batches = _judged(review, NAMES_UNIT, sequence.rules[NAMES_UNIT])
    read = [
        text
        for batch in batches
        for path, text in _read(batch).items()
        if path.startswith("tasks/")
    ]
    assert read, f"{NAMES_UNIT} never reached the judge with its configured description"
    assert all(PROPERTY in text and UNIT_KEY not in text for text in read), read


def test_a_checklist_naming_a_budget_no_task_owns_is_refused_by_the_rule_stating_it(
    sequence: Sequence,
) -> None:
    _refused_through(sequence, sequence.orphan, BUDGET_OWNERS, ORPHAN, "projects")


def test_the_verdict_comes_from_the_configured_rule(sequence: Sequence) -> None:
    """The procedure fixture passes once the copy's checklist no longer carries its rule."""
    assert not sequence.removed_rule_present
    again = sequence.procedure_without_rule
    assert again.ran.returncode == 0, again.ran.stdout + again.ran.stderr
    assert again.inputs, "the review judged nothing after the checklist changed"
    assert not [one for one in again.inputs if f"\n### {STATE_PROPERTIES}\n" in str(one["prompt"])]
    read = [text for one in again.inputs if one.get("llmlint") for text in _read(one).values()]
    assert any(PROCEDURE in text for text in read), "the same fixture was not judged again"


def test_a_budget_record_missing_a_field_is_refused_at_rendering_naming_the_pointer(
    sequence: Sequence,
) -> None:
    refused = sequence.budget_missing
    assert refused.returncode != 0, refused.stdout
    assert (
        'the answer to "budgets" does not satisfy its schema at JSON pointer "/0"' in refused.stderr
    )
    assert '"basis" is a required property' in refused.stderr, refused.stderr


def test_a_budget_record_with_a_wrongly_typed_field_is_refused_naming_the_pointer(
    sequence: Sequence,
) -> None:
    refused = sequence.budget_mistyped
    assert refused.returncode != 0, refused.stdout
    assert (
        'the answer to "budgets" does not satisfy its schema at JSON pointer "/0/threshold"'
        in refused.stderr
    )
    assert 'is not of type "number"' in refused.stderr, refused.stderr


def test_a_plan_level_answer_of_the_wrong_shape_is_refused_by_the_description_schema(
    sequence: Sequence,
) -> None:
    refused = sequence.answer_misshapen
    assert refused.returncode != 0, refused.stdout
    assert (
        'the answer to "checklist" does not satisfy its schema at JSON pointer "/0/summary"'
        in refused.stderr
    )
    assert 'is not of type "string"' in refused.stderr, refused.stderr


#: How `check-plan` attributes a line to the `onevcs` the engine's loader asks.
ONEVCS = "onevcs: "


def _lines(answered: subprocess.CompletedProcess[str], source: str) -> list[str]:
    """Every line of the recipe's report attributed to ``source``."""
    return [
        line for line in (answered.stdout + answered.stderr).splitlines() if line.startswith(source)
    ]


def _accepted(answered: subprocess.CompletedProcess[str]) -> None:
    report = answered.stdout + answered.stderr
    assert answered.returncode == 0, report
    assert answered.stdout.strip().endswith(": accepted"), report
    assert not _lines(answered, ONEVCS), report


def test_a_node_of_this_repository_waiting_on_a_target_this_host_does_not_install_is_refused(
    sequence: Sequence,
) -> None:
    """The engine's linked onevcs refuses it `uninstalled`, from the override's `installs`."""
    refused = sequence.crate
    report = refused.stdout + refused.stderr
    assert refused.returncode == 1, report
    lines = _lines(refused, ONEVCS)
    assert len(lines) == 1, report
    assert "consumer" in lines[0] and "uninstalled" in lines[0], report
    assert "crate:onepipeline" in lines[0], report


def test_the_same_node_consuming_an_installed_target_is_accepted(sequence: Sequence) -> None:
    _accepted(sequence.crate_consuming_npm)


def test_a_published_node_elsewhere_taking_a_default_this_host_installs_is_refused(
    sequence: Sequence,
) -> None:
    """A node of another repository naming no `consumes` behind a producer whose default is
    an artifact this repository's `installs` holds is refused `default-taken`."""
    refused = sequence.taken
    report = refused.stdout + refused.stderr
    assert refused.returncode == 1, report
    lines = _lines(refused, ONEVCS)
    assert len(lines) == 1, report
    assert "consumer" in lines[0] and "default-taken" in lines[0], report
    assert "npm:onepipeline-cli" in lines[0], report


def test_the_node_elsewhere_naming_an_installed_target_as_its_consumes_is_accepted(
    sequence: Sequence,
) -> None:
    _accepted(sequence.taken_consuming_npm)


def test_a_fast_local_direct_node_behind_a_release_gets_the_pinned_engines_own_answer(
    sequence: Sequence,
) -> None:
    """The pinned engine's loader accepts it.

    `local-direct` is what the scratch rules resolve for this repository, and `fast` is the
    node's own adoption, behind a producer whose base declares release targets and whose
    override names no default target: the adopted engine's loader, asking the `onevcs` it
    links, accepts that plan.
    """
    _accepted(sequence.fast)


def test_a_published_node_behind_a_producer_with_no_default_target_is_refused_by_onevcs(
    sequence: Sequence,
) -> None:
    """The loader refuses it under `onevcs`'s rule."""
    refused = sequence.no_target
    report = refused.stdout + refused.stderr
    assert refused.returncode == 1, report
    lines = _lines(refused, ONEVCS)
    assert any("consumer" in line and "published-no-target" in line for line in lines), report
