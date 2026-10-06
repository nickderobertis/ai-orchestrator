"""This repository's budgets files, held to the installed onebudgetspec and to each other.

The root `budgets.yaml` is what every push of this repository is held to, and its shape is
stated here in full so a budget that moved, or one added without a decision, fails rather
than passing as itself. Its threshold has one source, the file: nothing that measures or
refuses a push restates it. A project's own `budgets.yaml` is measured by its `budget` Nx
target, which a project declares only by opting in, so the file and the target are held
together here; the root project never declares one, because the root file is the hook's to
check, once, after the gate it measures. And the vocabulary this host's plan-budgets
template and `orchestrator/plan_budgets.py` copy from onebudgetspec — the `direction`
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
import re
import subprocess
import sys
from pathlib import Path

import yaml
from nx_inputs import BUDGET_SCOPED, project_declarations

from orchestrator.plan_budgets import BUDGETS_FILE, Direction
from orchestrator.root import REPO_ROOT

ROOT_BUDGETS = REPO_ROOT / "budgets.yaml"
TEMPLATE = REPO_ROOT / "templates" / "plan-budgets.md.j2"
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


def test_the_root_file_holds_exactly_gate_time_and_the_dispatches_condition() -> None:
    document = _root()
    (gate_time,) = document["budgets"]
    threshold = gate_time.pop("threshold")
    description = gate_time.pop("description")

    assert document["schema_version"] == 1
    assert document["conditions"] == [
        {"name": "dispatches", "command": ["scripts/budget-dispatches.sh"]}
    ]
    assert gate_time == {
        "id": "gate-time",
        "measure": "reported",
        "command": ["scripts/budget-gate-time.sh"],
        "unit": "seconds",
        "direction": "max",
    }
    assert isinstance(threshold, int | float) and threshold > 0, threshold
    assert "pre-push gate" in description and "record_local_direct_gate" in description


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


def test_the_gate_time_threshold_is_stated_in_the_budgets_file_alone() -> None:
    """Nothing that measures `gate-time`, or refuses a push over it, restates the number."""
    (gate_time,) = _root()["budgets"]
    number = re.compile(rf"(?<![\d.]){re.escape(f'{gate_time["threshold"]:g}')}(?![\d])")

    restating = [
        relative
        for relative in MEASURING
        if number.search((REPO_ROOT / relative).read_text(encoding="utf-8"))
    ]
    assert restating == [], f"{restating} restate the gate-time threshold budgets.yaml states"


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
    template_directions = re.search(r"`direction` \(string, `(\w+)` or `(\w+)`\)", template)
    template_file = re.search(r"the repository-relative path of the `([^`]+)` it goes in", template)

    assert template_directions is not None and template_file is not None, (
        f"{TEMPLATE} no longer states the direction vocabulary or the file name where this "
        "test reads them"
    )
    assert list(template_directions.groups()) == directions, template_directions.group(0)
    assert template_file.group(1) == file_name, template_file.group(0)
    assert list(Direction) == directions
    assert file_name == BUDGETS_FILE
