"""A project's own budgets, run by `just check`: deterministic ones by its diff, host ones always.

A project that holds a `budgets.yaml` declares the `budgets` and `budgets-host` targets,
which `nx.json`'s `targetDefaults` defines, each checking that one file with the installed
onebudgetspec and excluding every `onepipeline`-labelled budget. `budgets` skips the
budgets labelled `host` — the `elapsed` ones and those reading the host — and is cached on
the project's tree, its dependencies' production inputs and the onebudgetspec pin; `just
check` runs it among the targets its diff selection picks, so a project's deterministic
budgets run only when a change touches that project. `budgets-host` runs exactly the `host`
ones, uncached, among the targets the recipe runs whatever the diff, beside
`workspace:validate-budgets`, which validates every budgets file in the tree.

These journeys build a scratch Nx workspace — this checkout's `nx.json`, root
`project.json`, `scripts/` and locked installs, and two projects of their own, each
registering a budget whose command records that it ran and reports the value a file of
that project holds, and a `host` one timed as it runs. The focused cache journeys drive
real Nx; the unconditional-host journey drives the shipped `just check` recipe, including
its real cross-worktree cache check, without registering the parent suite as a target.

llmlint: ignore-file[e2e_not_mocked] Nothing is substituted: the two projects are the
subject, and every command they register is real; the only thing standing in is what a
budget measures, which is a file the journey writes so that each measurement is known.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] What these spend is a
scratch workspace of a few files and a handful of Nx invocations over it — no launch and
no paid turn — and what they read is `nx.json`, `project.json` and `scripts/`, which the
code tier's key already covers.

llmlint: ignore-file[shell_test_tiers_stay_split] For the same cost: these journeys spend
seconds, against a scratch workspace and nothing outside it, so a test project of their own
would narrow the key of a few seconds of work at the price of a catalog entry in each of
the places `tests/nx_inputs.py` and `tests/test_nx_cache_scope.py` hold every project to.

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
from nx_inputs import SELECTED_TARGETS, UNCONDITIONAL_TARGETS
from nx_workspace import NODE_MODULES, WORKSPACE_INSTALL_MARKS
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

pytestmark = [*WORKSPACE_INSTALL_MARKS]

#: What the scratch workspace takes from this checkout: the Nx configuration and the root
#: project the validation tier belongs to, the locked installs `scripts/nx.sh` heals from,
#: the onebudgetspec pin the `budgets` key names, and the scripts the recipe runs.
COPIED = (
    "justfile",
    "nx.json",
    "project.json",
    "package.json",
    "bun.lock",
    "pyproject.toml",
    "uv.lock",
    "config/onebudgetspec.version",
)
PROJECTS = ("alpha", "beta")
#: The project `alpha` depends on, whose production inputs its budgets are keyed on.
DEPENDENCY = "lib"
THRESHOLD = 10
#: What Nx prints when it replays a target rather than running it.
CACHE_HIT = "read the output from the cache"
#: The remote the scratch workspace's base is published to; never fetched.
ORIGIN = "https://example.invalid/budget-target.git"


def _budgets(project: str, marker: Path) -> dict:
    """A project's own file: one budget measured from `value`, one `host`, one `onepipeline`."""
    return {
        "schema_version": 1,
        "budgets": [
            {
                "id": f"{project}-size",
                "measure": "reported",
                "command": [
                    "sh",
                    "-c",
                    f'echo {project}-size >>"{marker}"; '
                    'printf \'{"value": %s}\' "$(cat value)" >"$ONEBUDGETSPEC_RESULT"',
                ],
                "unit": "bytes",
                "direction": "max",
                "threshold": THRESHOLD,
            },
            {
                "id": f"{project}-wall",
                "labels": ["host"],
                "measure": "elapsed",
                "command": ["sh", "-c", f'echo {project}-wall >>"{marker}"'],
                "unit": "seconds",
                "direction": "max",
                "threshold": THRESHOLD,
            },
            {
                "id": f"{project}-telemetry",
                "labels": ["onepipeline"],
                "measure": "elapsed",
                "command": ["sh", "-c", f'echo {project}-telemetry >>"{marker}"'],
                "unit": "seconds",
                "direction": "max",
                "threshold": THRESHOLD,
            },
        ],
    }


@dataclass(frozen=True)
class Workspace:
    """The scratch workspace, its own Nx cache, and the file each budget run writes to."""

    root: Path
    cache: Path
    marker: Path

    def environment(self) -> dict[str, str]:
        dropped = {
            "NX_SKIP_NX_CACHE",
            "NX_DISABLE_NX_CACHE",
            "ORCHESTRATOR_COMPARISON_BASE",
            "ORCHESTRATOR_COMPARISON_REMOTE",
            "ONEVCS_COMPARISON_BASE",
            "ONEVCS_COMPARISON_REMOTE",
        }
        return {
            **{key: value for key, value in os.environ.items() if key not in dropped},
            "XDG_CACHE_HOME": str(self.cache),
            "AI_ORCHESTRATOR_NX_SHOW_OUTPUT": "1",
            "UV_NO_SYNC": "1",
            "UV_PROJECT_ENVIRONMENT": str(REPO_ROOT / ".venv"),
        }

    def _run(self, *argv: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(argv),
            cwd=self.root,
            env=self.environment(),
            text=True,
            capture_output=True,
            timeout=e2e_timeout(300),
            check=False,
        )

    def check(self) -> subprocess.CompletedProcess[str]:
        """The recipe's diff-selected phase: its selection, and the targets it names."""
        selection = self._run("./scripts/nx-selection.sh")
        assert selection.returncode == 0, selection.stdout + selection.stderr
        assert selection.stdout.split()[0] == "affected", selection.stdout
        return self._run(
            "./scripts/nx.sh", *selection.stdout.split(), "-t", ",".join(SELECTED_TARGETS)
        )

    def unconditional(self) -> subprocess.CompletedProcess[str]:
        """The recipe's phase that runs whatever the diff."""
        return self._run("./scripts/nx.sh", "run-many", "-t", ",".join(UNCONDITIONAL_TARGETS))

    def ran(self) -> list[str]:
        if not self.marker.exists():
            return []
        return self.marker.read_text(encoding="utf-8").splitlines()

    def measure(self, project: str, value: int) -> None:
        """Write what the budget measures, stamped in a later second than the last write.

        Nx 23's file-hash archive reuses a file's stored hash while its size and its
        modification time in whole seconds are unchanged, so a same-size rewrite inside
        the second of the write Nx last hashed is invisible to it and the task replays.
        A person's edit lands seconds later; the journey's lands within a second when two
        checks run fast, so it is stamped the way an edit would be.
        """
        self.write(f"{project}/value", f"{value}\n")

    def write(self, relative: str, text: str) -> None:
        """Replace a file of the workspace, stamped as `measure` explains."""
        path = self.root / relative
        previous = path.stat().st_mtime_ns
        path.write_text(text, encoding="utf-8")
        stamp = max(path.stat().st_mtime_ns, (previous // 1_000_000_000 + 1) * 1_000_000_000)
        os.utime(path, ns=(stamp, stamp))


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
    (root / ".gitignore").write_text("node_modules\n.nx\n.logs\n", encoding="utf-8")
    (root / "node_modules").symlink_to(NODE_MODULES, target_is_directory=True)
    shutil.copytree(REPO_ROOT / "tests/fixtures/nx-cache", root / "tests/fixtures/nx-cache")
    marker = tmp_path / "ran"
    (root / DEPENDENCY).mkdir()
    (root / DEPENDENCY / "project.json").write_text(
        f'{{"name": "{DEPENDENCY}", "projectType": "library"}}\n', encoding="utf-8"
    )
    (root / DEPENDENCY / "source").write_text("measured\n", encoding="utf-8")
    for project in PROJECTS:
        (root / project).mkdir()
        (root / project / "project.json").write_text(
            f'{{"name": "{project}", "projectType": "library", '
            f'"implicitDependencies": {json.dumps([DEPENDENCY] if project == "alpha" else [])}, '
            '"targets": {"budgets": {}, "budgets-host": {}}}\n',
            encoding="utf-8",
        )
        (root / project / "budgets.yaml").write_text(
            yaml.safe_dump(_budgets(project, marker)), encoding="utf-8"
        )
        (root / project / "value").write_text("5\n", encoding="utf-8")
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
    return Workspace(root=root, cache=tmp_path / "cache", marker=marker)


def test_only_the_touched_projects_budgets_run_and_replay_until_its_inputs_move(
    workspace: Workspace,
) -> None:
    """Selected by the diff, cached on the project's inputs, labelled budgets never run."""
    workspace.measure("alpha", 6)

    first = workspace.check()

    assert first.returncode == 0, first.stdout + first.stderr
    assert workspace.ran() == ["alpha-size"], (
        "only the touched project's own budget should run, and never an onepipeline one: "
        f"{workspace.ran()}"
    )
    assert "budget alpha-size: actual 6 bytes" in first.stdout, first.stdout

    replayed = workspace.check()

    assert replayed.returncode == 0, replayed.stdout + replayed.stderr
    assert CACHE_HIT in replayed.stdout, replayed.stdout
    assert workspace.ran() == ["alpha-size"], "an unchanged project's budget ran again"

    workspace.measure("alpha", 8)
    remeasured = workspace.check()

    assert remeasured.returncode == 0, remeasured.stdout + remeasured.stderr
    assert workspace.ran() == ["alpha-size", "alpha-size"], workspace.ran()
    assert "budget alpha-size: actual 8 bytes" in remeasured.stdout, remeasured.stdout
    assert "actual 6 bytes" not in remeasured.stdout, remeasured.stdout


def test_a_host_budget_measures_on_every_check_while_a_deterministic_one_replays(
    workspace: Workspace,
) -> None:
    """Twice over an unchanged tree: the `elapsed` budget runs both times, `reported` once."""
    workspace.measure("alpha", 6)
    selected: list[subprocess.CompletedProcess[str]] = []

    for _ in range(2):
        selected.append(workspace.check())
        assert selected[-1].returncode == 0, selected[-1].stdout + selected[-1].stderr
        always = workspace.unconditional()
        assert always.returncode == 0, always.stdout + always.stderr

    ran = workspace.ran()
    assert ran.count("alpha-size") == 1, f"the deterministic budget did not replay: {ran}"
    assert CACHE_HIT in selected[1].stdout, selected[1].stdout
    assert ran.count("alpha-wall") == 2, f"the host budget was not measured each time: {ran}"
    assert ran.count("beta-wall") == 2, f"an untouched project's host budget was skipped: {ran}"
    assert not [entry for entry in ran if entry.endswith("-telemetry")], ran


@pytest.mark.parametrize(
    "moved",
    [f"{DEPENDENCY}/source", "config/onebudgetspec.version", "uv.lock"],
    ids=["dependency-production-input", "checker-pin", "checker-lock"],
)
def test_a_deterministic_budget_remeasures_when_what_it_measures_or_its_checker_moves(
    workspace: Workspace, moved: str
) -> None:
    """The key beyond the project's own tree: its dependencies, and the checker's pin."""
    workspace.measure("alpha", 6)
    for _ in range(2):
        replayed = workspace.check()
        assert replayed.returncode == 0, replayed.stdout + replayed.stderr
    assert CACHE_HIT in replayed.stdout, replayed.stdout
    assert workspace.ran() == ["alpha-size"], workspace.ran()

    workspace.write(moved, (workspace.root / moved).read_text(encoding="utf-8") + "\n")
    remeasured = workspace.check()

    assert remeasured.returncode == 0, remeasured.stdout + remeasured.stderr
    assert workspace.ran().count("alpha-size") == 2, (
        f"moving {moved} replayed alpha's budgets rather than measuring them: {workspace.ran()}"
    )


def test_an_over_budget_result_in_the_touched_project_fails_the_check(
    workspace: Workspace,
) -> None:
    workspace.measure("alpha", THRESHOLD + 5)

    over = workspace.check()

    assert over.returncode != 0, over.stdout + over.stderr
    assert "budget alpha-size:" in over.stdout + over.stderr
    assert "— over" in over.stdout + over.stderr, over.stdout + over.stderr
    assert workspace.ran() == ["alpha-size"], workspace.ran()


def test_the_validation_tier_refuses_a_malformed_nested_budgets_file(
    workspace: Workspace,
) -> None:
    """Whatever the diff selects, a budgets file anywhere that will not parse is refused."""
    passed = workspace.unconditional()
    assert passed.returncode == 0, passed.stdout + passed.stderr

    nested = workspace.root / "beta" / "budgets.yaml"
    document = yaml.safe_load(nested.read_text(encoding="utf-8"))
    document["budgets"][0]["thresold"] = THRESHOLD
    nested.write_text(yaml.safe_dump(document), encoding="utf-8")

    refused = workspace.unconditional()

    assert refused.returncode != 0, refused.stdout + refused.stderr
    reported = refused.stdout + refused.stderr
    assert "beta/budgets.yaml" in reported and "thresold" in reported, reported
    measured = [entry for entry in workspace.ran() if not entry.endswith("-wall")]
    assert measured == [], "validation ran a budget's command"


# The fixture also reads uv.lock and the checker pin, outside recipeWorkspace,
# so this journey belongs to the broader code-keyed tier.
def test_real_check_measures_host_budgets_when_no_project_is_touched(
    workspace: Workspace,
) -> None:
    """A file outside all project roots still gets every host budget checked."""
    (workspace.root / "operator-note.txt").write_text("outside projects\n", encoding="utf-8")

    for _ in range(2):
        checked = workspace._run("just", "check")
        assert checked.returncode == 0, checked.stdout + checked.stderr
        assert "all deterministic checks passed" in checked.stdout, checked.stdout

    ran = workspace.ran()
    assert ran.count("alpha-wall") == 2, ran
    assert ran.count("beta-wall") == 2, ran
    assert not [entry for entry in ran if not entry.endswith("-wall")], ran
