"""E2E proof that the llmlint tier's verdict is memoized by its cached Nx target.

The judge itself is non-deterministic, so `just lint-llm-diff` routes through the
cached `workspace:lint-llm-diff` target: an unchanged tree judged against an
unchanged base replays the recorded verdict instead of rolling the dice again.
These journeys drive the real recipe, the real `scripts/nx.sh`, the real Nx target
definition, and the real `llmlint config` resolution in a throwaway copy of this
repository. Only the billed judge run is faked — the same boundary the rest of the
e2e suite fakes — so `llmlint --diff` is counted rather than paid for, while
`llmlint config` still merges a real plugin off disk.

llmlint: ignore-file[e2e_not_mocked] The judge run is this repository's paid model
boundary, faked here exactly as tests/e2e/fake_backend.py fakes the agent harness.
It is also the one thing these journeys cannot use for real: the claim under test
is that an unchanged tree yields the same verdict twice, which a non-deterministic
judge cannot demonstrate. Counting `--diff` invocations is what proves a verdict
was replayed rather than re-rolled; every other boundary — the recipe, Nx, git, and
llmlint's own config resolution — is real.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from nx_workspace import copy_checkout, requires_workspace_install

ROOT = Path(__file__).resolve().parents[2]
PASS_VERDICT = "fake-judge: 16 passed, 0 failed"
FAIL_VERDICT = "fake-judge: 15 passed, 1 failed"
FAIL_FINDING = "fake-judge finding: robust_shell in scripts/llmlint-diff.sh"
CACHE_HIT = "replayed the recorded verdict (Nx cache hit)"
CACHE_MISS = "judged this diff (Nx cache miss)"
# scripts/llmlint-verdict.sh: an unusable record, distinct from the judge's 0 and 1.
UNUSABLE_RECORD = 2

pytestmark = [
    pytest.mark.skipif(
        shutil.which("llmlint") is None,
        reason="llmlint resolves the judge configuration this cache key is built from; "
        "run 'just setup-llmlint'",
    ),
    requires_workspace_install,
]


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


def _write_fake_judge(directory: Path) -> None:
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
        '  exec "$REAL_LLMLINT" "$@"\n'
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
    copy_checkout(root)

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
    _write_fake_judge(binaries)

    judge_log = tmp_path / "judge-runs.log"
    judge_log.write_text("", encoding="utf-8")
    env = {
        **os.environ,
        "PATH": f"{binaries}{os.pathsep}{os.environ['PATH']}",
        # Isolate both the Nx cache (scripts/nx.sh roots it here) and llmlint's own
        # plugin cache from the developer's real ones.
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "FAKE_LLMLINT_LOG": str(judge_log),
        "REAL_LLMLINT": real_llmlint,
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
    assert CACHE_HIT in second.stderr
    # "Green" is a claim about one base commit, so every run names the one it
    # judged: a gate and a publication rebuild that resolve different bases are
    # answering different questions, and that has to be visible without digging.
    for result in (first, second):
        assert f"lint-llm-diff: base {base} ({base})" in result.stderr


def test_an_ambient_global_cache_skip_is_reported_and_ignored(workspace: Workspace) -> None:
    """The only supported re-judge lever is per-tier, so a global one cannot re-roll."""
    base = workspace.head()

    first = workspace.lint(base, NX_SKIP_NX_CACHE="true")
    second = workspace.lint(base, NX_DISABLE_NX_CACHE="true")

    assert workspace.judge_runs() == 1
    assert CACHE_HIT in second.stderr
    for result in (first, second):
        assert result.returncode == 0, result.stdout + result.stderr
        assert "ignoring the ambient global Nx cache skip" in result.stderr
        assert f"just lint-llm-diff {base} --skip-nx-cache" in result.stderr


def test_changed_source_reruns_the_judge(workspace: Workspace) -> None:
    base = workspace.head()
    workspace.lint(base)

    changed = workspace.root / "orchestrator/dispatch.py"
    changed.write_text(changed.read_text() + "\n# judged again\n", encoding="utf-8")
    second = workspace.lint(base)

    assert second.returncode == 0, second.stdout + second.stderr
    assert workspace.judge_runs() == 2
    assert CACHE_MISS in second.stderr


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
    assert CACHE_MISS in moved.stderr
    assert CACHE_HIT in repeated.stderr


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
    assert CACHE_MISS in second.stderr


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
    assert CACHE_MISS in second.stderr


def test_changed_llmlint_version_reruns_the_judge(workspace: Workspace) -> None:
    base = workspace.head()
    workspace.lint(base, FAKE_LLMLINT_VERSION="0.3.25")

    second = workspace.lint(base, FAKE_LLMLINT_VERSION="0.4.0")

    assert workspace.judge_runs() == 2
    assert CACHE_MISS in second.stderr


def test_a_failing_verdict_is_replayed_with_its_findings_and_its_exit(
    workspace: Workspace,
) -> None:
    base = workspace.head()

    first = workspace.lint(base, FAKE_LLMLINT_EXIT="1")
    second = workspace.lint(base, FAKE_LLMLINT_EXIT="1")

    assert workspace.judge_runs() == 1
    assert CACHE_HIT in second.stderr
    for result in (first, second):
        report = result.stdout + result.stderr
        assert result.returncode != 0, report
        assert FAIL_FINDING in report
        assert FAIL_VERDICT in report


# llmlint: ignore[tests_mirror_real_usage] Rewriting the record is the premise, not the exercise.
def test_an_edited_verdict_loses_to_the_cached_one(workspace: Workspace) -> None:
    base = workspace.head()
    workspace.lint(base, FAKE_LLMLINT_EXIT="1")
    # Nx leaves pre-existing outputs alone, so the recorded failure would survive
    # as a pass here if the recipe did not clear it before asking for the cache.
    (workspace.root / ".nx/llmlint-diff/report").write_text("all clear\n", encoding="utf-8")
    (workspace.root / ".nx/llmlint-diff/status").write_text("0\n", encoding="utf-8")

    replayed = workspace.lint(base, FAKE_LLMLINT_EXIT="1")

    assert workspace.judge_runs() == 1
    assert replayed.returncode != 0
    assert FAIL_FINDING in replayed.stdout + replayed.stderr
    assert "all clear" not in replayed.stdout


def test_a_judge_that_never_reached_a_verdict_is_not_recorded(workspace: Workspace) -> None:
    base = workspace.head()

    first = workspace.lint(base, FAKE_LLMLINT_EXIT="2")
    second = workspace.lint(base, FAKE_LLMLINT_EXIT="2")

    # A broken toolchain is not a verdict, so it must re-run rather than stick.
    assert workspace.judge_runs() == 2
    for result in (first, second):
        report = result.stdout + result.stderr
        assert result.returncode != 0, report
        assert "exited 2 without reaching a verdict" in report


def test_skip_nx_cache_forces_a_fresh_judge_run(workspace: Workspace) -> None:
    """The documented way to re-judge one tier, and it works under a global skip too."""
    base = workspace.head()
    workspace.lint(base)

    forced = workspace.lint(base, "--skip-nx-cache", NX_SKIP_NX_CACHE="true")

    assert forced.returncode == 0, forced.stdout + forced.stderr
    assert workspace.judge_runs() == 2
    assert CACHE_MISS in forced.stderr


def _stub(directory: Path, name: str, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stub = directory / name
    stub.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    stub.chmod(0o755)
    return directory


def _run_target(workspace: Workspace, **overrides: str) -> subprocess.CompletedProcess[str]:
    """Invoke the Nx target the way someone who skipped the recipe would."""
    return subprocess.run(
        ["./scripts/nx.sh", "run", "workspace:lint-llm-diff"],
        cwd=workspace.root,
        env={**workspace.env, **overrides},
        check=False,
        text=True,
        capture_output=True,
    )


def _run_fingerprint(workspace: Workspace, **overrides: str) -> subprocess.CompletedProcess[str]:
    """Run the fingerprint the way an operator diagnosing a cache miss would."""
    return subprocess.run(
        [str(workspace.root / "scripts" / "llmlint-fingerprint.sh")],
        cwd=workspace.root,
        env={**workspace.env, **overrides},
        check=False,
        text=True,
        capture_output=True,
    )


@pytest.mark.parametrize(
    ("base_sha", "expected"),
    [
        ("", "must be a resolved commit id"),
        ("origin/main", "must be a resolved commit id"),
        ("0" * 40, "missing from this checkout"),
    ],
)
# The recipe resolves the base itself, so these states arise only when someone
# drives the cached target directly — the misuse this guard names, and the only
# way to reach it.
# llmlint: ignore[tests_mirror_real_usage] Only a direct target run reaches this state.
def test_the_target_refuses_a_base_it_cannot_judge(
    workspace: Workspace, base_sha: str, expected: str
) -> None:
    result = _run_target(workspace, LLMLINT_DIFF_BASE_SHA=base_sha)

    assert result.returncode != 0
    assert expected in result.stderr
    assert workspace.judge_runs() == 0


def _replay_verdict(workspace: Workspace) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(workspace.root / "scripts" / "llmlint-verdict.sh")],
        cwd=workspace.root,
        env=workspace.env,
        check=False,
        text=True,
        capture_output=True,
    )


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ({}, "no recorded verdict"),
        ({"report": "findings\n"}, "no recorded verdict"),
        ({"report": "findings\n", "status": "2\n"}, "is not a judged 0 or 1"),
    ],
)
# Nx restoring a partial record is the state this refuses; no recipe run produces it.
# llmlint: ignore[tests_mirror_real_usage] Only a broken cache restore reaches this state.
def test_an_incomplete_record_is_never_read_as_a_clean_run(
    workspace: Workspace, record: dict[str, str], expected: str
) -> None:
    verdict = workspace.root / ".nx/llmlint-diff"
    verdict.mkdir(parents=True)
    for name, content in record.items():
        (verdict / name).write_text(content, encoding="utf-8")

    result = _replay_verdict(workspace)

    assert result.returncode == UNUSABLE_RECORD
    assert expected in result.stderr


@pytest.mark.parametrize(
    ("unreadable", "expected"),
    [
        ("status", "could not read the recorded verdict status from"),
        ("report", "could not read the recorded findings from"),
    ],
)
# A file that passes `-r` and then fails to read is a broken cache restore, not a
# state any recipe run reaches.
# llmlint: ignore[tests_mirror_real_usage] Only a broken cache restore reaches this state.
def test_an_unreadable_record_is_a_hard_error_not_a_silent_pass(
    workspace: Workspace, unreadable: str, expected: str
) -> None:
    verdict = workspace.root / ".nx/llmlint-diff"
    verdict.mkdir(parents=True)
    (verdict / "status").write_text("1\n", encoding="utf-8")
    (verdict / "report").write_text("findings\n", encoding="utf-8")
    # A directory reads as present and readable, then fails at the read itself —
    # the shape of an I/O fault, and one that does not depend on running as a user
    # whose permissions can actually be revoked.
    (verdict / unreadable).unlink()
    (verdict / unreadable).mkdir()

    result = _replay_verdict(workspace)

    # Hard error, deliberately: an unusable record is neither a clean tree nor
    # findings, and this reader never re-judges to paper over one.
    assert result.returncode == UNUSABLE_RECORD
    assert expected in result.stderr
    assert f"{verdict}/{unreadable}" in result.stderr
    assert workspace.judge_runs() == 0


@pytest.mark.parametrize(
    ("stub_body", "expected"),
    [
        ('[[ ${1:-} == "--version" ]] && exit 1\nexit 0', "'llmlint --version' failed"),
        ('[[ ${1:-} == "config" ]] && exit 1\necho "llmlint 0.0.0-e2e"', "'llmlint config' failed"),
    ],
)
def test_the_fingerprint_names_an_unusable_judge_toolchain(
    workspace: Workspace, tmp_path: Path, stub_body: str, expected: str
) -> None:
    stubs = _stub(tmp_path / "judge-stub", "llmlint", f"set -uo pipefail\n{stub_body}")

    result = _run_fingerprint(workspace, PATH=f"{stubs}{os.pathsep}{workspace.env['PATH']}")

    assert result.returncode != 0
    assert expected in result.stderr


@pytest.mark.parametrize(
    ("labels_body", "expected"),
    [
        ("exit 1", "could not derive harness history labels"),
        ("echo 'not labels at all'", "not comma-separated key=value pairs"),
    ],
)
def test_the_recipe_refuses_unusable_harness_history_labels(
    workspace: Workspace, tmp_path: Path, labels_body: str, expected: str
) -> None:
    stubs = _stub(tmp_path / "label-stub", "uv", labels_body)

    result = workspace.lint(workspace.head(), PATH=f"{stubs}{os.pathsep}{workspace.env['PATH']}")

    assert result.returncode != 0
    assert expected in result.stderr
    assert workspace.judge_runs() == 0


def test_an_unresolvable_base_is_rejected_before_the_judge_is_paid(
    workspace: Workspace,
) -> None:
    result = workspace.lint("no-such-ref")

    assert result.returncode != 0
    assert "does not resolve to a commit" in result.stderr
    assert workspace.judge_runs() == 0
