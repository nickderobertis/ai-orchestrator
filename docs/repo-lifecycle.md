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
`LifecycleResult` whose `outcome` is one of: `merged`, `pr-open` (successful
publication with policy `none`),
`not-completed` (agent hit the turn cap), `gate-failed`, `no-changes`,
`checks-failed`, `closed`, `timeout`, `stack-conflict`, `error`.

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
publication path, normalized identity, effective repository type, workflow,
merge policy, PR base, and synthetic stack base in human output, JSON, and the
recorded run ledger.

The registry's version 3 format stores `identities` keyed by normalized origin and
stores alias-to-path records separately under `checkouts`. Workflow exists only on
the identity alongside `repo_type` (`single-owner` or `team`). Thus GitHub/SSH URL spellings, canonical clones, safety clones, linked
worktrees, and auxiliary clones resolve to one workflow even when several aliases
share the origin. Flat and v2 registries migrate lazily in one atomic replacement:
`local` is affirmative single-owner evidence; `remote` is inferred by comparing
the normalized GitHub origin owner case-insensitively with `gh api user --jq
.login`. Missing authentication or a non-GitHub origin fails before dispatch
unless the run supplies `--repo-type`. Conflicting legacy
entries fail with every alias/path/workflow and the exact migration command; no
workflow is selected implicitly.

`--repo-type` on `register-repo` persists. The same option on `repo-task`,
`repo-recover`, or `run-plan` is run-only. A plan node's `repo_type` beats the
command option, which beats stored or inferred type. Change stored type with
`just migrate-repo-type <repo> --repo-type <single-owner|team>`; choosing team
also normalizes workflow to `remote`.

The execution checkout hands out a **git worktree per branch** *outside* itself.
N parallel subtasks against a repo get N isolated trees without N clones. The
publication checkout is **never worked in directly and only ever fast-forwarded**
after publication; the execution checkout is fetched and fast-forwarded before a
worktree is cut from it. Before dispatch, the publication checkout must be clean
with the selected root branch checked out; a safety clone never makes an arbitrary
active publication branch the fast-forward target. Worktree
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

### Self-dispatch isolation for this repository

Multiple orchestrators operate concurrently from this repository's canonical
checkout. Treat that checkout strictly as the publication checkout: never author
changes in its working tree, including temporary plan files, personas, or docs.
Dispatch every change into a worktree created from the registered
`local/ai-orchestrator-isolated` execution clone, then let the registered local
workflow integrate it and fast-forward the canonical checkout. This preserves the
orchestrator's narrow authority to merge, fast-forward, sync, or resolve a small
conflict in already-dispatched work; it prohibits using the shared tree to author
the payload.

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
just repo-task /path/to/ai-orchestrator engineer - \
  --execution-checkout /path/to/ai-orchestrator-isolated
```

The branch is pushed and locally merged because the shared identity is local, then
the positional canonical checkout is fast-forwarded. Recovery is cheap because no
data is lost: `git config core.bare false` restores the checkout, and the agent's
real work is intact at its last commit *before* the `init`-commit corruption.

Register another clone without repeating workflow; it inherits from its origin:

```sh
just register-repo /path/to/ai-orchestrator --workflow local --repo-type single-owner
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

For work whose real verifier is remote CI, `verify_via_ci: true` (or the
run-level `--verify-via-ci`) injects a standard CI iteration contract into the
agent instructions. The agent must push and iterate on the branch until its
required checks pass; after dispatch, the lifecycle independently confirms that
the pushed head has a non-empty set of required checks and that all are green.
Red, pending, or absent required checks produce `not-completed` with their names
and states. This mode requires a remote GitHub/PR workflow and fails before
dispatch when no such path exists. An explicit plan-node boolean beats the
run-level flag, including `false` to opt a node out.

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

Selected from normalized identity type plus workflow. A
task/plan workflow value may assert the expected workflow but cannot contradict a
registered identity; use the migration command between runs to change it. New and
genuinely unknown identities must infer from authenticated GitHub ownership or
receive an explicit type; a filesystem path is not ownership evidence. Merge-policy
CLI defaults are intentionally unspecified: node policy beats command policy,
then repository-type defaults apply.

- **Team** — always effective workflow `remote`. Omitted policy opens an ordinary
  ready-for-review PR and returns `pr-open` immediately, without polling checks.
  Explicit `auto` or `direct` merges the PR by that policy. Team plus local
  registration/workflow migration/direct integration is rejected.
- **Single owner** — omitted policy preserves `local` direct publication or
  `remote` auto-merge. Explicit `none` forces remote PR publication for that run
  and leaves the PR open without mutating a stored local workflow. Because the
  local strategy only supports direct publication, an explicit `auto` is reported
  as the effective `direct` policy when the stored workflow remains local.

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

Only after content lands on the canonical checkout's checked-out root does that
checkout fetch and fast-forward with `--ff-only`. A merge into a feature or
synthetic stack base does not advance it. No merge assembly,
checkout, or hard reset occurs in that canonical working tree.

### Merged is not necessarily published

A `merged` lifecycle outcome proves that the change reached its base branch. It
does not prove a downstream release, deployment, package, generated changelog, or
other task destination consumed that change. Identify the destination and its
trigger while planning, then keep closeout open until the destination records the
change.

When release automation selects work by commit convention, that convention is part
of the destination gate. Confirm that the commit produced by the repository's merge
strategy satisfies the configured release trigger, then confirm the expected
release artifact or changelog entry exists. A green tree gate and a successful
merge are insufficient: release-plz can ignore a squash commit whose subject is not
a recognized conventional commit, leaving the change on the base branch but absent
from both the release and `CHANGELOG`.

## Lifecycle nodes in the tracked graph

A lifecycle node is an `agent` node in `just run-plan` with a `repo` and either a
`persona`+`task` or a `steps` workstream. It may also carry `deps`, `base_branch`,
`branch`, `title`, `verify_cmd`, `skip_verify`, `verify_via_ci`, `merge_policy`, `workflow`,
`repo_type`, validated `stack_bases`, `execution_checkout`, or validated `resume`
metadata. Independent top-level nodes run concurrently, and a node whose
dependency failed is skipped. Cross-repository dependencies only schedule. A
successful same-identity dependency not landed on the root base becomes a stack
prerequisite:

The default PR title is derived from Conventional Commit subjects on the branch,
with a non-releasing `chore:` fallback when none is usable. An explicit `title`
must itself be a Conventional Commit subject of at most 72 characters.

All explicit task, base, anchor, and recovery branch names pass Git's literal
branch validator before any Git command; a plan that explicitly combines
`repo_type: team` with `workflow: local` fails validation before dispatch.

- one prerequisite is the child's checkout and PR base;
- root-landed prerequisites are dropped using branch ancestry or the recorded PR
  state (which also covers squash merges and deleted head branches); legacy
  anchors without an identity fall back to their normalized repository slug;
- ancestor duplicates collapse to the descendant first. Several remaining
  prerequisites are merged in declared order into a pushed
  `ai-orchestrator/stack-base/*` branch cut from the root;
  that synthetic branch has no PR and remains remote while it is a PR base;
- stack prerequisites override an explicit `base_branch` as checkout/PR base,
  while that explicit/default branch remains the root for multi-parent assembly;
  an anchor recorded for a different root is a `stack-conflict` rather than being
  silently retargeted;
- a merge conflict aborts before child dispatch/publication as `stack-conflict`,
  cleans its unpublished local synthetic branch, and causes descendants to skip.

The child PR body lists dependency PR links and stack bases. Its final gate runs
against the complete stack, but the PR diff against its stack base is child-only.

### Diff-derived PR descriptions

After the final branch-vs-base gate passes and before a remote PR is created, the
lifecycle performs one additional plain onejudge dispatch with the `pr-author`
persona. That agent reads the completed diff and writes a terse body following
`.github/pull_request_template.md` (required `What` and `Why`, optional
`Additional info`) to a temporary path outside the worktree. The
lifecycle reads and removes that artifact, then appends stack metadata as usual.
This costs exactly one extra dispatch per published PR, including workstream and
draft-checkpoint PRs. An explicit body skips drafting; an explicit title does not.
A failed, incomplete, or empty drafting result falls back to the legacy
deterministic body, so description generation never prevents publication.

Run these nodes with `just run-plan`; `just repo-plan` is a deprecated alias that
accepts old lifecycle-only files unchanged. See
`examples/tracked-graph.example.json` and `examples/repo-plan.example.json`.

## Several onejudge on ONE PR: workstreams

A capable agent should normally implement a coherent change and its tests in one
step. A single PR may still need several agents when distinct workstreams contribute
independent results and a later agent must review and integrate all of them on the
*same* branch before it merges. A node's **`steps`** express that: a sub-DAG sharing
the node's one worktree/branch. Every dispatch already has a simulated-user
supervisor reviewing it, so do not add a separate routine review step; reserve the
dedicated `reviewer` for integration of several agents' independently produced
work. Agent steps have
`id`, `persona`, `task`, and optional `deps`; human steps have `id`, `kind:
human`, `task`, and optional `deps`, with no persona/execution fields. Steps run in
**topological order, serialized** because concurrent dispatches would corrupt the
shared tree. Each completed agent step commits its own work. A failed step stops
the workstream and skips dependents. A plain `persona`+`task` lifecycle node is
the one-step case.

### Human pause and branch continuation

When a human step becomes ready, the node returns `waiting-human`; later steps
are `blocked`. The tracked result exposes a `NODE_ID/STEP_ID` human action and
records step results plus `resume` metadata: branch, root base, PR base,
checkpoint SHA, completed steps, and optional draft PR URL. The temporary
worktree is always removed, but the branch and commits are preserved.

After `just next-round RUN --complete-human NODE_ID/STEP_ID`, the derived node
carries that validated resume metadata. Continuation fetches the branch,
fast-forwards safely, requires the recorded checkpoint to remain in its history,
and skips every recorded completed agent/human step. A missing or rewritten
branch/checkpoint fails as `resume-failed`; a recorded draft closed without merge
also fails explicitly. A draft made ready or merged before final workstream
success is likewise rejected so unfinished work cannot publish while paused. The
harness never infers the human completion.

For `workflow: local`, a pause remains only on the isolated local branch: no gate,
push, or base publication occurs until the final agent steps complete. For a
remote workflow with commits, the pause fetches and merges current
`origin/<pr-base>`, runs the available local gate with the same comparison
environment used at final publication, and pushes the branch without force. It
then creates or reuses a draft PR. A sync conflict or gate failure publishes no
draft; a pause with no commits creates no empty draft. Later pauses reuse the PR.
On final success the existing draft is marked ready, then the repository's normal
`auto`, `direct`, or `none` publication policy applies. Each checkpoint must be an
ancestor of the continued branch, so force-rewritten history cannot be blessed.

## Adaptive replanning: adjust between rounds

The DAG is static within a `run-plan` invocation; the orchestrator adapts between
rounds. Every round returns direct reports, lifecycle results, and ready human
actions. `orchestrator.replan.next_round` applies a small **edits** mapping:
`retry`, `split`, `add`, `drop`, and `complete_human`. Completed nodes landed on
root are removed as satisfied. Completed-but-open dependencies become
`stack_bases` anchors before their IDs are removed; a merge into a feature or
synthetic base carries that landed base until the content reaches root. Those
anchors also pass through a completed top-level human gate or other removed
non-publication node, preserving same-repository ancestry across rounds. Waiting
lifecycle nodes carry their resume checkpoint forward. The produced graph is
validated, so bad edits and human references fail loudly.

Publication closeout is executed by the orchestrator process, not the planner.
The orchestrator surfaces the resulting branch, PR, gate, and publication-checkout
state over the live planner channel; the planner accepts completion only after
reviewing that evidence.

`run-plan` records every invocation by default:

```
runs/<run-id>/round-01/plan.json
runs/<run-id>/round-01/status.json
runs/<run-id>/round-01/result.json
```

The plan mapping is preserved exactly and the result is the command's JSON
payload. The round directory and `running` status are committed before dispatch;
the result and `completed` status are atomic updates. A second process cannot claim
the same explicit run/round. If a process died, inspect its recorded worktrees and
then use `just run-plan ... --run <id> --recover`; recovery is explicit and never
silently overwrites a result. Pass `--run <id>` to name a run; without it, a fresh unique run id comes
from the plan's top-level `name` or filename. The continuation trailer is written
to stderr, so `--format json` stdout remains machine-readable. Use `--no-record`
to opt out or `--runs-dir` to move the ledger.

After inspecting a round, put retry/split/add/drop or `complete_human` decisions
in `edits.json`, or attest a ready human on the CLI:

```
just runs
just next-round <run-id> [edits.json]
just next-round <run-id> --complete-human <node-id>
just next-round <run-id> --complete-human <node-id>/<step-id>
just next-round <run-id> [edits.json] --plan-only
```

`next-round` writes `round-02/plan.json`, runs it with the canonical executor, and
records its result. A human completion is accepted only when the latest recorded
result names that exact ready action; unknown, blocked, agent, and already
completed references exit 2. Each accepted attestation is appended to
`runs/<run-id>/humans.json` with its reference, waiting round, and UTC timestamp.
`--plan-only` stops after writing the derived plan. The lower-level `just replan
<prev-plan.json> <result.json> [edits.json]` remains available.

The graph result state is `complete`, `waiting`, or `failed`; `ok` is true only
for complete. Node states are `done`, `waiting`, `blocked`, `failed`, or `skipped`.
Waiting output includes action prose and direct `unblocks`; blocked nodes include
transitive `blocked_by`. Failure takes precedence over waiting. Exit status is 0
only for complete, 1 for waiting or failed, and 2 for invalid input.

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

An incomplete dispatch commit carries the stable trailers
`Orchestrator-Status: incomplete` and `Orchestrator-PR-Base: <branch>`; legacy
`wip: ... (incomplete step)` commits without the base trailer are also recognized.
Integration rejects any candidate whose base-relative history
contains an incomplete marker without a matching lifecycle recovery attestation.
An ordinary later commit cannot clear it. Recover the preserved branch through
its registered workflow:

```sh
just repo-recover <branch> --repo <canonical-checkout>
```

Recovery retains the source branch on failure. It uses an isolated worktree,
infers a recorded stack/PR base from new preserved commits, fetches and merges
current `origin/<pr-base>`, runs the lifecycle gate with the
resolved comparison environment, writes an attestation, and pushes the feature
branch. Recovery uses the same type defaults and accepts run-only `--repo-type`;
team omission leaves its ready-for-review PR open, remote single-owner omission
enables auto-merge, and local single-owner omission uses direct merge. For an
older stacked preserved commit without the base trailer, pass the ledger's values
explicitly as `--base <root> --pr-base <recorded-pr-base>`; recovery never
fast-forwards the root publication checkout after a merge into a non-root base.
`repo-task-auto` prints this
command when it reports `not-completed`.

### Complete branch after publication failure

`repo-recover` applies only to a branch with lifecycle-preserved incomplete
provenance. It correctly rejects a complete branch:

```text
repo-recover: branch '<branch>' has no lifecycle-preserved incomplete provenance
```

A branch can nevertheless be complete and unpublished: the agent finishes and
commits, then publication fails at push because of the environment. For example,
when an execution checkout has no `refs/remotes/origin/HEAD`,
`scripts/comparison-base.sh` silently degrades base discovery to a fallback that
works only when the remote has one tracked branch. Stale dispatch branches make
that fallback ambiguous, so the pre-push gate exits 2 before publication. Restore
remote-HEAD discovery in the affected checkout with:

```sh
git remote set-head <remote> -a
```

Repair the environment fault, inspect the finished branch's base-relative diff,
and run its complete gate with the comparison remote and base explicit. Then
integrate the existing commits directly through the registered workflow; for a
local workflow, `just integrate <branch> --base <base> --remote <remote> --push`
re-verifies, fast-forwards, and pushes them. The push still runs the complete gate.
Do not invent incomplete provenance to use `repo-recover`, and do not redispatch
an agent to re-author identical content: it adds startup cost without improving
the result.

## Operating across workers and machines

- **Several agents in one process:** the run-plan scheduler owns concurrency.
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
round, inspect its worktrees and remote branch, then run `just run-plan <plan>
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

Switch identity type only between runs with `just migrate-repo-type
<alias-or-checkout> --repo-type <single-owner|team>`. Team selection atomically
normalizes workflow to remote. Finish or recover active publication first.

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
origin, for team open PRs, team/single-owner merged PRs, local direct and
run-only-open publication, linear and synthetic stacks, conflict safety,
gate-failure, not-completed, no-changes, checks-failed, and a multi-PR DAG. The
merge is never mocked; only GitHub's decisioning and the paid model are.
