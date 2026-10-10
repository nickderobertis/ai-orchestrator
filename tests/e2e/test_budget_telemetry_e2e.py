"""A budget read from the telemetry its project's test target recorded, enforced by Nx.

A cost figure here is a budget whose command analyses what a test recorded during its
normal run (`tests/budget_telemetry.py`), never a threshold a test asserts. These journeys
prove that arrangement end to end in a scratch Nx workspace taking this checkout's
`nx.json` — so its project's `budgets` target is the default `nx.json` ships — with one
project whose cached test target records parts through the real recorder, and whose
`budgets` target depends on it and runs the real analysis through the installed
onebudgetspec. Over its threshold the functional test passes while the budget fails,
naming its figure and every part of its breakdown; within it both pass; with nothing
recorded the budget fails rather than reporting a default; and a test result replayed
from the cache restores exactly the telemetry the budget then reads.

llmlint: ignore-file[e2e_not_mocked] Nothing is substituted: the scratch project is the
subject, and its test target, the recorder, the analysis, Nx and onebudgetspec all run
for real; what the project measures is a file the journey writes, so each figure is known.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] A scratch workspace of a
few files and a handful of Nx invocations over it — no launch and no paid turn — which is
what `tests/e2e/test_budget_target_e2e.py` beside it spends for the same reason.

llmlint: ignore-file[shell_test_tiers_stay_split] For the same cost: these journeys spend
seconds against a scratch workspace and nothing outside it, so a test project of their own
would narrow the key of a few seconds of work at the price of a catalog entry in each of
the places `tests/nx_inputs.py` and `tests/test_nx_cache_scope.py` hold every project to,
as `tests/e2e/test_budget_target_e2e.py` says of the same journeys' shape.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml
from nx_workspace import TOOLCHAIN_WRITER_MARKS, WORKSPACE_INSTALL_MARKS, private_node_modules
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: A writer: `scripts/nx.sh` heals the install of the scratch workspace it runs in, whose
#: `node_modules` is that workspace's own.
pytestmark = [*WORKSPACE_INSTALL_MARKS, *TOOLCHAIN_WRITER_MARKS]

#: What the scratch workspace takes from this checkout: the Nx configuration and root
#: project, the locked installs `scripts/nx.sh` heals from, the onebudgetspec pin, and the
#: recorder and analysis every budget here runs.
COPIED = (
    "justfile",
    "nx.json",
    "project.json",
    "package.json",
    "bun.lock",
    "pyproject.toml",
    "uv.lock",
    "config/onebudgetspec.version",
    "tests/budget_telemetry.py",
)
PROJECT = "alpha"
BUDGET = "alpha-points"
#: Where the project's test target records, which its `budgets` target reads.
TELEMETRY = f".telemetry/{PROJECT}/test"
#: The parts the project's test records: two that make the figure, one that explains it.
PARTS = {"phase reads": 3, "phase writes": 4}
EXPLAINING = ("per-step journey, not in the total: copy", 2)
MEASURED = sum(PARTS.values())
WITHIN = MEASURED + 3
BELOW = MEASURED - 2
#: How Nx names the test task it replayed from its cache rather than ran.
REPLAYED_TEST = f"> nx run {PROJECT}:test  [local cache]"
ORIGIN = "https://example.invalid/budget-telemetry.git"

#: The project's one test: it records each part through the recorder, as a journey does.
RECORDING_TEST = f"""\
import json
from pathlib import Path

import budget_telemetry


def test_records_what_it_measured():
    parts = json.loads(Path(__file__).with_name("parts.json").read_text(encoding="utf-8"))
    for label, value in parts["counted"].items():
        assert budget_telemetry.record("{BUDGET}", label, value, unit="points", requests=value)
    for label, value in parts["explaining"].items():
        assert budget_telemetry.record(
            "{BUDGET}", label, value, unit="points", counted=False, requests=value
        )
"""


def _project() -> dict:
    """The project's targets: a cached recording test, and `budgets` depending on it."""
    return {
        "name": PROJECT,
        "projectType": "library",
        "targets": {
            "test": {
                "command": f"rm -rf {TELEMETRY} && BUDGET_TELEMETRY_DIR={TELEMETRY} "
                f"uv run pytest {PROJECT} -p no:cacheprovider",
                "options": {"cwd": "."},
                "cache": True,
                "outputs": [f"{{workspaceRoot}}/{TELEMETRY}"],
                "inputs": [
                    "{projectRoot}/test_alpha.py",
                    "{projectRoot}/parts.json",
                    "{workspaceRoot}/tests/budget_telemetry.py",
                ],
            },
            "budgets": {
                "dependsOn": ["test"],
                "options": {"cwd": ".", "env": {"BUDGET_TELEMETRY_SOURCES": TELEMETRY}},
                "inputs": [
                    "default",
                    "{workspaceRoot}/tests/budget_telemetry.py",
                    {"dependentTasksOutputFiles": "**/*.json"},
                ],
            },
        },
    }


def _budgets(threshold: int) -> dict:
    return {
        "schema_version": 1,
        "budgets": [
            {
                "id": BUDGET,
                "description": (
                    "Points the scratch project's journey records. It protects that "
                    "journey's recorded cost."
                ),
                "measure": "reported",
                "command": [
                    "uv",
                    "run",
                    "--directory",
                    "..",
                    "python",
                    "-m",
                    "tests.budget_telemetry",
                ],
                "unit": "points",
                "direction": "max",
                "threshold": threshold,
            }
        ],
    }


@dataclass(frozen=True)
class Workspace:
    """The scratch workspace and its own Nx cache."""

    root: Path
    cache: Path

    def environment(self) -> dict[str, str]:
        dropped = {
            "NX_SKIP_NX_CACHE",
            "NX_DISABLE_NX_CACHE",
            "BUDGET_TELEMETRY_DIR",
            "BUDGET_TELEMETRY_SOURCES",
            "BUDGET_TELEMETRY_MEASURE",
        }
        return {
            **{key: value for key, value in os.environ.items() if key not in dropped},
            "XDG_CACHE_HOME": str(self.cache),
            "AI_ORCHESTRATOR_NX_SHOW_OUTPUT": "1",
            "UV_NO_SYNC": "1",
            "UV_PROJECT_ENVIRONMENT": str(REPO_ROOT / ".venv"),
        }

    def nx(self, target: str) -> subprocess.CompletedProcess[str]:
        """One project target through this checkout's real Nx wrapper."""
        return subprocess.run(
            ["./scripts/nx.sh", "run", f"{PROJECT}:{target}"],
            cwd=self.root,
            env=self.environment(),
            text=True,
            capture_output=True,
            timeout=e2e_timeout(300),
            check=False,
        )

    def check(self) -> subprocess.CompletedProcess[str]:
        """The deterministic tier as `just check` runs it over the diff from the base."""
        return subprocess.run(
            ["just", "check"],
            cwd=self.root,
            env=self.environment(),
            text=True,
            capture_output=True,
            timeout=e2e_timeout(600),
            check=False,
        )

    def write(self, relative: str, text: str) -> None:
        """Replace a file, stamped a second past its last write so Nx re-hashes it.

        Nx's file-hash archive reuses a stored hash while a file's size and its
        modification time in whole seconds are unchanged.
        """
        path = self.root / relative
        previous = path.stat().st_mtime_ns if path.exists() else 0
        path.write_text(text, encoding="utf-8")
        stamp = max(path.stat().st_mtime_ns, (previous // 1_000_000_000 + 1) * 1_000_000_000)
        os.utime(path, ns=(stamp, stamp))

    def threshold(self, value: int) -> None:
        self.write(f"{PROJECT}/budgets.yaml", yaml.safe_dump(_budgets(value), sort_keys=False))

    def records(self, counted: dict[str, int], explaining: dict[str, int]) -> None:
        parts = {"counted": counted, "explaining": explaining}
        self.write(f"{PROJECT}/parts.json", json.dumps(parts))


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    root = tmp_path / "workspace"
    root.mkdir()
    for relative in COPIED:
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, root / relative)
    listing = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "scripts"],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    for relative in filter(None, listing.split("\0")):
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, root / relative)
    (root / ".gitignore").write_text(
        "node_modules\n.nx\n.logs\n.telemetry\n__pycache__\n", encoding="utf-8"
    )
    private_node_modules(root)
    shutil.copytree(REPO_ROOT / "tests/fixtures/nx-cache", root / "tests/fixtures/nx-cache")
    (root / PROJECT).mkdir()
    (root / PROJECT / "project.json").write_text(json.dumps(_project(), indent=2), "utf-8")
    (root / PROJECT / "test_alpha.py").write_text(RECORDING_TEST, encoding="utf-8")
    scratch = Workspace(root=root, cache=tmp_path / "cache")
    scratch.records(PARTS, dict([EXPLAINING]))
    scratch.threshold(WITHIN)
    for args in (
        ("init", "-q"),
        ("config", "user.name", "test"),
        ("config", "user.email", "test.invalid"),
        ("remote", "add", "origin", ORIGIN),
        ("add", "-A"),
        ("-c", "commit.gpgsign=false", "commit", "-qm", "base"),
        ("update-ref", "refs/remotes/origin/main", "HEAD"),
    ):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    return scratch


def _failed_tasks(reported: str) -> list[str]:
    """Every task Nx lists under a `Failed tasks:` heading of the output."""
    failed: list[str] = []
    for block in reported.split("Failed tasks:")[1:]:
        listed = block.lstrip("\n").split("\n\n", 1)[0]
        failed += [line.strip().removeprefix("- ") for line in listed.splitlines()]
    return failed


def _breakdown_lines() -> list[str]:
    """The detail lines onebudgetspec prints under the budget's result, each indented."""
    label, value = EXPLAINING
    counted = [f"  {name}: {points} points, {points} requests" for name, points in PARTS.items()]
    return [*counted, f"  {label}: {value} points, {value} requests"]


def test_over_its_threshold_the_test_passes_and_the_budget_fails_naming_its_breakdown(
    workspace: Workspace,
) -> None:
    """Below the recorded figure, `just check` fails on the budget alone; restored, it passes."""
    workspace.threshold(BELOW)

    over = workspace.check()

    reported = over.stdout + over.stderr
    assert over.returncode != 0, reported
    assert _failed_tasks(reported) == [f"{PROJECT}:budgets"], reported
    assert f"budget {BUDGET}: actual {MEASURED} points, budget {BELOW} points" in reported, reported
    assert "— over" in reported, reported
    for line in _breakdown_lines():
        assert line in reported.splitlines(), (line, reported)
    tested = workspace.nx("test")
    assert tested.returncode == 0, tested.stdout + tested.stderr

    workspace.threshold(WITHIN)
    restored = workspace.check()

    reported = restored.stdout + restored.stderr
    assert restored.returncode == 0, reported
    within = workspace.nx("budgets")
    reported = within.stdout + within.stderr
    assert within.returncode == 0, reported
    assert f"budget {BUDGET}: actual {MEASURED} points, budget {WITHIN} points" in reported
    assert "— within" in reported, reported


def test_a_budget_whose_test_recorded_nothing_fails_rather_than_reporting_a_default(
    workspace: Workspace,
) -> None:
    workspace.records({}, {})

    tested = workspace.nx("test")
    assert tested.returncode == 0, tested.stdout + tested.stderr
    missing = workspace.nx("budgets")

    reported = missing.stdout + missing.stderr
    assert missing.returncode != 0, reported
    assert f"budget {BUDGET}: nothing was recorded" in reported, reported
    assert "— within" not in reported, reported


def test_a_test_result_replayed_from_the_cache_restores_the_telemetry_the_budget_reads(
    workspace: Workspace,
) -> None:
    """Telemetry gone from the tree, the test replays, and the budget reads what it restored."""
    first = workspace.nx("budgets")
    assert first.returncode == 0, first.stdout + first.stderr
    shutil.rmtree(workspace.root / ".telemetry")
    workspace.threshold(BELOW)

    replayed = workspace.nx("budgets")

    reported = replayed.stdout + replayed.stderr
    assert REPLAYED_TEST in reported.splitlines(), reported
    assert (workspace.root / TELEMETRY / BUDGET).is_dir(), "the cache restored no telemetry"
    assert replayed.returncode != 0, reported
    assert f"budget {BUDGET}: actual {MEASURED} points, budget {BELOW} points" in reported
    for line in _breakdown_lines():
        assert line in reported.splitlines(), (line, reported)
