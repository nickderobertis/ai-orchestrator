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
   →  run the repo's own gate in the worktree  (local verification)
   →  commit + push the branch
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

## Isolation: one clone per repo, one worktree per branch

`Workspace` (`orchestrator/workspace.py`) clones each repo once under its root and
hands out a **git worktree per branch** — a separate working directory over the
same object store. N parallel subtasks against a repo get N isolated trees
without N clones, which is how parallelism scales without re-paying clone cost.
Different repos get different clones. Clone/worktree creation is serialized per
repo (a lock) because concurrent `git worktree add` races on the clone's git
metadata; the slow part (the dispatch) always runs unlocked.

## Local verification before push

The orchestrator never pushes a change it hasn't proven locally. `verify.py`
detects the target repo's own gate — preferring an explicit `just check`, then
`make check`, `npm test`, `cargo test`, `pytest` — and runs it in the worktree.
A failing gate stops the lifecycle at `gate-failed` (nothing is pushed). Pass an
explicit `verify_cmd`, or `--skip-verify` to rely on CI alone.

## Merge strategies (where the change lands)

Selected automatically from the repo; override with an explicit strategy.

- **`GitHubMergeStrategy`** (GitHub repos) — opens a PR, then merges it **only
  once the repo's required (blocking) checks are green**. The default policy is
  GitHub **native auto-merge** (`gh pr merge --auto`), which by construction gates
  on required checks and ignores optional ones — so a non-blocking check never
  triggers or holds a merge. Policies: `auto` (native auto-merge; falls back to
  direct if the repo disallows it), `direct` (poll and merge ourselves on green
  required checks), `none` (open the PR and stop). Required-vs-optional comes from
  `statusCheckRollup.isRequired`; a failed required check ends at `checks-failed`.
- **`LocalMergeStrategy`** (local-path repos) — there is no PR/CI to wait on, so
  it **merges the verified branch straight into the base branch** with real git
  (`--no-ff`, a revertable merge commit) and pushes it to the local origin. This
  is the model for a local repo: *direct merge into main after the checks pass*.
  A **bare** local origin accepts the push directly; a non-bare origin needs
  `receive.denyCurrentBranch=updateInstead` so its working tree updates too.

## Many PRs for one task: `run_repo_plan`

A repo-plan is a DAG whose nodes each carry a `repo` and either a `persona`+`task`
or a `steps` workstream, plus `deps` (and optional `base_branch`, `branch`,
`title`, `verify_cmd`, `skip_verify`, `merge_policy`). `run_repo_plan` schedules it
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
`just replan <prev-plan.json> <result.json> [edits.json]` → next plan; feed it back
to `just repo-plan`. The judgment (what to retry/split/add) stays with the
orchestrator; `replan` just applies it correctly.

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
