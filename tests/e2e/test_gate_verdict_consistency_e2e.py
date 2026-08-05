"""E2E: one judged diff gets one verdict, from the worker's own gate to publication.

The complete gate of this repository ends in an LLM judge whose answer is not
reproducible, so its verdict is memoized per judged content and resolved base
commit. That memo only holds the invariant "a dispatched change is not done until
its own gate is green" if the worker's gate and the gate the merge path runs look
the same verdict up. When they resolve different base commits they do not: the
worker passes against a recorded verdict, the `pre-push` hook re-rolls the judge,
and the run dies at closeout on findings the worker never saw and can no longer
clear.

The lifecycle no longer runs the repository's gate itself — the executable
`pre-push` hook it required at dispatch does — so that hook is where this has to
hold. These journeys drive the real lifecycle (real git, real clone, worktree and
branch, real merge) against a repository whose own gate is a miniature of that
tier: a non-deterministic judge behind a verdict store keyed on (content, base
commit) in the shared cache directory, installed as a real `pre-push` hook and run
for real by Git on the publishing push. Only the paid harness is faked, as
everywhere else in this suite, and the fake agent runs the repository's real gate
command before it settles, exactly as a real worker does.

The miniature gate below memoizes *both* verdicts, which this repository's own
llmlint tier deliberately does not — Nx caches successful tasks only, so a red
there re-judges (`docs/repo-lifecycle.md`, "One judged diff, one verdict"). That is
a fixture choice, not a claim about the tier: memoizing the failure is what makes
the second journey's assertion sharp, because a re-roll of a judge that passes
every later time is then unmistakable. What both journeys actually own is narrower
and unchanged — whether the publishing push looks up the same (content, base)
question the worker's gate did.

llmlint: ignore-file[e2e_not_mocked] The judge here is a fixture because the claim
under test is that one tree yields one verdict across two runs, which a genuinely
non-deterministic judge cannot demonstrate. Everything the lifecycle owns — the
dispatch environment, the pre-push hook, git, and the merge — is real.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from conftest import install_pre_push_hook

from orchestrator import gitops
from orchestrator.lifecycle import run_repo_task
from orchestrator.workspace import Workspace

# A miniature of this repository's complete gate. The judge is deliberately
# non-deterministic: its first roll and every later roll are baked in by the
# journey below, so a re-roll can never be mistaken for agreement.
GATE = """#!/usr/bin/env bash
set -euo pipefail
remote=${ORCHESTRATOR_COMPARISON_REMOTE:-origin}
base=${ORCHESTRATOR_COMPARISON_BASE:-}
if [[ -z $base ]]; then
  discovered=$(git symbolic-ref --quiet --short "refs/remotes/$remote/HEAD")
  base=${discovered#"$remote/"}
fi
base_sha=$(git rev-parse --verify "refs/remotes/$remote/$base^{commit}")
content=$(git ls-files -z -c -o --exclude-standard | sort -z | xargs -0 sha256sum |
  sha256sum | cut -d' ' -f1)
store="$ORCHESTRATOR_CACHE_DIR/verdicts"
mkdir -p "$store"
record="$store/$content-$base_sha"
if [[ -r $record ]]; then
  echo "gate: replayed the recorded verdict for base $base_sha"
  exit "$(cat "$record")"
fi
rolls="$ORCHESTRATOR_CACHE_DIR/judge-rolls"
count=$(( $(cat "$rolls" 2>/dev/null || echo 0) + 1 ))
printf '%s\\n' "$count" >"$rolls"
if (( count == 1 )); then status=__FIRST_ROLL__; else status=__LATER_ROLL__; fi
echo "gate: judged this tree against base $base_sha (roll $count)"
printf '%s\\n' "$status" >"$record"
exit "$status"
"""

# The real merge-path gate: Git runs this for every publishing push out of the
# checkout. Its diagnostic names the hook so a rejection classifies as a gate
# failure rather than a raw transport error, exactly as production's does.
HOOK = """cd "$(git rev-parse --show-toplevel)"
if ! bash gate.sh; then
  echo "pre-push: complete gate rejected this tree" >&2
  exit 1
fi
"""


def _gate(*, first_roll: int, later_roll: int) -> str:
    return GATE.replace("__FIRST_ROLL__", str(first_roll)).replace(
        "__LATER_ROLL__", str(later_roll)
    )


def _origin_with_release(
    tmp_path: Path, bare_origin: Callable[..., Path], gate: str
) -> tuple[Path, str]:
    """Seed an origin whose publication base is *not* the branch remote HEAD names.

    A stacked workstream publishes onto its parent branch, not onto the repository
    default. That is the everyday case in which a worker left to discover its own
    comparison base resolves a different commit than the publishing push does.
    """
    origin = bare_origin(files={"gate.sh": gate}, branch="main")
    seed = gitops.clone(str(origin), tmp_path / "release-seed")
    gitops._git(["checkout", "-q", "-b", "release", "origin/main"], cwd=seed)
    (seed / "RELEASE.md").write_text("release line\n", encoding="utf-8")
    gitops._git(["add", "-A"], cwd=seed)
    gitops._git(["commit", "-q", "-m", "chore: open the release line"], cwd=seed)
    gitops.push(seed, "release", set_upstream=False)
    release_sha = gitops._git(["rev-parse", "release"], cwd=origin).stdout.strip()
    assert release_sha != gitops._git(["rev-parse", "main"], cwd=origin).stdout.strip()
    return origin, release_sha


def _run(
    tmp_path: Path,
    origin: Path,
    command_base: Callable[..., Path],
    personas_dir: Path,
    *,
    name: str,
):
    canonical = gitops.clone(str(origin), tmp_path / f"canonical-{name}")
    # The gate this repository publishes through, on the checkout every publishing
    # push originates in. `gitops.clone` installs a permissive hook for the suite;
    # this replaces it with the real judge for these journeys.
    install_pre_push_hook(canonical, HOOK)
    # The publication checkout is only ever fast-forwarded, and must have the root
    # branch this workstream publishes onto checked out before dispatch.
    gitops._git(["checkout", "-q", "release"], cwd=canonical)
    workspace = Workspace(
        tmp_path / f"worktrees-{name}",
        resolver=lambda _spec: canonical,
        workflow="local",
        repo_type="single-owner",
    )
    result = run_repo_task(
        str(origin),
        "complete-now write-change run-worker-gate",
        "engineer",
        workspace=workspace,
        base_branch="release",
        branch=f"verdict-{name}",
        base_path=command_base(),
        persona_dir=personas_dir,
        recorded_gate=["bash", "gate.sh"],
    )
    cache = workspace.ensure_cache_dir(workspace.repo_ref(str(origin)))
    return result, cache


def test_publication_replays_the_verdict_the_worker_cleared(
    tmp_path, bare_origin, command_base, personas_dir
) -> None:
    """The worker's judged verdict is the one that publishes its unchanged tree."""
    origin, release_sha = _origin_with_release(
        tmp_path, bare_origin, _gate(first_roll=0, later_roll=1)
    )

    result, cache = _run(tmp_path, origin, command_base, personas_dir, name="replay")

    assert result.outcome == "merged", result.detail
    worker_gate = (cache / "worker-gate.log").read_text(encoding="utf-8")
    # The worker judged the publication base, not the branch remote HEAD names.
    assert f"judged this tree against base {release_sha}" in worker_gate
    assert "exit=0" in worker_gate
    # One roll of a judge that would have failed every later roll: the pre-push
    # hook replayed the worker's verdict instead of asking the question again.
    assert (cache / "judge-rolls").read_text(encoding="utf-8").strip() == "1"
    merged = gitops._git(["show", "release:CHANGE.txt"], cwd=origin).stdout
    assert merged.strip() == "change from fake agent"


def test_publication_replays_a_recorded_failure_a_fresh_judge_would_pass(
    tmp_path, bare_origin, command_base, personas_dir
) -> None:
    """Consistency is not leniency: a cleared-nothing branch still cannot publish."""
    origin, _release_sha = _origin_with_release(
        tmp_path, bare_origin, _gate(first_roll=1, later_roll=0)
    )

    result, cache = _run(tmp_path, origin, command_base, personas_dir, name="failure")

    assert result.outcome == "gate-failed", result.detail
    assert "pre-push gate rejected" in result.detail
    assert (cache / "judge-rolls").read_text(encoding="utf-8").strip() == "1"
    # The worker retried its red gate and got its own verdict back rather than a
    # second roll: a finding it cannot clear is not one it can outlast either.
    worker_gate = (cache / "worker-gate.log").read_text(encoding="utf-8")
    assert worker_gate.count("exit=1") == 2, worker_gate
    assert "replayed the recorded verdict" in worker_gate
    published = gitops._git(["show", "release:CHANGE.txt"], cwd=origin, check=False)
    assert published.returncode != 0
