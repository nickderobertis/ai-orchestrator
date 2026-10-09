Plan project: authoring:sweep-fast-again

# Make the branch sweep fast again, and stop session start waiting on it

## What

Plan, in the `authoring` source as `authoring:sweep-fast-again`, one DAG that delivers the
six goals below across onevcs, onepipeline, onepipeline-ui and this repository. The findings
behind them follow.

### What was found (measured on this host on 2026-09-29; raw evidence in `scratch/sweep-cost/`)

- **The regression.** Commit `2c3a2966` (2026-09-28) moved `config/onevcs.version` from 0.33.0
  to 0.34.1 and `config/onepipeline.version` from 0.48.1 to 0.51.0. onevcs 0.34.0 (#241,
  `58591f3`) added a *finished-branches* retirement pass to `onevcs sweep`. The pass runs over
  every branch of every registered identity (`crates/onevcs/src/sweep.rs` `finished_branches`
  → `retire::pass_over`, `Scope::All`). The engine (onepipeline `cb8228d`, v0.51.0) runs the
  same pass from its idle maintenance, once per identity (`src/maintenance.rs` `retire()` →
  `onevcs::retire_finished`), at most every 600 s per driver.
- **Session start pays for it.** `scripts/session-setup.sh:459` runs the sweep recipe (`onevcs
  sweep --min-age-hours 4`, then `oneagentgraph sweep`) in the foreground of the
  `SessionStart` hook. That hook's timeout is 300 s. The hook fires for every Claude session
  that starts or resumes in this repository, in both isolated clones, and in **every dispatched
  worker**. Workers run `claude -p` inside an ai-orchestrator worktree, which loads that
  worktree's `.claude/settings.json`. Worker transcripts show the hook averaging 10–30 s
  before 2026-09-28. Since then, all 48 dispatch sessions hit the 300 s timeout
  (`worker-hook-durations.tsv`). A cancelled hook also skips everything session-setup runs
  after the sweep.
- **The sweep's cost.** An instrumented `onevcs sweep --dry-run --min-age-hours 4` took
  1,336 s and made 69,148 git/gh subprocess calls, of which only 14,839 were distinct
  (`dry-run-calls.tsv`, `dry-run-report.txt`):
  - 1,307 single-ref `git ls-remote --exit-code origin <ref>` calls took 764 s, run
    serially. They covered only 642 distinct refs; `refs/heads/main` was asked 11 times.
  - 41,727 `git cat-file -e <sha>^{commit}` calls covered only a few hundred distinct commits,
    with a median of ~107 checks per commit. 9,818 `git merge-base --is-ancestor` calls
    tested each of ~83 commits against each of ~123 base tips, one pair at a time.
  - 38 `gh pr view` calls, all distinct. That is roughly 40 GraphQL points, which is not a
    quota problem.
  - The outcome: 639 branches examined, **none retirable**. 549 were
    `unmerged-unique-commits`, 78 were `is-base`, and a handful were checked-out, dirty, or
    held. The pass re-derives the same "keep" for the same branches on every run. The host
    has 751 session records.
- The engine's idle pass does retire branches: seven `branches-retired` records since
  2026-09-28 in `runs/*/`.

### Goals

1. **Session start never waits on a sweep.** This covers a manager session and a dispatched
   worker alike, in any ai-orchestrator checkout or worktree. The rest of session setup runs
   every time.
2. **The sweep does not stack.** Several sessions starting at once, or a session starting
   while a sweep is already running, must not start overlapping host-wide sweeps. Today two
   ran side by side.
3. **Everything the sweep reclaims is still reclaimed on this host.** The engine's idle
   maintenance covers only branch retirement. `onevcs sweep`'s workspace and session-record
   reclamation and `oneagentgraph sweep`'s scratch reclamation have no other automatic caller
   here, so they must still run somewhere.
4. **onevcs's retirement pass is cheap when nothing changed.** onevcs records each branch's
   verdict and re-derives it only when something the verdict depends on has changed. The
   remote refs a pass needs are read in bulk per repository, not one ref per call. No
   existence or ancestry question is asked twice within one pass. This applies wherever the
   pass runs: `onevcs sweep`, `onevcs retire-finished`, and the engine's idle maintenance
   through the library. The user's bar is "fast again": a repeat sweep on this host with
   nothing changed should be back near the pre-2026-09-28 cost of tens of seconds, not
   minutes.
5. **This host actually runs the fix.** Per AGENTS.md "Which pin governs a dispatch", the
   engine's idle pass is governed by `config/onepipeline.version`, through the onevcs it
   links. `config/onevcs.version` governs only the CLI the sweep recipe runs. Both have to
   end up on releases that carry the change.
6. **A closing driver never waits on idle maintenance beyond the repository in progress.**
   Today a driver's close-out joins its whole maintenance thread (onepipeline v0.52.1
   `src/maintenance.rs`: `Maintenance::close` → `sweep.join()`, and `impl Drop for Sweep`),
   and neither `maintain_pools` nor `retire()` checks for a stop between identities. On this
   host, run `sweep-fast-again-planning-2-design` settled its only node at 13:30:59 and wrote
   its result at 13:48:41. The driver's time buckets account for none of that gap, and its
   status still reported "pool maintenance … in progress, started 13:09:48" after the node
   settled. So every run settling during a pass returns, fires its hooks and hands control
   back late. The required property:
   - once a driver begins closing, its maintenance stops at the next identity boundary, in
     both the pool pass and the retirement pass;
   - it is never interrupted inside an identity, so no retirement's compare-and-delete or
     lease is abandoned partway;
   - what the pass did is still journalled, and the record says it was cut short and names
     the identities it did not reach.

## Why

The user noticed that "the session start hook … seems like it's taking a long time every
time". Once the cause was found, they said: "I agree with onevcs recording its results so it
only has the rerun for each if something changed, seems like that could make it fast again."
They asked for both changes in one DAG. Their goals are that starting a session, or
dispatching a worker, in this repository stops costing a five-minute wait, and that the sweep
becomes cheap enough to run routinely again. Every dispatch into this repository since
2026-09-28 has lost five minutes to this before starting its task.

On goal 6, the user asked why maintenance, which "is supposed to be a background task",
would block anything. They chose to stop it at an identity boundary when the driver closes,
over letting it continue in the background after the run. A thread cannot outlive its driver
process. Handing the rest to a detached process would duplicate the host's own hourly sweep
(goal 1) and bring back the overlapping passes goal 2 removes. Other drivers' idle passes and
the host sweep pick up whatever a cut-short pass left due.

## Acceptance criteria

- The project `authoring:sweep-fast-again` exists in the `authoring` source. Read as a whole,
  it delivers each of goals 1–6 through a node that can be named for it.
- Goal 6 is an onepipeline node that depends on no onevcs node. Its change is carried by the
  engine release the plan already links and adopts, so it adds no release and no adoption
  node. Its criteria require a test driving a real driver whose idle maintenance runs over
  several identities, each slow. It fails if settling the run waits for more than the
  identity in progress, if a retirement is interrupted inside an identity, or if the
  journalled record does not name the identities left unreached. The plan's contracts
  section states the cut-short record's shape.
- The verdict-reuse, bulk remote-read and in-pass dedupe changes are onevcs nodes, stated as
  general features of that library with their contract. The contract says:
  - what a recorded verdict is keyed on;
  - when it is re-derived;
  - where it is stored, and how that storage coexists with the host-wide registry and
    older linked copies;
  - what happens when a key input is unreadable.
  No ai-orchestrator node fills any part of that gap.
- The ai-orchestrator session-start change does not depend on any onevcs, onepipeline or
  adoption node. It can land and remove the stall before the library work is released.
- Every onevcs or onepipeline change this host must run is adopted by a node of this
  repository, on the pin AGENTS.md names as governing. The node is sequenced `published`
  behind its producer and names no `consumes` and no version. The order of the onevcs and
  engine pins is justified from your research against AGENTS.md's host-wide onevcs pin
  rule and `tests/test_linked_libraries.py`, not assumed.
- The onevcs node's criteria require tests that drive the real retirement interfaces (the
  `sweep` and `retire-finished` commands and the library pass the engine calls) over real git
  repositories with real remotes:
  - one fails when a repeat pass with unchanged inputs re-derives verdicts or reads remote
    refs one ref at a time;
  - one fails when a repeat pass over a fixture of many branches with unchanged inputs costs
    more than a stated, small bound, so "fast again" is held by a check and not only by a
    call count;
  - one fails when a changed input, such as a moved branch tip or remote ref, still reuses
    the old verdict (retirement safety).
- The ai-orchestrator session-start node's criteria require tests that drive the real
  `session-setup.sh` and recipes. They fail if session start waits on a sweep, if two sweeps
  overlap, or if the workspace, record or scratch reclamation loses its only caller.
- No criterion names a repository's complete gate, a live CI lane, a merged change request,
  a release, or a third party, or reads as an instruction to cut or name a branch.
- Every claim the completion report makes about the plan is true of the store as it finally
  stands.
- The plan holds to the manager's rulings on the planner's questions:
  1. The detached, one-at-a-time session-start sweep is an ai-orchestrator node. There are
     no library single-flight nodes.
  2. A session start launches a sweep only when none is running and none finished within
     the last hour. The interval is stated in one place.
  3. The chain is onevcs → onepipeline (link) → onepipeline-ui (link) → one
     ai-orchestrator adoption node that moves the reconciled pins together. The onevcs node's
     contract stores recorded verdicts in a new sidecar under `$ONEVCS_HOME` that no older
     onevcs reads, with no registry schema change.
  4. The adoption node never moves any pin to a release older than the tree holds when it
     starts. The collision with `authoring:follow-up-board-reads-plan` is recorded as a
     planner exception.
  5. Session-record pruning is out of scope.
  6. Goal 6 stops maintenance at an identity boundary on close. It does not hand the pass
     to a detached process that outlives the run.
- The existing project `authoring:sweep-fast-again` (written by `sweep-fast-again-planning`
  and completed by `sweep-fast-again-planning-2`, whose five nodes were reviewed and whose
  design document is on the board) is extended with goal 6's node and whatever sequencing it
  needs, not rewritten. Nodes that already meet this brief stay as they are.

## Constraints

- **Retirement safety is not traded for speed.** A reused verdict may only skip work. It must
  never make a branch retirable that a fresh derivation would keep. A cached verdict's key
  must cover everything the verdict reads (AGENTS.md: "a memo may stand in for a verdict only
  when its key covers everything the check reads"). That includes the branch tip, the base
  tip, the branch's remote ref, change-request and landing state, supersession records, and
  live holders. Anything unreadable falls back to deriving fresh, never to "unchanged".
- **The onevcs change is a general feature of onevcs**, released and adopted here. It is not
  an ai-orchestrator workaround (AGENTS.md "A canonical example of the libraries, not a
  workaround layer"; `tests/test_upstream_workaround_rule.py`). If the ai-orchestrator side
  needs a verb, flag or contract that onevcs or oneagentgraph lacks, that is a gap to
  surface, not code to write here. Running this repository's two sweep verbs detached from
  session start, one at a time host-wide, is **not** such a gap. It is this repository's own
  composition of two verbs, which neither library owns, and it is done here the way
  `scripts/repos-bootstrap.sh --detach` already does it (the manager's ruling on the
  planner's first question). Whether onevcs's own pass is safe against a concurrent pass is
  onevcs's to answer, and its node surfaces it if not.
- **Moving the onevcs pin moves the whole host** (AGENTS.md: the registry schema migrates on
  first contact, and older linked copies then fail). Any adoption must respect that, including
  the order of the onevcs pin and the engine pin.
- The rest of AGENTS.md applies as written. That includes the self-dispatch rule for this
  repository, the prose that currently says "Session setup runs it" (the sweep recipe
  paragraph), and tests that drive the real recipes and scripts.

## Suggested shape (a suggestion only; the decomposition is yours)

- **onevcs:** a verdict record per branch keyed on its inputs, reused when they are unchanged.
  One bulk read of each repository's remote refs per pass, with the pass reading refs from
  that. A per-pass memo, or one batched object and ancestry read per base, in place of
  per-pair subprocesses. Session-record pruning is out of scope (ruling 5 above).
- **onepipeline:** a release linking the fixed onevcs.
- **ai-orchestrator:** stop running the sweep in the foreground of `session-setup.sh`. Give it
  one home that never blocks a session and never stacks, and adopt the releases. This change
  is independent of the onevcs work and should not wait on it, because it removes the
  five-minute stall today.

## Repositories

- `github.com/nickderobertis/ai-orchestrator` (this repository)
- `github.com/nickderobertis/onevcs`
- `github.com/nickderobertis/onepipeline`, if the engine needs a release to link the fix

## Out of scope

The GraphQL spend of the follow-up tickets tool's board paging. A separate brief covers it
(`scratch/briefs/follow-up-board-reads.md`).

