<!-- llmlint: ignore-file[contracts_have_one_source_or_a_drift_gate] Operator-facing wire examples are required here; the published `onepipeline` channel remains authoritative and its exact contract is exercised by that crate's own tests. -->
<!-- llmlint: ignore-file[no_redundant_instruction_pointers] Live cancellation can preserve incomplete commits; the recovery command must link its safety contract at the point of use. -->

# Tracked graph orchestration

`just orchestrate <source:project>` turns one large task into one recorded hierarchical
DAG. It is the **only** way to dispatch direct agent work, full repository
lifecycles, and explicit actions that only a person can complete: a single dispatch
is a project holding one task, so every piece of running work has a journal, an
ownership row, surfaces, and a place in the DAG UI. The engine drives that DAG
**continuously to settlement** — it schedules, dispatches, reconciles, and settles
on its own, with no verb to advance it and nothing to advance between. See
`examples:scheduler-research` and `examples:health-endpoint`.

Plans authored through this repository's configured plan store are read with
`just plans <onetaskgraph arguments>`. For example, `just plans project list
--allow-partial` lists the local Markdown projects even when the configured GitHub
Projects credential is absent, while `just plans project show
plans:<project>` reads one plan of this repository. The recipe always invokes
the pinned standalone `onetaskgraph` CLI against `onetaskgraph.yaml`; its source
diagnostics name a failed source and the credential or correction it needs. `just orchestrate
<source:project>` names that project directly; no export or intermediate plan file
is part of the launch. On the adopted engine, each node settlement is
written back to that source project, and live graph edits add, remove, or update its
tasks and dependency edges there. A run launched from GitHub Projects therefore
reports its changing graph and outcomes on the board itself; the run journal remains
the detailed execution record. The `plans` recipe is the operator's direct store
surface, while `orchestrate` launches the tracked run and keeps its source plan current.

## The plan store this repository plans against

**A plan of this repository is stored on the `plans` GitHub Projects board.** It held
one plan at a time until 2026-08-29, and the local Markdown store this repository retreated
to in the meantime is gone: the two defects that forced it were repaired upstream and
adopted here as onetaskgraph 0.2.12 — a release this host has since moved past — and,
in the engine that carries the write-back repair and every release since,
onepipeline 0.44.4. The whole of that reasoning —
the defects, the releases, what was measured against the real board, and why the retreat
was undone by deleting a source rather than repointing this one — is recorded once, in
[Where a plan of this repository
lives](../AGENTS.md#where-a-plan-of-this-repository-lives). Everything below describes the
source this repository now plans against.

The `plans` source is one GitHub Projects board, and since the redesigned
`github-projects` source **a board is a container of projects rather than a project**: a
project is an issue, its tasks are that issue's sub-issues, and the board's own title is
never read as a project. Creating an issue needs a repository and a board has none of its
own, so that source names one — `repository: nickderobertis/ai-orchestrator` in
`onetaskgraph.yaml`, spelled GitHub's own `owner/name` way — and it is where every
project and task issue a copy or a write-back creates is filed. A write with no
repository named is refused naming the field, so the field and
`config/onetaskgraph.version` move together: this source has named its repository since
https://github.com/nickderobertis/onetaskgraph/pull/56, which is the change request that
made a board a container of projects, and a release before it refuses that field as
unknown while every release since refuses the write without it. Reads never need
it, which is why a board can still be listed by a checkout that names none.
`tests/e2e/test_onetaskgraph_host_e2e.py` drives both halves against a board fixture —
a copy of a local project files its issues in the configured repository, and a
repository the credential cannot reach refuses the copy before anything is created.

### How a plan gets onto that board

**A plan is drafted locally, cleared locally, documented, copied up, approved and
launched from the board** — and `just plan` performs everything down to the approval:

```sh
just plan brief.md                       # the planner authors into `authoring`, its
                                         # closeout records what it wrote, and the tail
                                         # then reviews the plan, checks it, launches the
                                         # design document, copies both onto `plans`, and
                                         # reports where that board holds them
just approve-design plans:<project>      # the user's approval of the copy they read
just orchestrate plans:<project>
```

**The approval is a manager's own step, and it is recorded against the copy the user
read.** The flow ends by reporting where the destination holds the plan and the design
document it has just copied there; that copy is what goes in front of the user, and `just
approve-design` records their decision on it. Recording one against the local draft
instead would attach an approval to a different artifact from the one they judged, which
is why the copy comes before the approval rather than after it.

**`just finish-plan brief.md` is that tail on its own**, and is what an operator runs
after editing a plan the planner authored: the edit leaves that task carrying no review
record for what it now says, so the review spends a real judged turn on it before anything
else happens. It is the same five steps in the same order, because `just plan` reaches
them by running that script rather than by repeating them.

**The ordering is the point, and the second launch is what buys it.** A design document
describes a plan, so one written before anything reviewed that plan describes content
nobody read — which is the failure the review gate exists to prevent one step earlier.
A run cannot interject a review between its own nodes: a review record is written by this
repository's own code and never by a dispatched agent, because a worker runs in a worktree
and nothing it writes below the plan root is tracked; and a `kind: human` node is reserved
for an action an external person performs rather than for a manager's own validation. So
the document is a **second launch**, of a one-node project whose planning stamp names that
one node — which is what keeps its own exemption from the design-approval gate bounded to
the launch that writes a document rather than granted to the plan.

**Both of this flow's run ids are decided before either launch is made.** The tail's run
is the flow's own name plus `-design`, and `just plan` refuses it as taken before it
dispatches a planner: an hour of planning must not end at a name collision. Each id is
printed beside `just channel-next <id>`, so a supervisor holding only that output can
reach either run's channel.

**Its refusals are told apart by exit status**, because each names a different thing to
correct and a caller scripting the flow branches on the status: **1** is the review
refusing the plan's own criteria, **3** is the pre-launch check refusing the plan, **4** is
the document launch not settling, **5** is the destination refusing the copy, and **2** is
a flow that could not run at all. There is no repair loop between them — the planner's own
judge is the repair loop and it has already run — so a refusal hands every refused
criterion back and stops.

`--to` names the destination and defaults to `plans`; `--no-design-doc` stops the flow
after the planner, copying nothing and reporting no location, because a plan on the board
with no document can never be approved and so can never be launched — the review a plan
still gets under it is `just plan`'s own closeout, and `just finish-plan --no-design-doc`
reaches no step at all and says so. A `--detach`ed `just plan` hands back before the plan
exists, so it keeps the planner alone and its one receipt line carries the `just
finish-plan` command that finishes it.

**That order is forced rather than preferred, and the reason is where a review record
lives.** A record is one entry of the task's *own Markdown document*, so
`orchestrator/plan_store.py`'s `WRITABLE_PLUGIN` names the local Markdown plugin and
every other source is read-only to the writer: a board is not a directory, and `just
review-plan` on a plan held there refuses before it spends a turn, naming the plugin.
`just check-plan` then refuses that plan for carrying no review record. **Both refusals are correct and neither
is routed around** — a plan nothing has reviewed is how a plan written under time
pressure reaches a dispatch — so the board is a destination and never a drafting
surface. An author who starts there has no way out but to start again somewhere else.

**`just copy-plan` is the step that makes that order a command rather than a habit.** It
takes one qualified project id and copies that project and the tasks in it into the
`plans` source, or into whichever configured source `--to` names. Before it writes
anything to the destination it reads the plan and refuses it when any task carries no
review record for that task's *current* authored content, naming each such task and the
command that records one. It composes rather than replaces: it spends no judged turn,
reviews nothing itself, and changes neither of the two commands beside it.

Three properties of it are worth knowing before you build on it.

*It cannot disagree with `just check-plan` about what has been reviewed.* The pre-flight
calls `orchestrator/plan_review.py`'s own `unreviewed`, which is the function the plan
check calls, rather than restating how a record is keyed. That key covers the task's
authored content *and* the bar it was granted under, so a second implementation would
agree on the day it was written and diverge the first time either moved.

*Its refusals are told apart by exit status,* because a builder that read one as the
other would retry "nothing has reviewed this" as an outage of the board. **Exit 1** is
this command's own refusal, made before the store is asked to do anything; **exit 3** is
the destination refusing a plan every task of which carried a record; **exit 2** is a
plan, a review bar, or a store CLI that could not be read at all, so nothing was judged
and nothing was written.

*Everything it does not recognise reaches `onetaskgraph project copy` untouched* —
`--dry-run`, `--recreate`, `--match-by <KEY>` — rather than being re-declared by a
wrapper with no opinion about them. One has a reach worth knowing: a `--set` among them
configures the **copy** and not the pre-flight read, which the store makes through this
checkout's own configuration and environment. Repoint a source in `onetaskgraph.yaml` or
through the store's own `ONETASKGRAPH_` variables, both of which the whole command sees.

The review record travels with the copy, because it is an ordinary entry of the task's
metadata map and the copy carries that map; so `just check-plan plans:<project>` on what
landed accepts it and spends no second judged turn. The design approval below travels the
same way, one record further out — but in this flow it never has to, because it is
recorded *after* the copy rather than before it. What the copy has to carry is the
**document**: the store's own `project copy` carries none, and a plan copied without its
design document arrives on the board with nothing for a person to read and so nothing to
approve.
`tests/plan_tooling/test_copy_plan_recipe_e2e.py` drives the copy itself for real —
drafting, clearing, a trial run that writes nothing, the copy, the record read back off
what landed, and the check over it — against a second local store rather than the live
board. `tests/plan_tooling/test_plan_flow_e2e.py` drives the whole of `just plan` the same
way, and `tests/plan_tooling/test_finish_plan_recipe_e2e.py` drives the tail on its own
together with each of the refusals above.

**Where the destination holds what landed is read back out of it rather than composed.** A
destination decides its own native ids and where its records live — a board mints a number
where a directory keeps the name — so the two locations the flow reports are found through
the origin stamp the store writes onto every record it creates by copying, and rendered
from the `location` that store answers with. `orchestrator/plan_locations.py` is that
read, and it refuses rather than guessing where a destination holds no copy of the plan,
holds two, or holds anything other than exactly one design document for it.

### Approving the design document a plan is read as

**A launch is refused until the user has approved the document the plan is read as.**
That document is what a person can actually judge; the plan itself is not, and a
node-by-node walk through it in front of somebody is a reading of the graph rather than a
review of it. `just approve-design <source>:<project>` records that they approved one,
and `just orchestrate` refuses a project carrying no such record before anything is
dispatched.

*What is recorded, and where.* One entry of the design document's own metadata map,
holding a digest and the moment it was written. It goes onto the document in the plan
store rather than into a file beside the plan, which is what makes it readable from
whichever store the plan is held in — a directory of Markdown or a board. It is *written*
through `onetaskgraph document copy` rather than by editing a file, and that is the half
that makes the sentence above true of a board as well as of a directory: the write goes
through the store's own write side, so where a record can be written is the store's answer
rather than this repository's.

**Being writable on a board is what lets the approval be recorded against the copy a
person actually read.** The flow copies the plan and its document into the destination and
reports where that destination holds them; the user reads the document there, and the
approval goes onto that record rather than onto the draft it was copied from. The record
does also travel with `just copy-plan`, the way a review record travels with a task, so an
approval survives a later copy onward — but nothing in this flow depends on that, because
nothing here is approved before it is copied. That is the difference from the plan-review
record beside it, which is an entry of a task's own Markdown document and can therefore
only ever be written into a directory.

Two consequences of that route are worth knowing before reading a record:
the store rewrites `onetaskgraph.origin` to name the source the write was staged from,
because that key is its own bookkeeping of the last copy; and the write replaces the
record whole, so it stages every field the store just reported rather than the ones this
repository cares about.

*What invalidates one.* The digest covers the document's own authored content — its title
and its prose — **and** the tracked template that says what a design document is. So
editing the document after it was approved leaves it unapproved, and changing
`config/design-doc-template.md` leaves every previously approved document unapproved,
exactly as moving the plan-review bar invalidates every review record granted under the
previous one.

**Nothing the store the document sits in owns is in the digest**, and that is what makes
the travel above real rather than only carried. The origin rewrite is the obvious one: it
is the store's own bookkeeping, so it does not invalidate the record in the act of writing
it. The one that cost a plan is less obvious, and it is **which project the document
belongs to** — the store's own local identifier for the plan, which is exactly what a copy
changes. The same document is a document of `some-plan` where it was drafted and of an
opaque board identifier once it is on the board, so while the digest covered it the record
travelled intact and then no longer matched what the destination computed: a copied plan
was refused for want of an approval it was carrying. What that costs, and it is the same
thing `just review-plan`'s own key costs one record up, is that a document **moved to
another plan** after its approval keeps that approval — what a person approved is the
document, and it is unchanged.

*What the launch refuses, and how it says which.* Two refusals, told apart because they
owe different next actions: a project holding **no** design document is waiting on the
document being written, and one whose document carries **no approval for what it
currently says** is waiting on a person reading it. Each names the project, the second
names the document and where the store says it is, and both name `just approve-design`.
A project holding more than one document is a third answer — which of them is the design
document cannot be decided, so it is refused rather than guessed at.

*The one exemption, and it is a **launch** rather than a project.* A planning launch is
exempt, because its output *is* the plan and the document it will be reviewed as does not
exist yet. **This flow makes two of them**, and one rule covers both: the planner that
writes the plan, and the one-node launch that writes the document — which is the launch
producing the very thing an approval would be recorded against, so it can no more wait on
one than the planner can. Each is exempt because the launcher that generated its project
stamped that project as a planning project — `scripts/plan.sh` for the planner,
`scripts/finish-plan.sh` for the document — and the gate reads what the project says about
itself. Never because anything recognises its shape: a hand-written project carrying a
node of either shape is not exempt.

That stamp alone decided it once, and it was a hole: the plan a planner writes is stored
in the planning project the launch created, so the plan's own nodes inherited an exemption
granted to the project for life, and launching them dispatched real work with the design
document approved by nobody. So the exemption is bounded by both halves of what it was
written for — **the project holds exactly the nodes that launch dispatches**, which each
launcher names on the stamp it writes, **and it holds no design document**, since
once one exists there is something a person can read. Either half ending ends it, and the
refusal says which one did rather than reading as the gate mis-firing on a planning
project. *Exactly* is an agreement rather than a covering, and both directions of it end
the exemption: a task the stamp does not claim is a project that has grown past its
launch, and a claim the project does not hold describes a launch this is not — a project
that would otherwise grow into that claim while staying exempt. A stamp naming one node
twice is read as no claim at all, since no launch dispatches a node twice. A project
stamped by a `just plan` from before the stamp named its nodes bounds nothing and is
gated like any other.

*Being exempt is not the same silence as being approved.* A launch that dispatched on the
exemption says so on stderr, naming the nodes it is bounded to; a launch that dispatched
on an approval says nothing. They were one answer while the exemption was a property of
the project, which is why the hole above was invisible from the launch's own side.

*What it is not.* It is not `just check-plan` and not `just review-plan`, and neither of
those changes for it. Those ask whether a node's acceptance criteria would fail its
worker for something other than its work — a machine tier, run against the plan's tasks.
This is a person's decision about one document. Folding either into the other would let a
plan nobody read past pass because a machine liked its wording.

`tests/plan_tooling/test_approve_design_recipe_e2e.py` drives it end to end: a real
launch refused for want of an approval, the real recipe recording one, the same launch
then reaching the engine, an edit to the document refusing it again, a planning launch
reaching the engine with no document at all — and that same planning project refused once
it holds the plan a planner wrote into it, or the document a design-doc dispatch stored in
it.

## What the write-back owns, and what a green run proves

**What the write-back owns is the node projection, and nothing else on that record.**
It is a projection rather than a rewrite: before it builds anything it reads the
destination with `project show <project> --json`, and the shadow project it then
copies over carries **that read's own description** — so a body somebody authored on
the board survives every settlement of every run launched from it, re-read on the
adopted onepipeline 0.44.4. Below the 0.16.3 that fixed it, it did not: the shadow
was built with the body hardcoded to an empty string
and the copy that follows is a total replacement by contract, so every destination
faithfully propagated the deletion, on a local Markdown project and a GitHub Projects
board alike. Task bodies survived and only the project description was lost, which is
what let it reach a release.
`tests/e2e/test_onetaskgraph_host_e2e.py` is what holds it here, and it holds it as a
*comparison* — the description read back through `just plans` against the one read
before the run — because an assertion that the description is merely present cannot
fail on a deletion.

**That read refuses rather than defaults**, which is the half worth knowing before
trusting a projection. A destination read that exits non-zero, answers unparseable
JSON, reports partial results, finds no project, or answers with the wrong or a
duplicate project ends the projection there: nothing is written, and the destination
is left exactly as it was. A read that could fall back to a default is a read that can
delete — the shadow would carry an empty description and the copy is a total
replacement — so this is the property that makes the preservation above worth
anything, and it is held here rather than described.
`tests/e2e/test_onetaskgraph_host_e2e.py` launches a real run through `just
orchestrate` with `ONETASKGRAPH_BIN` pointed at `tests/e2e/stub_onetaskgraph.py`,
which hands every call to the real store CLI and injects one `errors` entry into the
write-back's own destination read. The run settles `complete`, and the injector's log
is what says the rest: the launch's plan read went through, the write-back's read was
refused, **no `project copy` was ever reached**, and the project record is byte-for-byte
what it was. Both halves are asserted because either alone passes for the wrong
reason — an untouched record is exactly what a run that never projected at all leaves
behind. Re-read on the adopted onepipeline 0.44.4; below the 0.16.3 that added that
read, all three fail at once — the release beneath it performs no destination read at
all, copies three times, and leaves the record with an empty body.

**A projection the store refuses is reported once and waits for the graph; any other
failure is spaced out.** onepipeline https://github.com/nickderobertis/onepipeline/pull/176
backs a failing write-back off from a prompt first retry to a one-minute ceiling instead of
retrying about four times a second, resets that schedule once it recovers, and retries until
the projection lands; stopping or settling stays prompt during a long backoff. Since
https://github.com/nickderobertis/onepipeline/pull/285, in force on the adopted onepipeline
0.44.4, that schedule answers only the failures a retry can change. A projection the store
**refuses**, by the `class` of its own failure document, is reported once and put on no
timer: the driver's line and the finding the run raises each carry the store's `class` and
`kind` and say the projection is attempted again when the run's graph next changes. Every
other failure keeps the spaced schedule. The reason is the allowance a refusal spent: the
board refuses the same projection the same way however often it is asked, and each attempt
re-ran its reads and a copy, so a run holding one refused node drained the board token's
hourly GraphQL allowance for as long as it lived. onepipeline's `docs/contract-divergences.md`
entry 72 holds the figures, and its `tests/e2e/store.rs` drives every class against the real
store. See [Where a plan of this repository
lives](../AGENTS.md#where-a-plan-of-this-repository-lives) for the secondary limiter, which is
the other half of that pressure.

**A projection carries only the nodes that changed.** Since
https://github.com/nickderobertis/onepipeline/pull/287, in force on the adopted engine, an
attempt names to `project copy --member` only the nodes whose shadow task changed since the
last projection that landed, and neither reads nor rewrites a node it does not name — so a
person's edit on an unnamed item stands until that node next changes. A projection is
**whole**, every node as before, for one of three reasons, recorded as its `whole_because`:
`first`, where nothing has landed in this driver yet, a driver `just orchestrate --adopt`
started included; `after-failure`, where the attempt before it failed; and
`store-lacks-members`, where the store predates the first onetaskgraph release offering
`--member`. onepipeline's `tests/e2e/writeback_projections.rs` drives all three against the
real store.

**A copy is allowed the deadline its items earn.** Every store command the write-back
runs used to be killed at one fixed minute, and a large enough plan outgrew it: a run could
settle every node with its board left behind by a copy killed mid-write. onepipeline
https://github.com/nickderobertis/onepipeline/pull/248, in force on the adopted engine,
bounds that copy at `max(60 s, per-item budget × items)` instead, and leaves the reads on
the sixty-second deadline. *Items* counts the nodes the copy carries — every node for a whole
copy, only the changed ones for a member copy — so one node's transition is bounded by the
floor. The per-item budget is ten seconds unless a launch names one —
`--writeback-item-budget`, then `ONEPIPELINE_WRITEBACK_ITEM_BUDGET`, then the launch
config's `writeback_item_budget`, in that order — and the budget a launch resolved is
recorded on the run's `launch.json`, so a driver adopted later bounds its copies the same
way. `just orchestrate` names none, so this host runs the shipped default. A copy that
outlasts its deadline is refused with the arithmetic that set it — `project-copy exceeded
100 seconds (10 items × 10 seconds per item)`, or `(the 60 second floor; 1 item × 10 seconds
per item is less)` where the floor governed — on the driver's stderr line below and on the
finding the run raises, which names the items the copy was carrying, and in the attempt's
projection record.
`tests/writeback_budget/test_adopted_engine_bounds_the_writeback_copy_e2e.py` holds it on
a real launch of a ten-item plan with one node's turn held open by the suite's stand-in
backend, so the driver stays alive to enforce a deadline: a whole copy held past the floor
lands with nothing reported, a copy carrying one parked node is killed at the floor, and the
whole attempt after that failure, held past a hundred seconds, is killed with exactly that
arithmetic. The same journey fails on the release before the landing, whose driver kills the
first copy at sixty seconds.

**Every attempt is recorded on the run.** The engine appends one JSON line per projection
attempt, landed or failed, to `writeback-projections.jsonl` in the run's own directory under
the runs root, beside its `driver.log`, and never rewrites one. Each line carries `at`,
`project`, `scope` (`whole` or `members`), `whole_because`, `items` (the lineage roots the
copy carried — one id per item, never one per retry), `outcome` (`projected` or `failed`),
the store's `class` and `kind` when it failed with its failure document, `reason`,
`duration_ms` (the whole attempt, reads included), `actions` (the copy report's `created`,
`updated`, `unchanged` and `orphaned`, and the `reopened` the engine derives beside them:
the carried items the store `updated` from a `done` or `cancelled` category onto a word that
is neither), and `spent` — the copy report's own `spent` object verbatim, and `null` wherever
the destination meters nothing, which is every local Markdown one. That is where a manager
reads what the write-back
cost rather than guessing it: how many items each copy carried and whether `whole` keeps
recurring (a run repeating `after-failure` is failing), whether a failure is `refused` and so
waiting on the graph rather than on a timer, how long each attempt took, and on the `plans`
board the GraphQL points `spent` names. onepipeline's `docs/contract-divergences.md` entry 73
is the source of that shape.
`tests/writeback_budget/test_adopted_engine_projects_incrementally_e2e.py` holds all three
paragraphs above on a real launch, reading that record beside a log of every command the
engine handed the store and the destination's own records: a whole first projection whose
copy names no `--member`; one settled node copied alone, its destination record gaining its
status and settlement while every unnamed record — one a person had retitled included —
stays byte for byte what it was; and a store refusal recorded once, with no further copy
across a window longer than the one-minute ceiling until the graph changes, and exactly one
when it does.

**One board item per lineage, reused for the life of the work.** A node's item is keyed on
its **lineage root** — `onepipeline.id` is the id the plan authored or an `add` stated — and
says what the lineage **head** says: `onepipeline.node` names the head, the one node in the
lineage nothing superseded, and `onepipeline.supersedes` lists the superseded ids in lineage
order, root first, written only where the head is not the root; the title, status word,
body, `deps` and `delivers` are the head's own. So a `retry` **edits** that item onto the
replacement rather than creating a card beside it, and a `retry` or `requeue` of a node
whose card reads closed on the board — closed by a person, or written `cancelled` by an
older engine for a superseded attempt — writes `queued` onto it: a `retry` or `requeue` of a
node whose item reads `done` or `cancelled` writes an open word onto it, which the store
pairs with reopening the issue and the attempt counts as `reopened`. A plain `cancel` of a
running node settles it `cancelled` in the run's own record while its item reads `parked` —
the park outranks the settlement, and it is an open word, so a cancel closes nothing and a
retry after it lands `queued` on the same item reporting `reopened: 0`; a `drop` projects
`cancelled` and keeps its paired close, and a dropped node is not retried. The item is what
is retried, so the run's views and the board agree on one card however many attempts the
work took. A board an older
engine wrote — one item per attempt, the superseded ones closed — keeps its dead siblings:
nothing deletes a card, and the engine reuses the item at the **furthest-along** position
of the lineage, leaving the rest exactly as they are. onepipeline's
`docs/contract-divergences.md` entry 80 is the source of that shape, and
`tests/test_engine_contracts.py` holds these keys and words to it at the pinned release;
`tests/writeback_budget/test_lineage_item_reuse_e2e.py` drives the installed engine
through a retry of a running node, a cancel, a card closed by hand and reopened by a second
retry, and an `--adopt`, against a local Markdown destination.

**A launched run claims its own items, and the tickets they deliver.** A node the run has
not started yet is projected `queued`, which `onetaskgraph.yaml` maps to the `Queued`
option on both boards; a running node is `in progress`, a settled one its own settlement
word, and at closeout — settled or stopped — a node that never started is written back to
`todo`, releasing what it claimed. The first whole projection is attempted **once, bounded,
before the first dispatch**, so a manager reading the board sees the claim before any of
the work it claims begins. A node's
`delivers` names the tickets that node delivers, and the tickets themselves are moved by
the **store's** `delivers` relation rather than by the write-back: the engine writes the
node's own word, and onetaskgraph re-evaluates each delivered ticket over every task
delivering it. A store older than the release carrying `queued` and `delivers` moves
nothing, writes every unstarted node `todo`, and says so once on the driver's stderr.

**What a delivered ticket reads, while the node delivering it is its only deliverer**, is
what that rule comes to in the ordinary case — the one a manager reads off the board:

| the node | its ticket |
| --- | --- |
| waiting, once the launch's first projection lands | `Queued` |
| running | `In Progress` |
| settled done | `Done` |
| failed, cancelled, parked, skipped, or never started | `Todo` |

A deliverer in another plan, still working the ticket or finished with it, decides
otherwise: the store resolves the ticket over **every** task delivering it, and that
resolution is onetaskgraph's own documented rule rather than anything this host applies.
See [the follow-ups section](#follow-ups-are-drafted-not-surfaced) for what each ticket
status means, and
[AGENTS.md](../AGENTS.md#follow-ups-drafted-while-a-run-works-verified-once-it-ends) for
how a brief names them.

**But write-back is best-effort, and a green run therefore proves nothing about the
plan store.** It runs on its own worker off the reconcile loop, store reads never
feed back into scheduling, and closeout never waits out a store command — so a run
whose projection never landed settles exactly like one whose projection did. Measured
the same day and the same way, with the destination store made unwritable: the run
settled `complete` and its node `done`, `just orchestrate` exited 0, and the stored
task carried no `onepipeline.settlement` at all. What says so is off the settlement: a
line on the driver's own stderr — `onetaskgraph write-back failed for '<project>':
<reason>;` then either that it is retrying, spacing attempts out to sixty seconds, or that
it will be attempted again when the run's graph next changes — said once per failing streak
and once more when it recovers, the finding the run raises beside it, and the attempt's line
in `writeback-projections.jsonl`. No node settlement repeats any of them, and a `--detach`ed
run writes the first to a log nobody opens. So read the board as a projection and never as
the record: when what became of a node actually matters, read the run's own `just results`,
`just status`, and journal, which are written by the engine itself and are the reason those
views exist.

## The manager and the planner

The top-level session agent is the **manager**: it holds the user conversation,
launches runs, reviews what settles, and answers surfaces. The **planner** is not
that session. It is a dispatched onejudge worker like every other node — supervised
by its own simulated-user judge, costing turns, settling on the ledger — whose
deliverable is a local Markdown project, and `just plan <brief.md>` is the launch that
dispatches it. What each of the two decides is stated where that role reads it: the
manager's in [AGENTS.md](../AGENTS.md#your-loop-as-manager), the planner's in
[`personas/planner.yaml`](../personas/planner.yaml), which is that dispatch's own
system prompt and so travels into whatever repository it plans against.

**`just plan` writes a direct node**, so the planner it dispatches works in the checkout
the launch was made from — the shared canonical checkout — rather than in a worktree of
its own. It wrote a lifecycle node until the adopted engine made that shape one a planner
cannot settle under: a lifecycle dispatch whose branch is level with its base settles
`failed` as `empty-branch` unless the node declared `expects_no_diff`
(https://github.com/nickderobertis/onepipeline/pull/229), and that declaration settles a
node **without dispatching it**, refusing one that carries a persona at all — so no
lifecycle declaration covers a dispatched node that commits nothing
(https://github.com/nickderobertis/onepipeline/issues/238). A planner is exactly that
node: its deliverable is a plan-store record under the exported authoring root, and its
branch stays level with the base by design. The direct shape is the honest model of what
it produces, and the reason the default once moved *off* it is answered in the task
instead of by the placement. That reason was an incident: a direct planner cut a branch
in the canonical checkout, committed to it, and left it checked out; a finished lifecycle
publication then failed at its last step because the publication checkout was on that
branch rather than on its base, and returning it to the base deleted the manager's plan
files, which that planner had force-added onto its branch from gitignored paths. So
every launch appends `PLAN_DIRECT_PLACEMENT_NOTE` to the brief, stating that the dispatch
works in a checkout it does not own, **may write only to gitignored paths, may not
commit, may not cut a branch, and may not leave the checkout on any branch but its
base**, and which clause of the shared completion bar that exempts it from — the clause
that demands every change committed, which a planner doing correct work once settled
`task-failed` against. Nothing enforces the note, which is why it is on every launch.

Two consequences a manager reads rather than derives. The planner's working directory is
this checkout, so the plan it authors lands under the exported authoring root — the same
`.plans/` the manager reads — and a plan written anywhere else is a plan the brief asked
for somewhere else. And `--repo`, `--execution-checkout` and `--direct` are refused by
name: the first two would compose the shape that fails, and the third named the only
shape there is. `scripts/plan.sh`'s header holds the whole of the reasoning, both
directions of it.

### The document the plan is read as, and the launch that writes it

**The document is written last, by a launch of its own, from a plan something has already
reviewed.** A person cannot usefully review a plan node by node; what they can judge is
one short document — what is being built and why, the architecture, the contracts, the
acceptance criteria, and the planned work as a table of links, each row pointing at its
task. A document written before anything reviewed the plan describes content nobody read,
which is what the review one step earlier exists to prevent, and a run cannot interject a
review between its own nodes: a review record is written by this repository's own code and
never by a dispatched agent. So the plan `just plan` writes is **one node**, and the
document is a **second launch**, made by `scripts/finish-plan.sh` once the review and the
check have passed.

That second launch writes a one-node project of its own, stamped as the planning project
it is and naming that one node, which is what keeps its exemption from the design-approval
gate bounded to the launch that writes a document while the plan project stays gated like
any other. Its node names [`graphs/design-doc.yaml`](../graphs/design-doc.yaml) as its
`agent_graph` and [`personas/design-doc.yaml`](../personas/design-doc.yaml) as its persona
— a path, because a bare `design-doc` resolves against the roles compiled into
`oneagentgraph` and reads no file of this repository's — and it is a direct node exactly
as the planner's is, for the same reason: it writes a document into the plan store and
never a commit, and the adopted engine fails a lifecycle dispatch that commits nothing. It
attaches no monitor either, for
its own version of the reason the planning launch attaches none: a one-node run that reads
a finished plan and writes one document has no frontier for a monitor to compare against a
plan, and it is the last step of a flow rather than the work a flow supervises. So it is
owed a watch for the same reason the planner is, and being the second such launch is what
makes it the one a supervisor forgets. Its task is the brief unchanged, followed by its
own instructions and its own acceptance criteria — which open by disowning the criteria
above them, because those are the *plan's* and a judge holds a dispatch to every criterion
it finds in its task. What states the document itself is
[`config/design-doc-template.md`](../config/design-doc-template.md), and nothing restates
it: the node's task names that path, the persona names that path, and the file is the one
statement of the shape, the reader, and every property the document is judged on.

The dispatch reads the finished plan out of the store, stores what it wrote as a
**document of that same project**, and reports where the store says that document is — a
link where the store puts it on a website, a path where it puts it in a file on this
machine. Storing it beside the plan rather than reporting it is the point: the reviewer
finds it where the plan is, and follows the store's own answer rather than a path
somebody composed. The flow then copies both into the destination and reports where *it*
holds them, which is the copy a person reads and the copy their approval is recorded
against.

**A brief therefore names the plan's qualified project id**, on a line reading
`Plan project: <source>:<project>`, and a brief without one is refused at the exit status
a brief missing a required section is refused at. The second launch has no other way to
find the plan: nothing hands one launch's output to the next, and the tail is a separate
process reading the store rather than a node scheduled behind the planner. Nothing else in
a brief is parsed — and a line that is *there* and unusable is refused as a bad value
rather than as an absence, because two declarations are ambiguous and a value naming a
project in no store, reported as a missing line, sends a manager looking for a line that
is already in front of them. `--no-design-doc` drops the requirement with the tail it
belongs to: it stops the flow after the planner, so there is no second launch to read the
plan and nothing that needs its id.

The published CLIs do not know that split and nothing here renames them to it.
Everywhere `onepipeline` and the recipes over it say *planner* — [the planner
channel](#the-planner-channel) and its surfaces, the `planner` [read
profile](#read-profiles), the [`awaiting-planner`](#when-an-attach-returns) state,
`onepipeline next`, `onepipeline reply` — the reader they name is the manager, and
every "planner" below is to be read that way. The dispatched planner reaches that
channel from the other end, as [an agent with a
question](#asking-the-manager): a surface *on* it rather than a
seat at it.

## The plan schema

The tracked-plan contract is the published `onepipeline` plan schema, and declaring
a `schema_version` is required: a plan that omits it, or declares a number this
build does not read, is refused at launch naming the ones it does. **Write version
3** — what every plan here declares, and the one the fields below describe. The
adopted `onepipeline` 0.44.4 also still reads 2 and 1, so an older plan file an
operator kept a copy of launches rather than failing; that is a courtesy to old
copies, not a version to write. There is no compatibility ladder to read a version
number against any more — the node shapes this repository grew through its own
schema versions 1 to 7 were adopted whole as that crate's v1, so the shapes below
are unchanged and only the number moved.

Version 2 made two changes, and a plan written before either fails at launch:

- **`done_when` is no longer a plan field at all.** A node carrying one is refused
  while the plan loads, at any declared version. A node's review bar is the
  `## Acceptance criteria` of its own task, which the judge is handed verbatim; a
  bar broader than one node lives once in `config/onejudge.base.yaml` under
  `user.done_when`. See [Where a node's review bar lives](#where-a-nodes-review-bar-lives).
- **`max_turns` is forwarded to the node's dispatch**, which version 1 never did:
  under v1 the field was read and then dropped, so a plan that asked for more room
  silently got the persona's or the base config's cap instead.

Version 3 then made the change requests a run publishes the plan's to state, and
both halves of it are why every lifecycle node in `examples/` carries a title:

- **`title` is required on every lifecycle node.** A node with a `repo` and no
  `title` is refused with "a lifecycle node states the title its change request
  opens under". Write a Conventional Commit subject: it is the published subject,
  and a squash-merged publication leaves exactly one commit carrying it.
- **`body` is a new optional node field**, the body the change request opens with.
  A node that states one publishes with it; a node that does not gets the body
  [the drafting graph](#the-agent-graphs-a-run-launches) writes, or none when the
  launch names no drafting graph. Stating `body` under a lower version is refused
  by name — "`body` is a schema 3 field and this plan declares schema_version 2".

Two field spellings also changed when the crate adopted this repository's shapes,
and a plan written before that fails at launch on either:

- `merge_policy` names the publication a change reaches its base branch by, and
  its values are now `local-direct`, `change-open`, `change-auto`, and
  `change-direct` — the old `direct` / `none` / `auto` vocabulary is refused by
  name.
- `context` is **one** planner note, a string, rather than a list of them. It is
  where a live `note` that no turn took is carried to. Where the note is carried it
  holds exactly that one; where a turn of the node's conversation took the note
  instead, the turn has read it and nothing is owed forward. See
  [Carried planner context](#carried-planner-context).

The schema adds two optional per-node fields this repository's own never had:
`executor`, naming which executor dispatches the node, and `agent_graph`, naming
an agent-graph config that overrides the default node-scope one — see [The agent
graphs a run launches](#the-agent-graphs-a-run-launches).

The optional boolean node `parked` is written by a live `cancel` and cleared by a
live `requeue`. A parked node stays in the graph and on the run's ledger
**without being dispatched**, which is what makes "stop for now, maybe resume
later" a state rather than a lost node. See [Live graph
edits](#live-graph-edits).

The optional top-level `goal` mapping has a required non-empty
`text` and optional `id`; when omitted, the id is derived from the text. Active
goals and their repository identities are visible across projects with `just goals`.
Before a recorded run starts, the launch refuses to overlap any active run that
targets the same registered repository identity. An operator may deliberately
proceed with `--acknowledge-concurrent`; the shared identities and active run IDs
are then recorded as a `concurrent-acknowledged` journal event for audit. The
reservation lasts as long as the run is unsettled and is removed when it settles.

The refusal says *which kind* of company each overlapping run is, because they
call for opposite decisions. A run reported `is LIVE (owner pid N on HOST)` has a
working owner: a second orchestration would share its checkouts. One reported
`holds pid N ... but shows no progress (PARKED)`, or `registered but not
observable here`, is a registration whose owner is not working — the residue
`--acknowledge-concurrent` exists to launch past. Acknowledging never hides a
live one: launching past it prints `proceeding alongside a live concurrent run`
on stderr, `just goals` states each registered owner's observed state, and the
planner's `just runs` and `just status` views carry a `CONCURRENT:` line naming
every live run that shares this one's identities — under the same
`--parked-after` threshold those views report parked with, so one view cannot
call a launch parked and a live neighbour in consecutive lines. Liveness is
observed at read time and never stored, because a recorded "this run was alive"
is false the moment its process exits.

## The agent graphs a run launches

Three files in `graphs/` decide what a run's agents actually are, and all three are
this repository's to write. `onepipeline` ships the **flag and the path**, not the
files: they name the operator's own onejudge base config, oneharness configs, and
personas, so a crate that shipped them would be naming files it cannot know.
Nothing scaffolds them either — `oneagentgraph` has no `init` — and a launch
pointed at one that is not there refuses, with `oneagentgraph: invalid config:
cannot read graphs/dag-scope.yaml`. Check one with `just validate-personas`'
sibling, `oneagentgraph validate graphs/dag-scope.yaml`.

- **`graphs/dag-scope.yaml`** is the **observer** graph `just orchestrate`
  attaches: the `monitor` member that watches the run — a foreground conversation
  the graph paces one turn per 300 seconds, see [The monitor is a paced foreground
  conversation](#the-monitor-is-a-paced-foreground-conversation) — and the
  resettable-cron `check-in` member that paces planner updates. Neither drives
  anything. The two
  name **different** oneharness configs, identical but for the per-turn deadline —
  a monitor watching a whole run has none, a scheduled pacemaker must keep one —
  and a member has no `timeout` field of its own, so the file is the seam. See
  [Choosing a deadline per
  side](onejudge-integration.md#choosing-a-deadline-per-side) before re-sharing
  one.

  Its `judge.command` is the one ref in that file resolved against the directory the
  run was **launched from** rather than against the file itself, because it is an
  argv rather than a config ref — which is the other reason `just orchestrate` is run
  from the repository root.

  Attaching it is **opt-in**: `--dag-graph <REF>` ships defaulting to `off`,
  because no agent is required to run a plan. `just orchestrate` names this file so
  every run **it** launches gets a watcher, and passes a caller's own `--dag-graph` —
  including `off` — through untouched. `just plan` names `off` instead, and keeps a
  caller's own the same way: a planning run's output *is* the plan, so a monitor
  attached to one watches it for drift from a document that does not exist yet, and
  the journal, ownership row, surfaces, and DAG UI place it would otherwise be
  credited with are all `onepipeline start`'s own. `just finish-plan` names `off` for
  the same class of reason on the design-document launch it makes, so the planning flow
  attaches a monitor to neither of its two runs. There is no environment variable for
  any of them; the flag is the only way to move it.

  The document declares **schema 9**, the first that admits every field it uses:
  `check-in` carries its own `task`, which needs 3; that task opens with `{task}`,
  which `oneagentgraph` expands only from 4; the monitor's `background` needs 8 and
  its `schedule`, on a two-party member, 9. `onepipeline` composes one task for this graph — it states
  what the run *is*, its id and its goal — and hands it to every member which does
  not claim one. A member's own `task` **replaces** it, so a member that claims one
  must interpolate it back in to learn which run it is on. `onepipeline` does also
  export `ONEPIPELINE_RUN_ID`, set to the run id, to an observer member — measured
  against onepipeline 0.44.4 by dumping both sides of a monitor member's whole
  environment on a real launch. `tests/e2e/test_orchestrate_launch_e2e.py` re-takes
  that measurement on the judge side of a real observer member every gate run, so a
  release that moved the export fails there rather than here. Write the member
  against `{task}` anyway: it is this graph's own contract rather than a per-release
  export, and it carries the run's goal as well as its id. Give a scheduled member
  its own task whenever its job is not the run-level task, open it with `{task}`, and
  see [the pacemaker](#the-planner-update-pacemaker) for why this one is scoped away
  from live edits.
- **`graphs/node-scope.yaml`** is what every dispatched node runs under: one
  worker supervised by one simulated-user judge. A plan node overrides it with
  `agent_graph`, and `ONEPIPELINE_NODE_GRAPH` moves the default.
- **`graphs/pr-author.yaml`** is the **drafting** graph: one single-sided
  `kind: oneharness` member that reads a verified branch's diff and answers with
  the body its change request opens under. Like the observer it is opt-in —
  `--pr-author-graph <REF>` ships naming nothing, and a launch that names nothing
  opens its change requests with the body its plan states, or with none — and `just
  orchestrate` names this file so every remote publication from this host is
  drafted. Unlike `--dag-graph` it takes no `off`: leave the flag off instead.

  Its member is a `schema_file` member, and that decides two things nothing else
  here does. `../oneharness.pr-author.toml` names
  `config/pr-author-body.schema.json`, resolved against **that config's** directory,
  so a turn answers with `{"body": "…"}` or is re-prompted with the validation
  error; and that same config sets `stream = false`, because oneharness validates a
  structured answer against the complete response and `oneagentgraph` refuses a
  member declaring both. It is also a third copy of the supervisory routing rather
  than a pointer at the monitor's or the pacemaker's, for the same
  [per-deadline](onejudge-integration.md#choosing-a-deadline-per-side) reason: a
  drafter is a bounded job that sits between a passed gate and a publication, so it
  keeps a finite `timeout` where the monitor keeps none.

  It declares **schema 4** for one of the reasons `dag-scope` declares 9: the member carries
  its own `task`, which needs 3, and that task opens with `{task}`, which expands
  only from 4. `onepipeline` composes the drafting task — "Read this branch's diff
  and write the change request's body…", followed by the task the branch delivered —
  and a member's own `task` replaces it, so the interpolation is what keeps the
  drafter looking at a diff it was actually shown. `../personas/pr-author.yaml` is
  named for the member's **label** alone: a single-sided member has no onejudge base
  config to layer a persona delta over, which is why the role prose lives in `task`.

All three paths are resolved **relative to the directory the run is launched from**,
which is why `just orchestrate` is run from the repository root; every ref *inside*
a graph is resolved relative to that graph file instead.
`tests/e2e/test_orchestrate_launch_e2e.py` proves the refusal a missing observer
graph gets by passing `--dag-graph` at a path that is not there. That suite's real
launch uses these files: the paid model is what it stands in for, through
`ONEAGENTGRAPH_ONEHARNESS_BIN`.

Two things about them are worth knowing before reading a surprising run:

- A node's `persona` is both an event label and the `worker` member's persona
  override. `onepipeline` appends that override after the launch's `--node-set`
  values, so the plan is authoritative. Nodes with no dispatch (human and
  `expects_no_diff` nodes) name no persona and still settle normally.
- The monitor's judge side **is** the planner channel: `onemessagebus serve surfaces
  --codec monitor` over `config/onemessagebus.yaml`, the binding this host declares
  there for onejudge's frames. A turn raises nothing, a lost turn and the end-of-run completion bar each
  raise a non-blocking surface of their own, and a monitor reports through the
  `finding` op; see [Serving the channel as the monitor's judge
  side](#serving-the-channel-as-the-monitors-judge-side).

### The monitor is a paced foreground conversation

`graphs/dag-scope.yaml` names two members, and both of them carry a clock. The `monitor`
is a `kind: onejudge` conversation the graph **paces**: `schedule: {every: 300,
start_after: 0}` opens it with the wave and then holds it 300 seconds between the
judge's answer and the next agent turn, so one conversation takes one turn every five
minutes rather than one every 23–60 seconds. The `check-in` pacemaker is a scheduled
single-sided member on its half-hour resettable clock, exactly as before. What keeps the
observer graph alive between the monitor's turns is no longer inferred from which member
is a conversation: it is a property the document **declares**. The monitor states
`background: false`, and the run stays open while a foreground member is unfinished — a
scheduled foreground member never finishes on its own, so the conversation holds the
run open until its judge completes it, onejudge settles it, `max_turns` bounds it, or the
driver's cancel at settlement ends it. The pacemaker states nothing, and a scheduled
member that states nothing is background: it fires inside the monitor's holds, and the
run does not stay open for it. This section records why that arrangement is the one
here, because the belief it replaces — that a scheduled member cannot be the monitor —
was true on the reader this host used to pin and is what a reader re-derives from the
same symptom.

**A hold is not silence.** For the length of it the member's heartbeat continues and its
activity clock is refreshed, so nothing condemns the monitor for waiting; `just status`
read during a hold reports neither `OBSERVER DEAD` nor `OBSERVER NOT RESTARTED`. The
driver's cancel at settlement ends the last hold, so a run that settles in seconds still
returns in seconds. What else ends a hold early is the linked `oneagentgraph`'s own
contract (its `docs/contract.md`, under `version: 9`), proven in that repository rather
than here: a `trigger`, the run's `stop` or the member's own `cancel`, and a **note**
offered to the member — the turn opens and the note is delivered into it — so a
manager's `note` to the monitor is never held for five minutes. And the judge side is
unchanged: this host's monitor binding answers every
supervisor frame at once, because the hold is the graph's and a judge that slept would
only stack a second wait on the first; the next turn opens after the hold and reads the
stream from the cursor `personas/orchestrator.yaml` keeps.

**Why the monitor is paced, in the operator's own numbers.** Measured over
2026-09-05..2026-09-11, the monitor's conversation was 58% of this host's Codex tokens:
12,313 turns since 2026-09-01, 94% of them read-only turns that reported nothing, each
costing roughly 10–17K uncached input tokens nearly flat with the gap before it. A
300-second hold, read against those turn timelines, gives about 0.19x the monitor's
tokens and 40–50% off the whole Codex bill. Those are readings of this host's own
journals rather than claims about a release, so nothing re-takes them.
<!-- dated-claim: incident the measurement that decided the 300-second hold, read off this host's accumulated journals at the time; a later reader recounts them by re-reading the runs root -->
The streams the monitor watches move at roughly 300 worker events an hour, so at one turn
per five minutes some 25–50 events land between turns, which is why the persona reads
the detailed stream from a cursor rather than a tail. It keeps no file for it: every
`onepipeline monitor` read ends in a `-- cursor 1:<run>:<byte>` resume line, each turn
reads `--cursor` from the line its previous turn ended with, which the held conversation
keeps, and a first turn or a refused cursor reads a bounded tail. The member's working
directory is the launch directory — often a publication checkout — so a file written
there is one that checkout's next publication is refused over, which is how the cursor
file this replaced blocked a `local-direct` landing.

**The reader still refuses a document nothing holds open, and the refusal now names the
declaration.** Remove the monitor's `background: false` from the shipped document and
defer its first turn, and `oneagentgraph validate` answers:

```
oneagentgraph: invalid config: nothing holds this run open — no member is foreground
and either scheduled or able to take a turn in the initial waves — so it settles as
soon as those waves are done and a deferred first turn (check-in, monitor) never comes
due; declare `background: false` on one of them, give each of them `start_after: 0`,
or add a foreground member for them to pace
```

The shipped document validates (`dag-scope: 2 member(s) OK`); only the removal makes it
invalid, and a document the reader refuses attaches **no** observer at all. Removing the
line alone, with the monitor's immediate first turn kept, is refused the same way naming
`check-in`: the pacemaker's first turn is deferred to its period, and once no member is
foreground nothing holds the run open for it. So reverting the declaration is refused at
the launch rather than discovered as a run nothing watched.

**What settled the graph before, kept as history.** On the reader this host pinned
through oneagentgraph 0.3.17, a graph ran while at least one member was unsettled —
mid-turn, or mid-conversation — and a scheduled member settled after each firing, so a
graph made only of scheduled members had nothing left to keep the process alive until the
next tick: the reader refused an all-scheduled document, and the `start_after: 0`
remedy it named loaded and then settled after one turn each, 125 milliseconds against
schedules of 600 and 1800 seconds, with the driver printing that the observer had
stopped watching and continuing without one. The evidence that the pacemaker's survival
was never a property of its kind is in this host's own recorded runs, counted over every
`member-settled` a pacemaker had written at the time: **89 of them**, across 27 runs,
splitting **50/39** on whether a conversation member was beside them. Fifty belong to
a two-member observer document — the shipped `graphs/dag-scope.yaml`, and the older
revision that spelled the same member `orchestrator` — and every one of those fifty
fired while that member's conversation was live; forty-eight settled while it was still
unsettled outright, and the remaining two were turns already in flight when the graph
tore down under them, started at `08:50:43.215Z` against a monitor death at
`08:51:34.017Z` (`condemn-answer-steer`) and at `14:10:57.668Z` against one at
`14:11:05.880Z` (`dag-ui-observability-2`). The other thirty-nine belong to one run,
`onetaskgraph-build-3`, wired to a one-member scratch document with no conversation
member at all (`scratch/graphs/dag-scope-quiet.yaml`, gitignored — a mitigation for a
flooding monitor, not a design), and each of those thirty-nine settlements took its whole
observer graph down with it, within **0.100s to 0.112s**, median 0.103s, every time.
No pacemaker turn on this host had ever begun after its graph's conversation member
ended. Both counts are readings of this host's accumulated journals rather than claims
about a release, so nothing re-takes them; what replaced the constraint they measured
is below.

**What lifted it, and where each half landed.** The constraint needed two upstream
changes, and both are in force on this host:

- **`oneagentgraph`** — the `background-liveness` release (0.3.18) let a member declare
  whether the run stays open for it, and settled a run once only background members
  survived; the `paced-conversations` release (0.3.19) let a `kind: onejudge` member
  carry a `schedule` that paces one conversation rather than starting a second, with a
  hold that keeps the heartbeat and the activity clock alive and ends early on a note.
  The `onepipeline` this host adopts links a release carrying both — 0.4.2 at this
  adoption — which `tests/test_linked_libraries.py` reads off the installed wheel's SBOM.
- **`onepipeline`** — the observer relaunch, which 0.21.1 already carried
  (https://github.com/nickderobertis/onepipeline/pull/197): a driver keeps an observer
  watching a live run and records why one ended, which is what `OBSERVER DEAD` and
  `OBSERVER NOT RESTARTED` in `just status` are the two halves of.

`tests/e2e/test_observer_graph_liveness_e2e.py` holds all of it against the installed
engine and the pinned reader: that the shipped document validates and declares the
monitor paced and foreground and the pacemaker neither; that a real launch under the
shipped document, with both periods overridden small through `just orchestrate --set
members.monitor.schedule.every=<seconds>` and `--set members.check-in.schedule.every=…`,
opens the monitor with the wave, holds it the interval between turns, keeps its
heartbeat and a clean `just status` through the hold, fires the pacemaker inside a hold
and survives it, and ends the observer at settlement without waiting the hold out; and
that a document nothing holds open is refused in the words quoted above. The
`--set` override is how a journey that needs turns closer together than the shipped
period says so; the shipped document's values are never edited to make one pass.

**What settles the monitor is a different thing, and worth not confusing with this
one.** Two things end that conversation. onejudge names the first in the member's own
report: *the agent and the supervisor repeated 2 no-op exchanges (no tool activity, and
the same agent reply each time); settled on the work already done*. A monitor answering
one fixed short sentence while it finds nothing satisfies that signature by
construction, which is why the quiet-turn sentinel that stopped it flooding the
planner's queue was also what ended its watch — and why there is no sentinel any more:
`personas/orchestrator.yaml` asks a quiet turn for words about what it read rather than
for a formula, so a healthy watch no longer walks into that signature on purpose.
onejudge declares a field for the contract that remains — `user.settle_on_noop`,
documented in `onejudge` 0.13.5's `src/cli/config.rs` and `src/engine.rs` as the opt-out
for "an observer instructed to answer with one fixed short sentence while it finds
nothing", with `max_turns` left as the bound. Nothing here sets it today; that is a
change to `personas/orchestrator.yaml` and a decision for a manager, not something this
section claims is in force.

The second is the bound itself, and on this host it was overwhelmingly the one that
fired: in `root-causes-94-plan` the monitor settled five times and every one of those
five is the turn immediately after its fiftieth. That ceiling is now 4500, derived from
this host's own recorded runs — the corpus, the eligibility rule, the exclusions, the
turn rate and the arithmetic are all written where the value is declared, in
`personas/orchestrator.yaml`, and restated there at the paced rate: at one turn per five
minutes the same ceiling is about 375 hours of watching. It stays finite deliberately:
it is what bounds a wedged or looping supervisory conversation, and the sibling
`check-in` member keeps a finite deadline for the same reason.

## The planner channel

`just orchestrate <source:project>` starts the run, prints its run id, and then **stays
attached**: it streams the run's merged events and returns when the run
[settles](#when-an-attach-returns). `--detach` returns at the launch record
instead, for a run that should go unattended. The launched process leads its own
session either way, so neither the end of the launching turn nor Ctrl-C stops it —
see [A run outlives the turn that launched
it](#a-run-outlives-the-turn-that-launched-it).

**The engine is the executor and nothing supervises it.** Its reconciler converges
the actual frontier toward a desired graph continuously, and the planner may edit
that graph while nodes run; there is no boundary to wait for and no verb to
advance. The driver alone writes the graph, journal, and ledger. After launch the
planner uses only the channel, `just stop`, and the read-only `just monitor` /
`just runs` views. Runs are owned by the session that launched them — see [Who
launched a run, and who may stop
it](#who-launched-a-run-and-who-may-stop-it).

What the planner hears from is the [monitor](#the-agent-graphs-a-run-launches) and
the [pacemaker](#the-planner-update-pacemaker), each raising a non-blocking surface
with the same verb:

```sh
onepipeline surface --kind check-in RUN <<'UPDATE'
Node X failed its gate; retry with a corrected fixture?
UPDATE
```

**The verb takes the surface's text as bytes.** It reads them from a `FILE`
argument, or from stdin when none is named; `--message TEXT` is the inline form,
kept for text a person typed and refused beside a `FILE` — `the argument
'--message <TEXT>' cannot be used with '[FILE]'`. Which matters because the two
callers that raise most of these surfaces are agents whose own prose is the input:
a message written onto the command line is parsed by the shell first, and a finding
that quoted a command name has run it here. So both supervisory members are told to
use the byte-carrying form — see [`personas/orchestrator.yaml`](../personas/orchestrator.yaml)
and the `check-in` task in [`graphs/dag-scope.yaml`](../graphs/dag-scope.yaml).

`--kind` is an **open word**: any kind matching `^[a-z][a-z0-9-]{0,63}$` is relayed
unchanged — its message, source, blocking flag and unread accounting — and a malformed
one is refused (the engine's contract, Contract K in onepipeline's `docs/contract.md`).
The engine acts on two of them: `check-in` is the pacemaker's word, and `finding` is
something a watcher saw and decided the planner should know, raised deliberately rather
than as the side effect of a turn having happened. It raises one of its own,
`edit-applied`, for each edit a non-planner author applied — `monitor-edit` was that
kind's name through the engine's 0.36 releases. This host's monitor binding raises
`monitor-failed` and `monitor-completion` the same way. Reading any surface restarts
the clock of every observer member the run's graph declares `resettable` — here, the
`check-in` pacemaker alone.

The planner reads it with `just channel-next RUN`, which hands out each queued
surface once, along with the events it was raised against:

```json
{"status":"surface","surface":{"id":0,"kind":"check-in","message":"Node X failed its gate; retry with a corrected fixture?","source":"check-in","blocking":false,"queued_at":1786490389925},"events":[]}
```

Which events accompany it is the [read profile](#read-profiles)'s decision, and the
default is `planner`. Settled workers may also surface while the run continues. A
worker's `surface` is advice only: it is raised and never replied to, and only the
planner and the monitor can issue edits. A worker that needs an answer before it can
continue does not raise one — it blocks on the channel instead, as [an agent with a
question](#asking-the-manager). The planner replies with one of
these legacy verdict shapes:

```json
{"completion":false,"message":"retry X with the fixture requirement","reason":"the graph is not complete"}
{"completion":true,"reason":"publication and follow-up triage verified"}
```

`completion: false` requires both `message` and `reason`; `completion: true`
requires `reason`. Completion is reserved for a verified `closeout` after the
whole graph is published and follow-ups are triaged. **Follow-ups are triaged** means,
for a run that ended with every node done, that the follow-up run its success hook
launched has settled and the planner has relayed the links to every issue it created
or updated; for a run that ended any other way, that the planner has decided with the
user whether to verify its drafts by hand with `just follow-ups <run-id>`. See
[the run-end hooks](#follow-ups-are-drafted-not-surfaced) for both. See [Live graph
edits](#live-graph-edits) to change the graph, which never waits for anything: the
reconciler applies an accepted edit on its next pass.

The planner-facing recipes are:

```sh
just orchestrate authoring:my-project
just channel-next RUN
just channel-reply RUN reply.json
just channel-approve RUN
just channel-reject RUN "verification failed"
just channel-continue RUN "apply the edit and continue"
just follow-ups RUN --detach
just follow-ups RUN --feedback feedback.md
just follow-ups-handle-comments RUN
```

`just follow-ups` verifies a finished run's drafted follow-ups in a run of its own; a
run `just orchestrate` launched whose every node ended `done` has it launched for it by
the success hook, and a manager types it for a run that ended any other way or to
re-dispatch with feedback. `just follow-ups-handle-comments` is that re-dispatch for what
people wrote on the board: it gathers every comment a person left on an issue the run owns or
has marked a comment on until a reply of the run naming that comment exists
(`orchestrator/follow_up_comments.py` states the boundary for comments older than replies),
into one feedback file under the drafts root's `feedback/<run-id>/`, each with its id, its
URL, its author and its text, and hands it to `just follow-ups --feedback`, whose agent acts
on each comment and answers it with a reply under the run's marker; a run with no new feedback
is refused and nothing is launched. [Follow-ups are drafted, not
surfaced](#follow-ups-are-drafted-not-surfaced) says what it does and how the hooks
reach it.

The ledger lives under `runs/` in the working directory; `ONEPIPELINE_RUNS_DIR`
moves it, and every one of these verbs reads the same variable.

`--detach` prints a JSON launch record containing `run_id`, `pid`, and literal
`commands.next` / `commands.monitor` values. Use that `run_id` for every
supervision recipe. A plan name is also accepted when it identifies exactly one
active launch; an ambiguous or stale name fails and lists valid run ids.

`just monitor RUN` is the re-attach path: it follows the run and renders a pending
planner surface without consuming it. Rendering is not reading — `channel-next`
is the only consumer, so the [pacemaker](#the-planner-update-pacemaker) must not
depend on consumption to keep ticking.

`channel-next` waits for one surface. A bounded wait with no message returns
`{"status":"running","surface":null}`; a settled run returns
`{"status":"finished"}`. Either way it carries the `events` its
[profile](#read-profiles) admits, so a planner reads what happened and what is
being asked in one call. `channel-reply` accepts a reply file or reads JSON from
stdin when its file argument is omitted. Both sides may exit and reattach between
messages: channel state is durable under `runs/<run-id>/channel/` as `queue.json`,
`surfaces.jsonl`, and `replies.jsonl`.

### Serving the channel as the monitor's judge side

The monitor is a two-sided onejudge member, and its judge side is the live planner
rather than a simulated user. That is what makes a reply *supervision*: the planner's
ruling reaches the monitor in its own conversation rather than only as graph edits.

That side is `onemessagebus serve surfaces --codec monitor` over
`config/onemessagebus.yaml`, as `graphs/dag-scope.yaml` names it: the bus's generic
binding interpreter, running the **`monitor` binding** this host declares in that file's
`codecs` block. The grammar a binding is written in is the bus's, in onemessagebus's
`codecs.md`, and is not restated here; what is this host's is the binding itself —
the texts a monitor is told, the two surface kinds it raises, what a lost turn is called
— written as data beside the persona whose behaviour it describes, with the queue it
raises on, the variables its asker and session bound are read from, and the [reply
window](#asking-the-manager). Nothing in it derives a fact from a frame, a transcript or
a harness identity: each frame is validated against the bundle onejudge publishes at the
release tag `config/onejudge.version` names, linked under the file's `schemas` key and
pinned to the protocol that release speaks, and every fact the binding names — a turn's
outcome, its cause, the harness it ran on — is a field onejudge reports on the frame.
The channel's own layout — its four queues `surfaces`, `replies`, `commands` and
`command-outcomes`, their policies, and the planner's grants — is the engine's, not the
bus's: the adopted `onemessagebus` compiles none in, and would refuse `profile:
planner-channel` without one, so the same `schemas` key links the
`schemas/planner-channel.json` the engine publishes at the tag `config/onepipeline.version`
names (onepipeline's `docs/contract.md`, "The planner-channel layout is published as a
document"), and every host verb reads and writes the channel directory under the layout a
dispatch's engine compiles in. `tests/test_onemessagebus_config.py` holds that link to the
pin and the resolved configuration to those four queues.
The retired onejudge codec, which read the run out of a frame's task, proved a lost turn
from the end of a harness transcript, and named its identity against
`ORCHESTRATOR_CODEX_ALT_HOME`, is history.

The channel directory is the run's own, and the judge command composes it from
`ONEPIPELINE_RUNS_DIR` and `ONEPIPELINE_RUN_ID`, which the engine exports to both sides
of an observer member — `ONEPIPELINE_RUN_ID` set to the run id, measured against
onepipeline 0.44.4 in the judge command's own environment on a real launch, and re-taken
on every gate run by `tests/e2e/test_orchestrate_launch_e2e.py`.

| Frame | What the binding does |
| --- | --- |
| `supervisor` whose `turn.outcome` is `taken` | the monitor took its turn: nothing is raised, and the member is answered with a non-completion telling it that prose reaches nobody, that a report reaches the planner only as a `finding` op, and that its next turn opens after the graph's hold |
| `supervisor` whose `turn.outcome` is `lost` | one non-blocking `monitor-failed` surface naming `turn.cause` and `turn.harness`, and the serving session fails (exit 1), which ends the member |
| `judge`, `kind: boolean` | the completion bar is asked as a non-blocking `monitor-completion` question and the ruling is relayed as the score — see [below](#the-completion-bar-is-scored-by-the-planner-too) |
| `judge`, `kind: numeric`; `respond`; `user`; `assess` | refused, exit 2 |

A lost turn is onejudge's classification, not this host's: onejudge reports the turn a
harness failed as `lost`, with the candidate's classified failure as its cause and the
composed id of the harness identity that ran — `codex:alternate`, say — so the surface
names the quota to look at without this host reading anything to find it. A turn whose
reply text is empty is still `taken`; the run keeps whatever it said where `just monitor
<run> --filter detailed` reads it.

Every surface the binding raises is **non-blocking**. A blocking one would hold the run
at `awaiting-planner` on every monitor turn — ending the attached launch's
settle-and-return contract, and stopping the frontier to ask about watching rather than
about work. A planner who never answers costs the run nothing.

**A live edit never reaches the monitor.** A reply is routed by the halves it carries: a
commands-only envelope reaches the `commands` queue alone and leaves the monitor's
pending surface standing, and one carrying a verdict and edits reaches both.

**Who may speak on the channel is declared in the same file.** The planner is the one
author the engine's planner-channel layout declares, granted every op, and the file links
that layout rather than the bus compiling it in. The monitor is this host's, under `authors.monitor`: its grants —
`retry`, `requeue`, `cancel`, `finding` and `add` — and, for every other op, the reason
the channel gives when it refuses one, in its own words (`'drop' is not an op the
monitor may issue: … Surface it to the planner instead`). An author the file does not
declare is refused before anything is appended; the engine applies what the bus admits
and surfaces each edit a non-planner author applied as `edit-applied`.

`tests/e2e/test_monitor_quiet_turn_e2e.py`, `tests/e2e/test_lost_turn_wire_contract_e2e.py`
and `tests/e2e/test_monitor_survives_the_channel_e2e.py` are this seam's journeys on a
real run's channel, with every frame written by a real onejudge, and
`tests/test_onemessagebus_config.py` holds the binding, the author and the schema link
to their sources. Why each rule is what it is was measured on the filter the retired
codec replaced, and is kept under [What the retired channel scripts
measured](#what-the-retired-channel-scripts-measured).

#### A monitor reports through the `finding` op

A monitor has exactly one way to tell the planner something, and it is the `finding` op
in a reply envelope it sends through the bus (`onemessagebus send replies` onto the run's
channel, as `personas/orchestrator.yaml` spells it). The prose a turn ends in raises **no** planner
surface, whatever it says, and there is deliberately no fixed quiet-turn string for a
monitor to get wrong: a sentinel is a vocabulary, and a vocabulary can be got wrong.

**What covers a monitor that observes something and does not file it** is its
supervisor: prose is the safety net no longer, so the monitor's own `user.persona`
requires such a turn be sent back until the finding is on the channel. The cost is stated
plainly because it is real — a monitor that notices something, writes it as prose, and is
not sent back has reported it to nobody.

**The periodic `check-in` member is untouched by all of this.** It is a single-sided
`kind: oneharness` member with no judge side at all: it raises its own surface with
`onepipeline surface --kind check-in`, and its report reaches the queue under its own
kind and source. `tests/e2e/test_monitor_quiet_turn_e2e.py` reads the pacemaker's
surface off the queue in the same run whose monitor prose raises none.

Structured output would be the heavier way to draw the same line, and one constraint
rules it out: oneharness validates a structured answer against the complete response, so
`stream = true` and `schema_file` cannot both hold, and turning streaming off for the
run's long-lived watcher would trade away the per-turn visibility a manager supervises
with.

#### The completion bar is scored by the planner too

onejudge asks a judge side **two** ops, not one. `supervisor` comes at each turn
boundary; `judge` comes once the conversation ends, to score `user.done_when` —
always, whether the supervisor ruled complete or the turn cap ran out, and
independently of `evals` and `assessment`. Measured on onejudge 0.13.5 with a
`kind: command` judge that logged every op it was asked.

That second one has **no configuration escape**, and the attempts are worth knowing
so they are not retried. `oneagentgraph` merges a persona's `user.done_when` as a
*second* bar alongside the base's rather than over it, so `done_when: null` adds
nothing and the base's bar survives into the member's effective config; and
`user.done_when_replaces_base` is refused outright — `names nothing to replace the
base's bar with` — for both `null` and `""`. A `kind: onejudge` member therefore
always carries a bar it is always asked to score, whether or not its judge side is a
model. Editing the bar out of `config/onejudge.base.yaml` is not the answer either:
that one is the shared dispatch bar for every worker on this host.

So the binding serves that op for the same reason it serves `supervisor` — **the planner
is this member's judge side, so the planner scores the criterion**. It raises the
criterion as its own non-blocking question, under kind `monitor-completion`, and relays
the ruling that comes back: `completion` becomes the boolean score and the ruling's
`reason` — or, lacking one, its `message` — becomes the score's. Nothing is invented. The surface arrives once, at the end,
and a manager meeting it for the first time must not read it as a run held up on them. A
planner who never answers costs nothing: a wait that elapses, or a question abandoned
before anyone ruled, is scored `unsatisfied` — the conservative direction — and never a
fabricated pass.

This is a **workaround for an upstream gap**, and it is written down as one so it can
be retired rather than maintained: a member whose judge side is not a harness still
gets a scored bar it cannot answer, and neither `done_when: null` nor
`done_when_replaces_base` can remove it.
`tests/e2e/test_monitor_survives_the_channel_e2e.py` asserts on a real launch that the
member still carries a bar at all — so the day a release lets one decline it, that
check fails and the score path can go.

`assess`, the op a top-level `assessment` produces, is refused, as is a `judge`
asking for a score on a scale: a planner rules with a boolean, and a boolean is not a
number. Neither can arrive from this repository's graphs, because
`tests/test_observer_judge_ops.py` forbids any channel-served persona from declaring
the keys that would ask them.

### Asking the manager

A worker that has reached a decision fork stops and asks rather than guessing. **Every
launch this repository makes** exports the path of `scripts/ask-manager.sh` into the
launch environment as `ORCHESTRATOR_ASK_MANAGER`, and that command is the one supported
way to ask. *When* a fork is worth blocking on is the dispatched role's judgment rather
than this page's; both are in [`personas/planner.yaml`](../personas/planner.yaml).

`scripts/ask-manager-env.sh` is the one source of that path and of the refusal when
it is not runnable, and `scripts/onepipeline.sh` takes it for `start` and `adopt` —
which is `just orchestrate` attached, detached, and adopted, and `just plan`, and
nothing else, because a read-only view dispatches nobody. `just plan` takes it a
second time before it writes anything, so a checkout that cannot ask is refused
rather than left holding a plan. It was once `just plan`'s alone, which meant no
dispatch of an orchestrated run had ever been given it: a worker read its persona's
instruction to run that command, found the empty string, and had nothing to fall
back on and nothing to report it to. The persona's own fallback is for a dispatch
some *other* launch made, which is why it is stated there rather than here.

**The command is the engine's `onepipeline ask`.** `scripts/ask-manager.sh` exists only
so `ORCHESTRATOR_ASK_MANAGER` stays one executable path: it execs the `onepipeline` this
checkout's lock installed beside its interpreter, falling back to the one on `PATH` only
where the checkout has none, and decides nothing itself — every argument reaches the verb
in order, stdin reaches it unread, and the verb's stdout and exit status are the
adapter's own. The verb takes the question as words, as `--file <path>`, or on stdin —
the three forms every dispatched task spells out — and raises one `planner-question`
frame on the run's `surfaces` queue at `${ONEPIPELINE_RUNS_DIR:-runs}/<run-id>/channel`,
under the bus policy the run's launch record carries, which is the same policy a
manager's reply verbs read; `ONEPIPELINE_CHANNEL_ASKER` names the asker where one is set,
`--about <node>` names the node the question is about, and `--timeout <seconds>` moves
the reply window for one ask. It refuses at exit 2, raising nothing, a blank question, a
NUL byte, an unset run id, and a run whose launch record cannot be read.
`tests/e2e/test_ask_manager_shim_e2e.py` holds the adapter to that transparency and reads
a `--file` and a stdin question back off a real channel unaltered, and `tests/ask_seam/`
drives it over runs `just orchestrate` really launched.

Everything after the question is on the queue is the bus's (onemessagebus's `ask.md`).
The bus prints `correlation: <c>` on stderr as soon as the question is queued, and one
answer on stdout:

| Answer | Exit | When |
| --- | --- | --- |
| `{"answer":"reply","correlation":…,"reply":…}` | 0 | a reply echoing the question's correlation |
| `{"answer":"timeout","correlation":…}` | 1 | the window elapsed with no reply; the question stands |
| `{"answer":"abandoned","correlation":…}` | 1 | no reply, and nobody is attending the question |
| `{"answer":"refused","reason":…}` | 1 | the bus refused the question, or the reply bound to it |

Only `reply` carries a `reply` member, so an elapsed wait can never be read as the
manager's ruling. The correlation is stamped on the question by the bus and a reply
binds by it, so a wait never consumes another question's answer, and there is nothing
for a manager to echo and nothing for the asker to re-ask. A later listener naming the
same asker takes back a question an earlier one left abandoned.

The manager answers it as an ordinary blocking surface: `just channel-next RUN` hands it
over, and `just channel-reply RUN --correlation <c>` answers it — or `just channel-reply
RUN` alone while it is the one pending ask. See [A planner writes a reply
once](#a-planner-writes-a-reply-once).

`ONEPIPELINE_RUN_ID` names the run to ask on, and an unset one is refused rather than
guessed at. What sets it depends on the launch, measured per shape by
`tests/ask_seam/launch/test_launch_ask_seam_e2e.py`: **every node dispatch of a run carries it
as of onepipeline 0.44.4**, composed where the dispatch is made, so all three `just
orchestrate` shapes reach a worker that can ask. That names the release in force rather
than the one it arrived in — `executor::dispatch_env` has composed the pair since
https://github.com/nickderobertis/onepipeline/pull/76, and `AGENTS.md` carries that
release number, because this page is held to naming the adopted one alone. Below *that*
release only the **attached** shape did, and by accident of process rather than by
design — an attached driver starts its observer graph in its own process, and the export
leaked from there into every dispatch it made afterwards, which is why a launch passing
`--dag-graph off` carried nothing even attached. Every `just plan` dispatch carries the
id on either side of that bump, because that recipe exports it itself; that is sound
only because it refuses a name whose run root is already taken — `onepipeline` mints
`<name>-2` when one exists, so without that refusal the exported id could name a live
run belonging to somebody else's workstream. What a `just orchestrate` dispatch is given
is not derived here from the plan's `name`, which would be restating how a run id is
minted; the journey reads it back and checks it names the one run that launch created.
An observer member carries the run's id too, so finding the variable says which run a
process is *under* and never that it is a dispatch.

**The reply window is fifty minutes — 3000 seconds — and that value is a measurement.**
It is `reply_window_seconds` in `config/onemessagebus.yaml`'s binding for the `surfaces`
queue, which the engine records with the run and `onepipeline ask` waits by when no
`--timeout` moves it for one ask. The engine's old rendezvous waited about thirty seconds
when nothing set a window — 29.8 seconds measured — which is a supervisor's cadence and
not a manager's, and a binding that names no window gets the same 30 seconds. A
question worth blocking on is worth waiting past the next time somebody looks at their
terminal: fifty minutes is long enough that a manager who stepped away still answers,
and short enough that a wedged question is not immortal. The window used to be set
through `ONEPIPELINE_REPLY_TIMEOUT_SECONDS`, which governed the engine's wait exactly —
measured 5 seconds as 5.03, and 12 as 11.89 — and appeared in no `--help` output, found
in the pinned binary instead; it is now the verb's `--timeout` and a configuration key,
stated where it is set, so a caller that forgets to export something can no longer
return a manager's window to a supervisor's.

### What the retired channel scripts measured

This host once reconciled the planner channel by hand, in four pieces that are deleted:
`channel-serve.py`, the observer's judge-side filter between onejudge's supervisor frame
and the engine's own `channel serve`, a verb the adopted engine no longer has;
`ask-manager.sh` as a full wrapper, with
`ask-manager-contract.sh`, the rule it shared with the reply recipe; `channel-reply.sh`,
the recipe's reply guard; and `live_edit_check.py`, the envelope's criteria check. Every
rule below was an incident first, and each is now the installed `onemessagebus`
release's to keep, in the document that states it: onemessagebus's `codecs.md` for the
filter, `ask.md` for the wrapper, its contract and the reply guard, `validators.md`
for the envelope check, and `queues.md` for the queue race. **This section is
history**: nothing in it describes the tree as it stands, and a release number in it is
the one pinned when the measurement was taken.

#### The observer filter, `channel-serve.py`

- **It existed because the two halves agreed on the response and not on the request.**
  The engine's `channel serve` answered a ruling with exactly the `{completion, message,
  reason}` object onejudge's `supervisor` op expects, but onejudge wrote `{"op":
  "supervisor", "task", "persona", "done_when", "worktree", "history_name", "messages":
  [...], "session"}` to a judge command, and `channel serve` read `{"kind", "message",
  "blocking"?, "node"?}`. Naming `channel serve` directly as the member's
  `judge.command` was refused on the first turn — `the observer emitted a bad frame:
  unknown field 'op'` — and onejudge killed the member with `provider produced no
  output`, leaving the run driven but unwatched. The filter recovered the run id from
  the composed task's opening line, and turned the engine's answer to an elapsed wait,
  `{"answer":"timeout","correlation":…}`, into a non-completion so the member lived.
- **Raising prose duplicated findings.** While prose was raised automatically, a monitor
  with a finding to file had three moves and none was clean: the prose alone, losing the
  operation's node attribution; the operation and prose, two surfaces for one finding; or
  the operation and a quiet-turn sentinel, one surface and a false statement its judge
  scored against a bar about surfacing everything observed. It chose the middle every
  time. Of `root-causes-94-plan`'s 54 surfaces, 19 were findings and 8 `monitor` prose,
  and all 8 duplicated the finding immediately before them, raised three to thirty-five
  seconds later, six byte-identical and two restatements — 30% of the monitor-authored
  surfaces, degrading the unread-surface line a planner may never filter. Suppressing
  prose that merely resembled a recent finding would have caught at most six of the
  eight, because the two restatements shared very little wording, so the path was removed
  instead.
- **A monitor that found nothing was not a monitor that failed.** A frame carrying no
  assistant content was refused as a protocol failure, `oneagentgraph` recorded
  `member-died {"rule":"provider-failure","cause":"protocol"}`, and the run carried on
  reporting `ACTIVE` with nothing watching it: `spanish-language-tutor-upgrade` lost its
  observer five minutes in and ran roughly two hours that way.
- **A lost turn did not always arrive empty.** The harness wrote its own transcript into
  the last message instead — measured off this host's `runs/rc-fixes-brief` channel,
  fifteen JSON-RPC frames and 21,531 characters, most of it the prompt echoed back,
  ending in a `method: error` frame and a `turn/completed` whose `status` was `failed`;
  twenty of them queued unread on one run. So the filter raised a proven loss as
  ``monitor turn failed: usageLimitExceeded on codex. It said nothing, so there is
  nothing to answer; its 21531-character transcript is not repeated here.``, and its
  proof vocabulary was deliberately never widened, because widening it is how the next
  harness's shape outruns the classifier and a planner is sent to the wrong quota.
- **Republished transcripts flooded the host.** Measured on 2026-08-24, 26 oversized
  surfaces on this host, every one `status: completed` with `error: null`, were raised as
  the monitor's own words: 176.1 MB of protocol carrying zero model-authored characters, a
  3.6 GB journal holding one 699 MB event line, and a read-only `just runs` that needed
  5.7 GB of RSS. A bounded `monitor-transcript` kind existed for that and went with the
  prose path.
  <!-- dated-claim: incident what this host's own accumulated journals held on one day, kept as the reason a lost turn's transcript is never repeated in a surface rather than as a claim about any release -->
- **The scoring op killed every watched run's monitor at its end.** `judge` was refused
  — `got 'judge'`, `provider-failure`/`protocol` — though onejudge always asks it of a
  member carrying a `done_when`, so the monitor died at the end of every run it watched.
  It was then served as the planner's score, and the filter wrote the ruling's prose as
  `rationale` where onejudge reads a score's justification from `reason`, so every score
  it relayed arrived unexplained; the codec writes `reason`.
- **A manager's live edit killed the watcher it was supervising.** Through the engine's
  0.8.x releases a reply went to whichever reader arrived first, so a live edit —
  `{"version":1,"commands":[…]}` with no boolean `completion` — reached the monitor's
  judge side whenever it got there first. Forty of this host's recorded dag-scope runs
  died there, refused as `is not a supervisor ruling`, precisely while a manager was
  supervising. The engine then routed a reply by its halves (`Channel::answer_if_verdict`,
  `Channel::claim_reply`), and the filter kept recognising such an envelope and answering
  the member with a non-completion naming the edits, never re-sending it: the engine's
  `reply` applied an envelope's commands itself before queuing it — replying `{"op":"add",
  …}` to a real run answered `{"reply":0,"state":"applied","commands":"applied"}` and
  recorded `edit-committed` there and then, and the same envelope re-submitted came back
  `add: node 'added-by-the-edit' already exists`, while an op with no such guard (`retry`,
  `cancel`, `requeue`) would simply have applied twice.

#### The ask wrapper, `ask-manager.sh`, and its shared contract, `ask-manager-contract.sh`

- **The frame had to be one compact line.** A pretty-printed frame was refused as `EOF
  while parsing an object at line 1 column 1`, a message naming the symptom and not the
  cause.
- **The server answered its own timeout with a plausible ruling.** Through engine
  release 0.31.0, `channel serve` printed `{"completion":false,"message":"no planner reply
  within the timeout; continue",…}` at exit 0 when its window elapsed, which a caller
  checking only the exit status acted on as the manager's answer; the release after it
  printed the wait itself, and the wrapper refused that line as a timeout with nothing on
  stdout.
- **A reply was claimed by whichever reader arrived next.** Measured on a live run under
  the engine's 0.8.x releases, a re-ask returned
  `{"version":1,"author":"monitor","commands":[{"op":"context",...}]}` — a live graph edit
  addressed to the engine. Read against the engine's source at tag v0.11.0, `Reply`
  carried no surface id and `claim_reply` handed the oldest unclaimed reply to whoever
  polled next, while `serve` had no listen-only mode, so every frame it accepted queued
  another surface. So the wrapper minted a correlation token per question, acted only on a
  JSON object carrying a boolean `completion` whose `message` echoed that token, and
  discarded everything else; the contract file was the one statement of that rule, the
  token's prefix and the reference grammar, sourced by the wrapper and the reply recipe
  alike, because a reply the recipe waved through and the wrapper then discarded would be
  reported `delivered` and read by nobody. The bus's correlation is the upstream change
  that note asked for: stamped on the question, bound by the reply, and never consumed by
  a wait it does not match.
- **Re-arm, not re-ask.** A drawn ruling echoing another ask's token left this question
  pending, so the wrapper re-armed a listener behind a non-blocking note; one echoing no
  token had spent this question's own surface, and a run with nothing blocking pending
  refused every further reply (`run '<id>' has settled, so nothing will ever read a reply
  to it`), so the question went back as a blocking surface. The split was forced by a
  self-sustaining duplicate: on run `issue-28` a re-ask queued a second blocking copy, the
  manager answered both, and the orphaned answer was drawn — milliseconds after asking —
  by the next question put to that channel, which doubled in turn.
- **A listener is rented, the asker is not.** A session naming no
  `ONEPIPELINE_CHANNEL_ASKER` adopted nothing, so each re-arm withdrew the question: the
  queue held it in neither slot, a verdict naming it was refused, and the ask blocked for
  its whole window and was killed with nothing on either pipe. So the wrapper inherited a
  dispatch's asker untouched and named itself from its own token when nothing had.
- **A lifecycle dispatch could not find its run.** Its working directory is a session
  worktree with no `runs` directory, so an ask was refused `no such run '<run>' under
  runs` with no surface raised. The wrapper resolved the runs root in two rungs, each
  corroborated against the run's `launch.json` — the directory the engine would look in,
  then `ONEPIPELINE_NODE_SCRATCH_DIR` walked up to the run's own directory — and never its
  own checkout, because asking confidently on the wrong store is worse than being refused.

#### The reply guard, `channel-reply.sh`

- **A reply the asker could not use was reported `delivered`.** Three replies omitting
  `completion` were each reported delivered and each read by nobody, and the planner that
  asked stayed blocked for about thirty-five minutes, re-asking twice. So the recipe read
  `queue.json` before sending and refused an envelope the wrapper would discard, naming
  the missing field — only while a blocking surface carrying the wrapper's token was
  pending, and never for an envelope carrying commands.
- **A verdict for a question nobody had handed out reached nobody.** The recipe refused a
  ruling echoing the token of a blocking question still waiting to be read, keyed on the
  envelope's own bytes rather than on queue state, because a monitor's score is the same
  bytes on the queue as a token-less answer. It also merged which halves the envelope
  carried, and each note's `reached` read back off the journal, into the engine's receipt.
  A reply bound by the bus's correlation reaches its question whether or not it was
  handed out first, and one naming a correlation nothing pending holds is refused.

#### The envelope check, `live_edit_check.py`

- The recipe piped the staged envelope into it, and it answered by exit status — `0`
  send, `1` refuse, `3` a judged turn that answered nothing, `4` a structural refusal. It
  composed the **whole effective task** the engine would compose from the run's own
  record — an `amend` onto the node's current task, a `requeue`'s overrides onto the
  parked node — and kept a register of passes under the run root, keyed on the text and
  both tiers' bars. Its structural tier was added after a `requeue` amending `adoption:
  fast` onto a node publishing `local-direct` behind a releasing dependency was accepted
  and then failed an hour of work at its last step. Its successor is the bus's command
  validator, which reads no run state, because the bus caches a pass on the envelope's
  bytes and a verdict folded over the run would make that cache unsound — so that
  refusal is now the engine's and the merge path's.

#### A question that was raised and then dropped

- **The queue was a document every reader rewrote.** `runs/<run-id>/channel/queue.json`
  was read-modify-written by every read of the channel — `onepipeline next`, which `just
  channel-next` calls — and by the process writing a surface into it, with nothing
  serialising the pair, so a read landing over a worker's write rewrote the queue from a
  state that never held the question. The asker then waited its whole window for a
  question nothing would hand out. Its signature was three records disagreeing:
  `surfaces.jsonl` held the surface, the journal recorded it queued, and `queue.json` read
  `next_id: 0`. What made it fire was never established: it reproduced under a single
  serial pytest process, and contention for this checkout's `.venv` lock was not
  excluded. This suite's played managers stopped being the reader in that window by
  looking at the file read-only before reading through the verb — measured, `onepipeline
  next` over an empty queue rewrote the file under a new inode — after two consecutive
  publication gates each lost a question to it. The bus keeps the log as the record and
  the document as a sealed projection of it, so a lost write costs the next reader a fold
  and never a record (onemessagebus's `queues.md`, "The projection").

### Read profiles

Every read verb shapes its view through a named **filter profile**, and two ship:

| Profile | What it admits | Who reads it |
| --- | --- | --- |
| `planner` | the pipeline's own events — node and step dispatch, completion and failure, decisions, planner surfaces, and committed or rejected edits | the default for `just channel-next` and `just monitor` |
| `monitor` | those plus every dispatched `oneagentgraph` launch's own turns and tool activity | the [monitor member](#the-agent-graphs-a-run-launches), which exists to judge that detail |

`--filter <NAME|SPEC>` selects one by name or takes a filter file or inline JSON;
`--all` reads the store through no profile at all. The two are mutually exclusive
and the CLI says so. `just channel-next` and `just monitor` name neither, so both
land on the CLI's own `planner` default and pass a caller's choice through
untouched. A launch may define or override a profile for its whole run with
`--filter-profile NAME=SPEC`, and may drop events at *write* time with
`--filter-agentgraph` / `--filter-vcs` — which is a different decision, because
what a write filter drops is not in the store for any profile to admit later.

The attached `just orchestrate` stream is the whole merged view rather than the
`planner` profile: profiles are options of the reading verbs, and a launch takes
none.

### A planner writes a reply once

**Acceptance means delivery for a reply, exactly as it does for an edit.** `just
channel-reply` is one `onemessagebus` verb over `config/onemessagebus.yaml` and the
run's own channel directory, and choosing the verb is all it adds. An envelope carrying
a verdict, or sent with `--correlation`, is `onemessagebus reply surfaces`. An envelope
carrying commands and no verdict, sent with no `--correlation`, is `onemessagebus send
replies`, because `reply` refuses such an envelope whenever no question is pending —
most of a run — and a live edit binds to no question anyway. Either way the layout
appends it durably — a verdict to `replies`, bound to the ask it answers, and commands
to `commands`, which the reconciler drains — so nothing has to be listening at the
moment the planner writes, and there is no boundary at which the listener changes: the
reconciler runs for as long as the run does.

**A verdict binds by correlation, never by arrival** (onemessagebus's `ask.md`).
`--correlation <c>` names the ask it answers; with none, the verdict binds to the one
pending ask, and is refused naming how many are pending when that is none or several. A
correlation nothing pending holds — unknown, or already answered — is refused naming
it, with nothing appended. So a reply cannot reach a reader that did not ask for it,
and a question that has not yet been handed out is still answerable by its correlation.

The command reports what happened, on stdout, as the bus's one line — from `reply`, and
from `send` for a commands-only envelope:

```json
{"answered": 3, "correlation": "c-…", "sent": […]}
{"queue": "commands", "position": 7, "id": 7}
```

`answered` is the question answered and `sent` every record appended; a commands-only
envelope sent with `--correlation` goes to `reply` and answers nothing — `answered` is
`null` — leaving the ask pending. **Either line is a transport receipt, not a receipt
that anybody acted on the reply**:
whether the reconciler applied an edit is the run's record to say, as an
`edit-committed` or `edit-rejected` event.

Replies used to depend on a live rendezvous, so `channel-reply` failed with
`channel rendezvous timed out` whenever nothing held the endpoint open — which was
a function of *when* in a round it was sent, and invisible from the planner's side.
Delivering one ruling took retry loops of up to ten attempts during live
supervision on 2026-08-05. No reply class requires a rendezvous now.

**One reply class is refused, immediately and by name.** A settled run has no
reader left, now or later, so queuing a reply to it would park it where nothing
drains it:

```
onepipeline: refused: run 'demo' has settled, so nothing will ever read a reply to
it; no reply was queued
```

That is an exit-2 refusal on the spot rather than a timeout, and it is the same
liveness verdict `channel-next` answers `{"status":"finished"}` on. A surface still
awaiting an answer outranks it: the run asked for that reply, so it is accepted
whatever the liveness probe makes of the process that asked.

Edits are the exception in the other direction, and deliberately: an edit is
validated against the graph projected from `events.jsonl` and applied to the
desired graph, which outlives the driver. That is why an `add` is accepted against
a run whose driver has exited while a *verdict* to the same run is not — see [the
edit table](#live-graph-edits).

### Planner-update pacemaker

`just orchestrate` seeds a durable 1800-second planner-update interval; override
it at launch with `--heartbeat-interval SECONDS`. A channel-side pacemaker keeps
checking that clock independently of graph reconciliation, including while a node
is inside a long-running agent step. When due, it claims and dispatches a dedicated
check-in agent. That read-only actor's turn is a bounded sequence its task
prescribes in order: `onepipeline status <run-id>` for the one run, the run's
follow-up drafts through `onetaskgraph task list --source drafts --project
<run-id> --json`, one compact update composed from those two readings and sent
exactly once with `onepipeline surface`, then exit. Those two readings are the
whole of its reading — the task forbids command discovery (`--help` walks, `just
--list`), host-wide run listings (a bare `just runs`, `onepipeline runs`) and
recursive filesystem searches (`rg`, `grep -r`, `find`) by name, because two
recorded turns spent the member's whole finite deadline on exactly those and were
killed having raised nothing — and a question the readings cannot answer is
reported in the update as unanswered rather than investigated. The surface verb is
the one [`personas/check-in.yaml`](../personas/check-in.yaml) names, called
directly because that member's working directory is the graph member's own scratch
and there is no `justfile` there to reach. `just channel-surface` is the operator's
spelling of the same verb. The command queues the non-blocking surface without
waiting for a planner reply; the reconciler neither authors nor relays its content.

**The pacemaker's scope is structural, not advisory.** The member carries its own
`task` in `graphs/dag-scope.yaml`, and that task both scopes it to reporting and
forbids `onepipeline reply` by name. Live edits belong to the
[monitor](#the-agent-graphs-a-run-launches) and the planner. This member is
single-sided and finitely deadlined by `oneharness.check-in.toml`: it takes one
short turn and exits, so an edit issued from it would be answered by the reconciler
after the member that issued it had stopped watching — nobody left to read the
outcome, and nobody who saw the state that justified it. Its own task is what makes
that impossible whatever the run-level task happens to say.
`tests/e2e/test_supervisory_prompt_discipline_e2e.py` launches the shipped member on
its own and reads the prompt it was actually given — the sequence, the prohibitions
and the edit ban — so a task that is present in the file but not reaching the model
fails there; `tests/e2e/test_orchestrate_launch_e2e.py` holds the same on the
document, and `tests/test_pacemaker_discipline.py` reconciles the persona's copy
against it.

Sending and delivery are recorded as two different facts. Queuing the update
appends `planner-surface-queued` to `events.jsonl` — carrying the surface kind and
message, the `source` that sent it (`check-in` for a pacemaker update, `proposal`
for a worker's), and the `workstream` node when one provoked it. Successful
consumption through `channel-next` is what resets the clock and
appends `planner-surfaced`. A reader of the journal can therefore tell "nothing was
sent" from "updates were sent and nobody read them", which the delivered-only
record could not express.

### A worker that goes quiet

The pacemaker says what the run is doing on a schedule; the engine itself says when
one of its workers stops doing anything. It watches every in-flight dispatch for
the life of the run and surfaces a **non-blocking** proposal for one that has
recorded nothing past a threshold — naming the node, what was last heard from it,
and how long ago:

```
quiet-worker: no activity for 2611s (threshold 2400s); last activity: nothing
recorded since it was dispatched. The dispatch has not failed — decide whether to
cancel it, retry it, or let it run.
```

"Last activity" is the live stream a dispatch publishes,
falling back to when the engine dispatched the node when nothing has published at
all — which is itself the answer for a worker that died before its first turn. The
threshold defaults to 2400 seconds, comfortably past the 600-2000 second first turns
this host runs; set it for a whole run by exporting
`ONEPIPELINE_STALL_AFTER_SECONDS` before `just orchestrate`.

It is non-blocking because a stall is evidence rather than a verdict: the planner
decides whether to `cancel` the node, `retry` it, or let it run, and a blocking
surface would stop the graph's other workers to ask. A node is reported once per
quiet stretch — a worker that wakes up, works, and goes quiet again is reported
again; one that simply stays quiet is not repeated.

The [monitor member](#the-agent-graphs-a-run-launches) is the judgment beside this
signal, not a duplicate of it: the engine reports that a worker went quiet, and the
monitor reads the detailed stream to say whether what it was doing before it went
quiet was the work its task asked for.

#### The claim is a lease on the dispatch, not a lock held until someone reads

The heartbeat record carries an atomic `in_flight` claim so concurrent pacemaker
ticks cannot dispatch duplicate check-ins. It covers the **dispatch**: taken when a
due check-in is claimed, handed back when that dispatch settles either way, and
recorded with the pid and host that took it so a later tick can reclaim a lease
whose holder is provably gone — the same probe the driver-liveness verdict uses. A
failed attempt is recorded and becomes eligible again after the next interval
without blocking the graph frontier.

Held instead until *delivery*, it silenced runs: `record_surface` was the only
thing that released it and that runs on consumption, so one update nobody read left
`due=True, in_flight=True` for the life of the run and no later check-in could ever
be claimed. Two live runs sat that way for over an hour each on 2026-08-01 with the
interval lowered to 900s, which is the diagnostic that separates this from a clock
problem — the wedge was the claim, so no cadence change reached it. `just monitor`
is named everywhere as the way to follow a run, so the documented way to watch one
was the way to wedge it. Rendering is not reading; only `channel-next` consumes.

Exactly one check-in is ever pending, and it is kept current rather than kept
still: the next interval's check-in **replaces** the queued update instead of being
blocked by it. Being ignored therefore makes the harness louder rather than
quieter. The clock is not reset by queuing, so the staleness `just runs` and `just
status` report (below) is measured from the last update a planner actually read and
keeps growing while the queued content stays fresh. A queued update discarded
unread — behind a surface awaiting a reply — releases the lease with it.

Every planner-visible update — a monitor surface, a worker's proposal, or a
delivered heartbeat — clears the due signal and restarts the clock. The next
check-in falls due one interval after the later of the last update a planner read
(`last_surface_at`) and the last check-in attempt that settled (`last_attempt_at`);
both are persisted, so a restarted or adopted driver continues the same durable
countdown rather than starting a fresh interval. That comparison is made against
the *current* interval on every tick rather than baked into a stored deadline,
which is what keeps the interval the only knob that changes cadence.

That knob is **launch-only**. `--heartbeat-interval SECONDS` is an option of
`onepipeline start` and of nothing else — `adopt` does not take it, and no channel
verb does. The reply envelope is closed to unknown fields and accepts exactly
`version`, `author`, `completion`, `message`, `reason`, and `commands`, so a reply
carrying `"heartbeat_interval"` sent through `just channel-reply` is refused whole, with
nothing appended and a nonzero exit:

```
onemessagebus: replies: the reply is malformed: Additional properties are not allowed ('heartbeat_interval' was unexpected)
```

That is the bus's wording, not the engine's, and it names only the field it refused. The
host's bus prepares a reply by the planner-channel layout document it links, and
onepipeline's `docs/contract.md` declares this as one of the places where that document's
bus differs from the engine: a malformed envelope is refused in the bus's words. The
engine's own wording, which the channel used while the bus compiled the layout in, also
listed the accepted fields. It does not reach a reply sent through this host's channel
verbs, so read the list above rather than the refusal for what the envelope takes. The
verdict and graph edits in that reply are lost with it, so trying costs the whole envelope
rather than being a no-op. Pick the interval at launch.

Which side of which member a recorded oneharness session is comes from the
`history_labels` in the config that ran it — `role = "agent"` in
`oneharness.orchestrator.toml` and `oneharness.check-in.toml`, `role = "judge"` in
`oneharness.judge.toml` — layered under the run and node labels a dispatch
inherits. That is what keeps monitor and check-in infrastructure separable from a
node's implementation worker in `just history`.

While a consumed surface is persisted awaiting an answer, `just runs` and `just
status` report `waiting for planner decision` for blocking surfaces and `waiting
for planner reply` for informational ones, followed by the surface kind and
message. A queued, unconsumed heartbeat remains non-blocking and is not reported
as a reply wait. This distinguishes a run held waiting on the planner from one
that is actively executing work.

Surfaces nobody has read yet are reported separately, because they are the state a
planner who never attached is blind to: the row above says only `ACTIVE`, and the
`planner-surfaced` record they would look for is written on delivery, which has not
happened. Both views therefore add one line per affected run, under that run's own
row, naming four things: how many surfaces are queued, **what kinds they are**, how
stale the oldest one is, and the read that consumes them. The line itself is quoted
in [`AGENTS.md`](../AGENTS.md), which owns the rule that a manager's watch may never
filter it.

Two of those four are worth knowing before reading one. The kind breakdown is what
makes the line worth reading rather than counting — a pile of `monitor` narration
and the one `finding` inside it are different situations, and the count alone cannot
tell them apart. And the read it names is the **published verb**, `onepipeline next
<run>`, because the engine renders its own spelling rather than this host's; `just
channel-next <run>` is the same call.

The queue is `channel/queue.json` beside the durable `channel/surfaces.jsonl`; a
surface that has been consumed and is awaiting an answer is not part of it, and is
reported by the wait above instead.

**It is not read in queue order.** A blocking surface is handed out ahead of every
non-blocking one, however much older those are, so a worker's question cannot sit
behind a pile of observations — the failure this ordering was added for. And once a
blocking surface is `pending`, reading the non-blocking surfaces behind it leaves it
pending: only a verdict answers it, so reading a queue down never consumes the
question. Measured on a real run — a blocking surface queued fourth was handed out
first, and three further reads handed out the older non-blocking ones while
`pending` stayed on it throughout. A run reported `DRIVER DEAD` or `PARKED` keeps
the line saying why it stopped rather than an invitation to read updates nothing
will follow up on.

That wait describes a *live* launch only. A queued surface outlives the work that
queued it, so a run whose driver is gone never wears it as its `just runs` summary:
the row keeps its own progress summary and the line beneath it says why the run
stopped. `just status` is the surface-reporting view and keeps both, in that order
— the stopped line first, the stale wait immediately after — so the reason the run
is stuck stays visible without the run reading as live supervision.

Every proposal includes `surface.blocking`: `true` means the worker is awaiting the
decision, while `false` is informational and does not stop the graph frontier. Either
way a proposal is for a decision the planner must see **during** the run. Everything the
monitor and the pacemaker raise is non-blocking by construction: an observation is not a
question the graph should stop for.

### Follow-ups are drafted, not surfaced

A follow-up — work some party noticed outside its own scope that can wait to be verified
after the run — does not travel over the channel at all. It is **drafted**: written as an
unverified local Markdown task into the `drafts` plan source (`onetaskgraph.yaml`, root
`.follow-ups/` in the launching checkout, gitignored and outside `default_sources`), in a
project of its own per run — `projects/<run-id>.md`, with each draft under
`tasks/<run-id>/drafts/`. Drafts live there rather than in a dispatch's worktree so they
survive the worktree being reaped, and they are read by a follow-up agent after the graph
completes; nothing reads them during the run.

Every launch exports the seam through `scripts/follow-up-env.sh`: the source's absolute
root and its plugin at the plan store's environment layer, and
`$ORCHESTRATOR_FOLLOW_UP_DRAFT`, the one command every party drafts with. A worker drafts
as itself; the monitor drafts `--as monitor --member monitor`; the pacemaker drafts
`--as pacemaker --member check-in` and reports in each update how many drafts the run's
project holds; a manager drafts with `just follow-up <run-id>`. The party supplies only a
title, the repository, the paths and a body carrying four headings; the command stamps the
rest — the run, the dispatch's scratch, session, working directory, branch and head, the
node resolved from the run's dispatch registry by process ancestry, and the command that
reads the turns the draft came from. `orchestrator/follow_up_drafts.py` is the one source
of that record, and `"$ORCHESTRATOR_FOLLOW_UP_DRAFT" --help` states it in full.

Drafting is only for what can wait. Anything blocking — a decision fork, a constraint
that cannot be met, a finding the planner should act on now — still goes over the channel
immediately: a worker's blocking question to its manager, the monitor's
`finding`, the pacemaker's update, a planner's exceptions. A draft never stands in for any
of them.

After the run, `just follow-ups <run-id>` dispatches the follow-up agent over its drafts:
one direct node under `graphs/follow-up.yaml`, a single-sided member with no judge on
`oneharness.follow-up.toml`, given the task `config/follow-up-task.md` composes. Its turn
runs in `bypass` mode: the member works in its agent graph's scratch directory, which is
not a repository, and a `default`-mode turn there is refused by codex as an untrusted
directory and denied its tools by Claude Code. The mode is declared in that harness config
rather than on the member because the linked oneagentgraph admits no `mode` on a
single-sided member, so a single-sided member that has to run commands declares its mode in
its own harness config. It verifies each draft against the registered checkouts' `origin/<base>`, writes one verified
ticket per root cause beside the drafts (`tasks/<run-id>/tickets/`), and copies each onto
the `followups` GitHub Projects board, where every session's tickets accumulate —
commenting on another run's open issue for the same root cause instead of filing a second.
A ticket's issue is created in the repository its root cause lives in, as an item of the
one board: the ticket's `repositories` names that one repository, which must be under the
board's owner, and `board-status` refuses a ticket naming a repository outside that owner
before anything is asked of the board, so it is reported rather than filed.
`orchestrator/follow_up_tickets.py` is the one source of the ticket's shape and of
ownership on that board: a run changes only the issues its own tickets created and the
comments whose marker names it — its evidence on another run's issue, and its replies to
people's comments. The recipe refuses a run something is still driving,
launches nothing for an initial run holding no drafts or tickets after checking any
existing disposition account, and checks the mode's own account
once an attached run settles. `--feedback FILE` re-dispatches over the same run with the
manager's words in the task, `--detach` returns at the launch record printing the follow-up
run and its watch command, and `--to SOURCE` copies onto another configured source.

**A dispatch is composed in one of two modes, and the caller states which.** Initial
mode — every launch but a comment gathering — is the task above, and owes a disposition
artifact giving each of the drafts the recipe recorded before the dispatch exactly one
of `filed`, `not-reproducible`, `already-fixed` or `too-low-impact`, which
`python -m orchestrator.follow_up_tickets check-dispositions` reads back. Feedback mode —
what `just follow-ups-handle-comments` composes, passing `--comments` through — is the far
shorter `config/follow-up-feedback-task.md`: it answers only the comments that gathering
quoted, in order, changes only the ticket of an issue a quoted comment sits on, and owes a
response artifact `python -m orchestrator.follow_up_tickets check-responses` reads back
against the board's own replies. Each composed task's own acceptance criteria name its
validator, which is what binds the detached run the success hook launches, and an attached
run has the recipe re-run it. Before the split there was one task, and a comment-only
re-dispatch spent its paid turn re-reading and re-copying every unrelated ticket after its
replies were already posted, until it reached the provider's deadline.

**A ticket names its host and reaches the board as a proposal.** Evidence is a claim about
trees read on one machine — its registered checkouts, its installed tools, its run
journals — so a ticket's record's `host` names the machine its verification ran on, read
from `hostname` rather than typed, and its `## Evidence` section states that host where a
reader of the issue sees it. A new ticket lands on the board in `Proposal`, and the user
moving it to `Todo` is what accepts it, so a later agent sent to work on accepted
follow-ups selects them by the board's status alone. The board is therefore the record of
that decision and no copy undoes it: before every copy the follow-up agent runs the
module's `board-status` command, and a ticket the board already holds is copied carrying
the status the board holds it at. Withdrawing a ticket closes a proposal as not planned,
and a run never withdraws a ticket the board shows as accepted: it copies nothing, leaves
its own ticket as it is, and reports what it would have withdrawn and why. The statuses,
the record's keys and the board's options are stated in `orchestrator/follow_up_tickets.py`
and `onetaskgraph.yaml` alone, and `tests/test_follow_up_ticket_docs.py` holds this
paragraph to them.

**A person may defer a ticket instead of accepting it.** The store word `draft` is written
to the board's `Deferred` option: a deferred ticket is not accepted and no agent picks it
up, but a later run verifying the same root cause still comments on it, a re-copy keeps it
deferred, and a run never withdraws it. The module renders the whole vocabulary, which
`python -m orchestrator.follow_up_tickets statuses` prints and the follow-up agent's task
carries, and this copy of it is held to that rendering:

- **Board status `Proposal`**, written `backlog`: a proposal awaiting the user's decision.
  Who moves an item there: a follow-up run's first copy of a new ticket. Not selected by
  an agent sent to pick up accepted tickets.
- **Board status `Todo`**, written `todo`: accepted, and not yet taken up. Who moves an
  item there: only a person, which is what accepting a ticket is. **Selected** by an agent
  sent to pick up accepted tickets, the only status that is.
- **Board status `Deferred`**, written `draft`: deferred for later by a person: not
  accepted, picked up by no agent, and still taking new evidence. Who moves an item there:
  only a person. Not selected by an agent sent to pick up accepted tickets.
- **Board status `Queued`**, written `queued`: accepted, and claimed by a launched DAG
  whose node has not started, so it returns to `Todo` if that work does not happen. Who
  moves an item there: no person — a launched run's first projection, over the store's
  `delivers` relation. Not selected by an agent sent to pick up accepted tickets.
- **Board status `In Progress`**, written `in-progress`: accepted and taken up. Who moves
  an item there: a person, or a dispatch whose own task says to. Not selected by an agent
  sent to pick up accepted tickets.
- **Closed as completed at Status `Done`**, written `done`: accepted and finished. Who
  moves an item there: a person, or a dispatch whose own task says to. Not selected by an
  agent sent to pick up accepted tickets.
- **Closed as not planned at Status `Cancelled`**, written `cancelled`: withdrawn. Who
  moves an item there: a follow-up run withdrawing its own ticket that nobody accepted or
  deferred, or a person. Not selected by an agent sent to pick up accepted tickets.

A brief to pick up "accepted" follow-up tickets means the items at `Todo` and nothing
else: never an item at `Proposal`, `Deferred`, `Queued` or `In Progress`, and never a
closed one.

**Every proposal is written against the board's accepted fixes.** Before it copies
anything, the agent lists the board's accepted items — `Todo`, `Queued`, `In Progress`,
and `Done` where the fix has not reached the basis the ticket was verified at — and reads
each one's suggested fix as if it were already in: a ticket that fix removes is not filed
(its drafts are reported as dropped, naming the accepted ticket), one it narrows has its
impact and root cause written to what remains, and one it displaces states the fix that
remains right, with the displaced one among its rejected fixes. Whenever an accepted fix
changes a ticket that way, the ticket depends on it as the store's own `depends_on` edge,
outside the `orchestrator.follow-up` record — the board carries it natively as the
issue's dependency, and `onetaskgraph task deps` walks it from either end — and the
ticket's text says, at each place the fix changed something, which accepted ticket
changed it and how, by the item's URL. `validate` holds the edge's shape and reads no
board; `board-status` resolves every edge against the board before the copy, refusing an
entry the board no longer holds as an accepted ticket of another root cause whose URL the
body names, which on a re-dispatch is the signal to re-derive the ticket from the board
as it now is. A `Proposal` or `Deferred` item's fix is never assumed, though a clearly
related one may be named as related by URL with no edge. The same-root-cause path is
unchanged: an accepted item for a ticket's own root cause takes the run's evidence as a
comment and is never depended on.

**The run-end hooks are what launch it.** `just orchestrate` names
`scripts/run-ended.sh`, by its absolute path in the launching checkout, as both the
engine's `--success-hook` and its `--failure-hook`, unless the caller named that flag;
`just orchestrate --adopt` names neither, because the engine replays both from the
launch record. The engine runs at most one of them, once, when the run **ends** —
after the driver has let go of the run, in the launch directory, awaited under
`--hook-timeout` — keeps its output in `runs/<run-id>/hooks/<hook>.log`, relays it to
an attached launch's stderr, and renders it in `just results <run-id>` — each record
against the epoch it belongs to. A run a recovery edit reopened keeps every hook record
from before that edit on its journal, and those describe an ending the run has since
left, so on the adopted engine `results` labels such a record
`superseded`, naming the edit that reopened the run after it, and says outright when the
current epoch has recorded nothing yet, rather than leaving the last superseded
failure hook's instructions to read as where the run is now; a log a later firing
overwrote is not attributed to the earlier one
(`tests/run_end_hooks/test_run_end_hooks_e2e.py` reads both labels off a run retried to
completion). A hook never
changes how the run settled. onepipeline's own `docs/contract.md` (**Run-end hooks**)
is the contract; this host does two things with it:

- **success** (every node `done`) runs `just follow-ups <run-id> --detach` and prints
  `main work of run <run-id> is complete; follow-ups are being verified in run
  <follow-up-run-id>; watch it with: just watch <follow-up-run-id>`. A run that drafted
  nothing prints the recipe's own line and that no follow-up run was launched. It exits
  with the recipe's status. The follow-up run is detached because the hook is awaited
  and the run outlives that deadline, and it belongs to the main run's owner: the
  engine exports that owner's launcher identity to the hook, and
  `scripts/onepipeline.sh` keeps an exported identity.
- **failure** (a failed or skipped node, an unfinished graph with nothing left to
  decide, or a clean `stop`) launches nothing, prints one line naming the run and the
  reason the engine gave, says that no follow-up run was launched and that `just
  follow-ups <run-id>` verifies its drafts by hand, and exits 0.

A run paused on a decision (`awaiting-planner`) has not ended and fires neither. Each
ending has one hook epoch: an accepted edit that makes ended work live again, or that
carries an ended run to a different ending, opens a new epoch. So a failed run retried
to completion fires its failure hook and then its success hook, and a run whose failure
hook fired and whose failed node a manager then `settle`s `done` — its work having
landed some other way — fires its success hook for the complete ending that settle
carried it to; either way follow-up verification launches on recovery. A settle that
changes why a run failed without changing that it did ends it under the same hook and
fires nothing new. A driver that dies fires nothing.
`just plan`, `just finish-plan` and `just
follow-ups` name no hook, so a planning run and a follow-up run never launch a
follow-up run, and nothing else in this repository runs `just follow-ups` — the
success hook and a manager typing it are the only two ways. **Hooks arrive with an
engine adopted between runs**: a launch record written before `config/onepipeline.version`
named a release carrying them names no hook, and that run fires none, whichever engine
later adopts it. `tests/run_end_hooks/test_run_end_hooks_e2e.py` drives both hooks through a
real launch.
`monitor` renders `ACK REQUIRED` while any blocking surface, including closeout,
awaits a reply.

The raw reply schema remains available for edits and automation. A continuing
reply is `{"completion":false,"message":"what to do next","reason":"why"}`;
an approval is `{"completion":true,"reason":"what was verified"}`. Either may
also contain `"version":3` plus a `"commands"` array using the operations below.
That version is the engine's own `REPLY_ENVELOPE_VERSION`, and it reads **3** since
`settle` grew the optional `landing` above; it read **2** from the manager-note
collapse that removed `context` from the envelope until then. The move is additive
and the engine still reads an envelope at the version before it, so a caller that
sends `2` is accepted and simply cannot name a landing — which is why the examples
below name the current one rather than the oldest that still works.
The convenience recipes construct the common forms: `channel-approve` sends a
completed verdict, `channel-reject` sends a continuing verdict whose reason and
message are the supplied text, and `channel-continue` sends the same continuing
shape for a non-rejection instruction.

Worker onejudge processes never use the planner channel. The driver may author in
an isolated execution checkout, but the runs directory it uses must resolve to the
same host-visible path for the detached process and the planner. The ledger and its
sibling `channel/` directory cannot live only inside a disposable worktree or
container-private filesystem.

### A finding names the rule it is grounded in

Both observer surfaces raise findings — the
[monitor](#the-agent-graphs-a-run-launches) on every turn, the
[pacemaker](#the-planner-update-pacemaker) when its schedule comes due — and a report
that calls something a **rule violation** has to quote the file and the line the rule
comes from: a node's task, a persona, a config, or a document of the repository under
work. What cannot be pointed at that way is an **observation**: what was seen and why
it looked wrong, with the ruling left to the planner.

The distinction is not tidiness. An observer that infers the rule an agent is judged
against reports a protective act as a breach, which costs a planner more than silence
would and discredits every other finding in the same update. So each observer's own
supervisor rejects an ungrounded violation and requires it re-reported as an
observation, rather than leaving the planner to sort the two apart.

Stated to the model on both sides of each member, in
[`personas/orchestrator.yaml`](../personas/orchestrator.yaml) and
[`personas/check-in.yaml`](../personas/check-in.yaml), and held to this section by
`tests/test_observer_grounding.py`.

### Live graph edits

Send an edit envelope to `channel-reply`, at the version the reply schema above names:

```sh
just channel-reply RUN <<'JSON'
{"version":3,"commands":[{"op":"reparent","id":"pending","deps":["slow_b"]},{"op":"drop","id":"slow_b","dependents":"detach"},{"op":"attest","ref":"approve"}]}
JSON
```

The accepted commands are:

| `op` | Required fields | Effect |
| --- | --- | --- |
| `add` | `node`: full node mapping | Add a new node. Its `deps`, if any, must name graph nodes or valid cross-DAG references. |
| `drop` | `id`; `dependents`: `"drop"` or `"detach"` | Remove the node and recursively drop its dependents, or detach its direct dependents. |
| `reparent` | `id`; `deps`: list of dependency references | Replace an unstarted node's dependencies. |
| `retry` | `id`; `node`: full replacement node mapping with a new id | Supersede a running, failed, or cancelled node with a fresh lineage and redirect its direct dependents. |
| `cancel` | `id`; optional `reason` | Park a pending or running node: [interrupt its live turn, kill the dispatch if it has not exited by the grace period](#what-a-cancellation-does-to-a-live-dispatch), and hold the node out of the frontier until a `requeue`. `reason` is the parking author's own words, recorded on the park beside who issued it. It is optional so every `cancel` written before the field existed parks exactly as it did, and **present-and-blank is refused** rather than recorded: a park carrying only a node id is indistinguishable downstream from a node idle for no reason anybody decided, and observers have requeued deliberate decisions read that way. |
| `requeue` | `id`; optional `amend`: partial node overrides | Return a parked node to the desired frontier, optionally amending it (for example `max_turns`, or a `resume` pin onto the preserved branch). Refused while that node's dispatch is still in flight. |
| `attest` | `ref` | Complete a currently ready, waiting human action. |
| `complete` | `reason` | Journal the planner's completion request independently of graph mutation. |
| `note` | `id`; `addressee`: `worker`, `supervisor` or `both`; `text`; optional `criterion`; optional `deliver`: `live` or `next`; optional `persist` | The **one** manager-note op. Deliver one note into the node's dispatch, to whichever party of it is speaking, with the other party receiving it in that party's response — and, where no turn took it, carry it to the node's next dispatch. `deliver` decides whether live delivery is attempted and `persist` decides whether the note is composed into the node's next dispatch; they are two axes rather than one, and neither answers the other's question. A `criterion` it carries enters the acceptance criteria the judge of the conversation it reached decides against; it binds that conversation and not the node's stored bar, so `amend` is still the op for a ruling that has to survive a re-dispatch. `addressee` is required and never guessed — a judge handed an update to the *worker's* task must not take the worker's job on. Blank `text` is refused, an `id` naming a node the graph cannot reach is refused, and so is a note that would reach nobody, each naming which it is. See [Carried planner context](#carried-planner-context) for the four `deliver`/`persist` combinations and the dispositions the op answers with. |
| `amend` | `id`; `text` | Replace the binding amendment that becomes part of the node's effective task for its next and later dispatches. Blank text and a node already settled `done` are refused. |
| `settle` | `id`; `outcome`: `done` or `failed`; `evidence`; optional `landing`, `release` | Settle a node at what an operator can see it reached, from evidence this run never observed — the case being a change that merged while the node's own record read `failed`. It mutates no edge and moves no lineage: the node keeps its id and its dependents, and only its recorded state moves, which is the thing that was wrong. `evidence` is required and never blank, and is journalled as the reason the node is in the state it is. A settle that changes nothing is refused as a duplicate; a node that settled *something else* is exactly what it is for, and the earlier settlement stays in the journal beside it. `landing` names **where the work landed** — the commit its change reached its base at, or the change request a person reads it in, either being a spelling `onevcs` resolves. It is the authoritative landing for manager-facing results and release correlation, with the stated-commit or stated-change-request evidence tier instead of a re-read of the superseded branch. An envelope without `landing` keeps the prior settlement behavior, and a present but unusable landing is refused rather than recorded. `release` may accompany a landing with the release target and semantic version that carry it; the engine records that attribution against the stated landing, while an invalid version is refused. |
| `finding` | `message`; optional `id`, `blocking` | Raise what the author saw as a planner surface of kind `finding`. Compiles to a `finding-raised` operation and mutates no graph. `message` may not be empty; `id`, when given, must name a node the run has and files the surface against that workstream; `blocking` defaults to false. |

#### Who issued an edit, and what that bounds

The envelope's optional `author` names who is asking: `planner` (the default, and the
one author the planner-channel layout declares) or an author `config/onemessagebus.yaml` declares — here
`monitor` alone. It is not decoration: the engine records it on the `edit-committed`
event, and for an edit by any author but the planner it also queues a non-blocking
`edit-applied` surface, sourced to that author, naming the command — so a fix the
[monitor](#the-agent-graphs-a-run-launches) applies is reported as the monitor's
without the monitor also having to report it.

`finding` is the exception, and deliberately so: it raises the finding's own
surface and **no** `edit-applied` surface beside it, because there is no edit to
report — the surface *is* the report, and queueing a second one would double every
observation in the one line a planner may not filter.

It is also a bound, and the bound is this host's declaration rather than anything the
bus or the engine builds in. `authors.monitor` in `config/onemessagebus.yaml` grants
exactly `add`, `retry`, `cancel`, `requeue`, and `finding`, and gives every other op the
reason the channel refuses it with — before anything is appended, as it refuses an
author the file does not declare at all, and a completion verdict from a monitor the same
way:

```
onemessagebus: replies: 'drop' is not an op the monitor may issue: removing work from
the graph is a decomposition decision the planner owns. Surface it to the planner
instead
```

`drop` and `reparent` are decomposition decisions, `attest` belongs to the person
who took the action, and whether the run is finished is the planner's verdict
rather than an observation. A `finding` is refused only on its own contents: an
empty message with `a finding carries what was found: this one has an empty
message`, and an `id` the run does not have with `cannot raise a finding about node
'nosuch', which this run does not have; it has: research`. Each refusal names the available action, because the
monitor's escalation path is exactly what it is meant to reach for. That bound is
stated to the model in [`personas/orchestrator.yaml`](../personas/orchestrator.yaml)
and enforced by the channel, and
`tests/e2e/test_orchestrate_launch_e2e.py` holds both halves — that the four
refusals are refusals, and that an in-allowlist op is judged on the graph's state
rather than on who asked.

A command-only envelope gets a synthesized continuing verdict. Commands can
instead accompany either legacy verdict, for example:

```json
{"completion":false,"message":"apply the replacement and continue","reason":"the failed node is retryable","version":3,"commands":[{"op":"retry","id":"failed","node":{"id":"retry","task":"No diff","expects_no_diff":true}}]}
```

`complete` is the versioned equivalent of a completion verdict and may share an
envelope with graph edits:

```json
{"version":3,"commands":[{"op":"complete","reason":"publication and follow-up triage verified"}]}
```

Completion is decoupled from scheduling: the reconciler journals it for audit
and replay, while the graph continues to settle its frontier.

Every delta is validated against the live frontier before commit. The resulting
graph must still satisfy the normal plan schema: ids and referenced dependencies
must exist, and dependencies cannot form a cycle or self-edge. `reparent` cannot
change a started node; `retry` requires a running, failed, or cancelled target and
a new replacement id; `cancel` requires a node that is pending or running and not
already parked, so a settled or unknown node is refused by name; `requeue` requires
a parked node and refuses an `amend` that rewrites `id` or `deps`, which are `add`'s
and `reparent`'s to change; `attest` requires a ready waiting human action; `note`
requires a node the graph holds and can still be reached, so one aimed at a node that
already settled `done` and asks for no live delivery is refused rather than accepted
into nothing. `drop` must
state the dependents' fate and cannot remove the last publication anchor while an
unresolved same-identity dependent remains. Commands are reconciled in
order. Each accepted delta, including a multi-edge reparent or retry, is appended
by the reconciler's single writer as one `edit-committed` event carrying both the
submitted `command` and its compiled `operations`, so replay sees all of that
delta's compiled mutations or none of them, and reconstructs the graph from what
was actually submitted rather than inferring it. Channel frames are locked,
acknowledged JSON lines and are not limited to a FIFO's atomic-write size.

**Every edit is applied or rejected, and the run records which.** There is no round
for an edit to be inside of: the desired graph is what an edit changes, and it outlives
any one dispatch. That is why an `add` is still accepted against a run whose driver has
exited, while a *verdict* to a settled run is refused — nothing will read the verdict,
but the graph is still there to be edited and an adopted driver will act on it.

`just channel-reply` appends accepted edits to the channel's durable `commands` queue —
through `onemessagebus send replies` when the envelope carries commands alone and names
no `--correlation`, through `onemessagebus reply surfaces` otherwise, as [A planner
writes a reply once](#a-planner-writes-a-reply-once) says — and the reconciler drains
them from there, validating each against the graph projected from `events.jsonl` and
answering each claimed command. The verb does not wait for that answer; its exits are
the bus's (onemessagebus's `cli.md`):

| Exit | Meaning | stdout |
| --- | --- | --- |
| 0 | the envelope was appended; the reconciler applies or rejects its edits from the queue | `{"queue":…,"position":…,"id":…}` from `send`, `{"answered":…,"correlation":…,"sent":[…]}` from `reply` |
| 1 | the bus said no, with nothing appended: the validator refused or could not judge the envelope, or — through `reply` — its correlation binds no pending ask | the reason, on stderr |
| 2 | the envelope was not a JSON object, its correlation was malformed, or the invocation was a usage error | the reason, on stderr |

Before anything is appended, an envelope carrying commands is held by the one validator
`config/onemessagebus.yaml` declares on `replies` — `scripts/envelope-review.sh`, which
runs `orchestrator/envelope_review.py` — to the bar a plan's task is held to: an
`amend`, the whole task an `add`, a `retry` or a `requeue` states, and the `criterion` a
`note` may carry. The envelope is judged **as it states itself** and no run state is
read, because the bus caches a pass on the envelope's bytes and a verdict that also
depended on the run's journal would make that cache unsound.
`orchestrator/criteria_guard.py`'s matchers read every stated task, then one judged turn
of the `just review-plan` reviewer reads a **novel whole task** — an added node, a
retry's replacement, a requeued node's amended task. A correction — a bare `amend`, a
note's `criterion` — is matched and never judged; a note's `text` is never read at all.
The bus judges the whole offer before appending any of it, so a refusal by either tier
sends nothing and its findings are the reason on stderr; a judged turn that answered
nothing is **unjudged**, saying `could not be judged`, and is never sent — send the same
envelope again once the harness answers rather than correcting it. Only a pass is
cached, by the bus, under `.cache/envelope-passes` keyed on the envelope's bytes and on
what `--bar-fingerprint` prints, so moving either tier of the bar invalidates every
recorded pass (onemessagebus's `validators.md`).

Ahead of both, every node an envelope states — what an `add` states, and a `retry`'s
replacement — is held to the structural rules `just check-plan` applies to a plan's
nodes (`orchestrator/structural_guard.py` lists them once for both tiers), over the
graph the envelope's own nodes form, and a node they refuse is refused with that rule's
own message. A `requeue` folding fields onto a parked node's own `repo` and `deps` is
not in that envelope, so it is the engine's and the merge path's to refuse.

An edit that reaches the queue can still lose a race to the live frontier. Every
rejection is surfaced as a `reconciler: rejected ...` proposal and recorded as an
`edit-rejected` event carrying the command and the reason. No accepted command is
silently dropped.

Both edits that change only *eligibility* — `attest` and `reparent` — wake the
scheduler on the same reconciler pass. `blocked` and `skipped` are derived
statuses, so every committed edit discards them and re-derives them against the
new graph; a node the planner just made eligible is scheduled against a free
concurrency slot without waiting for an unrelated event.

**A retry may name only one branch, and it gets that branch every time.** A
lifecycle replacement node that carries both a `branch` pin and a `resume`
checkpoint is refused at submission when the two name different branches: the
lifecycle honours the checkpoint's branch and ignores the pin, so the planner
would not get the branch it named. A retry that states a `resume` and **no**
`branch` is pinned to the resume's own branch, because naming a continuation is
naming the branch it lives on. A pin is honoured whether or not the branch it
names still exists: on the onevcs the adopted engine links, a session **continues**
the copy some checkout of the identity or origin carries — opening its worktree at
that branch's tip and merging the base in — and cuts the name fresh from the base
when nothing carries it. So a branch that was landed and deleted between the
attempt and the retry is a fresh cut under the pinned name rather than a failure,
and it is the `resume`'s `completed_steps` that keep the continuation from redoing
steps the branch already holds. What is refused is an ambiguity nothing can
resolve — a checkout's copy of the name and origin's that have diverged, neither
carrying the other — and that ends the dispatch as `infrastructure-failure`
carrying onevcs's own message, which names both tips and what to reconcile.
A replacement that names **neither** a `branch` nor a `resume` is still pinned:
`edits::compile_retry` runs `inherit_preserved_branch` before the envelope is
compiled, and it copies the superseded node's `branch` *and* its `resume` onto the
replacement. That is deliberate — the attempt being retried ran, committed, and
stopped, so cutting a fresh branch beside its preserved one would retry the
publication against an empty tree and leave the committed work for a person to
find — and it means an empty envelope is a request to continue, never a request to
start over. Naming either field is answered with what was named. So there is no
envelope that starts the work fresh: to put a retry's work on a new branch, `add` a
new node carrying the task and `reparent` the superseded node's dependents onto it.
This is why an accepted `retry` cannot quietly re-derive the work somewhere else:
the reconciler honours the continuation the planner named, or the one the run
recorded where they named none, and the dispatch says why not when it cannot.

Dropping or retrying a running node raises its cancellation signal, and that
signal [reaches the agent](#what-a-cancellation-does-to-a-live-dispatch). A
direct dispatch stops; a lifecycle dispatch preserves commits already made on
its branch with incomplete provenance before it settles `cancelled` (publication
already in its commit phase may finish). Verify and publish preserved lifecycle
work with [`just repo-recover`](repo-lifecycle.md#integrating-completed-workstreams).

#### What a cancellation does to a live dispatch

Raising the signal used to be the whole of it, and no agent process read it: a
`cancel`, a `drop`, and a `retry` all left the dispatch running until it stopped
itself, which is how one node reached 78 commits through two supervisor freezes.
Every edit that raises the signal now acts on the dispatch, in two steps, and the
planner sees a **non-blocking** surface for each:

* `dispatch-interrupted` — every turn the dispatch has named was asked, over
  `oneagentgraph`'s own turn-control lever, to start nothing new, commit what it
  has, and end. The surface names each turn and what the delivery answered: the
  running turn took the redirection, there was no turn to redirect, or the lever
  itself failed. None of those three is a failure, and none of them stops the
  clock. Expect the dispatch to settle shortly afterwards, with its work
  committed. A dispatch that has not yet named a turn has nothing to interrupt,
  and the surface says so.
* `dispatch-killed` — the grace period expired with the dispatch still running,
  so it was torn down and its process tree reaped. Whatever its turn had not
  committed is gone. Expect this only after the deadline, and read it as the
  reason a branch is short of what the transcript shows.

Either way the settled node carries how it stopped in its own `detail`, so a
reader who arrives after the surfaces have scrolled past can still tell the two
apart.

The grace period is configurable for a whole run by exporting
`ONEPIPELINE_CANCEL_GRACE_SECONDS` before `just orchestrate`; the default is the
engine's to state, and `onepipeline`'s own surfaces name the number of seconds
they are counting. An unusable value falls back to that default rather than
turning every cancel into an immediate kill.

### Parking a node, and picking it up again

`drop` removes a node and refuses to remove the last unresolved publication anchor;
`retry` demands an immediate successor. Neither says *stop for now*. `cancel` does:
it raises the same cancellation signal — [interrupting the live turn and killing the
dispatch that outlives the grace period](#what-a-cancellation-does-to-a-live-dispatch)
— preserving the branch exactly as a drop does and leaving the publication anchor in
the graph, and settles the node `parked` instead of `cancelled`.

Parked is a held state, not a failed one. The run settles without the node, its
dependents settle `blocked` rather than `skipped`, and the run's own state is
`waiting`. The flag lives on the node definition, so it survives everything that
reads the graph back — a driver that is adopted after this one dies **never
redispatches it** — and a parked lifecycle node keeps its preserved checkpoint, so
a later `requeue` adopts that branch rather than cutting a fresh one. `just
results` reports the node as `parked` and names the preserved branch.

Parking the node is not the same as stopping its dispatch, and a `requeue` sent
before that dispatch settles is **refused** — naming the graph run still carrying
it and how long it has been running. So a cancel is followed by *waiting for the
node to settle*, not by an immediate requeue: until then the dispatch still holds
the node's workspace, and a requeue accepted there would return the node to a
frontier where it waits silently on the occupancy lease its own predecessor holds.
`just status` names that wait — a `ready` node reports either the session holding
its repository's workspace or that it is queued for nothing but a slot, and says so
separately again when this host could not ask.

`requeue` then puts it back on the desired frontier, and the next reconciler pass
dispatches it through normal adoption:

```json
{"version":3,"commands":[{"op":"requeue","id":"sweep","amend":{"max_turns":32}}]}
```

An `amend` mapping is merged onto the node before it is redispatched, which is where
a raised turn budget or an explicit `resume` pin onto the preserved branch goes. It is
validated as the node it produces, so a malformed pin is refused at submission rather
than at the next dispatch. Omit it to requeue the node exactly as it was parked: the
compiled `node-requeued` operation then carries no `amend` at all, so "amended
nothing" and "amended with nothing" are one record rather than two.

## Node shapes

A qualified project id resolves through `onetaskgraph.yaml`. Project metadata named
`onepipeline.<field>` supplies plan-level settings; each project task becomes a
top-level node, with its body as the dispatched task and its `onepipeline.*` metadata
supplying node fields. A task's `depends_on` records supply ordinary dependencies.
The engine preserves the resolved project as JSON in the run ledger, so recorded
Unicode survives to `just history-show` and the read API.

Every top-level node needs a unique `id`; `deps` is an optional list of other
top-level ids or wait-only cross-DAG references of the form
`run:<run_id>#<node_id>`. Omitted `kind` defaults to `agent` for compatibility.
Cross-DAG edges resolve through the active-runs index and the upstream journal.
An unknown or inactive run, unfinished node, or failed node leaves the consumer
blocked. Once an upstream succeeds, the consumer records its journal sequence;
if that journal later advances, the consumer emits a non-crashing
`upstream-modified` event for planner review without rerunning work. The watch
outlives the node that carried it: a transition keeps the reference rather than
removing it as a satisfied dependency, and passes it to the dependents of a
consumer it carried out — see [The graph of record is the live graph](#the-graph-of-record-is-the-live-graph).

The planner writes every agent node and step `task` with this prose template:

```markdown
## What
<the change>

## Why
<the user's motivation: impact and decision driver>

## Acceptance criteria
<detailed, specific source of truth for done>

## Additional info
<optional; omit the section when empty>
```

The task is visible to both the worker and judge, so its Acceptance criteria hold
all detailed, change-specific requirements — and, since schema version 2, they are
the *only* place a per-node requirement can live. `Why` is the user-facing
impact and what drove the decision, not an orchestration handoff. If the request
does not make that why clear, the planner must ask the user before dispatch rather
than inventing it.

### Where a node's review bar lives

There are two, and only two, and neither is per-node prose the planner writes twice:

1. **The node's own `## Acceptance criteria`.** This is what a planner tunes when a
   dispatch needs a different bar. onejudge shows the judge the transcript, and the
   transcript's first message *is* the task, criteria included.
2. **`config/onejudge.base.yaml`'s `user.done_when`**, which every dispatch shares.
   onejudge hands that string to the judge verbatim as its criterion, and it is
   phrased against the task — "every acceptance criterion stated in the task is
   met" — so criterion (1) is what it resolves to for each node.

   One thing a planner has to know about how it arrives: a node's `persona` is a
   name resolved against roles built into the tool rather than against `personas/`,
   and a built-in role that declares a bar of its own is enforced **alongside** this
   one rather than in place of it — the judge is handed `Both of these must hold:`,
   this clause first and the role's second. `planner`, `reviewer`, and `researcher`
   declare one; `engineer` and `docs-writer` do not. A role displaces this clause
   only by declaring `user.done_when_replaces_base`, which none of the five does, so
   every node keeps criterion (1) whichever role it names. See
   [Which of these files a dispatch actually reads](../personas/README.md#which-of-these-files-a-dispatch-actually-reads).

Keep the shared bar to measures true of every dispatch alike. A clause naming a
specific check tier is the failure mode: the bar this one replaced demanded that
"every lint tier has passed" and refused complete work in repositories that run no
such tier.

Verify the mechanism rather than reasoning about it: a `command`-provider onejudge
run shows the judge call carrying `task`, `done_when`, and `messages` as separate
fields, and the `oneharness` provider composes them into one prompt reading
`Original task:` … `Completion criterion:` … then the transcript.

An unsatisfiable criterion does not fail fast. The simulated-user supervisor is
working correctly when it refuses completion, so the worker is re-asked until the
turn cap — spending a full attempt, often several, to produce a bare `task-failed`
that names neither the criterion nor the cause. Before
dispatch, ask of each criterion: what would the worker run to satisfy it, and can
that command succeed right now? When a criterion's proof is necessarily
indirect, pair it with an `## Additional info` instruction to state the blocker in
the final assessment and stop rather than wait.

| Shape | Required fields | Meaning |
| --- | --- | --- |
| Direct agent | `persona`, `task`; no `repo` | Dispatch one real onejudge process in the selected project directory. |
| Lifecycle agent | `repo`, plus `persona` + `task` or `steps` | Work on an isolated branch/worktree and publish through the repository's merge-path gate and registered policy. Dispatch refuses identities without an executable pre-push hook or required PR checks. |
| Human | `kind: human`, `task`; no persona or execution fields | Record an action only an external person or outside system can perform. Planner review, acceptance, validation, and integration happen through live channel edits, not a human node. |

**`repo` is the loaded node's field; the stored task record spells it as
`repositories`.** A GitHub-hosted target repository is named once, in the record's own
top-level `repositories` list, as exactly one normalized origin
(`repositories: ["github.com/nickderobertis/oneharness"]`), and the engine reads the
node's `repo` from that list's first entry — that field is also what the plan store
files the task's issue by, so a record with it empty lands its issue in the store's own
repository. The reserved `onepipeline.repo` metadata key is written only for an identity a
normalized origin cannot hold — a local checkout `onevcs` knows by its absolute path —
and never beside a non-empty `repositories`; the engine refuses a record naming both, and
`just check-plan` refuses a hosted identity written on the key, naming the origin to
write instead. `onepipeline.execution_checkout` stays a registered alias. The rule is
onepipeline's, stated in its `docs/contract.md`; `personas/planner.yaml` carries it to
the planner with one example of each shape.

An agent or lifecycle node may also carry `context`: one planner note,
rendered after the task prose as a `## Planner context` section stating that it
reports observed state and adds no acceptance criteria. A workstream renders it
into every agent step, since the note is about the node they share, and leaves
human steps as written. A human node cannot set it — the note is addressed to a
dispatch, and a human node has none. The planner rarely writes it by hand: it is
where a live `note` no turn took is carried to, and it lasts [one
dispatch](#carried-planner-context).

An agent node or lifecycle agent step may instead set `expects_no_diff: true`
with `task` and no `persona` or `max_turns`. This explicitly declares that the
task expects no repository change and no separate review evidence. It settles as
`done` with the existing `no-changes` outcome without dispatching onejudge. The
executor does not infer this from task prose. Combining the declaration with
`persona` or a turn budget is rejected while loading the plan, before any provider
time is spent. Omitting `expects_no_diff` preserves normal dispatch behavior.

A lifecycle `steps` list is its own DAG. Agent steps require `persona` and `task`.
Persona names may be top-level general roles (`engineer`) or slash-qualified
repo-specific roles (`crozier/crozier-corpus`).
Human steps require `kind: human` and `task`, and are referenced outside the node
as `NODE_ID/STEP_ID`. Steps share one branch and run serially in topological order
because concurrent writers cannot safely share a worktree. See
the records
[`examples/projects/tracked-release.md`](../examples/projects/tracked-release.md) and
[`examples/tasks/tracked-release/`](../examples/tasks/tracked-release) for direct,
lifecycle, top-level human, and nested human nodes in one plan.
The `/` separator is reserved: top-level human ids and human step ids cannot
contain it. Existing lifecycle node ids may contain `/`; nested completion strips
that node's exact prefix rather than assuming the first slash separates the step.
Resume metadata is accepted only on a workstream containing a human step. Its
explicit branch/base must agree with the node, completed steps must be unique and
dependency-closed, and a GitHub PR URL must name the lifecycle repository.

## Decomposition and scheduling

A fresh onejudge process pays a fixed setup cost to read and understand its
project. It is also a highly capable coding agent, pair-programmed with and reviewed
by a simulated-user supervisor that pushes back until the task is actually done.
Bias toward fewer, larger coherent tasks that amortize setup. Split only for
genuine parallelism, a real dependency, or a genuinely different role or review
bar — not simply to give a capable agent a smaller slice. Put every subtask-specific
requirement in the structured `task` prose, under `## Acceptance criteria`: that
list is the node's review bar, so it is the thing to tune when a dispatch needs a
different one. Use `max_turns` when a task needs more room, rather than proliferating
personas. Every dispatch already has the built-in supervisor/reviewer; reserve a
dedicated `reviewer` step for complex DAGs where it reviews and integrates several
agents' independently produced work. Dependencies should name only real inputs so
unrelated branches remain parallel.

Do not split implementation from the tests that prove it into separate nodes or
steps: the implementing agent writes those tests in the same dispatch, and the
unit settles fully proven. The narrow test-focused exception and persona choice
are defined in [the planner persona](../personas/planner.yaml), as is the
judgment about when an interface seam is worth a split at all.

A split that follows an interface seam models that interface as its own node whose
deliverable is the surface itself: the new route, method, or CLI command backed by
a no-op or sample-data implementation, or a **new optional field** with a defined
default and existing callers untouched. A seam is any agreement whose two sides can
move independently, so this node shape carries a persistence or storage schema and
the data model behind it, and the boundary between two internal collections of code
— packages, modules, libraries, services within one repository — on the same terms
as a call surface, and carries a seam none of the examples here names for the same
reason. What varies by kind is only what its first landing looks like:

- **A storage or data-model seam lands additively, reachable by the writers and
  readers that predate it and required by none of them**: a nullable or defaulted
  column, a key or document field the existing shape's consumers ignore, a format
  or entry carrying a version marker old readers already tolerate. Nothing writes
  it yet, nothing is backfilled, and the shape already there stays authoritative.
  The migration that populates it, the switch of the authoritative read, and the
  retirement of the old shape are the implementation's and each is a later node.
- **A seam between internal collections of code lands as the boundary declared
  with nothing moved across it**: the module, package or service interface exists
  with its names, types, and ownership stated, satisfied by delegating to whatever
  holds the behavior today, and every existing caller still reaches what it reached
  before. Relocating the implementation behind it and re-pointing callers are later
  nodes.

Its `task` states the contract literally — route with request and response fields
and types, the exact signature, the field name, type, and default, the columns or
keys and what each one holds, or the names one collection of code exposes and what
it owns — because the producer node and every consumer node restate it from there.
Its acceptance criteria are satisfiable inside its own dispatch: the surface
exists, and existing behavior is unchanged. Name it in the `deps` of the real
implementation and of each consumer; those siblings then become ready together
instead of serializing, and each ships against the default, the sample behavior,
or the shape already in the store until the implementation lands.

A consumer that finds a departure it wants — a missing field, a wrong shape, a
column or module boundary it would rather have drawn elsewhere, a better
decomposition — does not change the interface. It surfaces the ordinary
`kind: "proposal"` its dispatch already has, and the planner decides with the user
whether to amend the contract or defer it as a follow-up. Amending it is a [live
edit](#live-graph-edits), and which edit depends on what the node is doing: a node
that has not started is parked with `cancel` and returned by a `requeue` whose
`amend` restates its `task`, `retry` replaces one already running with a task stating
the new contract, and a surface that has to change again after its node settled is an
`add` with the affected consumers `reparent`ed onto it. A `note` carrying no
`criterion` is **not** that lever: it is rendered as observed state that adds no
acceptance criteria, so it tells a worker about the new contract without changing what
its judge reviews against. A `note` that *does* carry a `criterion` binds the
conversation it is delivered into and not the node's stored bar, so it does not survive
a re-dispatch either.

The engine starts every node whose dependencies are `done`, bounded by
`concurrency`, and keeps doing so until the graph is terminal. Lifecycle
dependencies on the same repository identity also carry publication/stack
ancestry; cross-repository dependencies only schedule. [Live
edits](#live-graph-edits) change the desired graph and the reconciler applies the
new reachable frontier on its next pass — there is nothing to wait for, because
there is no barrier between a node settling and the next one starting.

## Status, state, and exit contract

Each node settles once, and the run's result records that settlement:

- `done`: the agent completed or the lifecycle published successfully.
- `done` with outcome `no-changes`: an explicit `expects_no_diff` node settled
  deterministically without an agent dispatch.
- `waiting`: a ready human node or lifecycle human step needs action. Its
  `human_actions` entry includes the exact `task`, direct `unblocks`, and whether
  it unblocks workstream publication.
- `blocked`: execution is transitively gated by a waiting human, or by a parked
  dependency. `blocked_by` contains the ready top-level or `NODE_ID/STEP_ID` human
  references.
- `parked`: a `cancel` idled the node. Its live turn was interrupted — and its
  dispatch killed if it outlived the grace period — its branch preserved, its
  publication anchor stays in the graph, and nothing dispatches it again until a
  `requeue`, which is refused until that dispatch has settled. The settlement's
  `detail` says which of the two ended it. See [Parking a node, and picking it up
  again](#parking-a-node-and-picking-it-up-again).
- `failed`: an executed agent or lifecycle failed.
- `failed` with outcome `task-failed-change-open`: the dispatch failed its own
  verdict having already opened a change request from the session it worked in —
  `onevcs publish` in its own final turn, which the engine's publication step never
  ran. The settlement carries the URL. Read the change before re-running the work:
  a node that failed is not a node that changed nothing.
- `failed` with outcome `infrastructure-failure`: a recognized provider or
  harness failure, ENOSPC, OOM kill, or failed scratch-capacity preflight
  prevented dispatch from running. This one is **terminal**: the reconciler
  surfaces the underlying error as a blocking planner proposal on first
  occurrence and does not dispatch the node again on its own, so a `retry` edit is
  what puts it back. Unknown or ambiguous errors remain ordinary retryable task
  failures.
- `failed` with a `no-agent-progress` diagnosis: the budget was spent without the
  agent producing anything. onejudge counts every turn it attempts, and a provider
  that accepts a turn and answers with nothing still spends one, so a failing
  provider drains a whole cap in minutes. Reported apart from an ordinary turn cap
  because the two want opposite responses: retrying this one unchanged spends the
  next budget the same way.
- `skipped`: a failed dependency made execution unsafe. Failure takes precedence
  over a simultaneous waiting path, so such a descendant is skipped, not blocked.

The result's top-level `state` is `failed` if any node failed or skipped,
otherwise `waiting` if any node waits, is blocked, or is parked, otherwise
`complete`. `ok` is true only for `complete`. Human and JSON output carry the same
facts. Exit status is 0 for `complete`, 1 for `waiting` or `failed`, and 2 for
invalid plan, ledger, configuration, or command input. Recorded result schema v6
adds structured provider failure attribution; schema v5 added the terminal
`infrastructure-failure` and successful `already-integrated` outcome values.

## The recorded run

Recording is on by default:

The conservative sweep exposed as `just sweep` reclaims what a finished dispatch or
a finished publication left behind. A directory its ownership proof does not clear is
reported as retained rather than removed, and a directory that is only stale is
eligible after the conservative age threshold alone. Use `just sweep --dry-run` to
inspect candidates without removing any of them.

It is **two published verbs, and the recipe is nothing more than running both**:
`onevcs sweep` for the publication and recovery workspaces a lifecycle leaves behind,
then `oneagentgraph sweep` for the scratch a dispatch leaves behind. `--dry-run` and
`--min-age-hours` reach both and mean one thing across every family. Each verb prints
its own report, which the recipe neither parses nor rewrites; the second runs whatever
the first exits, and the recipe exits non-zero when either did, so a verb that failed
is in the status as well as in its own report.

Scratch a dispatch itself produces cannot wait for quiescence, so what replaces
quiescence is proven non-reference: a candidate a live process names — in its argv,
environment, working directory, root or executable links, open descriptors, or
file-backed memory mappings — is retained and reported. A procfs that cannot answer
the question at all is not the same as one answering "nothing is referenced": those
families are then left alone and the run reports that it could not prove them
unused. A short minimum age covers only the gap between creating a directory and the
first instant a process names it, and `--min-age-hours` governs the scratch that has
no such proof behind it.

**The recipe passes four hours when you name none, rather than the twenty-four both
verbs default to.** The recipe's own comment says so beside the number, so that moving
it is an argument with the measurement rather than with taste. At twenty-four the two
verbs reclaimed 0 B on this host while `--min-age-hours 4` reclaimed 23.9 GB, because a
host running several dispatches churns publication workspaces and dispatch scratch
hourly and almost nothing provably dead is ever a day old. A floor that never fires is
how a device reaches 100% with 2 MB free while every sweep reports success. Lowering it
weakens no proof — neither verb removes anything on age alone, and `onevcs` still keeps
its bounded recovery history. It is *not* GNU `find`'s `-mtime` truncation, which is
the obvious suspicion and is ruled out by measurement: a directory 30 hours old is
reclaimed by each verb at `--min-age-hours 24`. The floor was long, not truncated.

**Read each verb's "Families not examined" section before reading its reclaimed
figure.** Each verb names the families it examined and, separately, every family it
knows of and deliberately leaves alone, each with its owner — the verb or the operator
action that reaches it — so a sweep that reclaimed nothing means "nothing in the
examined families was reclaimable", never "a family was never looked at". `onevcs`
names three it owns and does not sweep, per identity: the per-run lifecycle clone root,
kept as a bounded recovery history and reached through `onevcs recoverable`; the pool
of warm worktree slots, which survive their session's close *on purpose* and which
`onevcs pool status <repo>` reads and `onevcs pool prune <repo>` empties (the placement
and the return are [the lifecycle
document's](repo-lifecycle.md#where-a-session-is-placed-and-what-a-close-returns)); and
the preserved unpublished branches that hold run roots from reclamation, which
`onevcs recoverable` names with the verb that lands each one. None is handed to a
sweeper because none is provably dead: a warm slot is waiting, and whether a preserved
branch should land is a judgement about work rather than about liveness. That
provable-or-nothing property is what makes both verbs safe to run unattended, so the
answer is an owner rather than a wider sweeper. `tests/e2e/test_sweep_e2e.py` holds
both installed verbs' `--format json` to that swept-or-owned invariant over a state
root holding a pool slot and a preserved branch.

**And no figure either verb prints is what says this host has room.** A sweep takes
only what it can *prove* dead, in the families it names and in no others; the scratch
other tools write under the host's `$TMPDIR` is neither verb's. The engine's `free
space:` line on `just status` and `just host` is what says whether the disk has room.
Neither does anything reclaim the **processes** a finished dispatch left running, which
reparenting to init puts outside every tree walk; the engines that start them own
keeping them alive and reaping them.

The ledger is **flat**, because a run has no phases to number:

```text
runs/<run-id>/plan.json          the plan as launched
runs/<run-id>/events.jsonl       the authoritative journal
runs/<run-id>/result.json        the settlement, rewritten as it moves
runs/<run-id>/launch.json        who launched it, with what, and how often adopted
runs/<run-id>/reports/           one report per dispatched agent-graph turn
runs/<run-id>/channel/           queue.json, surfaces.jsonl, replies.jsonl
runs/<run-id>/oneharness-sessions.jsonl   one line per harness run this run opened
```

**That last file is a pointer, and the transcripts it points at do not live here.**
Every dispatch launched from this host goes on recording its oneharness sessions in
**this host's own default store** — `$XDG_STATE_HOME/oneharness/history/`, listed with
`oneharness history list`, exactly as before the engine started writing this file. The
engine sets no `ONEHARNESS_HISTORY_DIR`, so an inherited store or a repository's own
`history_dir` is honoured untouched and nothing about a dispatch's sessions moves under
the run root. What the pointer file adds is the one thing the store could not answer:
*which* of its sessions belong to this run. Each line names a session and carries the
three fields that open it in the store the line names, `just agents <run-id> [<node>]`
is the engine reading it back grouped by session, and `oneharness history watch --label
onepipeline.run_id=<run-id>` follows the same sessions live. `just monitor` does not
fold any of it — see [Monitoring a live run](#monitoring-a-live-run).

**A run's pointer file is whole only if both pins moved.**
`config/oneharness.version` is what makes this host's two-party turns write a line —
the agent and judge sides of every worker spawn that CLI from `PATH` — and
`config/onepipeline.version` is what makes the observer's and the drafter's in-process
turns write one. Move one and not the other and the file holds half the agents, which
reads as a run that launched half of them.

The id is derived from the plan's `name` or filename and made unique. Plan and
result writes are atomic; a live run cannot be driven by two processes, and a run
whose driver died is picked up with `just orchestrate --adopt <run-id>` rather
than by starting a second one over it.
`just runs` summarizes each run's progress, including waiting action prose and what
each action unblocks, then points to `just results <run>`. The results view lists
every node's status and outcome, links its typed-id detail view, and prints the
concrete full-artifact paths for failures. A run containing failed nodes is still a
successful results lookup; only an invalid invocation exits non-zero.

There used to be a `round-NN/` directory per phase and a `Round:` line in `just
status` naming which one owned a worker's branch. Both are gone with the phases:
a checkout belongs to the node that cut it and to the run that dispatched it, and
those are the two things every view names now. A branch whose node has settled is
still retained (for a resume, or a recovery), and `just recoverable` is the view
that finds one.

`just status <run-id>` reports a dispatch that has finished no turn yet. oneharness
writes one history record per *completed* turn and dispatches here routinely spend
twenty minutes on their first, so a view built from history alone answered "No
dispatched tasks recorded" for a step that had been working for half an hour —
which reads as *nothing is running*, the opposite of the truth. The run's own
journal knows a node or step started, so the two are joined: the empty state now
names each in-flight dispatch, how long it has been running, and that no turn has
completed; a run whose journal really shows no dispatch still says so. It is
computed only for a named run, because the unscoped view would have to read every
recorded run's whole journal to answer the same question.

That join is the guarantee, and a [streamed agent
turn](onejudge-integration.md#streaming-the-agent-side) adds what only the turn
itself can answer: each in-flight line also carries what the node is doing right
now — `now Bash just gate (7 event(s), 4s ago)` — read from the events the harness
is publishing as it works. A dispatch that is not streaming, or has published
nothing yet, reads exactly as it did before.

**Where a node's gate evidence actually is.** `result.json` is terse and carries no
artifact paths: at `schema_version` 3 each node reads `id`, `status`, `outcome`,
`landing`, `branch`, `change_url`, and the human-action fields. The evidence lives
in `events.jsonl`, the merged three-stream journal, where a lifecycle node's
`onevcs` session is **followed** while it runs and every envelope is stamped with
the node it belongs to. Since onevcs 0.11.0 there is no `gate-started` /
`gate-verdict` pair to look for — that release removed the tier that emitted them —
and what the merge path wrote arrives on the session's **`push`** event: the
output, a `preserved_log` path that outlives the run's worktree, and the whole run
again as an `artifact` reachable with `onevcs artifact`. It is emitted for a green
publication as much as a refused one. The per-dispatch raw reports are files under
`runs/<run-id>/reports/`.

An identity verified by the host's required checks preserves no local log, because
nothing local produced one: its verdict arrives afterwards, as
`EventKind::ChangeCheck` events carrying each check's own log.

A publication that never reached the merge path at all — a base advanced under it, a
fetch or a worktree it could not build — produces no such evidence either. There is no
`publication-failed` *event*: `publication-failed` is a node's settled outcome, and
its whole account is the settlement's `detail`, which is `onevcs`'s own reason
prefixed `onevcs: `. It is no longer the *only* such outcome, and that is the part
worth knowing: `vcs::failure_of` sorts `onevcs`'s eight failure kinds into the five
a further attempt could answer — which settle `checks-failed`, `checks-unsettled`,
`push-rejected`, `sync-conflict`, or `pushed-unverified`, each after the node was
dispatched again on the same branch — and the three that nothing further could, which
keep the residual word. `pushed-unverified` is the one whose work is already **on the
origin**: onevcs 0.12.0 added it for a publishing push that reached the remote behind
a merge path that could not then be read, so a further attempt re-reads that path
rather than re-pushing, and its detail names both the commit it landed at and what
stopped the read. So "the gate rejected this" and "the base moved" are now two outcomes rather
than one; the detail still says what each of them said.

A failed `just gate` names the tier that failed and the loop to close it. An
llmlint failure also prints the comparison base the gate resolved: clear those
findings against `just lint-llm-diff <base>` alone, then rerun the complete `just
gate` once to confirm. A full gate cycle per lint fix re-pays the deterministic
tier for a finding that tier cannot re-check.

Execution is one long-lived reconcile loop for the whole run: it compares the live
desired graph with actual node state projected from `events.jsonl`, starts the
reachable frontier, and reacts to each completion until the graph is terminal. It
never stops to be advanced, so there is no state in which the graph is settled but
the run is not moving except the ones [an attach returns
on](#when-an-attach-returns).

`events.jsonl` is the authoritative record and the graph is derived from it, which
is what makes an adopted driver possible at all: it replays its dead predecessor's
prefix, retains settled nodes byte-for-byte, resumes nodes that were running
without another start transition, and converges the remaining frontier. The journal
record contract belongs to the published `onepipeline`, and its own tests pin it;
what matters here is that every supported version stays readable and a reader skips
records from a version it does not know rather than failing the run it is
observing.

**A record's readability and its claim on a sequence number are different
questions.** A writer resuming a journal takes its next sequence above every line
that *claims* one for this run — including a line written by a newer schema, which
it cannot read. Skipping such a line when picking the number is how one run came to
hold two events at `seq` 104: the planner's `channel-next` ran from a newer
checkout than the driver it was supervising. Strict replay stays strict in the
other direction — a line it cannot read might have been an authoritative graph
mutation — but a *collision*, two records sharing a number with both present and in
order, loses nothing and is read through. That last part is not cosmetic:
the reconciler validates every live edit against this reader, so treating a
collision as fatal ends a healthy run's supervisability, which is what it did.

### A run outlives the turn that launched it

A run must not die because the turn that launched it ended. `just orchestrate`
starts the driver as a *child program* leading a session of its own, so the
launching turn's group teardown misses it and `uv run` — which forwards the signal
it receives to its own direct child and then escalates to SIGKILL — reaches only
`orchestrate` itself.

That is what makes the foreground default purely additive: what owns the run does
not depend on whether anything is waiting, so `--detach` decides only whether this
command waits, and Ctrl-C ends the attachment rather than the run.

Escaping the launching turn's *signals* used to be only half of it: a second
protection re-attributed a launched run away from its launcher's dispatch ownership
stamp, because the sweep read that stamp as proof of
a leaked tree once the launching step settled. That half is no longer needed here —
`oneagentgraph sweep` terminates nothing, so there is no reaper left for a driver to
be mistaken by. See [Keeping a process that outlives its
launcher](repo-lifecycle.md#keeping-a-process-that-outlives-its-launcher) for what
that leaves, and for the leaked worker nothing collects any more.

**A driver that stops is derived, not trusted.** `launch.json` records
`{"pid": ..., "host": ...}` once and is never rewritten, so a process that crashed
or was killed would otherwise leave a run reading exactly like ordinary finished
work. When this host can prove that pid gone and the graph is not terminal, `just
runs` and `just status` report the run as `DRIVER DEAD` with the command that
attaches a fresh driver:

```
* probe-live               [mine]                   0/2 done  DRIVER DEAD
    DRIVER DEAD — its ledger is intact; attach a fresh driver with: onepipeline adopt probe-live
```

Every unknown withholds that verdict instead — an owner that cannot be probed, or
an unreadable record — because sending a planner to tear down live work is the
worse error. Withholding it is not the same as saying nothing: a record that still
claims an owner this host could not refute keeps its run listed as `ACTIVE`, and an
owner on **another host** is exactly that case, since a pid means nothing across
machines. A run another driver is working therefore reads as the live work it is.
Only a record this host cannot read at all drops out of both views, having
supported no claim either way.

A live pid is ownership, not progress. A driver that keeps its pid while doing
nothing — no child process, no planner surface, and no ledger write — is *parked*,
and the read-only views report it as `PARKED (...)` rather than as running. All
three signals must be absent past the threshold, which is derived from the
planner-update interval (1800 seconds by default), and
`ONEPIPELINE_PARKED_AFTER_SECONDS` moves it. Every unreadable input resolves
toward "still working", so a busy driver is never misreported as parked: one live
descendant of the launch, one fresh surface, or one journal, plan, or result write
is enough to keep it reported as running. A descendant that has already exited and
been left uncollected is not one of them — that dangling entry is the wedged
launch's own signature, so counting it would make the state unreportable on the
very run it describes. A persisted `last_surface_at` that is not a finite number is
discarded rather than timed, since a non-finite stamp would otherwise make the run
look eternally fresh or eternally silent.

### Who launched a run, and who may stop it

Several planners share this host, so every view says whose run it is looking at.
`just orchestrate` records the launching session automatically: it mints a
`launch_id` into the run directory and writes the launcher and its session id to a
short-lived record under `$XDG_STATE_HOME/ai-orchestrator/launches/`, outside every
repository, because the session id may be sensitive. The launcher is detected from
the environment the harness exports — never from process ancestry — and
`--launcher` / `--launcher-session` (or `$ORCHESTRATOR_LAUNCHER` /
`$ORCHESTRATOR_LAUNCHER_SESSION`) still override it. A launch nothing identifies,
and every run recorded before this was populated, resolves to `unknown`: missing,
malformed, and expired records are all read the same way, and none of them is ever
attributed to the reader. The launch record is the single source for the
scheme.

```sh
just runs       # * demo   [mine]                     2/3 done  ACTIVE
                #   other  [claude-code:3f9a1c2e]     1/4 done  ACTIVE
                #   older  [unknown]                  1/1 done  SETTLED
just runs --mine             # only the runs this session launched
```

`[mine]` is this session; a named session is another planner's, labelled by a
stable digest rather than by the session id itself; `[unknown]` is a run nobody can
attribute. A provenance-less run never displays as the caller's.

The same attribution reaches the branches a run leaves: every `onevcs` session a node
opens is labelled with its `run`, its `node` and the `launcher` — the launching session
id, omitted for a launch nothing attributed — so `onevcs recoverable` and `onevcs session
holders` name whose each preserved branch is, and filter on it with `--label`.
`tests/e2e/test_repositories_field_dispatch_e2e.py` reads the three off a real dispatch.

### Stopping a run

```sh
just stop <run-id>                       # a run this session launched
just stop <run-id> --force               # after reporting whose run it is
```

`just stop` refuses a run launched by another session, and refuses an `unknown` one
by the same rule, naming the owner either way; `--force` prints who owns it before
it proceeds. It resolves the processes to stop from the run's own records and walks
the live tree below them — never from `ps` output, and never as one process group,
because a dispatched worker leads a group of its own and a `killpg` on the recorded
pid would leave it running. Each takes SIGTERM first so it can record its own
teardown; survivors are escalated, and a process that outlives even that is
reported by pid with a non-zero status rather than hidden under a success.

Stopping records nothing about the run itself: the ledger stays intact and the work
stays reclaimable — `just orchestrate --adopt <run-id>` attaches a fresh driver to
it. `complete` on the channel is a completion verdict and does not stop scheduling;
`just stop` is what ends a run.

**A stop settles the run only until a driver adopts it.** The `run-stopped` record stays
on the journal, and on the adopted engine the fold clears what it decides at
`driver-adopted`: the stop was evidence about the driver it ended, and the record the
adoption rewrote names the one driving the run now. So `just status`, `just watch` and
`just unwatched` judge a stopped-then-adopted run by that driver — `ACTIVE` while it
drives, a watch that heartbeats and elapses rather than ending on `nothing-driving`, an
unwatched run the hook is told about — and a run stopped and never adopted still reads
as stopped. Before it every view went on reading the stop: `just status` said
`DRIVER DEAD` over a driver that was dispatching, and the watch a manager armed on the
adopted run returned at once.
`tests/e2e/test_adopted_engine_reads_an_adopted_run_e2e.py` drives a stop and an adoption
through the real recipes and reads all three views.

### Shutting a host down

```sh
just shutdown <run-id>                   # one run this session owns
just shutdown --mine                     # every run this session owns
just shutdown --host                     # every run under this runs root, whoever owns it
just shutdown --host --grace 120         # a shorter grace than the default ten minutes
just shutdown --host --force             # no interrupt, no wait
```

A soft shutdown is for a host that is going away with work still running on it, and it
differs from a stop in what it asks, what it keeps and what it records.

* **Scope and ownership.** A run id or `--mine` is held to the ownership rule `just stop`
  holds. `--host` is not: shutting a host down is a decision about the host, so it takes
  every run under the runs root and names each run's owner in the report instead of
  refusing.
* **The ask and the grace.** Every live dispatch is asked to wrap up and commit, and has
  600 seconds — ten minutes — to end on its own terms; `--grace <seconds>` names
  another. A dispatch still running when the grace elapses is stopped the way `just stop`
  stops one. `--force`, and `--grace 0`, skip the ask and the wait and go straight to
  that teardown.
* **A preservation, not a publication.** Every branch the shut-down runs' own records
  name is then pushed to its origin under its own name, through `onevcs preserve`. No
  change request is opened, no merge path runs, no base is touched, nothing is
  force-pushed, and the branch stays exactly as recoverable as it was: `just
  recoverable` still names the verb that lands it, now with the branch also on its
  origin. Why that push may skip the pre-push hook, and what bounds it, is stated once,
  in `AGENTS.md`'s *Commits and merging*.
* **Still adoptable.** No node is parked or settled and the ledger is intact, exactly as
  after a stop: `just orchestrate --adopt <run-id>` reattaches a driver, which
  re-dispatches an in-flight node pinned to the branch it was working on.
* **Not a stop, and not an ending.** The run journals `host-shutdown`, and one
  `dispatch-stopped` per dispatch it acted on, rather than `run-stopped`, and no run-end
  hook fires — so no follow-up run is launched on a host that is going away, and a later
  reader can tell a host shutdown from a stop, a crash and a kill, and a dispatch that
  ended on its own terms from one killed at the deadline. `just status` reads such a run
  as `HOST SHUTDOWN` and names the adopt that resumes it.
* **The report is the product.** Read it rather than the exit status alone. Non-zero
  means a dispatch was killed at the deadline, a process could not be killed, or a push
  was refused; a branch already on its origin and a branch whose identity has no origin
  are reported at exit zero. Its last section names every other unpublished branch on
  this host that the shutdown did not push.

`just shutdown` stays outside `.claude/settings.json`'s allowlist for the reason `just
stop` does, and more so: `--host` acts on runs this session does not own.

### Retrying a provider refusal

A run is most fragile where it is least busy: a turn has just finished, its work is
recorded, and the only thing left is asking a provider for the next one — with the
quota that turn just spent. One refusal there used to kill the process that owned
the whole run.

Those requests are retried with bounded backoff before the process is allowed to
die, and the test is not a classification of the refusal — it is **whether the
attempt produced any events at all**. `engine::attempt` re-asks a failed dispatch
that reached silence; one that recorded anything has already answered, whatever its
exit status, and asking again would spend another budget on work already done. A
provider that refuses before the first turn is the case this exists for: the failure
carries no work to lose.

Three attempts by default, five seconds apart and doubling to a two-minute ceiling;
`ONEPIPELINE_BOUNDARY_ATTEMPTS` and `ONEPIPELINE_BOUNDARY_BACKOFF_SECONDS` change
both, and an unusable value falls back to the default rather than disabling the
recovery it configures. Every retry reaches `events.jsonl`, so a retry that saved a
run is visible in the run's own record rather than only to whoever tails a log.

### Adopting a run whose driver died

A run whose driver process is gone is not over — its journal, its ledger, its
channel, and every preserved branch and stack anchor recorded against it are all
intact. What it has lost is the thing driving it, and only that.

```sh
just orchestrate --adopt <run-id>            # attach a fresh driver to that run
just orchestrate --adopt <run-id> --detach   # the same, unattended
```

Adoption keeps everything the run owns and replaces only the driver. It re-reads
the launch parameters from the run's own `launch.json` — the plan, the working
directory, the node-scope graph, the planner-update interval — registers the new
process as the owner, reopens the channel, and resumes reconciling the graph
projected from `events.jsonl`, including every live edit accepted while nothing was
driving. The run id, the journal, and the anchors are the ones it already had;
minting a new run id and pinning a resume was what stranded publication anchors
before this existed. `launch.json` counts the adoptions, so how many times a run
has lost its driver is part of its record.

Two things refuse it, and neither has a `--force`: adopting takes over ongoing work
rather than ending it, which is exactly the case where a second opinion is worth
more than an override.

* **A run this session did not launch.** Ownership is the same rule `just stop`
  keeps, including `unknown` never being yours.
* **A run something is still driving.** End it with `just stop <run-id>` first if
  that is what you mean.

Each adoption takes a conversation of its own and never resumes the dead driver's:
a relaunch that asked the harness for a session it no longer had is what burned
whole lineages on `No conversation found`. The dead driver's log moves aside rather
than being truncated — it is the evidence of how it died, and the first thing to
read after adopting.

**A dispatch outlives its driver, so a dead-driver verdict beside live dispatches is a
recoverable state rather than lost work.** The driver is one process and every dispatch
it made is another: they are forked to outlive the launch and are the engines' own to
reap, which is why nothing here ever works around a kill with `nohup` by hand. So the
ordinary shape of a driver that died mid-run is `just status` reporting `DRIVER DEAD`
while `just host` still lists that run's nodes as live dispatches, recorded doing
something seconds ago. Read the two together rather than reading the verdict alone:
adopting the run attaches a fresh driver beside those dispatches and they keep the turns
they are in, where treating the verdict as lost work throws away turns that are still
running. The dispatches are also what a supervisor confirms it against — a node whose
turn age keeps moving under a dead driver is the state this paragraph describes, and one
that stopped is a different question.

**What killed the driver is a separate reading from the driver being dead, and the
cheapest of them is the disk.** A full disk kills a driver and the run then reports a
dead driver, which is the same verdict a crash gives and reads as a crash to diagnose:
that cost twenty minutes on a host whose filesystem had 1.3 GiB of 197 left with three
dispatches building on it. The engine's `free space:` line `just status` prints above its
provider block, and the same line on `just host`, is what parts the two before anything is
diagnosed. It
is a host-wide reading on a host several managers share, so read it and leave clearing
space to whoever owns what is filling it.

`tests/e2e/test_driver_death_is_recoverable_e2e.py` is what compares this statement
against the installed engine rather than leaving it standing on its own: it drives both
views over a run whose launch process is gone and whose dispatched process is alive, and
fails when the engine stops answering the way this reads.

## Monitoring a live run

`just runs` says how far each run has got and `just history-show` says everything
about one thing in it. `just monitor` answers the question in between — "what is
happening right now, across the whole run?" It is the standard first view for
every recorded in-flight dispatch, from a wide DAG down to a one-node plan — the
one executor means there is no dispatch it cannot see. It folds three stores that
settle at different times into one ordered stream:

| Source | Read from | Reported when |
| --- | --- | --- |
| Run journal | `runs/<run-id>/events.jsonl` | every node transition, as it is appended |
| Git | commits on each known lifecycle branch | once per commit, ever |
| GitHub | each lifecycle-linked PR | its state or any check changes |

**A fourth row used to name `oneharness history` — "sessions whose `run_id` label
names this run" — and the installed engine has never folded it.** The claim was
wrong when it was written and read as an argument for not looking anywhere else,
which is the expensive half: a manager who believed `just monitor` covered the
harness store had no reason to ask the store itself. A run's sessions are found
three ways instead, none of them through this stream:

* **`just agents <run-id> [<node>]`**, which is `onepipeline agents` — the engine
  reading the run's own pointer file and grouping what it finds by session. `--project
  <source:project>` is the union across every run launched from one project. This is
  the read to reach for first, because it answers *for this run* without a filter.
* **`oneharness history pointers <run root>/oneharness-sessions.jsonl`**, the same
  file read with the producing library's own verb. Reach for it when the question is
  about the file rather than about the run — a torn line, a session the engine's
  grouping skipped.
* **`oneharness history watch --label onepipeline.run_id=<run-id>`** on this host's
  default store, which is live rather than a snapshot. It works for every dispatch
  now because the engine stamps that label on all of them; below the adopted engine
  it matched nothing, whatever the run.

PR check events identify the check, state, and whether it is required, and emit
each transition. Do not replace this aggregate view with an ad hoc `gh pr checks`
poller or bespoke lifecycle watch script. Start with `just monitor`; a targeted
`gh` query remains appropriate for a one-off detail absent from its stream.

```sh
just monitor RUN_ID               # stream one named run's merged events
```

The run id is required — there is no "newest active run" default — and
`ONEPIPELINE_RUNS_DIR` moves the ledger as everywhere else.

**Following is a terminal affordance**: the stream is flushed line by line and
Ctrl-C is how a person ends it. That is why the *supervision* entry point is `just
orchestrate`, which streams exactly this and then **returns** when the run settles
— see [When an attach returns](#when-an-attach-returns). The planner is an
automated supervisor whose invocations are always captured, so a command that
produced nothing and never returned was one it could not use, and every status
check got done by reading `events.jsonl` and `/proc` by hand instead.

### What is running right now, and on what

`just runs` and `just status` answer from the run ledger, and the ledger stops at the
node: it records that a dispatch started and that it settled, and between those two a
turn on this host runs for 600-2000 seconds. A planner needing more than that had one
tool — matching `ps` output by pattern — which is how six live dispatches were counted
where there were two and a judge turn wedged for 1h54m was missed entirely.

The dispatch ownership registry is the answer that does not guess, and its candidate set
is the ownership registry the scratch sweep already trusts: the dispatch's ownership
stamp the kernel fixes into the environment of
everything a dispatch starts, paired with the owner lock a live dispatcher holds for
that scratch directory's whole scope. A process counts as this harness's only when
both agree. Command lines are read only *after* that, to tell one turn from another
inside a dispatch that owns them all — an agent turn, its judge, and an llmlint tier
are three invocations of one harness under one stamp, distinguished by the config each
names. The harness identity serving a turn is read the same way: oneharness selects it
by falling through a chain, and the credential directory it hands the provider
(`CLAUDE_CONFIG_DIR`, `CODEX_HOME`) is where that selection becomes observable.

What that buys each view:

* **`just status <run-id>`** adds the live role, harness, and turn age to each
  in-flight node, flags a turn past a generous multiple of its role's typical duration
  as `ANOMALOUS` (a judge's threshold is fifteen minutes; a worker's is over an hour),
  and flags a node the ledger records as running that no live dispatch is driving as
  `UNDRIVEN`. That label is deliberately not `parked`: this vocabulary already has a
  `parked` node state and it means the opposite — a node the planner idled with
  `cancel`, whose work is preserved and which `requeue` resumes. Its header carries the host's load averages with the runs and nodes
  producing them, so a slow host names its cause instead of being reconstructed later.
* **`just runs`** carries the live/parked distinction per row: how many dispatches
  carry that run's stamp, or that none do.
* **`just host`** is the whole-host view, across every planner sharing it: per live
  dispatch, its owning session, run/node, role, turn age, and load contribution.

**Free space** is on both views too, and is the engine's: one `free space:` line per
distinct filesystem among the runs root and the `workspaces/` under `onevcs`'s state root
where every lifecycle worktree and per-run clone is cut, one line naming both when they
share a device. On `just status` it sits **above** the provider block, which is where a
supervisor's watch is told to cut this view. It is host-wide on a shared host: read it,
and leave acting on it to whoever owns what is filling the disk. What it answers is in
[Adopting a run whose driver died](#adopting-a-run-whose-driver-died). Both recipes
pass the engine's view through untouched, and
`tests/host_views/test_status_and_host_views_e2e.py` drives them over a built run root and one a
real launch wrote, holding the line above the cut and the output to the engine's own.

Both flags are *positive* claims and are made only where they can be proven. The
node-level `UNDRIVEN` needs two things beyond "the registry saw nothing for this node":
the registry must have seen at least one live dispatch, which shows this reader is
looking at the scratch root the dispatchers write into, and the run's launch must be
observably working, which distinguishes one node losing its dispatch from the whole
run stopping — the second is already reported one level up by
the liveness verdict. Every other uncertainty resolves toward "still working",
and a view that can observe nothing reports exactly what it reported before any of
this existed.

### Asking what nothing is watching

`just unwatched` reports which of this session's runs has nothing watching it. It is
the read behind the `Stop` hook `.claude/settings.json` registers, offered as a command
so an operator can ask the same question by hand; AGENTS.md's watch rule is what the
hook enforces, and this is what it asks.

```sh
just unwatched                    # this session's runs, from the ownership environment
just unwatched --session ID       # somebody else's session, named
```

**Two streams and a status, and the status is the whole of what a caller branches on.**
One line per reported run on standard output — the run id, its standing word, why
nothing is watching it, and the watch that would — and nothing at all when there is
nothing to report. Everything the verb could not resolve goes on standard error, where
it changes no status: a run whose evidence could not be read is neither an unwatched
run nor a reason to refuse the question that was asked about the others. `6` says at
least one owned run is unwatched; `0` says none is; a refusal is neither and says so.
The recipe carries that status back unchanged.

**What is reported is what was proven, and every unknown resolves toward unwatched.**
A run is kept out only by a *current* summary document saying it stopped or its graph
converged — a document behind the journal beside it is what a run still recording looks
like, so it proves nothing. One declaring a schema this build has moved past is the
previous release's document, not an unreadable one: the verb folds it once through the
listing's own reader, rewrites it at this build's schema, and decides it as any other —
a settled run excluded, a run still recording reported — because reporting such a
document unread refused the manager's turn after every run the previous binary had
settled, at every schema bump (the engine's 0.28.1 release;
`tests/unwatched/test_unwatched_and_stop_hook_e2e.py` holds both halves). A watcher
record naming another host, another run, a pid this host has proved is gone, a pid that
is now some other process, or one nothing here can decide is in each case not a live
watch. The record is written by the `watch` verb itself and removed on a clean exit, so
nothing stands between a watcher dying and its run reading unwatched — no heartbeat, no
expiry, no cleanup step, because the deaths that matter have no clean exit.

**The hook is the engine's stop guard, wired as the engine's own page gives.**
`onepipeline stop-guard` is one harness-neutral verb, and [its own
page](https://github.com/nickderobertis/onepipeline/blob/main/docs/stop-guard.md) states its contract and then its wiring for Claude Code and for
Codex; the `Stop` entry `.claude/settings.json` registers is that page's Claude Code
wiring — `onepipeline stop-guard --format claude-code`, named by path out of this
checkout's `.venv/bin` through `$CLAUDE_PROJECT_DIR`, with its timeout kept — and nothing
of this repository's sits between the harness and the verb. A manager launched from Codex
reads the same page for its own wiring. What the guard answers is the page's: the session
comes off the Stop payload and never out of the environment, so a dispatched worker — who
inherits both this tracked settings file and its manager's launcher session, while its
own session owns no run — is silent; a session owning a run proven unwatched is blocked
with `unwatched`'s own lines as the reason, once per condition, the continuation over an
unchanged report ending the turn and a changed one blocking again; anything that is not
evidence is a warning shown to the person, never a block; and every ending exits 0. The
block-once memory is the engine's, under its own state root, and
`tests/unwatched/test_unwatched_and_stop_hook_e2e.py` drives the registered command over
runs roots holding an unwatched run, a watched one, a continuation and a worker-shaped
environment.

### The preserved branches this host is holding onto

`just unpublished` is the inventory beside the listing below: what this host is still
holding, what each branch costs in disk, and the `just` command that lands it. It is a
view over `onevcs` verbs — `recoverable --json` for the rows, `repos` for the registered
identities and `session holders --json` for the sessions each row joins to — and never a
re-derivation from `git` or a walk of
`events.jsonl`; the one `git` it runs is a `rev-parse` that keys an acknowledgement and
decides nothing about what is unpublished. `orchestrator/unpublished.py` is the one
statement of the contract: the row shape, the counting rule, the exit statuses, the
acknowledgement file, and the session-label keys.

```sh
just unpublished --host                       # every registered identity
just unpublished --session s-0123456789ab …   # one session token, repeatable
just unpublished --host --json                # the rows, for something other than a person
just unpublished --host --no-disk             # skip the walk
just unpublished --acknowledge BRANCH --reason "why it is deliberately left"
```

**Two targets answer today, and the third is declared and refused.** `--host` answers
for every registered identity from any directory, asking each one by name, which is the
visibility gap: `just recoverable` run inside a checkout answers for that identity alone. `--session <s-token>` answers for the
branches those sessions hold or held, the branch read off the holder record rather than
derived from the token, because a retried session holds a branch named for an earlier
one. The **own-sessions** target — the default, `--own`, and a `--session` naming a
manager session id rather than an `s-` token — is a filter on the session labels (`run`,
`node`, `launcher`) the engine stamps and the adopted `onevcs` stores, which a later node
turns on as one filtered read; until then it refuses, naming what it awaits, and this
repository joins nothing to imitate it. **A session opened before that adoption carries
no labels**, so the own-sessions target never reaches a branch it preserved and nothing
backfills them; unlike a branch no session record names, such a branch is still reached by
an explicit `--session <s-token>`, through its holder record, as any other is.

**What a row carries** is what `onevcs` states — the identity, the branch and its base,
the provenance, the `landed` object, the change URL and why the workstream stopped —
joined to the `onevcs` session token that preserved it (`null` for a branch no session
record names), with `run`, `node` and `manager_session` `null` until those labels land.
Beside that it carries the resume command **in its `just` form**, because the raw
`onevcs publish-branch` line lands with an empty description; whether the row is in
flight; whether it is counted; its acknowledgement; and the disk — the run root, clone
and worktree together, and each build-output directory under the worktree
(`target`, `node_modules`, `.venv`, `.nx`, `dist`) called out on its own. The walk
counts allocated file bytes and follows no symbolic link, so a sparse file does not
claim its apparent length as disk cost. The default rendering is that table plus a
trailer stating the counted total and the bytes held by build output, with both ways
out named beside each counted row.

**A row `onevcs` reports a `held_by` for is shown, marked in flight, and never counted**
— a publication landing that branch this moment is exactly what a consumer of this view
must not refuse a turn over. It is the presence of `held_by` that decides it rather than
which `holding` value it carries: every variant of that enum is a session that has not
finished with the branch, so one `onevcs` adds is in flight without anything here moving. Every `landed.state` the listing carries counts: `no`, `unknown`
(the *may have landed* rows) and `in-part`.

**Acknowledging is never landing.** `--acknowledge <branch> --reason "<text>"` records
that the calling manager session has seen and deliberately left that branch; the row
still shows, marked with its reason, with `counted` false. It is keyed on that session
and on **what the branch stands at**, so a branch that moves past the recorded tip counts
again, and it is invisible to every other session. A missing or blank reason is refused,
because an acknowledgement carrying only a branch is indistinguishable downstream from
one nobody meant, and so is a branch no row of the host-level reading names. The record
lives under `$XDG_STATE_HOME/ai-orchestrator/stop-unfinished/acknowledged/`, one file per
session; nothing in the tree or the runs root is written.

**The exit status is the whole of what a consumer branches on**: `7` when at least one
row is counted — preserved, not in flight, not acknowledged at its current tip — `0`
when none is, `2` on a refused invocation, and any other non-zero when the view could not
answer, which it says on standard error. Everything it could not resolve goes there too,
where it changes no status. An acknowledgement answers for the host-level target it was
looked up in, read after the write: `7` while any other branch there still counts, since
one branch left deliberately says nothing about the rest. A wrapper that cannot load its
helper or interpreter also returns the could-not-answer status, with the failed prerequisite on standard error.
`scripts/unpublished.sh --print-surface` prints that
vocabulary for a consumer to read rather than restate. The view reclaims no disk, lands
no branch and closes no session: `just sweep`, the three landing verbs and `onevcs
session close` stay where they are.

### Preserved work that has not been published

`just recoverable` lists every branch across the registered repository identities that
holds commits no `origin` ref has — from a registered checkout or from a retained
lifecycle run clone, which is the one place a killed dispatch's branch can be. Each
row names where the branch lives, its tip and age, why the workstream stopped (from
the recorded result that named it), whether it carries an incomplete-step provenance
marker, and the exact command that lands it: `just repo-recover` for incomplete
provenance, `just integrate` for a complete branch. When the publication checkout does
not have the branch, the suggested command starts with the ref-only fetch that brings
it there — aiming `integrate`, which reads local branches only, at a branch the
canonical checkout never had is the invocation this exists to stop. A branch that
merged, or that its base has since reached, drops out on that evidence rather than by
a name-shaped guess — **but which evidence there is to drop out on depends on the
identity's publication workflow.** A `local-direct` landing is stamped onto the base by
`onevcs`'s own squash commit, as `Orchestrator-Landed-Commit: <the branch's tip>` under
the rules file's `trailer_prefix`, so the listing drops it from git history alone. A
remote landing's base commit is written by the host and carries no trailer, so its only
records are the session record and the change request that record names, both in
`$ONEVCS_HOME`; a remote branch whose record is gone comes back into this listing under
`— may have landed`, with a `publish-branch` command beside it, however long ago it
merged. Read that row as *no record*, never as *not published*, and confirm it against
the change request before running the command it prints. Measured 2026-08-27 on the
pinned `onevcs`, over one landing of each workflow asked in a state root holding no
session record for either: the `local-direct` one still answered `landed: yes` from its
trailer and stayed out of the listing, the remote one answered `landed: unknown` and
came back into it. The view opens repositories to read and writes
nothing, so it is safe beside live dispatches.

### When an attach returns

The foreground `just orchestrate` waits on this ending. **Settled** is a property
of the run: it is no longer advancing on its own, so the next move is the planner's.
That is the only reading that works now the engine runs continuously — there is no
intermediate finish line to return on, and *the driver exited* would never return on
the ordinary case, because that process stays alive holding a question nobody is
answering.

Three conditions say it, and nothing else does:

| Settlement | What it means | Exit |
| --- | --- | --- |
| `complete` | the graph completed successfully | 0 |
| `awaiting-planner` | the run cannot move without the planner: a **blocking** surface is pending, or a human action is waiting to be attested | 0 |
| `unattended` | nothing is driving the run — parked, or a driver that is gone with the graph unfinished | 3 |

A *non-blocking* surface is deliberately not `awaiting-planner`: the run continues
past a heartbeat update or a monitor observation without waiting for a reply, so
returning there would walk away from working work. `unattended` exits non-zero
because it is the state a planner must intervene in, and because a launch that
parked reads exactly like one that is merely quiet to anyone who is not watching
the stream.

This ending is why `just orchestrate` can be run on a terminal and off one alike:
it ends itself rather than needing a reader to end it. `--detach` asks for the
other shape — return at the launch record and leave the run unattended — and
`just monitor RUN_ID` re-attaches to the same stream afterwards.

**Output shape.** The text stream's first line is exactly:

```text
Concise graph events; ask the producing library for full detail by stream id.
```

That is the contract, not a banner. Every event line carries exactly one strict
typed id — `graph:` for the pipeline's own events, `agent:` for a dispatched
`oneagentgraph` launch's, and `git:`/`pr:` for observed publication state — so each
summary can stay one control-stripped line derived from recorded status/result
values, and the id says which store to ask for the rest. The monitor never tries to
*be* the detail. Heartbeat lines carry no id — a heartbeat is the absence of an
event, and inventing one would put a value in the stream nothing can resolve. PR
state and check observations are separate events under the same `pr:` id; every
check line says `required` or `optional` before its state and name.

Which of those ids a read carries is the [profile](#read-profiles)'s decision: the
default `planner` profile admits the `graph:` lines and drops the `agent:` ones,
which is why a view that reports no tool calls has to be read with `--filter
monitor` or `--all` before that means anything.

**Dedup and snapshots.** Every source is polled, so each pass re-reads what it has
already reported. An observation is keyed by a **durable source identity** the
source itself guarantees — a journal sequence, a commit sha, a PR state signature
or an individual check observation — never by its position in a pass, so a monitor
restarted mid-run replays and then continues rather than double-reporting. Git and
GitHub are remote state that outlives the run but is not reproducible from the run
directory (a branch is deleted once its PR merges), so what they report is
persisted beside the journal, keyed by the same typed id. That is what makes a
replay of a finished run show the commits and PRs the live session saw, without
re-reaching the network.

The monitor only ever reads: it writes nothing to the ledger or journal, takes no
lock a writer needs, and treats every source as optional. A missing `gh`, an
unfetched branch, or an absent history store degrades that source to silence
instead of ending the stream.

All three read-only views take the run id `launch.json` advertises: `just monitor
RUN_ID`, `just status RUN_ID`, and `just telemetry RUN_ID`. Each resolves it the
same way — an exact run directory, or a plan name that names exactly one active
launch — and each takes a run id and nothing else, so an id that is all digits is
addressed no differently from any other. Scoped, `status` reports only that run's
indicators and the sessions its own scopes labelled; `telemetry` reports that run
whether or not it has settled, since naming it is the request and the settled-run
filter exists only to keep the *unscoped* index about live work.

A run's `result.json` is rewritten as it moves, so both `telemetry` and the DAG
read model describe its nodes from the journal itself: a node is `running` only
until the journal records it settling. A node recorded as `node-failed` reads as
failed in every read-only view, including while the rest of the graph is still in
flight.

`status` answers the same question from different evidence — a session is running
while its branch is still a checked-out worktree — and there the journal still
wins. A failed node can keep its checkout (a direct agent works in one it never had
to remove), so a session whose node the journal has settled is reported with that
recorded status, never as running. The rule across all of these is one rule: no
read-only view calls a node running once the ledger has recorded it settled,
whatever the filesystem still looks like.

For automation, `just telemetry [--all]` emits one schema-versioned JSON run
index. It joins phase, typed provider/failure identity, latest progress,
branch/commit/checkpoint, independent agent/gate/publication-wait timing plus
overlap-safe wall time, retry lineage, comparison base, reusable gate attestation,
and the persisted last-completed-check/current-blocker rollup. Top-level metrics
make retry reuse, recovered/abandoned branches, no-diff dispatches, and time from
green gate to publication directly consumable. The default is active runs;
`--all` includes settled runs.

Use `just telemetry --breakdown [--all]` for the operator view. The practical
field guide and diagnostic workflow are in [`telemetry.md`](telemetry.md); the
versioned cross-layer contract is in [`telemetry-model.md`](telemetry-model.md).

`just telemetry-server` serves the same read model continuously instead of once:
a loopback-bound HTTP API plus an SSE invalidation stream over a runs directory,
for the DAG UI and any other live viewer. `just dag-ui` is that same published
command with `--ui`, which answers the browser view built into the binary at every
path the API does not own — so the view and its data share one origin without
anything in front of them. It is not read-only: it also wraps the
post-launch verbs — stop, adopt, reply, shutdown — each performed as the one acting
session the server was started under and refused by the engine's own ownership rule,
as [`dag-ui.md`](dag-ui.md#supervising-from-the-browser) states. Its flags, response
shapes, and event vocabulary are fixed by
[`dag-ui/design.md`](dag-ui/design.md#running-it).

## Human completion attestations

After doing a reported action, attest it explicitly, over the live channel:

```sh
just channel-reply RUN <<'JSON'
{"version":3,"commands":[{"op":"attest","ref":"HUMAN_ID"},{"op":"attest","ref":"NODE_ID/STEP_ID"}]}
JSON
```

Several `attest` commands combine in one envelope, but references must be unique.
Only a human action recorded as `waiting` can be attested. Unknown ids, blocked nodes/steps, agent ids, and previously
completed humans are invalid and exit 2.

`onepipeline attest RUN REFERENCE` is the same operation as a one-command
envelope, for an operator with nothing else to say.

The harness never guesses that a meeting, approval, deployment, or other human
action happened. Each accepted attestation is journalled as a `human-attested`
operation with its reference, and the reconciler clears the decision it was
blocking on the same pass — so the dependents it was gating become ready without
anything else being asked for. If a top-level human is the final node, that
settles the run.

## The graph of record is the live graph

There is no replanning step, because there is no point at which the plan stops
being live. `plan.json` is the run's **launch record** and the reconciler never
rewrites it; the graph that is actually executing is the one projected from
`events.jsonl`, and every live edit the reconciler committed is in it — an `add`, a
`drop`, a `reparent`, a `retry` replacement and its new id, an amended `task` (the
node's review bar travels inside it, in `## Acceptance criteria`) or `max_turns`, a
branch pin. Only edits the reconciler *rejected* are absent, and their submitter
was told so synchronously.

Everything a transition used to do, the reconciler does as it goes:

- **Completed nodes leave the frontier** when they settle, rather than being
  dropped from a next plan.
- **A satisfied dependency stops blocking** its dependents on the same pass, so
  siblings that became ready together start together.
- **Unresolved lifecycle stack anchors and resume checkpoints stay on their nodes**,
  because nothing re-derives those nodes. An unresolved same-repository publication
  anchor is never cut by an attestation removing the human gate in front of it.
- **A preserved branch stays pinned to its node.** Which failures leave one is not a
  judgement anything makes at a boundary: `pin_preserved_branch` keys on the
  *status*, so `failed`, `cancelled`, and `parked` all keep their branch — see
  [Preserved committed work implies a recorded
  pin](repo-lifecycle.md#preserved-committed-work-implies-a-recorded-pin). A gate
  rejection and a refused publication both keep theirs. Nothing continues one on its
  own; a `retry` or `requeue` edit is what picks it up.

A **cross-DAG reference is not a dependency the graph can satisfy**: such a
reference — its spelling is the one [`## Node shapes`](#node-shapes) defines — names
a node of another run and none of this graph, so nothing here ever removes it. It
stays on its node and, when a `drop` carries that consumer out, passes to whatever
still depends on it — the same pass-through the publication anchor gets, for the
same reason. A watch its own consumer's completion silently ended would stop
reporting `upstream-modified` and stop blocking on an upstream that became
unresolvable, which is exactly what the reference is for. A consumer with no
dependents leaves nothing to carry the watch, and it ends there.

**Nothing continues a failed or cancelled lifecycle node on its own.** There is no
continuation budget and no `resume.attempts` field — `Resume` is
`{branch, checkpoint?, completed_steps}` under `deny_unknown_fields`, so a plan
writing an `attempts` is refused while it loads. A recorded settlement stands:
`graph::derive` re-derives only the two gates `blocked` and `skipped`, so a node
recorded `failed` or `cancelled` keeps that status for the life of the run. A
`retry` or `requeue` live edit is the whole of how one is picked back up, and the
branch stays recoverable with `just repo-recover` or `just publish-branch` in the
meantime.

The one automatic re-dispatch in the engine is narrower than that and is not about
continuation at all: `engine::attempt` asks again **only when a dispatch produced no
events whatsoever** — a provider that refused before the first turn. Three attempts
by default (`ONEPIPELINE_BOUNDARY_ATTEMPTS`), 5 seconds of backoff doubling to a
120-second ceiling, and each one recorded as its own `node-dispatched` carrying
`attempt` and `attempts`. Spent without the agent producing anything, the node
settles `no-agent-progress`; a dispatch that never started settles
`infrastructure-failure`. An attempt that recorded anything is never re-asked,
whatever its exit status.

`cancelled` and `parked` still differ, but in what the *planner* may do rather than
in what the engine does: `cancelled` is a stop the engine took (a `drop` or a
`retry` stopping a live dispatch), while `parked` is the planner's or the monitor's
own idle through `cancel`, and only a
[`requeue`](#parking-a-node-and-picking-it-up-again) lifts it. Both preserve their
branch — `projection::pin_preserved_branch` treats `failed`, `cancelled`, and
`parked` alike — so a later pick-up continues that branch rather than cutting a
fresh one beside it.

This replaced a rule under which the plan of record was re-read from a launch file
between rounds and structural live edits were round-scoped. Two failures were not
separable from that rule, and both were observed twice on real runs:

- **A merged node was rescheduled to redo its work.** A live `retry` gives the
  replacement a new id, so a transition reading the launch file saw only the
  original. The merged replacement was not recognised as done, and the superseded
  original was carried forward and dispatched again — `suite-false-failures-final`
  settled `merged` at 16:19:17Z and started again at 16:47:37Z.
- **A pinned retry was re-cut fresh.** A retry pinned to a preserved branch reached
  the graph but not the next round's plan, so the node dispatched `resumed=false` on
  a new branch cut from the base, orphaning verified work.

From the planner's side that rule was also indistinguishable from a dropped edit:
`channel-reply` reported the edit applied — and it was — with an expiry nothing
surfaced. That is worse than a rejection, which is at least reported. Neither
failure has anywhere left to happen: an edit is applied to the live graph and the
live graph is what executes.

`just replan` remains only to say so, and to name `just channel-reply`.

### Carried planner context

What the planner or the monitor learned while a node was running is not in the
launch record, and restoring the opening brief over it is how a worker came to
re-derive 41 commits of finished work.

A `note` is where that knowledge goes, and it is the **one** manager-note op: the
engine collapsed `context` into it and removed `context` from the reply envelope
outright, so an envelope still carrying that op is refused by name at the wire. The
field set, each field's default, and the dispositions the op answers with are declared
once, on `onepipeline::channel::Command::Note`; everything below derives from that
declaration as it stands in onepipeline 0.44.4 rather than restating it independently.

A note carries `id`, a required `addressee` of `worker`, `supervisor` or `both`,
`text`, and three optional fields: a `criterion`, a `deliver` of `live` or `next`
defaulting to `live`, and a `persist` boolean defaulting to `true`.

**`deliver` and `persist` are two axes, not one.** `deliver` decides whether live
delivery is attempted; `persist` decides whether the note is composed into the node's
next dispatch. Neither answers the other's question, and saying so is load-bearing:
`deliver: next` and `persist: true` both read as "on the next dispatch", and a reader
who conflates them gets this contract wrong. `persist: true` composes the note into
the node's next dispatch **if and only if the note did not reach a running turn** —
read it as "do not lose this" rather than as "send it twice". Their four combinations:

| `deliver` | `persist` | what happens |
| --- | --- | --- |
| `live` | `true` (**the default**) | the running turn is attempted; where it took the note nothing is owed forward, and where it did not the note is composed into the node's next dispatch and is **not** a refusal |
| `live` | `false` | the running turn is attempted; where it took the note that is the whole of the delivery, and where it did not the note is **refused** — the combination to ask for when that refusal is what you need |
| `next` | `true` | the running turn is not interrupted, so the note never reaches one and is always composed into the node's next dispatch |
| `next` | `false` | no live delivery is attempted and the note composes forward into nothing, so it reaches nobody whatever the run does, and is refused at the envelope before the run is reached |

The default is `deliver: live` with `persist: true` because it is the combination
that attempts the running turn *and* cannot leave the note nowhere. It is exactly
what the removed `context` op's `auto` delivery meant, and `auto` is gone with it:
it named a combination of both axes rather than a point on the delivery one.

**A note that would reach nobody is refused, naming what left it nowhere to go.**
One rule, checked wherever it can be decided — at the envelope, where `deliver: next`
with `persist: false` reaches nobody by construction; and at delivery, where only the
run can decide it, which is `deliver: live` with `persist: false` and no turn that
took it. A blank `text` and an `id` naming a node the graph cannot reach are refused
too, each naming which it is.

**What the op answers with is the disposition**, and under the default it is the only
way to learn which of the two things happened. Five words the run records —

- `worker` — the worker's live turn took it.
- `supervisor` — the supervisor's live turn took it.
- `judged-with` — the supervisor's decision was re-taken with it in hand and
  completed, carrying that completion reason, so no further worker turn took it.
- `queued` — no turn was live, so the next turn *of that conversation* to open takes
  it.
- `carried` — no turn of the node's dispatch took it, so it went to that node's next
  dispatch instead.

— plus one more a caller reads back from the verb rather than from the run: the reply
was accepted durably without the reconciler having answered within the reply timeout.
That is still queued rather than a refusal, and **never** an instruction to send the
note again. `just channel-reply` answers the bus's receipt alone, so the recorded word
is read off the run: the `reached` of the note's operation in its `edit-committed`
event, which `just monitor` renders.

The first four are the note reaching the running dispatch's conversation and `carried`
is the note reaching no turn of it; under the default those two are the only ways one
accepted note succeeds, and they are exhaustive and mutually exclusive. That is the
same biconditional `persist` is defined by, and telling them apart is the whole point:
they are materially different to whoever sent the note.

**A note goes to whichever party of the node's dispatch is speaking**, through the
delivery seam `oneagentgraph` publishes rather than through a bare interrupt, so the
party that is live takes it and the other party receives it with that party's
response. A `criterion` it carries enters the acceptance criteria that conversation's
judge decides against. That is what `context` could never do, and it is the incident
this collapse was made from: a manager's approval sent as a `context` note rendered
into the worker's task alone, the judge never saw it, and the node was failed for
omitting what the manager had approved.

**What it deliberately cannot do.** Reaching the running turn and being carried into
the next dispatch are mutually exclusive under `persist`'s biconditional, so there is
**no** way to do both. A correction that has to reach the live turn *and* still bind
the node's next dispatch is a ruling that survives a re-dispatch, and `amend` is the
op for that. The note is not given a second, weaker way to say what `amend` already
says properly.

**A carried note lasts one dispatch, and is its `text` and nothing else.** It is set as
the node's `context` on the running graph, rendered as a `## Planner context` section of
the task that node dispatches, and consumed when that dispatch takes it — which is the
whole reason the field is one string rather than a list. Its `criterion` is **not**
carried with it, because a criterion binds the conversation that read the note and a
carried note reached none: what the next dispatch sees is prose under a heading
declaring it adds no acceptance criteria. That is the removed op's behaviour exactly, so
a `carried` disposition on a note that carried a criterion is the signal to `amend` the
node instead. A note reports state observed while one
attempt was running, so it is stale the moment the next attempt moves; a note that
still matters is one somebody attaches again against what the run now shows. That is
what stops a node accumulating instructions nobody re-read.

Two more things do not carry, for the same reason they never did. A carried note follows
a **node id**, so a `retry` replacement — a new id — starts with none, and a note
given to the node it superseded stays with that node. A `retry` whose replacement
states `context` itself is the way to carry one across, an empty one included.

Everything *structural* carries without being carried at all, because the graph the
reconciler is executing is [the graph of record](#the-graph-of-record-is-the-live-graph).
Branch pins, resume checkpoints, and stack anchors are untouched by any of this:
context rides alongside them and changes none of them.

## Where this lives

Everything on this page is implemented by the published CLIs this repository
pins, and each recipe named above is a thin wrapper over one of their verbs.

- **`onepipeline`** — the plan and its validation, the continuous reconciler that
  schedules and settles it, the run ledger and its journal, the planner channel
  (surfaces, replies, live edits and their authorship, attestation), the read-time
  filter profiles, the read-only views, and the driver `just orchestrate`
  launches.
- **`onemessagebus`** — the channel's transport and the verbs this host speaks it
  through: `ask` (the shim `ORCHESTRATOR_ASK_MANAGER` names), `reply` and `send` (`just
  channel-reply`), `serve --codec monitor` (the observer's judge side) and `status`,
  with the validator, its pass cache, the monitor's author and binding, and the schema
  link the binding validates frames against configured once in
  `config/onemessagebus.yaml`.
- **`oneagentgraph`** — one dispatched agent turn and the graph of members a run
  drives, its history records, and its scratch.
- **`onevcs`** — repository identity, the rules that resolve a policy from it,
  sessions over isolated worktrees, publication, recovery, and integration.

Their contracts are documented in their own repositories; this page holds the
judgment and the operating protocol a planner works this host through.
