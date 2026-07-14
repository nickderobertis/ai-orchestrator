# Repo lifecycle: clone → verify → PR/merge, across many repos

The orchestrator doesn't only dispatch a onejudge at a directory — it manages the
**full life cycle** of a change against any repo: acquire the repo, do the work
in isolation, verify it locally, get it reviewed by CI, and merge it. One larger
task becomes **multiple isolated PRs**, coordinated by a dependency DAG. This doc
is the reference for that layer (`orchestrator/lifecycle.py` and friends); the
onejudge dispatch mechanics are in [onejudge-integration.md](./onejudge-integration.md).

## The unit of work: `run_repo_task`

```
ensure clone (once per repo)  →  fresh worktree on a new branch off base
   →  dispatch onejudge in the worktree  (agent makes the change, bypass mode)
   →  commit the agent's changes
   →  fetch + merge the current origin/base into the branch  (pre-handoff sync)
   →  run the repo's own gate on the merged result  (local verification)
   →  push the branch
   →  publish + merge  (strategy: GitHub PR / local direct-merge)
   →  remove the worktree
```

Everything up to "publish + merge" is identical for every repo; only the last
step differs by where the repo lives (see *Merge strategies*). The result is a
`LifecycleResult` whose `outcome` is one of: `merged`, `pr-open` (policy `none`),
`not-completed` (agent hit the turn cap), `gate-failed`, `no-changes`,
`checks-failed`, `closed`, `timeout`, `error`.

`just repo-task <repo> <persona> "<task>"` runs one. `<repo>` is a GitHub
`name` / `owner/name` / URL, **or a local filesystem path**.

## Isolation: one canonical checkout per repo, one worktree per branch

`Workspace` (`orchestrator/workspace.py`) resolves each repo through the persistent
registry (`orchestrator/registry.py`) to ONE **canonical local checkout** — found on
disk, else cloned, then registered — and hands out a **git worktree per branch**
*outside* that checkout: a separate working directory over the canonical checkout's
object store. N parallel subtasks against a repo get N isolated trees without N
clones, which is how parallelism scales without re-paying clone cost. The canonical
checkout is **never worked in directly and only ever fast-forwarded**; it is fetched
and fast-forwarded to stay current before a worktree is cut from it. Worktree
creation is serialized per repo (a lock) because concurrent `git worktree add` races
on the checkout's git metadata; the slow part (the dispatch) always runs unlocked.

### Self-dispatch hazard: worktrees share the canonical `.git`

A worktree shares its canonical checkout's `.git` common dir (config, refs, object
store). That is safe for ordinary changes, but hazardous when the *dispatched agent
edits the git-manipulating subsystems of this repo itself* (`gitops`, `workspace`,
`lifecycle`, `integrate`): the agent's in-progress code runs through its own
`just check` (whose e2e drives real worktree/merge operations), and a bug there can
mutate the shared `.git` — observed as `core.bare` flipping to `true`, which makes
the canonical checkout report itself bare and mangles the agent's branch history
into spurious `init` commits and mass deletions. Develop those subsystems against an
**isolated clone** (its own `.git`) and fast-forward the verified commit into the
canonical checkout instead of self-dispatching onto it. Recovery is cheap because no
data is lost: `git config core.bare false` restores the checkout, and the agent's
real work is intact at its last commit *before* the `init`-commit corruption.

## Local verification before push

Before the final gate, the lifecycle fetches `origin` and merges the current
`origin/<base>` (normally `origin/main`) into the dispatched branch. It then runs
the target repo's gate on that merged result and pushes only after the gate
passes. If the sync conflicts, the lifecycle aborts the merge, reports a
`gate-failed` result with `sync-conflict` detail, and does not push.

This ordering makes the agent's gate exercise the same branch-plus-current-base
diff that the target repo's pre-push boundary enforces, rather than proving a
stale view of the base. `verify.py` detects the target repo's own gate — preferring
an explicit `just check`, then `make check`, `npm test`, `cargo test`, `pytest` —
and runs it in the worktree. A failing gate stops the lifecycle at `gate-failed`
(nothing is pushed). Pass an explicit `verify_cmd`, or `--skip-verify` to skip the
gate; the pre-handoff sync still occurs.

## Merge strategies (where the change lands)

Selected from the canonical registry entry's `workflow` (`local` or `remote`),
with an explicit per-task/plan-node override available. Unregistered GitHub URLs
default to remote and local paths default to local.

- **`GitHubMergeStrategy`** (GitHub repos) — opens a PR, then merges it **only
  once the repo's required (blocking) checks are green**. The default policy is
  GitHub **native auto-merge** (`gh pr merge --auto`), which by construction gates
  on required checks and ignores optional ones — so a non-blocking check never
  triggers or holds a merge. Policies: `auto` (native auto-merge; falls back to
  direct if the repo disallows it), `direct` (poll and merge ourselves on green
  required checks), `none` (open the PR and stop). Required-vs-optional comes from
  `statusCheckRollup.isRequired`; a failed required check ends at `checks-failed`.
- **`LocalMergeStrategy`** (`workflow: local`) — there is no PR/CI to wait on, so
  it builds the verified branch-to-base merge in a detached scratch worktree and
  pushes the result to the origin. This is the model for direct merge into main
  after the checks pass, including GitHub origins intentionally marked local.
  A **bare** local origin accepts the push directly; a non-bare origin needs
  `receive.denyCurrentBranch=updateInstead` so its working tree updates too.

After either strategy reports a merge, the canonical checkout fetches and
fast-forwards its checked-out default branch with `--ff-only`. No merge assembly,
checkout, or hard reset occurs in that canonical working tree.

## Many PRs for one task: `run_repo_plan`

A repo-plan is a DAG whose nodes each carry a `repo` and either a `persona`+`task`
or a `steps` workstream, plus `deps` (and optional `base_branch`, `branch`,
`title`, `verify_cmd`, `skip_verify`, `merge_policy`, `workflow`). `run_repo_plan` schedules it
on the **same engine as `run_plan`** (`plan.schedule_dag`): independent nodes run
concurrently (their PRs open in parallel), a dependent node waits for the one it
needs to **merge** first and then branches off the updated base (each node
re-fetches at start), and a node whose dependency failed is skipped — its PR is
never opened against a broken precondition. `just repo-plan <repo-plan.json>`; see
`examples/repo-plan.example.json`.

## Several onejudge on ONE PR: workstreams

A single PR often wants more than one agent — implement, then add tests, then
review and fix — all on the *same* branch before it merges. A node's **`steps`**
express that: a sub-DAG of `Step`s (`id`, `persona`, `task`, `deps`) that share the
node's one worktree/branch. They run in **topological order, serialized** — they
share a working tree, so two dispatches into it at once would corrupt it; `deps`
give ordering and each step sees its predecessors' commits. Each step commits its
own work (one commit per step, labeled), the accumulated branch is verified **once**
at the end, and it merges as **one** PR. A step that doesn't complete fails the
workstream and skips its dependents (nothing merges). A plain `persona`+`task` node
is just the one-step case. (True *concurrent* steps within a PR would need
sub-worktrees merged back — deferred; cross-PR parallelism is where concurrency
lives.)

## Adaptive replanning: adjust between rounds

The DAG is static *within* a `run_repo_plan` call; the orchestrator adapts
*between* rounds. Every round returns structured results — which PRs merged, which
failed, the judge verdicts — and the orchestrator (an agent following `AGENTS.md`)
decides the next round from them. `orchestrator.replan.next_round` formalizes the
mechanics: given the prior plan, its results, and a small **edits** mapping
(`retry` a failed node with overrides, `split` a too-big node into sub-nodes,
`add` follow-up work, `drop` what's no longer needed), it emits the next round's
plan — carrying **merged nodes out** (done, not re-run) and dropping a new node's
dependency on a merged node as *satisfied* (its predecessor is on the base branch
now). The produced plan is validated, so a bad edit fails loudly.

`repo-plan` records every round by default:

```
runs/<run-id>/round-01/plan.json
runs/<run-id>/round-01/result.json
```

The plan mapping is preserved exactly and the result is the command's JSON
payload. Pass `--run <id>` to name a run; without it, a fresh unique run id comes
from the plan's top-level `name` or filename. The continuation trailer is written
to stderr, so `--format json` stdout remains machine-readable. Use `--no-record`
to opt out or `--runs-dir` to move the ledger.

After inspecting a round, put retry/split/add/drop decisions in `edits.json` and
continue without manually locating the prior files:

```
just runs
just next-round <run-id> [edits.json]
just next-round <run-id> [edits.json] --plan-only
```

`next-round` calls the existing replanner, writes `round-02/plan.json`, runs it,
and records its result. `--plan-only` stops after writing the derived plan. The
lower-level `just replan <prev-plan.json> <result.json> [edits.json]` remains
available. The judgment stays with the orchestrator; these commands only apply
and persist it.

Use `just status` for the joined operational view: worker history, its latest
agent output and commands, commits on the checked-out lifecycle branch, and the
latest ledger round that names that branch. By default it shows only running
tasks. A task is running when its latest history status is explicitly
non-terminal (`pending`, `started`, `running`, or `in_progress`) **and** its
branch is still checked out in the recorded project worktree. This conservative,
testable rule avoids treating an abandoned branch as a live process. Pass a
positive limit (`just status 10`) or `--all` to include recent finished sessions;
use `--format json` for pure machine-readable stdout.

## Integrating completed workstreams

When several dispatched local branches are ready, `just integrate` runs their
merge train without letting one failure block the others:

```sh
just integrate claude/api claude/docs --push
just integrate --refresh                 # update discovered claude/* branches only
just integrate --format json             # machine-readable result
```

Each candidate first merges the current checked-out base into its own worktree.
The normal `just gate` then runs on that updated branch, and a passing branch
fast-forwards the base. Conflicts and gate failures are restored and reported as
skips. `--push` updates `origin` only when the base advanced. Omit branch names to
discover checked-out worktree branches and local branches matching `claude/*`;
use `--pattern` to change the glob or `--gate` to inject a different gate command.
The base and candidate worktrees must be clean.

## Auto mode without approvals — and why bypass

The lifecycle dispatches with `oneharness_mode` defaulting to **`bypass`** — the
no-approval mode (codex's `--dangerously-bypass-approvals-and-sandbox`: no
approval prompts, no inner OS sandbox). That is deliberate and safe **because the
whole environment is already a sandbox** (a container): codex's own
`workspace-write` sandbox (`auto` mode) additionally needs unprivileged user
namespaces, which this host disables, so `auto` can't initialize here anyway —
`bypass` is the working no-approval mode and the container is the boundary. Where
you want a guardrail in place of the OS sandbox, the allowlister `repo-write` hook
is still wired (see [onejudge-integration.md](./onejudge-integration.md)); with a
container boundary it is belt-and-suspenders rather than the containment line.

## The two external seams (and how they're tested)

Git is real everywhere. Only the two things the offline gate genuinely cannot run
for free are injected, each at its own seam:

| Seam | Real thing | Test double |
| --- | --- | --- |
| The paid harness | `dispatch` (onejudge + model) | a `dispatch_fn` that makes a real edit and returns a completed `Report` |
| GitHub PR/CI | `CliGitHubBackend` (`gh`) | a `GitHubBackend` that decides PR/check state but performs the merge with **real git** against a local bare repo |

So the lifecycle e2e (`tests/e2e/test_lifecycle_e2e.py`) drives the whole journey
— clone, worktree, branch, commit, push, and merge — against a real bare git
origin, for both the local direct-merge and the GitHub PR+auto-merge paths, plus
gate-failure, not-completed, no-changes, checks-failed, and a multi-PR DAG. The
merge is never mocked; only GitHub's decisioning and the paid model are.
