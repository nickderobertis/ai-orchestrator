"""E2E proof that the worker's gate and the merge path look up one llmlint verdict.

`tests/e2e/test_llmlint_cache_e2e.py` proves the cached judge run holds when the
same checkout asks twice. Production never asks twice from the same checkout. The
worker judges in a per-branch worktree cut from its run's clone, under the
environment a dispatch carries; the publication is rebuilt in a detached scratch
worktree at an unrelated path and judged there by the `pre-push` hook, under the
environment a publishing push carries. Two paths, one content, one base — and the
cache is only worth having if both reach the same stored run.

They did not. On 2026-07-31 a branch cleared its own complete gate at 95.99%
coverage (`38 rules: 18 passed, 0 failed`) and was rejected seventeen minutes later
on the merge path over byte-identical content and the same base commit
(`38 rules: 16 passed, 2 failed`). **Both runs logged `(Nx cache miss)`**: the judge
was rolled twice and disagreed with itself, because the two paths hashed the same
question to different keys. `LLMLINT_ONEHARNESS_BIN` was the input that varied —
`llmlint config` renders it, the fingerprint read the caller's value, and a
dispatched agent carries one while a publishing push carries none.

So these journeys run the tier's real recipe from both callers, each under the
environment its own caller supplies, and assert on the thing the incident produced:
how many times the judge was rolled. A cache hit on the merge path is the claim; a
second roll is the defect, whichever way the second roll then lands.

The three ways the key must still move — the judged content, the base commit, and
the judge configuration — are proved across the two paths too, because a fix that
made the merge path agree by hashing less would replay a verdict for a tree nobody
judged. What this journey owns is the question both callers ask, and whether one
stored answer comes back.

Only a *clean* run is stored: Nx caches successful tasks only, and this tier no
longer smuggles failures through it. So a worker whose own gate went red is judged
again on the merge path rather than replayed — the accepted cost of deleting that
protocol. It is not a way past the gate, and the journey below says so by rolling a
judge that fails every time.

llmlint: ignore-file[e2e_not_mocked] The judge run is this repository's paid model
boundary, faked here exactly as tests/e2e/fake_backend.py fakes the agent harness,
and for the same reason as in the sibling journey: the claim under test is that two
paths reach one verdict, which a non-deterministic judge cannot demonstrate.
Counting `--diff` invocations is the evidence. Everything else is real — the real
recipe operators run, the real Nx target and its fingerprint, a real clone and real
worktrees, a real squash merge, and llmlint's own config resolution off disk.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from conftest import git
from nx_workspace import copy_working_tree, requires_workspace_install

from orchestrator import REPO_ROOT, gitops
from orchestrator.verify import comparison_env

BASE_BRANCH = "main"
FEATURE_BRANCH = "feature"
PASS_VERDICT = "fake-judge: 16 passed, 0 failed"
FAIL_VERDICT = "fake-judge: 15 passed, 1 failed"
FAIL_FINDING = "fake-judge finding: robust_shell in scripts/llmlint-judge.sh"
CACHE_HIT = "replayed the recorded verdict for base"
CACHE_MISS = "judged this diff against base"

#: A dispatched agent's gate inherits an `LLMLINT_ONEHARNESS_BIN` naming a checkout
#: that is not the worktree being judged, and a publishing push inherits none. The
#: exact value is deliberately arbitrary here: the tier's contract is that *no*
#: caller-supplied value reaches the key, so pinning this to one producer's output
#: would test less, not more.
DISPATCH_AMBIENT_BIN = "/dispatch/checkout/scripts/llmlint-oneharness.sh"

pytestmark = [
    pytest.mark.skipif(
        shutil.which("llmlint") is None,
        reason="llmlint resolves the judge configuration this cache key is built from; "
        "run 'just setup-llmlint'",
    ),
    requires_workspace_install,
    # Copying the whole tree is this journey's premise, and the tree includes its
    # prose: this belongs to the whole-workspace tier by construction.
    pytest.mark.reads_docs,
]


@dataclass
class TwoPaths:
    """One repository, judged the two ways production judges it."""

    origin: Path
    seed: Path
    clone: Path
    worker: Path
    plugin: Path
    judge_log: Path
    scratch_parent: Path
    env: dict[str, str]
    rebuilt: int = field(default=0)

    def judge_runs(self) -> int:
        return len(self.judge_log.read_text().splitlines())

    def worker_llmlint_tier(self, **overrides: str) -> subprocess.CompletedProcess[str]:
        """The llmlint tier of a dispatched agent's gate, run in its own worktree.

        Only that tier: the rest of the gate has nothing to do with the recorded
        verdict these journeys are about.
        """
        return self._llmlint_tier(
            self.worker,
            {"LLMLINT_ONEHARNESS_BIN": DISPATCH_AMBIENT_BIN},
            overrides,
        )

    def merge_path_llmlint_tier(self, **overrides: str) -> subprocess.CompletedProcess[str]:
        """The same tier, where the publication is rebuilt: a detached scratch worktree.

        Cut from the same clone at an unrelated path and carrying the workstream's
        comparison identity and nothing else, exactly as the lifecycle rebuilds and
        judges a publication before pushing it.
        """
        gitops.fetch(self.clone)
        self.rebuilt += 1
        scratch = self.scratch_parent / f"orchestrator-merge-{self.rebuilt}" / "worktree"
        gitops.worktree_add_detached(self.clone, scratch, f"origin/{BASE_BRANCH}")
        (scratch / "node_modules").symlink_to(REPO_ROOT / "node_modules", target_is_directory=True)
        gitops.merge_squash(scratch, f"origin/{FEATURE_BRANCH}", message="publication")
        try:
            return self._llmlint_tier(scratch, {}, overrides)
        finally:
            gitops.worktree_remove(self.clone, scratch)

    def _llmlint_tier(
        self, cwd: Path, caller: dict[str, str], overrides: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        """Run `lint-llm-diff` the way its operators do, resolving the base as they do."""
        return subprocess.run(
            ["bash", "-c", 'just lint-llm-diff "$(./scripts/comparison-base.sh)"'],
            cwd=cwd,
            env={**self.env, **comparison_env(BASE_BRANCH), **caller, **overrides},
            check=False,
            text=True,
            capture_output=True,
        )

    def commit_on_branch(self, message: str) -> str:
        gitops.add_all(self.worker)
        return gitops.commit(self.worker, message)

    def push_branch(self) -> None:
        """Get the branch to origin so the merge path can rebuild it.

        `--no-verify` because the branch push is not this journey's subject and a
        worker whose gate failed is exactly the state the invariant has to survive:
        work whose own gate never passed, sitting on origin, one merge away.
        """
        git("push", "--no-verify", "--force", "origin", FEATURE_BRANCH, cwd=self.worker)

    def advance_base(self, message: str) -> str:
        """Move `origin/main` without changing any file, isolating the base commit."""
        advanced = gitops.commit_empty(self.seed, message)
        git("push", str(self.origin), BASE_BRANCH, cwd=self.seed)
        return advanced

    def base_sha(self) -> str:
        return gitops.ref_sha(self.origin, BASE_BRANCH)


def _write_fake_judge(directory: Path) -> None:
    """Install an `llmlint` that counts `--diff` runs but resolves config for real."""
    directory.mkdir(parents=True, exist_ok=True)
    fake = directory / "llmlint"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ ${1:-} == "--version" ]]; then\n'
        '  echo "llmlint 0.0.0-e2e"\n'
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
def two_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TwoPaths]:
    seed = tmp_path / "seed"
    seed.mkdir()
    copy_working_tree(seed)

    # A plugin outside the tree: no file input can see it, so only the judge
    # configuration fingerprint can notice when its rules change.
    plugin = tmp_path / "external-plugin.yml"
    plugin.write_text(
        "version: 1\nrules:\n"
        "  - name: plugin_rule\n"
        "    description: The change documents every new operator entry point.\n",
        encoding="utf-8",
    )
    (seed / "llmlint.yml").write_text(
        f'files:\n  exclude:\n    - "**/.git/**"\nplugins:\n  - "{plugin}"\n'
        "rules:\n  - name: local_rule\n"
        "    description: The change keeps every touched shell script POSIX-safe.\n",
        encoding="utf-8",
    )
    git("init", "-q", "-b", BASE_BRANCH, str(seed))
    gitops.add_all(seed)
    gitops.commit(seed, "the repository under test")
    # Bare, like every remote the lifecycle publishes to: a checked-out branch
    # refuses a push for a reason that has nothing to do with the gate.
    origin = tmp_path / "origin.git"
    git("clone", "--bare", "-q", str(seed), str(origin))

    # The per-run clone the lifecycle cuts every worktree from — both paths' trees
    # come out of this one, which is what makes them two views of one repository.
    clone = gitops.clone_sharing(origin, tmp_path / "clone", origin=str(origin), base=BASE_BRANCH)
    # Each worktree borrows this checkout's install through a symlink, and
    # `.gitignore`'s `node_modules/` does not match one. Excluding it in the clone
    # keeps that plumbing out of every commit, so the tree the merge path rebuilds
    # is byte-identical to the tree the worker judged.
    exclude = gitops.common_dir(clone) / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.write_text("node_modules\n", encoding="utf-8")
    worker = gitops.worktree_add(
        clone, tmp_path / "run" / "worker", FEATURE_BRANCH, base=f"origin/{BASE_BRANCH}"
    )
    (worker / "node_modules").symlink_to(REPO_ROOT / "node_modules", target_is_directory=True)

    binaries = tmp_path / "bin"
    real_llmlint = shutil.which("llmlint")
    assert real_llmlint is not None
    _write_fake_judge(binaries)

    judge_log = tmp_path / "judge-runs.log"
    judge_log.write_text("", encoding="utf-8")
    # The orchestrator process drops this before it pushes (dispatch.py, watchdog.py),
    # so the merge path must not inherit one from whoever is running the suite.
    monkeypatch.delenv("LLMLINT_ONEHARNESS_BIN", raising=False)
    scratch_parent = tmp_path / "scratch"
    scratch_parent.mkdir()
    env = {
        **os.environ,
        "PATH": f"{binaries}{os.pathsep}{os.environ['PATH']}",
        # Isolate both the Nx cache (scripts/nx.sh roots it here) and llmlint's own
        # plugin cache from the developer's real ones. Shared across both paths on
        # purpose: one host, one cache, exactly as production has it.
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "FAKE_LLMLINT_LOG": str(judge_log),
        "REAL_LLMLINT": real_llmlint,
        # Reuse this repository's already-synced environment rather than building
        # each throwaway worktree as a distinct project.
        "UV_NO_SYNC": "1",
        "UV_PROJECT_ENVIRONMENT": str(REPO_ROOT / ".venv"),
    }
    yield TwoPaths(
        origin=origin,
        seed=seed,
        clone=clone,
        worker=worker,
        plugin=plugin,
        judge_log=judge_log,
        scratch_parent=scratch_parent,
        env=env,
    )
    # The lifecycle releases a task worktree when its workstream settles; a linked
    # worktree left behind is a leak the suite's guard fails on, and rightly.
    gitops.worktree_remove(clone, worker)


def _work(paths: TwoPaths, note: str = "the change under test") -> None:
    changed = paths.worker / "orchestrator/dispatch.py"
    changed.write_text(f"{changed.read_text()}\n# {note}\n", encoding="utf-8")
    paths.commit_on_branch(note)


def test_the_worker_gate_and_the_merge_path_reach_one_verdict(two_paths: TwoPaths) -> None:
    """The incident, inverted: one content and base must cost exactly one judge roll."""
    _work(two_paths)
    base = two_paths.base_sha()

    worker_run = two_paths.worker_llmlint_tier()
    two_paths.push_branch()
    rebuilt = two_paths.merge_path_llmlint_tier()

    assert worker_run.returncode == 0, worker_run.stdout + worker_run.stderr
    assert PASS_VERDICT in worker_run.stdout
    assert f"{CACHE_MISS} {base}" in worker_run.stderr
    # The whole claim: the merge path asked the same question and got the recorded
    # answer, rather than rolling a non-deterministic judge a second time.
    assert rebuilt.returncode == 0, rebuilt.stdout + rebuilt.stderr
    assert f"{CACHE_HIT} {base}" in rebuilt.stderr
    assert PASS_VERDICT in rebuilt.stdout
    assert two_paths.judge_runs() == 1


def test_a_failed_worker_gate_is_judged_again_and_still_rejected(
    two_paths: TwoPaths,
) -> None:
    """A red is not stored, so the merge path re-judges — and still refuses the push."""
    _work(two_paths)

    worker_run = two_paths.worker_llmlint_tier(FAKE_LLMLINT_EXIT="1")
    two_paths.push_branch()
    rebuilt = two_paths.merge_path_llmlint_tier(FAKE_LLMLINT_EXIT="1")

    assert worker_run.returncode != 0, worker_run.stdout + worker_run.stderr
    assert FAIL_FINDING in worker_run.stdout
    # Nothing was cached for the worker's failing run, so this is a second roll —
    # the deliberate cost of the cache holding successes only. What the tier still
    # owes is the outcome: findings reported, and the push this verdict gates
    # rejected rather than waved through.
    assert rebuilt.returncode != 0, rebuilt.stdout + rebuilt.stderr
    assert FAIL_FINDING in rebuilt.stdout
    assert CACHE_MISS in rebuilt.stderr
    assert two_paths.judge_runs() == 2


def test_content_the_worker_never_judged_is_judged_on_the_merge_path(
    two_paths: TwoPaths,
) -> None:
    """A verdict covers the tree it judged, so later commits do not inherit it."""
    _work(two_paths)
    worker_run = two_paths.worker_llmlint_tier()

    # The worker settled with one more commit than its gate ever saw. Publishing
    # that must not replay the cleared verdict for the tree it replaced.
    _work(two_paths, "a change the worker's gate never saw")
    two_paths.push_branch()
    rebuilt = two_paths.merge_path_llmlint_tier()

    assert worker_run.returncode == 0, worker_run.stdout + worker_run.stderr
    assert rebuilt.returncode == 0, rebuilt.stdout + rebuilt.stderr
    assert CACHE_MISS in rebuilt.stderr
    assert two_paths.judge_runs() == 2


def test_an_advanced_base_is_judged_again_on_the_merge_path(two_paths: TwoPaths) -> None:
    """Same tree, different comparison: two different diffs, two judgements."""
    _work(two_paths)
    judged = two_paths.base_sha()
    worker_run = two_paths.worker_llmlint_tier()

    # Empty, so the published tree stays byte-identical to the judged one and the
    # resolved base commit is the only thing that moved.
    advanced = two_paths.advance_base("advance the base under the settled branch")
    two_paths.push_branch()
    rebuilt = two_paths.merge_path_llmlint_tier()

    assert worker_run.returncode == 0, worker_run.stdout + worker_run.stderr
    assert advanced != judged
    assert rebuilt.returncode == 0, rebuilt.stdout + rebuilt.stderr
    assert f"{CACHE_MISS} {advanced}" in rebuilt.stderr
    assert two_paths.judge_runs() == 2


def test_a_changed_judge_configuration_is_judged_again_on_the_merge_path(
    two_paths: TwoPaths,
) -> None:
    """The rules moved between the two paths, and no file input can see it."""
    _work(two_paths)
    worker_run = two_paths.worker_llmlint_tier()

    # The plugin lives outside the checkout, so both trees are byte-identical: only
    # the judge configuration fingerprint can notice, and only if it is in the key.
    two_paths.plugin.write_text(
        two_paths.plugin.read_text().replace("operator entry point", "operator entry point twice"),
        encoding="utf-8",
    )
    two_paths.push_branch()
    rebuilt = two_paths.merge_path_llmlint_tier()

    assert worker_run.returncode == 0, worker_run.stdout + worker_run.stderr
    assert rebuilt.returncode == 0, rebuilt.stdout + rebuilt.stderr
    assert CACHE_MISS in rebuilt.stderr
    assert two_paths.judge_runs() == 2
