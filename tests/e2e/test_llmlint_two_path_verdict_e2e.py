"""E2E proof that the worker's gate and the merge path look up one llmlint verdict.

`tests/e2e/test_llmlint_cache_e2e.py` proves the memo holds when the same checkout
asks twice. Production never asks twice from the same checkout. The worker judges
in a per-branch worktree cut from its run's clone, under the environment
`orchestrator/dispatch.py` builds; the publication is rebuilt in a detached scratch
worktree at an unrelated path, under the environment `orchestrator/merge.py` pushes
with, and judged there by the repository's real `pre-push` hook. Two paths, one
content, one base — and the memo is only worth having if both reach the same
recorded answer.

They did not. On 2026-07-31 a branch cleared its own complete gate at 95.99%
coverage (`38 rules: 18 passed, 0 failed`) and was rejected seventeen minutes later
on the merge path over byte-identical content and the same base commit
(`38 rules: 16 passed, 2 failed`). **Both runs logged `(Nx cache miss)`**: the judge
was rolled twice and disagreed with itself, because the two paths hashed the same
question to different keys. `LLMLINT_ONEHARNESS_BIN` was the input that varied —
`llmlint config` renders it, the fingerprint read the caller's value, and a
dispatched agent carries `dispatch.py`'s while a publishing push carries none.

So these journeys run both paths for real, each under the environment its own
production caller supplies, and assert on the thing the incident produced: how many
times the judge was rolled. A cache hit on the merge path is the claim; a second
roll is the defect, whichever way the second roll then lands.

The three ways the key must still move — the judged content, the base commit, and
the judge configuration — are proved across the two paths too, because a fix that
made the merge path agree by hashing less would replay a verdict for a tree nobody
judged. And a worker whose own gate failed still cannot reach the base: the hook
replays the recorded failure and rejects the publishing push.

llmlint: ignore-file[e2e_not_mocked] The judge run is this repository's paid model
boundary, faked here exactly as tests/e2e/fake_backend.py fakes the agent harness,
and for the same reason as in the sibling journey: the claim under test is that two
paths reach one verdict, which a non-deterministic judge cannot demonstrate.
Counting `--diff` invocations is the evidence. Everything else is real — the real
clone, worktrees and squash merge through `orchestrator.gitops`, the real `pre-push`
hook Git runs on the publishing push, the real recipe, the real Nx target, and
llmlint's own config resolution off disk.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from conftest import git, install_pre_push_hook
from nx_workspace import copy_working_tree, requires_workspace_install

from orchestrator import REPO_ROOT, gitops
from orchestrator.verify import comparison_env

BASE_BRANCH = "main"
FEATURE_BRANCH = "feature"
PASS_VERDICT = "fake-judge: 16 passed, 0 failed"
FAIL_VERDICT = "fake-judge: 15 passed, 1 failed"
FAIL_FINDING = "fake-judge finding: robust_shell in scripts/llmlint-diff.sh"
CACHE_HIT = "replayed the recorded verdict for base"
CACHE_MISS = "judged this diff against base"
HOOK_REJECTION = "pre-push: the llmlint tier rejected this tree"

#: What `orchestrator/dispatch.py` puts in a dispatched agent's environment. It
#: names the *orchestrator's* checkout, never the worktree being judged, which is
#: why the fingerprint's `{root}` fold-out cannot strip it.
DISPATCH_LLMLINT_BIN = str(REPO_ROOT / "scripts/llmlint-oneharness.sh")

# The merge path is a *different* verifier, so it gets the repository's real
# pre-push hook rather than a call the test makes itself. Production's hook clears
# Git's local environment before running anything, or every nested git command in
# the gate would target the pushing repository instead of its own cwd.
HOOK = """
for name in $(git rev-parse --local-env-vars); do unset "$name" || true; done
comparison=$(./scripts/comparison-base.sh "${1:-origin}" "${ORCHESTRATOR_COMPARISON_BASE:-}")
if ! just lint-llm-diff "$comparison"; then
  echo "pre-push: the llmlint tier rejected this tree" >&2
  exit 1
fi
"""

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


@dataclass(frozen=True)
class Publication:
    """What the merge path did with the work: its push output and whether it landed."""

    accepted: bool
    output: str


@dataclass(frozen=True)
class TwoPaths:
    """One repository, reached the two ways production reaches it."""

    origin: Path
    seed: Path
    clone: Path
    worker: Path
    plugin: Path
    judge_log: Path
    env: dict[str, str]
    scratch_parent: Path

    def judge_runs(self) -> int:
        return len(self.judge_log.read_text().splitlines())

    def worker_gate(self, **overrides: str) -> subprocess.CompletedProcess[str]:
        """The gate a dispatched agent runs before it settles, in its own worktree.

        Under the dispatch environment, `LLMLINT_ONEHARNESS_BIN` included: the
        asymmetry between this caller and the publishing push is the defect, so
        removing it here would leave the journey proving nothing.
        """
        dispatch_env = {"LLMLINT_ONEHARNESS_BIN": DISPATCH_LLMLINT_BIN}
        return subprocess.run(
            ["bash", "-c", 'just lint-llm-diff "$(./scripts/comparison-base.sh)"'],
            cwd=self.worker,
            env={**self.env, **comparison_env(BASE_BRANCH), **dispatch_env, **overrides},
            check=False,
            text=True,
            capture_output=True,
        )

    def commit_on_branch(self, message: str) -> str:
        gitops.add_all(self.worker)
        return gitops.commit(self.worker, message)

    def push_branch(self) -> None:
        """Get the branch to origin without re-judging it; the merge path is the subject.

        `--no-verify` is the premise rather than the exercise: it is exactly the
        state the invariant has to survive — work whose own gate never passed,
        sitting on origin, one merge away from the base.
        """
        subprocess.run(
            ["git", "push", "--no-verify", "--force", "origin", FEATURE_BRANCH],
            cwd=self.worker,
            check=True,
            text=True,
            capture_output=True,
        )

    def advance_base(self, message: str) -> str:
        """Move `origin/main` without changing any file, isolating the base commit."""
        advanced = gitops.commit_empty(self.seed, message)
        git("push", str(self.origin), BASE_BRANCH, cwd=self.seed)
        return advanced

    def publish(self) -> Publication:
        """Rebuild the work on the merge path and push it, exactly as merge.py does."""
        gitops.fetch(self.clone)
        scratch = Path(tempfile.mkdtemp(prefix="orchestrator-merge-", dir=self.scratch_parent))
        worktree = scratch / "worktree"
        gitops.worktree_add_detached(self.clone, worktree, f"origin/{BASE_BRANCH}")
        (worktree / "node_modules").symlink_to(REPO_ROOT / "node_modules", target_is_directory=True)
        gitops.merge_squash(worktree, f"origin/{FEATURE_BRANCH}", message="publication")
        # Merged over os.environ by gitops, and deliberately without a
        # LLMLINT_ONEHARNESS_BIN: a publishing push carries the comparison identity
        # and nothing else, which is precisely how it differed from the worker.
        push_env = {**self.env, **comparison_env(BASE_BRANCH)}
        try:
            output = gitops.push(worktree, f"HEAD:{BASE_BRANCH}", set_upstream=False, env=push_env)
            accepted = True
        except gitops.GitError as exc:
            output, accepted = exc.output, False
        finally:
            # merge.py releases the scratch tree whether or not the push landed.
            gitops.worktree_remove(self.clone, worktree)
        return Publication(accepted=accepted, output=output)

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
    # refuses the publishing push for a reason that has nothing to do with the gate.
    origin = tmp_path / "origin.git"
    git("clone", "--bare", "-q", str(seed), str(origin))

    # The per-run clone the lifecycle cuts every worktree from — both paths' trees
    # come out of this one, which is what makes them two views of one repository.
    clone = gitops.clone_sharing(origin, tmp_path / "clone", origin=str(origin), base=BASE_BRANCH)
    install_pre_push_hook(clone, HOOK)
    # Every worktree here borrows this checkout's install through a symlink, and
    # `.gitignore`'s `node_modules/` does not match one. Excluding it in the clone
    # covers every worktree cut from it, so neither path commits its own plumbing
    # and the two trees stay byte-identical.
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
    # so the publishing push must not inherit one from whoever is running the suite.
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
        env=env,
        scratch_parent=scratch_parent,
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

    gate = two_paths.worker_gate()
    two_paths.push_branch()
    publication = two_paths.publish()

    assert gate.returncode == 0, gate.stdout + gate.stderr
    assert PASS_VERDICT in gate.stdout
    assert f"{CACHE_MISS} {base}" in gate.stderr
    # The whole claim: the merge path asked the same question and got the recorded
    # answer, rather than rolling a non-deterministic judge a second time.
    assert publication.accepted, publication.output
    assert f"{CACHE_HIT} {base}" in publication.output
    assert two_paths.judge_runs() == 1
    assert gitops.ref_sha(two_paths.origin, BASE_BRANCH) != base


def test_a_failed_worker_gate_cannot_reach_the_base_through_the_merge_path(
    two_paths: TwoPaths,
) -> None:
    """Replay is not leniency: a recorded failure rejects the publishing push."""
    _work(two_paths)
    base = two_paths.base_sha()

    gate = two_paths.worker_gate(FAKE_LLMLINT_EXIT="1")
    two_paths.push_branch()
    publication = two_paths.publish()

    assert gate.returncode != 0, gate.stdout + gate.stderr
    assert FAIL_FINDING in gate.stdout
    assert not publication.accepted, publication.output
    assert HOOK_REJECTION in publication.output
    # The findings the worker was shown are the findings that rejected the push,
    # and the base never moved.
    assert FAIL_FINDING in publication.output
    assert CACHE_HIT in publication.output
    assert two_paths.judge_runs() == 1
    assert gitops.ref_sha(two_paths.origin, BASE_BRANCH) == base


def test_content_the_worker_never_judged_is_judged_on_the_merge_path(
    two_paths: TwoPaths,
) -> None:
    """A verdict covers the tree it judged, so later commits do not inherit it."""
    _work(two_paths)
    two_paths.worker_gate()

    # The worker settled with one more commit than its gate ever saw. Publishing
    # that must not replay the cleared verdict for the tree it replaced.
    _work(two_paths, "a change the worker's gate never saw")
    two_paths.push_branch()
    publication = two_paths.publish()

    assert publication.accepted, publication.output
    assert CACHE_MISS in publication.output
    assert two_paths.judge_runs() == 2


def test_an_advanced_base_is_judged_again_on_the_merge_path(two_paths: TwoPaths) -> None:
    """Same tree, different comparison: two different diffs, two judgements."""
    _work(two_paths)
    judged = two_paths.base_sha()
    two_paths.worker_gate()

    # Empty, so the published tree stays byte-identical to the judged one and the
    # resolved base commit is the only thing that moved.
    advanced = two_paths.advance_base("advance the base under the settled branch")
    two_paths.push_branch()
    publication = two_paths.publish()

    assert advanced != judged
    assert publication.accepted, publication.output
    assert f"{CACHE_MISS} {advanced}" in publication.output
    assert two_paths.judge_runs() == 2


def test_a_changed_judge_configuration_is_judged_again_on_the_merge_path(
    two_paths: TwoPaths,
) -> None:
    """The rules moved between the two paths, and no file input can see it."""
    _work(two_paths)
    two_paths.worker_gate()

    # The plugin lives outside the checkout, so both trees are byte-identical: only
    # the judge configuration fingerprint can notice, and only if it is in the key.
    two_paths.plugin.write_text(
        two_paths.plugin.read_text().replace("operator entry point", "operator entry point twice"),
        encoding="utf-8",
    )
    two_paths.push_branch()
    publication = two_paths.publish()

    assert publication.accepted, publication.output
    assert CACHE_MISS in publication.output
    assert two_paths.judge_runs() == 2
