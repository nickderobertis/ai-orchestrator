"""A project's own budgets, run by `just check`'s affected selection and nothing wider.

A project that holds a `budgets.yaml` declares the `budget` target, which `nx.json`'s
`targetDefaults` defines: the installed onebudgetspec checks that one file, excluding every
`onepipeline`-labelled budget, cached on the project's inputs like the other targets. `just
check` runs it among the targets its diff selection picks, so a project's budgets run only
when a change touches that project; and `workspace:validate-budgets`, among the targets
the recipe runs whatever the diff, validates every budgets file in the tree.

These journeys build a scratch Nx workspace — this checkout's `nx.json`, root
`project.json`, `scripts/` and locked installs, and two projects of their own, each
registering a budget whose command records that it ran and reports the value a file of
that project holds — and drive it through the real `scripts/nx-selection.sh` and
`scripts/nx.sh`, with the target lists the recipe names (`tests/nx_inputs.py`, which
`tests/test_nx_cache_scope.py` holds to the recipe).

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

llmlint: ignore-file[tests_mirror_real_usage] The user-facing surface over these is `just
check`, which cannot be driven from inside the suite it runs: its last phase builds the
cross-worktree cache fixture this scratch workspace does not carry. Its sequencing and
aggregate verdict, the phase that always runs included, are driven through the real recipe
in `tests/e2e/test_workspace_contract_e2e.py`, and `tests/test_nx_cache_scope.py` holds
the target lists read here to the recipe's own; these journeys take the other half — what
real Nx runs for that selection, as `tests/e2e/test_nx_cache_scope_e2e.py` does.
"""

from __future__ import annotations

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
#: and the scripts the recipe runs.
COPIED = ("nx.json", "project.json", "package.json", "bun.lock", "pyproject.toml", "uv.lock")
PROJECTS = ("alpha", "beta")
THRESHOLD = 10
#: What Nx prints when it replays a target rather than running it.
CACHE_HIT = "read the output from the cache"
#: The remote the scratch workspace's base is published to; never fetched.
ORIGIN = "https://example.invalid/budget-target.git"


def _budgets(project: str, marker: Path) -> dict:
    """A project's own file: one budget measured from `value`, one labelled `onepipeline`."""
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
        path = self.root / project / "value"
        previous = path.stat().st_mtime_ns
        path.write_text(f"{value}\n", encoding="utf-8")
        stamp = max(path.stat().st_mtime_ns, (previous // 1_000_000_000 + 1) * 1_000_000_000)
        os.utime(path, ns=(stamp, stamp))


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    root = tmp_path / "workspace"
    root.mkdir()
    for relative in COPIED:
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
    marker = tmp_path / "ran"
    for project in PROJECTS:
        (root / project).mkdir()
        (root / project / "project.json").write_text(
            f'{{"name": "{project}", "projectType": "library", "targets": {{"budget": {{}}}}}}\n',
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
    assert workspace.ran() == [], "validation ran a budget's command"
