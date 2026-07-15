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
`name` / `owner/name` / URL, **or a local filesystem path**. It selects the
publication repository identity and checkout. For self-dispatch safety, pass
`--execution-checkout <isolated-clone>` to cut the task worktree from that exact
clone while keeping `<repo>`'s publication workflow and post-merge fast-forward.

## Repository identity, checkout roles, and isolation

`Workspace` (`orchestrator/workspace.py`) resolves two independent decisions through
the persistent registry (`orchestrator/registry.py`): the **publication checkout**
selected by the repository argument and the **execution checkout** used to create
the task worktree. Normally they are the same. `--execution-checkout` deliberately
separates them for a safety clone. The lifecycle reports the exact execution path,
publication path, normalized identity, and publication workflow in both human and
JSON output.

The registry's version 2 format stores `identities` keyed by normalized origin and
stores alias-to-path records separately under `checkouts`. Workflow exists only on
the identity. Thus GitHub/SSH URL spellings, canonical clones, safety clones, linked
worktrees, and auxiliary clones resolve to one workflow even when several aliases
share the origin. Legacy flat registries load deterministically when duplicate
aliases agree and are written as version 2 on the next save. Conflicting legacy
entries fail with every alias/path/workflow and the exact migration command; no
workflow is selected implicitly.

The execution checkout hands out a **git worktree per branch** *outside* itself.
N parallel subtasks against a repo get N isolated trees without N clones. The
publication checkout is **never worked in directly and only ever fast-forwarded**
after publication; the execution checkout is fetched and fast-forwarded before a
worktree is cut from it. Worktree
creation, removal, refresh, publication, and integration are serialized across
processes by an OS advisory lock keyed by the checkout's resolved git common-dir.
Locks have bounded waits and report the owning PID/host on timeout. The slow agent
dispatch remains unlocked. Default lifecycle branches include a unique run suffix;
an explicit `--branch` is the intentional resume/override path. An active branch or
occupied worktree is never reset or forcibly removed: inspect the reported path and
recover that run, or remove it manually only after confirming its owner is gone.

The registry uses its own process-shared locks for resolution and first clone. Its
JSON is reloaded and merged while locked, then atomically replaced, so concurrent
registrations are retained and interruption cannot leave partial JSON.

### Self-dispatch hazard: worktrees share the canonical `.git`

A worktree shares its canonical checkout's `.git` common dir (config, refs, object
store). That is safe for ordinary changes, but hazardous when the *dispatched agent
edits the git-manipulating subsystems of this repo itself* (`gitops`, `workspace`,
`lifecycle`, `integrate`): the agent's in-progress code runs through its own
`just check` (whose e2e drives real worktree/merge operations), and a bug there can
mutate the shared `.git` — observed as `core.bare` flipping to `true`, which makes
the canonical checkout report itself bare and mangles the agent's branch history
into spurious `init` commits and mass deletions. Develop those subsystems against an
**isolated clone** (its own `.git`) while preserving the canonical checkout as the
publication selection:

```sh
just repo-task /path/to/ai-orchestrator backend-engineer - \
  --execution-checkout /path/to/ai-orchestrator-isolated
```

The branch is pushed and locally merged because the shared identity is local, then
the positional canonical checkout is fast-forwarded. Recovery is cheap because no
data is lost: `git config core.bare false` restores the checkout, and the agent's
real work is intact at its last commit *before* the `init`-commit corruption.

Register another clone without repeating workflow; it inherits from its origin:

```sh
just register-repo /path/to/ai-orchestrator --workflow local
just register-repo /path/to/ai-orchestrator-isolated
```

A contradictory `--workflow` is rejected. Change publication policy only through
the identity-wide migration command. For the current ai-orchestrator aliases, the
remediation is:

```sh
just migrate-repo-workflow local/ai-orchestrator --workflow local
```

## Local verification before push

Before the final gate, the lifecycle fetches `origin` and merges the current
`origin/<base>` into the dispatched branch. It then runs
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

This repository's complete gate resolves that same comparison ref with
`scripts/comparison-base.sh`. `just gate` discovers the base from a valid remote
HEAD (or a sole remote branch); use `just gate <remote> <base>` when discovery is
ambiguous. The lifecycle exports `ORCHESTRATOR_COMPARISON_REMOTE` and
`ORCHESTRATOR_COMPARISON_BASE` to both verification passes, and the pre-push hook
uses the remote name Git passes as its first argument. Invalid names, missing
refs, and ambiguous remote branches fail with a remediation instead of falling
back to `main`. `just sync` discovers the same branch; `just sync <branch>
<remote>` is the explicit form.

## Merge strategies (where the change lands)

Selected from normalized repository identity metadata (`local` or `remote`). A
task/plan workflow value may assert the expected workflow but cannot contradict a
registered identity; use the migration command between runs to change it. New and
genuinely unknown identities default to `remote`; multiple aliases for one known
origin are not ambiguity, and a filesystem path is not by itself evidence that
direct base updates are intended. Choose `local` explicitly only for a known
no-CI/local-first repo.

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
`title`, `verify_cmd`, `skip_verify`, `merge_policy`, `workflow`,
`execution_checkout`). `run_repo_plan` schedules it
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
runs/<run-id>/round-01/status.json
runs/<run-id>/round-01/result.json
```

The plan mapping is preserved exactly and the result is the command's JSON
payload. The round directory and `running` status are committed before dispatch;
the result and `completed` status are atomic updates. A second process cannot claim
the same explicit run/round. If a process died, inspect its recorded worktrees and
then use `just repo-plan ... --run <id> --recover`; recovery is explicit and never
silently overwrites a result. Pass `--run <id>` to name a run; without it, a fresh unique run id comes
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

For a repository explicitly registered with `workflow: local`, `just integrate`
runs a merge train without letting one failure block the others:

```sh
just integrate claude/api claude/docs --push
just integrate --refresh                 # update discovered claude/* branches only
just integrate --format json             # machine-readable result
```

Before any mutation, integration resolves the supplied checkout back to its
canonical registry entry. Remote and unregistered repositories reject normal
integration and `--push` with exit 2; there is no routine bypass. `--refresh`
remains available because it updates candidate branches without advancing base.

Each permitted candidate fetches the selected remote, merges current
`<remote>/<base>` (then earlier train candidates) in its own worktree, and runs
the gate with `ORCHESTRATOR_COMPARISON_REMOTE` and
`ORCHESTRATOR_COMPARISON_BASE`. A passing branch fast-forwards the local base.
Conflicts and gate failures are reported as skips. `--push` updates the remote
only when the base advanced. Omit branch names to
discover checked-out worktree branches and local branches matching `claude/*`;
use `--pattern` to change the glob or `--gate` to inject a different gate command.
The base and candidate worktrees must be clean.

An incomplete dispatch commit carries the stable trailer
`Orchestrator-Status: incomplete`; legacy `wip: ... (incomplete step)` commits are
also recognized. Integration rejects any candidate whose base-relative history
contains an incomplete marker without a matching lifecycle recovery attestation.
An ordinary later commit cannot clear it. Recover the preserved branch through
its registered workflow:

```sh
just repo-recover <branch> --repo <canonical-checkout>
```

Recovery retains the source branch on failure. It uses an isolated worktree,
fetches and merges current `origin/<base>`, runs the lifecycle gate with the
resolved comparison environment, writes an attestation, and pushes the feature
branch. A remote workflow opens/reuses a PR and enables auto-merge; a local
workflow uses the verified direct-merge strategy. `repo-task-auto` prints this
command when it reports `not-completed`.

## Operating across workers and machines

- **Several agents in one process:** the repo-plan scheduler owns concurrency.
  Per-repo in-process locks serialize short canonical-checkout operations; agent
  dispatches in separate worktrees remain concurrent.
- **Several processes on one machine:** OS advisory locks serialize registry,
  ledger-claim, and shared-git-common-dir mutations. Locks live under
  `$AI_ORCHESTRATOR_HOME/locks` (normally `~/.ai-orchestrator/locks`) and protect
  only that machine. On timeout, inspect the reported PID and host rather than
  deleting a live lock or worktree.
- **Several machines, remote-first:** GitHub is the remote coordinator. Local
  locks and ledgers are independent; unique branches, PR state, required checks,
  and merge/auto-merge coordinate publication globally.
- **Several machines, local-first:** there is no cross-machine lock. Publication
  is optimistic: fetch, rebuild and re-verify the merge, then attempt a non-force
  base update. A loser fetches the winner and retries up to the configured limit.
  A non-bare local origin must set `receive.denyCurrentBranch=updateInstead`;
  otherwise use a bare origin.

The ledger is machine-local, not a global liveness source. On the launching
machine use `just runs`, `just status --all`, and `just history` /
`just history-show <id>`. The recorded branch and worktree are the recovery source
of truth. Resume an intentional branch with `--branch <branch>`. For an interrupted
round, inspect its worktrees and remote branch, then run `just repo-plan <plan>
--run <id> --recover`. Recovery may reclaim a `running` record, so use it only
after proving its owner is gone; never remove or reset an active worktree.

Switch registry workflow metadata only between runs and only with `just
migrate-repo-workflow <alias-or-checkout> --workflow <local|remote>`, which updates
every alias of the normalized identity in one atomic replacement. First finish or recover
active publication, fetch, and confirm the canonical checkout is clean and
fast-forwarded. Before switching to `remote`, configure GitHub permissions,
branch protection, and required checks. Before switching to `local`, confirm all
machines can reach the origin and it accepts safe base updates. Do not switch
metadata to bypass an in-flight PR or failed gate; close or recover that run under
its original workflow.

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
