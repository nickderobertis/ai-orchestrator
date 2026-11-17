"""This repository's budgets files, held to the installed onebudgetspec and to each other.

The root `budgets.yaml` is what every push of this repository is held to, and its shape is
stated here in full so a budget that moved, or one added without a decision, fails rather
than passing as itself. Its threshold has one source, the file: nothing that measures or
checks it restates it. `gate-time` is labelled `host-variable`, so the hook warns rather
than refuses a passing push over it — gate wall clock on a shared, variably loaded host is
not a consistent measurement, so a strict absolute threshold belongs on a consistent system
such as a CI runner — while a failed gate or an errored measurement still refuses. A
project's own `budgets.yaml` is measured by its `budgets` and `budgets-host` Nx targets,
which a project declares only by opting in, so the file and the targets are held together
here; the root project never declares them, because the root file is the hook's to check,
once, after the gate it measures. And the vocabulary this host's task-budgets partial copies
from onebudgetspec — the `direction` values, the id pattern and the file name, in its words
and in its answer schema — is read from the installed library's own `onebudgetspec schema`,
so a release that moved any of them fails here.

llmlint: ignore-file[shell_test_tiers_stay_split] The installed onebudgetspec is run twice
here, `schema` and `validate`, each in milliseconds and reading nothing outside this
checkout: the CLI is the pinned install `uv.lock` names, and the files it validates are
the tree's own, all in the code tier's key. A test project of its own would narrow that
key for no measurable saving.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest
import yaml
from nx_inputs import BUDGET_HOST_SCOPED, BUDGET_SCOPED, project_declarations

from orchestrator import npm_installs
from orchestrator.root import REPO_ROOT

#: onebudgetspec's one budgets file name, held to the installed library below.
BUDGETS_FILE = "budgets.yaml"
ROOT_BUDGETS = REPO_ROOT / BUDGETS_FILE
TEMPLATE = REPO_ROOT / "templates" / "plan-task-budgets.md.j2"

#: The budget holding a design document's budget summary short enough to approve from.
SUMMARY_BUDGET = "design-doc-budget-summary-length"
#: `onebudgetspec check`'s exits for a budget it measured: within, and over its threshold.
MEASURED_WITHIN = 0
MEASURED_OVER = 1

#: The installed onebudgetspec, beside the interpreter: the locked install the hook and the
#: Nx targets run.
CHECKER = Path(sys.executable).parent / "onebudgetspec"
#: The files that measure `gate-time` or refuse a push over it, none of which may state its
#: threshold: the journeys read it from the file, and the scripts never need it.
MEASURING = (
    ".githooks/pre-push",
    "scripts/gate-budgets.sh",
    "scripts/budget-gate-time.sh",
    "scripts/budget-dispatches.sh",
    "scripts/lock-timeout.sh",
    "tests/e2e/budget_gate/test_gate_time_budget_e2e.py",
    "tests/e2e/budget_gate/test_budget_target_e2e.py",
)


def _root() -> dict:
    return yaml.safe_load(ROOT_BUDGETS.read_text(encoding="utf-8"))


def _schema() -> dict:
    ran = subprocess.run(
        [str(CHECKER), "schema"], capture_output=True, text=True, check=False, timeout=60
    )
    assert ran.returncode == 0, ran.stderr
    return json.loads(ran.stdout)["roots"]["budgets-file"]


def test_the_root_file_holds_gate_time_cycle_time_and_the_dispatches_condition() -> None:
    document = _root()
    by_id = {budget["id"]: budget for budget in document["budgets"]}
    gate_time, cycle_time = by_id["gate-time"], by_id["cycle-time"]
    threshold = gate_time.pop("threshold")
    description = gate_time.pop("description")

    assert document["schema_version"] == 1
    assert document["conditions"] == [
        {"name": "dispatches", "command": ["scripts/budget-dispatches.sh"]}
    ]
    assert gate_time == {
        "id": "gate-time",
        "labels": ["host-variable"],
        "measure": "reported",
        "command": ["scripts/budget-gate-time.sh"],
        "unit": "seconds",
        "direction": "max",
    }
    assert isinstance(threshold, int | float) and threshold > 0, threshold
    assert "pre-push gate" in description and "It protects" in description
    assert cycle_time.pop("threshold") > 0
    description = cycle_time.pop("description")
    assert "first dispatch" in description and "It protects" in description
    assert cycle_time == {
        "id": "cycle-time",
        "labels": ["onepipeline"],
        "measure": "reported",
        "command": ["scripts/budget-cycle-time.sh"],
        "unit": "seconds",
        "direction": "max",
    }


@pytest.mark.parametrize(
    ("budget", "figure", "unit", "threshold"),
    [
        ("gate-leftovers", "leftovers", "entries", 0),
        ("gate-peak-temp-disk", "peak-temp-disk", "gib", 2),
        ("gate-leaked-processes", "leaked-processes", "processes", 0),
    ],
)
def test_the_root_file_holds_each_gate_host_impact_figure_strictly(
    budget: str, figure: str, unit: str, threshold: int
) -> None:
    """Unlabelled, so over it refuses the push; read back from the gate's own record."""
    (entry,) = [one for one in _root()["budgets"] if one["id"] == budget]
    description = entry.pop("description")
    assert "It protects" in description and "gate" in description, description
    assert entry == {
        "id": budget,
        "measure": "reported",
        "command": [".venv/bin/python", "-m", "orchestrator.gate_impact", "report", figure],
        "unit": unit,
        "direction": "max",
        "threshold": threshold,
    }


def test_the_root_file_registers_the_design_documents_budget_summary_length() -> None:
    """The summary a person approves a plan from is a budget measured for #1568."""
    (summary,) = [one for one in _root()["budgets"] if one["id"] == SUMMARY_BUDGET]
    description = summary.pop("description")
    threshold = summary.pop("threshold")

    assert isinstance(threshold, int | float) and threshold > 0, threshold
    assert summary == {
        "id": SUMMARY_BUDGET,
        "measure": "reported",
        "command": ["scripts/budget-design-doc-summary.sh"],
        "unit": "characters",
        "direction": "max",
    }
    flat = " ".join(description.split())
    assert "#1568" in flat and "`## Budgets` section" in flat, flat
    assert "body of every `<details>` block excluded and its `<summary>` line kept" in flat, flat


#: The full `## Budgets` section the design-doc template renders for the committed fixture.
SUMMARY_RENDERING = REPO_ROOT / "tests" / "fixtures" / "budgets" / "issue-1568-summary.txt"


def _unfolded(section: str) -> tuple[int, list[int]]:
    """What a reader sees of ``section`` before opening a block, and each block's length.

    Read line by line rather than by the script's own pattern: from each `<details>` line,
    the `<summary>` line after it is kept and every line after that up to `</details>` is
    the collapsed body, which is not counted.
    """
    lines = section.split("\n")
    seen = 0
    blocks: list[int] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line == "<details>" and lines[index + 1].startswith("<summary>"):
            end = lines.index("</details>", index)
            blocks.append(len("\n".join(lines[index : end + 1])))
            seen += len(line) + len(lines[index + 1]) + len("</details>") + 2
            index = end + 1
            if index < len(lines):
                seen += 1
            continue
        seen += len(line) + (1 if index < len(lines) - 1 else 0)
        index += 1
    return seen, blocks


@pytest.mark.parametrize(
    ("budget", "arguments", "threshold"),
    [
        ("agents-md-instruction-tokens", ["AGENTS.md"], 24100),
        (
            "planner-prompt-instruction-tokens",
            ["personas/planner.yaml", "--yaml-key", "system_prompt"],
            20800,
        ),
    ],
)
def test_the_root_file_holds_each_instruction_token_budget_strictly(
    budget: str, arguments: list[str], threshold: int
) -> None:
    """Unlabelled, so over either refuses the push; measured from the pushed tree's text."""
    (entry,) = [one for one in _root()["budgets"] if one["id"] == budget]
    description = entry.pop("description")
    assert "It protects" in description and "Estimated tokens" in description, description
    assert entry == {
        "id": budget,
        "measure": "reported",
        "command": [".venv/bin/python", "-m", "orchestrator.instruction_tokens", *arguments],
        "unit": "tokens",
        "direction": "max",
        "threshold": threshold,
    }


@pytest.mark.parametrize("where", ["checkout", "elsewhere"])
def test_the_library_measures_the_summary_of_the_committed_fixture(
    where: str, tmp_path: Path
) -> None:
    """`onebudgetspec check` reads the entry, runs its command once and reports its length.

    Also from a copy of the entry outside the checkout, its command made absolute, as the
    gate-time journeys check the root file: the library runs a command from its file's
    directory, and this suite's environment sets plan-store source roots as a launch does,
    which the plan store refuses as settings with no source anywhere but the checkout.
    """
    budgets = ROOT_BUDGETS
    # The root file's `dispatches` condition runs on every check, whatever budget is
    # targeted, and reads the engine's runs root; this one is the test's own and empty, so
    # the check's cost and its condition never depend on this host's run history.
    runs = tmp_path / "runs"
    runs.mkdir()
    environment = {**os.environ, "ONEPIPELINE_RUNS_DIR": str(runs)}
    if where == "elsewhere":
        (summary,) = [one for one in _root()["budgets"] if one["id"] == SUMMARY_BUDGET]
        summary["command"] = [str(REPO_ROOT / summary["command"][0])]
        budgets = tmp_path / "budgets.yaml"
        budgets.write_text(
            yaml.safe_dump({"schema_version": 1, "budgets": [summary]}), encoding="utf-8"
        )
    ran = subprocess.run(
        [str(CHECKER), "check", "--id", SUMMARY_BUDGET, "--output", "json", str(budgets)],
        cwd=budgets.parent,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    # Measured, within or over: whether the figure is over its threshold is the budget's
    # verdict, never this test's.
    assert ran.returncode in (MEASURED_WITHIN, MEASURED_OVER), ran.stdout + ran.stderr
    (result,) = json.loads(ran.stdout[ran.stdout.index("{") :])["results"]
    section = SUMMARY_RENDERING.read_text(encoding="utf-8")
    seen, blocks = _unfolded(section)
    assert blocks, "the fixture's section collapses no block, so nothing here is excluded"
    assert result["actual"] == seen < len(section), result
    assert f"full section {len(section)} characters" in result["detail"], result
    lengths = ", ".join(str(length) for length in blocks)
    assert f"collapsed blocks {lengths} characters" in result["detail"], result
    if where == "checkout":
        assert result["host"]["conditions"]["dispatches"] == "0", result["host"]


class Checkout(NamedTuple):
    """A checkout holding only what the summary command reads, over the real tools."""

    #: The checkout's root, and the fixture it renders.
    root: Path
    fixture: Path
    #: Its `.venv/bin`, holding links to the locked interpreter and plan store.
    binaries: Path
    #: The scratch root the command is pointed at, and the result file onebudgetspec names.
    scratch: Path
    result: Path
    #: The command's environment.
    environment: dict[str, str]


def _isolated_checkout(tmp_path: Path) -> Checkout:
    """A :class:`Checkout` under ``tmp_path``."""
    root = tmp_path / "checkout"
    script = root / "scripts" / "budget-design-doc-summary.sh"
    script.parent.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "scripts" / script.name, script)
    shutil.copy2(REPO_ROOT / "onetaskgraph.yaml", root / "onetaskgraph.yaml")
    fixture = root / "tests" / "fixtures" / "budgets" / "issue-1568.json"
    fixture.parent.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / fixture.relative_to(root), fixture)
    (root / "templates").mkdir()
    for name in ("templates.yaml", "design-doc.md.j2"):
        shutil.copy2(REPO_ROOT / "templates" / name, root / "templates" / name)
    binaries = root / ".venv" / "bin"
    binaries.mkdir(parents=True)
    installed = Path(sys.prefix)
    (binaries.parent / "pyvenv.cfg").symlink_to(installed / "pyvenv.cfg")
    (binaries.parent / "lib").symlink_to(installed / "lib", target_is_directory=True)
    for name in ("python", "onetaskgraph"):
        (binaries / name).symlink_to(Path(sys.executable).parent / name)
    engine = npm_installs.installed_command(node_modules=root / "node_modules")
    engine.parent.mkdir(parents=True)
    engine.symlink_to(npm_installs.ENGINE)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = tmp_path / "result.json"
    environment = {**os.environ, "ONEBUDGETSPEC_RESULT": str(result), "TMPDIR": str(scratch)}
    return Checkout(root, fixture, binaries, scratch, result, environment)


def _measured(root: Path, result: Path, environment: dict[str, str]) -> dict[str, object]:
    """Run the checkout's real summary command and answer the result it wrote."""
    ran = subprocess.run(
        [str(root / "scripts" / "budget-design-doc-summary.sh")],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    measured = json.loads(result.read_text(encoding="utf-8"))
    assert isinstance(measured, dict), measured
    return measured


def test_the_summary_counts_no_collapsed_body_and_every_line_outside_one(tmp_path: Path) -> None:
    """A longer collapsed block leaves the figure as it was; longer text outside raises it.

    The fixture as committed, then with one more budget changed only in its measure and
    command at no change in check runtime, under a long name — a line in the collapsed
    block and nothing outside it, the block's count staying one digit — then with a longer
    sizing answer, which is read before any block opens.
    """
    checkout = _isolated_checkout(tmp_path)
    root, fixture, result, environment = (
        checkout.root,
        checkout.fixture,
        checkout.result,
        checkout.environment,
    )
    committed = _measured(root, result, environment)
    answers = json.loads(fixture.read_text(encoding="utf-8"))

    (changed, *_) = [one for one in answers["budgets"] if one["file_change"] == "change"]
    another = json.loads(json.dumps(changed))
    another["id"] = "linear-asset-requests-per-unchanged-issue-image"
    another["name"] = another["previous"]["name"] = (
        "Linear requests per unchanged image on an issue re-copy"
    )
    another["link"] = f"{another['location']}#budget-{another['id']}"
    # Its check costs what it did, so the total read outside the block stays as it was.
    another["previous"]["check_runtime_seconds"] = another["check_runtime_seconds"]
    answers["budgets"].append(another)
    fixture.write_text(json.dumps(answers), encoding="utf-8")
    folded = _measured(root, result, environment)

    added = " It holds at the busiest hour of the week."
    answers["plan_budgets"]["sizing"] += added
    fixture.write_text(json.dumps(answers), encoding="utf-8")
    outside = _measured(root, result, environment)

    assert folded["value"] == committed["value"], (committed, folded)
    assert folded["detail"] != committed["detail"], "the longer block's length is not reported"
    assert outside["value"] == committed["value"] + len(added), (committed, outside)


def test_a_section_collapsing_no_block_is_counted_whole(tmp_path: Path) -> None:
    """With only added budgets nothing is collapsed: the figure is the whole section."""
    checkout = _isolated_checkout(tmp_path)
    answers = json.loads(checkout.fixture.read_text(encoding="utf-8"))
    answers["budgets"] = [one for one in answers["budgets"] if one["file_change"] == "add"]
    checkout.fixture.write_text(json.dumps(answers), encoding="utf-8")

    measured = _measured(checkout.root, checkout.result, checkout.environment)

    detail = str(measured["detail"])
    assert f"full section {measured['value']} characters" in detail, measured
    assert "collapsed blocks none" in detail, measured


@pytest.mark.parametrize(
    ("fault", "diagnostic"),
    [
        ("no-result", "ONEBUDGETSPEC_RESULT names no result file"),
        ("no-fixture", "cannot be read, so there are no answers to render"),
        ("no-python", "is not this checkout's locked interpreter"),
        ("no-scratch", "a scratch directory for the template's loader"),
        ("no-template", "the pinned engine did not resolve"),
        ("malformed-fixture", "fixture does not hold the design document's"),
        ("malformed-record", "the fixture's budget 0 lacks numeric threshold"),
        ("malformed-checklist", "the fixture's plan-level `checklist` is not a list"),
        ("missing-answer", "the pinned plan store did not render"),
        ("no-section", "the rendering carries no '## Budgets' section"),
        ("unwritable-result", "the result could not be written"),
    ],
)
def test_the_summary_command_reports_failure_without_leaving_scratch(
    tmp_path: Path, fault: str, diagnostic: str
) -> None:
    """Run the real command and tools against a damaged, isolated input checkout."""
    checkout = _isolated_checkout(tmp_path)
    root, fixture, binaries = checkout.root, checkout.fixture, checkout.binaries
    scratch, result, environment = checkout.scratch, checkout.result, checkout.environment
    match fault:
        case "no-result":
            environment.pop("ONEBUDGETSPEC_RESULT")
        case "no-fixture":
            fixture.unlink()
        case "no-python":
            (binaries / "python").unlink()
        case "no-scratch":
            scratch.rmdir()
            scratch.write_text("not a directory", encoding="utf-8")
        case "no-template":
            (root / "templates" / "design-doc.md.j2").unlink()
        case "missing-answer":
            template = root / "templates" / "design-doc.md.j2"
            template.write_text(
                template.read_text(encoding="utf-8").replace(
                    "variables:\n",
                    "variables:\n  missing:\n    description: An unanswered variable.\n"
                    "    type: text\n    required: true\n",
                ),
                encoding="utf-8",
            )
        case "unwritable-result":
            result.mkdir()
        case _:
            answers = json.loads(fixture.read_text(encoding="utf-8"))
            match fault:
                case "malformed-fixture":
                    answers.pop("budgets")
                case "malformed-record":
                    answers["budgets"][0]["threshold"] = "fast"
                case "malformed-checklist":
                    answers["plan_budgets"]["checklist"] = {"not": "a list"}
                case _:
                    answers["budgets"] = []
                    answers["plan_budgets"]["checklist"] = []
                    answers["plan_budgets"]["repo_wide_effects"] = []
            fixture.write_text(json.dumps(answers), encoding="utf-8")
    ran = subprocess.run(
        [str(root / "scripts" / "budget-design-doc-summary.sh")],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert ran.returncode == 1, ran.stdout + ran.stderr
    assert diagnostic in ran.stderr
    assert not result.is_file(), "a failed measurement must not publish a result"
    if scratch.is_dir():
        assert list(scratch.iterdir()) == [], "the invocation must discard its own scratch"


def test_the_summary_command_reports_host_directory_failures_and_installs_its_trap() -> None:
    """Deleting the running checkout or revoking cleanup permission needs a host race.

    Input failures run above against isolated real tools. These host failures are held
    structurally: racing a deletion or chmod against those tools would test scheduling
    luck, and replacing cd or rm would double the shell boundary being checked.
    """
    script = (REPO_ROOT / "scripts" / "budget-design-doc-summary.sh").read_text(encoding="utf-8")
    assert 'cd -- "$(dirname -- "$0")/.." && pwd) ||' in script
    assert "cannot be entered, so the template and fixture it renders cannot be read" in script
    assert "trap discard EXIT" in script
    assert 'rm -rf -- "$scratch" ||' in script
    assert "the scratch directory $scratch could not be removed; delete it by hand" in script


def test_the_installed_library_validates_the_whole_tree() -> None:
    ran = subprocess.run(
        [str(CHECKER), "validate", "--recursive", "."],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr


@pytest.mark.parametrize("identifier", ["gate-time", "cycle-time"])
def test_each_delivery_threshold_is_stated_in_the_budgets_file_alone(identifier: str) -> None:
    """Nothing that measures `gate-time`, or refuses a push over it, restates the number."""
    budget = next(entry for entry in _root()["budgets"] if entry["id"] == identifier)
    number = re.compile(rf"(?<![\d.]){re.escape(f'{budget["threshold"]:g}')}(?![\d])")

    restating = [
        relative
        for relative in (
            *MEASURING,
            "scripts/budget-cycle-time.sh",
            "scripts/follow-up-budget-inputs.sh",
            "templates/follow-up-task.md.j2",
        )
        if number.search((REPO_ROOT / relative).read_text(encoding="utf-8"))
    ]
    assert restating == [], f"{restating} restate the {identifier} threshold budgets.yaml states"


def test_a_project_declares_the_budgets_targets_exactly_when_it_holds_its_own_budgets_file() -> (
    None
):
    """The root project holds the root file and never the targets: the hook checks that one.

    Both targets, because which of a file's budgets are `host` ones can change with any
    edit to it, and a project declaring only `budgets` would then measure them nowhere.
    """
    declarations = project_declarations()
    with_file = {
        root for root in declarations if root and (REPO_ROOT / root / BUDGETS_FILE).is_file()
    }
    for target in (BUDGET_SCOPED, BUDGET_HOST_SCOPED):
        with_target = {
            root for root, declared in declarations.items() if target in declared.get("targets", {})
        }

        assert "" not in with_target, f"the root project declares a {target} target"
        assert with_target == with_file, (
            f"projects with a {target} target: {sorted(with_target)}; projects holding their "
            f"own {BUDGETS_FILE}: {sorted(with_file)}"
        )


def _budget_schema() -> dict[str, object]:
    """The per-budget object schema the task-budgets partial declares for its `budgets`."""
    front = TEMPLATE.read_text(encoding="utf-8").split("---\n")[1]
    declared = yaml.safe_load(front)["variables"]["budgets"]["schema"]["items"]
    assert isinstance(declared, dict)
    return declared


def test_the_direction_vocabulary_and_file_name_are_the_installed_librarys() -> None:
    """The template's copies, in words and in its schema, against `onebudgetspec schema`."""
    schema = _schema()
    directions = [choice["const"] for choice in schema["$defs"]["Direction"]["oneOf"]]
    (file_name,) = re.findall(r"`([^`]+)`", schema["description"])
    template = TEMPLATE.read_text(encoding="utf-8")
    template_directions = re.search(r"`direction`\s+\(string,\s+`(\w+)`\s+or\s+`(\w+)`\)", template)
    template_file = re.search(
        r"the\s+repository-relative\s+path\s+of\s+the\s+`([^`]+)`\s+it\s+goes\s+in", template
    )

    assert template_directions is not None and template_file is not None, (
        f"{TEMPLATE} no longer states the direction vocabulary or the file name where this "
        "test reads them"
    )
    assert list(template_directions.groups()) == directions, template_directions.group(0)
    assert template_file.group(1) == file_name, template_file.group(0)
    assert file_name == BUDGETS_FILE, f"onebudgetspec names {file_name}, this suite {BUDGETS_FILE}"
    properties = _budget_schema()["properties"]
    assert isinstance(properties, dict)
    assert properties["direction"]["enum"] == directions
    assert re.search(properties["file"]["pattern"], file_name)


def test_the_budget_id_pattern_is_the_installed_librarys() -> None:
    """The template schema's copy, which a schema-2 budget's heading anchor rests on."""
    properties = _budget_schema()["properties"]
    assert isinstance(properties, dict)
    assert (
        _schema()["$defs"]["Budget"]["properties"]["id"]["pattern"] == (properties["id"]["pattern"])
    )
