# Repo lifecycle: clone → verify → PR/merge, across many repos

The orchestrator doesn't only dispatch a onejudge at a directory — it manages the
**full life cycle** of a change against any repo: acquire the repo, do the work
in isolation, verify it locally, get it reviewed by CI, and merge it. One larger
task becomes **multiple isolated PRs**, coordinated by a dependency DAG. This doc
is the reference for that layer, implemented by `onevcs` and driven by
`onepipeline`; the
onejudge dispatch mechanics are in [onejudge-integration.md](./onejudge-integration.md).

## What the adopted engines actually do, and when this was checked

Everything below about engine behaviour was read out of the engines' own source
rather than remembered, and the load-bearing part of it — [the outcome
vocabulary](#the-outcome-vocabulary-is-closed-and-it-is-this) — is reconciled against
that source on every `just check` rather than restated: **`onepipeline` v0.45.0**
(`config/onepipeline.version`) and
the **`onevcs` 0.32.0** its `Cargo.lock` resolves, which is the copy a dispatched
lifecycle node publishes through — and the copy `just publish-branch` and `just
repo-recover` land through, since both run the engine's own landing verbs. The other
manager verbs — `just recoverable`, `just work-status`, `just integrate` — run the
`onevcs` CLI `config/onevcs.version` pins, which is **onevcs 0.32.0** as well at this
pair of pins; the two are separate pins that have coincided before and will
diverge again, so where a claim depends on which copy runs it this document says so.
**They diverged again at the adoption on 2026-08-25 and have not re-converged**: two
consecutive adoptions before it had `config/onepipeline.version` and
`config/onevcs.version` carrying one number, and this pair of pins has them at 0.45.0
and 0.32.0. The habit that ambiguity taught is worth keeping rather than retiring
with it — read a version here **with the tool beside it and never
on its own**, because the next coincidence will arrive without announcing itself and a
bare number says nothing about which of the two CLIs a sentence is about. Re-read the source before trusting a claim
here against a later pin; that is the discipline this section exists to replace, and
the sentences it replaced described a Python implementation that was deleted when
those crates were extracted.

## The unit of work: one lifecycle node

```
onevcs session open  →  per-run clone of the execution checkout, worktree, branch
   →  dispatch each step into that one worktree  (agent commits its own work)
   →  onevcs publish  →  fetch, merge the change base, push (the repository's own
                          `pre-push` hook rules on it), open/merge the change request
   →  onevcs session close  →  worktree and occupancy lease released
```

Every step of a node runs in the worktree the *first* step's session opened;
`onepipeline` asks for a session only once and passes the path thereafter, because a
second session on the same branch would start from the base and reclaim the first
one's workspace. A `kind: human` step settles the node `waiting` and holds the
branch — the harness never infers that a person acted.

### The outcome vocabulary is closed, and it is this

A node's `outcome` is not free text. `onepipeline` writes exactly these words, and
`just results` and `just status` render them:

| Status | Outcome | What it is |
| --- | --- | --- |
| `done` | `merged` | The change reached its base, at a commit `onevcs` observed. |
| `done` | `change-open` | A change request is open, which the policy asked for. |
| `done` | `queued` | The host took the merge and will land it once its checks pass. |
| `done` | `no-changes` | Every step declared no diff, or the base already carried the branch's content. |
| `failed` | `empty-branch` | A lifecycle dispatch left its branch level with its base, having committed nothing to it, and the node declared no `expects_no_diff` — settled before any drafting or publication is spent, naming the branch and what it was compared against (https://github.com/nickderobertis/onepipeline/pull/229). The other reading `no-changes` used to cover: a worker asked for a change that produced none. A retry carrying `expects_no_diff` accepts it as that deterministic success, which settles without a dispatch; an amended task sends it back to produce a diff. |
| `complete-but-draft` | `change-draft` | Every step ran and the branch is published, and the host is holding its change request as a **draft** because a release the node adopted early has not happened yet. Deliberately neither `done` nor a failure and deliberately not settled: merging now would make the node's temporary git pin permanent in a base branch, so no dependent starts on it and no run holding one has settled. What clears it is the release arriving, which puts a new worker on the branch this node already has. |
| `done` | *(none)* | A direct agent node — no publication to name. |
| `waiting` | *(none)* | A `kind: human` step is ready and the branch is held for a person. |
| `failed` | `publication-failed` | The publication started and did not land, and **nothing a further attempt could answer** ended it: a request `onevcs` refused, a seam with no implementation, or something that judged the publication turning it down where no narrower kind says which — the repository's own `commit-msg` hook refusing the composed subject, or a host that took a merge and then reported it unperformed. |
| `failed` | `checks-failed` | A required check the host reports concluded red, and the publication budget is spent. |
| `failed` | `checks-unsettled` | The bound on watching the host elapsed with the change still outstanding, and the budget is spent. |
| `failed` | `push-rejected` | The publishing push was refused by the merge path, and the budget is spent. |
| `failed` | `sync-conflict` | The base moved under the publication and the bounded resolve-and-requeue did not converge, and the budget is spent. |
| `failed` | `pushed-unverified` | The publishing push **reached the remote** and the merge path could not then be read, and the budget is spent. The one failure word whose work is already on the origin: its reason names both the commit it landed at and what stopped the read, and a further attempt re-reads that path rather than re-pushing. |
| `failed` | `task-failed` | The dispatch failed its own judge. |
| `failed` | `task-failed-change-open` | It failed its judge having already opened a change request — the URL is on the settlement. |
| `failed` | `dispatch-died` | The dispatch started and the agent worked, but the dispatch process then ended without an agent verdict; unlike a pre-work infrastructure refusal, it is not retried. |
| `failed` | `provider-failed` | The same death, where what killed the dispatch was the **provider** — a narrowing of `dispatch-died` and not a second word for it. A node whose provider went is a node with nothing wrong with its work, so read it as the quota, auth, or outage question `just status`'s health block answers rather than looking for what the work got wrong. A node that failed its own task still settles `task-failed`, and a dispatch that died to anything else still settles `dispatch-died`. |
| `failed` | `no-agent-progress` | Every boundary attempt was spent and the agent produced nothing. |
| `failed` | `infrastructure-failure` | The dispatch layer refused before any work began — or, on the adopted engine, the merge path refused the publishing push because **this host** lacks a tool or a credential one of its hooks needs, said by the hook on the one line beginning with `onevcs`'s host-prerequisite marker. The same word on purpose: both are the host and never the work standing in the way. The publication case is settled **once**, never re-dispatched, and carries the preserved branch, the commit it stands at and the hook's own remediation, so the fix is one install on the host and a `retry` onto that branch. |
| `failed` | `invalid-node` | The node itself could not be run as written. |
| `cancelled` / `parked` | *(none)* | A stop, engine-taken or planner-taken. |

**This table is reconciled, not copied.**
`tests/test_engine_contracts.py` reads every settlement the adopted
`onepipeline` release composes — `Settlement::plain`, the `failed(node, …)` helper,
`vcs::outcome_of`, and the words `vcs::Preserving::outcome` declares for a
publication a further attempt could answer — across the crate whole rather than a
named handful of files,
with test modules stripped, and fails when a **pairing** appears on one side and not
the other, in whichever direction it drifted. Both columns, because a table of
pairings gated on one of them is gated on neither: a row that moved
`publication-failed` to `done` would invent no word and drop none. The same gate covers the other engine contracts this
document and `telemetry.md` state: every enum a passage enumerates exhaustively
(`FailureKind`, `Retention`, `Coverage`, the telemetry `BucketName`, `Resume`'s
fields) against the engine's own declaration, and every constant either quotes a
number for (the boundary attempts and backoff, the two git bounds and the drain, the
preserved merge-path-log retention and directory) rebuilt from the engine's own `const`. It
runs in the uncached tier, because the source it reads is another repository's
checkout and no cache key here describes one.

`gate-failed` is **not** in it and never was on this stack, and that gate holds it
absent too, so the denial fails the moment it becomes real. The only `gate-failed`
either engine writes is `onevcs integrate`'s per-candidate *skip reason* — the
train's word for "something said no about this branch, so the train stepped over it" — which
is not a node outcome and never reaches a plan. `checks-failed` was denied here for
the same release cycles and is denied no longer: onevcs 0.10.0 gave it a
`FailureKind` and onepipeline 0.10.0 gave it a settlement, so it is a row of the
table above rather than a word to look for and never find.

### What a failed publication actually settles

`onevcs` distinguishes nine failures and `onepipeline` keeps the distinction
that decides what happens next. `PublishOutcome::Failed` carries a `kind` — `gate`,
`invalid`, `sync-conflict`, `not-implemented`, `checks-failed`, `checks-unsettled`,
`push-rejected`, `pushed-unverified`, or `host-prerequisite`, which the CLI reports as exit 1 for the six
verification failures, 2 for `invalid`, 3 for `sync-conflict`, and 70 for
`not-implemented` — a human-readable `reason`, and a `retained` saying whether the branch was
`handed-back` to a registered checkout or `refused` by it.

`onepipeline`'s `vcs::failure_of` sorts those nine kinds arm by arm rather than by a
wildcard, and the sort **is** the routing. Four of them are
`Preserving` — `checks-failed`, `checks-unsettled`, `push-rejected`, and
`sync-conflict` — because their fix is more work on the same
branch, and each settles under its own word. `pushed-unverified` is routed apart from
them as `Unread`: onevcs 0.12.0 added it so that a push which **reached the
remote** behind an unreadable merge path stops reading as work that never landed, and
what answers it is a bounded re-read of the merge path rather than another dispatch,
since nothing about the tree is what has to change.
Widening the vocabulary rather than adding a clause to the four beside it is
deliberate on onevcs's part and is the reason a fifth word exists at all — a router
branches on the *kind*, so only a kind can stop that state routing as a refusal.
`host-prerequisite` is the ninth and is routed apart from both, for the same reason a
kind was the only way to say it: the linked `onevcs` added it
(https://github.com/nickderobertis/onevcs/pull/163) for a merge path that refused
the push because **the host** lacks a tool or a credential a hook needs — the hook says
so itself, on the one line beginning with the marker `onevcs::HOST_PREREQUISITE_MARKER`
spells, and only for a failure independent of the tree being pushed — and the adopted
engine (https://github.com/nickderobertis/onepipeline/pull/394) settles it once as
`infrastructure-failure`, carrying the branch, its head and
the remediation, and dispatches nobody: a worker sent back to the branch would meet the
same refusal however it edited, which is what spent whole publication budgets on one
missing tool before this arm existed. A refused push carrying no such line is
`push-rejected`, exactly as before. The other three are terminal: `gate` ran on the tree as
it stands and said no, `invalid` was refused at a trust boundary, and
`not-implemented` has nothing behind it, so all three settle the residual
`publication-failed` — the word every publication failure used to settle on, kept
for exactly the endings no continuation follows from.

A preserving failure whose branch `onevcs` handed back is **not settled at all on
the first attempt**: the node is dispatched again onto that same branch, carrying
the failure's reason and pointers to `onevcs`'s own evidence, so the worker meets
the thing that rejected it rather than a fresh worktree cut from the base. The
budget is `ONEPIPELINE_PUBLICATION_ATTEMPTS`, **3** by default and the whole budget
rather than the retries beside it. Only when it is spent does the node settle
`failed` under the last failure's word, with a roll-up of every attempt in the
detail. A preserving kind whose branch the execution checkout *refused* settles
straight away, because there is nothing left to continue.

Either way the `reason` is kept, prefixed `onevcs: `, as the settlement's `detail`.
**So the outcome word now says which merge-path refusal this was, and the detail
says what it said** — read both, and read the session's own `push` event beside
them, which is where what the merge path wrote is stored.

A drafting failure adds words to that same detail and never causes it; see
[Diff-derived PR descriptions](#diff-derived-pr-descriptions).

### The one automatic re-dispatch, and everything it is not

`engine::attempt` asks a dispatch again **only when it produced no events at all** —
a provider that refused before the first turn, an executor that could not start
anything. An attempt that recorded anything has already answered, whatever its exit
status, and is never re-asked. The budget is `DEFAULT_BOUNDARY_ATTEMPTS` = **3**,
with a 5-second backoff that doubles to a 120-second ceiling
(`ONEPIPELINE_BOUNDARY_ATTEMPTS`, `ONEPIPELINE_BOUNDARY_BACKOFF_SECONDS`). Spent
without the agent producing anything, the node settles `no-agent-progress`; a
dispatch that never started settles `infrastructure-failure`. Each attempt is its own
`node-dispatched` record carrying `attempt`, `attempts`, and the previous attempt's
bounded reason, so counting dispatches per node shows the re-ask.

Nothing else re-dispatches anything. In particular there is **no** automatic
continuation of a failed, cancelled, or turn-capped node, no per-node continuation
budget, and no relaunch of a dead conversation with its history seeded back in. The
pre-extraction lifecycle's `MAX_AUTOMATIC_STEP_RESUMES` and its
`DEFAULT_LIFECYCLE_STEP_MAX_TURNS` segment size are in neither engine; a step's turn
budget is its own `max_turns` and nothing tops it up. A step that hits its cap ends
its workstream with that settlement and the branch is preserved; picking it back up
is a manager `retry` or `requeue` live edit and nothing else.

### What a preserved branch carries into a continuation

A node settling `failed`, `cancelled`, or `parked` is pinned by
`projection::pin_preserved_branch` to the branch its attempt left behind, so a later
`retry` or `requeue` continues that branch rather than cutting a fresh one beside
committed work. A `branch` the planner wrote wins outright.

The pin is a `Resume`, and it has exactly three fields —
`branch`, an optional `checkpoint`, and `completed_steps` — under
`deny_unknown_fields`. There is **no** `mode`, no `attempts`, and no `stack_bases`;
a plan carrying any of them is refused while it loads. `completed_steps` is the only
record of what a workstream finished, and empty or absent re-runs the whole thing,
which is the safe direction: work is repeated, never skipped.

Which steps carry forward is decided by *where* the node stopped, and the two cases
differ:

* **A step failed, was cancelled, or hit its cap.** The settlement carries the steps
  that had already completed, so the continuation skips them.
* **The steps all completed and the publication failed.** The settlement records
  **no** completed steps, because `publication_failed` builds a plain settlement and
  never populates them. A continuation therefore re-dispatches every declared step
  over a branch that already carries their work. That is the engine's behaviour as
  written, not a design this document is defending: if the re-run matters, land the
  branch directly with `just publish-branch` instead of retrying the node.

`checkpoint` is carried, never derived — `onepipeline` records the value it was
given and nothing it was not.

## Every git command is bounded

`onevcs` bounds every git call it makes and, on expiry, fails naming the command and
the elapsed time. An unbounded git turns a transient network problem or an
unreleasable `index.lock` into a run that looks exactly like one still working, and
from outside the only way to tell the two apart was reading `/proc` by hand.

There are two bounds because the two populations differ by orders of magnitude:

- `ONEVCS_GIT_TIMEOUT` (default **600s**) covers a command that runs no repository
  hook — two orders of magnitude above the largest ordinary operation performed
  against a repository of this size.
- `ONEVCS_GIT_HOOK_TIMEOUT` (default **5400s**) covers a command that runs the
  repository's own hooks. This repository's `pre-push` hook runs `just gate`, about
  fourteen minutes, so the default leaves room for a gate slowed by everything else
  on the host without letting a genuinely hung push sit forever. Bounding these at
  the ordinary value would abort every publication the harness exists to perform.

Hook-running commands: `git clone`, `git checkout`, `git commit`, `git merge`, `git push`, `git rebase`, `git worktree add`.

`git::HOOK_RUNNING` is that list's one source and `git::run` classifies each call
from its own argv, so a new hook-running operation cannot silently inherit the
ordinary bound. A non-numeric, zero, negative, or infinite value is refused at the
boundary rather than silently reverting to unbounded.

While the bound is live, the exit loop waits at most `EXIT_POLL` (10ms) between
checks when neither output reader has ended. When it fires, the whole git process
*group* is terminated before the child is collected and both output readers are
joined. The group matters because a hook's children inherit git's pipes and outlive
the shell that started them; killing git alone would leave those writers holding the
readers open.

The group, and not a walk from git's pid, because a walk names the set of processes
that existed when it ran and a git being torn down goes on starting more. Its
transport is one git restarts whenever the connection it was using dies — and the
first signal of the teardown is what kills that connection, so the replacement is
born after the walk that was supposed to have found everything. Measured under this
host's ordinary concurrent-dispatch load: the bound fired, the sampled transport
died, a second one appeared with `init` for a parent, and the drain then sat out its
whole 30s ceiling on pipes nothing would ever close — turning a 3s bound into a 33s
one and leaving a live process behind. git is therefore started in a session of its
own, so every process it starts is born into one group the kernel keeps valid across
all of that reparenting.

## Repository identity, checkout roles, and isolation

`onevcs` resolves two independent decisions through
its persistent registry: the **publication checkout**
selected by the repository argument and the **execution checkout** used to create
the task worktree. Normally they are the same. `onevcs session open <repo>
--execution-checkout <alias>` deliberately separates them for a safety clone — that
flag is on `session open` and nowhere else, so a lifecycle node names its safety
clone through the plan's `execution_checkout` rather than by passing a flag to a
recovery or publication verb. The lifecycle reports the exact execution path,
publication path, normalized identity, resolved publication policy and approvals,
merge policy, PR base, and synthetic stack base in human output, JSON, and the
recorded run ledger.

The registry's version 6 format stores `identities` keyed by normalized origin —
each carrying its `origin` and the `gate` `register` guessed, and nothing else — and
alias-to-path records separately under `checkouts`. Thus GitHub/SSH URL spellings,
canonical clones, safety clones, linked worktrees, and auxiliary clones resolve to one
identity even when several aliases share the origin, and one *policy*, because the
policy is the rules file's and lives on no record. Versions 2 through 5 migrate lazily
in one atomic replacement, on the first contact of any verb, and the replacement is
one an older release cannot read: version 5 carried `workflow` and `repo_type`, which
`register` inferred from whether the origin had a host — `remote` / `team` for every
hosted one — and onevcs 0.21.0 dropped both, because two verbs read them as the routing
and refused every hosted `local-direct` identity on their strength. On a host several
managers share that migration is one-way for every process still on an older release:
the first 0.21.0 verb to touch `$ONEVCS_HOME` — a read included — rewrites the shared
registry, and every dispatch still running under an engine that links onevcs below
0.21.0 then refuses it (`missing field `workflow``) at its next registry read. The
remedy for a stranded consumer is to re-provision it onto the adopted pin; a hand
rewrite of the file back to version 5, with `workflow: remote` and `repo_type: team`
on every hosted identity, is the stop-gap that unbreaks a live run until it can be,
and holds only until the next 0.21.0 contact.

Type is not a command option on any of these verbs, and there is no verb left that
takes one: `just register-repo` forwards only `--origin`, and every other `onevcs` verb
resolves the policy from the rules file the identity matches. A plan node may still
spell `repo_type` and `workflow` — schema-3 fields the engine keeps so a plan that wrote
one still loads and still round-trips through a task's metadata — and **nothing reads
either**: a node carrying one publishes exactly as one carrying none. Change the
resolved policy by editing the rule that matches the repository in the rules file;
`onevcs rules check` reports which rule a repository matches and the policy that
follows.

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
whose default is the engine's own lock timeout — minutes,
not seconds, because a legitimate turn can hold a gate run. `flock` releases on
process death, so a crashed holder hands the queue to the next waiter instead of
wedging it. A timeout reports the owning PID/host. The slow agent dispatch remains unlocked. Default lifecycle branches
include a unique run suffix; an explicit `--branch` is the intentional
resume/override path. An active branch or occupied worktree is never reset or
forcibly removed: inspect the reported path and recover that run, or remove it
manually only after confirming its owner is gone.

A session is placed on a **pooled slot** or a **run root** — [the section
below](#where-a-session-is-placed-and-what-a-close-returns) says which and what each
costs — and everything in this paragraph is about the run roots under `runs/`. A
process working in a run root holds a **shared** occupancy lease on it for the
duration of the `onevcs` command it is running: `open`, `adopt`, `close` and the
publication paths each take one and drop it as they return. Abandoned run directories
are reclaimed by the next `session open` on the same identity, which skips every run
root a session record still `open` names — whatever became of the process that opened
it — and reclaims any other only when nobody holds the shared lease at that instant
and the clone has no commit that never reached origin. So a dispatch's run root is
protected for as long as its session is open, with nothing held on this side; a pooled
slot is outside that reclamation and outside `onevcs sweep`, which names the pool as a
family it does not reach. The newest **3** dead runs holding unpublished work
are kept, matching pytest's useful bounded failure history. A retry or recovery
for one of their branches claims the dead run's occupancy lease and adopts its
exact worktree, including uncommitted files; a held lease or live recorded owner
forces a fresh worktree. Dirty adopted work is committed with incomplete-step
provenance before it proceeds and must pass the ordinary merge path before
publication. Published roots are immediately reclaimable and older incomplete
roots age out. Rejoin a retained run with that run's token to recover it explicitly
(tearing its worktree down copies the branch into the execution checkout). Flat
per-branch directories left by the pre-`runs/` layout are never claimed or
reaped, and a run never collides with them.

### Where a session is placed, and what a close returns

Some languages build large outputs — a Rust `target/`, a `node_modules/` — that a
session cutting a fresh worktree builds from nothing and deletes on close, every node
of every run, and the disk wears for it. So an identity may keep a **pool** of warm
worktree slots, sized by the host in `$ONEVCS_HOME/workspaces.yml` — which `just
repos-apply` installs beside the rules and releases files as the tracked
`config/onevcs.workspaces.yml` overlaid by the host's own
`${XDG_CONFIG_HOME:-$HOME/.config}/ai-orchestrator/workspaces.yml` when that exists
(its `default` keys and its `rules` by `match` win; `orchestrator/workspaces_overlay.py`
states the rule), and validates through `onevcs pool status` in a scratch home first.
The tracked header says what each key means; the shared defaults are `pool: 1` and
`overflow: unlimited`, so every identity keeps one warm slot and admits every session
past it, and a host that can afford more raises `pool` in its own file, never in the
tracked one. **Placement is per open, in order:** a resumable open session for the pinned
branch resumes in place; else an idle slot whose lender is the request's execution
checkout; else, while fewer than `pool` slots exist, a new slot; else, while the
identity's sessions under `runs/` are below `overflow`, a run root exactly as before;
else the open is refused with `PoolExhausted`, exit 4. The pool is lazy — a slot is cut
when a session needs one — so a project with one concurrent task only ever gets one
worktree.

**A close on a slot returns it rather than removing it**: everything a close does
today up to the worktree removal — the occupancy refusal, the dirty-tree preservation,
the hand-back, the stray-work refusal — then the worktree is detached onto the base,
hard reset, cleaned of untracked files and directories **without touching ignored
paths**, and every `delete` path the identity's rule names is removed (this repository
deletes `.logs/`, the innermost stage's log, which would otherwise read as the next
session's own). The next session on that slot finds the build output still there and
its own branch checked out. A slot is bound to the execution checkout it was cut
from — its lender — and is never handed to a session opened on another checkout of the
same identity, because a branch the hand-back could not copy may reference objects
only the old lender holds; so an identity with several execution checkouts spends its
pool one slot per lender, and `pool` is sized per lender you alternate.

**Slots are outside every reclamation.** The bounded run-root history walks `runs/`
alone, `onevcs sweep` names the pool as a family it does not reach, and `just sweep`
says the same in its trailer and its one-line verdicts. A slot is removed only by
shedding against the file's `pool` at an open, or by `onevcs pool prune <repo>`, which
removes every idle slot whose clone retains no branch and says why the rest were kept;
`onevcs pool status <repo>` names each slot, its lender, whether it is idle or which
session holds it, and when it was last maintained. A node's own `pool` and `overflow`
fields ([the node schema](#lifecycle-nodes-in-the-tracked-graph)) govern only where
*that* session is placed and whether that open is capped; neither sheds a warm slot.

**A full identity holds a node; it never fails one.** The engine reads an identity's
capacity before a queued lifecycle node comes forward and holds it under the
`workspace` hold reason when the identity would refuse it, raising `workspace-wait` —
a non-blocking surface naming the identity, the numbers and the `onevcs pool status`
that names the holders — and re-reading the identity every pass and every minute until
it admits the node. That is a run waiting on the host, never a stall and never a
settlement; `just status` renders the hold beside a release hold. A `PoolExhausted`
refusal that races the read requeues the node without a settlement, journalled as
`node-requeued` with `reason: workspace-exhausted`, and a re-dispatch inside a
publication-retry loop is requeued onto the same branch and attempt it interrupted,
so a `retry` is never what continues a node the host was merely too busy for.

**Idle slots are maintained on a schedule the engine keeps no state for.** Every
`start` names `config/onepipeline.maintenance.yaml` as `--maintenance-config` — a
`default` and per-identity `rules` carrying one key each, `every`, a week here — and an
idle driver sweeps every registered identity through `onevcs pool maintain
<identity> --older-than <every>`, which runs the identity's `maintain` command from
the workspaces file in each idle slot not maintained within that span (`cargo sweep
--time 7` for every Rust identity this host registers) and stamps the attempt on the
slot as `last_maintained`. The stamp is what decides what is due, so late is fine,
nothing runs twice, and two drivers on one host are safe by the same fact. A sweep
that ran something is journalled once as a **`pool-maintenance`** record carrying
`started_at` and, per identity, the `every` it was maintained on and the outcome
`onevcs` reported — `just results` names the last one — and a sweep that found every
slot not due, in use, or without a command writes nothing. While a sweep is live the
run root carries `maintenance.json`, which is how `status` names a maintenance in
progress from another process.

**Which pin governs each half.** A dispatched session is placed, returned and
maintained by the `onevcs` the engine links, so `config/onepipeline.version` governs
where a node works and what sweeps its slots; `config/onevcs.version` governs the
`onevcs pool` verbs a manager runs and the CLI `tests/e2e/test_worktree_pool_e2e.py`
drives through a pooled scratch identity, while `tests/test_linked_libraries.py` holds
the linked copy to a release carrying the pool.

The registry uses its own process-shared locks for resolution and first clone. Its
JSON is reloaded and merged while locked, then atomically replaced, so concurrent
registrations are retained and interruption cannot leave partial JSON.

### Self-dispatch isolation for this repository

Multiple orchestrators operate concurrently from this repository's canonical
checkout. Treat that checkout strictly as the publication checkout: never author
changes in its working tree, including temporary plan files, personas, or docs.
Dispatch every change into a worktree created from the registered
`ai-orchestrator-isolated` execution clone, then let the registered local
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

```json
{
  "schema_version": 3,
  "tasks": [
    {
      "id": "self",
      "repo": "/path/to/ai-orchestrator",
      "execution_checkout": "/path/to/ai-orchestrator-isolated",
      "persona": "engineer",
      "title": "fix(gitops): …",
      "task": "## What\n…\n\n## Why\n…\n\n## Acceptance criteria\n- …"
    }
  ]
}
```

The branch is pushed and locally merged because the shared identity is local, then
the node's `repo` canonical checkout is fast-forwarded. Recovery is cheap because no
data is lost: `git config core.bare false` restores the checkout, and the agent's
real work is intact at its last commit *before* the `init`-commit corruption.

Register another clone without repeating workflow; it inherits from its origin:

```sh
just register-repo /path/to/ai-orchestrator
just register-repo /path/to/ai-orchestrator-isolated
```

Neither line names a workflow or a type, because `onevcs register` takes neither:
its only option is `--origin`, for a checkout whose own remote is not the one to
resolve the identity from. What each identity publishes under comes from the rules
file instead; `docs/host-setup.md` covers
[why `just repos` does not report the routing](host-setup.md#just-repos-does-not-report-the-routing-just-repo-policy-does).

When `register-repo` receives a GitHub repository spec such as `owner/name` and
finds no existing checkout, it clones directly into the managed default
`~/.ai-orchestrator/repos/owner__name` and registers that checkout. An explicit
path or an already discovered checkout keeps the existing selection behavior.

Registration prints ranked gate candidates. Monorepo affected commands (Nx,
Turborepo, Bazel, pnpm, or Lerna) rank ahead of whole-repository gates (`just
check`, `make check`, `npm test`, Cargo, or pytest). Accept one, override it with
`--gate`, or investigate first. A gateless checkout stores `<no-op>` and warns
that it is unproven.

**This stored gate is not the retired rules gate, and confusing the two is easy.**
It is the registry's own *detection* of what a checkout verifies with — a
description of that repository, available to agents and to recovery metadata, and
never a command `onevcs` runs before publishing. Nothing resolves it from
`config/onevcs.rules.yml`, so there is no rule to correct it in; re-register the
checkout to change it. It is still read where it matters: `onevcs recover`'s
`attests_nothing` refuses a recovery when the stored gate is `<no-op>` **and** the
merge path covers nothing, because an attestation with nothing behind it attests
nothing. The merge path itself is what is authoritative and what actually rules:
an executable `pre-push` hook runs the local bar, or required PR status checks
decide a remote-first publication.

Registration also audits what the merge path itself runs, which since onevcs 0.11.0
is the whole of the verification. It reports an
executable effective `pre-push` hook (respecting `core.hooksPath`) and required
GitHub status checks on the repository's actual default branch. A configured
hooks directory without an executable `pre-push` does not count. Which evidence
counts depends on the identity's resolved publication policy: **`local-direct`**
pushes straight to its base branch and never opens a PR, so branch protection has
nothing to run against and only the hook can cover it. A change-request policy is
covered by either —
the hook judges the branch push that feeds the PR, and required checks decide the
merge. If nothing applicable is
present, registration succeeds but prints an identity-specific warning; an
unavailable GitHub response is reported as unknown, and local-only origins are
reported as not applicable. Audit every existing identity without re-registering
it with:

```sh
just repos --audit-gate-coverage
```

### The audit answers what can refuse a merge, not only what verifies an identity

Its `merge-path coverage:` line is about the *presence* of a verifier, and reading that
line as coverage is what hid this host's own defect. Two things it does not say, which
the `required checks:` line above it now does:

- **A `pre-push` hook is not necessarily a complete bar.** Four identities
  registered here report coverage by a hook that is a screencomp
  visual-regression guard, which re-captures screenshots and judges nothing else.
- **No local verifier is the merge path for a remote-publishing identity.** A pull
  request merges when GitHub's required checks pass, and a local hook running less
  than they do verifies a branch that cannot merge. `nick-derobertis-site` reported
  coverage, a dispatched branch passed the gate this host then ran, published as PR
  #77, and the required `llmlint` check that gate never ran refused it.

So `just repos --audit-gate-coverage` here is `onevcs repos --audit-gates`, and since
onevcs 0.21.0 that audit names, per identity, each check the host requires before a
merge — read off the repository's own branch protection and rulesets at the moment it is
asked — above every checkout's resolved policy and the verifier on its merge path:

```
github.com/nickderobertis/nick-derobertis-site	just gate
  required checks: check, classify-gate, llmlint (required by the repository's rulesets and branch protection for master)
  nickderobertis__nick-derobertis-site	/home/…/nickderobertis__nick-derobertis-site
    publication: change-auto (from rule 7)
    approvals: none (from rule 7)
    merge-path coverage: pre-push hook at /home/…/.githooks/pre-push
```

Since onevcs 0.11.0 that list is *every* required check rather than the leftovers
after a local gate: this host front-runs nothing, so nothing here can be the whole
bar and the audit stops implying it might be. An identity whose base declares no
protection reads `none required`, naming both places it looked, which is what parts
"nothing is required" from "nothing was asked". **There is no tracked copy of that
list here any more.** A tracked JSON inventory under `config/` held it, a filter rewrote
the audit out of it, and a test at the end of the gate compared it with GitHub — so
whenever a sibling renamed a check, every branch here failed a whole gate to learn it,
after the suite had run, on a defect no retry could fix; two siblings drifted in one
evening and two publications paid a full gate each within the hour. With the tool
reporting the checks itself there is nothing to go stale, and
`tests/e2e/test_merge_path_audit_e2e.py` drives the recipe against the real
repositories' branch protection instead.
`test_the_resolved_policy_is_publication_and_approvals_and_nothing_else` reads the
policy back off `onevcs` so a gate reintroduced into the rules file fails here; it lives
in `tests/e2e/test_repo_registry_apply_e2e.py`.

Lifecycle dispatch and `just repo-recover` repeat this audit and refuse before
starting any work when coverage is missing or unknown, because nothing here runs a
verifier of its own. They inspect the **execution** checkout rather than an
arbitrary alias: every publishing push originates in one of its worktrees, and
Git resolves hooks through the shared common directory, so its `pre-push` is the
hook that will actually run. Both judge against the run's *effective* workflow, so
a run that publishes locally cannot qualify on required checks a local push never
triggers. The command only reports coverage; it never installs
hooks or changes branch protection.

A contradictory `--workflow` is rejected. Change publication policy in the rules
file — the rule that matches the repository names the workflow that follows — and
confirm what it resolved to with `just repos`.

## Merge-path verification

Before publication, `onevcs` fetches `origin` and merges the current change base
into the dispatched branch. A sync conflict that the bounded resolve-and-requeue
cannot converge aborts before any push, as `FailureKind::SyncConflict`.

**`onevcs` runs no gate of its own.** onevcs 0.11.0 removed the concept: there is no
`gate:` on a rule, no `GateKind`, no `gate.rs`, and no `gate-started` / `gate-verdict`
pair on a session's stream. The verifier is the repository's own merge path, and
`onevcs` hands it the one thing it cannot work out for itself.

**Which merge path an identity has is detected, not declared.** `store::Coverage` is
the answer and it has exactly three values:

| Coverage | What verifies a change | When the verdict arrives |
| --- | --- | --- |
| `PrePushHook(path)` | git, running that executable hook at the publishing push | As push output |
| `RequiredChecks` | the host, on the change request | After the change request exists |
| `None` | nothing | Never — and this is warned about, loudly |

`store::merge_path_coverage` is the one source of that answer, and three callers read
it rather than each deciding for themselves: `onevcs register` warns on it, `onevcs
repos --audit-gates` reports it (`Coverage::describe`), and `onevcs recover`'s
`attests_nothing` refuses a recovery whose identity names no complete bar *and* whose
merge path covers nothing — because an attestation with nothing behind it attests
nothing. `just integrate` prints that same warning before the train advances a base,
in the same words, because an operator who learns afterwards that nothing will judge
what the train landed has already landed it.

Whatever verifies it is handed the comparison identity as environment —
`ONEVCS_COMPARISON_REMOTE` and `ONEVCS_COMPARISON_BASE`, the remote and base this
change is being published onto. A judging process left to discover its own base
resolves the repository default, which for a stacked change is not the base the push
is publishing onto; see [One judged diff, one verdict](#one-judged-diff-one-verdict).

**The failure vocabulary keeps the word `gate` and no longer keeps the tier.**
`FailureKind::Gate` is still a variant, deliberately: the contract fixes this
vocabulary across the three libraries that route on it, so a variant is not renamed
because the tier behind it went away. What it means now is narrower — a publication
refused by something that judged it where no narrower kind says which, such as the
repository's own `commit-msg` hook turning down the composed subject, or a host that
took a merge and then reported it unperformed. The narrower kinds are where a merge
path's own refusals land: a `pre-push` rejection is `Error::PushRejected`, because git
cannot tell an arbitrary hook rejection from a transport rejection and `onevcs` reports
what git said per ref — a word a further attempt could answer, so the node is
dispatched again onto that same branch before it settles `push-rejected`. What the
hook wrote is not inline; it is the artifact `record_push` stored, because it is a run
of the repository's whole verification. `ChecksFailed` and `ChecksUnsettled` are what
the *host's* checks reported.

There is no plan key that names or overrides a verifier, and there never was a
gate-skipping switch to inherit. The `Node` schema is `deny_unknown_fields`, so
`recorded_gate`, `verify_cmd`, `skip_verify`, and `no_identity_gate` are not
"accepted and ignored" — a plan carrying any of them is **refused while it loads**. `verify_via_ci` was the one
survivor and is no longer even that: it is not a field of `Node` on onepipeline
v0.45.0 and is refused **by its own name**, at every schema version and on a live
edit's `add` alike, because a plan's author has to act on the field rather than on
a version number. The refusal says where what it asked for went, which is the whole
of the change: nothing ever read the flag, and the host's own required checks are
now the merge-path verification of a `change-auto` node — watched to their
conclusion, settling the node `checks-failed` when one concludes red and
`checks-unsettled` when the bound elapses with one still outstanding.

`just integrate` runs no gate per candidate either — it has none to run. Each
candidate fast-forwards the *local* base before the single optional push, and what
judges that push is the same `pre-push` hook a publication meets. A candidate the
train leaves where it was is `Status::Skipped(reason)`, and `gate-failed` is one of
the reasons that word can carry — that word is the train's, and it is not a node
outcome.

This repository's complete gate resolves that same comparison ref with
`scripts/comparison-base.sh`. `just gate` discovers the base from a valid remote
HEAD (or a sole remote branch); use `just gate <remote> <base>` when discovery is
ambiguous. The lifecycle exports `ONEVCS_COMPARISON_REMOTE` and
`ONEVCS_COMPARISON_BASE` to **every dispatch and every publishing push of a
workstream**, and the pre-push hook reads that base and
uses the remote name Git passes as its first argument. `ORCHESTRATOR_COMPARISON_*`
is the same pair under this repository's own older spelling and still wins where it
is set, because it is also the operator's documented override. Invalid names, missing
refs, and ambiguous remote branches fail with a remediation instead of falling
back to `main`. `just sync` fast-forwards the publication checkout to its
origin, and `just sync <branch>` names one rather than the registered base.

### Where a merge-path verdict is preserved

What the publishing push wrote is written twice, and the second copy is the point:

* as an event **artifact** — `stream::store_artifact("log", …)` on the `push`
  event, reachable through `onevcs artifact`; and
* into **`<run root>/gate-logs/<branch with `/` flattened>/`** by
  `merge_path::preserve_log`, one file per invocation, `gate-0001.log` upward in the
  order they were claimed, named on the same event as `preserved_log`.

That directory keeps its name on purpose now that no gate writes into it. It is an
**on-disk layout** rather than a symbol: `sweep` decides whether a run root may be
reclaimed by whether a verdict was ever recorded under it, and every run root an
earlier build left behind carries this directory. Renaming it would leave each of
those answering that nothing judged it — the answer that keeps a workspace forever.

The durable copy exists because the run does not. The push runs inside a worktree that
is removed as soon as the workstream settles, and a run root is retained only while
recovery may still need it — so a *passing* merge path used to leave nothing readable
once the work landed, while the *failure* it superseded stayed on disk. A branch whose 16:55
rejection was recoverable and whose 17:48 pass was not is what this closed. Both
verdicts land in the same place by the same mechanism, whichever driver published: a
lifecycle node, `just repo-recover`, `just publish-branch`, or `just integrate`.

One file per invocation rather than one appended log, because reading the second of
four attempts out of a single 190 KB file meant counting bytes into it. Numbering
starts from the highest the branch has ever reached rather than the first free gap, so
the directory reads in the order the attempts happened, and a number another writer
already claimed is never written over — a retry beside the recovery of what it
replaced is exactly that race. Retention keeps the newest **10**
(`merge_path::PRESERVED_LOG_ATTEMPTS`) and prunes the rest, so a branch that re-pushes
through a red merge path all night cannot grow the directory without end. Contents are
passed through `stream::redact` before they are written.

A publication that fails *before* the merge path ruled — a base that moved, a fetch or
a worktree that could not be built — preserves no log, because none was produced. Its
whole account is the `reason` on `PublishOutcome::Failed`, which reaches the node as
its settlement detail.

Four things a reader may arrive looking for are not here: there is no
`merge-gate-coverage` event recorded before a dispatch, no `verification-finished`
event bracketing the push, no `gate_log` on a node's artifacts, and — since onevcs
0.11.0 — no `gate-started` or `gate-verdict` event at all, because nothing here runs a
tier to bracket. The `push` event on the session's own stream is the whole record, and
a node result carries no artifact paths at all.

### Keeping a process that outlives its launcher

Some processes here are *meant* to outlive the thing that started them: a dispatched
worker, a publication driver (`just repo-recover`, `just integrate`), and above them
the driver `just orchestrate` spawns. Reparenting to init
puts every one of them outside the tree walk its launcher would be found by.

This repository no longer arbitrates that. The sweep it used to arbitrate with is gone
with the rest of the implementation: `oneagentgraph sweep` reclaims the two scratch
families it owns and terminates nothing, so there is no reaper here for a long-lived
process to be spared from, and no stamp for it to re-attribute itself under. Keeping
these processes alive, and reaping them once they are done, belongs to the engines that
start them.

What that leaves for an operator is a gap worth knowing about rather than a mechanism to
drive. A leaked worker from a dispatch that ended is nobody's to collect, and it will sit
there until the host is restarted or someone kills it by hand — `just host` is where it
shows up. Never work around that with `nohup` or `setsid` by hand: a process detached
that way loses the attribution the run views are built on, which trades a visible leak
for an invisible one.

### One judged diff, one verdict

A gate tier can end in a judge that is not reproducible — this repository's
llmlint tier does — so the judge *run* is cached: `just lint-llm-diff` drives the
cached Nx `workspace:lint-llm-diff` target, keyed on the whole workspace content,
the resolved base **commit**, and the judge configuration fingerprint. Ask the same
question twice and Nx replays the first run's own terminal output rather than
rolling the dice again. The target's command is `scripts/llmlint-judge.sh`, which
validates the base, stamps `role=llmlint` on the harness session, judges the diff
with `llmlint --diff --diff-base <sha> -v`, and exits with the judge's own status —
that last part is what leaves the caching decision entirely to Nx. The script owns
the rest of how it runs; read it there rather than trusting a second copy of it
here. Nothing writes a verdict record: `-v` makes
the terminal report complete — every rule itemized, plus the `llmlint history <id>`
pointer — and replaying that report *is* replaying the verdict.

A green elides exactly one thing, and it is a size constraint rather than a taste
one. `-v` also prints the oneharness debug view, of which two lines — one
serialized judge call each, every judged file's content inside the prompt — are
214KB of a 226KB run. Nx replays a cache hit as one burst and exits, and a burst
larger than one pipe buffer loses its tail; through `scripts/nx.sh`, whose
redaction filter is a pipe, a 226KB replay arrives cut off at ~64KB, taking the
per-rule verdicts, the summary, and the history pointer with it. So the target
keeps every diagnostic line on a green — including the pointer, which is where
those payloads are retrievable in full — and elides only the payloads, naming each
elision and its size. A failure prints all of it: failures are never cached, so
they stream out as the judge produces them and never face a replay.

**Only a green is cached.** Nx caches successful tasks only, so findings (llmlint
exit 1) and a toolchain that never reached a verdict (exit >= 2) fail the tier and
leave nothing behind: a red re-judges on every run of an unchanged tree. This tier
used to work the other way. The target recorded its findings and status into a
declared output, exited 0 so Nx would store a failure too, and a second script
replayed both — a bespoke protocol whose only purpose was smuggling a red past
Nx's success-only cache. On 2026-08-03 it cost more than it bought: two concurrent
invocations shared one `.nx/llmlint-diff` directory, the second one's `rm -rf`
preamble unlinked the first's report mid-append, and Nx cached `status=1` with a
zero-byte report under the real key. `--skip-nx-cache` — the documented re-judge
lever — could not displace the poisoned entry, and `main` stayed blocked until a
full `nx reset`. Caching the run directly gets non-caching of broken runs, output
replay, and race-freedom from Nx for free. The trade is explicit and was the
operator's call: a branch working through a red pays an honest re-roll each time,
and every roll lands in `llmlint history` (retained at `history.max_runs` in
`llmlint.yml`).

That only holds while the key is a function of the judged question alone, so
`scripts/llmlint-fingerprint.sh` resolves the llmlint version *and* the merged
config through `scripts/llmlint-runtime-env.sh` — the one environment
`scripts/llmlint-judge.sh` also judges under. One helper, sourced by both ends, is
the whole mechanism: neither end can read a value the other did not.

`LLMLINT_ONEHARNESS_BIN` is the input that actually varied. `llmlint config`
renders it into its output as `oneharness.bin`, and it is not one value: a
dispatched agent inherits the dispatching checkout's root — the
orchestrator's own checkout, never the worktree being linted, so the fingerprint's
`{root}` fold-out cannot strip it — or `scripts/session-setup.sh`'s session path,
or nothing at all where `dispatch.py` and `watchdog.py` drop it and the config
renders `"bin": null`. Run the pre-fix fingerprint under those three and it emits
three different digests for byte-identical content. That is the visible symptom:
one judged diff hashes to a key per dispatch, the judge re-rolls on every run, and
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
make "the failing rules differed this time" true. The remote plugins in
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
refetches; expect it to invalidate every cached run.

**The cached green for exactly that content, base commit, and judge configuration
is authoritative, and the worker's gate is where it is paid for.**
The merge path looks up the same key and replays what the worker cleared — the
`pre-push` hook the publishing push runs, which is `just gate`, the same recipe the
worker ran. Until onevcs 0.11.0 this identity also had a `command:` gate resolving to
that recipe a third time, which is the duplication the removal ended. That is the only
assignment consistent with the invariant that *a dispatched change is not done until
its own gate is green*: an agent can only clear findings it was shown, so a verdict
that first appears after the agent has settled can neither be cleared nor appealed.

A worker that settled **red** is judged again on the merge path, because nothing
was stored for it. That is not a way past the gate: the merge path runs the same
tier over the same content and base, findings still reject it, and the node still
settles `publication-failed` naming the rejecting command. What it costs is a second
roll of a non-deterministic judge on work that already failed once — the accepted
price of the trade above, and the one direction where the two paths no longer share
an answer.

Keeping the key equal across the two runs is what makes this hold, and the
comparison base is the part that used to drift. A worker left to discover its own
base resolves the remote HEAD, while the publishing push judges the workstream's
recorded PR base — the parent branch for a stacked node, not the repository
default, and a trailer on the branch rather than any field of a plan or flag of a
verb. Two
different base commits are two different diffs and therefore two independent
judge rolls, the second one invisible to the only party who could act on it. So
`onevcs`'s comparison environment is the one source of that identity, and the
lifecycle exports it into every dispatch of a workstream **and into every publishing
push**, where `scripts/comparison-base.sh` and the pre-push hook read it. Reading
only the older `ORCHESTRATOR_*` spelling is how that identity was lost once already:
the names moved with the engine, nothing here followed them, and every lifecycle
path silently fell back to resolving its own base — which is exactly the drift this
section exists to prevent, wearing the appearance of a working mechanism.
Nothing else stands between the worker's verdict and the
merge, so a push that resolved its own base could merge work whose own gate had
failed.

That identity is a **workstream** boundary, not a dispatch boundary. Measured on
2026-08-27 against the installed onepipeline 0.16.3 binary (which its SBOM and
embedded crate paths both identified as linking onevcs 0.15.4) and re-read against the
onepipeline 0.34.0 / onevcs 0.24.0 pair, every follow-up shape
keeps the workstream's publication base:

| Follow-up shape | What `ONEVCS_COMPARISON_BASE` names | Measured source | Judged surface |
| --- | --- | --- | --- |
| A later step in one lifecycle node | The base on the existing session, unchanged when the next step adopts it. | A second `session-opened` event for one token carries `reused: true` and the same `base`; for example `s-1eb70760df86` recorded `base: main` both before and after adoption. The engine exports that `Session.base` to the dispatch. | Every change on the branch since that base, including earlier steps. |
| A retry pinned to a preserved branch | The integration/publication base supplied when the retry opens the branch, not the preserved tip or the previous attempt's starting commit. | A new `session-opened` event carries `continued: true` and its `base`. In the observed notignored chain `s-eb9396e3be58` -> `s-1d39dd1d8430` -> `s-47281e54cb6c`, every session record says `base: main` while each new worktree continues branch `onevcs/s-eb9396e3be58`. | Every change on the preserved branch since that base, including every earlier attempt. |
| A dispatch continuing the pinned branch of a stopped run | The original session's recorded base. | Resumption emits `session-opened` with `reused: true` on the original token; `s-064f1bc2517f`, for example, retained `base: main`. `workspace::resume` returns that record rather than constructing a new comparison point. | Every change on the resumed branch since that base, including work left by the stopped dispatch. |

`scripts/comparison-base.sh` then turns the exported pair into the remote-tracking
ref (for example `origin/main`), and `scripts/llmlint-fingerprint.sh` keys the
verdict on the commit that ref resolves to. It has no session tip, previous dispatch
tip, or previous published branch tip from which it could select a narrower diff.
Consequently a three-line follow-up can be failed by a judged-tier finding anywhere
on the earlier branch surface. A worker can clear such a finding even though it is
outside that worker's requested delta, or report the out-of-scope finding and let the
node fail; there is no supported way today to ask this tier to judge only the
follow-up without making the worker and publishing push ask different questions.

Changing that locally would break the invariant this section records. The publishing
path calls `merge_path::comparison_env("origin", context.target.base())`, where the
target is the branch or change request's publication base, so it replays the
whole-workstream question. A sound incremental design is therefore a **onevcs
follow-up**, not a different fallback in `comparison-base.sh`: onevcs would have to
record an immutable previously judged frontier (normally the last published branch
tip), export that comparison commit to both the follow-up dispatch and its publishing
push, and establish that the verdict covering the prefix from the publication base
to that frontier is still valid. This repository would then need to accept that
explicit commit as a comparison target and include it in the existing fingerprint.
Without the recorded prefix verdict, narrowing merely stops the merge path from
judging part of the tree it is about to publish.

Where the key genuinely differs the hook does judge again, and its verdict is
then the authoritative one, because it is the only judgement of the content that
will actually land: the base advanced after the worker settled and the merge
changed what is being published, so the worker's clearance never covered it. To
keep that honest rather than silent, a passing `just gate` reports which base
commit was judged and whether the verdict was judged now or replayed from the
cache — green is always a claim about one specific base commit. That provenance is
read out of Nx's own cache reporting, not out of a side-channel marker; the
journeys below assert both wordings, so an Nx upgrade that renames the line fails
the suite instead of quietly calling every run freshly judged.

None of that is provable from one checkout, which is where this went wrong once:
`tests/e2e/test_llmlint_cache_e2e.py` asks twice from the same tree, and
production never does. `tests/e2e/test_llmlint_two_path_verdict_e2e.py` runs the
tier's recipe from both callers instead — a worker worktree carrying the
`LLMLINT_ONEHARNESS_BIN` a dispatch inherits, and a detached scratch worktree
rebuilt by a squash merge carrying only the comparison identity a publishing push
does, both cut from one clone — and counts how many times the judge was rolled for
one content and one base. For a green the answer has to be once. Run it against the
fingerprint as it stood before `2ba9685` and it is twice: the merge path re-judges
work that had already been cleared. The failing-verdict journey asserts the other
half — a red is rolled again on the merge path and still refused — so the cost of
the trade is stated by the suite rather than assumed. The three invalidations are
asserted across the two paths for the same reason, because a fix that made them
agree by hashing less would replay a verdict for a tree nobody judged. The
publishing push those verdicts decide is not restaged there;
`tests/e2e/test_publish_branch_e2e.py` drives it through the real verb against a
real `pre-push` hook, which since onevcs 0.11.0 is the only verifier in that path.

Forcing a real re-judge is deliberately **per tier and per invocation**:

```sh
just lint-llm-diff origin/main --skip-nx-cache   # re-judge the llmlint tier
just test --skip-nx-cache                        # re-run the test tier
```

Know its limit before planning a rescue around it: under this Nx, `--skip-nx-cache`
neither reads nor writes the cache. It buys one fresh look and leaves the stored
run exactly where it was, so a **wrong green** an operator disagrees with sticks —
the next ordinary invocation replays the same entry — until the tree, the base
commit, or the judge configuration moves. `./scripts/nx.sh reset` is the blunt
instrument that does clear it, at the cost of every other cached target for this
repository. Go through the wrapper: it is what points `NX_CACHE_DIRECTORY` at the
per-repository shared cache root these entries actually live in, so a bare
`nx reset` would clear Nx's default location and leave the stale green in place.

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
`llmlint.yml`, `scripts/`, `.githooks/pre-push` — so
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
  `just` recipes and shell scripts — keyed on `recipeWorkspace`, which
  `orchestrator/project.json` declares: the `justfile`, `scripts/**`, the root
  manifests, the fixtures, and the modules that define those tests, plus the
  test-support units their modules import. They read no prose and no `orchestrator/`
  at all, and most commits here touch nothing else, so most commits replay them.
- **`orchestrator:test-checkouts`** runs the tests marked
  `@pytest.mark.reads_checkouts` and is **uncached**, because there is no key that
  would be right. Its subject is the *other* repositories this host routes — their
  registered checkouts, and the required checks each merge path really declares,
  which `just repos --audit-gate-coverage` reads off GitHub — and those live outside
  the workspace, so no `nx.json` glob could name one and a memo would describe
  whatever they required when it was recorded.
- **`orchestrator:test`** runs everything else, keyed on `codeWorkspace` — the
  whole workspace with `docs/**` and `**/*.md` removed.

`just check` runs these in three phases, because Nx takes one selection per invocation:
the diff selection (`format-check`, `lint`, `typecheck`, `test`, `test-docs`,
`test-recipes` over the projects `scripts/nx-selection.sh` picks), the unconditional
`test-checkouts` and `coverage`, and `workspace:check-nx-cache`. Every phase runs
whatever an earlier one returned, all three write one `.logs/check.log`, and the recipe
fails if any phase did, naming each that failed — so one run reports every failing
target rather than the first phase's alone. One tier is outside all three:
`session-setup-pypi:test-pypi`, the journeys that run real session setup in a fixture
repository and install the adopted published tools from PyPI. It is a project of its own
because an outside service is no diff's to charge, and `just test` and `just upgrade`
run it.

`tests/conftest.py` holds that last boundary from the other side: an *unmarked* test
that opens a registered checkout fails there, naming the checkout and the marker,
because such a read in a memoized tier is exactly the false green these keys exist
to prevent.

A shared helper under `tests/` reaches a key through the project graph rather than
through a list each tier keeps. Every such module is a **test-support unit**: a project
of its own under `tests/support/`, whose `testSupport` named input carries the module
and the data files it opens, and whose `implicitDependencies` are the units it imports.
Each test project depends on the units its modules import, and its narrow target takes
them with `{"input": "testSupport", "dependencies": true}`, which Nx hashes across every
transitive dependency; `orchestrator`, whose `test-recipes` reaches fewer helpers than
its other tiers, names that target's closure with a `projects` input instead, because
Nx's edges are per project. So an edit to a helper invalidates exactly the tiers whose modules reach it,
`nx affected` selects them through the reverse edges, and each tier's own key lives in
its own `project.json`, leaving `nx.json` only the keys every project shares.

`workspace:check-nx-cache` is narrowed on the same principle rather than by tier:
it builds two linked worktrees out of `tests/fixtures/nx-cache/` and drives the
real `scripts/nx.sh` in both, so `nxCacheCheck` carries that fixture, those
scripts, and the root manifest — and nothing else.

Where a literal cannot be told from a read the key is deliberately the wider one —
the fixture records `.githooks/pre-push` as a journal detail *value* and never
opens it, and the key carries it anyway. The two errors are not symmetric. A key
wider than its reads costs one run nobody needed; a key narrower than them is the
false green above.

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
but documentation and the front end falls outside the code key, that every tier of the
suite depends on exactly the test-support units its modules import and takes exactly
those into its key, that no configuration names a path that does not exist, that the
recipe key covers every module routing a test into it and stays inside the code key, that the
cache-check key carries every `$root/` path its script names, and that every
repository file the browser tier's fixture stack imports or names is part of
`dagUiServerSurface`. That last one is the browser tier's `conftest.py`: a runtime
guard cannot see an import, so the reads are reconciled from the fixture's own
import closure, and a fixture that starts loading Python outside the surface fails
with the named input it has to widen. `tests/e2e/test_nx_cache_scope_e2e.py` then
proves each one against real Nx over a copy of this checkout: for every key, an
edit inside it must miss and an edit outside it must replay.

One more thing has to hold for a replayed test verdict to be usable, and it is
proved in the same place: the measuring tiers declare their coverage data files as
Nx `outputs`, so a cache hit **restores** `.coverage.parallel` and `.coverage.serial`
rather than leaving the uncached combine with nothing. Delete both, replay both
tiers, and `orchestrator:coverage` still combines and reports — a dropped `outputs`
declaration turns every replayed commit's gate into a failure instead of a saving.

Three tiers, one answer, and none of them lenient: a recorded llmlint **failure**
replays as a failure, and a tree the suite would fail can no longer replay a pass.

#### Four workers, and the tests that cannot have any

The suite waits on subprocesses rather than on compute — a serial run holds one
core at about 3.5% for a quarter of an hour — so its wall clock is latency and
workers are nearly free. `orchestrator:test`, `orchestrator:test-docs`,
`orchestrator:test-recipes`, `orchestrator:test-checkouts`, `plan-tooling:test`,
`plan-tooling:test-docs`, `session-setup-pypi:test-pypi`, each ask-seam journey's `test`
target, and `just test-e2e` all run `-n 4 --dist loadgroup`.

Both numbers come from measuring this host, not from a default. One sample each,
same tier and same selection, taken back to back while a second worktree ran its
own suite — so they are comparable to each other and pessimistic in absolute
terms: `-n 4` 322s, `-n 6` 386s, `-n 8` 354s, `-n 14` — what `-n auto` resolves to
here — 345s, and one serial sample at 843s.

The curve is flat past four, because what the run cannot beat is its longest
single test, not its core count; more workers buy no wall clock and take cores this
host wants for live dispatches. `--dist loadfile` measured 331s but raises that
floor from the longest *test* to the longest *file*, which is nearly the whole
measurement. `--dist worksteal` measured fastest at 280s, but that sample failed a
race-window test and the run-to-run spread at a fixed configuration is the same
size as its lead.

A test whose subject is a process-wide or machine-wide resource declares that as a
scheduling constraint rather than as a loosened assertion. Two markers used to
carry those constraints here — one for a test whose subject is the process itself,
selected into a serial tier, and one for a family of journeys whose constraint
lives *between* tests and which therefore share an xdist group. Both went with the
dispatch journeys that needed them when this repository became a configuration
layer. Neither was ever a place to put "this was flaky once", and a test of either
shape reintroduces its marker, its tier, and its reason together; a test that is
merely slow, or that races something it does not own, is a test to fix.

#### One tier measures, another judges

`orchestrator:test` measures under pytest-cov — which is what carries coverage into
the xdist workers — into `.coverage.parallel`, and **judges nothing**. The uncached
`orchestrator:coverage` waits on it, reads that file, and reports.

`coverage report` is what compares the total to the declared floor, so the floor
has exactly one source and is evaluated exactly once. The measuring tier carries a
`--cov-fail-under=0` because pytest-cov otherwise adopts `fail_under` from the
config, and a memoized tier that judged the floor would let a replayed pass stand
in for a comparison nobody made; that zero is the tier declining to judge, not a
second floor, and `tests/test_coverage_gate.py` holds every target to it — no
target may name a non-zero floor, and none but `coverage` may report. That module
also drives the whole shape for real, over a generated package whose total lands in
the rounding band the floor once forgave.

Two more things follow, and both are declarations rather than conventions. The
enforcing command names the data file it reads, so a tier whose data never arrived
fails the command instead of quietly lowering the total the floor is judged
against. And `orchestrator:coverage` is **uncached**: its input is a file on disk
rather than the tree, it costs seconds, and a floor that always runs is one no
replay can skip.

`tests/test_nx_cache_scope.py` holds the three tiers to a real partition —
collecting each selector for real and requiring their union to equal the suite —
because commands selecting on markers is exactly the shape that drops tests in
silence.

## Merge strategies (where the change lands)

Selected from normalized identity type plus workflow. A
task/plan workflow value may assert the expected workflow but cannot contradict a
registered identity; use the migration command between runs to change it. New and
genuinely unknown identities must infer from authenticated GitHub ownership or
receive an explicit type; a filesystem path is not ownership evidence. Merge-policy
CLI defaults are intentionally unspecified: node policy beats command policy,
then repository-type defaults apply.

The policy names are the published `merge_policy` vocabulary — `local-direct`,
`change-open`, `change-auto`, `change-direct` — and a plan that writes the older
`direct` / `none` / `auto` spellings is refused by name at launch. That
vocabulary is `onepipeline`'s and this list is a copy of it: the launcher
enumerates what it accepts in the refusal it writes for what it does not, and
`tests/e2e/test_orchestrate_launch_e2e.py` holds every copy of the list in this
repository — here, in `AGENTS.md`, and in `docs/orchestration.md` — to that
enumeration.

- **Team** — always effective workflow `remote`. Omitted policy opens an ordinary
  ready-for-review PR and returns `change-open` immediately, without polling checks.
  Explicit `change-auto` or `change-direct` merges the PR by that policy. Team
  plus local registration/workflow migration/direct integration is rejected.
- **Single owner** — omitted policy preserves `local-direct` publication or
  `remote` auto-merge. Explicit `change-open` forces remote PR publication for
  that run and leaves the PR open without mutating a stored local workflow.
  Because the local strategy only supports direct publication, an explicit
  `change-auto` is reported as the effective `local-direct` policy when the stored
  workflow remains local.

Single-owner automated publication is serialized by a process-shared FIFO merge
queue keyed by the **publication** checkout's git common directory — the one thing
every run of an identity shares, now that each run merges from a clone of its own.
Worktrees and checkout
aliases of one identity therefore enqueue together, including `local-direct`
merges, remote `change-auto`/`change-direct` merges, recovery, and the direct
`integrate` train. Each
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
while authoring. Resolve-and-requeue attempts are bounded before the publication
fails with `FailureKind::SyncConflict`, which is one of the four a further attempt
could answer: the branch is retained, the node is dispatched again onto it with that
reason, and only a spent publication budget settles it `sync-conflict`. A push
declined because the branch moved on the host since this run last had it is the same
failure kind, and its reason names the two shas and the `just publish-branch` that
lands it after a reconcile.

- **The change-request path** (`change-*`, GitHub repos) — `publish_as_change`
  pushes the branch, then adopts an existing change request for the same head and
  base or opens one, then asks the host to land it. `change-open` returns there.
  The rest take a ticket in the identity's merge queue first. The default policy is
  GitHub **native auto-merge** (`gh pr merge --auto`), which by construction gates
  on required checks and ignores optional ones — so a non-blocking check never
  triggers or holds a merge. Policies: `change-auto` (native auto-merge; falls
  back to merging directly if the repo disallows it), `change-direct` (merge it
  ourselves), `change-open` (open the change request and stop).

  **What `onevcs` watches follows the merge policy, and never a gate.** That is the
  correction onevcs 0.10.0 made, and the old rule is worth knowing because it silently
  did nothing here: watching used to happen only where the resolved gate was
  `{kind: checks}`, and every rule on this host then named a `command:` gate — so the
  host's required checks were observed for no repository at all. onevcs 0.11.0 removed
  the gate concept outright, so there is no longer a resolved kind for this to have
  keyed on either way. Now a `change-direct` publication calls `await_checks` before asking for the
  merge it is about to perform itself, and a `change-auto` one arms auto-merge
  inside a watch that ends at the merge the host performs. Only required checks
  count (`statusCheckRollup.isRequired`), each transition is emitted as an
  `EventKind::ChangeCheck` event carrying the check's log as an artifact, and the
  three ways out are each named: the watch's own ending, a required check concluding
  red (`Error::ChecksFailed`, quoting its log), or the bound elapsing with one still
  outstanding (`Error::ChecksUnsettled`, naming what was pending). Those are two
  different situations and they now settle two different words — see [What a failed
  publication actually settles](#what-a-failed-publication-actually-settles) — where
  a single sentence about settled checks used to cover both.

  **Under `change-auto` a node settles when the merge completes, at the commit the
  host merged it at.** That is the other half of 0.10.0: the watch runs to `merged`,
  the landing is recorded as a provenance trailer on the branch itself — a `chore:
  record the landing of <branch>` commit whose `Orchestrator-Landed-Commit:` names the
  base commit the host merged it at — and the publication checkout is fast-forwarded.
  **That trailer is written and never read back**: `onevcs status`'s landing tiers look
  for a trailer on the *base*, which only the `local-direct` path below writes, so it is
  a record for a person reading the branch rather than one the verbs consult — see
  [what `decided by:` can reach, per workflow](#what-decided-by-can-reach-per-workflow).
  So a `change-auto` node no longer counts
  as *settled without landing* for the life of the run — the state that used to
  persist after the PR merged, after the release went out, forever. What it costs is
  time: the node holds its identity's merge slot until the host lands the change or
  the bound elapses, and a host holding it behind a check nobody declared is what
  `checks-unsettled` is for.
  Before opening one, `find_changes` queries the host for the same head and base and
  the first result is adopted rather than a second opened.
- **The local path** (`local-direct`) — there is no change request to wait on, so
  it builds the branch-to-base merge in a detached scratch worktree and pushes
  that exact tree, which is where the repository's own `pre-push` hook runs. The
  branch lands as one squashed commit whose single parent is the prior base tip and
  whose message is the composed subject, plus one
  `Orchestrator-Landed-Commit: <the branch's tip>` trailer under the rules file's
  `trailer_prefix`. That trailer is the whole reason a `local-direct` landing stays
  decidable after this host forgets it: it is on the base, in the repository's own
  history, and it is what `onevcs status`'s third tier reads. This is the model for
  direct merge into main after the checks pass, including GitHub origins intentionally
  marked local.
  A **bare** local origin accepts the push directly; a non-bare origin needs
  `receive.denyCurrentBranch=updateInstead` so its working tree updates too.

Only after content lands on the canonical checkout's checked-out root does that
checkout fetch and fast-forward with `--ff-only`. A merge into a feature or
synthetic stack base does not advance it. No merge assembly,
checkout, or hard reset occurs in that canonical working tree.

### Merged is not necessarily published

A `merged` node outcome proves that the change reached its base branch. It
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

A lifecycle node is an `agent` node in a plan with a `repo` and either a
`persona`+`task` or a `steps` workstream. In the plan store that `repo` is the first
entry of the task record's own `repositories` list — a hosted repository's normalized
origin, `host/owner/name` — and the reserved `onepipeline.repo` key carries it only for
an identity that list cannot hold, a local checkout named by its absolute path; the two
are never written together. The `repo` shown in the plan documents on this page is the
loaded node's field, which the engine reads from either. `onevcs` is handed that value
verbatim, so among several registered checkouts of one identity the origin form selects
the one whose alias sorts first; `tests/test_registry_resolution.py` holds this host's
checkouts to that order. The `Node` schema is
`deny_unknown_fields`, so what it may carry is a closed list —
`id`, `kind`, `task`, `amendment`, `persona`, `deps`, `max_turns`, `expects_no_diff`,
`context`, `parked`, `executor`, `agent_graph`, `repo`, `repo_type`, `workflow`,
`merge_policy`, `base_branch`, `branch`, `title`, `body`, `draft`,
`execution_checkout`, `steps`, `resume`, `adoption`, `consumes`, `delivers`, `pool`,
`overflow` — and
anything else is refused while the plan loads. `pool` and `overflow` arrived with
onepipeline 0.40.0 and are the worktree pool's half of the schema, copied onto the
session request a lifecycle step opens with: `pool` is a non-negative integer — `0`
places that session fresh under `runs/` and still spends the identity's overflow —
and `overflow` a non-negative integer or `unlimited`, which opts that one open out of
the cap; both are refused by name below schema 3, the way `draft` is, and neither
sheds a warm slot, because surplus is measured against `$ONEVCS_HOME/workspaces.yml`
alone (see [where a session is placed](#where-a-session-is-placed-and-what-a-close-returns)).
`draft` arrived with onepipeline 0.28.0:
a node carrying `draft: true` leaves the change request it publishes as a draft at
closeout for a person to lift, settles `done` with outcome `change-draft` and no
landing, and is refused at load on a node whose resolved publication opens no change
request. `adoption` and `consumes` arrived with onepipeline
0.13.0 and are release adoption's half of the schema: `adoption` is `fast` or
`published`, and `consumes` names a release target per **dependency node id**. Both
are validated at load — `adoption: "bogus"` is refused with `unknown variant
`bogus`, expected `fast` or `published``, and a `consumes` key that is not one of the
node's own `deps` is refused naming the node and the key — and both resolve to
today's behaviour on a host that declares no release targets, which this one is. `stack_bases` is a pre-adoption
field held only in `tests/fixtures/legacy-runs/`; a plan that writes one is refused,
and so is `verify_via_ci`, which this schema once accepted. Independent top-level nodes run
concurrently, and a node whose dependency failed is skipped. Cross-repository dependencies only schedule. A
successful same-identity dependency not landed on the root base becomes a stack
prerequisite:

The default PR title is derived from the most significant Conventional Commit
subject on the branch, with a non-releasing `chore:` fallback when none is usable
— see [A subject names the change, whole](#a-subject-names-the-change-whole). An
explicit `title` must itself be a Conventional Commit subject of at most **120**
characters — `onevcs::provenance::SUBJECT_LIMIT`, raised from 72 in onevcs 0.2.8 —
and nothing counts it by hand: since onepipeline 0.6.1 the plan **loader** holds
every node title to it, so a title one character over is refused before any node is
dispatched. Measured against the adopted pair: 120 loads, and 121 is refused with
`invalid: node 't': the title is 121 characters, over the 120-character limit onevcs
holds a publication subject to`.

All explicit task, base, anchor, and recovery branch names pass Git's literal
branch validator before any Git command.

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

The child PR body lists dependency PR links and stack bases. Its merge path judges
the complete stack, but the PR diff against its stack base is child-only.

### Diff-derived PR descriptions

<!-- llmlint: ignore[changed_behavior_has_e2e] Every behaviour this section describes past the graph run belongs to `onepipeline` and `onevcs` — when drafting is invoked, what the plan's own `body` bypasses, and what a publication does when a draft cannot start — and each is proven in its repository. This suite doubles the published CLIs at the recipe boundary precisely because driving one for real would open a pull request on a real repository. What this repository owns is the graph and its response contract, and `tests/e2e/test_orchestrate_launch_e2e.py` drives that for real: the launch record's `pr_author_graph`, a drafting turn answering `{body}`, and a non-conforming answer being re-prompted. -->

**One drafter, reached two ways.** A change request's body is drafted by an **agent
graph a command names** — [`graphs/pr-author.yaml`](../graphs/pr-author.yaml) — and the
engine is what runs it on both roads a branch takes to a change request here: a run's own
publication closeout, and the two out-of-band landing verbs, which is how branches
usually reach a base on this host.

| command | what it lands | how drafting reaches it |
| --- | --- | --- |
| `just orchestrate <source:project>` | a run's own lifecycle publications | `onepipeline start --pr-author-graph graphs/pr-author.yaml` |
| `just publish-branch <branch> --repo <checkout>` | a complete branch no session holds | `onepipeline publish-branch --pr-author-graph graphs/pr-author.yaml`, into the linked `onevcs publish-branch` |
| `just repo-recover <branch> --repo <checkout>` | a preserved branch with an incomplete-step marker | `onepipeline repo-recover --pr-author-graph graphs/pr-author.yaml`, into the linked `onevcs recover` |

`just integrate` is not among them and needs nothing: the local merge train opens no
change request, so there is no body for it to carry.

Naming no graph is the shipped default, and a launch that names none opens its change
requests with the body its plan states, or with none. `just orchestrate` names this one,
so every remote publication from this host is drafted and a bare `onepipeline start`
elsewhere is not. A node that states its own `body` (a plan schema 3 field) publishes
with that.

The landing verbs are the engine's, so what an operator's landing publishes is what the
run path would have published for that branch. Their contract, which the recipes rely
on and `tests/e2e/test_publish_branch_e2e.py` drives through them: the branch, `--repo`
— a path or an alias `just repos` lists — and every other argument reach the linked
`onevcs` verb unchanged; a caller's own `--body` or `--body-file` is forwarded untouched
and spends no turn; `--no-draft` spends none either, and is the escape for a bulk
landing down `just recoverable`; a `local-direct` identity, which builds the base's
squash commit itself and opens no change request, is never drafted for; and a draft
that cannot run — its dispatch failed, or it answered with no body — never blocks the
landing, which opens its change request with no body and says which ending left it
so. The recipes name the graph ahead of the caller's arguments, so a caller ending its
options with `--` still has its branch drafted for.

**The turn is spent before the gate**, which is a decision rather than an oversight.
The body is an argument to `onevcs`, so it has to exist before the verb is called, and
the verb is what runs the identity's merge path: a branch the merge path then rejects
has paid for a body nothing used. The cost is bounded and rare, and `--no-draft` is the
escape.

**`onevcs recoverable`'s printed `Resume:` line drafts nothing.** It renders its own
argv — `onevcs publish-branch <BRANCH> --repo <PATH>` — and pasting that reaches the
verb directly, below the engine's drafting verb, which is a change request opened with
an empty description. `just recoverable` re-renders each of those commands in its `just`
form for that reason, so the line an operator pastes is the one that drafts; every other
line of the report, and the whole of `--json`, is `onevcs`'s own and passes through
untouched.

On the run path, what runs is one turn of that graph, after the final branch-vs-base
gate passes and before the change request is **published** — the one ordering the
landing verbs cannot have, for the reason above: there, drafting is what produces an
argument to the verb that runs the merge path. **The closeout runs in one order, and the
onepipeline 0.28.0 moved where the drafter sits in it**
(https://github.com/nickderobertis/onepipeline/pull/235, on the `onevcs` half at
https://github.com/nickderobertis/onevcs/pull/138):

1. The steps run and settle. Nothing moves before verification.
2. The session's own change request is read — `onevcs change show`, never an open —
   because a worker may have opened it as a draft itself: `onevcs publish
   "$ONEVCS_SESSION" --draft`, which `config/dispatch-appendix.md`'s carve-out lets a
   lifecycle worker do when its task says so, and under which it may write what only it
   knows into the description (`onevcs change describe`) and stack a throwaway
   demonstration change request on the draft. A host that could not be asked has not
   said the session holds nothing; that is said on standard error and the closeout
   publishes as it always has.
3. The drafting dispatch runs, in the node's own worktree, on a composed task:
   `onepipeline`'s opening sentence — "Read this branch's diff and write the change
   request's body, following the repository's own template" — then the task the branch
   delivered, then a `## Change request` section **only where the session holds one**
   (its URL, whether the worker holds it as a draft, and the description verbatim under
   `### Description as the worker left it`), and **always** a `## Worker transcript`
   section naming `onepipeline transcript <RUN> <NODE>`, which `ONEPIPELINE_RUN_ID` and
   `ONEPIPELINE_RUNS_DIR` in the dispatch's environment serve, so the drafter can read
   every tool call the worker made and cite what it finds. `graphs/pr-author.yaml`'s
   member is written to finish the description the worker started and to read that
   transcript, and `tests/test_engine_contracts.py` holds both headings to the pinned
   engine.
4. Where it drafted a body and the session holds a change request, the body is written
   onto it — `onevcs change describe`, under the node's own `title` on every write, so
   the change request carries the plan's subject whatever the worker opened it under —
   and the description write reaches the run's journal as its own `review`-phase event
   through the session follow. Where it
   drafted none, `body-not-drafted` is recorded as below and the description stays as
   the worker left it, said on the settlement.
5. One publication — `onevcs publish` with no body where the session holds the change
   request, and with the drafted body where it opens one — which lifts the adopted draft
   (`draft-lifted`) and lands under the resolved policy. The node settles on the
   publication's own words, and its detail says the worker opened the change request as
   a draft, that the closeout wrote the description onto it, and that it was marked
   ready for review.

The drafted body is read out of `results[].structured.body` of the retained member
report. Stack metadata is appended as usual. It costs one turn per published change
request, including workstream and draft-checkpoint ones — and a retry that continues
a preserved branch whose earlier attempt opened a change request now has its
description **rewritten** from the final tree at closeout, where before the re-drafted
body was silently ignored for an adopted change request; the drafter sees the previous
description in its task and keeps what is still true.

**A node may leave its change request as a draft on purpose.** `draft: true` on a
lifecycle node — `onepipeline.draft` in a task's metadata, default `false` and omitted
when false — leaves the change request as a draft at closeout for a person to lift:
opened as one where the session holds none, held as one where the worker opened it.
Such a node settles `done` with outcome **`change-draft`** and no landing, and its
dependents proceed. It is deliberately not `complete-but-draft`, which is the status of
a draft a release arrives to lift and which holds the run; where the node also awaits a
release, the release reason wins and it settles `complete-but-draft` as before. The
loader refuses it before anything dispatches on a node whose resolved publication opens
no change request, in the shape `consumes` is refused on a `local-direct` identity.
Every dispatch of a lifecycle node's agent steps, and its drafting dispatch, carries
`ONEVCS_SESSION` naming the session whose worktree it runs in, and a direct node's
carries none; `tests/ask_seam/launch/test_launch_ask_seam_e2e.py` reads both off real
dispatches.

That `structured` is the contract, and this repository's graph is built around it:
`oneharness.pr-author.toml` names `config/pr-author-body.schema.json` as its
`schema_file`, so a turn answers with `{"body": "…"}` or is re-prompted with the
validation error, up to `schema_max_retries`. It is also why that config sets
`stream = false` — oneharness validates a structured answer against the complete
response, so a streaming member and a `schema_file` cannot both hold, and
`oneagentgraph` refuses a member that declares both.

**Drafting never blocks publication, and it has no second attempt.** A drafting
dispatch that cannot start, and a publication with no worktree to draft in, each
warn on the node — `onepipeline: node '<id>': … so it publishes with no body` — and
publish with no body at all. There is no deterministic body it falls back to and no
retry of the graph run.

**It is not silent either, on the adopted onepipeline 0.45.0.** Where a drafting
dispatch was *configured and attempted* and produced no body, the run records a
`body-not-drafted` event against the node carrying `ending` and `detail`, and the
same `detail` lands on the node's own settlement — after the publication's reason
where that failed too, because the publication is what settled the node — so `just
results` shows it without a reader opening the store. Three endings, kept apart
because they take three different fixes:

| `ending` | what happened | the fix it points at |
| --- | --- | --- |
| `dispatch-failed` | the drafting graph could not be run, or ran without succeeding (a failed turn and a cancelled one both land here) | the graph, its harness config, or its quota |
| `schema-refused` | it succeeded and the schema it was validated against rejected every answer it made | `config/pr-author-body.schema.json`, or the prompt that answers it |
| `no-body` | it succeeded and there was no body in what it answered with | the drafting persona's prose |

Nothing is emitted for the two endings that are **not** failures: a launch that
named no pr-author graph, and a node that carried its own `body`. Neither spends a
dispatch, and an event for either would report the shipped default as a fault. Below
0.7.5 there was no kind for any of this and the warning on the node was the whole
record, which is why a bodyless change request could not say whether the drafter ran
and failed or was never wired at all.

### A branch landed by hand takes the body its caller gives it

All of the above is the *lifecycle's* path to a body, and nothing drafts one for a
branch an operator lands themselves. Until onevcs 0.7.0 there was nowhere to put one
either: `publish-branch` and `recover` took a `--title` and no body, so every branch
recovered by hand opened a change request with an empty description — and those are
the branches with the most context to lose, because a branch reaches those verbs by
its workstream dying rather than by finishing.

Both now take `--body <TEXT>` or `--body-file <PATH>`, and the body reaches the
change request verbatim whichever publication path lands the branch. Naming both is
refused by name rather than ranked. Omitting both keeps the old behaviour, so this
is worth stating as a habit rather than a flag: **write the body**. A file is the
usual shape, since a body is prose and a shell is not where prose is edited.

```sh
just publish-branch <branch> --repo <checkout> --body-file /tmp/body.md
just repo-recover <branch> --repo <checkout> --body-file /tmp/body.md
```

`integrate` takes neither, and that is not an omission: the local merge train opens
no change request, so there is nothing for a body to be the description of.

Run these nodes with `just orchestrate <source:project>`, the one way to dispatch.
See the `examples:tracked-release` and `examples:repo-plan-example` projects, and
the one-node `examples:health-endpoint` and `examples:scheduler-research` forms.

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

When a human step becomes ready the node settles **`waiting`** — the status is that
one word; there is no `waiting-human` — and its dependents derive `blocked`. The
settlement carries the branch and the steps already completed, and the session's
event follow is dropped, because nothing is left to read for as long as the driver
lives. The branch and its commits are preserved; the worktree is the session's and
goes when the session does.

**A pause pushes nothing and opens nothing.** The conclusion is unchanged and the
reason it used to rest on is gone: both engines now have a draft change request —
`onevcs` 0.32.0 answers `PublishOutcome::ChangeDraft`, *"change request open as a draft
… which cannot land while it is one"*, and `onepipeline` v0.45.0 settles the node that
made one `complete-but-draft` — so "no notion of one" is no longer why. A draft is a
**publication** outcome, reached once the last step has settled and the publication
starts, because a release the node adopted early has not happened yet or because the
node declared `draft: true` — or a **worker's** outcome, reached from inside a step
under the appendix's carve-out; nothing on the pause path itself can produce one, on a
local or a remote identity. A pause is still purely local branch state, and the
difference now matters: a node holding a draft is neither paused nor settled, and
reading one as the other is what a manager would otherwise do with `complete-but-draft`.

**`attest` takes a node id, and it folds that node to `done`.** This is the part
most worth re-reading before relying on a human step. `compile_attest` accepts one
of exactly two references — a node recorded `waiting`, or a node that settled
`failed` — and records it `done`; there is no `NODE_ID/STEP_ID` reference on the
adopted schema. Since `graph::derive` leaves a recorded settlement standing, a
lifecycle node attested at a human step is `done` and is **not** dispatched again,
so the steps after that human step do not run. Model a human action that work must
follow as its own `kind: human` node with the follow-on work depending on it, not as
a middle step of a lifecycle node.

`resume.checkpoint` is the one field of that record that **does nothing**.
`onepipeline` records the value it was given and carries it forward, but the
`SessionRequest` it builds for a dispatch has only `repo`, `branch`, `base`, and
`execution_checkout` — there is nowhere for a commit to go. So nothing on the
adopted stack checks that a checkpoint is still an ancestor of the continued branch,
and force-rewritten history is not caught here. The one thing validated is
agreement: a `retry` whose `branch` pin and `resume.branch` name different branches
is refused at submission rather than resolved silently.

### Preserved committed work implies a recorded pin

A pause is not the only settlement that leaves commits on a branch, and a preserved
branch is continued only when the node carries a `resume`. So an ending that
preserved work without recording one silently discarded it: the node was
redispatched unpinned, against a fresh branch beside finished work nothing would look
at again. A merge-path gate rejection and a publication that refused its own commit
subject — both after every step had settled `done` — cost one planner three
hand-written branch pins in a single run.

`projection::pin_preserved_branch` is what closes that, and it is keyed on the
**status**, not on a list of endings somebody remembered: `failed`, `cancelled`, and
`parked` all preserve their branch, because each of them may hold work and anything
that runs the node again has to continue it. `done` never reaches there, and
`waiting` and `skipped` never dispatched. The pin is folded from the run's own
journal rather than held in a process, so the graph a later driver reads already
carries it. A `branch` the planner wrote wins outright, and the `resume` follows it.

What the pin **does not** do is re-dispatch anything. A recorded settlement stands:
`graph::derive` re-derives only the two gates `blocked` and `skipped`, so a node
recorded `failed` or `cancelled` keeps that status for the life of the run and a
later pass never picks it up. A `retry` or `requeue` live edit is the whole of how a
preserved branch is continued.

Two things about the pin are worth knowing before reading one:

* **There is no mode.** `Resume` is `{branch, checkpoint?, completed_steps}` under
  `deny_unknown_fields` — no `mode: retry`, no `mode: continue`, no `attempts`. Which
  verb an operator should reach for is read from the branch itself: `just
  recoverable` looks for an unattested incomplete-provenance marker and offers `just
  repo-recover` when it finds one and `just publish-branch` or `just integrate` when
  it does not. See [Which verb lands which branch
  state](#which-verb-lands-which-branch-state).
* **The completed steps depend on where it stopped.** A step that failed, was
  cancelled, or hit its cap settles carrying the steps already done, so a
  continuation skips them. A publication that failed after every step settled `done`
  records **none**, and since onepipeline 0.10.0 that is deliberate rather than
  incidental: the engine's own re-dispatch of a preserving failure is made under the
  same rule, because a continuation that skipped the steps the branch already holds
  would publish that same rejected tree again and meet the same refusal. So a
  continuation re-dispatches the whole workstream over a branch that already carries
  its work — which is what you want when the merge path rejected the tree, and not
  what you want when it did not. For a node that settled `publication-failed`,
  nothing further was tried and `just publish-branch` is usually the move; for one
  that settled under a preserving word, three attempts already were.

`onevcs` hands a branch back where it can: `PublishOutcome::Failed` carries a
`retained` naming whether the branch was `handed-back` to a registered checkout or
`refused` by it. A pin that names a branch no checkout outside the run holds is a pin
a continuation cannot use, and `just work-status <branch>` says so in as many words —
`nothing on this host holds the branch`.

**Whose preserved branch a row is, the row says.** Each `onevcs recoverable` row names
the `session` that opened it and that session's `labels` — the `run`, `node` and
`launcher` the engine stamps on every session a node opens — and the listing takes
`--session <token>` and `--label KEY=VALUE` to answer only the matching rows; a label
nothing carries is an empty report, not a refusal.
`tests/e2e/test_session_labels_e2e.py` drives both filters on the pinned `onevcs`.

## Adapting a running graph

The DAG is never static and there is no interval between adaptations: the
reconciler converges the live desired graph continuously, so an accepted edit —
`retry`, `add`, `drop`, `reparent`, `cancel`, `requeue`, `note`, `attest` — is
applied on its next pass. Completed nodes landed on root leave the frontier as
satisfied. A same-identity dependency whose change has not reached the root base is
that repository's **publication anchor**: `drop` refuses to remove the last
unresolved one, so the stack always keeps something to publish onto. There is no
`stack_bases` field on the adopted schema; the anchor is derived from the graph on
each pass rather than recorded. Every edit is validated against the live graph
before it is accepted, so bad edits and human references fail loudly and
synchronously.

Publication closeout is executed by the engine, not the planner. The resulting
branch, PR, gate, and publication-checkout state reach the planner over the live
channel; the planner accepts completion only after reviewing that evidence.

Every run is recorded, and the ledger is flat:

```
runs/<run-id>/launch.json    who owns the run, and what to relaunch it with
runs/<run-id>/plan.json      the plan as launched, preserved exactly
runs/<run-id>/events.jsonl   the authoritative merged three-stream journal
runs/<run-id>/result.json    rewritten whenever a driver closes out
runs/<run-id>/summary.json   the row a listing renders, folded as the journal is written
runs/<run-id>/checkpoint.json where a reader resumes a fold from instead of replaying
runs/<run-id>/maintenance.json the marker a live driver's pool-maintenance sweep holds while it runs
runs/<run-id>/owner.lock     the single-writer ownership lock
runs/<run-id>/driver.log     a detached driver's own output
runs/<run-id>/channel/       the planner channel's transport state
runs/<run-id>/dispatches/    per-dispatch records
runs/<run-id>/reports/       per-node raw reports
runs/<run-id>/watchers/      one record per process watching this run
```

`launch.json` is what a run is *discovered* by, so a runs root without one is
invisible to every read verb. `result.json` appears when a driver closes out, which
is why a run still working commonly has none. `summary.json` is the one derived
record here, and it is what a **listing** reads instead of the journal: the only
constructor for a run folds that run's whole merged event store into memory, so a
listing that built one per run root read every byte every run had ever recorded, and
asking this host for one row cost more than asking it for fifty. The journal's own
writer folds each appended record into it as it appends, which buys the two properties
nothing else would — it is current for a run that is *still recording*, which is the
run a listing most needs to be right about, and it costs O(1) per record rather than a
pass. A run recorded before the document existed, or one whose summary is stale against
the journal's length or mtime, is folded once and cached instead; neither path answers
differently, because the same derivation runs over both.

`checkpoint.json` is the second derived record, and it answers the same problem one
level down: a reader that needs a run's *state* rather than a listing's row folded the
whole journal to get it, which on the journals this host really holds is seventeen to
twenty-six seconds a read — and the blocking watch verb re-reads that state on every
tick, so an unbounded wait built on it would be a fresh instance of the silence that
verb exists to end. A reader resumes its fold from the checkpoint and folds only what
was appended after it. Both derived records are the run's own and are thrown away with
it, and neither is a source: delete either and the next read folds the journal and
writes it again, which is why nothing here treats one as evidence about a run.

`watchers/` holds one record per process that has armed a blocking watch on the run —
its pid, the host that pid means anything on, and the kernel's own start token for it —
and it is what `onepipeline unwatched` decides from. It is neither derived nor
authoritative in the way the two above are: each record is written by the watch that
armed it and removed on that watch's clean exit, and the deaths that matter have no
clean exit, so a record being there is a claim to be **decided against this host now**
rather than a fact. Every way of failing to decide one — another host, another run, a
pid this host would not describe, a record this build cannot read — answers *not
watched*, which is the opposite of how a driver's own liveness resolves an unknown and
is deliberate: being wrong about a watch costs one re-armed watch, where being wrong
about a driver costs live work. Removing a record needs the stronger evidence, which is
why a watch whose start this host will not report keeps its record while reading
unwatched.

The plan mapping is preserved exactly and the result is the command's JSON payload.
Journal appends and result writes are atomic, and a second process cannot drive the
same run. If a driver died, inspect its recorded worktrees and then use `just
orchestrate --adopt <run-id>`; recovery is explicit and never silently overwrites a
result. `just runs` and `just status` report a run whose recorded driver no longer
exists as `DRIVER DEAD` rather than as in flight, so a lifecycle node that lost its
executor is visibly waiting for that recovery rather than looking like work in
progress. A fresh unique run id comes from the plan's top-level `name` or filename;
`ONEPIPELINE_RUNS_DIR` moves the ledger. There is no unrecorded mode, which is what
keeps a running dispatch inside the ledger and the views built on it.

Send decisions and human attestations over the live channel whenever the evidence
arrives; nothing has to be waited for:

```
just runs
just channel-reply <run-id> <<'JSON'
{"version":2,"commands":[{"op":"attest","ref":"<node-id>/<step-id>"}]}
JSON
```

A human completion is accepted only when the graph records that exact ready
action; unknown, blocked, agent, and already completed references are refused.
Each accepted attestation is journalled as a `human-attested` operation, and the
reconciler clears the decision it was blocking on the same pass. There is no
derivation command to run afterwards: `just replan` exits saying so and names
`just channel-reply`.

The graph result state is `complete`, `waiting`, or `failed`; `ok` is true only
for complete. Node states are `done`, `waiting`, `blocked`, `failed`, or `skipped`.
Waiting output includes action prose and direct `unblocks`; blocked nodes include
transitive `blocked_by`. Failure takes precedence over waiting. Exit status is 0
only for complete, 1 for waiting or failed, and 2 for invalid input.

Use `just status [RUN]` for the joined operational view: what is driving the run,
and what is running under it. Named, it covers one run; omitted, it covers every
one. There is no count argument and no finished-session window — a run whose work
has settled is read with `just results` and `just telemetry`.

## Which verb lands which branch state

Every branch this harness can leave behind has a `onevcs` verb that lands it. That
is the whole point of routing version control through `onevcs`: an agent that finds
its documented path missing improvises with raw `git` or `gh`, and improvised
publication is how a change reaches a base branch with nothing having ruled on it. So
the table is exhaustive by construction — **no branch state here requires raw `git` or
`gh`** — and each row's condition is the one its verb enforces, read out of the
adopted `onevcs` rather than restated from memory.

| The branch is… | Verb | What it does |
| --- | --- | --- |
| held by a live session, work finished | `onevcs publish` (the lifecycle's own closeout) | Verifies the session's work and publishes it under its policy |
| complete, unpublished, no session holds it | [`just publish-branch <branch> --repo <checkout>`](#complete-branch-after-publication-failure) | Verifies it and publishes it under the policy the rules resolve — for a `local-direct` identity that is one squash on the base, pushed |
| carrying unattested incomplete provenance | [`just repo-recover <branch> --repo <checkout>`](#what-the-base-branch-carries-for-a-recovered-incomplete-step) | Attests the incomplete-step marker, verifies, and publishes |
| complete, and the identity's **resolved publication policy** is `local-direct` — `onevcs rules check` answers it, whatever the origin's host | [`just integrate <branch>… --push`](#integrating-completed-workstreams) | Merge train: merges the named branches into the base locally, in order, then pushes; refused by name for the three change-request policies |
| any unlanded state, and you want it **kept** rather than landed | `onevcs preserve --repo <checkout> <branch>` — what `just shutdown` runs for every branch its runs name | Pushes the branch to its identity's origin under its own name and lands nothing: no change request, no merge path, no base touched, no force-push. The branch stays on whichever row above it was on, now also on its origin |

That last row's condition is stated as the policy because the verb now gates on
nothing else. Through onevcs 0.19.3 it gated on two stored identity fields that
`register` inferred from whether the origin had a host — every `github.com` identity
was recorded `remote` / `team`, and the train refused it as `(repo_type: team)` — so
the row read *"the identity publishes `local-direct`"*, this repository's own identity
met it exactly, and the verb refused it. onevcs 0.21.0
(https://github.com/nickderobertis/onevcs/pull/136) removed the two fields and reads
the rules, which is what
`tests/e2e/test_integrate_local_direct_e2e.py` drives: a hosted scratch identity whose
rules resolve `local-direct`, landed by the train through the recipe.

`just recoverable` is how you find out which row a branch is on: it lists every
preserved unpublished branch, why its workstream stopped, whether it carries an
incomplete-step marker, and the exact command that lands it. Reach for it before
diffing clones by hand. **Read its `Resume:` line as naming two of the three landing
verbs, never the third**: it offers `just repo-recover` for a branch carrying
incomplete provenance and `just publish-branch` for every other unlanded one, whatever
policy the identity resolves, and it never composes an `integrate`. That is not a gap
in the table — `publish-branch` lands a complete `local-direct` branch as the row above
says — and it is why the claim of exhaustiveness is about the *table* rather than the
handoff: the merge train is reached from here, or from `repo-recover`'s own refusal of
a branch that carries no incomplete marker, which names it for a `local-direct`
identity. Reach for the train when several finished branches must land on one base in
an order, and for `publish-branch` when there is one.

`just unpublished` asks the same question from the other end: not *which row is this
branch on* but *what is this host still holding, and what is it costing*. It answers
for every registered identity under `--host`, from any directory — the gap that
matters, because `just recoverable` run inside a checkout answers for that identity
alone — for named `onevcs` sessions under
repeated `--session <s-token>`, and by default for the sessions your own runs opened,
found by the `launcher` label the engine stamps on each (`--own`, or `--session <manager
session id>` for another manager's). A session opened before the labelling engine was
adopted carries none, so that target never reaches what it preserved and nothing
backfills it, while `--host` and an explicit `--session <s-token>` still do. A row's `--json` keys are `identity`, `branch`, `base`, `provenance`, `landed`,
`change_url`, `stopped_because`, `session`, `run`, `node`, `manager_session`,
`resume_command` (the landing command in its `just` form), `in_flight`, `counted`,
`acknowledgement` and `disk` — the run root's bytes and each build-output directory under
its worktree on its own, which is the reason to reach for it, because the number a person
acts on is the disk rather than the branch count. It reclaims nothing, lands nothing and
closes nothing; the verbs above stay where they are. `--acknowledge <branch> --reason
"<why>"` drops a branch out of the count without landing it — acknowledging is never
landing.

Every row of that table reads the branch from the identity's **publication
checkout**, never from wherever a session happens to be working. So a branch that
exists only in a session worktree or a per-run clone is on no row yet: each verb
refuses it as being *in none of the checkouts* of its identity, and an agent
refused there is one step from `git push`. `just import-branch <branch> --repo
<checkout>` is what puts it on a row — it makes the branch reachable from the
identity's registered checkouts, taking `--from <source>` to name the worktree or
clone that has it (omitted, everywhere this identity keeps work is searched) and
`--as <name>` when the original name is spent. Import first, then land it with the
row's own verb.

`just work-status <ref>` answers the question the landing verbs raise afterwards:
what became of one piece of work, given a change request's URL, a session token, a
branch name, or a commit — read in that order, and refused by name, listing all
four, when the reference names nothing this host knows. It is no longer the **only**
re-read there is, and that is what the adopted `onepipeline` changed: a run recorded a
node's landing as its own settlement observed it and nothing looked again, so `just
results` and `just status` said *as of settlement* and meant it, and a change that
merged an hour later still read there as not landed — which had an adoption node
dispatched three times against work already on its base. From onepipeline 0.22.0 the
node line, the summary count and the `status` line each take a fresh landing read when
they render, for every node they report on except one the run already recorded as
landed, and a read that cannot decide is reported **undecided** rather than as not
landed. What this verb still owns is one piece of work asked about by name, with the
evidence behind the answer; ask it when the question is about a branch rather than a
run, and read `--json` when something other than a person is going to act on it.

#### What `decided by:` can reach, per workflow

The verb answers `landed:` from four tiers in order, and names the one that decided in
`decided by:`. Three of them are records — `a recorded landing`, `the change request's
number in the base`, `a landing trailer on the base` — and the fourth, `content
comparison`, is a comparison of the paths the branch touched against the base, which is
why it may answer `no` or `unknown` and never `yes`. **Which of the three records a
landing can have is fixed by the workflow that published it**, because `onevcs` can only
stamp the commit it writes itself:

| Workflow | What carries the landing | Where it lives | What is left when the record is gone |
| --- | --- | --- | --- |
| `local-direct` | the base's squash commit, carrying `Orchestrator-Landed-Commit: <the branch's tip>` | the repository's own history | the trailer, so `landed: yes` still |
| `change-open` / `change-auto` / `change-direct` | a `chore: record the landing of <branch>` commit on the **branch**, plus the session record and the change request it names | the branch, and `$ONEVCS_HOME` | nothing the tiers read — `landed: unknown`, `decided by: content comparison` |

The branch-side trailer a remote landing writes is never read back, because the third
tier looks at the base; and the second tier looks for a change request `onevcs` has a
record of, so the `(#51)` a GitHub squash puts in the base subject does not answer it on
its own. The practical consequence is one asymmetry to carry: a **remote** branch that
merged, asked on a host whose session record for it is gone, answers `landed: unknown`
by `content comparison` and comes back into `just recoverable` under `— may have landed`
with a `publish-branch` command beside it. Read that as *no record*, confirm against the
change request, and do not run the command. A `local-direct` branch in the same state
still answers from its trailer. The tiers and their order are `crates/onevcs/src/landed.rs`
at the tag `config/onevcs.version` names, which is the one source of this table, and
`tests/e2e/test_work_status_and_import_e2e.py` is the gate that holds the table to it:
it lands one branch through `publish-branch` and one the way a host squashes — a base
commit carrying the number in its subject and no trailer — then asks both through a
registry holding no session record, and asks the stamped one again under another
`trailer_prefix`, where the trailer tier finds nothing and the answer falls to the
comparison.

The distinction that matters most is the middle two. `repo-recover` is the only
verb that knows how to attest an incomplete-step marker, so a branch carrying one
must never be finished off with an ordinary commit — see [what the base branch
carries for a recovered incomplete
step](#what-the-base-branch-carries-for-a-recovered-incomplete-step). And
`publish-branch` is not `integrate`: `integrate` merges into the base locally and
opens no change request, while `publish-branch` publishes under whatever policy the
identity's rule resolves, which for a team identity means opening the PR.

## Integrating completed workstreams

For a repository whose identity's rules resolve `publication: local-direct`, `just
integrate` runs a merge train without letting one failure block the others:

```sh
just integrate claude/api claude/docs --push
```

The branches are named, always: `onevcs integrate` takes a required
`<BRANCHES>...`, so omitting them is a usage error rather than a train over
everything outstanding. `just recoverable` is where that discovery lives — it
lists every preserved unpublished branch and the command that lands each one, and
its output is what feeds this argument list.

Before any mutation, integration resolves the checkout it is run inside back to its
canonical registry entry — it must be run from within a registered checkout of the
identity. An identity whose policy is any of the three change-request ones, and an
unregistered repository, reject integration and `--push` with exit 2, naming the
policy; there is no routine bypass. A hosted origin is not a refusal on its own any
more: through onevcs 0.19.3 it was, because the verb read the stored `repo_type` and
`workflow` that `register` inferred from the host, and this repository's own identity
— hosted, and `local-direct` by its rules — was refused the one verb this document
named for it.

Each permitted candidate fetches the selected remote and merges current
`<remote>/<base>` (then earlier train candidates) in its own worktree, under the
comparison identity `onevcs` exports, so the candidate and the publishing push are
resolved against the same diff. **The train runs no verifier of its own** — onevcs
0.11.0 left it none to run — so what judges the work is the `pre-push` hook at the
single push, and the train says so before it starts: when
`store::merge_path_coverage` answers `Coverage::None` it warns, in the same words
`onevcs register` uses, that what it is about to land is unproven. It warns
*before* rather than after, because an operator who learns afterwards that nothing
will judge what the train landed has already landed it. A permitted branch
fast-forwards the local base. Conflicts and per-candidate refusals are reported as
skips. `--push` updates the
remote only when the base advanced. The base and candidate worktrees must be
clean.

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

A publication its merge path refused does not publish the work, and does not discard
it either. The branch outlives the run: `PublishOutcome::Failed` carries a `retained`
naming whether it was `handed-back` to a registered checkout of the identity or
`refused` by it, and the node's settlement names the branch. An operator inspects it
there, or lands it by name, without reaching into run scratch. Because the dispatch
itself completed, this preservation adds no incomplete provenance; a genuinely
stopped dispatch is what leaves the marker.

`onevcs recover` is what verifies and publishes a branch that *does* carry an
unattested marker, and it refuses everything else by name:

- **A branch with no unattested marker** is refused with the verb it actually needs —
  `integrate` for an identity whose rules resolve `local-direct`, `publish-branch`
  otherwise. Through onevcs 0.19.3 this read the stored `repo_type` and `workflow`
  instead, and sent every hosted identity to `publish-branch`.
- **A marker written under a prefix this host is not configured with** is refused as
  unreadable rather than read or ignored; `trailer_prefix` in the rules file is the
  one source of that spelling.
- **An identity that "names no complete bar and nothing on its merge path verifies
  one"** is refused before anything is written: `attests_nothing` fires when the
  *stored* identity gate is `<no-op>` **and** `store::merge_path_coverage` answers
  `Coverage::None`. It reads no rules file — the gate it used to consult there is
  gone — and its message names both ways to give the identity a bar: an executable
  `pre-push` hook in the source checkout, or a host whose required checks judge its
  change requests.
- **A branch whose subjects will not compose a publication subject** is refused
  before the attestation is written, so `--title` is offered on the branch as the
  operator left it.

Only then does it sync the change base, write the attestation, emit
`recovery-attested`, and publish through the *same* landing path everything else
uses — the same rules-resolved policy and the same merge path. It takes no option that
overrides that policy: `just repo-recover` forwards `--repo` and `--title` (plus the
`--body-file` this repository drafts for it), and there is no base or type to pass.
Recovery cuts its own run root under `~/.onevcs/workspaces/`, with a clone sharing
the source checkout's objects and a worktree of its own; the publication checkout is
still never worked in.

**A `resume` the planner names is authoritative.** A `retry` that states one, through
`just channel-reply`, is answering "continue *this* work". `validate_retry_pin`
refuses a replacement whose `branch` pin and `resume.branch` disagree rather than
resolving it silently, and `pin_retry_branch` fills the pin in from the resume where
only the resume was given. `inherit_preserved_branch` carries the superseded node's
branch and resume onto the replacement **only when the replacement names neither** —
so to deliberately discard a preserved attempt and start fresh, set `branch` to a
*different* valid branch name in the retry edit. The opt-out belongs on `branch`
because it is already the plan's authoritative branch-routing field. Naming the
preserved branch itself is not an opt-out in any useful sense: the dispatch lands on
those commits either way, and all that is lost is the record of the continuation and
the completed steps it carried.

What this document used to describe here, and what is **not** on the adopted stack:
there is no `not-completed` and no `resume-failed` outcome, no `branch-discovered`
event, no `resumed` / `resumed_from` / `resume_declined` / `retry_lineage` fields, no
provisional incomplete-provenance marker that a later attempt removes, and no bounded
conflict-resolution cycle that a recovery reports as its own outcome. A node that
stopped short reads `task-failed`; there is no separate word for the turn cap. A pinned branch
a dispatch cannot adopt fails as whatever `onevcs` says went wrong, reaching the node
as `publication-failed` or `task-failed` with that reason as its detail. **How a
dispatch behaves when its pinned branch is unusable is not stated here, because
nothing in either engine's source states it** — do not infer a fallback either way;
read `just work-status <branch>`, which answers where the work is now.

To continue authoring after a lifecycle node hits its turn cap, do not relaunch
the original plan. While supervising its existing `orchestrate` run, send a
`retry` live edit through `just channel-reply` to replace only the capped node.
Give the replacement a new id, copy the original node (including
routing fields such as `execution_checkout`, dependencies, or `steps`), and set
the larger `max_turns`. The replacement inherits the capped node's preserved branch
and its completed steps from the pin the run's own journal already carries, and
continues authoring on that branch. `repo-recover` is
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
branch's own history keeps the remaining steps — all but one kind of line. Since
https://github.com/nickderobertis/onevcs/pull/163, which the adopted engine links, a
`local-direct` squash's message carries, between the subject and the
trailers, every issue-closing reference the branch's commits made — `close`, `fix` and
`resolve` in any inflection and case, followed by `#N`, `owner/name#N` or an issue URL,
a github.com URL written back as `owner/name#N` — each issue once, as `<Keyword>
<reference>`, in the order the commits first named it. GitHub closes an issue only from
the commit that reaches the default branch, so the squash that dropped those lines
left every issue a landing delivered open until a person closed it.
`tests/e2e/test_publish_branch_e2e.py` lands such a branch through `just
publish-branch` and reads the lines off the origin's base. Its type and breaking marker still
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

`repo-recover` applies only to a branch with unattested incomplete provenance. It
correctly rejects a complete branch, and hands over to a verb that does publish one
rather than only refusing:

```text
onevcs: invalid input: branch "<branch>" carries no unattested incomplete
provenance: it has commits ahead of <base>, and all of them are complete.
`recover` publishes interrupted work; publish a completed branch with
`onevcs integrate <branch>`
```

The refusal names the **local merge train**, which is the right answer for an
identity that publishes `local-direct`. For an identity that publishes through a
change request, `just publish-branch <branch> --repo <checkout>` is the one to
reach for instead: it verifies the branch and publishes it under whatever policy
the rules resolve, where `integrate` only merges into the base locally. Neither is
raw `git`, which is the point — see [which verb lands which branch
state](#which-verb-lands-which-branch-state).

`just integrate` names `repo-recover` symmetrically, with the exact command, when
it skips a candidate for incomplete provenance. A recovery whose publishing push a
`pre-push` hook judges preserves what that hook wrote under its own run root's
[`gate-logs/`](#where-a-merge-path-verdict-is-preserved), named as `preserved_log`
on the `push` event, so consecutive attempts on one branch are comparable instead of
reading alike. An identity verified by the host's required checks preserves no log,
because nothing local produced one: those checks decide after the change request
exists, and `EventKind::ChangeCheck` events are where they are read.

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
and run its complete gate with the comparison remote and base explicit. Then land
the existing commits through the registered workflow: `just publish-branch <branch>
--repo <checkout>` verifies and publishes under the policy the rules resolve, and
for a `local-direct` identity `just integrate <branch> --push` re-verifies,
fast-forwards, and pushes them. The push still runs the complete gate. Neither verb
takes a base or a remote — the comparison identity travels in the environment
(`ONEVCS_COMPARISON_REMOTE` / `ONEVCS_COMPARISON_BASE`), which is what the pre-push
hook reads.
Do not invent incomplete provenance to use `repo-recover`, and do not redispatch
an agent to re-author identical content: it adds startup cost without improving
the result.

## Operating across workers and machines

- **Several agents in one process:** the engine's continuous reconciler owns
  concurrency.
  Per-repo in-process locks serialize short canonical-checkout operations; agent
  dispatches in separate worktrees remain concurrent.
- **Several processes on one machine:** each run works in its own clone, so the
  only shared state left is the registry, the ledger claim, and short mutations of
  the execution/publication checkouts. OS advisory locks serialize those, queueing
  contenders rather than racing them; automated single-owner merges use the FIFO
  queue above. Locks and queue state live under `$ONEVCS_HOME/locks` (normally
  `~/.onevcs/locks`) and protect only that machine — the same root holds
  `registry.json`, `rules.yml`, `sessions/`, `streams/`, `artifacts/`, and the
  per-run `workspaces/`. `ONEVCS_LOCK_TIMEOUT_SECONDS` bounds a queued wait, and
  on this host nothing has to set it by hand any more: a legitimate turn here *is*
  a gate inside a merge — a `local-direct` publication runs the complete pre-push
  gate under the lock — so `scripts/lock-timeout.sh` derives the bound from how
  long that gate last took and every waiter's `onevcs` is started with it. The
  gate records its own duration at the pre-push hook, host-locally under
  `${XDG_STATE_HOME:-$HOME/.local/state}/ai-orchestrator/`; the bound is that
  duration times the queue depth this host runs a plan at, plus a fixed margin, and
  never below `onevcs`'s own default. A positive number of seconds you export
  yourself is kept, so raising it by hand still works. On timeout, inspect the reported PID and host
  rather than deleting a live lock or worktree.
- **Several machines, remote-first:** GitHub is the remote coordinator. Local
  locks and ledgers are independent; unique branches, PR state, required checks,
  and merge/auto-merge coordinate publication globally.
- **Several machines, local-first:** there is no cross-machine lock. Publication
  is optimistic: fetch, rebuild and re-verify the merge, then attempt a non-force
  base update. A loser fetches the winner and retries up to the configured limit.
  A non-bare local origin must set `receive.denyCurrentBranch=updateInstead`;
  otherwise use a bare origin.

The ledger is machine-local, not a global liveness source. On the launching
machine use `just runs`, `just status`, and `just history` /
`just history-show <id>`. The recorded branch and worktree are the recovery source
of truth. For an interrupted
run, inspect its worktrees and remote branch, then attach a fresh driver with
`just orchestrate --adopt <run-id>`. Recovery may reclaim a `running` record, so
use it only after proving its owner is gone; never remove or reset an active
worktree.

Switch publication workflow only between runs, and only by editing the rule that
matches the repository — one rule resolves the policy for every alias of the
normalized identity. First finish or recover
active publication, fetch, and confirm the canonical checkout is clean and
fast-forwarded. Before switching to `remote`, configure GitHub permissions,
branch protection, and required checks. Before switching to `local`, confirm all
machines can reach the origin and it accepts safe base updates. Do not switch
metadata to bypass an in-flight PR or failed gate; close or recover that run under
its original workflow.

Switch identity type only between runs, by editing the rule that matches the
repository; a team repository publishes remotely. Finish or recover active
publication first.

There is no identity gate to switch. onevcs 0.11.0 removed the rules gate, so what
verifies a change is the repository's own merge path and changing that means changing
the repository — its `pre-push` hook, or its branch protection — rather than anything
here. The registry's own detected `gate` column is a description of a checkout, not a
policy; re-register the checkout to move it.

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

The lifecycle's own suite is `onepipeline`'s, not this checkout's — the
implementation moved to that crate and its journeys went with it. What is worth
knowing here is where it draws its seams, because the same two are the only things
this repository doubles either.

Git is real everywhere. Two things the offline gate cannot run for free are stood
in for, each at its own seam: the **paid harness**, and the **GitHub host** behind
`onevcs`. `onepipeline`'s `crates/testfakes` holds both — a double for
`oneagentgraph`, and one for the `gh` the host calls — and the crate's own comment
on it is the discipline worth copying: the `oneagentgraph` double answers out of the
real crate's own types and refuses what the real CLI refuses, because a double that
accepted more than the sibling is an oracle for a build nothing runs. `onevcs` is
*called* rather than spawned, so nothing stands in for it, and
`tests/e2e/real_vcs.rs` drives the real one.

Read those journeys in `onepipeline`'s own `tests/e2e/` — `lifecycle.rs`,
`real_vcs.rs`, `session.rs`, `session_reuse.rs`, `boundary.rs`, `cancellation.rs`,
`live_edit.rs` and the rest — rather than from a list restated here, which is
exactly how the list that used to be here came to name journeys that no longer
exist.

**The cost analysis that used to follow this section has been removed rather than
corrected.** It measured a Python lifecycle implementation that no longer exists —
`run_repo_task`, `MAX_AUTOMATIC_STEP_RESUMES`, `terminate_process_group`, and every
journey it named are absent from `onepipeline` v0.45.0 — so every number in it was a
measurement of something else. The one part of it that still holds is the shape:
**read a journey's price as its number of dispatches times the price of one**, since
the clone, the worktree, the commit and the push are not the cost and never were.
Any figure beyond that has to be re-measured against the crate that runs it.
