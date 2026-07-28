"""E2E proof that the llmlint tier's verdict is memoized by its cached Nx target.

The judge itself is non-deterministic, so `just lint-llm-diff` routes through the
cached `workspace:lint-llm-diff` target: an unchanged tree judged against an
unchanged base replays the recorded verdict instead of rolling the dice again.
These journeys drive the real recipe, the real `scripts/nx.sh`, the real Nx target
definition, and the real `llmlint config` resolution in a throwaway copy of this
repository. Only the billed judge run is faked — the same boundary the rest of the
e2e suite fakes — so `llmlint --diff` is counted rather than paid for, while
`llmlint config` still merges a real plugin off disk.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PASS_VERDICT = "fake-judge: 16 passed, 0 failed"
FAIL_VERDICT = "fake-judge: 15 passed, 1 failed"
FAIL_FINDING = "fake-judge finding: robust_shell in scripts/llmlint-diff.sh"
CACHE_HIT_MARKER = "Nx read the output from the cache"

pytestmark = pytest.mark.skipif(
    shutil.which("llmlint") is None,
    reason="llmlint resolves the judge configuration this cache key is built from; "
    "run 'just setup-llmlint'",
)


@dataclass(frozen=True)
class Workspace:
    """A throwaway checkout wired to count judge runs instead of paying for them."""

    root: Path
    plugin: Path
    judge_log: Path
    env: dict[str, str]

    def lint(self, base: str, *nx_args: str, **overrides: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["just", "lint-llm-diff", base, *nx_args],
            cwd=self.root,
            env={**self.env, **overrides},
            check=False,
            text=True,
            capture_output=True,
        )

    def judge_runs(self) -> int:
        return len(self.judge_log.read_text().splitlines())

    def commit(self, message: str, *, allow_empty: bool = False) -> str:
        empty = ["--allow-empty"] if allow_empty else []
        self._git("add", "-A")
        self._git("commit", "-q", "-m", message, *empty)
        return self.head()

    def head(self) -> str:
        return self._git("rev-parse", "HEAD").strip()

    def _git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-c", "user.name=e2e", "-c", "user.email=e2e@invalid", *args],
            cwd=self.root,
            check=True,
            text=True,
            capture_output=True,
        ).stdout


def _copy_checkout(destination: Path) -> None:
    """Copy exactly the files Nx would hash: everything git would commit from here.

    Ignored state — live run directories with their channel FIFOs, node_modules,
    the virtualenv, Nx's own scratch — is deliberately left behind.
    """
    listing = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    for relative in filter(None, listing.split("\0")):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target, follow_symlinks=False)


def _write_fake_judge(directory: Path, real_llmlint: str) -> None:
    """Install an `llmlint` that counts `--diff` runs but resolves config for real."""
    directory.mkdir(parents=True, exist_ok=True)
    fake = directory / "llmlint"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ ${1:-} == "--version" ]]; then\n'
        '  echo "llmlint ${FAKE_LLMLINT_VERSION:-0.0.0-e2e}"\n'
        "  exit 0\n"
        "fi\n"
        'if [[ ${1:-} == "config" ]]; then\n'
        f'  exec "{real_llmlint}" "$@"\n'
        "fi\n"
        'printf "%s\\n" "$*" >>"$FAKE_LLMLINT_LOG"\n'
        "if [[ ${FAKE_LLMLINT_EXIT:-0} != 0 ]]; then\n"
        f'  echo "{FAIL_FINDING}"\n'
        f'  echo "{FAIL_VERDICT}"\n'
        '  exit "$FAKE_LLMLINT_EXIT"\n'
        "fi\n"
        f'echo "{PASS_VERDICT}"\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    root = tmp_path / "checkout"
    _copy_checkout(root)
    (root / "node_modules").symlink_to(ROOT / "node_modules", target_is_directory=True)

    # A plugin outside the tree: no file input can see it, so only the judge
    # configuration fingerprint can notice when its rules change.
    plugin = tmp_path / "external-plugin.yml"
    plugin.write_text(
        "version: 1\nrules:\n"
        "  - name: plugin_rule\n"
        "    description: The change documents every new operator entry point.\n",
        encoding="utf-8",
    )
    (root / "llmlint.yml").write_text(
        f'files:\n  exclude:\n    - "**/.git/**"\nplugins:\n  - "{plugin}"\n'
        "rules:\n  - name: local_rule\n"
        "    description: The change keeps every touched shell script POSIX-safe.\n",
        encoding="utf-8",
    )

    binaries = tmp_path / "bin"
    real_llmlint = shutil.which("llmlint")
    assert real_llmlint is not None
    _write_fake_judge(binaries, real_llmlint)

    judge_log = tmp_path / "judge-runs.log"
    judge_log.write_text("", encoding="utf-8")
    env = {
        **os.environ,
        "PATH": f"{binaries}{os.pathsep}{os.environ['PATH']}",
        # Isolate both the Nx cache (scripts/nx.sh roots it here) and llmlint's own
        # plugin cache from the developer's real ones.
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "FAKE_LLMLINT_LOG": str(judge_log),
        # Reuse this repository's already-synced environment rather than building
        # the throwaway copy as a distinct project.
        "UV_NO_SYNC": "1",
        "UV_PROJECT_ENVIRONMENT": str(ROOT / ".venv"),
    }
    workspace = Workspace(root=root, plugin=plugin, judge_log=judge_log, env=env)
    workspace._git("init", "-q")
    workspace.commit("checkout under test")
    return workspace


def test_unchanged_tree_and_base_replays_the_recorded_verdict(workspace: Workspace) -> None:
    base = workspace.head()

    first = workspace.lint(base)
    second = workspace.lint(base)

    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode == 0, second.stdout + second.stderr
    assert workspace.judge_runs() == 1
    assert PASS_VERDICT in first.stdout
    assert PASS_VERDICT in second.stdout
    assert CACHE_HIT_MARKER in second.stdout


def test_changed_source_reruns_the_judge(workspace: Workspace) -> None:
    base = workspace.head()
    workspace.lint(base)

    changed = workspace.root / "orchestrator/dispatch.py"
    changed.write_text(changed.read_text() + "\n# judged again\n", encoding="utf-8")
    second = workspace.lint(base)

    assert second.returncode == 0, second.stdout + second.stderr
    assert workspace.judge_runs() == 2
    assert CACHE_HIT_MARKER not in second.stdout


def test_advanced_base_reruns_the_judge_and_then_caches_per_base(workspace: Workspace) -> None:
    original = workspace.head()
    workspace.lint(original)

    # Identical tree, advanced base: only the resolved base commit differs, so a
    # hit here would replay a verdict computed against a different comparison.
    advanced = workspace.commit("advance the base", allow_empty=True)
    assert advanced != original
    moved = workspace.lint(advanced)
    repeated = workspace.lint(advanced)

    assert workspace.judge_runs() == 2
    assert CACHE_HIT_MARKER not in moved.stdout
    assert CACHE_HIT_MARKER in repeated.stdout


def test_changed_rule_configuration_reruns_the_judge(workspace: Workspace) -> None:
    base = workspace.head()
    workspace.lint(base)

    config = workspace.root / "llmlint.yml"
    config.write_text(
        config.read_text().replace("POSIX-safe", "POSIX-safe and shellcheck-clean"),
        encoding="utf-8",
    )
    second = workspace.lint(base)

    assert workspace.judge_runs() == 2
    assert CACHE_HIT_MARKER not in second.stdout


def test_changed_plugin_rule_source_reruns_the_judge(workspace: Workspace) -> None:
    base = workspace.head()
    workspace.lint(base)

    # The plugin lives outside the checkout: the tree Nx hashes is byte-identical.
    workspace.plugin.write_text(
        workspace.plugin.read_text().replace("operator entry point", "operator entry point twice"),
        encoding="utf-8",
    )
    second = workspace.lint(base)

    assert workspace.judge_runs() == 2
    assert CACHE_HIT_MARKER not in second.stdout


def test_changed_llmlint_version_reruns_the_judge(workspace: Workspace) -> None:
    base = workspace.head()
    workspace.lint(base, FAKE_LLMLINT_VERSION="0.3.25")

    second = workspace.lint(base, FAKE_LLMLINT_VERSION="0.4.0")

    assert workspace.judge_runs() == 2
    assert CACHE_HIT_MARKER not in second.stdout


def test_failing_verdict_is_never_replayed_as_a_pass(workspace: Workspace) -> None:
    base = workspace.head()

    first = workspace.lint(base, FAKE_LLMLINT_EXIT="1")
    second = workspace.lint(base, FAKE_LLMLINT_EXIT="1")

    assert first.returncode != 0
    assert second.returncode != 0
    assert workspace.judge_runs() == 2
    for result in (first, second):
        report = result.stdout + result.stderr
        assert FAIL_FINDING in report
        assert FAIL_VERDICT in report


def test_skip_nx_cache_forces_a_fresh_judge_run(workspace: Workspace) -> None:
    base = workspace.head()
    workspace.lint(base)

    forced = workspace.lint(base, "--skip-nx-cache")

    assert forced.returncode == 0, forced.stdout + forced.stderr
    assert workspace.judge_runs() == 2
    assert CACHE_HIT_MARKER not in forced.stdout
