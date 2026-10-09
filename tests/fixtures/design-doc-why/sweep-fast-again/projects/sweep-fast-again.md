---
title: sweep-fast-again
status: backlog
metadata:
  onepipeline.schema_version: 3
  onepipeline.concurrency: 3
  "onepipeline.goal": {"text":"Session start in any ai-orchestrator checkout or worktree never waits on a sweep. The host sweep runs detached, one at a time, at most hourly, and remains the caller of every reclamation it did. onevcs's finished-branches retirement pass records each branch's verdict and reuses it while every input it reads is unchanged, reads remote refs in bulk, and asks no question twice per pass. A closing onepipeline driver stops its idle maintenance at the next identity boundary and journals what it did not reach. This host runs that onevcs and that engine through both the engine pin and the onevcs CLI pin."}
  "orchestrator.plan-review": {"by":"review-plan","key":"a9b50976b80dc264d454beef2f4a12390c0a6ff57bd1d01343986cd23b1201f7","reviewed_at":"2026-09-29T14:29:57.311942+00:00"}
---
Execution plan sweep-fast-again.

**Four repositories, six nodes.** The session-start fix stands alone and lands first, so the
five-minute stall ends without waiting on any release. In parallel, onevcs gains verdict
reuse, bulk remote reads and in-pass dedupe, and the engine learns to stop its idle
maintenance at an identity boundary when its driver closes. The engine links that onevcs, the read API links
that engine, and this host then adopts all of it in one node. `tests/test_linked_libraries.py`
holds the onevcs CLI pin equal to the onevcs the engine links, and holds the read API to
exactly the pinned engine.

| node | repository | what it delivers | goals | depends on |
| --- | --- | --- | --- | --- |
| `aio-session-sweep` | `github.com/nickderobertis/ai-orchestrator` | session setup starts `just sweep` as a detached job and returns at once. One host-wide `flock` means no two sweeps overlap, including one run by hand. A job starts only if none finished in the last hour. The job remains the only automatic caller of workspace, session-record and scratch reclamation. The prose is updated to match. | 1, 2, 3 | — |
| `vcs-retire-verdicts` | `github.com/nickderobertis/onevcs` | a verdict record per branch, keyed on every input the derivation reads, and reused only while those inputs are unchanged. One bulk `ls-remote` per identity. No `cat-file -e` or ancestry question asked twice in a pass. A `derivation` field in the report. | 4 | — |
| `op-close-cuts-maintenance` | `github.com/nickderobertis/onepipeline` | a closing driver stops its idle sweep at the next identity boundary, in both the pool pass and the retirement pass, never inside an identity. The `pool-maintenance` and `branches-retired` records of a cut-short sweep carry `cut_short`, naming the identities never reached. | 6 | — |
| `op-link-onevcs` | `github.com/nickderobertis/onepipeline` | the engine's `onevcs` requirement and lock move to that release. It is stacked on `op-close-cuts-maintenance`, so the engine release it produces carries goal 6 too. | 5, 6 | `vcs-retire-verdicts` (`published`, `crate`); `op-close-cuts-maintenance` (same repository, so a stack prerequisite rather than a release wait) |
| `ui-link-engine` | `github.com/nickderobertis/onepipeline-ui` | the read API's exact `onepipeline` requirement moves to that engine | 5 | `op-link-onevcs` (`published`, `crate`) |
| `aio-adopt-releases` | `github.com/nickderobertis/ai-orchestrator` | `config/onevcs.version`, `config/onepipeline.version` and `config/onepipeline-ui.version` move together, plus any other pin the engine's SBOM moves (for example `config/oneagentgraph.version`). A journey reads `derivation: reused`. A repeat sweep is timed on this host. | 4, 5 | all four above (`adoption: published`, no `consumes`, default targets) |

## The contracts, and where each is declared once

1. **The verdict record** (`vcs-retire-verdicts` produces; `op-link-onevcs` and
   `aio-adopt-releases` consume). It is declared once in onevcs's `docs/contract.md`, and a
   onevcs test holds the serialized field to it.
   - **What the key covers:** every input the derivation reads. That is the record format
     version and the onevcs version that derived it; the identity and branch; the base name
     and base tip; every copy's place and tip, including the origin tip or its absence; the
     places that could not be read; a digest of the stream evidence naming the branch; the
     trailer prefix; and the host's change-request answer wherever the derivation asked it.
   - **What is always fresh:** live holders, exclusions, checked-out and dirty worktrees, and
     the pre-deletion re-read, compare-and-delete and lease. A reused verdict may only skip
     the derivation.
   - **When a verdict is re-derived:** whenever any input differs, the record is
     missing, corrupt, of another format or from another onevcs version, or any input
     cannot be read. Unreadable is never "unchanged".
   - **Where records are stored:** `$ONEVCS_HOME/verdicts/`, one file per
     (identity, branch), written atomically. `registry.json` stays at version 6, session
     records at version 3, and no stream kind is added. No older onevcs reads the
     directory, so moving the pin migrates nothing.
   - **What consumers see:** each examined entry carries `derivation`, which is
     `derived` or `reused`, in `onevcs retire-finished --json` and in the `finished-branches`
     family of `onevcs sweep --format json`.
   - **Reversing it later:** that costs deleting one directory and one additive report
     field. No other state depends on either.
2. **The host sweep lock** (`aio-session-sweep` alone). This is one lock file under
   `${XDG_CACHE_HOME:-$HOME/.cache}/ai-orchestrator/sweep/`, with a completion stamp and a
   log beside it. Session setup and the `just sweep` recipe both take it. The one-hour
   interval is declared once in this repository, and its tests read it from there.
3. **The cut-short maintenance record** (`op-close-cuts-maintenance` produces it. Its readers
   are `results` in the engine, onepipeline-ui's timeline, which files events by kind only, and
   ai-orchestrator's `just results`, which prints the engine's own lines). It is declared
   once in onepipeline's `src/payload.rs`, the `agent.pipeline.<kind>@2` documents for
   `pool-maintenance` and `branches-retired`. The engine's payload registry holds the writer
   to those documents, and `docs/contract.md`'s pool-maintenance paragraph states it.
   - **The shape:** both records gain one optional key, `cut_short`:
     `{"reason": "driver-closing", "unreached": ["<identity>", ...]}`. `reason` is a closed
     word with one member. `unreached` is non-empty and names, in visiting order, exactly the
     identities that pass never began.
   - **When it appears:** only on a sweep the driver's close cut short. The identity in
     progress is finished and reported as an ordinary entry. A cut-short pass is journalled
     even if it would otherwise record nothing. If the pool pass is cut, the retirement pass
     never starts, and its record names every identity as unreached.
   - **What does not change:** no event kind is added, so onepipeline-ui's kind-category gate
     is untouched. A sweep that visits every identity writes exactly what it writes today.
   - **Reversing it later:** that costs one optional key on two documents.
4. **The pins** (`aio-adopt-releases`). `tests/test_linked_libraries.py` is the one check that
   reconciles them with the installed engine's SBOM.

## Decisions taken while planning (manager rulings)

- Single flight and detach live in ai-orchestrator, as `setsid` plus `flock`, following
  `scripts/repos-bootstrap.sh --detach`. Holding one sweep of both verbs at a time is this
  repository's own composition, so there is no library single-flight node. Whether two
  retirement passes are safe against each other (the engine's idle pass beside a host
  sweep) is onevcs's to answer. `vcs-retire-verdicts` surfaces it if they are not.
- A session start launches a sweep only when none is running and none finished within the
  last hour. The engine still retires branches every ten minutes per idle driver, and
  reclamation already has a four-hour floor.
- The verdict record is a sidecar with no registry schema bump. So the onevcs pin can move
  together with the engine pin without breaking live runs whose engine links 0.34.1.
- Goal 6 stops maintenance at an identity boundary when the driver closes. It does not hand
  the rest of the pass to a detached process that outlives the run, because that would
  duplicate the hourly host sweep and bring back overlapping passes.
- Pruning session records is out of scope. The onevcs worker may draft it as a follow-up.

## Planner exceptions

1. **The adoption needs two more releases than "the engine links the fix".** The engine
   requires `onevcs = "0.34.1"`, and the host holds `config/onevcs.version` *equal* to the
   onevcs in the engine's SBOM, with no divergence declared. The read API pins the engine
   exactly (`=0.52.1`). So the plan carries an engine release, a read-API release and one
   adoption node that moves the pins together. The engine's `main` already links
   oneagentgraph 0.5.3, so `config/oneagentgraph.version` will likely move too.
2. **This plan collides with `authoring:follow-up-board-reads-plan`.** That plan also has an
   engine link node, a read-API link node, and an adoption node that moves
   `config/onepipeline.version` and `config/onepipeline-ui.version`. The two plans' link nodes
   stay independent. `aio-adopt-releases` never moves a pin to a release older than the
   one the tree names when it starts. It adopts exactly the releases rendered into its
   references. If one of those is older than the tree's pin, it leaves that pin unmoved and
   asks the manager, and its report carries the ruling. The
   other plan's adoption node carries no such criterion, so it could move these pins
   backward if it lands second.
3. **The single-flight lock is bash in this repository.** The brief named a single-flight
   or detach primitive as a possible library gap. The manager ruled it is this repository's
   own composition, with `repos-bootstrap --detach` as precedent. The lock does not cover the
   engine's own idle retirement pass, which runs through the library.
4. **"Fast again" is held by a check in onevcs and measured once on this host.** The onevcs
   node owns a bound journey: a repeat `onevcs sweep --dry-run` over a host-shaped fixture
   (several identities, at least 150 candidate branches) must finish under a wall-clock bound
   of a few seconds, declared once as a named constant and below what the pass at its starting
   base takes. That holds the bar by time as well as by call counts. The adoption node reports
   two consecutive `onevcs sweep --dry-run --min-age-hours 4` wall times on this host. Its
   criterion is met by a repeat under 60 seconds, or by the manager's ruling on a slower one
   carried in the report, because host load is outside a dispatch's control. A repeat pass still reads each branch's tips and
   live holders fresh, so it stays roughly proportional to the ~640 branches on this host.
5. **The adoption node states `adoption: published` itself** and names no `consumes`. The
   host override would resolve `published` for this repository anyway. Stating it keeps the
   node right if the override changes.
6. **Goal 6 adds no release and no adoption node, but it adds one edge.** Another onepipeline
   node carries the link release, so `op-close-cuts-maintenance` depends on no onevcs node,
   and `op-link-onevcs` gains it as a dependency. The dependency is in the same repository, so
   the engine treats it as a stack prerequisite and never as a release wait
   (`src/release.rs`, `Dependency`). The engine release that `ui-link-engine` and
   `aio-adopt-releases` wait on therefore carries goal 6. `op-link-onevcs`'s task body is
   unchanged. Only its dependencies moved, which clears its review record until
   `just review-plan` reads it again.
7. **onepipeline's approved contract changes by the manager's ruling rather than by a
   proposal.** `docs/contract.md` says a driver closing out joins its sweep.
   `op-close-cuts-maintenance` records the goal-6 ruling as a ruled divergence entry and
   amends that paragraph to match.
8. **The design document on the board predates goal 6.** The project's goal, table and
   contracts above now include it. The plan review and the design document need to run again
   before approval.
