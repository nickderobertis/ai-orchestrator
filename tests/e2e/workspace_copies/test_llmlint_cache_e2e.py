"""E2E proof of what the llmlint tier's recipe and target refuse, report and pass on.

`just lint-llm-diff` resolves its base, refuses one it cannot judge or one its own
origin ref has moved past, and routes the judge run through the
`workspace:lint-llm-diff` target with `-v`. These journeys drive the real recipe, the
real `scripts/nx.sh`, the real Nx target definition, and the real `llmlint config`
resolution in a throwaway copy of this repository, and read what each run says: the
refusals, the green report and what it elides, a red's whole report, the labels a
run carries, and the ambient cache skip the recipe names and drops. Which change
replays or re-runs the target is Nx's behaviour over its configuration, and no
journey here asserts it. Only the billed judge run is faked — the same boundary the
rest of the e2e suite fakes — so `llmlint --diff` is recorded rather than paid for,
while `llmlint config` still merges a real plugin off disk.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] This module has always
been where it is, and every one of its journeys drives real Nx, real git and a recorded
judge under the same whole-workspace `reads_docs` key — none is cheaper than the others,
so the placement is the module's rather than any one test's. Excepting a single journey
would split a suite whose fixture and fake judge are shared while leaving the cost
exactly where it is.

llmlint: ignore-file[shell_test_tiers_stay_split] Same placement, same reason. What could
be kept out of this tier was: `tests/e2e/publish_and_landing/test_base_freshness_e2e.py`
drives the same `scripts/base-freshness.sh` with no Nx and no judge at all, from the
recipe-scoped tier keyed on `scripts/**`.

llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] Same placement, same
reason. `reads_docs` here is not a deselecting tier either: it names the whole-workspace
key this module's own premise — copying the tracked tree, prose included — requires.

llmlint: ignore-file[e2e_not_mocked] The judge run is this repository's paid model
boundary, faked here exactly as tests/e2e/fake_backend.py fakes the agent harness.
It is also the one thing these journeys cannot use for real: the claims under test
are about which report a given verdict produces and whether a refusal stopped the
judge being paid, which a non-deterministic judge cannot demonstrate. Recording each
`--diff` invocation is what proves a refused run never reached it; every other
boundary — the recipe, Nx, git, and llmlint's own config resolution — is real.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from nx_workspace import TOOLCHAIN_WRITER_MARKS, WORKSPACE_INSTALL_MARKS, copy_checkout

ROOT = Path(__file__).resolve().parents[3]
PASS_VERDICT = "fake-judge: 16 passed, 0 failed"
FAIL_VERDICT = "fake-judge: 15 passed, 1 failed"
FAIL_FINDING = "fake-judge finding: robust_shell in scripts/llmlint-judge.sh"
#: Emitted only when the tier asks for `-v`: the per-rule itemization and the
#: pointer into `llmlint history` are what the tier's report is for.
VERBOSE_DETAIL = "fake-judge detail: local_rule passed on 6 files"
HISTORY_POINTER = "fake-judge: run logged as 0ff1ce — see `llmlint history 0ff1ce`"
#: `-v` also sends the oneharness debug view to stderr: short diagnostics, plus one
#: enormous line per judge call carrying every judged file inside the prompt. A green
#: report stays small by eliding that payload and keeping the pointer to where it can
#: be retrieved.
DEBUG_DIAGNOSTIC = "fake-judge: see full results with `llmlint history 0ff1ce`"
DEBUG_PAYLOAD = "fake-judge serialized judge call: " + "x" * 3000
ELISION = f"elided a {len(DEBUG_PAYLOAD)}-character serialized judge call"

pytestmark = [
    pytest.mark.skipif(
        shutil.which("llmlint") is None,
        reason="llmlint resolves the judge configuration this tier runs under; "
        "run 'just setup-llmlint'",
    ),
    *WORKSPACE_INSTALL_MARKS,
    # `just lint-llm-diff` reaches `scripts/nx.sh`, whose heal installs into the copy it
    # runs in, which is the copy's own.
    *TOOLCHAIN_WRITER_MARKS,
    # Copying the whole tree is this journey's premise, and the tree includes
    # its prose: this belongs to the whole-workspace tier by construction.
    pytest.mark.reads_docs,
]


@dataclass(frozen=True)
class Workspace:
    """A throwaway checkout wired to record judge runs instead of paying for them."""

    root: Path
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
        # asked for `-v`.
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

    # A plugin outside the tree, so `llmlint config` merges a real plugin off disk
    # the way the judge configuration fingerprint reads one.
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
    workspace = Workspace(root=root, judge_log=judge_log, env=env)
    workspace._git("init", "-q")
    workspace.commit("checkout under test")
    return workspace


def test_an_ambient_global_cache_skip_is_reported_and_ignored(workspace: Workspace) -> None:
    """The only supported re-judge lever is per-tier, so a global one is named and dropped.

    Each run here is green, so it is also where the green report is read: the `-v`
    report is the product — per-rule detail and the pointer into `llmlint history` —
    and the short diagnostic survives while the serialized payload that would push a
    real report past a pipe buffer, and take the lines above with it, is elided.
    """
    base = workspace.head()

    first = workspace.lint(base, NX_SKIP_NX_CACHE="true")
    second = workspace.lint(base, NX_DISABLE_NX_CACHE="true")

    assert workspace.judge_arguments()[0] == f"--diff --diff-base {base} -v"
    for result in (first, second):
        assert result.returncode == 0, result.stdout + result.stderr
        assert "ignoring the ambient global Nx cache skip" in result.stderr
        assert f"just lint-llm-diff {base} --skip-nx-cache" in result.stderr
        assert PASS_VERDICT in result.stdout
        assert VERBOSE_DETAIL in result.stdout
        assert HISTORY_POINTER in result.stdout
        report = result.stdout + result.stderr
        assert DEBUG_DIAGNOSTIC in report
        assert DEBUG_PAYLOAD not in report
        assert ELISION in report
        # "Green" is a claim about one base commit, so the one line of provenance
        # names it.
        assert f"base {base}" in result.stderr


def test_findings_fail_the_tier_with_the_whole_report(workspace: Workspace) -> None:
    """Findings fail the tier, and the whole report reaches the operator who must act on it."""
    base = workspace.head()

    result = workspace.lint(base, FAKE_LLMLINT_EXIT="1")

    report = result.stdout + result.stderr
    assert result.returncode != 0, report
    assert FAIL_FINDING in report
    assert FAIL_VERDICT in report
    # Only a green is elided: a red's whole debug view, payload included, is what the
    # operator diagnoses from.
    assert DEBUG_PAYLOAD in report
    assert ELISION not in report


def test_a_judge_that_never_reached_a_verdict_fails_and_says_what_it_saw(
    workspace: Workspace,
) -> None:
    """A broken judge toolchain is not a verdict: the tier fails and says what it saw."""
    base = workspace.head()

    result = workspace.lint(base, FAKE_LLMLINT_EXIT="2")

    report = result.stdout + result.stderr
    assert result.returncode != 0, report
    # Whatever the broken toolchain managed to say still reaches the operator — its
    # report and its debug view both.
    assert FAIL_FINDING in report
    assert DEBUG_PAYLOAD in report


def _stub(directory: Path, name: str, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stub = directory / name
    stub.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    stub.chmod(0o755)
    return directory


def _run_target(
    workspace: Workspace, **overrides: str
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Invoke the Nx target the way someone who skipped the recipe would.

    Returns the run and every refusal the tier said, wherever it arrived. Nx can drop a
    refusal from its terminal output (`scripts/llmlint-judge.sh` says when), so the tier
    also appends each one to the file `LLMLINT_DIFF_REFUSALS` names, which is how `just
    lint-llm-diff` still relays it; this reads both, as the recipe does.
    """
    refusals = workspace.root.parent / "lint-llm-diff-refusals.log"
    refusals.unlink(missing_ok=True)
    result = subprocess.run(
        ["./scripts/nx.sh", "run", "workspace:lint-llm-diff"],
        cwd=workspace.root,
        env={**workspace.env, "LLMLINT_DIFF_REFUSALS": str(refusals), **overrides},
        check=False,
        text=True,
        capture_output=True,
    )
    said = result.stderr + (refusals.read_text(encoding="utf-8") if refusals.exists() else "")
    return result, said


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
    result, said = _run_target(workspace, LLMLINT_DIFF_BASE_SHA=base_sha)

    assert result.returncode != 0
    assert expected in said
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
        said = result.stderr
        expected = "llmlint fingerprint: could not load the pinned runtime environment"
    else:
        result, said = _run_target(workspace, LLMLINT_DIFF_BASE_SHA=workspace.head())
        expected = "lint-llm-diff: could not load the pinned runtime environment"

    assert result.returncode != 0
    assert expected in said
    assert "restore scripts/llmlint-runtime-env.sh and retry" in said
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
