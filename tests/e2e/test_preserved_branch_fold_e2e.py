"""E2E: a branch the merge path left behind reaches the next round without a planner.

A lifecycle node whose steps all settled ``done`` and whose publication then failed used
to record no continuation at all. The fold carries a preserved branch only when the
recorded result names one (`orchestrator.replan`: ``status in _PRESERVING_STATUSES and
resume is not None``), so the next round compiled that node **unpinned** and dispatched
it against a fresh branch beside finished work nothing would look at again. A planner
caught it three times in one run by hand-writing the pin; a planner who missed it once
would have paid for the work twice.

The recorder is keyed on the outcome domain, and the domain splits in two by what the
merge path refused — which is the split that decides how much of the branch a
continuation may keep. Both sides are driven here, because proving one proves nothing
about the other:

* **The content was refused** (`REJECTED_CONTENT_OUTCOMES`, here ``gate-failed``). The
  tree itself is what failed, so the continuation clears ``completed_steps`` and
  re-dispatches: republishing the identical tree would be refused identically.
* **The publication mechanism failed** (every other eligible outcome, here ``error``
  from a `commit-msg` hook refusing the squash commit — the second of the two real
  incidents above). The tree was never judged, so the continuation keeps its completed
  steps and the next round retries the publication alone rather than paying an agent to
  re-derive work nothing objected to.

`tests/test_preserved_work_invariant.py` holds the recording invariant across the whole
`LifecycleOutcome` domain against real git; these two prove that what it records
survives a real round fold and drives the round after it.

Everything here is real: a real bare origin, real repository hooks rejecting the way a
repository's own hooks do, registration through the operator's own `just register-repo`,
the real `just run-plan` recording the round, and the real `just next-round` compiling
the round after it. Only onejudge's paid model is the `command`-provider double in
`fake_backend.py`.
"""

# llmlint: ignore-file[e2e_not_mocked] The git origin, the rejecting hooks, the recorded
# round, and the fold are all real; only the paid model is onejudge's own command-provider
# double, the external-boundary exception AGENTS.md documents for this suite.

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from orchestrator import REPO_ROOT, gitops
from orchestrator.provenance import incomplete_commits
from orchestrator.runs import RunId

#: Refuses a commit message that is a subject and nothing else — the shape of a
#: conventional-commit hook that insists every commit say why. The lifecycle's own step
#: commits carry a body and pass it; the publication squash commit is its subject alone,
#: so this refuses exactly the publication, which is the incident being reproduced.
_BODY_REQUIRED_COMMIT_MSG_HOOK = """#!/bin/sh
if [ -z "$(sed -e '1d' -e '/^#/d' -e '/^[[:space:]]*$/d' "$1" | tr -d '[:space:]')" ]; then
  printf 'commit-msg: every commit must explain why; add a body\\n' >&2
  exit 1
fi
exit 0
"""


def _just(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["just", *args], cwd=REPO_ROOT, text=True, capture_output=True)


def _install_hook(checkout: Path, name: str, script: str) -> None:
    """Put ``script`` where this checkout's git — and every worktree cut from it — runs it."""
    hook = gitops.hooks_dir(checkout) / name
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(script, encoding="utf-8")
    hook.chmod(0o755)


def _registered_checkout(tmp_path: Path, origin: Path, name: str) -> Path:
    """A local-workflow checkout, registered the way an operator registers one.

    Through `just register-repo` rather than `Registry.register`: registration is what
    admits an identity to dispatch at all, and it is a command an operator runs. The
    recipe also ranks gate candidates and reports merge-path gate coverage from the
    checkout's effective hooks, so registering past it would exercise a shorter
    admission than any real run gets.
    """
    checkout = gitops.clone(str(origin), tmp_path / name)
    registered = _just("register-repo", str(checkout), "--workflow", "local")
    assert registered.returncode == 0, registered.stderr
    return checkout


def _plan(path: Path, *, name: str, checkout: Path, task: str) -> Path:
    """A one-node lifecycle plan that names no branch.

    Deliberately no ``branch``: the plan pins nothing, so the only thing that can point
    the next round at preserved work is the continuation the settlement recorded.
    """
    path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "name": name,
                "tasks": [
                    {
                        "id": "publish",
                        "repo": str(checkout),
                        "persona": "engineer",
                        "task": task,
                        "workflow": "local",
                        "repo_type": "single-owner",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _common(runs: Path, base: Path, onejudge_bin: str) -> tuple[str, ...]:
    return (
        "--runs-dir",
        str(runs),
        "--base",
        str(base),
        "--onejudge-bin",
        onejudge_bin,
        "--format",
        "json",
    )


def _recorded(runs: Path, run: RunId, round_number: int, artifact: str):
    """One artifact a recorded round wrote, decoded.

    Deliberately unannotated: the ledger's JSON is what the round actually wrote, and
    restating its shape here would be a second declaration of a contract
    `orchestrator.runs` already owns — one this suite could then satisfy while the
    recorded payload had moved on.
    """
    return json.loads(
        (runs / str(run) / f"round-{round_number:02d}" / artifact).read_text(encoding="utf-8")
    )


def _step_event_kinds(runs: Path, run: RunId, round_number: int) -> list[str]:
    """The step lifecycle this round recorded. A step nobody ran never *started*."""
    events = (runs / str(run) / "events.jsonl").read_text(encoding="utf-8").splitlines()
    return [
        record["kind"]
        for line in events
        if (record := json.loads(line))["kind"] in {"step-started", "step-settled"}
        and record["round"] == round_number
    ]


def _branch_discoveries(runs: Path, run: RunId, round_number: int) -> list[tuple[str, str]]:
    """What each branch this round opened was continued from, in the run's own journal."""
    events = (runs / str(run) / "events.jsonl").read_text(encoding="utf-8").splitlines()
    return [
        (record["detail"]["branch"], record["detail"]["resumed_from"])
        for line in events
        if (record := json.loads(line))["kind"] == "branch-discovered"
        and record["round"] == round_number
    ]


def test_a_gate_rejected_branch_is_carried_into_the_next_round_with_no_planner_edit(
    tmp_path: Path, bare_origin, command_base, onejudge_bin: str
) -> None:
    run = RunId("gate-rejected")
    checkout = _registered_checkout(tmp_path, bare_origin(), "gate-rejecting-canonical")
    # A `pre-push` hook that refuses everything is how a repository's own complete gate
    # rejects a publication, and it is the hook dispatch admitted this identity on.
    _install_hook(
        checkout,
        "pre-push",
        "#!/bin/sh\nprintf 'pre-push: complete gate failed\\n' >&2\nexit 1\n",
    )
    runs = tmp_path / "runs"
    plan = _plan(
        tmp_path / "gate-rejected.json",
        name=str(run),
        checkout=checkout,
        task="complete-now write-change publish through the rejecting gate",
    )
    common = _common(runs, command_base(), onejudge_bin)

    rejected = _just(
        "run-plan", str(plan), "--run", str(run), "--workspace", str(tmp_path / "wt"), *common
    )
    assert rejected.returncode == 1, rejected.stderr

    node = _recorded(runs, run, 1, "result.json")["results"]["publish"]
    assert node["outcome"] == "gate-failed", node["detail"]
    resume = node["resume"]
    assert resume is not None, f"the rejected branch was discarded: {node['detail']}"
    branch = resume["branch"]
    # Complete metadata: every field a continuation needs, none of them empty.
    assert branch == node["branch"]
    assert resume["base_branch"] == "main"
    assert resume["pr_base"] == "main"
    # The steps all settled `done`, and none of them may be skipped next round: the
    # merge path refused this tree, so a continuation that republished it unchanged
    # would be refused identically.
    assert resume["completed_steps"] == []
    # `retry` is the mode whose validation demands unattested incomplete provenance,
    # and a whole rejected change carries none — claiming it would produce a pin the
    # next round declines in favour of a fresh branch.
    assert resume["mode"] == "continue"

    # Preserved is a claim about the shared checkout, not about the run's own clone,
    # which is disposable. The recorded checkpoint has to be findable there.
    assert gitops.branch_exists(checkout, branch)
    assert resume["checkpoint"] == gitops.ref_sha(checkout, branch)
    assert not incomplete_commits(checkout, "origin/main", branch)
    assert [commit.sha for commit in gitops.log_delta(checkout, "origin/main", branch)]

    # The repaired gate, so the round the fold compiles can be driven to its end: a pin
    # the next round records and then declines would look identical at the plan alone.
    _install_hook(checkout, "pre-push", "#!/bin/sh\nexit 0\n")

    folded = _just("next-round", str(run), "--workspace", str(tmp_path / "wt"), *common)
    assert folded.returncode == 0, folded.stderr

    (carried,) = [
        task for task in _recorded(runs, run, 2, "plan.json")["tasks"] if task["id"] == "publish"
    ]
    assert carried["resume"]["branch"] == branch, carried
    assert carried["resume"]["checkpoint"] == resume["checkpoint"]
    # The rejected tree is re-derived rather than re-pushed: the carried continuation
    # still names no completed step, so the round below runs the dispatch again.
    assert carried["resume"]["completed_steps"] == []
    # The fold spent one automatic continuation on it, which is what bounds a branch
    # that keeps being rejected instead of looping on it forever.
    assert carried["resume"]["attempts"] == 1

    # And the round it compiled continued that branch rather than cutting a fresh one
    # beside it, which is the whole point of the pin.
    republished = _recorded(runs, run, 2, "result.json")["results"]["publish"]
    assert republished["outcome"] == "merged", republished["detail"]
    assert republished["branch"] == branch
    assert _branch_discoveries(runs, run, 2) == [(branch, resume["checkpoint"])]


def test_a_branch_whose_publication_failed_is_continued_without_redoing_its_steps(
    tmp_path: Path, bare_origin, command_base, onejudge_bin: str
) -> None:
    """The other half of the domain: nothing judged this tree, so nothing re-derives it.

    An `error` settlement is the publication *mechanism* failing around finished work —
    here the repository's own `commit-msg` hook refusing the squash commit the merge
    path builds. Before the recorder was keyed on the outcome domain this recorded no
    continuation at all, because `error` was not one of the endings anybody had
    remembered to handle.
    """
    run = RunId("publication-refused")
    checkout = _registered_checkout(tmp_path, bare_origin(), "commit-msg-rejecting-canonical")
    _install_hook(checkout, "commit-msg", _BODY_REQUIRED_COMMIT_MSG_HOOK)
    runs = tmp_path / "runs"
    plan = _plan(
        tmp_path / "publication-refused.json",
        name=str(run),
        checkout=checkout,
        task="complete-now write-change publish past the commit-msg hook",
    )
    common = _common(runs, command_base(), onejudge_bin)

    refused = _just(
        "run-plan", str(plan), "--run", str(run), "--workspace", str(tmp_path / "wt"), *common
    )
    assert refused.returncode == 1, refused.stderr

    node = _recorded(runs, run, 1, "result.json")["results"]["publish"]
    assert node["outcome"] == "error", node["detail"]
    resume = node["resume"]
    assert resume is not None, f"the finished branch was discarded: {node['detail']}"
    branch = resume["branch"]
    assert branch == node["branch"]
    assert resume["base_branch"] == "main" and resume["pr_base"] == "main"
    # The contrast this journey exists for: the steps that settled `done` are kept, so
    # the continuation retries the publication rather than paying an agent to rewrite a
    # tree no gate ever objected to.
    completed = [step["id"] for step in node["steps"] if step["status"] == "done"]
    assert completed, node["steps"]
    assert resume["completed_steps"] == completed
    # Whole work, so no incomplete marker and no `retry`: marking it would be a lie
    # about the branch, and `retry`'s validation would then decline this very pin.
    assert resume["mode"] == "continue"
    assert not incomplete_commits(checkout, "origin/main", branch)

    # Handed to the shared checkout, where the continuation has to be able to find it.
    assert gitops.branch_exists(checkout, branch)
    assert resume["checkpoint"] == gitops.ref_sha(checkout, branch)

    # The hook a maintainer would fix, so the continuation can be driven to its end.
    _install_hook(checkout, "commit-msg", "#!/bin/sh\nexit 0\n")

    folded = _just("next-round", str(run), "--workspace", str(tmp_path / "wt"), *common)
    assert folded.returncode == 0, folded.stderr

    (carried,) = [
        task for task in _recorded(runs, run, 2, "plan.json")["tasks"] if task["id"] == "publish"
    ]
    assert carried["resume"]["branch"] == branch
    assert carried["resume"]["checkpoint"] == resume["checkpoint"]
    assert carried["resume"]["completed_steps"] == completed
    assert carried["resume"]["attempts"] == 1

    republished = _recorded(runs, run, 2, "result.json")["results"]["publish"]
    assert republished["outcome"] == "merged", republished["detail"]
    assert republished["branch"] == branch
    assert _branch_discoveries(runs, run, 2) == [(branch, resume["checkpoint"])]
    # The work reached the base branch without paying for a second dispatch. A step the
    # continuation settles as already completed still reports `done` — it is done — so
    # the claim is checked where it is visible: round one *started* a step and round two
    # settled the same one having started nothing.
    assert [step["status"] for step in republished["steps"]] == ["done"] * len(completed)
    assert _step_event_kinds(runs, run, 1) == ["step-started", "step-settled"]
    assert _step_event_kinds(runs, run, 2) == ["step-settled"]
