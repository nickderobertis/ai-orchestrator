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
   →  push the branch  (the repository's pre-push hook runs its complete gate)
   →  publish + merge  (strategy: GitHub PR / local direct-merge)
   →  remove the worktree
```

Everything up to "publish + merge" is identical for every repo; only the last
step differs by where the repo lives (see *Merge strategies*). The authoritative
closed `LifecycleResult.outcome` domain is `LifecycleOutcome` in
`orchestrator/outcomes.py`; recorded values outside that type are rejected during
recovery. In its common publication states, `merged` means publication created
and landed a commit, `pr-open` means policy `none` left a successful publication
open, and `already-integrated` means the verified content was already present in
the publication base. Failure and recovery outcomes retain their specific
gate, check, conflict, timeout, or retry diagnosis rather than collapsing to a
generic task failure.

Local publication builds its squash in an intentionally detached scratch
worktree. If the squash produces no tree change, closeout treats that as
`already-integrated`, records `publication-finished`, and fast-forwards the
registered publication checkout. A no-change commit is never attempted, so this
case cannot be misreported as `Not currently on any branch`.

Lifecycle agent steps use a larger turn segment than the shared direct-dispatch
budget: repository orientation, implementation, and the complete gate commonly
need more than one short conversation. The executable segment size and bounded
continuation count live in `DEFAULT_LIFECYCLE_STEP_MAX_TURNS` and
`MAX_AUTOMATIC_STEP_RESUMES` in `orchestrator/lifecycle.py`. A step that hits its
cap and leaves a preserved incomplete commit automatically continues on the same
branch, carrying completed step IDs so earlier steps are not re-run. An explicit
step or node `max_turns` replaces the default segment size. Cancellation, a
`worker-died` dispatch signal, missing preserved work, or exhausted automatic
continuations settles as `not-completed` for planner review; partial committed
work retains the same incomplete provenance marker. The orchestrator can retry
through its existing bounded continuation/live-edit path, and a later explicit
retry uses the same recorded resume metadata. A `worker-died` settlement carries
the dispatcher's account of the death — the watchdog pid, the agent child's exit
status, and the tail of its stderr — because a worker that dies before its first
turn leaves no report, transcript, or verdict to read instead.

Each agent step names its conversation for the branch **and** the worktree it
runs in (`dispatch.scoped_session`), and so does the PR-author dispatch. Steps and
automatic continuations within one run share that worktree and therefore one
conversation; a later run pinned, resumed, or recovered onto the same branch cuts
a worktree under its own run root and gets its own. A name that repeated across
runs would ask the harness to resume a conversation it filed under a directory
that no longer exists, which fails before the first turn.

`just repo-task <repo> <persona> "<task>"` runs one. `<repo>` is a GitHub
`name` / `owner/name` / URL, a **local filesystem path**, or an exact checkout alias
shown by `just repos`. It selects the publication repository identity and checkout.
For self-dispatch safety, `--execution-checkout` likewise accepts a path or alias
and cuts the task worktree from that exact clone while keeping `<repo>`'s publication
workflow and post-merge fast-forward.

Before resolving or cloning the target, lifecycle dispatch checks free space on
the filesystem backing Python's temporary directory. It refuses to start below
the conservative default in `orchestrator.scratch.DEFAULT_MIN_FREE_BYTES` and
reports the scratch path, available bytes, and `just sweep-scratch`.
Set `ORCHESTRATOR_MIN_FREE_BYTES` to a non-negative byte count when a host needs a
different threshold. This preflight is a terminal infrastructure failure in a
tracked graph, so it does not consume another round.

The lifecycle acquires the host scratch shared lock before this preflight and
holds it through dispatch, verification, and publication. Destructive cleanup of
aged third-party scratch requires the exclusive lock, so a concurrent sweep
cannot remove scratch that an in-flight target gate owns or is about to use.
The families a dispatch produces itself are exempt from that lock and swept while
it runs, because they only accumulate while dispatches run; their safety comes from
proven non-reference rather than quiescence.

## Repository identity, checkout roles, and isolation

`Workspace` (`orchestrator/workspace.py`) resolves two independent decisions through
the persistent registry (`orchestrator/registry.py`): the **publication checkout**
selected by the repository argument and the **execution checkout** used to create
the task worktree. Normally they are the same. `--execution-checkout` deliberately
separates them for a safety clone. The lifecycle reports the exact execution path,
publication path, normalized identity, effective repository type, workflow,
merge policy, PR base, and synthetic stack base in human output, JSON, and the
recorded run ledger.

The registry's version 4 format stores `identities` keyed by normalized origin and
stores alias-to-path records separately under `checkouts`. Workflow exists only on
the identity alongside `repo_type` (`single-owner` or `team`) and `gate`. Thus GitHub/SSH URL spellings, canonical clones, safety clones, linked
worktrees, and auxiliary clones resolve to one workflow even when several aliases
share the origin. Flat, v2, and v3 registries migrate lazily in one atomic replacement;
the migration detects a gate from a registered checkout or stores `<no-op>`:
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

Each run cuts a **private clone** from the execution checkout and hands out a **git
worktree per branch** from that clone, under
`<workspace-root>/<repo-key>/runs/<run-token>/`. Git's worktree registry, ref
store, and internal locks all belong to one clone, so runs that share a clone
share the machinery that adds, prunes, and removes worktrees — and one run's
cleanup can then reach a sibling's live tree. A per-run clone removes the sharing
rather than the mutual exclusion.

The clone is made with `git clone --shared --no-checkout`, so it borrows the
execution checkout's object store through `objects/info/alternates` and populates
no working tree of its own. A second concurrent run against this repository costs
**184 KB** measured against a current execution checkout; it duplicates no
history. Because a live run reads its history out of the lender, the lifecycle
sets `gc.auto=0` and `gc.pruneExpire=never` on every execution checkout it
borrows from: nothing the lender does on its own can then drop an object a
borrower needs. Do not run `git gc --prune=now` or `git repack -ad` in a
registered execution checkout while runs are active — those override the config
and are the one way to corrupt a live per-run clone.

A run's clone is disposable, so anything that must outlive it — a preserved
branch, a pushed lifecycle branch, a recovery attestation — is copied back into
the execution checkout, which stays the durable record every later run, monitor,
and `repo-recover` reads. A run whose branch was preserved by an earlier run
adopts it from there before resuming.

The publication checkout is **never worked in directly and only ever
fast-forwarded** after publication; the execution checkout is fetched and
fast-forwarded before a run clone is cut from it. Before dispatch, the publication
checkout must be clean with the selected root branch checked out; a safety clone
never makes an arbitrary active publication branch the fast-forward target.
Worktree creation, removal, refresh, publication, and integration are serialized
by an OS advisory lock keyed by the checkout's resolved git common-dir — now
per-run for worktree work, and shared only for the brief local operations that
touch the execution or publication checkout. Fetches run **outside** every
exclusive section, so one slow origin cannot hold another run out.

Contended locks **queue** in the kernel's own `flock` line rather than racing a
non-blocking retry, under a watchdog set by `ORCHESTRATOR_LOCK_TIMEOUT_SECONDS`
whose default is `DEFAULT_LOCK_TIMEOUT` in `orchestrator/coordination.py` — minutes,
not seconds, because a legitimate turn can hold a gate run. `flock` releases on
process death, so a crashed holder hands the queue to the next waiter instead of
wedging it. A timeout reports the owning PID/host. The slow agent dispatch remains unlocked. Default lifecycle branches
include a unique run suffix; an explicit `--branch` is the intentional
resume/override path. An active branch or occupied worktree is never reset or
forcibly removed: inspect the reported path and recover that run, or remove it
manually only after confirming its owner is gone.

Every process working in a run root holds a **shared** occupancy lease on it for
its lifetime, so a re-dispatch that rejoins a run under way is protected too rather
than only its first process. Abandoned run directories are reclaimed by the next
run on the same identity, and only when all three hold: no one holds that shared
lease, the recorded owning process is provably gone, and the clone has no commit
that never reached origin. A run holding
unpublished work is kept; rejoin it with that run's token to recover the work
(tearing its worktree down copies the branch into the execution checkout). Flat
per-branch directories left by the pre-`runs/` layout are never claimed or
reaped, and a run never collides with them.

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

When `register-repo` receives a GitHub repository spec such as `owner/name` and
finds no existing checkout, it clones directly into the managed default
`~/.ai-orchestrator/repos/owner__name` and registers that checkout. An explicit
path or an already discovered checkout keeps the existing selection behavior.

Registration prints ranked gate candidates. Monorepo affected commands (Nx,
Turborepo, Bazel, pnpm, or Lerna) rank ahead of whole-repository gates (`just
check`, `make check`, `npm test`, Cargo, or pytest). Accept one, override it with
`--gate`, or investigate first. A gateless checkout stores `<no-op>` and warns
that it is unproven. The stored identity gate describes the repository's complete
bar and remains available to agents and recovery metadata. The merge path itself
is authoritative: an executable pre-push hook runs the local bar, or required PR
status checks gate remote-first publication. Correct an existing identity across
every alias with `just migrate-repo-gate <repo> --gate '<complete-gate-command>'`.

Registration also audits whether the merge path itself runs a gate. It reports an
executable effective `pre-push` hook (respecting `core.hooksPath`) and required
GitHub status checks on the repository's actual default branch. A configured
hooks directory without an executable `pre-push` does not count. Which evidence
counts depends on the identity's workflow: a **local** workflow pushes straight
to its base branch and never opens a PR, so branch protection has nothing to run
against and only the hook can cover it. A remote workflow is covered by either —
the hook gates the branch push that feeds the PR, and required checks gate the
merge. If nothing applicable is
present, registration succeeds but prints an identity-specific warning; an
unavailable GitHub response is reported as unknown, and local-only origins are
reported as not applicable. Audit every existing identity without re-registering
it with:

```sh
just repos --audit-gate-coverage
```

Lifecycle dispatch and `just repo-recover` repeat this audit and refuse before
starting any work when coverage is missing or unknown, because neither runs the
gate itself any more. They inspect the **execution** checkout rather than an
arbitrary alias: every publishing push originates in one of its worktrees, and
Git resolves hooks through the shared common directory, so its `pre-push` is the
hook that will actually run. Both judge against the run's *effective* workflow, so
a run that publishes locally cannot qualify on required checks a local push never
triggers. The command only reports coverage; it never installs
hooks or changes branch protection.

A contradictory `--workflow` is rejected. Change publication policy only through
the identity-wide migration command. For the current ai-orchestrator aliases, the
remediation is:

```sh
just migrate-repo-workflow local/ai-orchestrator --workflow local
```

## Merge-path verification

Before publication, the lifecycle fetches `origin` and merges the current
`origin/<base>` into the dispatched branch. A sync conflict aborts before any
push.

For a local workflow, each push runs the repository's executable pre-push hook.
The branch push verifies the branch-plus-current-base tree; the later direct-base
push verifies the detached squash publication tree itself. The orchestrator does
not invoke the stored command again before either push. A hook rejection becomes
a recorded `gate-failed` outcome when its output identifies the pre-push gate;
otherwise the lifecycle records `error`, preserving a self-describing publication
failure and Git's diagnostic
because Git cannot distinguish an arbitrary hook rejection from a transport
rejection.

For a remote-first workflow, required status checks are authoritative at PR merge
time. A remote identity may also carry a pre-push hook, and then the branch push
is gated too: a rejection there settles the node the same way, before any PR
exists, rather than surfacing as a raw Git error.
A node's `recorded_gate` runs nothing: it overrides which gate command the
round *records* as the identity's complete bar, and cannot bypass or replace
merge-path coverage. `verify_cmd` is its pre-merge-path spelling and remains
accepted. The gate-skipping switch is gone — the `--skip-verify` flag was
removed, and the `skip_verify` and `no_identity_gate` plan keys are accepted and
ignored so existing plans and in-flight ledgers keep loading. Neither ever
skipped merge-path verification, and nothing can.

Because the gate's verdict now arrives late — as `git push` output — each
lifecycle node records one `merge-gate-coverage` event before it dispatches,
naming the hook path, the required checks, and the identity gate that hook stands
for. That is what separates "the gate ran and rejected this" from "nothing was
ever going to run" without re-auditing the identity after the fact.

Two gate runs are deliberately **not** removed, because no merge-path verifier
subsumes them:

| Gate run | Why it stays |
| --- | --- |
| `just integrate` per candidate | Each candidate fast-forwards the *local* base before the single optional push, so skipping it lets unverified commits reach the local base — and a later aggregate hook rejection can no longer name the branch of the train that broke it. |
| The repository's own `pre-push` hook | It is the merge-path gate. Never weaken it, and never push with `--no-verify`. |

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
`ORCHESTRATOR_COMPARISON_BASE` to **every dispatch and every publishing push of a
workstream**, and the pre-push hook reads that base and
uses the remote name Git passes as its first argument. Invalid names, missing
refs, and ambiguous remote branches fail with a remediation instead of falling
back to `main`. `just sync` discovers the same branch; `just sync <branch>
<remote>` is the explicit form.

### One judged diff, one verdict

A gate tier can end in a judge that is not reproducible — this repository's
llmlint tier does — so its verdict is memoized: `just lint-llm-diff` records the
judged findings and status in the cached Nx `workspace:lint-llm-diff` target,
keyed on the whole workspace content, the resolved base **commit**, and the judge
configuration fingerprint. Ask the same question twice and you get the recorded
answer rather than a second roll of the dice.

That only holds while the key is a function of the judged question alone, so
`scripts/llmlint-fingerprint.sh` resolves the llmlint version *and* the merged
config through `scripts/llmlint-runtime-env.sh` — the one environment
`scripts/llmlint-diff.sh` also judges under. One helper, sourced by both ends, is
the whole mechanism: neither end can read a value the other did not.

`LLMLINT_ONEHARNESS_BIN` is the input that actually varied. `llmlint config`
renders it into its output as `oneharness.bin`, and it is not one value: a
dispatched agent inherits `orchestrator/dispatch.py`'s `REPO_ROOT` — the
orchestrator's own checkout, never the worktree being linted, so the fingerprint's
`{root}` fold-out cannot strip it — or `scripts/session-setup.sh`'s session path,
or nothing at all where `dispatch.py` and `watchdog.py` drop it and the config
renders `"bin": null`. Run the pre-fix fingerprint under those three and it emits
three different digests for byte-identical content. That is the visible symptom:
one judged diff hashes to a key per dispatch, the judge re-rolls every round, and
a branch collects opposite verdicts on the same code.

Reading the caller's environment fails a second, quieter way. Nx scores a runtime
input that exits non-zero as *no contribution* rather than as an error, so a
fingerprint the caller can break does not fail the tier — it silently shrinks the
key to the tree and the base. That is the worse half: a re-roll only costs a judge
call, while a degraded key replays a verdict the judge configuration has since
moved on from. Both directions are held by `tests/e2e/test_llmlint_cache_e2e.py`,
and a cache hit alone is not the proof — a failing fingerprint produces one too,
so the ambient-`PATH` journey reads the fingerprint itself and requires it to
resolve, and to the same digest, under either caller llmlint.

One residual is worth knowing when reading that helper: it *prepends*
`.venv/bin`, but `scripts/setup-llmlint.sh` installs llmlint with `uv tool` into
`~/.local/bin`, so `llmlint` itself is normally resolved from the inherited
`PATH` rather than pinned by the checkout. That is not a split-key hazard, because
the fingerprint and the judge resolve it from the same `PATH` and so can never
disagree — but it does mean a host that upgrades llmlint invalidates recorded
verdicts, which is correct invalidation rather than a miss to investigate.

A second residual sits one layer further out, and it is the other thing that can
make "the failing rules differed this round" true. The remote plugins in
`llmlint.yml` are pinned with an `@<version>` suffix, and llmlint caches each one
at `$XDG_CACHE_HOME/llmlint/plugins/<url-hash>/<version>.yml` and never
revalidates it: under a fixed pin the rules a host judges by are whatever it
fetched the first time, even after the publisher edits that version in place. So
the fingerprint is a function of that cache as well as of the tree — point
`XDG_CACHE_HOME` at a cold directory and the digest moves, because the merged
rules genuinely moved with it. On one host that is not a split-key hazard: nothing
in the lifecycle rewrites `HOME` or `XDG_CACHE_HOME` for a dispatch, and
`scripts/nx.sh` roots the Nx cache under the same variable, so a memo and the
plugin content it was judged with can only move together. Across hosts it means
two machines can be running different judges under identical pins, which the
fingerprint reports as a miss rather than hides. `rm -rf ~/.cache/llmlint/plugins`
refetches; expect it to invalidate every recorded verdict.

**The recorded verdict for exactly that content, base commit, and judge
configuration is authoritative, and the worker's gate is where it is paid for.**
The merge-path gate — the `pre-push` hook, which is the only verifier the
lifecycle now runs — looks up the same key and replays what the worker cleared.
That is the only assignment consistent with the invariant that *a dispatched
change is not done until its own gate is green*: an agent can only clear findings
it was shown, so a verdict that first appears after the agent has settled can
neither be cleared nor appealed. Replay is not leniency — a recorded **failure**
replays as a failure, the hook rejects the push, and the run ends `gate-failed`
even where a fresh roll would have passed.

Keeping the key equal across the two runs is what makes this hold, and the
comparison base is the part that used to drift. A worker left to discover its own
base resolves the remote HEAD, while the publishing push judges the workstream's
`pr_base` — the parent branch for a stacked node, not the repository default. Two
different base commits are two different diffs and therefore two independent
judge rolls, the second one invisible to the only party who could act on it. So
`verify.comparison_env` is the one source of that identity, and the lifecycle
exports it into every dispatch of a workstream **and into every publishing push**,
where the hook reads it. Nothing else stands between the worker's verdict and the
merge, so a push that resolved its own base could merge work whose own gate had
failed.

Where the key genuinely differs the hook does judge again, and its verdict is
then the authoritative one, because it is the only judgement of the content that
will actually land: the base advanced after the worker settled and the merge
changed what is being published, so the worker's clearance never covered it. To
keep that honest rather than silent, a passing `just gate` reports which base
commit was judged and whether the verdict was judged now or replayed from the
record — green is always a claim about one specific base commit.

None of that is provable from one checkout, which is where this went wrong once:
`tests/e2e/test_llmlint_cache_e2e.py` asks twice from the same tree, and
production never does. `tests/e2e/test_llmlint_two_path_verdict_e2e.py` runs the
tier's recipe from both callers instead — a worker worktree carrying the
`LLMLINT_ONEHARNESS_BIN` a dispatch inherits, and a detached scratch worktree
rebuilt by a squash merge carrying only the comparison identity a publishing push
does, both cut from one clone — and counts how many times the judge was rolled for
one content and one base. The answer has to be once. Run it against the fingerprint
as it stood before `2ba9685` and it is twice, in both directions: the primary
journey sees the merge path re-judge work that had already been cleared, and the
failed-gate journey watches a recorded **failure** get overruled by a fresh pass.
The three invalidations are asserted across the two paths for the same reason,
because a fix that made them agree by hashing less would replay a verdict for a
tree nobody judged. The publishing push those verdicts gate is not restaged there;
`tests/e2e/test_gate_verdict_consistency_e2e.py` already drives it through the real
lifecycle.

Forcing a real re-judge is deliberately **per tier and per invocation**:

```sh
just lint-llm-diff origin/main --skip-nx-cache   # re-judge the llmlint tier
just test --skip-nx-cache                        # re-run the test tier
```

An ambient global Nx cache skip (`NX_SKIP_NX_CACHE` / `NX_DISABLE_NX_CACHE`) is
reported and ignored by that recipe and by `scripts/check-nx-cache.sh`. Exporting
one re-rolls the judge from every unrelated command and breaks the checks whose
contract *is* cache replay, so it is not a supported way to re-judge this tier.
Every other Nx target still honours it.

### When a cached verdict may stand in for a verdict on this tree

llmlint is not the only memoized tier. **Every cached Nx target replays a recorded
answer**, including `orchestrator:test` — the tier the coverage floor and the
"tests are the only QA loop" invariant rest on. One rule governs all of them:

> A cached verdict may stand in for a verdict on this tree only when the cache key
> covers everything the check reads.

Where it does, replay is exactly right and the recorded verdict is authoritative:
the same question gets the same answer, and the worker's gate is where that answer
was paid for. Where the key covers less than the check reads, replay is not a
saving but a **false green** — a file the check reads can change the answer without
changing the hash, and the tier reports a pass for a tree whose run would have
failed.

`orchestrator:test` used to be keyed on a hand-listed subset:
`orchestrator/`, `tests/`, `personas/`, `config/`, `pyproject.toml`, `uv.lock`. The
suite reads well past that list — `AGENTS.md`, `docs/`, the `justfile`,
`llmlint.yml`, `scripts/`, `.githooks/pre-push`, `apps/dag-ui/vite.config.ts` — so
editing any of them replayed a green verdict on a tree carrying a real regression.
So the Python targets, which all run from the workspace root over the whole tree,
share the `wholeWorkspace` named input in `nx.json` with the llmlint tier.

#### The narrowed keys, and what earns each

Keyed on the whole workspace, a documentation-only change re-ran the ~8-minute
suite. But "keyed on everything it reads" and "keyed on the whole workspace" are
not the same requirement, and the suite answers at more than one scope, so it is
split at those seams rather than at convenient ones:

- **`orchestrator:test-docs`** runs the tests marked `@pytest.mark.reads_docs` and
  keeps the `wholeWorkspace` key. Seconds, not minutes.
- **`orchestrator:test-recipes`** runs the tests marked `@pytest.mark.reads_recipes`
  — the journeys that build real worktrees and run real package installs to drive
  `just` recipes and shell scripts — keyed on `recipeWorkspace`: the `justfile`,
  `scripts/**`, the root manifests, the fixtures, and the modules that define and
  collect those tests. They read no prose and no `orchestrator/` at all, and most
  commits here touch nothing else, so most commits replay them.
- **`orchestrator:test`** runs everything else that can share a process with
  execnet's receiver thread, keyed on `codeWorkspace` — the whole workspace with
  `docs/**`, `**/*.md`, and the `apps/**` and `packages/**` no Python test opens
  removed.
- **`orchestrator:test-serial`** runs the `single_threaded` remainder under the
  same `codeWorkspace` key. Same tree, same reads; a different process shape is
  not a different scope, so this is a task boundary rather than a key boundary.

`workspace:check-nx-cache` is narrowed on the same principle rather than by tier:
it builds two linked worktrees out of `tests/fixtures/nx-cache/` and drives the
real `scripts/nx.sh` in both, so `nxCacheCheck` carries that fixture, those
scripts, and the root manifest — and nothing else.

Each half of every one of those claims is load bearing. A key must still invalidate
on what its tier reads, and must still replay on what it does not; a key that
covers less than its check reads fails *open*, which is the false green above. That
is exactly how `nxCacheCheck` first shipped — named on its scripts but not on the
fixture the check is built from, so editing the fixture replayed a verdict for a
tree the check had never seen.

Soundness here cannot be a reviewer's recollection — the enumerated list above went
stale exactly that way. So each declaration is enforced where it is made: autouse
guards in `tests/conftest.py` fail an undeclared test the moment it opens something
its own tier's key does not carry, naming the path and the marker it needs. A read
from inside a child process is out of a guard's reach, but a journey that hands a
real tool the whole tree copies the tree first, and copying is itself a read.

`tests/test_nx_cache_scope.py` holds every declaration to its globs — that nothing
but documentation and the front end falls outside the code key, that the recipe key
covers every module routing a test into it and stays inside the code key, and that
the cache-check key carries every `$root/` path its script names. `tests/e2e/
test_nx_cache_scope_e2e.py` then proves each one against real Nx over a copy of
this checkout: for every key, an edit inside it must miss and an edit outside it
must replay.

Three tiers, one answer, and none of them lenient: a recorded llmlint **failure**
replays as a failure, and a tree the suite would fail can no longer replay a pass.

#### Four workers, and the one test that cannot have any

The suite waits on subprocesses rather than on compute — a serial run holds one
core at about 3.5% for a quarter of an hour — so its wall clock is latency and
workers are nearly free. `orchestrator:test`, `orchestrator:test-docs`,
`orchestrator:test-recipes`, and `just test-e2e` all run `-n 4 --dist load`.

Both numbers come from measuring this host, not from a default. One sample each,
same tier and same selection, taken back to back while a second worktree ran its
own suite — so they are comparable to each other and pessimistic in absolute
terms: `-n 4` 322s, `-n 6` 386s, `-n 8` 354s, `-n 14` — what `-n auto` resolves to
here — 345s, and one serial sample at 843s. That serial figure is a single
exploratory reading of the code tier alone, not the tier's baseline; the
pre-parallel median for the whole suite was about seventeen minutes.

The curve is flat past four, because what the run cannot beat is its longest
single test (about 143s), not its core count; more workers buy no wall clock and
take cores this host wants for live dispatches. `--dist loadfile` measured 331s but
raises that floor from the longest *test* to the longest *file*
(`test_workspace_contract_e2e.py`, 293s), which is nearly the whole measurement —
it has no headroom left when the box is quiet. `--dist worksteal` measured fastest
at 280s, but that sample failed a race-window test and the run-to-run spread at a
fixed configuration is the same size as its lead.

Five consecutive runs of both tiers at the chosen setting settled at a 614.5s
median (469.5s / 591.6s / 614.5s / 623.6s / 637.8s), each one 2140 passed, 4
skipped, 125 prose tests, 96.03% coverage. Those totals run the two tiers one after
the other, which is not the shape anything here actually uses: `just test` runs
them concurrently through Nx, and forcing both fresh with `--skip-nx-cache`
measured 311s against the roughly seventeen-minute median the tier cost before.

The code suite therefore runs in **two invocations**, and the second is not an
optimization but a correctness requirement.
`tests/test_runs.py::test_a_signalled_round_records_its_abandonment_and_stops_being_live`
blocks SIGTERM on its own thread and then calls a handler that re-raises that
signal at the whole process. Blocking is per thread, so this only survives in a
process the test is the only thread of — and an xdist worker always carries
execnet's receiver thread, which blocks nothing and dies of the default
disposition the handler just restored. It fails at `-n 1` too: the constraint is
the process, not the load. So it is marked `single_threaded` and scheduled into a
serial invocation rather than rewritten to survive a worker.

#### Two invocations, two tasks, one floor

Those two invocations were chained inside one Nx target by `&&`, which bought
three costs for one line: the four workers idled through the serial run, one
change invalidated both halves, and a serial failure meant the parallel half never
reported at all. They are now `orchestrator:test` and `orchestrator:test-serial` —
separate tasks, neither depending on the other, keyed identically because they
read one tree.

The `&&` was not really about ordering, though; it was about `--cov-append`. The
serial run wrote `.coverage` and the parallel one appended to it, and only the
second reported, which is what kept `[tool.coverage.report] fail_under` evaluated
once against a combined total. Splitting the tasks splits that data, so each tier
now **measures and judges nothing**: `orchestrator:test-serial` measures under
`coverage run` into `.coverage.serial`, `orchestrator:test` measures under
pytest-cov — which is what carries coverage into the xdist workers — into
`.coverage.parallel`, and the uncached `orchestrator:coverage` waits on both,
combines them into `.coverage`, and reports.

`coverage report` is what compares the total to the declared floor, so the floor
still has exactly one source and is still evaluated exactly once. The parallel
tier carries a `--cov-fail-under=0` because pytest-cov otherwise adopts
`fail_under` from the config and would fail every run on its own share; that zero
is the tier declining to judge, not a second floor, and
`tests/test_coverage_gate.py` holds every target to it — no target may name a
non-zero floor, and none but `coverage` may report. That module also drives the
whole shape for real, over a generated package whose total lands in the rounding
band the floor once forgave, and proves the combined total exceeds what either
tier measured alone.

Two more things follow from the split, and both are declarations rather than
conventions. The combine names each tier's data file, so a tier whose data never
arrived fails the command instead of quietly lowering the total the floor is
judged against. And `orchestrator:coverage` is **uncached**: its inputs are two
files on disk rather than the tree, it costs seconds, and a floor that always runs
is one no replay can skip.

`tests/test_nx_cache_scope.py` holds the two tiers to a real partition —
collecting each selector for real and requiring their union to equal the suite —
because two commands selecting on one marker is exactly the shape that drops tests
in silence, and separate targets make that easier to get wrong rather than harder.

**The wall clock is a wash, and the measurement says so.** Three interleaved
samples of each shape on this host: chained 191.8s / 163.1s / 119.0s, split
126.8s / 130.8s / 117.1s. The medians look like a 36s win, but the spread inside
one shape is larger than the gap between them — this box also runs live
dispatches — and the structural difference cannot be that big. Head-of-line
blocking is bounded by the serial invocation's own runtime, and that tier is a
single test: 0.9s. The parallel tier stops rendering its own terminal report and
`orchestrator:coverage` renders it instead, measured at 0.9s. Those cancel.

So the split is not a speed-up, and the third cost the `&&` carried is the one
worth having. Chained, a serial-tier failure meant the parallel half never ran, so
a cycle reported one failure where the suite had several; and re-running the
one-second serial test meant re-running three minutes of parallel suite with it.
Split, both halves report in one cycle and
`nx run orchestrator:test-serial --skip-nx-cache` re-runs a second's work alone.
The two still share `codeWorkspace`, so an edit still invalidates both — that is
correct, because both read the same tree — but they are separate cache entries and
either can be forced on its own.

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

Single-owner automated publication is serialized by a process-shared FIFO merge
queue keyed by the **publication** checkout's git common directory — the one thing
every run of an identity shares, now that each run merges from a clone of its own.
Worktrees and checkout
aliases of one identity therefore enqueue together, including local direct merges,
remote `auto`/`direct` merges, recovery, and the direct `integrate` train. Each
writer waits for its queue position without a bounded merge-lock timeout and runs
its own in-memory merge context when it reaches the head; there is no daemon.
Every waiter may act as the opportunistic queue leader: dead-PID tickets are
reaped before advancing, so a process killed during its turn cannot strand later
writers or cause its unexecuted merge to be replayed. Team PR publication remains
outside this host-side queue. Queue wait telemetry is emitted as `lock-wait` with
the git identity, elapsed seconds, and the original one-based queue position.
If the current base content-conflicts with a local branch at the head, the turn is
dequeued before its original `branch:step` worker session resolves the conflict.
The resolved branch takes a new ticket at the queue tail; it never holds the head
while authoring. Resolve-and-requeue attempts are bounded before the lifecycle
returns `sync-conflict` and retains the branch for manual recovery.

- **`GitHubMergeStrategy`** (GitHub repos) — opens a PR, then merges it **only
  once the repo's required (blocking) checks are green**. The default policy is
  GitHub **native auto-merge** (`gh pr merge --auto`), which by construction gates
  on required checks and ignores optional ones — so a non-blocking check never
  triggers or holds a merge. Policies: `auto` (native auto-merge; falls back to
  direct if the repo disallows it), `direct` (poll and merge ourselves on green
  required checks), `none` (open the PR and stop). Required-vs-optional comes from
  `statusCheckRollup.isRequired`; a failed required check ends at `checks-failed`.
  Before opening a PR, closeout queries all PR states for the same head and base.
  It adopts an existing open PR. It also treats a merged PR as authoritative
  completion when that PR's recorded head SHA equals the branch head being
  published; a stale merged PR whose branch later advanced is not reused.
- **`LocalMergeStrategy`** (`workflow: local`) — there is no PR/CI to wait on, so
  it builds the branch-to-base merge in a detached scratch worktree and pushes
  that exact tree through the repository's pre-push gate. The branch lands as one squashed commit whose
  single parent is the prior base tip and whose message is the merge title. This
  is the model for direct merge into main after the checks pass, including GitHub
  origins intentionally marked local.
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
`branch`, `title`, `recorded_gate`, `verify_via_ci`, `merge_policy`, `workflow`,
`repo_type`, validated `stack_bases`, `execution_checkout`, or validated `resume`
metadata. Independent top-level nodes run concurrently, and a node whose
dependency failed is skipped. Cross-repository dependencies only schedule. A
successful same-identity dependency not landed on the root base becomes a stack
prerequisite:

The default PR title is derived from the most significant Conventional Commit
subject on the branch, with a non-releasing `chore:` fallback when none is usable
— see [A subject names the change, whole](#a-subject-names-the-change-whole). An
explicit `title` must itself be a Conventional Commit subject of at most 72
characters.

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
A failed, incomplete, or empty drafting result is retried once, then falls back
to the legacy deterministic body, so description generation never prevents
publication. Both failed attempts retain their underlying dispatch or harness
detail in the node journal's drafting-fallback event and in the lifecycle
follow-up surfaced to the planner.

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
`origin/<pr-base>` and pushes the branch without force through any configured
pre-push hook. It
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
the result and `completed` status are atomic updates, and an owner that stops
without recording a result leaves `abandoned` instead of `running`. A second
process cannot claim the same explicit run/round. If a process died, inspect its
recorded worktrees and then use `just run-plan ... --run <id> --recover`; recovery
is explicit and never silently overwrites a result. `just runs` and `just status`
report a round whose recorded owner no longer exists as `ABANDONED` rather than as
in flight, so a lifecycle round that lost its executor is visibly waiting for that
recovery rather than looking like work in progress. Pass `--run <id>` to name a
run; without it, a fresh unique run id comes from the plan's top-level `name` or
filename. The continuation trailer is written to stderr, so `--format json` stdout
remains machine-readable. Use `--no-record` to opt out — it claims no round and so
has no ledger to abandon or recover, though it detaches like any other round — or
`--runs-dir` to move the ledger.

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
the gate with `ORCHESTRATOR_COMPARISON_REMOTE` and `ORCHESTRATOR_COMPARISON_BASE`.
Unlike the lifecycle's own gate runs, this one is kept even with `--push`: every
candidate fast-forwards the local base *before* the single push, so the pre-push
hook does not stand between an unverified candidate and the local base, and an
aggregate rejection could not say which branch of the train caused it. A passing
branch fast-forwards the local base. Conflicts and gate failures are reported as
skips. `--push` updates the remote only when the base advanced. Omit branch names to
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

Recovery resolves the branch across every registered checkout of the identity,
publication checkout first, because a lifecycle branch only reaches the
publication checkout once something has already pushed it — a branch that reaches
publication on its first attempt exists solely in the execution checkout the work
was done in. It fetches that ref into the publication checkout (a ref write only;
the publication checkout is still never worked in) and, when the work was done
somewhere the identity does not know about, accepts `--execution-checkout PATH`.
A branch found nowhere names every checkout that was searched.

A merge-path gate rejection still does not publish the work, but it does not
discard it either. Before removing the run worktree, the lifecycle copies the
rejected branch into the registered execution checkout and names both that
checkout and branch in the `gate-failed` result. An operator can inspect the local
branch there or retry it by that branch name without reaching into run scratch.
Because the dispatch itself completed, this preservation does not add incomplete
provenance; genuine stopped dispatches retain the marker contract above.

An automatic continuation may temporarily add that provenance when its first
bounded attempt stops after committing work. If a later attempt in the same
lifecycle completes, the provisional empty marker is removed from the unpublished
branch while any later commits are replayed. Markers inherited from an earlier
run are not provisional and still require `repo-recover` attestation.

Recovery retains the source branch on failure. It refuses an identity whose merge
path has no coverage, exactly as dispatch does, then uses an isolated worktree,
infers a recorded stack/PR base from new preserved commits, fetches and merges
current `origin/<pr-base>`, writes an attestation, and pushes the feature branch
through the same pre-push/required-check merge path. It still refuses a `<no-op>`
identity gate: an identity that cannot name its complete bar has nothing to hand a
resolver worker or a reader of the recovery attestation. Recovery uses the same type defaults and accepts run-only `--repo-type`;
team omission leaves its ready-for-review PR open, remote single-owner omission
enables auto-merge, and local single-owner omission uses direct merge. For an
older stacked preserved commit without the base trailer, pass the ledger's values
explicitly as `--base <root> --pr-base <recorded-pr-base>`; recovery never
fast-forwards the root publication checkout after a merge into a non-root base.
`repo-task-auto` prints this
command when it reports `not-completed`.

Local recovery performs its base sync, recovery attestation, gated branch push,
and gated direct merge inside one FIFO turn. A content conflict dequeues the turn,
resumes the worker session recorded by the incomplete step commit, and requeues at
the tail after the worker commits a resolution. Recovery uses the same bounded
retry policy. Missing or invalid worker metadata, an incomplete resolver, or exhausted
cycles returns `sync-conflict` without discarding the preserved branch.

When a lifecycle attempt exhausts those conflict-resolution cycles after committing
clean work, it records the preserved branch and commit as a retry resume checkpoint.
A later graph retry carries that checkpoint forward and resumes the same branch;
automatic retry behavior is unchanged. An attempt that produced no commit has no
checkpoint and retries from a fresh worktree as before.

Ordinary later rounds treat an unresolved lifecycle node the same way as a retry
replacement: if its prior attempt recorded a committed retry checkpoint, the
unchanged node resumes that branch automatically. A plan's explicit `branch`
always takes precedence over inferred retry metadata. To deliberately discard a
preserved attempt and start fresh, set `branch` to a new valid branch name in the
retry edit. The opt-out belongs on `branch` because it is already the plan's
authoritative branch-routing field; a separate reset flag could conflict with it
and create two sources of truth. The next `branch-discovered` event records
`resumed: true` only for resume metadata, and `false` for an explicit fresh
branch. That precedence covers preserved attempts only. A waiting workstream is
not choosing a branch, so an explicit `branch` never discards its pause resume
and the human steps it already recorded as completed.

To continue authoring after a lifecycle node hits its turn cap, do not relaunch
the original plan. While supervising its existing `orchestrate` run, send a
`retry` live edit through `just channel-reply` to replace only the capped node.
Give the replacement a new id, copy the original node (including
routing fields such as `execution_checkout`, dependencies, or `steps`), and set
the larger `max_turns`. The reconciler discovers the capped node's preserved
lifecycle branch and checkpoint from the run ledger, adds retry-resume metadata
to the replacement, and continues authoring on that branch. `repo-recover` is
different: use it when the preserved commits are already complete and need
verification and publication, not when the worker needs more turns.

### What the base branch carries for a recovered incomplete step

Provenance commits are branch state, never base-branch history. Every path that
advances the base squashes the branch — lifecycle publication, `repo-recover`, and
the `integrate` train alike — so the `chore: ... (incomplete step)` marker and the
`chore: attest verified recovery of preserved work` commit that clears it stay on
the preserved branch; the base branch gets one publication commit, exactly as the
squash-merge model prescribes. That commit carries the fact forward instead: its
message ends with one `Orchestrator-Recovered-Incomplete: <marker sha>` trailer per
marker the branch recovered — in the local squash message, and in the PR body the
remote path publishes from. Nothing hides that a step was left incomplete; the
attestation is a trailer on `main` and a commit on the branch.

A published subject therefore describes the change alone. Provenance commits are
excluded before a subject is synthesized from a branch's commits: a marker's
subject is a valid Conventional Commit, so including it produced published subjects
like `feat: adopt the design system; ## What (incomple…` — the marker's own text was
the task's `## What` heading rather than any description of the work. Both halves
are fixed: a marker names the first line of task prose that carries content, and no
provenance commit reaches a subject at all. Where such subjects and provenance
commits already reached the base branch, they stay: history on the registered base
is never rewritten.

The `integrate` train was the hole in this. It fast-forwarded each verified
candidate, replaying an attested branch's marker and attestation commits onto the
base — which is what `main` shows today. It squash-publishes now: the verified tree
becomes one commit built in a detached scratch worktree that the base checkout
fast-forwards onto, so the checkout an operator has open is still only ever
advanced. Its skip of a branch with *un*attested markers is unchanged; what changed
is what an attested one leaves behind. Two consequences follow from squashing:
a candidate is no longer an ancestor of the base afterwards, so a re-run re-verifies
it and reports `already-merged` from finding no content to add rather than from
ancestry; and a base advanced during the candidate's gate run is `not-ready` rather
than silently reconciled, because the tree that would land is no longer the tree
the gate judged.

### A subject names the change, whole

Publication squashes a branch, so one subject reaches the base branch for all of
its commits. That subject **names the change**: the most significant commit (the
breaking ones first, then by type priority) supplies the description, and the
branch's own history keeps the remaining steps. Its type and breaking marker still
describe the whole branch — they are its release semantics — and its scope is kept
only when every usable commit shares one, since a narrower scope would misdescribe
what landed.

Synthesizing the subject by joining every description and cutting the result to 72
characters is what published `feat: make orchestration-run ownership visible and
enforced; read the r…` onto `main`. A cut description names nothing, breaks
mid-word, and reads as corruption, so **a description is published whole or not at
all**. One that does not fit is not shortened; the next candidate is offered
instead:

1. the most significant commit's description;
2. the first line of task prose that carries content — the planner's own one-line
   name for the whole change, which describes the branch at least as well as any
   commit on it.

There is no third candidate. **A subject that cannot be formed is refused**: the
run settles as an `error` whose detail names the limit and the two ways out —
shorten a commit subject on the branch, or publish with an explicit `title`. A
generic `chore: orchestrated change` was the tempting alternative and is the same
defect in a different costume, because the base branch's history is the durable
record and a subject naming no change makes it a worse record than a refusal does.
Task prose that carries no content line is that same non-name, so it is not offered
as a candidate either. A task's first content line is therefore load-bearing: keep
the `## What` line within the limit — or hand the node an explicit `title` — and a
branch whose commits name nothing still publishes.

Nothing is dropped to buy room. The type, an optional scope, and the breaking
marker are published exactly as the branch's commits carry them, so a branch whose
`feat:` description overflows still publishes a releasing `feat:`, and a valid
common scope survives a fall-through instead of being traded for a description that
fits without it.

`repo-recover` refuses the same way and reports it as `repo-recover: no description
fits …`. It refuses before it attests anything, so the preserved branch keeps its
unattested marker and the base is untouched; the recovery runs again unchanged once
the branch carries a subject that fits.

The one commit exempt from all of this is the `(incomplete step)` marker, which
keeps its suffix through a generic fall-through. It is branch state that no
publication carries, and preserving partial work must never fail on long task
prose; a marker missing its trailer is still recognized by that text.

### Complete branch after publication failure

`repo-recover` applies only to a branch with lifecycle-preserved incomplete
provenance. It correctly rejects a complete branch, and hands over to the verb
that does publish one rather than only refusing:

```text
repo-recover: branch '<branch>' carries no lifecycle-preserved incomplete
provenance: it has commits ahead of origin/<base>, and all of them are complete.
'repo-recover' publishes interrupted work; publish a completed branch with
'just integrate <branch> --repo <checkout>' or through its lifecycle/PR path
```

`just integrate` names `repo-recover` symmetrically, with the exact command, when
it skips a candidate for incomplete provenance. A recovery whose push a pre-push
hook gates also preserves that gate run under the recovery workspace's
`gate-logs/`, named in the reported detail and in `--format json` as `gate_log`,
so consecutive failures on one branch are comparable instead of reading alike.
An identity covered by required PR checks instead has no gate at its push — the
checks decide afterwards — so no verdict is recorded there.

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
- **Several processes on one machine:** each run works in its own clone, so the
  only shared state left is the registry, the ledger claim, and short mutations of
  the execution/publication checkouts. OS advisory locks serialize those, queueing
  contenders rather than racing them; automated single-owner merges use the FIFO
  queue above. Locks and queue state live under
  `$AI_ORCHESTRATOR_HOME/locks` (normally `~/.ai-orchestrator/locks`) and protect
  only that machine. Raise `ORCHESTRATOR_LOCK_TIMEOUT_SECONDS` when a legitimate
  turn (a gate inside a merge) can exceed the default. On timeout, inspect the
  reported PID and host rather than deleting a live lock or worktree.
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

Switch the identity gate only between runs with `just migrate-repo-gate
<alias-or-checkout> --gate '<command>'`. Templates may use `{base}` for the
comparison ref, and the replacement is atomic for every alias.

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
The lifecycle also exports `LLMLINT_ONEHARNESS_BIN` only when the resolved target
identity is `https://github.com/nickderobertis/ai-orchestrator`. That wrapper is
part of this harness's own gate; foreign repository workers inherit
`ONEHARNESS_MODE` but explicitly receive no harness-specific llmlint wrapper.

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

### What a lifecycle journey costs, and which part of it is a choice

**Where the time is.** Not in git. Instrumenting every subprocess and every
lifecycle phase of the slowest journeys puts essentially the whole of each one
inside `run_repo_task` → `dispatch` → the real `onejudge` subprocess: in
`test_real_lifecycle_dispatch_drafts_pr_bodies_and_preserves_fallbacks`, the 771
synchronous subprocesses it runs — almost all of them git — account for about one
second of the eighteen its twelve `run_repo_task` calls take. The
clone, the worktree, the commit and the push are not the cost and never were; the
**dispatch count** is. Read a journey's price as its number of dispatches times the
price of one, and optimise only those two numbers.

**What one dispatch costs.** A step that completes on its first turn costs about
0.4s, and that figure is flat in the turn cap — a cap it never reaches charges
nothing. A step that *exhausts* its budget costs about 1.0s at a cap of one and
about 0.5s more per additional turn of cap, because it is dispatched
`MAX_AUTOMATIC_STEP_RESUMES + 1` times and every turn of every segment is two
provider processes. Those numbers are what make the two paragraphs below the only
levers: the cap on an exhausting step, and the fixed cost every dispatch pays.

**The fixed cost, and the part of it that was waste.** Every dispatch tears down
its worker tree through `terminate_processes`, `terminate_process_group` and
`terminate_tree`, and each of those holds a `SIGKILL` back from its `SIGTERM` for
a grace period. That grace exists so a harness with a shutdown handler can use it.
It was slept out unconditionally, including on the overwhelmingly common path
where the dispatch had already finished and there was nothing left to be graceful
toward — four grace periods, 200ms, per dispatch, which was 2.49s of a 9.04s
`test_ordinary_next_round_resumes_committed_lifecycle_branch`. `_await_shutdown`
in `orchestrator/watchdog.py` now waits on the processes rather than on the clock:
same ceiling for anything still running, nothing for anything already gone. It is
a per-dispatch saving, so it applies to every journey in the suite. It is also a
*latency* saving — the waits it removes consumed no CPU, so it shows up in full on
a quiet host and is progressively masked when this host is already oversubscribed
by concurrent dispatches.

**What is left is the round, and the round is the unit under test.** A journey
that proves four branch-selection behaviors across four rounds pays for four
rounds; one that asserts twelve distinct PR-body outcomes pays for twelve
publication journeys. Of the six slowest journeys in the lifecycle e2e:

| Journey | Why it costs what it does |
| --- | --- |
| `..._drafts_pr_bodies_and_preserves_fallbacks` | 35 dispatches, every one of them a first-turn completion at the 0.4s floor, for twelve asserted PR-body and title outcomes. Cost *is* coverage. |
| `test_ordinary_next_round_resumes_committed_lifecycle_branch` | Four rounds for four branch-selection behaviors — first attempt, ordinary resume, explicit fresh branch, explicit pin — each needing a node that commits and then fails. |
| `test_a_node_that_cannot_finish_settles_instead_of_being_redispatched_forever` | Its subject *is* `MAX_AUTOMATIC_ROUND_RESUMES`. Every round it drives is the bound being exercised. |
| `test_an_explicit_retry_restores_an_exhausted_preserved_branchs_budget` | Needs the same exhausted budget as a precondition, and the ledger it asserts against is written by real rounds. Cheaper only by fabricating the state the `retry` is supposed to act on. |
| `test_lifecycle_failure_survives_simultaneous_deferred_teardown` | Exactly one not-completed dispatch. That is the floor for its outcome. |
| `test_repo_plan_ledger_and_guided_next_round` | Three dispatches, plus about a third of its time in real `just telemetry` and `just runs` invocations — the CLI boundary it exists to prove. |

None of them is reducible by dropping work it does not need; each is reducible
only by dropping a case it asserts. What remains after the cap below and the
teardown above is the `onejudge` launch and the provider processes underneath it.

What sits on top of the floor is a choice, and it used to be an accidental one. A
step that never completes spends its whole turn budget and is then automatically
resumed `MAX_AUTOMATIC_STEP_RESUMES` more times, and every turn is two provider
processes. At `DEFAULT_LIFECYCLE_STEP_MAX_TURNS` that is 147 provider processes
and about twelve seconds for a single dispatch whose only job is to reach *a* cap.
Naming a small explicit cap at those call sites — `EXHAUSTED_STEP_MAX_TURNS` in
the lifecycle e2e — keeps the exhaustion, the three segments, the preserved
branch, and the round-level budget exactly as they were, for 15 processes instead
of 147. The default's own height stays pinned by
`test_run_repo_task_journals_a_step_that_hit_the_turn_cap` in
`tests/test_lifecycle_unit.py`, which spends no processes at all. A journey about
what happens *at* a cap should say which cap it means; inheriting the production
default there buys no coverage and costs the whole difference.
