"""This repository's budgets files, held to the installed onebudgetspec and to each other.

The root `budgets.yaml` is what every push of this repository is held to, and its shape is
stated here in full so a budget that moved, or one added without a decision, fails rather
than passing as itself. Its threshold has one source, the file: nothing that measures or
checks it restates it. `gate-time` is labelled `host-variable`, so the hook warns rather
than refuses a passing push over it — gate wall clock on a shared, variably loaded host is
not a consistent measurement, so a strict absolute threshold belongs on a consistent system
such as a CI runner — while a failed gate or an errored measurement still refuses. A
project's own `budgets.yaml` is measured by its `budget` Nx target, which a project declares
only by opting in, so the file and the target are held together here; the root project
never declares one, because the root file is the hook's to check, once, after the gate it
measures. And the vocabulary this host's task-budgets
partial and `orchestrator/plan_budgets.py` copy from onebudgetspec — the `direction`
values and the file name — is read from the installed library's own `onebudgetspec
schema`, so a release that moved either fails here.

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

import pytest
import yaml
from nx_inputs import BUDGET_SCOPED, project_declarations

from orchestrator.plan_budgets import BUDGETS_FILE, Direction
from orchestrator.root import REPO_ROOT

ROOT_BUDGETS = REPO_ROOT / "budgets.yaml"
TEMPLATE = REPO_ROOT / "templates" / "plan-task-budgets.md.j2"

#: The budget holding a design document's budget summary short enough to approve from.
SUMMARY_BUDGET = "design-doc-budget-summary-length"

#: The installed onebudgetspec, beside the interpreter: the locked install the hook and the
#: Nx targets run.
CHECKER = Path(sys.executable).parent / "onebudgetspec"
#: The files that measure `gate-time` or check a push against it, none of which may state its
#: threshold: the journeys read it from the file, and the scripts never need it.
MEASURING = (
    ".githooks/pre-push",
    "scripts/gate-budgets.sh",
    "scripts/budget-gate-time.sh",
    "scripts/budget-dispatches.sh",
    "scripts/lock-timeout.sh",
    "tests/e2e/test_gate_time_budget_e2e.py",
    "tests/e2e/test_budget_target_e2e.py",
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
    gate_time, cycle_time, _ = document["budgets"]
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
    assert "pre-push gate" in description and "record_local_direct_gate" in description
    assert "warns rather than refuses" in description and "CI runner" in description
    assert cycle_time.pop("threshold") > 0
    description = cycle_time.pop("description")
    assert "reported after landing" in description and "never blocks" in description
    assert cycle_time == {
        "id": "cycle-time",
        "labels": ["onepipeline"],
        "measure": "reported",
        "command": ["scripts/budget-cycle-time.sh"],
        "unit": "seconds",
        "direction": "max",
    }


def test_the_root_file_registers_the_design_documents_budget_summary_length() -> None:
    """The summary a person approves a plan from is held to 3,000 characters for #1568."""
    (summary,) = [one for one in _root()["budgets"] if one["id"] == SUMMARY_BUDGET]
    description = summary.pop("description")

    assert summary == {
        "id": SUMMARY_BUDGET,
        "measure": "reported",
        "command": ["scripts/budget-design-doc-summary.sh"],
        "unit": "characters",
        "direction": "max",
        "threshold": 3000,
    }
    assert "#1568" in description and "`## Budgets` section" in description


@pytest.mark.parametrize("where", ["checkout", "elsewhere"])
def test_the_library_measures_the_summary_of_the_committed_fixture(
    where: str, tmp_path: Path
) -> None:
    """`onebudgetspec check` reads the entry, runs its command once and reports it within.

    Also from a copy of the entry outside the checkout, its command made absolute, as the
    gate-time journeys check the root file: the library runs a command from its file's
    directory, and this suite's environment sets plan-store source roots as a launch does,
    which the plan store refuses as settings with no source anywhere but the checkout.
    """
    budgets = ROOT_BUDGETS
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
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    (result,) = json.loads(ran.stdout[ran.stdout.index("{") :])["results"]
    rendered = REPO_ROOT / "tests" / "fixtures" / "budgets" / "issue-1568-summary.txt"
    assert result["verdict"] == "within", result
    assert result["actual"] == len(rendered.read_text(encoding="utf-8")), result
    assert result["threshold"] == 3000, result


@pytest.mark.parametrize(
    ("fault", "diagnostic"),
    [
        ("no-result", "ONEBUDGETSPEC_RESULT names no result file"),
        ("no-fixture", "cannot be read, so there are no answers to render"),
        ("no-python", "is not this checkout's locked interpreter"),
        ("no-scratch", "a scratch directory for the template's loader"),
        ("no-template", "the pinned engine did not resolve"),
        ("malformed-fixture", "fixture does not hold exactly the three budget answers"),
        ("malformed-record", "fixture's answers are malformed"),
        ("missing-answer", "the pinned plan store did not render"),
        ("no-section", "the rendering carries no '## Budgets' section"),
        ("unwritable-result", "the result could not be written"),
    ],
)
def test_the_summary_command_reports_failure_without_leaving_scratch(
    tmp_path: Path, fault: str, diagnostic: str
) -> None:
    """Run the real command and tools against a damaged, isolated input checkout."""
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
    (root / "orchestrator").symlink_to(REPO_ROOT / "orchestrator", target_is_directory=True)
    binaries = root / ".venv" / "bin"
    binaries.mkdir(parents=True)
    installed = Path(sys.prefix)
    (binaries.parent / "pyvenv.cfg").symlink_to(installed / "pyvenv.cfg")
    (binaries.parent / "lib").symlink_to(installed / "lib", target_is_directory=True)
    for name in ("python", "onepipeline", "onetaskgraph"):
        (binaries / name).symlink_to(Path(sys.executable).parent / name)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = tmp_path / "result.json"
    environment = {**os.environ, "ONEBUDGETSPEC_RESULT": str(result), "TMPDIR": str(scratch)}
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
                    answers["plan_budgets"]["sizing"] = 42
                case _:
                    for budget in answers["budgets"]:
                        budget["file_change"] = "none"
            fixture.write_text(json.dumps(answers), encoding="utf-8")
    ran = subprocess.run(
        [str(script)], cwd=root, env=environment, capture_output=True, text=True, timeout=120
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
    """Nothing that measures `gate-time`, or checks a push against it, restates the number."""
    budget = next(entry for entry in _root()["budgets"] if entry["id"] == identifier)
    number = re.compile(rf"(?<![\d.]){re.escape(f'{budget["threshold"]:g}')}(?![\d])")

    restating = [
        relative
        for relative in (
            *MEASURING,
            "scripts/budget-cycle-time.sh",
            "orchestrator/follow_up_tickets.py",
            "templates/follow-up-task.md.j2",
        )
        if number.search((REPO_ROOT / relative).read_text(encoding="utf-8"))
    ]
    assert restating == [], f"{restating} restate the {identifier} threshold budgets.yaml states"


def test_a_project_declares_a_budget_target_exactly_when_it_holds_its_own_budgets_file() -> None:
    """The root project holds the root file and never the target: the hook checks that one."""
    declarations = project_declarations()
    with_target = {
        root
        for root, declared in declarations.items()
        if BUDGET_SCOPED in declared.get("targets", {})
    }
    with_file = {
        root for root in declarations if root and (REPO_ROOT / root / BUDGETS_FILE).is_file()
    }

    assert "" not in with_target, "the root project declares a budget target"
    assert with_target == with_file, (
        f"projects with a budget target: {sorted(with_target)}; projects holding their own "
        f"{BUDGETS_FILE}: {sorted(with_file)}"
    )


def test_the_direction_vocabulary_and_file_name_are_the_installed_librarys() -> None:
    """The template's and `plan_budgets.py`'s copies, against `onebudgetspec schema`."""
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
    assert list(Direction) == directions
    assert file_name == BUDGETS_FILE
