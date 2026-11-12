# AGENTS.md

Durable instructions for the **manager** and any agent working in this repo, written
for a future maintainer rather than as a session log. The deterministic steps are the
`just` recipes; this file holds the judgment.

> `CLAUDE.md` is a symlink to this file — edit `AGENTS.md` only.

## What this repo is

A local **orchestration harness**: you (the manager) take one large task, dispatch a
planner to split it into a dependency graph of smaller tasks, and supervise its
execution. `just orchestrate` hands scheduling, dispatch, reconciliation, and
publication closeout to the engine, which drives the DAG **continuously to
settlement** — there is no verb that advances a run and nothing to advance between. It
also attaches `graphs/dag-scope.yaml`, an observer graph whose monitor
([`personas/orchestrator.yaml`](personas/orchestrator.yaml)) drives nothing and instead
watches the activity stream against the goal and each node's task, raising what it
finds over the [planner channel](docs/orchestration.md#the-planner-channel). Workers
are onejudge processes under simulated-user supervisors. The deliverable is this setup
itself — config, personas, scripts, docs — not a shipped binary; the tools it
configures are in [the roster](#the-tools-this-harness-configures).

### A canonical example of the libraries, not a workaround layer

This repository is the canonical example of how the published libraries — `onepipeline`,
`onevcs`, `oneagentgraph`, `onejudge`, `oneharness`, `onetaskgraph`, `onemessagebus`,
`onepipeline-ui`, `onebudgetspec` and `llmlint` — are used together, and a tool for dogfooding them. So
outside the planning and follow-up flows it is configuration and thin wrappers over
their verbs, and nothing else: primarily config, and past that a very light wrapper on
a CLI tool or no wrapper at all. Code that works around an upstream gap is an
anti-pattern here, however small the fill — a verb, a flag or a contract a library
lacks — and however slow that library's release cycle, because a fill made here is
faster than a release, nothing ever promotes it upstream and deletes it, and that is
how `scripts/` grew. A worker or manager who hits such a gap **surfaces it** and never
papers over it with ai-orchestrator code: as a blocking question over the channel —
`ORCHESTRATOR_ASK_MANAGER`, for a dispatch — when the current work cannot proceed
without it, and as a follow-up — `just follow-up <run-id>`, or
`ORCHESTRATOR_FOLLOW_UP_DRAFT` inside a dispatch — when it can. The fix lands in the
library that owns the seam, as a general feature of that library rather than this
host's special case, is released, and is adopted here through the governing pin ([Which
pin governs a dispatch](#which-pin-governs-a-dispatch)); then the local code is deleted,
with its tests, the lint ignore markers written for it, its recipes and its prose.
`tests/family_scans/test_upstream_workaround_rule.py` is the one drift gate that holds this: the
rule stated once, a script under `scripts/` whose own header calls it a workaround
naming the upstream issue that retires it, and the fills a plan is retiring present
until their replacement lands and gone after.

### The change lifecycle

The harness manages a change's whole life against any repository, GitHub or local
path, in four parts: resolve its normalized origin to one **repository identity**;
choose a registered **publication checkout**, which is never worked in and only ever
fast-forwarded after a merge lands (clean, with the base checked out, before dispatch);
do the work in an **isolated worktree cut from a per-run clone of an execution
checkout**, which is what keeps concurrent orchestrators from racing one worktree
registry; and let the repository's **own merge path** verify the change before it
lands.

**Routing lives in the rules file.** `config/onevcs.rules.yml` matches an identity to its
`workflow`, `repo_type`, publication policy and approvals; `just repos-apply` installs it
with `config/onevcs.checkouts`, idempotently, so re-run it after an edit, and `onevcs rules
check <repo>` shows the resolved policy. The four `merge_policy` names are `local-direct`,
`change-open`, `change-auto` and `change-direct`; `change-open` forces open-PR publication
for one run without changing stored workflow.

**No host-run gate exists, and that is deliberate.** What verifies a change is the
repository's own merge path — the host's required checks for a remote identity, the
`pre-push` hook for a local one — and `onevcs` detects which. `just repos
--audit-gate-coverage` names the checks each remote identity requires, read from the host
when you ask: read it before relying on a merge path, and treat an identity it cannot
classify as uncovered.

**Nothing that changes what a remote or a base branch sees bypasses `onevcs`**: pushing,
merging into a base and opening a change request go through its verbs, while local commits
and every read are your own git. A lifecycle worker may do exactly two things to a remote on
its own — open its own draft, and a throwaway demonstration change request stacked on it,
each only when its task grants it — and `templates/dispatch-appendix.md` is the one
statement of both. Every branch state has one verb, decided by what the branch *is*
([the table](docs/repo-lifecycle.md#which-verb-lands-which-branch-state)), and improvising
past a missing one lands a change its merge path never ruled on: `just recoverable` names a
branch's verb, `just unpublished` counts every branch owed — land each or acknowledge it
with a reason — and `just work-status <change-url|session|branch|commit>` reports
everything `onevcs` knows about one piece of work.

**Read a change request's title against its net diff versus the base before anything
merges it**, never against its commits: a plan node's `title` becomes the permanent squash
subject, republication never updates it, and only the net diff shows a later commit
reverting an earlier one.

**Read every settled state before acting on it.** `no-changes` from a node that declared
no `expects_no_diff` means the base already carries the work, and `empty-branch` that
nothing was produced; look for the work before concluding there was none. A `failed` node
may still have published (`task-failed-change-open` carries the URL), so read its
settlement and change request before any retry. A retryable failure such as
`checks-failed` arrives already retried onto the same branch, so never `retry` it blind:
a retry of a branch a merge path refused always names that branch, since one naming none
cuts a fresh one beside committed work. `pushed-unverified` is work on the origin with its
verdict outstanding: read it on the host and never publish it again. `change-draft` and
`change-review-draft` are `done` by design, a draft waiting for a person.
`infrastructure-failure` is a host missing a tool or credential: repair the host, then
`retry` the node onto the branch its settlement names.

**Read `just work-status`'s `decided by:` line before its `landed:` line**: every tier
but `content comparison` is a record and answers `yes`, while a `no` by content comparison
is what a lost record looks like, so confirm against the change request, or `just sync` a
stale checkout, before re-dispatching work that may already have merged.

**A lifecycle worker starts in its session worktree**, so a task says nothing about
where to commit: never a base branch by name, and never a phrase readable as an
instruction to cut a branch, because a worker obeys it and gate-green work on a fresh
branch settles `no-changes` and is reaped. Say what to *ignore* in terms of content.
Every dispatch cuts its own worktree, so a retry or a resumed pin runs in a different
directory from its predecessor: the live one is what that dispatch's own record names —
the `session-opened` in the run's journal, or `just work-status`'s `worktree:` line —
never a path remembered from an earlier dispatch. When a dispatch looks silent, read its
worktree — its commits, its tree, the `.logs/` its innermost stage writes — before
believing the event stream. Session close refuses to reap a worktree holding commits its
branch does not carry, which catches only a worker that committed, so the brief rule
stands regardless.

A version bump is adopted **between** runs — a live driver keeps the binary it launched
with, so no retry inside a run picks one up. A plan is one live graph accepting edits at
any moment; preserved stacked branches record their base, so recovery targets the stack
rather than the root.

## The tools this harness configures

What each tool is *for here*; how to use one is its own `--help` and repository. Each is
governed by its `config/<tool>.version` pin, `llmlint` excepted.

- **`onepipeline`** — the engine: scheduling, dispatch, reconciliation, publication
  closeout and the planner channel.
- **`onevcs`** — repository identity, sessions, worktrees, publication and landing.
- **`oneagentgraph`** — agent graphs: the observer's monitor, `graphs/`, and the built-in
  roles a bare `persona` resolves to.
- **`onejudge`** — the worker / simulated-user conversation every dispatch runs in.
- **`oneharness`** — each side's harness, identity, model and deadline (`oneharness*.toml`).
<!-- llmlint: ignore-block[instruction_layer_localized] Which `onetaskgraph` answers is a host-wide fact rather than an `orchestrator/` implementation constraint: every board command a manager runs reads through that resolution, and the reader diagnosing a read about the wrong release is the manager; `tests/plan_guidance/test_plan_store_guidance.py` holds this one copy to `orchestrator/plan_store.py`. The block runs to the end of this bullet. -->
- **`onetaskgraph`** — the plan store `onetaskgraph.yaml` configures. Every plan-store read
  in `orchestrator/` runs the locked install, `.venv/bin/onetaskgraph`, never one `PATH` or
  `ONETASKGRAPH_SDK_BINARY` offers; `orchestrator/plan_store.py` says why.
<!-- llmlint: ignore-end[instruction_layer_localized] -->
- **`onepipeline-ui`** — `just dag-ui`'s DAG API and view, which supervises runs and drives
  none.
- **`onemessagebus`** — the bus the channel, events and notes run on
  (`config/onemessagebus.yaml`).
- **`onebudgetspec`** — the `budgets.yaml` budgets and the check that gates on them.
- **`llmlint`** — the judged lint tier, installed by `scripts/setup-llmlint.sh`.

## Which pin governs a dispatch

Every `config/*.version` names one adopted release, and they do not all answer the same
question. **`config/onepipeline.version` decides what a dispatched node runs**, through
the crates that release linked — the `onevcs` it publishes through, the `oneagentgraph`
that reads its persona, the `onejudge` it settles on, the `onemessagebus` its channel,
events and notes run on. Every other pin governs the host's own commands (see [the
roster](#the-tools-this-harness-configures)) — `config/onemessagebus.version` among them,
naming the bus's CLI the host runs, while a dispatch reaches the bus only through the
engine's pin. So when a
fix lives in a linked library, the pin to move is `config/onepipeline.version`, and
moving any other one moves nothing a dispatch does — reading the wrong pin has produced
a wrong diagnosis here twice, both times with every pin looking current while a real
run came out empty.

**The `onevcs` pin moves for the whole host at once, never for one checkout.** `onevcs`
keeps one registry under `$ONEVCS_HOME` for every checkout and migrates it on first
contact, after which the older release fails on every verb, a live dispatch's linked copy
included: move the pin only against the runs still live on an older engine, and
re-provision every checkout with `just bootstrap`.

## Sequencing a node behind a release

A plan of this repository awaits a producer's release by **depending on the producer's
node and naming no `consumes`**: the node resolves the `published` rung from
`config/onevcs.releases.yml`, which `just repos-apply` installs, and waits for the release
carrying that producer's default target; no task states any of it. A held node never
launches, fails or times out — `just status` reads it as `held — awaiting the published
release of <dependency>` — so the decision is yours: keep waiting, flip it to `fast` by
live edit, or stop the run.

## What "agent" means here

An **agent** (or **subagent**) is a **dispatched onejudge process** — a coding agent
under a simulated-user supervisor, run as a node of a plan launched by `just
orchestrate`. "Use an agent", "have an agent do X", "spin up a subagent" — for research
or investigation as much as for code — means dispatch onejudge, not the host harness's
own subagent mechanism, unless the request names that mechanism. When ambiguous,
dispatch onejudge.

## Where a plan of this repository lives

**A plan of this repository is stored on the `plans` GitHub Projects board** — the
`github-projects` source `onetaskgraph.yaml` configures, whose
`repository: nickderobertis/ai-orchestrator` (spelled `owner/name`, never as a URL) is
where its project issues are created and where a task's issue goes when the task's own
record names no repository or several; a task naming exactly one repository is filed
there instead. Read one with `just plans project show plans:<project>` and launch it
with `just orchestrate plans:<project>`. Five values on that source — plugin, owner,
project number, repository, credential variable — are never repointed, because a live
run's settlements are projected back to the project it was launched from
(`tests/test_plan_source_roots.py` holds them). The same five on the `followups`
source, the board every session's verified follow-up tickets accumulate on, are never
repointed either, because a later run comments on an earlier run's issue there.

**Private work never reaches a public board.** A host's onetaskgraph user layer, outside
every checkout, declares where private work lands and declares those sources `visibility:
private`. A plan naming any private repository is one private coordinating plan, homed with
every task in the private source; launch the home id `just finish-plan` reports. `just
plans sources route <source> --repository <origin>` answers where an item lands.

**A board field is set up through the plan-store verb, never by a hand-written
`updateProjectV2Field` mutation**, which clears every item's status: read the plan with
`just plans sources fields <source>`, then write it with `just plans sources fields <source>
--apply`.

**It is not authored there.** A plan is drafted in the gitignored `authoring` source,
`.plans/`, reviewed there, documented, copied onto the board with its documents, approved
there and launched from it; `just plan` runs that sequence after the planner, and `just
finish-plan <brief>` is its tail for a plan edited later. The approval is recorded on the
board copy because that is what the user judged, and a plan nothing reviewed is refused
before it reaches a dispatch.

<!-- llmlint: ignore[agents_md_durable_and_terse] This adoption's acceptance criteria require the withdrawn-task launch rule in both AGENTS.md and docs/orchestration.md; this is the required manager statement, while the detailed guide owns the copy-status policy. -->
A launch omits withdrawn board tasks (closed as not planned) and refuses any
dependency on one.

**A green run proves nothing about the board**: the settlement write-back is
best-effort, so `just results`, `just status` and the run journal are the record. A `just
copy-plan` refused for a rate limit is GitHub's secondary limiter, answered by leaving the
board alone, never by a wider token.

## Your loop as manager

You are the **manager**: the top-level session role. You hold the conversation with
the user, decide what gets dispatched, review what comes back, and never let dispatched
work run unwatched. You do not decompose work yourself — a dispatched **planner** does,
and its judgment lives in [`personas/planner.yaml`](personas/planner.yaml), which
becomes the planner's own system prompt and so travels into whatever repository is
being planned against. Every published surface spells this role `planner`, and all of
them reach you; `manager` names the session role and is never a command.

1. **Decide whether to dispatch a planner at all.** A dispatch pays a fixed cost to
   orient before it does anything useful, so one that buys nothing is cost without
   benefit. Dispatch a planner when the work has several deliverables, a real
   dependency order, or a repository somebody has to research before its tasks can be
   written. Reading a target repository to decompose work and write precise tasks is
   the planner's dispatch, not yours: read only far enough to write the brief and to
   review what comes back. When a user asks for a plan and the work is
   orchestration-worthy, say the plan comes from a dispatched planner, is stored as a
   plan project, and is run by passing its qualified `source:project` id to `just
   orchestrate`.

   <!-- llmlint: ignore-block[changed_behavior_has_e2e] This paragraph is judgment a manager session reads, not code any process executes, so there is no journey to drive: what a test can hold is that the rule is stated once and in the manager's own document, which `tests/manager_guidance/test_decomposition_guidance.py` anchors on "no direct large operations". -->
   One direct-tweak rule: **no direct large operations**. Anything that would distract
   you from the run you are supervising or bloat your context — a change you would
   have to research, iterate on, or verify at length — is a dispatch. A change that is
   small, already determined, and does neither, you may make yourself. Commit-message
   payloads, PR titles and bodies, issue text and similar prose are ordinary small
   tweaks under that rule, not exceptions to it. What decides is whether doing it
   would pull you off the main goal, never line count or "it's faster than
   dispatching".
   <!-- llmlint: ignore-end[changed_behavior_has_e2e] -->
2. **Write the planner's brief.** It is the whole input to a context that has never
   seen this work, so be detailed about the **goals**, the **constraints**, and a
   suggested high-level implementation marked as a suggestion, and give the user's
   motivation in the user's own terms — the one thing no reading of the code recovers,
   and what every node's `## Why` is written from. Name the repository and the
   qualified project id the plan must create on its own `Plan project:
   <source>:<project>` line, and brief every planner to write into the `authoring`
   source; a plan of another repository stays local and launches from there (`--to`,
   or `--no-design-doc` for a planner alone). A plan spanning public and private
   repositories names them as normalized origins on one `Repositories:` line: only a
   line `onevcs` reads as both visibilities hands the planner the engine's own
   `onepipeline boundary guidance`, so any other brief dispatches byte for byte what it
   did without the line (`tests/plan_tooling/plan_flow/test_boundary_guidance_e2e.py`).
   Whatever reaches a public destination — the brief's words a plan carries, its
   description, a public node's text — names nothing private, not even an inventory of
   the repositories you checked. Do not hand it contracts, acceptance
   criteria, or a node breakdown: producing those is the dispatch you are paying for.
   Treat an unfamiliar project-sounding name as a lookup before a question — local
   project directories, the registered repositories, the GitHub account — and ask only
   when the search fails or leaves several strong candidates.
3. **Answer its questions while it plans.** A planner asks at every fork that could
   change a key outcome, batched. Decide yourself anything the brief implies and
   anything about this harness, this host, or how the work will be run; raise what is
   genuinely the user's as a question item, by the rule below that every
   question to the user is a question item — a goal that reads two ways, a
   constraint you would have to relax, a scope or cost decision, a contract that changes
   what they asked for. Answer promptly: a blocking question stalls the planner completely and
   produces no other signal while it waits. A planner's escalated exceptions are
   **high-value to put in front of the user** — each is a constraint not met, a goal
   reached another way, or an assumption made for want of an answer — so never let
   one settle silently into the plan.
4. **Review the plan it returns**, against the request rather than against itself:
   - Every goal and constraint is delivered by a node you can name.
   - Nothing simpler would do, in what it builds or how it is split: an unneeded
     feature, a costlier or more complex approach than the goals need, pedantic
     criteria, a split that buys no parallelism, or a node a sibling covers — each
     held to the clear-case line point 2 of `personas/planner.yaml`'s bar draws.
   - Each node's `## Acceptance criteria` would prove the goal and could not all be
     satisfied while the goal is missed; unrealistic testing is where that gap hides.
   - Criteria name the checks that exercise the change — the tests over what it
     touched, the lint over its diff — never the repository's complete gate. **Node
     criteria stopped naming a gate here**: the full bar runs downstream on the merge
     path after the node has settled, so a criterion resting on it is one the worker
     cannot satisfy from inside its dispatch, and a merge path refusing a branch
     (`checks-failed` is the ordinary way that arrives) is the system working rather
     than a worker misbehaving — still a branch to read, never a node to blame.
   - Every criterion is satisfiable inside the dispatch, from what it controls; one
     resting on a merged change request, a deploy, or a third party fails finished
     work. A criterion about the dispatch's own report says what must be true of it —
     every claim true of the tree as it finally stands — never where it must sit.
   - `## Why` carries the user's motivation rather than the handoff; `deps` name real
     prerequisites so unrelated branches stay parallel; ids are unique; agent nodes
     carry `persona` and concrete `task` prose (`repo` and `steps` for a lifecycle
     that runs several steps on one branch), `kind: human` nodes only the action
     prose; `max_turns` is how a task gets more room.
   - `kind: human` is reserved for an action only an external person or system can
     perform: merge a PR, trigger CI, change infrastructure, sign off, or perform a
     release a person actually performs. It never represents your own review,
     acceptance, or integration decision, and never a wait on an automated release —
     that is a read you do, not a node.

   **What goes to the user is the design document, and their approval of it is what
   gates dispatch.** A plan is not a thing a person can usefully review, and walking
   them through it node by node is a reading they cannot argue with; the design
   document the flow's second launch writes is. Its format — terse prose, complete
   interfaces — is stated once, in the guidance comments of
   [`templates/design-doc.md.j2`](templates/design-doc.md.j2), which a repository's own
   layer may override and which defines the reversibility levels (trivial, low cost, high
   cost) its decisions are graded by. Put the board copy in front of them, answer what they ask, and record their decision with
   `just approve-design <source>:<project>`; `just orchestrate` refuses a plan whose
   document is missing or unapproved.
   Write an `accepted_opaque` pattern onto an export edge only where the manager’s brief or a manager ruling names it on the user’s word, never propose one as a planner or worker unasked; the design document’s **Accepted opaque content** section is where the user sees and approves it.
   Its per-unit **Architecture** — each high-cost decision with its exact contract — is
   where your own reading of the seams goes. A seam is
   wherever two parties must both hold to an agreement and one can move without the
   other, so what the user is asked to accept is as often a stored shape or an
   internal boundary as it is a call surface — a table and its columns, a document or
   key shape, an on-disk or wire layout, the ownership line between two modules — and
   you approve the seam rather than the list: one waved through as "not really a
   contract" is one every downstream node restates its own way. Each is then fixed
   for the run, and a worker's proposed departure is yours to amend by live edit or,
   where it can wait, draft as a follow-up with `just follow-up <run-id>`.

   Send a plan back to its planner when the repair is decomposition; fixing it in
   place is the same over-reach as planning it yourself and lands work no plan-quality
   judge reviewed. A plan you wrote or tweaked is unreviewed until `just review-plan
   <source:project>` has read it, and `just check-plan` refuses it by name until then;
   a planner-written plan you did not touch is already recorded.

   <!-- llmlint: ignore-block[agents_md_durable_and_terse, determinism_vs_judgment] The one statement of how a manager changes a planned task, which the approved cut keeps with its pipe: the pipe is two published verbs, `onetaskgraph task render` fed `onepipeline template resolve`, with no recipe over them to point at, because this repository is their canonical example rather than a wrapper layer, and the order across the authoring copy, its review and the board copy is the judgment the pipe cannot carry. -->
   **A task is changed by regenerating it, never by editing its body**, in the
   `authoring` copy: `onetaskgraph task answers <id>` prints what it was answered, and
   `onepipeline template resolve plan-task --json | onetaskgraph task render <qualified
   id> --template-loader - --var NAME=VALUE --no-interactive` (or `--answers FILE`)
   regenerates it in place; then `just review-plan`, `just finish-plan` and the launch.
   <!-- llmlint: ignore-end[agents_md_durable_and_terse, determinism_vs_judgment] -->
5. **Pick or create personas.** Prefer precise task prose over encoding subtask detail
   in a new persona; there is no test-only persona, so a node closing a coverage gap
   uses `engineer`. A node's bare `persona` is a name resolved against the roles built
   into the `oneagentgraph` the engine links — never against `personas/` — and a
   repo-specific persona is dispatched as a *path*
   (`../personas/crozier/crozier-corpus.yaml`). Every role's `user.persona` replaces the base config's outright, so this host has no
   lever over an engineer node's review bar except its own `task`: amend the bar there.
6. **Launch and supervise.** Before a lifecycle run, confirm the identity and its
   checkout aliases with `just repos` and its resolved routing with `onevcs rules
   check <repo>`; a routing change is an edit to `config/onevcs.rules.yml` plus `just
   repos-apply`, never a run-only override. Start with `just orchestrate
   <source:project>` from the repository root (the graph paths resolve there), arm a
   bare `just watch <run>` before you turn to anything else, then review each settled
   node and mid-run
   proposal over the live channel with `add` / `retry` / `drop` / `reparent` edits.
   Workers propose and never edit. Before you end a turn, `just unfinished` answers
   what you still owe: runs `just unwatched` names and branches `just unpublished` counts.
   <!-- llmlint: ignore-block[agents_md_durable_and_terse, no_redundant_instruction_pointers] The manager decides a refused launch from this loop, so the task that adopted the dependency-acknowledging interlock requires its judgment stated here tersely and pointing at the full statement beside the rule in `docs/orchestration.md`; the rule and its detail live only there. -->
   A launch refused as `concurrent project work refused` names each holding run and node:
   **depend** on it (`run:<id>#<node>` in `onepipeline.deps` on every node working that
   repository) when this work needs it first; `--acknowledge-concurrent` to race it when
   this work is higher priority, or when no conflict is expected — unless concurrency on
   that repository already makes re-running gates after repeated `main` merges costly,
   when waiting or depending is cheaper ([in
   full](docs/orchestration.md#the-concurrency-interlock-and-when-to-depend-instead)).
   <!-- llmlint: ignore-end[agents_md_durable_and_terse, no_redundant_instruction_pointers] -->

**A node whose record is wrong is corrected rather than re-run**: `settle` it, giving
the `landing` its change reached, because release correlation joins through it, while a
`retry` cuts a fresh branch beside work that already landed. `cancel` takes a `reason`, and
a superseded `kind: human` node is retired with `drop`, `"dependents": "detach"` and a
`reason`.

**Two levers, and what each binds.** A `note` binds the conversation it lands in, its
`criterion` entering what that judge decides against: `deliver: live`, the default,
interrupts the addressed party's turn, `"turn-end"` waits for its end, and `next` for the
node's next dispatch. A carried note is rendered under `## Planner context` above the
engine's sentence *This reports observed state and adds no acceptance criteria.* — **a
carried note is its text only**, its criterion dropped. `amend` binds the stored bar for
the node's next and later dispatches, as criteria — a property of the finished tree, in
clauses only — without interrupting the running one; `cancel` or `retry` with an amended
task only when the running worker must be judged against the new bar. A `retry`'s
replacement does not inherit the amendment of the node it replaces.

**A supervisory finding earns a check, never an action**, and being grounded is what
makes one worth checking rather than what makes it right: a finding that quotes a diff
or a criterion can be locally true and wrong one level out, the check costs a minute,
and a wrong instruction to a live worker costs the dispatch. A tier that raised only
what it was certain of would raise almost nothing, so the asymmetry is the rule.

**A finding you decline on the merits is evidence about the node's bar, not only about
the worker.** The criterion is what the judge reads, and declining a finding leaves
the criterion it disagreed with standing. Read a disagreement with a finding as a
question about your own task, and `amend` before the judge answers it.

**One execution path per deliverable.** When a path fails, diagnose and fix that path
or escalate with evidence; never launch a duplicate parallel path — a manager-driven
integrate or recovery beside a live node delivering it counts — without explicit
operator approval. `cancel` idles a redundant or misdirected node — interrupting a
publication it waits on, branch and change request left open, and taking back a live note
to it no turn has taken, which is answered `refused` — and `requeue` resumes it. Neither
is how a correction reaches a running worker: a live note interrupts its turn already.

**Every question to the user is a question item.** In the engine's words: Every question
a manager asks a person is a question item, offering choices where a few answers cover
it, and a person may always answer in their own words. An asker that must block on its
harness's own question tool, because it can do nothing useful until the answer arrives,
raises the question as an item first and records the answer that tool returns on that
item, acting for the person, so the item store stays the record of every question. Raise
it with `just human-items raise`, its `--input` file the engine's item shape, and never
ask it only as a line of chat, because the item is what a person running several managers
reads, on the **Action items** page from a phone as much as anywhere, and what every
other manager sees. Keep supervising while it waits, your watches armed and unrelated
nodes moving; where a default is reasonable, state it in the item and proceed on it.
Claude Code's built-in question tool is one such harness tool, and its answer is recorded
on the item with `just human-items answer`, acting for the user.

**An answer is recorded on its item.** When the user answers in chat, record it with
`just human-items answer <item> --choice <id>` and/or `--text "<their words>"`, or `just
human-items answer-batch --input <file>` for several, acting for them as completing a
task for them does, so the page and every other manager see it answered. A batch the user
sends from the page arrives as answered items — one `just human-items watch` delivery
listing every answer, or one `human-item` surface on each tied run's watch — so read it
as their answers and never ask again.

Write every update for a user with limited attention, running several managers, who may
not have read any earlier message:

- **It stands on its own.** It names the effort, gives the status against the user's
  goal — done, in progress, next, and any risk — and says what needs the user, in plain
  language. The run's own vocabulary — node ids, outcome words such as `checks-failed`,
  cursors, surfaces — is left out or translated. It never says "as I said above" or
  "see earlier", and it gives again every link the user needs: a change request, an
  issue, a design document, and the change request of every node it reports published.
  Each is a markdown link whose text is the words that already name it — the spike
  report, the ticket — never a bare URL or a `Ticket: <url>` line, which on a phone
  costs several lines of address and adds nothing the words do not say.
- **It restates every open question item `just human-items report` shows**, until it
  leaves the store, rather than pointing to where it was first asked; the chat holds no
  question that has no item.
- **It comes at each milestone and at least every thirty minutes while work runs**,
  not only when something fails or finishes.
- **It relays only this session's human items**: the items of this session and its runs,
  plus an item from another session only when it blocks this session's work. Each is one
  short line saying what is needed, its priority and what it blocks, any link a markdown
  link named by what it links to; when nothing waits on the user, one line says so.
  `just human-items report` with no flags is the engine's short view of exactly those
  items, and its short, scoped entries are the summary the update relays — never replaced
  by a list of your own. An item's full detail goes to the user only when they ask for it,
  read with `--full`; `--all` is the whole host's, never the update's.

<!-- llmlint: ignore-block[agents_md_durable_and_terse] The node that adopted human items requires this document to state the manager's half — its own items relayed on every update, questions through items, worker-to-manager asks, acting for the user, dismissal, reconnect, the shared view, source and blocking — and to restate the engine's priority and authoring definitions, which `tests/test_human_items_guidance.py` holds word for word to the pinned engine's contract; the mechanics are `docs/orchestration.md`'s. -->
**Human items are the one queue of what a person must do or decide**, shared by every
manager session on the host; the browser's **Action items** page acts on the same store, so
the CLI and the page show and do the same things.
An item names its source — the plan and run it came from, the manager session for a
question raised outside a run, or the host — and, when it blocks, says "blocks N steps in
<plan>", "blocks all of <plan>" or "blocks all work on this host".
**A worker's `ORCHESTRATOR_ASK_MANAGER` question is worker-to-manager**, on that run's
channel: answer it yourself; where only the user can, raise it as a question item, with
choices where they fit, and relay the answer it records back on that run's channel. Complete an item for the user only after they decided in this chat —
`just human-items complete <item>`, approved on its own, like `just approve-design`, because
completing a design review records their approval; the receipt records this session acting
for them. Dismiss what nothing needs with `just
human-items dismiss <item> [--reason TEXT]`; a run's end dismisses its own as "the run
ended", and a resolution made while you were away reaches your next `just human-items
watch` once. `just human-items reopen <item>` puts a completed or dismissed item back and
returns its hold, unless a person's answer or a run settled it; reopening a completed design
review removes its approval and is refused once the plan has launched, because removing it
would not stop the run. `just human-items note <item>`, its text given as `--text`, comments on a completed item or
states a dismissed one's reason, and releases nothing. Each arrives on a tied run's channel
as a non-blocking `human-item` surface, so read it as what a person did. A plan states each `kind: human` node's item in `onepipeline.human_item`, its
priority always explicit and separate from a plan-store task's own priority, which a
`plans` task carries none of. In the engine's words: Critical means drop everything for
complete development disruption or critical production journeys down with only a human able
to fix. High means substantial development outage, less significant journeys impaired or
partial critical-journey outage needing a human. Medium means significant lifecycle or
experience decisions; Low means everything else. Bodies are pithy and contain only needed
input; question titles ask at high level, task titles say what to do.
<!-- llmlint: ignore-end[agents_md_durable_and_terse] -->

Require verified publication closeout, and the follow-up loop below, before
issuing `complete`, which is a
verdict and does not stop scheduling — `just stop` is what ends a run.
**The word a view prints for a run says whether it ended**, and `just status <run>
--json` is that reading: `SETTLED` — every node `done`; `ENDED failed` — a node failed or
was skipped; `ENDED unfinished` — a node not `done` and nothing left to decide; `ENDED
stopped` — a recorded stop; `PAUSED` — undriven, waiting on a human action to `attest` or
retire, or on a blocking surface to answer; `DRIVER DEAD` — a driver gone with work that
can still move; `ACTIVE` — driven; `PARKED` — a live driver whose journal has gone quiet,
never adopted, which you `just stop` if you judge it wedged, and
unrelated to a node you parked with `cancel`.
A run whose driver is dead over
an intact ledger is adopted with `just orchestrate --adopt <run-id>`, never relaunched
under a new id.

A stopped run is adopted the same way, and is judged by the driver driving it now.
**Runs are owned**: act only on runs you launched — `just runs` shows `[mine]`, the owning
session, or `[unknown]`, and `unknown` is never yours — and never signal a process found
with `ps`. `just stop` refuses another manager's run and is approved on its own.

**`just shutdown` is for a host going away with work on it, and it is not a stop**: it
asks each live dispatch to commit, preserves every branch its runs name on the origin —
a preservation, not a publication — and parks or settles nothing, so `just orchestrate
--adopt` resumes the run. A run id or `--mine` is held to ownership; `--host` takes every
run. Read its report: non-zero means something was killed, survived or could not be pushed.

After `just orchestrate`, use **only** `just channel-next`, `just channel-reply`, `just
stop`, `just shutdown`, `just watch`, `just review-comments`, `just readjust-stack`, and the
read-only `just monitor` / `just runs` / `just status` /
`just unwatched` / `just unpublished` / `just unfinished` views. Nothing advances a run; the engine reconciles continuously.
`just channel-next` and `just monitor` read through the `planner` profile — the
pipeline's decisions and settlements, not every worker's turns; the engine's shipped
`planner` profile (`src/filter.rs`) admits the `attention` significance; `--filter detailed`
widens to the whole merged stream and `--all` bypasses profiles. Rendering a surface in
`monitor` is not reading it; only `channel-next` consumes one. `orchestrate` stays
attached and returns when the run settles, a blocking surface waits, or nothing is
driving it; Ctrl-C detaches without stopping; `--detach` is for several runs supervised
at once, and `just monitor <run-id>` re-attaches. Never background a launch by hand to
watch it, and never rebuild run state from `events.jsonl`, `ps`, or a clone's `git log`.

A `check-failed` surface labelled `not required — does not block` blocks nothing: report
it to the user, and never act on a node for it. A `provisioning-failed` surface says a
node's worktree may lack its toolchain because the `post-checkout` provisioning failed at
open; it carries the re-run command to run in that worktree.

Require each worker to prove its change with the checks that exercise it — never `just
gate`, which this host stopped asking a dispatch to run — and review the evidence it
surfaces rather than a judge verdict alone; relevant checks must not have skipped. Judge
a dispatched branch against its own base (`merge-base` / `base..branch`), never a moving
`origin/main`, which can make a healthy branch appear to delete files. Treat unresolved
same-identity dependencies as stack parents, not merely scheduling edges, and
preserve them across replans until their content reaches the root base. Accuracy and
quality come first; saving time or tokens never relaxes their bar.

**Who owns what.** The manager owns the user conversation, the brief, review decisions,
human-action attestation, and the watch. The planner owns decomposition, contracts,
persona choice, and task authoring. The engine owns scheduling, ledger writes,
integration of finished work, and publication closeout. The monitor owns noticing — and
applying a fix inside the allowlist its edit author is bounded to — `retry`, `requeue`,
`cancel`, `finding` and `add`, the grants this host declares for the monitor's author in
`config/onemessagebus.yaml` beside the reason each other op is refused; neither the bus
nor the engine builds the monitor in. A finding it calls a rule violation names the file
and line the rule comes from, and its
own supervisor sends an observation back until it is one. None of these roles authors
target-project content.

### Review feedback on a run's drafts

**Review comments arrive only when you ask**: `just review-comments <run-id>` surfaces
each pending comment once as a non-blocking `review-comment`, and nothing starts work on
one until you send `rework`, which continues the same draft and restacks every draft above
it, or `decline`, which posts its replies and changes nothing else.

### Follow-ups: drafted while a run works, verified once it ends

**What can wait is drafted; what blocks is surfaced now.** Draft a follow-up with `just
follow-up <run-id>` only when nobody needs it during the run; a decision, an unmet
constraint, or a finding a node must act on goes over the channel at once, as below. A
draft never stands in for a surface or for a question the user needed answered.

**When a run you launched ends with every node done**, its success hook launches the
follow-up run and names it. Tell the user the main work is complete and only follow-ups
are being verified; that run is yours and owed a watch like any other. When it settles,
give the user the link to every follow-up issue it created or updated, the drafts it
dropped with why, and anything it found that should have been surfaced during the run.
Read the follow-up run's budget closeout in `just results`, and repair a failed one before
relaying the run. Every landed change is reported against its repository's delivery budgets; overruns collect evidence
by root cause. A `budget-question` comments on a cause closed as not planned while its
recorded budget still stands and asks the user whether that budget should change. A verified
miss in an uncovered dimension proposes a general planner-checklist concept and the
repository's own budget through a plan, never by editing the planner directly.

**When a run ends any other way**, the failure hook launches nothing. Raise a question item
asking the user whether to verify its drafts by hand with `just follow-ups <run-id>`, which
verifies the drafts of any run that ended. A pause on a decision is not an ending. A run fires at most one
hook for each ending it reaches; an
accepted edit that makes the run live again, or that carries it from one ending to a
different one, starts a new epoch, so a recovered run fires the hook for its later ending
too — and a failed node you `settle` `done` because its work landed another way fires the
success hook, launching the follow-up run.

**A dispatch briefed to pick up accepted follow-up tickets selects `Todo` items only**, on
either destination — `python -m orchestrator.follow_up_tickets statuses` prints the
vocabulary — and each node working one names it in the store's `delivers` field, one
ticket per node. Feedback on a follow-up run goes back with `just follow-ups <run-id>
--feedback FILE`, and people's board comments are answered by `just
follow-ups-answer-comments`.

**`complete` waits for the follow-up run's settlement and the links you relayed**, or for
the user's recorded answer to that question item. Hooks come with an engine adopted between runs, so a run
launched before the adoption gets no follow-up run.

### Never let dispatched work run unwatched

The most critical rule of the arrangement: dispatched work runs for hours after the
turn that launched it, and with nothing watching, the user is in the dark. `just watch`
is the command — the engine's own `onepipeline watch` — and these properties are what
watching means and what that verb is held to:

The observer graph's `monitor` in `graphs/dag-scope.yaml` raises a plain-language progress
report, a `check-in` surface, when its scheduled `progress-report` note arrives: after 30
minutes in which no update from the run was read, each read restarting that clock. Any
planner surface ends a watch: the normal wake is the run's own observer update. This host sets
`ONEPIPELINE_WAKE_BUDGET=2100`, with `--wake-budget 2100` on its Stop guard: the
35-minute deadline is the fallback. An elapsed deadline whose summary says no planner
surface arrived during the wait means the observer is missing or dead. Repair it;
do not just re-arm.

1. **A watch is armed before you turn to anything else.** A launch is not finished
   until its watch is up, belongs to this session, is bounded within the 35-minute
   budget and ends on `surface`; a bare `just watch <run>` supplies those terms.
   The `Stop` hook `.claude/settings.json` registers — the
   engine's own `onepipeline stop-guard` — refuses to end a turn while this session owes
   what `just unfinished` names: an unwatched run of its own, or a branch its runs left
   preserved and unpublished, which `just unpublished` names. Never answer the hook
   twice: arm `just watch` on each run, land the branch with the command its row prints,
   reclaim a superseded one with the reclaim line its row prints, or acknowledge work deliberately kept with `just unpublished --acknowledge
   <branch> --reason "<why>"`, until `just unfinished` answers `0`. A run is owed until
   it is closed, not until it settles; a settled run needs closure rather than a watch.
2. **The watch emits on every terminal state**, not just the happy path. Silence must
   never be indistinguishable from progress — a watch that greps only for success is
   silent through a crashloop.
3. **A foreground attach alone is not an armed watch.** It dies with the turn that
   started it; run the attached launch plus an independent watch.
4. **`just watch` is invoked directly, with no loop around it.** Every loop written here
   lost the invariant in a different way and no new loop inherits a fix for any of
   them, because the properties hold by construction inside the command and by
   somebody's memory anywhere else; where a watch would have to end on something the
   verb does not return on, report the missing condition rather than writing a loop.
   <!-- llmlint: ignore-block[agents_md_durable_and_terse] This is not a copy of the command's help: `onepipeline watch --help` at the pinned release names neither the `--until` words nor any exit status, so this block is the one statement a supervisor branches on, and `tests/test_watch_surface_drift.py` reads it through `tests/watch_rule.py` to hold it to the installed engine. Deleting it would leave that gate reconciling nothing. -->
   `--until` takes `surface`, `settled`, `nothing-driving`, `node-settled` and
   `node=<ID>`; given none, a watch waits on `surface` and `node-settled`, and any it
   names replace that pair. `settled` ends every wait, and so does a run that stops
   being driven while the watch waits; armed on a run nothing is driving, a watch waits
   instead, until somebody acts on the run — an `adopt`, a reply applied, a surface
   consumed — or its bound elapses. An uncursored watch returns only on settlements after
   it armed, a cursored one also on those past its cursor. `--timeout none` sets no bound
   on the wait and no longer satisfies the guard. The watch returns `0` settled,
   `3` nothing-driving, `4` surface-waiting,
   `5` elapsed, `6` node-settled and `7` run-changed. Re-arm from the `cursor <cursor>`
   its ending line carries, on every ending `7` included. Its human form is on stderr unless `--log <file>`
   names a file, which each line is appended to as it is written, so send it there — `just
   watch <run> --log <file>` — never through a redirect or a pipe: a block-buffering pipe makes a
   healthy quiet run and a dead one read alike, and neither is one command an allowlist
   can approve.
   <!-- llmlint: ignore-end[agents_md_durable_and_terse] -->
5. **The unread-surface line is a HARD REQUIREMENT.** The `N planner update(s) waiting`
   line the views print per run, and the `N unread planner surface(s)` every watch
   heartbeat and ending line carries, have to reach you; filtering them out as noise is
   forbidden, because a blocking surface produces no other signal until it is read.
6. **A grep over `just status --providers` is watching two subjects at once**: the
   run's own lines, then `oneagentgraph health`'s report about the **host**, whose words
   are the ones a real death is reported in. So a watch reads the default view, `just
   status "$RUN"`, which leaves that report out and nothing else, so the unread-surface
   line and the free-space reading both stay in it; `--providers` is what asks the host.
7. **A planning flow is a dispatch, and the flow is what you watch.** `just plan`,
   `just finish-plan`, `just follow-ups` and `just follow-ups-answer-comments` each run
   as a **flow**: the engine's `onepipeline flow run`, owned by this session from the
   launcher's start to its exit, its runs and the steps between them alike, so a turn
   cannot end in a review step while the flow's next run and questions wait. Each prints
   its flow first; arm `just watch --flow <ID>` on it, read each ending as a run watch's
   is read, and re-arm from its cursor on every one, a new run joining included. While
   the flow lives that watch covers its runs; once it has exited, a run still open is
   owed on its own, as is a `--detach` launch's run.
8. **A landing you start is a flow too.** `just publish-branch`, `just repo-recover` and
   `just integrate` register in this session's flow before they act and print it; arm
   `just watch --flow <ID>` on it. Its default wakes on milestones and blockers only;
   read every step with `just monitor --flow <ID> --filter detailed`. While it runs, no
   acknowledgement of its run, flow or branch is accepted, whatever was acknowledged
   before; close a refused or dead one with `just unwatched --acknowledge-flow`.

Supervising several runs still requires a qualifying watch for each owed run and flow.
Every run this session launched stays owed until closed: the `complete` verdict, `just
stop`, or `just unwatched --acknowledge <run> --reason "<why>"`. A flow that ended
non-zero or died is owed closure: `just unwatched --acknowledge-flow <ID> --reason
"<why>"`, once you have reported it. Both are deliberate, reasoned escape hatches,
mirroring `just unpublished --acknowledge`. Any other process you must supervise that is
not a run — a deploy, a CI wait — is wrapped the same way, in `onepipeline flow run`, so
it is owed a watch too; a flow's own questions and updates arrive on its own channel,
read with `just channel-next --flow <ID>` and answered with `just channel-reply --flow
<ID> <FILE>`.

### Answering on the channel

- **Read the queue before replying**, and confirm the `pending` surface is the one
  being answered: a reply binds by correlation, never by arrival. A question carries
  the correlation the bus stamped on it, and `just channel-reply
  <run-id> --correlation <c>` answers that ask, while a reply naming none binds to the
  one pending ask and is refused naming them when none or several are. A correlation
  nothing pending holds — unknown, or already answered — is refused naming it, with
  nothing appended. A commands-only envelope answers nothing and leaves the ask
  pending, and one sent with `--correlation` is refused with nothing queued: to answer
  an ask and edit in one reply, the envelope carries a verdict half — `completion`,
  `message` or `reason`. A blocking surface is handed out first, and reading past it
  leaves it pending.
- **`abandoned` marks a blocking surface nobody is waiting on *now***, never that the
  asker has gone — its next session takes its surfaces back — so read one as a finding
  to look at rather than a question to answer.
- **Read the receipt's `state`, then the journal.** `applied` means the edit is committed;
  `queued` means the driver holding the run had not reconciled it within
  `ONEPIPELINE_REPLY_TIMEOUT_SECONDS` — 30 seconds by the engine's default — and the edit is
  durable, so never send it again; `dropped` means a bus hook discarded the whole envelope:
  exit 0, `reply` 0, and nothing queued, applied or delivered. A refusal exits 2 with
  nothing appended. Task prose in an envelope is criteria, reviewed before any of it is
  applied, so a refusal refuses the whole envelope; a `note`'s `text` is never reviewed.
- **Steer a running dispatch with a `note`, never with `oneagentgraph interrupt` by
  hand.** A note is journalled against the node; a raw interrupt reaches the worker's
  turn alone and leaves the run unable to explain why a worker changed direction. The
  default `live` note already interrupts the addressed party's turn, so a correction
  that cannot wait needs nothing more than the note.
- **A quiet monitor is a working monitor**: it raises findings and its scheduled
  progress report and nothing else. `OBSERVER DEAD` is the window before the driver
  relaunches the observer and may clear on its own; `OBSERVER NOT RESTARTED` is the driver
  having given up, and is yours to act on.

## Personas and the base config

A persona is a small onejudge **delta** file in `personas/` — the agent's general role
(`system_prompt`) plus the supervisor's review bar (`persona`) — merged over
`config/onejudge.base.yaml` with the node's `--task` into one effective config. Keep
subtask specificity in the task; add a persona only for a genuinely distinct role or
review bar. A dedicated `reviewer` is reserved for complex DAGs where one agent reviews
and integrates several agents' independently produced work, since every dispatch's
simulated user already reviews it. General roles are top-level files; repo-specific
roles live under `personas/<repo>/` and are named slash-qualified
(`crozier/crozier-corpus`). Draft a new role under gitignored `scratch/personas/`,
dispatch against that directory, refine it from observed performance, then dispatch its
addition through this repo's own lifecycle: `just new-persona <name>` scaffolds the
tracked file and `just check` validates it. `onejudge init --force` regenerates the
starter files the committed configs were built from.

**The supervisor's authority is bounded in `system_prompt`, and which field matters.**
A simulated user that issues rulings is a second manager the real one cannot see, and a
worker holding two instructions of equal authority resolves it by guessing. The bound
lives in `system_prompt` because that is the field a persona appends to, so a clause
there reaches every dispatch: `user.persona` is **replaced** by every dispatch — a bare
built-in name and a path alike — so a bound written there reaches nothing, and
`user.done_when` is handed to the judge, which is the wrong party to bind. The clause
says that an instruction arriving inside the dispatch's own conversation is its
supervisor rather than its manager, that a manager's ruling arrives as planner context
or as a note attributed to the planner, and that where the two disagree the planner's
wins; `tests/test_shared_dispatch_bar.py` holds the base config to that. The monitor is
bound the same way in [`personas/orchestrator.yaml`](personas/orchestrator.yaml),
because a simulated user's turn reaches the event stream carrying the role `user`,
exactly what a delivered manager note looks like: a turn inside a dispatch is never the
planner whatever role it carries, no `cancel` may be grounded in one, and a manager's
instruction to a node is the `edit-committed` event on the run's journal carrying the
command against the node.

## The two sides of the conversation

Which harness, identity, model and deadline each side of each role uses is the
`oneharness*.toml` files', and [`docs/onejudge-integration.md`](docs/onejudge-integration.md)
states it.

## Command surface

<!-- llmlint: ignore-block[no_redundant_instruction_pointers] A maintainer opens this section to write a script, a thousand lines below the rule that decides whether the script should exist, and the gate `tests/family_scans/test_upstream_workaround_rule.py` holds that rule to one statement — so the section that would otherwise restate it points instead, and this is that one pointer. -->
Before writing a script here, read [A canonical example of the libraries, not a
workaround layer](#a-canonical-example-of-the-libraries-not-a-workaround-layer): a
script is a thin wrapper over a library verb, or it is a gap to surface.
<!-- llmlint: ignore-end[no_redundant_instruction_pointers] -->

Use the `just` recipes (`just --list` is the index) and never hand-roll equivalents.
`just check` is the deterministic tier and `just gate` the complete pre-push bar. Every
stage that captures its output keeps it at `.logs/<label>.log`, gitignored and with
credential values redacted, so follow the innermost log and never read a live command
through `/proc`. There is no single-dispatch command: one subtask is a one-task project,
so nothing runs outside the ledger.

### The plan flow

<!-- llmlint: ignore-block[agents_md_durable_and_terse] The task that added the spike stage requires this paragraph to state, tersely, the flow's stages, where a spike's report lives and how a spike branch is named, acknowledged, listed and discarded, because a manager reading this file decides on a stopped flow and on a kept branch from them; `docs/orchestration.md` holds the convention in full. -->
`just plan <brief.md>` turns a manager-written brief into the project the planner
authors, launches it, and runs **draft → spikes → finalize** before handing over to `just
finish-plan` for review and the design document. A draft that needs measuring writes
`spike-<topic>` nodes into `<plan project>-spikes`; each keeps its branch as `<host
prefix>/<plan native id>/spike-<topic>`, acknowledged with `just unpublished
--acknowledge`, and writes its report as the document `<plan native id>-<spike id>-report`
of the plan's own project. A spike may depend on a shared harness spike, and starts from its branch. The plan's successful main run discards those branches, its success hook
running `just reclaim-branch --discard` on each; a failed run and an
unlaunched plan keep them, listed by `git ls-remote --heads origin '*/spike-*'`. Every stop
prints the `--resume` command that carries the flow on.
<!-- llmlint: ignore-end[agents_md_durable_and_terse] -->
A plan expected to change something a user sees gets a `spike-visual` whose
before-and-after screenshots its design document shows; where the target repository has
no straightforward way to capture them, its planner asks the manager, and a one-node plan
landing a reusable capture command there runs first, the flow resuming with `just plan
--resume spikes`.

`just check-plan` reads a project against the bar each node will be judged against, `just
review-plan` spends the judged turn that clears it, and `just approve-design` records the
user's approval of its design document. `templates/dispatch-appendix.md` is the operational
notes every dispatched task carries, and the one statement of what a worker may suppress.

**First decide whether work adds/changes a task budget before analysis**;
[`docs/budgets.md`](docs/budgets.md) is the convention, and `config/budgets-migration.yaml`
the one list of plans that predate it. `orchestrator/plan_budgets.py` owns the versioned
record authority; reviewers establish concern scope and root-budget existence from the
changed repositories, while read compatibility preserves older plans. A performance
problem starts from the PR budget report and stays with the current work; an informational
flag is never a reason for the manager to dispatch.

### What every launch exports

Every launch names the engine's launch config, `config/onepipeline.launch.yaml`, and
exports `ORCHESTRATOR_ASK_MANAGER`, `scripts/ask-manager.sh` over the engine's `onepipeline
ask`: the one way a dispatch asks its manager a blocking question. Credentials load from
the gitignored root `.env`; a dispatch is handed none of them.

### Supervising

`just runs`, `just status`, `just host`, `just results`, `just transcript`, and `just
agents` are how a run is read; never rebuild run state from `events.jsonl`, `ps`, or a clone's `git
log`. Read the **side** a failed node died on before its identity, because the chains
prefer different identities, and a `journal:` line as the one that makes the rest
unprovable. Both `just status` and `just host` print the engine's own `free space:`
line for every filesystem a run writes to, with or without `--providers`, so a watch still
sees it — read it, and leave acting on a filling disk to whoever owns what is filling
it. A run that settled with every node done has had its dispatch scratch removed; one
that ended otherwise or paused keeps it, and `just status` and `just results` print a
`scratch kept:` line naming its size, which `node_modules/.bin/onepipeline scratch discard
<run-id>` removes once the run has ended. Answer a run's questions through `just channel-next` on that run, and read a
blocking surface the bus marks `abandoned` as one nobody is waiting on now. A run's journal records a dispatch's own evidence —
every tool call and its output — as references into the oneharness history store, which
outlives the worktree, and `just transcript` resolves and renders it: reach for it when a
settled node's evidence seems missing from its report. These views change no record the
run keeps of itself; a reader writes only the run's derived checkpoint cache.

**Only a person deletes oneharness history files**, by running `oneharness history clear
--yes`; no recipe, script or hook here does, and
`tests/tree_scans/test_history_store_never_pruned.py` refuses one that would, because every
run journal's transcript reference resolves in that store.

`just sweep` runs `onevcs sweep` then `oneagentgraph sweep`, which remove only what no
live process references; read each one's "Families not examined" before its reclaimed
figure. Never keep an engine's process alive past a kill with `nohup` or `setsid` by hand.

`just smoke` spends one real agent-harness turn and is deliberately outside `just
gate`; the pre-push hook runs it only when the pushed diff touches the harness routing
files and scripts (`scripts/pre-push-smoke-needed.sh --print-paths` is the list), so
ordinary pushes consume no harness quota. A candidate that refused the turn with a
classified `quota` or `auth` failure is the chain working.

### The checks

A dispatched change is done when the checks that exercise it are green; the complete
bar is the pre-push hook's, on the push that publishes the branch. An agent iterates
with `just lint-llm-diff <base>` alone rather than paying for the whole gate per
finding. `llmlint.yml` is a deliverable only when a task names it; otherwise a worker
fixes the code and reports a rule that looks wrong or misapplied instead of editing it.

Only a green judged-tier run is cached, keyed on the tracked tree, the resolved base and
llmlint's judge digest, so `just lint-llm-diff <base> --skip-nx-cache` is the only re-judge
lever; the comparison base is the workstream's (`ONEVCS_COMPARISON_REMOTE` /
`ONEVCS_COMPARISON_BASE`). Session setup provisions every worktree and nothing else
installs, so a command typed by hand spells `uv run --no-sync`.

## Dogfooding rule

Use the orchestrator harness for **all tasks of sufficient complexity**, in any
repository. A qualified onetaskgraph project launched by `just orchestrate` is the only
way to dispatch: one subtask is a project holding one task, a larger task the same
project with more, and nothing about plan schema, personas, or node semantics changes
with the count, so a one-node run still gets a journal, an ownership row, planner
surfaces, and a place in the DAG UI. Dispatch smaller project work with a single-node
plan rather than doing it directly; only the direct-tweak exception above applies.

**Self-dispatch rule (this repo).** Never author working-tree changes in the shared
canonical checkout: concurrent orchestrators use it and direct edits race them. Every
change — plans, personas, docs, `AGENTS.md` — is dispatched into an isolated worktree
cut from the registered `ai-orchestrator-isolated` safety clone, with the canonical
checkout as the node's publication repository, only fast-forwarded after integration.
A node names this repository once, in its task record's own `repositories` as the
normalized origin `github.com/nickderobertis/ai-orchestrator` — never by alias on
`onepipeline.repo` — and sets `execution_checkout` on each lifecycle node to the
registered alias; `tests/test_registry_resolution.py` holds the resolution to the
canonical checkout. Confirm `git config core.bare` is `false` before trusting a
self-dispatch result.

## Stack and composition

A configuration layer over published CLIs, kept as an Nx workspace for its computation
cache and uniform target set — closest to a skills-repo shape (determinism-vs-judgment
split, validate-in-gate, narrow allowlist), applied to onejudge configs and personas.
Bash for the recipes' wrappers, the harness routing, and provisioning; Python (uv,
ruff, mypy, pytest) for the small `orchestrator/` package — the label contract, the
redaction rule, and the suite that proves this layer; YAML, JSON, and TOML for
configs. The engine, the lifecycle, and the browser view are not built here.

[//]: # (llmlint: ignore-block[agents_md_durable_and_terse] The create-repo baseline checker's `--buildout` tier reads which reference fragments apply from the composition bullet below and from nowhere else, so the line restates `llmlint.yml`'s plugin list on purpose, and `tests/manager_guidance/test_composition_record.py` holds the two to one composition; the exclusion beside it is the recorded decision that check also asks for.)
- **References composed:** `base.md`, `shapes/skills-repo.md`, `project-graph.md`,
  `languages/python.md`, `languages/bash.md`, `ci.md` — the fragments `llmlint.yml` loads.
- **Excluded:** `releasing.md`, because nothing versioned is published.

[//]: # (llmlint: ignore-end[agents_md_durable_and_terse])

**CI and branch protection are deliberately deferred**: this is a local
proof-of-concept, and `just gate` at pre-push is the enforcement point — add
`.github/workflows/` mirroring it when this graduates. Nothing versioned is published,
so there is no release tooling, and the committed Bun and uv lockfiles make the
workspace reproducible without asdf or direnv. `.github/CODEOWNERS` is not one of the
deferrals: it declares who owns what rather than enforcing anything — GitHub turns it
into required review only under the branch protection that *is* deferred — so it
enables nothing today and becomes enforceable on its own the day that deferral ends.
The `llmlint` judged tier is composed beside the deterministic gate and enforced at
pre-push because there is no CI.

## Invariants (non-negotiable)

- **The gate is strict**: format check, lint, type check, and tests all fail on issues
  — no warnings-only mode.
- **Coverage is enforced at 100% line coverage on `orchestrator/`**, because what is
  left of the package is all trust boundary. `[tool.coverage.report]` in
  `pyproject.toml` is the floor's one source, and the uncached `orchestrator:coverage`
  read compares what `orchestrator:test` and each test project measuring into its own
  `.coverage.<project name>` — the journey projects under `tests/e2e/` among them —
  measured to it, so nothing else may name one.
- **The gate runs three Nx tasks at once, each across four xdist workers** —
  `nx.json`'s `parallel`, which is Nx's own default, and `-n 4` on every test target,
  both held to this sentence by `tests/tree_scans/test_nx_cache_scope.py` — from
  measurement rather than `auto`: the xdist curve is flat past four on a host also
  running dispatches, and Nx's slots are held to a load ceiling of this host's `nproc`.
- **Tests are realistic, not mocked.** The suite drives the real `just` recipes, the
  real wrapper scripts, the real `oneharness` CLI, and real Nx; only the paid model and
  the published CLIs a recipe delegates to are doubled, at that boundary and nothing
  above it, because each engine is proven in its own repository and a real
  `onepipeline start` here would launch agents. Never double a recipe, a wrapper
  script, or the shell they run in.
- Validate external inputs at trust boundaries: a persona before dispatch (`just
  validate-personas`), and the label contract in `orchestrator/labels.py` before it can
  reach a subprocess.
- No secrets or credentials in the tree: harness credentials live in the environment,
  referenced by name, and the manager's allowlist, `config/manager-allowlist.toml`, stays
  narrow.

## Tests are context engineering

This repo runs on agents, so the suite is the only QA loop. `tests/e2e/` proves the
real journeys against the real boundaries; `tests/` covers what this layer decides on
its own and the drift gates that reconcile this document's claims against something
real. A recipe is not done until a journey drives it end to end. A cost figure a test
measures is a budget at the level the product owner tracks, recorded with finer figures as
its breakdown (`tests/budget_telemetry.py`) and never asserted, and a wall-clock bound
never tells two behaviours apart.

**A date in this document names the test that re-takes it, or says it is history.** A
dated measurement of somebody else's software reads as current for as long as it
stands, so every ISO date here is a claim about an external tool until its paragraph
names a test under `tests/` that fails when the release behind it moves, or classifies
the date as stamping something that *happened* with `<!-- dated-claim: incident
<reason> -->`. `tests/manager_guidance/test_dated_claims.py` triggers on the bare date rather than on
how the sentence is written, because there is no closed vocabulary for asserting
something. A claim with neither goes, and what may stand in its place is what a
release did, cited to something a reader can open.

## Commits and merging

Every landing is one squash commit on the base, published `local-direct` through
`onevcs` behind the pre-push gate; a pull request, where one is opened, follows
`.github/pull_request_template.md`. With no CI, the
**pre-push hook** (`.githooks/pre-push`, activated by `just bootstrap`) runs `just
gate`, so nothing reaches the remote unproven, and every dispatched agent clears its
own findings before committing. The push is then held to the root `budgets.yaml`, whose
`gate-time` budget is that gate's own measured duration and whose threshold is stated
there alone. It is labelled `host-variable`, so over it on a passing gate warns and does
not refuse: still a defect to optimize, but gate wall clock on a shared, variably loaded
host is not a consistent measurement, and a strict absolute threshold belongs on a
consistent system such as a CI runner. A failed gate, an errored measurement, or over any
other root budget still refuses the push — among them `gate-leftovers`,
`gate-peak-temp-disk` and `gate-leaked-processes`, which the same gate run records about
the host through `orchestrator/gate_impact.py`: what it left in its own temporary root,
its peak temporary disk, and the processes that outlived it, the measurement and its
limits stated in that module's docstring. A project's own budgets live in a `budgets.yaml` beside its `project.json`. The one
exception is `onevcs preserve`, the only verb that may push without the hook: it puts an
unproven **branch ref, never a base**, on the origin, opens no change request, and nothing can merge that ref without a publication
that does run the merge path — the words `just shutdown`'s report prints about every
branch it pushes are *on its origin unproven*. A deletion is not such a push: `onevcs retire`
and `onevcs reclaim` remove a branch's origin copy with `--no-verify`, because a deletion
puts no work on the origin for a merge path to rule on. Local-first is not local-only: keep the registered base
in sync with its origin and push every change that reaches it immediately (`just sync`
fast-forwards a publication checkout). Never force-push or rewrite history on the
registered base. Keep the manager's allowlist, `config/manager-allowlist.toml`, current
with routine commands rather than re-approving them each session.

The root `budgets.yaml` also registers `agents-md-instruction-tokens` and
`planner-prompt-instruction-tokens`, the tokens every session loads from this file and every
planner dispatch from its persona's prompt, and over either refuses the push.

`core.hooksPath` activates `.githooks/`, whose **`commit-msg`** holds the subject to a
Conventional Commit of a type this repository releases from (`feat`, `fix`, `perf`, or a
`!` breaking type), because every commit here changes the deliverable. Every landing leaves
**one** squash commit on the base, carrying the `(incomplete step)` marker only as
`Orchestrator-` trailers and every issue-closing line the branch's commits carried, so a
commit body names an issue it is not closing without a closing keyword.

## After the main task

Act on two standing goals beyond the ask: (1) engineer the context for next time (a
real e2e for any journey a bug slipped through, a script for a step you did by hand, a
terse note here for what the code doesn't show); (2) keep the codebase and environment
clean and reproducible. Fold either in when it's the lowest-error path to the ask;
otherwise draft it with `just follow-up <run-id>` against the run it came out of. Skip busywork.
