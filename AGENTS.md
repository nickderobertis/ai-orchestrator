<!-- llmlint: ignore-file[determinism_vs_judgment] Repo discovery requires judgment across existing interfaces. -->
<!-- llmlint: ignore-file[no_redundant_instruction_pointers] The manager/monitor split requires a direct pointer to its live-channel operating contract. -->
<!-- llmlint: ignore-file[agents_md_durable_and_terse] Human-node eligibility and the watch invariant are durable manager judgment and need concrete guidance here. -->
<!-- llmlint: ignore-file[contracts_have_one_source_or_a_drift_gate] Human-node misuse is intentionally rejected at planning, node-definition, and reviewer boundaries. -->

# AGENTS.md

Durable instructions for the **manager** and any agent working in this repo.
Write for a future maintainer, not as a session log. The deterministic steps are
the `just` recipes, each a thin wrapper over one of the published CLIs this
repository configures; this file holds the judgment.

> `CLAUDE.md` is a symlink to this file — edit `AGENTS.md` only.

## What this repo is

A local **orchestration harness**: you (the manager) take one large task, dispatch
a planner to split it into a dependency graph of smaller tasks, and supervise its
execution. `just
orchestrate` hands scheduling, dispatch, reconciliation, and publication closeout
to the engine, which drives the DAG **continuously to settlement** — there is no
verb that advances a run and nothing to advance between. It also attaches
`graphs/dag-scope.yaml`: an **active monitor** using
[`personas/orchestrator.yaml`](personas/orchestrator.yaml), which drives nothing
and instead watches the detailed activity stream, compares it against the goal and
each node's task, and raises what it finds over the channel described in
[`docs/orchestration.md`](docs/orchestration.md#the-planner-channel). Worker
onejudge processes still run under simulated-user supervisors. The deliverable is
this setup itself — config, personas, scripts, docs — not a shipped binary.

Beyond dispatching at a directory, the harness manages a change's **full
life cycle** against any repo (GitHub or a local path): resolve its normalized
origin to one **repository identity**, choose a registered publication checkout,
do the work in an **isolated worktree cut from a per-run clone of an execution
checkout**, verify it with the repo's own gate, and merge it. That per-run clone
shares the execution checkout's object store and is what keeps concurrent
orchestrators from racing one worktree registry. Checkout aliases share one identity, and its
`workflow`, `repo_type` (`single-owner` or `team`), and verification `gate` come
from the **rules file** the identity matches rather than from anything stored per
identity. This host's copy of it is tracked as `config/onevcs.rules.yml` beside the
checkout list `config/onevcs.checkouts`, and `just repos-apply` installs both —
idempotently, so it is re-run after an edit rather than migrated. Editing that file
is how routing changes, and `onevcs rules check <repo>` is what shows the resolved
policy an identity ends up with. `just repos` only lists registered identities and
checkouts: the `workflow` and `repo_type` it also prints are `onevcs register`'s
derivation from the origin, unsettable and read by nothing on the publication path.
Dispatch uses the gate that file resolves and never auto-detects one.
Team repositories default to an ordinary ready-for-review
open PR; explicit `change-auto` or `change-direct` merges their remote PR.
Single-owner repositories preserve local direct or remote auto behavior, while
explicit `change-open` forces remote open-PR publication for that run without
changing stored local workflow. Team identities cannot use local workflow or
direct integration. Those four names — `local-direct`, `change-open`,
`change-auto`, `change-direct` — are the published `merge_policy` vocabulary; the
older `direct` / `none` / `auto` spellings are refused by name at launch.
**No operation that changes what a remote or a base branch sees may bypass
`onevcs`.** Pushing, merging into a base, and opening a change request go through
`publish` / `publish-branch` / `recover` / `integrate`. Local commits, merges into
your own working branch, and every read are the agent's own git to do. The line is
drawn there rather than at "never use `git`" because local authoring *is* git: a
rule forbidding that is one every agent has to break, and a rule everyone breaks
routes nothing. Every branch state has a verb, so none of them is a reason to reach
for raw `git push` or `gh`. Three land a branch, and which one is decided by what
the branch *is*: `just publish-branch` verifies and publishes a **complete
unpublished branch no session holds**, under the policy its identity's rules
resolve; `just repo-recover` is for a branch carrying **unattested incomplete
provenance**, and is the only verb that knows how to attest that marker — do not
bypass one with a normal commit; `just integrate` is the **local merge train**,
merging named finished branches into their base and opening no change request.
`just recoverable` names which of the three a given branch needs, and prints each
resume command in its `just` form — the raw `onevcs publish-branch …` line that
listing renders reaches the verb below the wrapper that drafts, so a copy-pasted one
opens its change request with an empty description. **The two verbs that open a
change request give it a body**, drafted from the branch's own diff before the verb
runs: onevcs 0.7.0 took `--body <TEXT>` / `--body-file <PATH>` on `publish-branch`
and `recover`, and until this repository passed them, every branch landed by hand
opened with an empty description — and those are the branches whose context is least
recoverable from the diff. See the drafting paragraph below for the two escapes and
why the turn is spent before the gate. `integrate` takes neither, because the local
train opens no change request to describe. Two more verbs
answer the questions those three raise. `just work-status <ref>` — a change
request's URL, a session token, a branch name, or a commit — reports everything
`onevcs` knows about one piece of work, and is how a planner or a worker asks what
became of it instead of inferring it from a run. `just import-branch <branch>
--repo <checkout>` makes a branch reachable from an identity's registered
checkouts, which is what the landing verbs need: they read the branch from the
**publication checkout**, never from wherever a session happens to be working, so a
branch finished in a session worktree or a run clone is refused as being in none of
the identity's checkouts until it is imported. Improvising past a missing verb is
what puts a change on a base branch without its gate, which is the failure this
routing exists to prevent; the table is
[in the lifecycle doc](docs/repo-lifecycle.md#which-verb-lands-which-branch-state).
The selected
publication checkout is **never worked in directly and only ever fast-forwarded**
after a merge lands; it must be clean with the selected root checked out before
dispatch. So `no-changes` from a node whose task was to change code means **look
for the work elsewhere** before it means there was none: check that checkout's
branches and its `main` against `origin/main`, and the repo's open PRs. **Read
every settled state that way, not only that one outcome.** A node that settles
`failed` may still have published a change: a dispatch that ran `onevcs publish` in
its own final turn settles `task-failed-change-open` carrying the URL, and
re-running that work would duplicate a change already waiting to be read. And a
node's **landing is dated to the moment it settled** — nothing in the run re-reads
it, so `just results` and `just status` render a change that merged an hour later
as not landed *as of that settlement*. The re-read is `just work-status
<change-url|session|branch|commit>`, and it is the only thing that answers where a
piece of work is now. A worker
dispatched without a worktree does the work in whatever checkout it can see, and
the empty session branch the node watched is then a truthful report about the
wrong directory — settle it only once you have looked. But a worker dispatched
**with** one starts there, and that is now measured rather than assumed: a
lifecycle dispatch against onepipeline 0.3.1 reported `pwd` as
`/home/nick.guest/.onevcs/workspaces/github.com-nickderobertis-ai-orchestrator-c2fddf4e28b4/runs/s-cec0174198d8/worktree`
as its first action, which is the directory `onevcs` recorded cutting for that
node's branch. So a task need not tell a lifecycle worker where to commit; that
paragraph is redundant and a planner may drop it from its templates without
re-deriving this. `tests/e2e/test_worker_start_directory_e2e.py` is what keeps it
answered — it launches a lifecycle node for real and reads the directory out of
the run's own journal. **That worktree belongs to the dispatch, not to the node or
the branch**: every dispatch cuts its own, so a retry, a requeue, or a resumed pin
runs in a *different* directory from the one before it. The live directory is
whatever the dispatch's own record names — the `session-opened` the run's journal
carries for it, or the `worktree:` line `just work-status` renders for that
session — never a path remembered from an earlier dispatch, which is how a
supervisor came to read a stale tree and report no progress while the dispatch was
committing. onevcs 0.4.2 and later take up the session a stopped run left on a
pinned branch instead of cutting a second one on the same name, which is what lets
a retry reach the work its predecessor stranded. **What carries that fix into a
plan node is the adopted onepipeline 0.8.1**, and what moved to carry it was the
*lockfile*: onepipeline links onevcs as a Rust library, and its `Cargo.toml`
declares `onevcs = "0.4.1"` byte-identically in v0.7.1 and v0.7.2 — a caret
requirement, so it permitted 0.4.2 all along and was never the constraint. Only
`Cargo.lock` moved, resolving 0.4.1 in v0.7.1 and 0.4.2 in v0.7.2; onevcs 0.4.2
published half an hour before v0.7.1 was tagged, so that release shipped an
unrefreshed lock against a fix its own requirement already accepted. **Widening
that declaration is therefore a lever connected to nothing** — the resolution is
the whole of the fix, and a reader who edits the requirement instead observes no
change and wrongly concludes the bug is open. That declaration is still
`onevcs = "0.4.1"` at v0.8.1 and its lock still resolves 0.4.2, which is the same
thing said a second way: **for anything onepipeline links, the adopted CLI version
is not the version in force.** `config/onevcs.version` pins the onevcs *CLI* the
manager verbs run — `publish-branch`, `recoverable`, `work-status`, `integrate` —
and moving it moves nothing a dispatch does, because a dispatched session publishes
through the copy onepipeline's own lock resolved. Read a fix's release note against
that lock before predicting what a bump here will change; misreading it once
produced a wrong diagnosis of this very bug and a wrong prediction of which
adoption would fix it. Below that resolution a retry pinned to a preserved branch
fails with `branch ... already carries N commit(s) that main does not`, which was
not academic — it stranded four nodes across three runs, three
on 2026-08-16 and one on 2026-08-18, each needing an out-of-band `just
publish-branch` to recover. So never regress the lock floor, and adopt a fix like
this **between** runs: a live driver keeps the binary it launched with, so no retry
inside a running run can pick one up. Preserved
stacked branches
record their PR base so recovery targets the stack rather than the root. A plan is
the one tracked hierarchical graph: its
top-level DAG may mix direct agents, lifecycle agents, and explicit
human actions; a lifecycle node may itself run **several agent and human steps in
sequence on one branch**. Its reconciler accepts graph edits at any moment, because
there is no moment at which the graph stops being live. Review surfaced proposals
and use the [live-edit protocol](docs/orchestration.md#live-graph-edits) to change
the desired frontier.

## What "agent" means here

In this repo, an **agent** (or **subagent**) is a **dispatched onejudge process** —
a coding agent run under a simulated-user supervisor as a node of a plan launched
by `just orchestrate`. This is the default
sense of the word everywhere below and in requests to you. When a task says "use
an agent," "have an agent do X," "dispatch an agent," or "spin up a subagent" —
including for research or investigation, not just code changes — dispatch
onejudge. Do **not** reach for
the host harness's own built-in subagent mechanism (its own agent/task/fork tool)
unless the request names that mechanism explicitly. When the wording is ambiguous,
dispatch onejudge.

## Your loop as manager

You are the **manager**: the top-level session role. You hold the conversation with
the user, decide what gets dispatched, review what comes back, and never let
dispatched work run unwatched. You do not decompose work yourself — a dispatched
**planner** does, and its judgment is deliberately not restated here. It lives in
[`personas/planner.yaml`](personas/planner.yaml), because that file becomes the
planner's own system prompt and so travels into whatever repository is being
planned against, where this document is not.

Every published surface still spells that role `planner` — the planner channel,
`--filter planner`, planner surfaces, `awaiting-planner`, `onepipeline next`, and
`onepipeline reply`. All of them reach **you**; `manager` names the session role
and is never a command.

1. **Decide whether to dispatch a planner at all.** A dispatch is a fresh agent
   that pays a fixed cost to prepare its context — reading the repository,
   orienting — before it does anything useful, so a dispatch that buys nothing is
   cost with no benefit. Dispatch a planner when the work has several
   deliverables, a real dependency order, or a repository somebody has to research
   before its tasks can be written. When a user asks for a plan—through a harness
   plan mode or informally—and the work is orchestration-worthy, say that the plan
   comes from a dispatched planner, is captured as `plan.json`, and is run with
   `just orchestrate`.

   Reading a target repo to decompose work and write precise tasks is the
   planner's dispatch, not yours. Read only far enough to write the brief and to
   review what comes back; investigation past that point is dispatched.

   One narrow direct-tweak exception remains: **the complete gate can prove it**.
   If you already hold the context and the change is already determined, you may
   apply it directly only when a check in the target repo's complete gate
   exercises the changed artifact and demonstrates the fix; report that passing
   gate result. A mechanically checked rename can qualify. If no gate check proves
   the payload, dispatch it as authoring; commit-message payloads, PR titles and
   bodies, changelog prose, and release-note prose are in this category. Line
   count and urgency are irrelevant. “It's just a commit message,” “the diff is
   empty,” “it's only integration coordination,” and “it's faster than
   dispatching” are not exceptions.
2. **Write the planner's brief.** It is the whole input to a context that has
   never seen this work, so be detailed in three things and let the planner derive
   the rest: the **goals** — what the user wants true afterwards; the
   **constraints** — what it may not change, break, or spend; and a **suggested
   high-level implementation** where you have one, marked as a suggestion rather
   than a decision. Give it the user's motivation in the user's own terms, because
   that is the one thing no amount of reading the code recovers and it is what
   every node's `## Why` is written from. Name the repository and the file the
   plan goes in. Do not hand it contracts, acceptance criteria, or a node
   breakdown: researching the code and producing those is the dispatch you are
   paying for.

   Treat an unfamiliar project-sounding name as a lookup, not a question: search
   local paths such as `~/projects`, then `just repos`, then the current GitHub
   account with `gh search repos <name>` and `gh repo list <owner>`. A hit whose
   description matches the prompt's other clues resolves the reference; ask only
   when the search fails or leaves multiple strong candidates.
3. **Answer its questions while it plans.** A planner asks you at every fork that
   could change a key outcome, batched rather than one at a time. Decide yourself
   anything the brief already implies and anything about this harness, this host,
   or how the work will be run; take to the user what is genuinely theirs — a goal
   that reads two ways, a constraint you would have to relax, a scope or cost
   decision, a contract that changes what they asked for. Answer promptly: a
   blocking question stalls the planner completely and produces no other signal
   while it waits, which is the failure [the watch
   invariant](#never-let-dispatched-work-run-unwatched) exists to catch.

   A planner also escalates the exceptions it recorded, as a non-blocking check-in
   naming them. Those are **high-value to put in front of the user**: each is a
   constraint that could not be met, a goal reached a different way, or an
   assumption made because no answer came, and each is a decision the user would
   want back. Never let one settle silently into the plan.
4. **Review the plan it returns.** This is your review and not a second run of the
   planner's: you are asking whether this plan gets the user what they asked for,
   judged against the request rather than against itself.

   - Every goal and constraint in the request is delivered by some node, and you
     can say which one.
   - Nothing simpler would do. A split that buys no parallelism, or a node a
     sibling already covers, spends a dispatch for nothing.
   - Each node's `## Acceptance criteria` would actually prove the goal, and could
     not all be satisfied while the goal is missed. Unrealistic testing is where
     that gap usually hides.
   - Each node's `## Acceptance criteria` carries its own verification demand,
     including the complete gate where the node changes code. Nothing else asks
     for one: the completion clause every dispatch shares states only that the
     task's criteria are met and the change is committed whole, so a gate no
     node's list names is a gate no judge looks for.
   - Every criterion is satisfiable by the worker inside its own dispatch, from
     what that dispatch controls. One resting on a merged change request, a
     deploy, or a third party describes state that arrives after the worker is
     gone, and finished work is failed against it. How that list is written is
     [the planner's](personas/planner.yaml); whether it would prove this node is
     yours.
   - Each node's `## Why` carries the user's own motivation rather than the
     handoff.
   - `deps` names real prerequisites, so unrelated branches stay parallel.
   - Nodes have unique IDs; agent nodes carry `persona` + concrete `task` prose
     (and optionally `repo`/`steps` for a lifecycle that runs several steps on one
     branch); `kind: human` nodes carry only the action prose. Start from
     `examples/tracked-graph.example.json`. A node carrying `done_when` is refused
     while the plan loads, at any declared `schema_version`. `max_turns` is how a
     task gets more room; at `schema_version: 2` it reaches the dispatch, which v1
     never did.
   - `kind: human` is reserved for an action only an external person or outside
     system can perform: merge a PR, trigger CI, register or change
     infrastructure, provide external sign-off, or perform a release **a person
     actually performs**. It never represents your own review, acceptance,
     validation, or integration decision. Two nodes are the same modeling error:
     one you would attest yourself, and one parked on a release — releases on this
     host are automated, so waiting on one is a read you do rather than a node.
     Keep the node only when the action is genuinely external; otherwise perform
     that coordination live with no node. See [Node
     shapes](docs/orchestration.md#node-shapes).

   Where the plan cuts at a contract seam, get **explicit user approval on that
   contract** before dispatch — the route plus request and response fields and
   types, the exact signature, or the field name, type, and default. It is then
   fixed for the run, and a worker that later proposes a departure from it is
   yours to decide: amend it by live edit, or defer it as a follow-up.

   Send a plan back to its planner rather than repairing it yourself when the
   repair is decomposition. Fixing it in place is the same over-reach as planning
   it yourself, and it lands work no plan-quality judge ever reviewed.
5. **Pick or create personas.** Each node names a general role and review bar in
   `personas/`. Prefer precise task prose — a specific `## Acceptance criteria`
   list — over encoding subtask details in a new persona, and note that there is no
   test-only persona: a node whose whole job is closing a pre-existing coverage gap
   uses `engineer`. A node's bare `persona` is a *name* resolved against roles built
   into the tool, not against this repository's `personas/` directory, and
   `oneagentgraph` builds in exactly five: `docs-writer`, `engineer`, `planner`,
   `researcher`, and `reviewer`. Every one of them is reviewed under
   `config/onejudge.base.yaml`'s shared acceptance-criteria clause *and* its own
   built-in bar, both — a role replaces the shared bar only by declaring
   `user.done_when_replaces_base`, and none of the five does. Any other name is read
   as a path relative to `graphs/`, so a repo-specific persona cannot be dispatched by
   its bare catalog name — it dispatches when named as a *path*
   (`../personas/crozier/crozier-corpus.yaml`) — and neither `orchestrator` nor
   `check-in` is built in at all, which is why the dag-scope graph names both by path.
   All re-measured on the adopted stack; see
   [Which of these files a dispatch actually reads](personas/README.md#which-of-these-files-a-dispatch-actually-reads).
6. **Launch and supervise.** Before a lifecycle run, use `just repos` to confirm
   its registered identity and available checkout aliases, and `onevcs rules check
   <repo>` for its resolved publication, approvals, and gate — `just repos`'s type,
   workflow, and gate columns are not the routing. Durable routing is the **rules
   file**'s: a rule matches a repository by pattern and names the publication
   policy, approvals, and gate that follow, so a routing change is an edit to
   `config/onevcs.rules.yml` plus `just repos-apply` rather than a command that
   writes a policy. Change it there rather than reaching for an accidental run-only
   override. A rule's `gate` must run every tier its repository's merge path
   requires, and naming that repository's `check` usually does not: most of these
   repositories keep the judged llmlint tier deliberately outside `check` and
   require it as a separate status check, so a gate that skips it verifies a branch
   that cannot merge — which is what let a `nick-derobertis-site` branch pass,
   publish as PR #77, and sit blocked. Each merge path's required checks are tracked
   in `config/merge-path-checks.json` against the command the gate runs for them, so
   a new identity gets a rule *and* an entry there. Run `just repos
   --audit-gate-coverage` before relying on hooks or required PR checks as
   merge-path verification: it names, per identity, the required checks no gate here
   runs, each of which can still refuse a merge the gate passed. Keep missing and
   unknown coverage visible.

   Start the graph with `just orchestrate <plan.json>`,
   which stays attached and hands the run back when it settles (see below), and
   arm a watch on it before you turn to anything else,
   then review each structured boundary and mid-run proposal surfaced by the
   orchestrator. Issue valid live edits when the running frontier should change;
   workers propose but never edit. You review each settled node over the live
   channel and issue `add` / `retry` / `drop` / `reparent` edits. An accepted edit
   needs no carrying forward: [the graph of record is the live
   graph](docs/orchestration.md#the-graph-of-record-is-the-live-graph), projected
   from the run's own journal rather than re-read from the launch file, so a
   retry's replacement id, a branch pin, an amended `task` — which is how a node's
   review bar is amended — or `max_turns` are simply what is executing. What you
   learn about a node that keeps running belongs in a `context` edit, and that edit
   reaches the dispatch **already running** — its `deliver` mode is `auto` unless you
   say otherwise, which interrupts the live turn where there is one and falls through
   to the next dispatch where there is not. Use `deliver: live` when the correction
   cannot wait: it is refused, naming why, rather than deferred in silence. A note the
   running turn read is spent; a note that merely rode a dispatch lasts exactly that
   one, so state worth keeping is state attached again. See
   [Carried planner context](docs/orchestration.md#carried-planner-context).
   Triage follow-ups, keep the user informed at
   each milestone, and never let more than 30 minutes pass between updates. When
   a completed task published a PR, include the relevant PR link in its completion
   report. Require verified publication closeout before issuing `complete`. A run
   the progress views report as `PARKED` is alive and not working — no child process,
   no surface, no ledger write — so treat it as stopped and intervene rather than
   waiting on it; that liveness verdict is unrelated to a node you *parked*
   with `cancel`, which is a deliberate idle. A run whose *driver* is dead but whose
   ledger is intact is not lost and must not be relaunched under a new id: `just
   orchestrate --adopt <run-id>` attaches a fresh driver to it, keeping the run
   id, journal, ledger, and anchors, and refuses another session's run and one
   something is still driving — see [Adopting a run whose driver
   died](docs/orchestration.md#adopting-a-run-whose-driver-died). While the run
   works, the engine surfaces a **non-blocking** update for a dispatch that has
   recorded nothing past its stall threshold, and the monitor surfaces what it sees
   drifting from the plan; both are evidence to act on rather than verdicts, so
   decide between `cancel`, `retry`, `context`, and letting it run.
   `channel-reply` refuses an edit it
   cannot apply, with the reason, and every edit it accepts reaches the graph; a
   non-zero reply is a rejection to correct, never a command to resend.

   **One execution path per deliverable.** When a path fails, diagnose and fix that
   path or escalate to the operator with evidence; never launch a duplicate parallel
   path for the same deliverable — a manager-driven integrate or recovery beside a
   live node delivering it counts as a duplicate — without explicit operator
   approval. `cancel` is the tool for idling a redundant or misdirected node and
   `requeue` for resuming it; see [Parking a node, and picking it up
   again](docs/orchestration.md#parking-a-node-and-picking-it-up-again).

### Never let dispatched work run unwatched

This is the most critical rule of the arrangement. Dispatched work runs for hours
after the turn that launched it; with nothing watching, the project runs
unsupervised and the user is in the dark. It is an **invariant, not a mechanism**,
and more than one mechanism satisfies it:

1. **A watch is armed before you turn to anything else** — not after the next
   step, not when convenient. A launch is not finished until its watch is up.
2. **The watch emits on every terminal state, not just the happy path**: a
   blocking surface waiting, a worker gone quiet, `DRIVER DEAD`, `UNDRIVEN`,
   `PARKED`, and settlement. Silence must never be indistinguishable from
   progress — a watch that greps only for success is silent through a crashloop,
   and that silence reads exactly like work in flight.
3. **A foreground attach alone is not an armed watch.** It dies with the turn that
   started it. Run the attached launch **plus** an independent watch, neither
   depending on the other.
4. **Any mechanism meeting 1-3 is acceptable** — a `just channel-next` loop, an
   out-of-band poll on `just status` that emits on state change, a scheduled
   wake-up. The invariant is what is named here; the tool is yours to pick.
5. **The watch must emit on the unread-surface line specifically.** This is a
   HARD REQUIREMENT: the `N planner update(s) waiting, unread for T` line that
   `just runs` and `just status` add per affected run has to reach you, and
   filtering it out as noise is
   forbidden. A manager watch that filtered it went silent while 26 updates queued
   and one blocking question was asked three times, with every other indicator
   green throughout. A blocking surface produces no other signal until it is read
   — the run reports plain `ACTIVE`, never `awaiting-planner` — so dropping that
   one line removes the whole question channel invisibly.

### Answering on the channel

Two measured channel defects make reply discipline part of the job rather than a
detail:

- **Read `runs/<run-id>/channel/queue.json` before replying**, and confirm the
  `pending` surface is the one being answered. A reply binds to whatever is
  pending at that instant, not to the surface you just read.
- **A blocking surface may have no asker.** A `channel serve` that timed out exits
  without withdrawing its surface, so `awaiting-planner` does not prove anybody is
  still waiting for the answer.
- **Steer a running dispatch with a `context` edit, never with `oneagentgraph
  interrupt` by hand.** These are not alternatives: a `context` edit *is* an
  interrupt against that dispatch's control socket, wrapped so the lever's own events
  reach the run's journal stamped with the node. An interrupt is not journalled, which
  leaves the run's own record unable to explain why a worker changed direction — so
  prefer the edit because it records what the raw verb does not, and reach for
  `deliver: live` when the correction cannot wait, since its refusal tells you the note
  did not land instead of leaving you to assume it did. See
  [Carried planner context](docs/orchestration.md#carried-planner-context).

After `just orchestrate`, the manager uses **only** `just channel-next`, `just
channel-reply`, `just stop`, and the read-only `just monitor` / `just runs` /
`just status` views. `channel-reply` carries both legacy verdicts and [versioned
live edits](docs/orchestration.md#live-graph-edits). There is no verb that advances
a run, so there is nothing left for the manager to drive: the engine reconciles
continuously and two writers would race the ledger lock anyway.

`just channel-next` and `just monitor` read through the `planner`
[profile](docs/orchestration.md#read-profiles) — the pipeline's own decisions and
settlements, not every worker's turns. That narrowing is the point: the detail is
the monitor's to read, through `--filter monitor`, and the same flag is here when
you want it. `--all` bypasses profiles entirely.

`orchestrate` **stays attached by default**: it prints the launch record, streams
the run's merged events, and returns when the run **settles** — the
graph completed, a blocking planner surface is waiting on you, or nothing is
driving the run any more (exit 3, and the state to intervene in). Ctrl-C detaches
without stopping the run. Pass `--detach` when a run should go unattended — several
runs supervised at once, where you launch each one and come back to it — and
`just monitor <run-id>` re-attaches to any of them, streaming the same events
without the settle-and-return contract the foreground launch has. Do **not**
background a launch by hand to watch it; that is
what the foreground default replaced. Rebuilding run state
from `events.jsonl`, `ps`, or `git log` in a run clone instead is the omission
the read-only views now prevent: `just runs` and `just status` name every unread
surface, how stale it is, and the `just channel-next` that reads it, so an update
nobody read can no longer hide behind a row that says only `ACTIVE`. Rendering a
surface in `monitor` is not reading it — only `channel-next` consumes one.

**Runs are owned.** Several managers share this host, each supervising its own
workstreams, so a run belongs to the session that launched it. `just orchestrate`
records that session automatically and `just runs` shows it per row: `[mine]`, the
owning session (`[claude-code:3f9a1c2e]`), or `[unknown]`. `just runs --mine` lists
only yours. Act **only** on runs you launched. A run you cannot attribute belongs to
another manager until proven otherwise — `unknown` is never yours, and a run
launched before its session was recorded stays `unknown` forever. Never derive a
process list from `ps` and signal it: that pattern knows nothing about whose work it
matched, and it has already interrupted another manager mid-supervision here.
`just stop <run-id>` is the supported way to stop a run; it refuses another
manager's run and an unattributable one, naming the owner, and `--force` reports
that owner before overriding. `just orchestrate --adopt <run-id>` attaches a fresh driver
to its intact ledger. `complete` is a completion
verdict on the channel and deliberately does **not** stop scheduling; use `just
stop` when a run must actually end. A stopped run is left reclaimable exactly as a
run whose driver died is. `stop` is deliberately **not** in
`.claude/settings.json`'s allowlist: it ends live work, and `--force` overrides the
ownership check the incident above is about, so each one is approved on its own.

An agent-written, non-blocking per-workstream status also arrives when the durable
planner-update pacemaker becomes due (30 minutes by default). That pacemaker is the
`check-in` member of `graphs/dag-scope.yaml`, and it carries its own `task` that
opens with `{task}` — which is why that document declares schema 4. The task is what
keeps the member reporting rather than editing: `onepipeline` composes one task for
the graph and `oneagentgraph` gives it to every member that claims none, so a member
whose job is not the run-level task must state its own — and must interpolate the
composed one back in, because that composed task is this graph's own way of naming
the run. The environment names it too, as `ONEPIPELINE_RUN_ID` — measured against
onepipeline 0.8.1 on a real launch and gated in `tests/e2e/` — but that is a
per-release export rather than a contract, so members here are written against
`{task}`. Never let this one reach `onepipeline
reply`; live edits belong to the `monitor` member, which stays for the whole run,
and to you. Every planner-visible surface resets that clock. The interval is set
once, at launch, with `just orchestrate ... --heartbeat-interval SECONDS`, and
there is no way to change it afterwards: the reply envelope `just channel-reply`
sends is closed to unknown fields and accepts exactly `version`, `author`,
`completion`, `message`, `reason`, and `commands`, so a reply carrying
`"heartbeat_interval"` is refused whole and its verdict and graph edits go with it.
`--heartbeat-interval` is on `onepipeline start` alone — not on `adopt` either — so
choose the interval when launching, and relaunch rather than expecting to retune a
live run. The run continues without waiting for a reply to these surfaces.

Judge a dispatched branch against its own base (`merge-base` / `base..branch`),
never a moving `origin/main`; concurrent advancement can make a healthy branch
appear to delete files.

Treat unresolved same-identity dependencies as stack prerequisites, not merely
scheduling edges, and preserve them across replans until their content reaches
the root base. The deterministic mechanics live in `docs/repo-lifecycle.md`.

Accuracy and quality come first; saving time or tokens never relaxes their bar.
Subject to that, minimize both.

The manager owns the user conversation, the brief, review decisions, human-action
attestation, and the watch. The planner owns decomposition, contracts, persona
choice, and task authoring — its judgment is in
[`personas/planner.yaml`](personas/planner.yaml), and the operational half of it
under [Decomposition and
scheduling](docs/orchestration.md#decomposition-and-scheduling). The engine owns
scheduling, ledger writes,
integration of finished work, and publication closeout. The monitor owns noticing —
and, where a fix is unambiguous and inside its
[allowlist](docs/orchestration.md#who-issued-an-edit-and-what-that-bounds), applying
it. A finding it or the pacemaker calls a **rule violation** quotes the file and line
that rule comes from; what neither can ground that way is an **observation**, and its
own supervisor sends it back until it is one. See [A finding names the rule it is
grounded in](docs/orchestration.md#a-finding-names-the-rule-it-is-grounded-in). None of these roles authors target-project content; dispatch implementation and
research to workers.

Require each worker to prove its own change with `just gate`. Review surfaced gate
evidence rather than a judge verdict alone; relevant checks must not have skipped.

## Personas and the base config

A persona is a small onejudge **delta** file in `personas/` — the agent's general
role (`system_prompt`) plus the supervisor's review bar (`persona`). General
cross-repo roles are top-level files; repo-specific roles live under
`personas/<repo>/` and are dispatched with `<repo>/<name>`.
Common settings live once in `config/onejudge.base.yaml` (the base config);
`dispatch` merges base ⊕ persona ⊕ the CLI `--task` into one effective config and
runs `onejudge run` on it. Keep subtask-specificity in the node controls described
above; add a persona only for a genuinely distinct role or review bar. A dedicated
`reviewer` is reserved for complex DAGs where one agent reviews and integrates
several agents' independently produced work, since the simulated-user supervisor
already reviews every dispatch.

Draft a new role under gitignored `scratch/personas/`, dispatch against that
directory, and refine it from observed performance. Once proven, dispatch its
addition through this repo's isolated self-lifecycle; `just new-persona <name>`
scaffolds the tracked file and `just check` validates it. General personas stay
flat; repo-specific personas use slash-qualified names and subdirectories
(`crozier/crozier-corpus` maps to `personas/crozier/crozier-corpus.yaml`).

## The two sides of the conversation

This repository adopts the exact onejudge version declared in
`config/onejudge.version`, and session setup installs the pinned PyPI
`onejudge` distribution and verifies both its `onejudge_sdk` import and matching
CLI wheel. Dispatch calls the typed Python SDK, which drives and validates the
real CLI. onejudge drives a
two-party conversation, and harness/model selection for each
side lives in oneharness config, not onejudge:

**Every role names the same five identities** — `claude-code:alternate`,
`claude-code:alternate2`, `codex`, `codex:alternate`, `claude-code:primary` — and
they differ only in order. A role that omitted one would lose that quota entirely
once everything ahead of it was exhausted, which is the failure this arrangement
exists to prevent. The primary Claude identity is last everywhere:

- **Agent side** (does the work) — `oneharness.toml`, discovered from the repo root;
  it prefers both alternate Claude subscriptions, in order, because the personas
  are tuned against that model tier, and only then falls back to codex. Every agent
  turn also carries the normalized tool transcript, selected by
  `scripts/oneharness-agent.sh` because it is a `run` flag with no config key: the
  planner-visible views already build `just status`'s `Commands:` line and `just
  history-show`'s detail from each record's `events`, and claude-code's default
  output format carries no tool transcript at all, so those were empty for every
  claude-code dispatch. A dispatched turn takes **`--stream`**, which delivers those
  same events *as they occur* and implies `--events`' format selection; `--events`
  alone says only that the transcript is in the report at the end, which left a node
  invisible for the 600-2000 seconds a turn runs here. Streaming a
  `run_mode = "fallback"` chain has been allowed since oneharness 0.6.5, so nothing
  is traded for it. See [Streaming the agent
  side](docs/onejudge-integration.md#streaming-the-agent-side) for the filter that
  reconciles a stream with onejudge's single-report contract, the probe that decides,
  and why `--events` remains the degrade path.
- **Judge / simulated-user side** (supervises) — `oneharness.judge.toml`, passed
  as the base config's `provider.judge_config`; codex first, then the alternate
  Claude subscriptions. It keeps its cheaper-supervisor intent through `model`
  (`claude-sonnet-5` for all three of its Claude variants) rather than by staying
  off those subscriptions.
- **Monitor side** (watches a tracked graph) — `oneharness.orchestrator.toml`,
  named by `graphs/dag-scope.yaml`'s `monitor` member as its **agent** side. That
  member's judge side is not a harness config at all: it is the live manager, over
  `scripts/channel-serve.py`, so replying to a monitor surface steers its next turn.
  Deliberately the reverse of the worker order: both codex identities carry the
  role first, so this long-lived supervisory process does not queue ahead of the
  workers while Codex can still run it. It is also the **one** side here with no
  per-turn deadline (`timeout = 0`), because it watches for as long as the run
  lasts and a deadline would end the watching.
- **Pacemaker side** (the `check-in` planner update) — `oneharness.check-in.toml`,
  the monitor's routing verbatim with a finite deadline. It is a separate file
  for exactly one reason, and re-merging the two is a silent regression: see
  [Choosing a deadline per
  side](docs/onejudge-integration.md#choosing-a-deadline-per-side).
- **Drafting side** (the `pr-author` body a change request opens under) —
  `oneharness.pr-author.toml`, named by `graphs/pr-author.yaml`'s one member as its
  **agent** side; it has no judge side, because the review is a JSON Schema. The same
  supervisory order and a third copy of it, for the same per-deadline reason the
  pacemaker's is a copy: a drafter sits between a passed gate and a publication, so it
  keeps a finite `timeout`. It is also the one side here with `stream = false`, which
  is forced rather than chosen — oneharness validates a structured answer against the
  complete response, so `stream = true` and `schema_file` cannot both hold.
- **LLM lint side** — `oneharness.llmlint.toml`, forced by
  `scripts/llmlint-oneharness.sh`; the same supervisory order. It is **no longer
  codex-only**.

The judge and llmlint reaching the workers' subscriptions at all is the operator's
deliberate trade: those tiers can now contend for that Claude quota, and that is
accepted because a supervisory tier that can still run beats one isolated from the
quota that is left. Do not reorder these to restore the old isolation.

**Streaming and out-of-band turn control are independent, and a control failure is
never a reason to stop streaming.** `--stream` decides *when* a turn's transcript
arrives; `--control` opens a socket a separate process can interrupt the live turn
over. Both are supported together on a multi-identity fallback chain as of oneharness
0.8.0, whatever harness families it mixes: 0.7.2 stopped the control validator
counting *candidates* where it meant concurrent turns, and 0.8.0 stopped it demanding
one turn-control mechanism across the whole chain, binding the mechanism to the
candidate that serves the turn instead. That was the constraint that bit here — every
chain mixes claude-code with codex, which declare different mechanisms — so the
committed chains are now planned with each candidate on its own. The other constraint
was the socket **address**, capped at 108 bytes on Linux; oneharness 0.10.0 bounds it
at construction, and a real launch on the adopted stack settles a dispatched worker
whose report names a bound control session with `control_unavailable` null. So turn
control is live on every dispatch here rather than held in reserve — at an address
measured 107 bytes long, which is the whole budget.
Turning a member's `stream` off to dodge a control refusal trades away the per-turn
visibility a manager supervises with and fixes nothing; that workaround was written
against this repository and rejected, and no member carries a `stream` key today. See
[Streaming and turn control are independent
concerns](docs/onejudge-integration.md#streaming-and-turn-control-are-independent-concerns).

Those files decide the defaults for every run on this host. Pairing the two sides
differently for **one** run is a property of that run's agent graph: pass
`--node-set members.worker.agent.oneharness_config=REF` and the corresponding
`members.worker.judge.oneharness_config=REF` override to `just orchestrate`, where
each referenced config declares the intended identity. Use `--set` instead of
`--node-set` to override a dag-scope member. Do not edit a config concurrent runs
also read.
The adopted graph invokes oneharness directly, and since oneagentgraph 0.2.18 that
is literal rather than loose: a single-sided `kind: oneharness` member — this host's
`check-in` pacemaker — runs its turn through `oneharness_core` on a thread of the
graph process, so **no `oneharness` CLI is spawned for it** and
`ONEAGENTGRAPH_ONEHARNESS_BIN` does not reach it. Every `member-started` on the
adopted stack reports `runner: library`. What is still a process is the provider the
turn selects. `scripts/oneharness-agent.sh` is
still the smoke/manual boundary — the one path that deliberately spawns the CLI — but
its `ORCHESTRATOR_WORKER_HARNESSES` /
`ORCHESTRATOR_JUDGE_HARNESSES` compatibility variables are not on this launch
path. oneharness's `ONEHARNESS_HARNESSES` is process-wide and cannot express a
per-side choice. See
[Choosing a harness per
side](docs/onejudge-integration.md#choosing-a-harness-per-side).

Choosing the identity does not choose the **tier**: every one of those files pins a
`model` per harness, and the judge's three Claude identities are pinned to the
cheaper supervisor model deliberately. The model half of the per-side seam is
graph-native: override `members.worker.agent.model` and
`members.worker.judge.model`, and it applies to whichever candidate the chain
selects — `fallback` does not fall through a task failure, so a model a side's
selected identity rejects dies on that rejection rather than degrading. Note what
the seam does **not** buy — a config's per-harness `model` beats `ONEHARNESS_MODEL`,
so a model exported into the environment is inert against
`oneharness.llmlint.toml`'s own pins; overriding which *identity* that tier judges
on is what changes its model. See [Choosing a model per
side](docs/onejudge-integration.md#choosing-a-model-per-side).

Nor does it choose the **deadline**, and that half of the seam has no graph-native
field at all: a member takes `oneharness_config`, `model`, and `stream`, so the
config file is the only place a per-member `timeout` can live. Since oneharness
0.7.0, absent means no deadline; the worker, judge, and llmlint configs intentionally
take that default. `timeout = 0` also means no deadline and remains explicit in
`oneharness.orchestrator.toml`, because the monitor watches for the life of the run.
That is why the `check-in` pacemaker no longer shares that file: an unbounded
deadline reaching a scheduled member would leave a wedged turn alive forever,
which fails silently, so it reads
`oneharness.check-in.toml` instead. Never point two members at one config to save a
copy, and never set `ONEHARNESS_TIMEOUT` to fix a deadline — it is process-wide for
the whole graph run and beats every file, so it moves every member at once. See
[Choosing a deadline per
side](docs/onejudge-integration.md#choosing-a-deadline-per-side).

`scripts/claude-alt-config-dir.sh` is the one source of **both** alternate config
directories (`ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR` → `$HOME/.claude-alt`,
`ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR` → `$HOME/.claude-alt2`), and all three
wrappers source it, so no role needs either exported by hand and no two can drift
apart. Authenticate the second plan with `CLAUDE_CONFIG_DIR="$HOME/.claude-alt2"
claude`; until then it costs nothing, because claude-code classifies an absent
config directory exactly as it classifies an empty one — `auth`, which falls
through — so unlike the codex helper this one creates nothing. See
[The second alternate Claude
subscription](docs/onejudge-integration.md#the-second-alternate-claude-subscription).

Every one of those chains also names `codex:alternate`, a **second codex
identity** that absorbs an exhausted quota without changing which subscription a
role competes for. `scripts/codex-alt-home.sh` is its one source — it derives
`ORCHESTRATOR_CODEX_ALT_HOME` as `$HOME/.codex-alt` and all three wrappers source
it, because oneharness refuses to start whenever that indirection is unset.
Authenticate it with `CODEX_HOME="$HOME/.codex-alt" codex login`; until then the
candidate costs nothing, since the helper guarantees the directory **exists** and
an empty codex home falls through as `auth` while an absent one hard-fails. That
asymmetry is the whole reason the helper creates it — see
[The second Codex identity](docs/onejudge-integration.md#the-second-codex-identity).

`onejudge init` scaffolds both files plus a starter `onejudge.yaml`. The adopted
exact oneharness release is declared in `config/oneharness.version`, installed as
the `oneharness-cli` PyPI wheel, and verified by `scripts/session-setup.sh`. Session
setup also installs and verifies Bun for oneharness's SDK gate. The
committed configs are that output with auth-variant routing, a cheaper Claude
judge fallback, and the `IS_SANDBOX` env, and
`config/onejudge.base.yaml` supersedes init's starter `onejudge.yaml`. Regenerate
with `onejudge init --force`.

**Live dispatch** picks a harness via `oneharness.toml`'s fallback (alternate
Claude subscription primary, codex secondary).
The lifecycle *and the orchestrator process itself* dispatch in **`bypass`** mode by
default — the no-approval mode. It is correct here because the **whole environment
is a sandbox** (a container):
codex's own `workspace-write` sandbox (`auto` mode) needs unprivileged user
namespaces this host disables, so `bypass` (no approvals, no inner sandbox) is the
working no-approval mode and the container is the boundary. The **allowlister**
`repo-write` hook stays wired (`scripts/session-setup.sh`) as belt-and-suspenders.
Full rationale, the merge strategies, and the claude-code caveat:
`docs/repo-lifecycle.md` and `docs/onejudge-integration.md`.

## Command surface

Use the `just` recipes (`just --list` is the index); do not hand-roll
equivalents. `just bootstrap` sets up from a clean clone (installs the toolchain,
activates the git hooks); `just check` is the deterministic tier, while `just gate`
is the complete pre-push bar: `check` plus the llmlint diff tier. Both report the
line-coverage total they measured, and every stage that captures its output keeps
it at `.logs/<label>.log` (`nx`, `workspace-install`, `check`, `upgrade`,
`gate-check`, `gate-llmlint`) — gitignored, owner-only, credential values redacted, truncated
per run. Each log fills as its own stage runs, so follow the innermost one:
`.logs/nx.log` while the Nx targets run (the long part), `.logs/check.log` for
the stages after them. Read a finished run from the same paths, and never read a
live command through `/proc`. Those paths are safe to tail because the truncation
is per *invocation*, not per path: a run records the log it is writing in the
exported `ORCHESTRATOR_PRESERVED_LOGS` claim list, and a nested run that finds its
path already claimed by a live enclosing one writes `.logs/<label>.<pid>.log`
instead and names that path in its own failure. This matters here because the
suite runs `just lint-llm-diff` against this checkout from inside `just check`.
There are no engine verbs left to invoke: `onepipeline start` drives the DAG to
settlement on its own, so `just orchestrate <plan.json>` is the whole launch and
the manager supervises the surfaces and proposals it raises over the [live
channel](docs/orchestration.md#the-planner-channel). There is no single-dispatch
command: one
subtask is a one-node plan (`examples/single-node-direct.plan.json`,
`examples/single-node-lifecycle.plan.json`), so no running work falls outside the
run ledger and the views built on it.
`just plan <brief.md>` is that same launch for one shape of node: it writes the
one-node plan a manager-written brief becomes
(`examples/planner-brief.example.md` → `examples/single-node-planner.plan.json`)
and launches it. The brief is the dispatched task verbatim, so it is written in
the `## What` / `## Why` / `## Acceptance criteria` template and refused when it is
not. Two things about that launch are the reason the recipe makes it rather than a
manager: the persona is the **path** `../personas/planner.yaml`, because the bare
name resolves to a role built into the tool and this repository's file is never
read; and the run id it prints is guaranteed to be the run's own, by refusing a name
whose run root is already taken rather than by predicting the `<name>-2`
`onepipeline` would mint instead. That refusal is what lets the recipe also hand
that id to its dispatch as `ONEPIPELINE_RUN_ID`, the run whose channel a blocking
question goes to — without it, a detached planner's questions would queue on a live
run belonging to somebody else's workstream.

**Every** launch this repository makes exports `ORCHESTRATOR_ASK_MANAGER`, the path
of `scripts/ask-manager.sh`, which is how a dispatched agent puts one blocking
question to its manager over the run's own channel instead of guessing at a
decision fork: `just orchestrate` attached, detached, and adopted, and `just plan`.
The wrapper is half of that seam and the run it asks on is the other half — it reads
`ONEPIPELINE_RUN_ID` and refuses rather than guessing at one — and **every node
dispatch of a run carries it as of onepipeline 0.8.1**, composed where the dispatch
is made. Below that release nothing composed it: it reached a worker only by leaking
out of an *attached* driver that had started an observer graph in its own process, so
a dispatch of a detached or adopted run met its first fork with the wrapper there and
no run for it to ask on, and every brief had to be written to be answerable without
asking. `just plan` is the exception that stayed sound throughout, because it writes
the plan and exports the id itself.
`tests/e2e/test_launch_ask_seam_e2e.py` measures both halves per launch shape,
and its run-id journey fails on the detached and adopted shapes below the bump.
`scripts/ask-manager-env.sh` is the wrapper's one source and `scripts/onepipeline.sh`
is where a launch takes it, so a read-only view — which dispatches nobody — takes it
not at all. That wrapper is the only supported way to ask: `onepipeline channel
serve` answers its own timeouts at exit 0 with a ruling that reads like a decision,
and a reply is claimed by whichever reader reaches it next, so a question asked any
other way can be answered by a fabricated verdict or by a live graph edit meant for
the engine. A question is answered with `just channel-next` and `just
channel-reply`, which the launch prints.
`just runs` lists recorded runs with the session that launched each one and the
surfaces each has queued unread; `just runs --mine` narrows that to this session's.
`just stop <run-id>` ends a run and its whole dispatch tree, subject to the
[ownership rule](#your-loop-as-manager) above. Every one of those verbs goes
through `scripts/onepipeline.sh`, which is the one place a manager's identity is
established: `onepipeline` decides ownership from `ONEPIPELINE_LAUNCHER` /
`ONEPIPELINE_LAUNCHER_SESSION`, the reader's as well as the launcher's, so a view
that did not identify itself matches no run and `--mine` lists nothing.

What a run's agents *are* is [`graphs/`](docs/orchestration.md#the-agent-graphs-a-run-launches),
and it is this repository's content rather than the engines': `onepipeline` ships
the paths `graphs/dag-scope.yaml`, `graphs/node-scope.yaml`, and
`graphs/pr-author.yaml`, not the files, because they name this operator's own
onejudge base config, oneharness configs, and personas. A checkout without them
refuses every plan it has. All three paths
resolve against the directory a run is launched from, which is why `just
orchestrate` is run from the repository root.

Those views also stop guessing at what is *running*. A node the ledger records as
started now reports which side of the conversation is serving it, on which harness
identity, and for how long — with an anomalous duration for that role flagged, and a
node nothing is driving flagged `UNDRIVEN` (deliberately not `parked`, which is the
node state a manager's own `cancel` produces). All of it is proven
from the dispatch ownership registry, never from `ps` output matched by pattern: the
`ORCHESTRATOR_AGENT_STATUS_DIR` stamp the kernel fixes into every process a dispatch
starts, plus the owner lock a live dispatcher holds. `just status` carries the host's
load averages with that same attribution, and **`just host`** is the whole-host
view — per live dispatch, its owning session, run/node, role, turn age, and load
contribution. Miscounting live dispatches from `ps`, and missing a judge turn wedged
for nearly two hours, are what these replace.
They also report the tier *above* those dispatches: one driver line per unfinished
launch naming whether the orchestrator's recorded pid is still there, which part of
its loop the run's own state places it in, and how long since anything of it was last observed doing something.
A driver this host has proved is gone reads `DRIVER DEAD … nothing is driving this
run` — distinct from `PARKED`, which is a launch that still holds its pid. The same
tier is served as run-scope timeline spans, from a bounded local capture when the
harness refused to write its history; see [Seeing the supervisory
tier](docs/telemetry.md#seeing-the-supervisory-tier). Both views also say when they
cannot fully answer: on the adopted onepipeline 0.8.1 a run whose journal does not hold
every record whole prints `journal: … — this run's record of itself is incomplete`, which
is the one line that makes the rest unprovable, so read it before acting on a node
those views show as never settled. It used to be said only on the driver's stderr,
which a detached run writes to a log nobody opens.
**`just recoverable`** is the other half of that: every preserved-but-unpublished
branch, where it lives, why its workstream stopped,
whether it carries an incomplete-step marker, and the exact command that lands it —
`just repo-recover` for incomplete provenance, `just publish-branch` for a complete
branch under its identity's policy, `just integrate` for the local merge train,
with the fetch included when the publication checkout does not have the
branch. Reach for it instead of diffing clones by hand. **Which branches "every"
covers is decided by where you run it**, and it is not always every identity: run
inside a registered checkout it answers for that checkout's identity **alone**, and
run anywhere else it answers across every registered identity. Run from this
repository root — a registered checkout — it lists ai-orchestrator branches and no
onepipeline ones. So an empty or short result is never "nothing to recover"
anywhere; read the first line, which names the scope it just answered at, and ask
again from outside any registered checkout for the cross-identity view. Every one of these views is
read-only and safe beside live work.
Human completion is never inferred and enters the graph only as an explicit live
`attest` command, or the equivalent `onepipeline attest RUN REFERENCE`. Keep
operational
syntax and result contracts in
`docs/orchestration.md` and lifecycle policy in `docs/repo-lifecycle.md` rather
than duplicating command help here.
The root quality recipes delegate project selection and caching to Nx:
`check`/`test` use the full uniform target set through `run-many`, while
`lint`/`typecheck`/`format` use affected selection. The language-native tools
inside each project target remain authoritative, and `format-check` remains a
format-only verification. Session provisioning and the initial locked Bun
install precede Nx because they make Nx available; bootstrap then delegates
project setup through uniform Nx `bootstrap` targets.
`scripts/workspace-install.sh` is that locked Bun install's one source. A freshly
created worktree carries no `node_modules`, so every `scripts/nx.sh` runs it first
and heals itself; `just bootstrap` runs it with `--force`, which reapplies a
lockfile that moved. Nothing here asks an operator to run Bun by hand, and the e2e
journeys that drive real Nx provision through the same script rather than skipping
when a worktree is fresh — a bare `pytest` in one means what the gate means.
Use `docs/telemetry.md` to inspect session timing, usage, and the agent/judge
turn timeline with `just telemetry`.
A node that died to the provider is diagnosed from `just status` alone: it and
`just runs` print a provider-health block for all five configured identities —
each one's binding window, utilization, and reset, with a failed probe listed as
`unknown` rather than dropped — above one rolled-up line per repeated cause naming
the refusing **side** and **identity**. Read the side first: the agent and judge
chains prefer different identities, so a fix aimed at the wrong one changes
nothing, and a whole night was once lost to a judge-chain quota that read as a
bare `harness failed (quota)`. `just results` and the read API carry the same
attribution per node with the harness's own bounded output. See [Diagnosing a
provider failure](docs/telemetry.md#diagnosing-a-provider-failure).
`just telemetry-server` serves the published read-only DAG API over a runs root and
`just dag-ui` serves the published browser bundle against it; both are read-only and
mutate no run. Neither is built here any more — the API is `onepipeline-api` and the
view is the `onepipeline-ui` bundle — so `just dag-ui` puts the two behind one origin
and `just dag-ui-screens` photographs that bundle at every viewport in the matrix,
printing the gitignored per-invocation gallery it wrote. Operational detail lives in
[`docs/dag-ui.md`](docs/dag-ui.md).
`just sweep-scratch` reclaims the scratch a dispatch leaves behind — the families
`oneagentgraph` itself produces, each judged on proven non-reference: a candidate no
live process names in its argv, environment, `cwd`/`root`/`exe`, open descriptors,
or memory mappings, past a short age that only covers the gap between creating a
directory and first naming it. `--dry-run` inspects without removing, and
`--min-age-hours` moves the conservative threshold for scratch that is only stale.
Session setup runs it automatically. Every sweep names the families it examined and
the families it could not, so a sweep that reclaimed nothing never hides an unswept
one.

The processes that are *meant* to outlive their launcher — the driver `just
orchestrate` starts, and the dispatches and publications it forks — are the
engines' own to keep alive and to reap; never work around a kill here with
`nohup`/`setsid` by hand.
Dead lifecycle runs form a separate bounded recovery history: retain the newest
**3** run roots with unpublished work. A retry or `repo-recover` adopts the exact
worktree only after claiming its free occupancy lease and rejecting a live
recorded owner; dirty adopted work becomes an incomplete-step commit and must
pass the ordinary merge-path gate before publication.

`just smoke` spends one real agent-harness turn in a throwaway directory and
verifies exact prompt delivery plus a successful, fully accounted oneharness
history record. Native per-phase timing is provider-optional, so its absence is a
telemetry-quality signal rather than a launch failure. The *launch* is relaunched a
bounded number of times — `oneagentgraph smoke` owns that policy now — and only the
launch: a host under
concurrent e2e load has started the selected harness and had it die, which reads as
a launch-path outage and once cost a publication that had already passed its gate.
A genuinely broken launch path fails every attempt and still fails, and a recorded
turn that violates the contract fails on the first. A passing run says how many
launches it took. The record it judges is the **selected** candidate's: a
`fallback` chain records every candidate it attempts, so an identity that refused
the turn with a classified `quota` or `auth` failure — or was skipped outright — is
the chain working, and the smoke names it in the pass rather than failing on it.
Anything else is still a launch failure: the selected record breaking the contract,
a candidate failing for a reason the chain does not move past, a candidate whose
record does not back the reason it names — one that identifies no harness, or that
carries a turn somebody was billed for — or every candidate refusing. See [The
record a fallback chain is judged
by](docs/onejudge-integration.md#the-record-a-fallback-chain-is-judged-by).
It is deliberately outside `just gate`. The pre-push hook runs it
only when the pushed diff touches `scripts/`,
`config/oneharness.version`, `config/onejudge.base.yaml`, `oneharness.toml`,
`oneharness.judge.toml`, `oneharness.orchestrator.toml`, or
`oneharness.check-in.toml`; ordinary pushes consume no harness quota.

A dispatched change is not done until `just gate` is green, and its agent clears
its own llmlint findings rather than leaving closeout to integration: iterate on
them with `just lint-llm-diff <base>` alone, then run `just gate` once to confirm.
`llmlint.yml` is a legitimate deliverable when a task names it; otherwise a worker
fixes the code or adds a justified site-scoped `ignore` directive, and reports a
rule that looks wrong or misapplied instead of editing it. Deciding when a marginal
finding stops being worth another gate cycle—landing with a justified line-scoped
suppression plus a tracked follow-up—is the manager's call from that surfaced
report, never the worker's by suppressing.

The judge behind that tier is non-deterministic, so the run itself is cached: `just
lint-llm-diff` resolves the base ref to a commit and runs the cached Nx
`workspace:lint-llm-diff` target (the root `project.json` — the check spans the
whole tree, so it belongs to no single project), whose command is
`scripts/llmlint-judge.sh` — llmlint with `-v`, and nothing else. Re-running `just
gate` on an unchanged tree against an unchanged base replays that run's own
terminal output instead of rolling the judge again, which is what stops one branch
from being blocked by opposite verdicts on an identical diff. `-v` is why replay
loses nothing that matters: the report carries every rule and the `llmlint history
<id>` pointer, and Nx replays it verbatim. A green elides one thing — the
serialized judge calls `-v` also prints, ~214KB of a 226KB run — because Nx
replays a hit as one burst and a burst past one pipe buffer arrives truncated;
`llmlint history <id>` has them in full, and a failure is never cached and so
prints everything. The key covers the whole workspace, the
resolved base commit, and `scripts/llmlint-fingerprint.sh` — the installed llmlint
version plus the effective merged config, so a rule change in a plugin fetched from
outside this repository still invalidates. That fingerprint resolves both of those
through `scripts/llmlint-runtime-env.sh` — the one environment the target itself
judges with — rather than the caller's, so the key always describes the judge
configuration the run would actually use. `LLMLINT_ONEHARNESS_BIN` is why: `llmlint
config` renders it as `oneharness.bin`, and a dispatch inherits the orchestrator's
checkout path, the session's own, or nothing at all, so one judged diff hashed to a
different key per dispatch and the judge re-rolled on every run. Reading the caller
fails a quieter way too — because Nx scores a runtime input that exits non-zero as
*no contribution* rather than as an error, a fingerprint the caller's environment
can break does not fail the tier, it drops the judge configuration out of the key
and replays a verdict that configuration has moved on from. Because Nx caches
successful tasks only, **only a green is cached**: findings (llmlint exit 1) and a
toolchain that never reached a verdict (exit >= 2) both fail the tier and re-judge
on the next run. That is deliberate — the record/replay protocol that used to
smuggle failures through Nx is gone, and with it the shared scratch directory a
concurrent invocation could clear out from under an in-flight judge. A branch
working through a red pays a fresh roll each time, and `llmlint history` (retained at
`history.max_runs` in `llmlint.yml`) is where every roll lands. A wrong *green*
still sticks: `just lint-llm-diff <base> --skip-nx-cache` re-judges but neither
reads nor writes the cache, so the next ordinary run replays the same entry until
the tree, the base commit, or the judge configuration moves. That per-invocation
flag is the only supported re-judge lever; an ambient global
`NX_SKIP_NX_CACHE` / `NX_DISABLE_NX_CACHE` is reported and ignored by this tier,
because it re-rolls the judge from every unrelated command and breaks the checks
whose contract is cache replay. When a
miss is unexplained, run `scripts/llmlint-fingerprint.sh` — a changed fingerprint
on an unchanged tree is a changed judge, not a changed diff. The cached green for
one content, base commit, and judge configuration is authoritative and the
worker's own gate pays for it: `ONEVCS_COMPARISON_REMOTE` / `ONEVCS_COMPARISON_BASE`
is that identity's one source, and the lifecycle exports it to every dispatch and
every publishing push
of a workstream so the `pre-push` hook replays what the worker cleared instead of
re-rolling against findings it never saw — a push that resolved its own base could
merge work whose own gate had failed. See
[One judged diff, one verdict](docs/repo-lifecycle.md#one-judged-diff-one-verdict).

Every cached Nx target replays a recorded answer, so one rule governs the test tier
too: a memo may stand in for a verdict on this tree only when its key covers
everything the check reads. The Python targets run from the workspace root over the
whole tree — pytest reads documentation, recipes, hooks, and app config — so they
are keyed on it through `nx.json`'s `wholeWorkspace` input. Narrowing one back to a
subset makes a green suite a claim about a tree that was never run; force a real
re-run of a single tier with `--skip-nx-cache` on that one invocation instead. The
narrowings that earn their keep answer at the scope their tests read:
`orchestrator:test-docs` runs the handful that assert on this repository's prose
and keeps the whole-workspace key; `orchestrator:test-recipes` runs the journeys
that drive `just` recipes and shell scripts under `recipeWorkspace`, exactly what
they drive; `orchestrator:test` runs the rest under `codeWorkspace` — the
workspace minus `docs/**` and `**/*.md`.
`workspace:check-nx-cache` is narrowed the same way, onto the fixture and
scripts it builds its two worktrees from. A documentation edit stops charging for
the whole suite. Where no key would be right the tier is **uncached** instead:
`orchestrator:test-checkouts` reconciles this repository's routing against the
registered checkouts of the repositories it routes — which of them run cargo-nextest,
against the gate argv each rule gives — and those live outside the workspace, so a
memo would describe whatever they looked like when it was recorded. The same tier
holds the **lost-turn wire drift gate**, for the same reason and against a different
outsider: `tests/test_lost_turn_wire_contract.py` reconciles the harness wire shape
`scripts/channel-serve.py` restates against the installed `codex` — what it emits on a
real lost turn, and what `codex app-server generate-json-schema` says it emits — and
`tests/e2e/test_lost_turn_wire_contract_e2e.py` drives that same lost turn through the
filter to a real channel. A memo keyed on this workspace would replay a green across
the very producer upgrade both exist to catch. No split may go
stale silently: an undeclared test that opens
this checkout's own documentation fails in `tests/conftest.py` and is told to carry
`@pytest.mark.reads_docs`, a `@pytest.mark.reads_recipes` test that opens
anything outside its narrower key fails the same way, and so does an unmarked test
that opens a registered checkout of another repository. See
[When a cached verdict may stand
in](docs/repo-lifecycle.md#when-a-cached-verdict-may-stand-in-for-a-verdict-on-this-tree).

A remote lifecycle change request's body is drafted by an agent graph the launch
names, the way its observer is: `just orchestrate` adds `--pr-author-graph
graphs/pr-author.yaml` beside `--dag-graph graphs/dag-scope.yaml`, and both ship
defaulted to nothing because the crate ships the flags and not the documents. One
post-verification turn reads the branch's diff and answers with the
template-shaped body, validated against `config/pr-author-body.schema.json` — the
`{body}` contract `onepipeline` reads out of `results[].structured.body`. A node
that states its own `body` publishes with that. Drafting never blocks publication
and never retries: a draft that cannot run warns on the node and the change
request opens with **no body**, which is also what a launch naming no drafting
graph does. Those two are not the same thing to read, and on the adopted onepipeline 0.8.1
they no longer look it: a drafting dispatch that was configured, attempted, and
produced nothing records `body-not-drafted` against the node with which of
`dispatch-failed` / `schema-refused` / `no-body` it was, and `just results` carries
that ending — while a launch that named no graph, and a node that carried its own
`body`, emit nothing, because neither spends a dispatch and neither is a fault.
**Three commands here draft, and they are the three that open a change request**: that
launch, `just publish-branch`, and `just repo-recover`. The two landing verbs go
through `scripts/land-branch.sh`, which reads the branch and `--repo` out of the
arguments, drafts through the same graph out of band, and appends `--body-file` to
what it forwards; an argument list it cannot read that way lands exactly as it did
before. Two escapes, in the order they win: a caller's own `--body` or `--body-file`
is forwarded untouched and spends no turn, and `--no-draft` skips drafting and is
consumed here rather than forwarded, because `onevcs` has no such option. It is the
escape for a bulk landing. **The turn is spent before the gate** — the body is an
argument to `onevcs` and the verb is what runs the gate, so a branch its gate then
rejects has paid for a body nothing used; that is accepted rather than overlooked, and
moving drafting behind the gate would be a different repository's design. `just
integrate` drafts nothing and needs nothing, because the local merge train opens no
change request. And **`onevcs recoverable`'s own printed `Resume:` line drafts
nothing**: it renders `onevcs publish-branch …`, which reaches the verb below the
wrapper and opens an empty description, so `just recoverable` re-renders each of those
commands in its `just` form and passes every other line — and the whole of `--json`,
whose `recover_command` other consumers read — through untouched. See
[Diff-derived PR
descriptions](docs/repo-lifecycle.md#diff-derived-pr-descriptions).

## Dogfooding rule

Use the orchestrator harness for **all tasks of sufficient complexity**, in any
repo or project. A **plan file launched by `just orchestrate`** is the only way to
dispatch: one subtask is a plan holding one node — one direct agent
(`examples/single-node-direct.plan.json`) or one lifecycle node
(`examples/single-node-lifecycle.plan.json`) — and a larger task is the same file
with more nodes. Nothing about plan schema, personas, or node semantics changes
with the node count, so a one-node run still gets a journal, an ownership row,
planner surfaces, and a place in the DAG UI. Lifecycle nodes clone the target,
work in an isolated worktree, verify with its gate, and publish. Dispatch smaller
project work with a single-node plan rather than doing it directly; only the
slight-tweak exception above applies. This repo is one
local-mode case of the same rule.

**Self-dispatch rule (this repo).** Never author working-tree changes in the shared
canonical checkout: concurrent orchestrators use it and direct edits race them.
Every change — including plans, personas, docs, and `AGENTS.md` — must be dispatched
into an isolated worktree cut from the registered `ai-orchestrator-isolated`
safety clone, with the canonical checkout retained as the node's `repo` publication
repository and only fast-forwarded after integration. Set `execution_checkout` on
each lifecycle node of the plan rather than passing a top-level flag.
This does not restrict the narrow direct git operations above on finished
dispatched work. Confirm `git config core.bare` is `false` before trusting a
self-dispatch result.

## Stack and composition

How this polyglot monorepo was built up from the create-repo reference pieces:

- **Product shape:** a configuration layer over four published CLIs, kept as an Nx
  workspace for its computation cache and its uniform target set. It remains
  closest to `shapes/skills-repo.md` (determinism-vs-judgment split,
  validate-in-gate, narrow allowlist), applied to onejudge configs + personas
  rather than skills. The engine, the lifecycle, and the browser view are no longer
  built here: `onepipeline`, `oneagentgraph`, `onevcs`, and `onepipeline-ui` own
  them, and the `just` recipes are thin wrappers over their verbs.
- **Language(s):** Bash for the recipes' wrappers, the harness routing, and
  provisioning; Python (uv, ruff, mypy, pytest) for the Nx `orchestrator` project —
  now the label contract, the redaction rule, and the suite that proves this
  layer; YAML, JSON, and TOML for configs.
- **Composed:** `base.md` (always) + `shapes/skills-repo.md` + `monorepo.md`. Nx
  provides the project graph, affected execution, and computation caching; the
  underlying language tools remain the source of each check.
- **Excluded, and why:** **CI** — deliberately, per the repo's charter: this is a
  local, private proof-of-concept ("local config/scripts/docs at this point"). The
  full gate still runs locally as `just gate` and at pre-push; add
  `.github/workflows/` mirroring it when this graduates past PoC. `releasing.md` —
  nothing versioned is published. asdf / direnv — the committed Bun and uv
  lockfiles already make the workspace reproducible.
- **Composed additionally:** the `llmlint` LLM-judge tier (`ci.md`'s companion) —
  `llmlint.yml` + the `lint-llm*` recipes, enforced at **pre-push**
  (`.githooks/pre-push`) since there is no CI.

## Invariants (non-negotiable)

- The gate is strict: format check, lint, type check, and tests all fail on
  issues — no warnings-only mode.
- **Coverage is enforced at 100% line coverage** on the `orchestrator/` package
  (`just test`); the gate fails below it. `[tool.coverage.report]` in
  `pyproject.toml` is the floor's one source — `fail_under` sets it and
  `precision` decides it, because pytest-cov compares the total *after* rounding
  at that precision. `tests/test_coverage_gate.py` holds that combination to one
  that can actually fail the build. The floor is 100 because what is left of the
  package is small and is all trust boundary: the label contract handed to a
  subprocess, the redaction rule `scripts/preserved-log.sh` mirrors, and the paths
  both read. `orchestrator:test` measures and judges nothing; the uncached
  `orchestrator:coverage` reads what it wrote and compares that total to the
  declared floor. Nothing else may name a floor, and a measuring tier that did not
  write its data fails that read rather than lowering the total silently.
- **The suite runs across four xdist workers** (`-n 4 --dist loadgroup`), chosen
  from measurement rather than from `auto`: it is latency-bound, its floor is its
  longest single test, and the curve is flat past four while this host also runs
  live dispatches. A test whose subject is a process-wide or machine-wide
  resource declares that as a scheduling constraint — never as a loosened
  assertion, and never as a per-run solo re-proof by hand. The two markers that
  used to carry those constraints went with the dispatch journeys that needed
  them; a test of that shape reintroduces the marker, its tier, and its reason
  together rather than weakening an assertion to survive a worker.
- **Tests are realistic, not mocked.** What this repository still owns is its
  command surface, so the suite drives the *real* `just` recipes, the real wrapper
  scripts, the real `oneharness` CLI, and real Nx. The published CLIs a recipe
  delegates to are doubled at that boundary and nothing above it: each engine is
  proven in its own repository, and a real `onepipeline start` here would launch
  agents. Never double a recipe, a wrapper script, or the shell they run in.
- Validate external inputs at trust boundaries: a persona is validated before
  dispatch (`just validate-personas`), and the label contract handed to a
  subprocess is validated in `orchestrator/labels.py` before it can reach one.
- Do not commit secrets or credentials. Harness credentials (e.g.
  `CLAUDE_CODE_OAUTH_TOKEN`) live in the environment, referenced by name; the
  agent allowlist in `.claude/settings.json` stays narrow.

## Tests are context engineering

This repo runs on agents, so the suite is the only QA loop.

- **e2e** (`tests/e2e/`) proves the real journeys against the real boundaries: the
  whole delegation table driven through the real recipes and wrapper scripts, the
  llmlint tier's cached verdict and its two judging paths, the Nx cache keys
  against real Nx in real linked worktrees, the harness wrapper against the real
  `oneharness` CLI and its fallback chain, and session setup installing the
  adopted releases for real from PyPI. Only the paid model and the published CLIs
  a recipe delegates to are doubled.
- **unit** (`tests/`) covers what this layer decides on its own: the label
  contract, the redaction rule, the coverage floor's enforceability, and the drift
  gates over the pins and the prose.
- A recipe is not done until a journey drives it end to end in `tests/e2e/`.

## Commits and merging

Squash-merge via PR is the intended model; PRs follow
`.github/pull_request_template.md` (terse **What** / **Why**). With no CI, the
**pre-push hook** (`.githooks/pre-push`, activated by `just bootstrap`) is the
enforcement point: it runs `just gate`, so nothing reaches the remote unproven.
Every dispatched agent must clear its own findings before committing. Branch
protection and a CI mirror of this gate are deferred with CI. Keep the
`.claude/settings.json` allowlist current: add a new routine command there instead
of re-approving it each session. Local-first is not local-only: keep the registered
base branch in sync with its origin, and push every change that reaches it immediately rather
than leaving verified work only in the local checkout. A dispatched `local` merge
already pushes to origin, and `just sync` fast-forwards a publication checkout to
what is on it. The pre-push
gate guards every push. Never force-push or rewrite history on the registered base.

`core.hooksPath` activates the whole directory, so that same `just bootstrap` step
also activates **`.githooks/commit-msg`**, which holds the *subject*. This
repository's product is its tracked source — config, personas, scripts, docs — so
every commit changes the deliverable, and the hook takes that to its conclusion: a
subject must be a Conventional Commit within onevcs's 120-character publication
limit, carrying a type this repository releases from (`feat`, `fix`, `perf`, or any
type marked breaking with `!`). A `docs:` or `chore(deps):` change to tracked source
merges green and then never cuts a release, which is what cost two changes in one
plan and was caught both times only by a person reading the title. The hook reads the
subject and nothing else — no index, no diff, no branch — because the adopted
**onevcs 0.7.0** puts the composed subject a publication is about to land under to
that repository's own `commit-msg` hook, where none of that exists, and one policy
must mean the same thing to both callers. It is 0.6.1 that started asking, and
re-measured here on the adopted 0.7.0 rather than carried forward: the same journeys
drive the same refusal on that release. **What 0.6.1 changed is *when* the
question is asked, not whether the hook runs**, and the difference is the whole
value: the disposable clone a publication works in is given the lender's
`core.hooksPath` (or its tracked `.githooks/`) when it is cut, so git has always run
this repository's hook on the squash commit a publication writes — from the far side
of a gate run and a merge, reported as `invalid input: git commit -m <subject>
failed`. From 0.6.1 it asks first, before anything is written, and refuses with
the branch, the subject, what the hook said, and `publish with an explicit title
that satisfies it`. `tests/e2e/test_publish_branch_e2e.py` holds both halves and was proven red
against 0.5.0 on that wording. Two things it is therefore *not*: `just integrate`
composes the train's subject through `provenance::publication_subject` rather than
the publication path, so it never asks; and a lifecycle dispatch publishes through
the onevcs onepipeline links (0.4.2), where the question reaches an operator only as
git's own refusal of the commit. Git's own generated subjects, autosquash
markers, and the `(incomplete step)` marker and its attestation are exempt: no
publication carries them.

Squash-merge is what a recovered incomplete step publishes too, so **every** path
that advances the base — lifecycle publication, `repo-recover`, and the `integrate`
train — leaves **one** commit on it: the `(incomplete step)` marker and its
`chore: attest verified recovery of preserved work` are branch state, and merging or
fast-forwarding the branch's provenance commits onto the base contradicts this
model. The attestation is not dropped — the publication commit's message ends with one
`Orchestrator-Recovered-Incomplete: <marker sha>` trailer per marker it recovered,
so the base still records that a step was left incomplete and a green gate cleared
it. A published subject names the change only; a marker's text never appears in
one. That `Orchestrator-` prefix is this host's, not the published default —
`config/onevcs.rules.yml`'s `trailer_prefix` is its one source, and a marker under a
prefix the rules file does not name is reported unrecognized and refused
publication rather than read or ignored. See [What the base branch carries for a
recovered incomplete
step](docs/repo-lifecycle.md#what-the-base-branch-carries-for-a-recovered-incomplete-step).
Provenance commits and marker-fragment subjects that already reached `main` stay
where they are: that history is never rewritten.

## After the main task

Act on two standing goals beyond the ask: (1) engineer the context for next time
(a real e2e for any journey a bug slipped through, a script for a step you did by
hand, a terse note here for what the code doesn't show); (2) keep the codebase and
environment clean and reproducible. Fold either in when it's the lowest-error path
to the ask; otherwise propose it as a follow-up. Skip busywork.
