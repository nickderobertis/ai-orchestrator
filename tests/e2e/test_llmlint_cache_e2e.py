"""E2E proof that the llmlint tier's judge run is cached by Nx, and only when clean.

The judge itself is non-deterministic, so `just lint-llm-diff` routes through the
cached `workspace:lint-llm-diff` target — but nothing records or replays a verdict
any more. Nx caches the judge *run*: a clean run's `-v` report is the task's
terminal output and Nx replays it verbatim, while a run with findings and a run
that never reached a verdict fail the task and are never stored. These journeys
drive the real recipe, the real `scripts/nx.sh`, the real Nx target definition, and
the real `llmlint config` resolution in a throwaway copy of this repository. Only
the billed judge run is faked — the same boundary the rest of the e2e suite fakes —
so `llmlint --diff` is counted rather than paid for, while `llmlint config` still
merges a real plugin off disk.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] This module has always
been where it is, and every one of its journeys drives real Nx, real git and a counted
judge under the same whole-workspace `reads_docs` key — none is cheaper than the others,
so the placement is the module's rather than any one test's. Moving it is a change to
this repository's Nx project selection, which a sibling node of this plan owns, and
excepting a single journey would split a suite whose fixture, fake judge and cache-key
reasoning are shared while leaving the cost exactly where it is.

llmlint: ignore-file[shell_test_tiers_stay_split] Same placement, same reason. What could
be kept out of this tier was: `tests/e2e/test_base_freshness_e2e.py` drives the same
`scripts/base-freshness.sh` with no Nx and no judge at all, from the recipe-scoped tier
keyed on `scripts/**`.

llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] Same placement, same
reason. `reads_docs` here is not a deselecting tier either: it names the whole-workspace
key this module's own premise — copying the tracked tree, prose included — requires.

llmlint: ignore-file[e2e_not_mocked] The judge run is this repository's paid model
boundary, faked here exactly as tests/e2e/fake_backend.py fakes the agent harness.
It is also the one thing these journeys cannot use for real: the claim under test
is that an unchanged tree yields the same report twice, which a non-deterministic
judge cannot demonstrate. Counting `--diff` invocations is what proves a report was
replayed rather than re-rolled; every other boundary — the recipe, Nx, git, and
llmlint's own config resolution — is real.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from nx_workspace import WORKSPACE_INSTALL_MARKS, copy_checkout

ROOT = Path(__file__).resolve().parents[2]
PASS_VERDICT = "fake-judge: 16 passed, 0 failed"
FAIL_VERDICT = "fake-judge: 15 passed, 1 failed"
FAIL_FINDING = "fake-judge finding: robust_shell in scripts/llmlint-judge.sh"
#: Emitted only when the tier asks for `-v`: the per-rule itemization and the
#: pointer into `llmlint history` are what make a replayed run worth as much as a
#: fresh one, so they are the thing the cache has to carry.
VERBOSE_DETAIL = "fake-judge detail: local_rule passed on 6 files"
HISTORY_POINTER = "fake-judge: run logged as 0ff1ce — see `llmlint history 0ff1ce`"
#: `-v` also sends the oneharness debug view to stderr: short diagnostics, plus one
#: enormous line per judge call carrying every judged file inside the prompt. Nx
#: replays a hit as one burst and exits, so a replay bigger than a pipe buffer loses
#: its tail — a green has to stay small enough to survive its own replay, which it
#: does by eliding the payload and keeping the pointer to where it can be retrieved.
DEBUG_DIAGNOSTIC = "fake-judge: see full results with `llmlint history 0ff1ce`"
DEBUG_PAYLOAD = "fake-judge serialized judge call: " + "x" * 3000
ELISION = f"elided a {len(DEBUG_PAYLOAD)}-character serialized judge call"
CACHE_HIT = "replayed the recorded verdict for base"
CACHE_MISS = "judged this diff against base"

pytestmark = [
    pytest.mark.skipif(
        shutil.which("llmlint") is None,
        reason="llmlint resolves the judge configuration this cache key is built from; "
        "run 'just setup-llmlint'",
    ),
    *WORKSPACE_INSTALL_MARKS,
    # Copying the whole tree is this journey's premise, and the tree includes
    # its prose: this belongs to the whole-workspace tier by construction.
    pytest.mark.reads_docs,
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

    def judge_arguments(self) -> list[str]:
        """The argument line each judge run was actually invoked with."""
        return [line.split("\t", 1)[0] for line in self.judge_log.read_text().splitlines()]

    def judge_labels(self) -> list[str]:
        """The `ONEHARNESS_HISTORY_LABELS` each judge run was actually handed."""
        return [line.split("\t", 1)[1] for line in self.judge_log.read_text().splitlines()]

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
        # One line per run, so counting them still counts judge runs; the arguments
        # and the labels the recipe handed over ride along on it because they are
        # the other things a run of this tier is supposed to carry.
        'printf "%s\\t%s\\n" "$*" "${ONEHARNESS_HISTORY_LABELS:-}" >>"$FAKE_LLMLINT_LOG"\n'
        # The detail a bare run omits, so a report that carries it proves the tier
        # asked for `-v` — and a replayed one proves Nx kept it.
        'if [[ " $* " == *" -v "* ]]; then\n'
        # Single-quoted: the pointer quotes the command with backticks, which a
        # double-quoted echo would run instead of print.
        f"  echo '{VERBOSE_DETAIL}'\n"
        f"  echo '{HISTORY_POINTER}'\n"
        # Both halves of what `-v` puts on stderr: a short diagnostic that has to
        # survive a replay, and the payload that would sink one.
        f"  echo '{DEBUG_DIAGNOSTIC}' >&2\n"
        f"  echo '{DEBUG_PAYLOAD}' >&2\n"
        "fi\n"
        "if [[ ${FAKE_LLMLINT_EXIT:-0} != 0 ]]; then\n"
        f'  echo "{FAIL_FINDING}"\n'
        f'  echo "{FAIL_VERDICT}"\n'
        '  exit "$FAKE_LLMLINT_EXIT"\n'
        "fi\n"
        f'echo "{PASS_VERDICT}"\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)


def _write_version_only_llmlint(directory: Path, version: str) -> Path:
    """Install an ambient llmlint whose version must not enter the pinned target."""
    directory.mkdir(parents=True, exist_ok=True)
    fake = directory / "llmlint"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        '[[ ${1:-} == "--version" ]] || { echo "ambient llmlint reached $1" >&2; exit 2; }\n'
        f'echo "llmlint {version}"\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return directory


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
    pinned = root / ".venv/bin"
    pinned.mkdir(parents=True)
    (pinned / "llmlint").symlink_to(binaries / "llmlint")

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


def test_unchanged_tree_and_base_replays_the_whole_judge_report(workspace: Workspace) -> None:
    """One judge roll, and the restored run says everything the first one said."""
    base = workspace.head()

    first = workspace.lint(base)
    second = workspace.lint(base)

    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode == 0, second.stdout + second.stderr
    assert workspace.judge_runs() == 1
    assert workspace.judge_arguments() == [f"--diff --diff-base {base} -v"]
    for result in (first, second):
        # The `-v` report is the product now — per-rule detail and the pointer into
        # `llmlint history` — so a replayed run has to carry all of it, not a
        # summary line reconstructed from a record.
        assert PASS_VERDICT in result.stdout
        assert VERBOSE_DETAIL in result.stdout
        assert HISTORY_POINTER in result.stdout
        # The short diagnostic survives; the payload that would push a real replay
        # past a pipe buffer — and take the three lines above with it — does not.
        report = result.stdout + result.stderr
        assert DEBUG_DIAGNOSTIC in report
        assert DEBUG_PAYLOAD not in report
        assert ELISION in report
    # "Green" is a claim about one base commit, so the one line of provenance names
    # it: a worker's gate and the push that publishes its work resolving different
    # bases are answering different questions, visible without digging.
    assert f"judged this diff against base {base} (Nx cache miss)" in first.stderr
    assert f"replayed the recorded verdict for base {base} (Nx cache hit)" in second.stderr


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

    changed = workspace.root / "orchestrator/labels.py"
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


def test_ambient_judge_bin_does_not_invalidate_the_verdict(workspace: Workspace) -> None:
    """The target pins its judge binary, so an inherited caller value is not an input."""
    base = workspace.head()
    workspace.lint(base, LLMLINT_ONEHARNESS_BIN="/caller/one/oneharness")

    second = workspace.lint(base, LLMLINT_ONEHARNESS_BIN="/caller/two/oneharness")

    assert second.returncode == 0, second.stdout + second.stderr
    assert workspace.judge_runs() == 1
    assert CACHE_HIT in second.stderr


def test_ambient_llmlint_path_does_not_invalidate_the_verdict(workspace: Workspace) -> None:
    """Both fingerprint commands resolve llmlint the way the judge does, not the caller.

    A cache hit alone would not prove that: a fingerprint that *fails* under the
    caller's llmlint also produces one, because Nx scores a runtime input that
    exits non-zero as no contribution rather than as an error, and both runs then
    share the same degraded key. So the fingerprint is read directly too — it has
    to resolve under each ambient llmlint, and resolve to the same digest, which is
    what says the checkout's judge configuration is still in the key.
    """
    base = workspace.head()
    first_bin = _write_version_only_llmlint(workspace.root.parent / "ambient-one", "1.0.0")
    second_bin = _write_version_only_llmlint(workspace.root.parent / "ambient-two", "2.0.0")
    on_first = {"PATH": f"{first_bin}{os.pathsep}{workspace.env['PATH']}"}
    on_second = {"PATH": f"{second_bin}{os.pathsep}{workspace.env['PATH']}"}

    first = workspace.lint(base, **on_first)
    second = workspace.lint(base, **on_second)
    first_print = _run_fingerprint(workspace, **on_first)
    second_print = _run_fingerprint(workspace, **on_second)

    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode == 0, second.stdout + second.stderr
    assert workspace.judge_runs() == 1
    assert CACHE_HIT in second.stderr
    assert first_print.returncode == 0, first_print.stdout + first_print.stderr
    assert second_print.returncode == 0, second_print.stdout + second_print.stderr
    assert first_print.stdout.strip() == second_print.stdout.strip() != ""


def test_an_ambient_llmlint_still_lets_the_judge_configuration_invalidate(
    workspace: Workspace,
) -> None:
    """A caller's llmlint must not quietly drop the fingerprint out of the cache key.

    Nx treats a runtime input that exits non-zero as *no contribution* rather than
    as an error, so a fingerprint the caller's environment can break does not fail
    the tier — it silently shrinks the key to the tree and the base. That is the
    worse half of the split-verdict defect: spurious misses only re-roll the judge,
    but a degraded key replays a verdict the judge configuration has since moved on
    from. Resolving the fingerprint under the same pinned runtime that judges is
    what keeps it contributing while an unrelated llmlint sits on PATH.
    """
    base = workspace.head()
    ambient = _write_version_only_llmlint(workspace.root.parent / "ambient-judge", "1.0.0")
    on_path = {"PATH": f"{ambient}{os.pathsep}{workspace.env['PATH']}"}

    first = workspace.lint(base, **on_path)
    # The plugin lives outside the checkout, so no file input can see this: the
    # judge configuration fingerprint is the only thing that can notice the rules
    # changed, and only if it is still part of the key.
    workspace.plugin.write_text(
        workspace.plugin.read_text().replace("operator entry point", "operator entry point twice"),
        encoding="utf-8",
    )
    second = workspace.lint(base, **on_path)

    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode == 0, second.stdout + second.stderr
    assert workspace.judge_runs() == 2
    assert CACHE_MISS in second.stderr


def test_findings_fail_the_tier_and_are_never_cached(workspace: Workspace) -> None:
    """A red is re-judged every time: Nx caches successful tasks only.

    That is the deliberate trade for deleting the record/replay protocol this tier
    used to smuggle failing verdicts through Nx with. An honest re-roll costs a
    judge call; the protocol cost a poisoned entry no documented lever could
    displace.
    """
    base = workspace.head()

    first = workspace.lint(base, FAKE_LLMLINT_EXIT="1")
    second = workspace.lint(base, FAKE_LLMLINT_EXIT="1")

    assert workspace.judge_runs() == 2
    for result in (first, second):
        report = result.stdout + result.stderr
        assert result.returncode != 0, report
        assert FAIL_FINDING in report
        assert FAIL_VERDICT in report
        # A failure is never cached, so it never has to survive a replay: the whole
        # debug view reaches the operator who has to act on it, payload included.
        assert DEBUG_PAYLOAD in report
        assert ELISION not in report
        assert CACHE_MISS in result.stderr


def test_a_judge_that_never_reached_a_verdict_fails_and_is_never_cached(
    workspace: Workspace,
) -> None:
    """A broken judge toolchain is not a verdict, so it must re-run rather than stick."""
    base = workspace.head()

    first = workspace.lint(base, FAKE_LLMLINT_EXIT="2")
    second = workspace.lint(base, FAKE_LLMLINT_EXIT="2")

    assert workspace.judge_runs() == 2
    for result in (first, second):
        report = result.stdout + result.stderr
        assert result.returncode != 0, report
        # Whatever the broken toolchain managed to say still reaches the operator —
        # its report and its debug view both; what it must never do is become a
        # stored answer about the diff.
        assert FAIL_FINDING in report
        assert DEBUG_PAYLOAD in report
        assert CACHE_MISS in result.stderr


def test_a_cleared_red_caches_the_green_that_replaced_it(workspace: Workspace) -> None:
    """The path a worker actually walks: judge, fix, judge again, then settle."""
    base = workspace.head()

    red = workspace.lint(base, FAKE_LLMLINT_EXIT="1")
    cleared = workspace.root / "orchestrator/labels.py"
    cleared.write_text(cleared.read_text() + "\n# the finding, cleared\n", encoding="utf-8")
    green = workspace.lint(base)
    settled = workspace.lint(base)

    assert red.returncode != 0, red.stdout + red.stderr
    assert green.returncode == 0, green.stdout + green.stderr
    assert settled.returncode == 0, settled.stdout + settled.stderr
    assert workspace.judge_runs() == 2
    assert CACHE_MISS in green.stderr
    assert CACHE_HIT in settled.stderr


def test_skip_nx_cache_forces_a_fresh_judge_run(workspace: Workspace) -> None:
    """The documented way to re-judge one tier, and it works under a global skip too."""
    base = workspace.head()
    workspace.lint(base)

    forced = workspace.lint(base, "--skip-nx-cache", NX_SKIP_NX_CACHE="true")

    assert forced.returncode == 0, forced.stdout + forced.stderr
    assert workspace.judge_runs() == 2
    assert CACHE_MISS in forced.stderr


def test_a_forced_re_judge_does_not_replace_the_cached_run(workspace: Workspace) -> None:
    """The honest limit of the re-judge lever, so nobody plans a rescue around it.

    Under this Nx, `--skip-nx-cache` neither reads nor writes the cache: it buys one
    fresh look at the diff and leaves the stored run exactly where it was. So a
    *green* an operator disagrees with sticks until the tree, the base commit, or
    the judge configuration moves — the next ordinary invocation replays the same
    entry, and the judge is not rolled again.
    """
    base = workspace.head()
    workspace.lint(base)

    workspace.lint(base, "--skip-nx-cache")
    afterwards = workspace.lint(base)

    assert afterwards.returncode == 0, afterwards.stdout + afterwards.stderr
    # Two rolls: the original and the forced one. The third invocation replayed the
    # original rather than anything the forced run produced.
    assert workspace.judge_runs() == 2
    assert CACHE_HIT in afterwards.stderr


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
    pinned = workspace.root / ".venv/bin/llmlint"
    pinned.unlink()
    pinned.symlink_to(stubs / "llmlint")

    result = _run_fingerprint(workspace)

    assert result.returncode != 0
    assert expected in result.stderr


@pytest.mark.parametrize("entrypoint", ["fingerprint", "target"])
def test_a_missing_pinned_runtime_helper_is_actionable(
    workspace: Workspace, entrypoint: str
) -> None:
    (workspace.root / "scripts/llmlint-runtime-env.sh").unlink()

    if entrypoint == "fingerprint":
        result = _run_fingerprint(workspace)
        expected = "llmlint fingerprint: could not load the pinned runtime environment"
    else:
        result = _run_target(workspace, LLMLINT_DIFF_BASE_SHA=workspace.head())
        expected = "lint-llm-diff: could not load the pinned runtime environment"

    assert result.returncode != 0
    assert expected in result.stderr
    assert "restore scripts/llmlint-runtime-env.sh and retry" in result.stderr
    assert workspace.judge_runs() == 0


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
    # Whatever the task says — findings or a refusal like this one — comes back on
    # the report stream, because Nx's terminal output is what the tier replays.
    assert expected in result.stdout + result.stderr
    assert workspace.judge_runs() == 0


def test_labels_the_tier_only_passes_through_reach_the_judge_unnarrowed(
    workspace: Workspace,
) -> None:
    """A dispatched agent's own gate carries labels this tier did not set.

    `orchestrator/labels.py` is the declared trust boundary for the label contract,
    and that contract allows a dot or a hyphen in a key and a space in a value. The
    recipe layers `role=llmlint` over whatever it inherited; a second, narrower
    opinion of the contract in the recipe failed the whole gate over a label it was
    only meant to pass along.
    """
    base = workspace.head()

    judged = workspace.lint(base, ONEHARNESS_HISTORY_LABELS="ticket-id=ENG 123,agent.role=worker")

    assert judged.returncode == 0, judged.stdout + judged.stderr
    assert PASS_VERDICT in judged.stdout
    assert workspace.judge_runs() == 1
    assert workspace.judge_labels() == ["ticket-id=ENG 123,agent.role=worker,role=llmlint"]


def test_an_unresolvable_base_is_rejected_before_the_judge_is_paid(
    workspace: Workspace,
) -> None:
    result = workspace.lint("no-such-ref")

    assert result.returncode != 0
    assert "does not resolve to a commit" in result.stderr
    assert workspace.judge_runs() == 0


def _tracking(workspace: Workspace, origin: Path) -> None:
    """Give this checkout a real origin with `main` on it, tracked the ordinary way."""
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    workspace._git("checkout", "-q", "-B", "main")
    workspace._git("remote", "add", "origin", str(origin))
    workspace._git("push", "-q", "-u", "origin", "main")


def test_a_base_its_own_origin_ref_has_moved_past_is_refused_and_no_other_state_is(
    workspace: Workspace, tmp_path: Path
) -> None:
    """A stale base names a real commit, so nothing downstream can see it is the wrong one.

    `main` inside a session clone is the ordinary case rather than an odd one: the branch
    is cut once and the origin moves on, and the name goes on resolving. Everything below
    the recipe then behaves perfectly — the commit keys the cache, the judge reads the
    range it was given, and what comes back is a valid verdict over commits the branch
    does not carry. The one thing that can notice is the recipe, and the one thing a
    reader needs is which two refs disagree, so the refusal names both and both commits.

    Refused strictly for being behind, and the other three states are what say so. A base
    level with its origin ref, one ahead of it, and one with no origin ref at all are each
    driven through the same real recipe against the same real repository and each judged
    over its own commit — because a refusal that also fired on those would refuse the
    default gate, every worker on an unpushed branch, and every base named by commit.
    """
    _tracking(workspace, tmp_path / "origin.git")

    level = workspace.head()
    at_level = workspace.lint("main")

    assert at_level.returncode == 0, at_level.stdout + at_level.stderr
    assert PASS_VERDICT in at_level.stdout
    assert f"base {level}" in at_level.stderr, (
        f"a base level with its origin ref was not judged over its own commit:\n{at_level.stderr}"
    )

    ahead = workspace.commit("work the origin has not seen", allow_empty=True)
    at_ahead = workspace.lint("main")

    assert at_ahead.returncode == 0, (
        f"a base ahead of its own origin ref was refused, which is every worker on a "
        f"branch they have not pushed:\n{at_ahead.stdout}{at_ahead.stderr}"
    )
    assert f"base {ahead}" in at_ahead.stderr, at_ahead.stderr

    # The same commit the local branch just had, now on the origin and no longer on the
    # branch: `main` is a strict ancestor of `origin/main`, which is the one shape where
    # the extra commits are unambiguously work this base has not caught up with.
    workspace._git("push", "-q", "origin", "main")
    workspace._git("reset", "--hard", "-q", "HEAD~1")
    assert workspace.head() == level
    judged_so_far = workspace.judge_runs()

    behind = workspace.lint("main")

    assert behind.returncode != 0, (
        f"a base its own origin ref had moved past was judged, so the verdict covers "
        f"commits this branch does not carry:\n{behind.stdout}{behind.stderr}"
    )
    assert "is behind its own origin ref" in behind.stderr, behind.stderr
    for named in ("'main'", "'origin/main'", level, ahead):
        assert named in behind.stderr, (
            f"the refusal does not name {named}, so the reader is not told which two "
            f"refs disagree or where each of them is:\n{behind.stderr}"
        )
    assert workspace.judge_runs() == judged_so_far, (
        "the stale base was refused and the judge was paid for it anyway"
    )

    # A branch of its own with nothing tracking it: no upstream, and no `origin/side`
    # either, so there is no ref that could have moved past it.
    workspace._git("checkout", "-q", "-b", "side")
    untracked = workspace.commit("work on a branch nothing tracks", allow_empty=True)
    at_untracked = workspace.lint("side")

    assert at_untracked.returncode == 0, (
        f"a base with no origin ref of its own was refused, which is every base named "
        f"by commit:\n{at_untracked.stdout}{at_untracked.stderr}"
    )
    assert f"base {untracked}" in at_untracked.stderr, at_untracked.stderr
