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
onepipeline 0.22.2. The whole of that reasoning —
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

**A plan is drafted locally, cleared locally, copied up, checked, and launched from the
board** — five commands in that order and no other:

```sh
just plan brief.md                       # a planner authors into `authoring` and a
                                         # design-doc node writes the document that plan
                                         # is reviewed as; a settled run's closeout
                                         # records the tasks it wrote
just review-plan authoring:<project>     # the judged turn, for anything nothing has read
just approve-design authoring:<project>  # the user's approval of that document
just copy-plan authoring:<project>       # onto `plans`; `--to` names another destination
just check-plan plans:<project>
just orchestrate plans:<project>
```

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
landed accepts it and spends no second judged turn. So does the design approval below,
for the same reason and one record further out — which is why the copy carries the plan's
**documents** as well as its tasks: the store's own `project copy` carries none, and a
plan copied without its design document arrives on the board with nothing to approve.
`tests/plan_tooling/test_copy_plan_recipe_e2e.py` drives the whole flow for real —
drafting, clearing, a trial run that writes nothing, the copy, the record read back off
what landed, and the check over it — against a second local store rather than the live
board.

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
whichever store the plan is held in — a directory of Markdown or a board — and what lets
it travel with `just copy-plan` the way a review record travels with a task. It is
*written* through `onetaskgraph document copy` rather than by editing a file, and that is
the half that makes the sentence above true of a board as well as of a directory: the
write goes through the store's own write side, so where a record can be written is the
store's answer rather than this repository's. That is the difference from the plan-review
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
exist yet. It is exempt because `scripts/plan.sh` stamps that project as a planning
project and the gate reads what the project says about itself — never because anything
recognises its shape, so a hand-written project that happens to carry a planner node and
a design-doc node is not exempt.

That stamp alone decided it once, and it was a hole: the plan a planner writes is stored
in the planning project the launch created, so the plan's own nodes inherited an exemption
granted to the project for life, and launching them dispatched real work with the design
document approved by nobody. So the exemption is bounded by both halves of what it was
written for — **the project holds exactly the nodes that launch dispatches**, which
`scripts/plan.sh` names on the stamp itself, **and it holds no design document**, since
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
it holds the plan a planner wrote into it, or the document its design-doc node produced.

## What the write-back owns, and what a green run proves

**What the write-back owns is the node projection, and nothing else on that record.**
It is a projection rather than a rewrite: before it builds anything it reads the
destination with `project show <project> --json`, and the shadow project it then
copies over carries **that read's own description** — so a body somebody authored on
the board survives every settlement of every run launched from it, re-read on the
adopted onepipeline 0.22.2. Below the 0.16.3 that fixed it, it did not: the shadow
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
behind. Re-read on the adopted onepipeline 0.22.2; below the 0.16.3 that added that
read, all three fail at once — the release beneath it performs no destination read at
all, copies three times, and leaves the record with an empty body.

**A projection that keeps failing is now spaced rather than hammered.** onepipeline
https://github.com/nickderobertis/onepipeline/pull/176, in force on the adopted
onepipeline 0.22.2, backs a failing write-back off from a prompt first retry to a one-minute ceiling
instead of retrying about four times a second, resets that schedule once it recovers, and
still retries until the projection lands; closeout still attempts the terminal projection,
and stopping or settling stays prompt during a long backoff. The reason is GitHub's
secondary rate limiter: a refused projection retried four times a second is what keeps the
board under the pressure that refused it, so the retry was extending its own outage. An
operator still gets one failure message per outage and one when it ends, and the failure
message now says the attempts are being spaced out. What that does **not** change is
anything in the paragraph below — see [Where a plan of this repository
lives](../AGENTS.md#where-a-plan-of-this-repository-lives) for the other half of that
limiter, which is the plan-store repair this host adopts beside this one, and for what
this host accepts in order to run it.

**But write-back is best-effort, and a green run therefore proves nothing about the
plan store.** It runs on its own worker off the reconcile loop, store reads never
feed back into scheduling, and closeout never waits out a store command — so a run
whose projection never landed settles exactly like one whose projection did. Measured
the same day and the same way, with the destination store made unwritable: the run
settled `complete` and its node `done`, `just orchestrate` exited 0, and the stored
task carried no `onepipeline.settlement` at all. The **only** thing that says so is a
line on the driver's own stderr — `onetaskgraph write-back failed for '<project>':
<reason>; retrying`, said once per failing streak and once more when it recovers —
which no run event, node settlement, or planner surface repeats, and which a
`--detach`ed run writes to a log nobody opens. So read the board as a projection and
never as the record: when what became of a node actually matters, read the run's own
`just results`, `just status`, and journal, which are written by the engine itself
and are the reason those views exist.

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

**`just plan` writes a lifecycle node**, so the planner it dispatches works in a
worktree cut from the registered `ai-orchestrator-isolated` safety clone with the
canonical checkout as its publication repository — the placement every other node
this repository dispatches carries, and for the reason the [self-dispatch
rule](../AGENTS.md#dogfooding-rule) gives: the launch directory is the shared
canonical checkout, and a direct node works in it. One did. It cut a branch there,
committed to it, and left it checked out; a finished lifecycle publication then
failed at its last step because the publication checkout was on that branch rather
than on its base, and returning it to the base deleted the manager's plan files,
which that planner had force-added onto its branch from gitignored paths.

Three consequences a manager reads rather than derives. The planner's working
directory is that worktree, so a brief that wants the plan back names a path that is
**committed** — a plan written to a gitignored path there does not outlive the run.
The launch takes the identity's repository holder like any other run of this
repository, so a `just plan` while a run of it is live is refused as concurrent
project work rather than queued. And `--repo` / `--execution-checkout` name a
different registered pair, while **`--direct`** is the escape for a planning dispatch
that must cut no worktree at all: it is the old shape, it dispatches into this shared
checkout, and such a dispatch may write only to gitignored paths, may not commit, and
may not leave the checkout on any branch but its base. Nothing enforces that, which
is why the recipe says it on every launch that takes the flag.

### The document a planning run also produces

**A planning run writes two nodes, and the second one writes the document the plan is
reviewed as.** A person cannot usefully review a plan node by node; what they can judge
is one short document — what is being built and why, the architecture, the contracts, the
acceptance criteria, and the planned work as a table of links, each row pointing at its
task. So `just plan` writes a `design-doc` node depending on the planner node, dispatched
under [`graphs/design-doc.yaml`](../graphs/design-doc.yaml) with
[`personas/design-doc.yaml`](../personas/design-doc.yaml) named as a path, placed at the
same publication repository and execution checkout the planner node is placed at. Its
task is the brief unchanged, followed by its own instructions and its own acceptance
criteria — which open by saying the criteria above them are the *plan's* rather than that
dispatch's, because a judge holds a dispatch to every criterion it finds in its task and
producing the plan was another node's job. What states the document itself is
[`config/design-doc-template.md`](../config/design-doc-template.md), and nothing restates
it: the node's task names that path, the persona names that path, and the file is the one
statement of the shape, the reader, and every property the document is judged on.

The dispatch reads the finished plan out of the store, stores what it wrote as a
**document of that same project**, and reports where the store says that document is — a
link where the store puts it on a website, a path where it puts it in a file on this
machine. Storing it beside the plan rather than reporting it is the point: the reviewer
finds it where the plan is, and follows the store's own answer rather than a path
somebody composed.

**A brief therefore names the plan's qualified project id**, on a line reading
`Plan project: <source>:<project>`, and a brief without one is refused at the exit status
a brief missing a required section is refused at. The second node has no other way to
find the plan: nothing hands one node's output to a later node, and a plan written to a
gitignored path in the planner's own worktree does not outlive the run. Nothing else in a
brief is parsed — and a line that is *there* and unusable is refused as a bad value
rather than as an absence, because two declarations are ambiguous and a value naming a
project in no store, reported as a missing line, sends a manager looking for a line that
is already in front of them. `--no-design-doc` drops the node **and** the requirement —
with nothing that reads the plan there is nothing that needs its id — and writes exactly
the one-node project this recipe wrote before the second node existed.

The published CLIs do not know that split and nothing here renames them to it.
Everywhere `onepipeline` and the recipes over it say *planner* — [the planner
channel](#the-planner-channel) and its surfaces, the `planner` [read
profile](#read-profiles), the [`awaiting-planner`](#when-an-attach-returns) state,
`onepipeline next`, `onepipeline reply` — the reader they name is the manager, and
every "planner" below is to be read that way. The dispatched planner reaches that
channel from the other end, as [an agent with a
question](#a-dispatched-agent-asks-its-manager): a surface *on* it rather than a
seat at it.

## The plan schema

The tracked-plan contract is the published `onepipeline` plan schema, and declaring
a `schema_version` is required: a plan that omits it, or declares a number this
build does not read, is refused at launch naming the ones it does. **Write version
3** — what every plan here declares, and the one the fields below describe. The
adopted `onepipeline` 0.22.2 also still reads 2 and 1, so an older plan file an
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
  attaches: the `monitor` member that watches the run, and the resettable-cron
  `check-in` member that paces planner updates. Neither drives anything. The two
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
  credited with are all `onepipeline start`'s own. There is no environment variable
  for either; the flag is the only way to move it.

  The document declares **schema 4**, for two fields. `check-in` carries its own
  `task`, which needs 3; that task opens with `{task}`, which `oneagentgraph`
  expands only from 4. `onepipeline` composes one task for this graph — it states
  what the run *is*, its id and its goal — and hands it to every member which does
  not claim one. A member's own `task` **replaces** it, so a member that claims one
  must interpolate it back in to learn which run it is on. `onepipeline` does also
  export `ONEPIPELINE_RUN_ID`, set to the run id, to an observer member — measured
  against onepipeline 0.22.2 by dumping both sides of a monitor member's whole
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

  It declares **schema 4** for the same reason `dag-scope` does: the member carries
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
- The monitor's judge side **is** the planner channel. Every monitor turn ends at a
  supervisor boundary that `onepipeline channel serve` raises as a non-blocking
  surface and blocks on, so a planner answering it with `just channel-reply` is not
  only editing the graph — their `message` becomes the monitor's next instruction.
  It is reached through `scripts/channel-serve.py`, because the two halves agree on
  the response object and disagree on the request; see [Serving the channel as the
  monitor's judge side](#serving-the-channel-as-the-monitors-judge-side).

### The observer graph is alive only while one of its members is

`graphs/dag-scope.yaml` names two members and only one of them is a conversation, which
reads like a free choice and is not. The `check-in` pacemaker is a scheduled
single-sided member and survives every run it fires on; the `monitor` is a
`kind: onejudge` conversation and is routinely settled early by its own quiet turns. The
obvious repair — make the monitor the pacemaker's shape, a scheduled `kind: oneharness`
member that samples every few minutes and cannot be scored against a completion bar — is
**not available on the pinned reader**, and reaching for it costs a run its watcher
entirely. This section records why, because the belief it corrects is one a reader
re-derives from the same symptom.

**The reader refuses an observer graph whose members are all scheduled.** Convert the
`monitor` block to a scheduled single-sided member, leave the pacemaker untouched, and
`oneagentgraph validate` answers:

```
oneagentgraph: invalid config: every member of this graph is scheduled or descends
from one, so the run quiesces as soon as its clocks tick and a deferred first turn
(check-in, monitor) never comes due; give each of them `start_after: 0`, or a member
outside the schedules for them to pace
```

The shipped document validates (`dag-scope: 2 member(s) OK`); only the conversion makes
it invalid, and a document the reader refuses attaches **no** observer at all. Giving the
monitor a deferred first turn while the pacemaker takes an immediate one is refused the
same way, naming `monitor`: once no member is outside the schedules, the reader asks
`start_after: 0` of every one of them.

**The remedy that refusal names buys one tick.** An all-`start_after: 0` observer graph
loads, and then, driven through `just orchestrate` against a real run with the suite's
provider stand-ins, its own journal reads:

```
07:40:48.055Z  graph-started   dag-scope
07:40:48.055Z  member-started  check-in / monitor
07:40:48.079Z  member-settled  check-in / monitor
07:40:48.180Z  graph-settled   {check-in: settled, monitor: settled}
```

125 milliseconds, against schedules of 600 and 1800 seconds. The driver then printed
`onepipeline: the observer graph for 'scheduler-research' has stopped watching; the run
is still being driven` and **did not relaunch it**; that run's remaining 88 seconds were
unwatched, and a run with no observer reports plain `ACTIVE`.

**The cause is liveness, not scheduling.** A graph runs while at least one member is
unsettled — mid-turn, or mid-conversation — and a scheduled member settles after each
firing, so a graph made only of scheduled members has nothing left to keep the process
alive until the next tick. `kind: onejudge` is the only long-lived member shape a
document here can declare. So the monitor's conversation is what keeps the whole observer
graph running, and the `check-in` pacemaker fires *inside* it.

**The pacemaker's survival is therefore not a property of its kind**, and reading it as
one is what makes the repair above look available. The evidence is in this host's own
recorded runs, counted over every `member-settled` a pacemaker has ever written: **89 of
them**, across 27 runs, and they split **50/39** on whether a conversation member was
beside them. Fifty belong to a two-member observer document — the shipped
`graphs/dag-scope.yaml`, and the older revision that spelled the same member
`orchestrator` — and every one of those fifty
fired while that member's conversation was live. Forty-eight settled while it was still
unsettled outright. The remaining two settled after it had **died**, and reading them as
counter-examples is the mistake this paragraph is guarding: both are turns that were
already in flight when the graph tore down under them, started at `08:50:43.215Z`
against a monitor death at `08:51:34.017Z` (`condemn-answer-steer`) and at
`14:10:57.668Z` against one at `14:11:05.880Z` (`dag-ui-observability-2`). **No
pacemaker turn on this host has ever begun after its graph's conversation member
ended.**

The other thirty-nine settlements are the same finding from the other side, and they are
why the count is worth having rather than merely large. They belong to one run,
`onetaskgraph-build-3`, wired to a one-member scratch document with no conversation
member at all (`scratch/graphs/dag-scope-quiet.yaml`, gitignored — a mitigation for a
flooding monitor, not a design). Each of those thirty-nine settlements took its whole
observer graph down with it, within **0.100s to 0.112s**, median 0.103s, every single
time. Read them as the **directly observed form of the failure the scheduled repair
would have introduced**, rather than as an exception to the rule the other fifty state:
this host has been running that experiment by accident for thirty-nine firings, and it
came out the way the 125-millisecond probe above did. A pacemaker with nothing beside it
paces nothing, because there is nothing left alive for it to pace. Both counts are
readings of this host's accumulated journals rather than claims about a release, so
nothing re-takes them; what the gate below holds is the behaviour underneath them.

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
documented in `onejudge` 0.7.0's `src/cli/config.rs` and `src/engine.rs` as the opt-out
for "an observer instructed to answer with one fixed short sentence while it finds
nothing", with `max_turns` left as the bound. Nothing here sets it today; that is a
change to `personas/orchestrator.yaml` and a decision for a manager, not something this
section claims is in force.

The second is the bound itself, and on this host it was overwhelmingly the one that
fired: in `root-causes-94-plan` the monitor settled five times and every one of those
five is the turn immediately after its fiftieth. That ceiling is now 4500, derived from
this host's own recorded runs — the corpus, the eligibility rule, the exclusions, the
turn rate and the arithmetic are all written where the value is declared, in
`personas/orchestrator.yaml`. It stays finite deliberately: it is what bounds a wedged
or looping supervisory conversation, and the sibling `check-in` member keeps a finite
deadline for the same reason.

**Two upstream changes would lift the constraint, and neither belongs to this
repository.** Either alone is enough:

- **`oneagentgraph`** — keep a graph whose members are all scheduled alive between
  ticks, instead of refusing the document and settling the `start_after: 0` variant
  after one firing each. It would be working when a two-member observer graph with
  deferred first turns loads, and its members' second turns appear in the run's own
  event stream at their declared periods.
- **`onepipeline`** — relaunch an observer graph that has settled while its run is
  still being driven, rather than printing that it has stopped watching and continuing
  without one. It would be working when the driver emits a second `graph-started` for
  the same `--dag-graph` after that message, and the run's remaining nodes are covered
  by monitor turns.

Do not implement either from here. Until one of them lands, the monitor stays a
conversation, and `tests/e2e/test_observer_graph_liveness_e2e.py` holds all of it: that
the shipped document still validates, that the reader still refuses the all-scheduled
one in the words quoted above, and that the remedy it names still settles the observer
after one turn per member.

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

`--kind` accepts **`check-in` and `finding`**, and refuses anything else by naming
both: `invalid value 'whatever' for '--kind <KIND>' [possible values: check-in,
finding]`. `check-in` is the pacemaker's word — it also resets the pacemaker's
clock through `oneagentgraph reset-timer RUN check-in` — and `finding` is something
a watcher saw and decided the planner should know, raised deliberately rather than
as the side effect of a turn having happened. That is the CLI's own restriction and
not the queue's: `kind` on a queued surface is a free-form string, which is how
`scripts/channel-serve.py` raises `monitor-failed` and `monitor-completion`, and how
the engine raises `monitor-edit` and `edit-rejected`.

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
question](#a-dispatched-agent-asks-its-manager). The planner replies with one of
these legacy verdict shapes:

```json
{"completion":false,"message":"retry X with the fixture requirement","reason":"the graph is not complete"}
{"completion":true,"reason":"publication and follow-up triage verified"}
```

`completion: false` requires both `message` and `reason`; `completion: true`
requires `reason`. Completion is reserved for a verified `closeout` after the
whole graph is published and follow-ups are triaged. See [Live graph
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
```

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
rather than a simulated user. That is what makes a reply *supervision*: the
planner's `message` is handed back to the monitor as its next instruction.

The wiring goes through `scripts/channel-serve.py`, and the reason is one
mismatch. The **response** shapes already agree — `onepipeline channel serve`
answers with exactly the `{completion, message, reason}` object onejudge's
`supervisor` op expects, and the filter passes it through byte for byte. The
**request** shapes do not:

| | Shape |
| --- | --- |
| onejudge 0.7.0 writes to a judge command | `{"op": "supervisor", "task", "persona", "done_when", "worktree", "history_name", "messages": [...], "session"}` |
| `onepipeline channel serve` reads | `{"kind", "message", "blocking"?, "node"?}` |

Naming `onepipeline channel serve` directly as the member's `judge.command` is
therefore refused on the first turn — `the observer emitted a bad frame: unknown
field 'op'` — and onejudge kills the member with `provider produced no output`,
leaving the run driven but unwatched. The filter recovers the two values a surface
needs, both out of the frame itself:

- **the run id**, from the composed task's opening ``onepipeline run `<id>```. The
  environment carries it too — `ONEPIPELINE_RUN_ID` is set to the run id there,
  measured against onepipeline 0.22.2 in the judge command's own environment on a real
  launch, and re-taken on every gate run by
  `tests/e2e/test_orchestrate_launch_e2e.py`. The filter reads the frame it already
  validates instead, because that is a contract rather than a per-release export;
- **whether the monitor took its turn at all**, from the last thing it said. What it
  said is not read beyond that, because prose raises no surface — see [A monitor
  reports through the `finding` op](#a-monitor-reports-through-the-finding-op) below.
  The one thing still read out of that message is a turn the monitor did *not* take.

A turn the agent side lost does not always arrive empty: the harness writes its own
machine transcript into that message instead — measured off this host's
`runs/rc-fixes-brief` channel, fifteen JSON-RPC frames and 21,531 characters, most of
it the prompt echoed back, ending in a `method: error` frame and a `turn/completed`
whose `status` is `failed`. Twenty of them queued unread on one run. That is the same
provider defect as an empty turn wearing content, and reading it as content would let
a monitor whose agent side is failing look healthy for the rest of the run. So the
filter recognises a lost turn and raises it as a named failure, under its own kind:

| | |
| --- | --- |
| kind | `monitor-failed` |
| message | ``monitor turn failed: usageLimitExceeded on codex. It said nothing, so there is nothing to answer; its 21531-character transcript is not repeated here. Read it with `just monitor rc-fixes-brief --filter monitor`.`` |

The identity is the actionable half — this host's two codex identities hold separate
quotas — and it is read out of the transcript's own opening frame, compared against
`ORCHESTRATOR_CODEX_ALT_HOME`. A transcript that names no harness says so rather than
guessing at one. Only a turn the filter can *prove* was lost is named as a failure: a
terminal turn status of `failed`, or an error frame. That vocabulary is deliberately
never widened, because widening it is how the next harness's fourth shape outruns the
classifier and a planner is sent to the wrong quota.

**A transcript no failure can be proven inside raises nothing**, and it used to raise a
bounded `monitor-transcript` line of its own. That kind is gone with the prose path it
was compensating for. It existed because prose was republished verbatim: measured on
2026-08-24, 26 oversized surfaces on this host, every one `status: completed` with
`error: null`, so nothing was provable about any of them and all 26 were raised as the
monitor's own words — 176.1 MB of protocol carrying zero model-authored characters, a
3.6 GB journal holding one 699 MB event line, and a read-only `just runs` that needed
5.7 GB of RSS. With nothing republished there is nothing to bound: an unprovable
transcript is content the member produced, so the member is answered and lives, no
surface is queued, and the run keeps the turn where `just monitor <run> --filter
monitor` reads it. That date stamps this host's own accumulated journals rather than any
release, and `tests/e2e/test_monitor_quiet_turn_e2e.py` drives what an unprovable
transcript gets now.

`tests/e2e/test_lost_turn_wire_contract_e2e.py` drives the surviving surface onto a real
published channel with the real `codex` transcript behind it, and reads it back out of
`runs/<run-id>/channel/queue.json` where a manager reads one.

It raises the surface **non-blocking**. A blocking one would hold the run at
`awaiting-planner` on every monitor turn — ending the attached launch's
settle-and-return contract, and stopping the frontier to ask about watching rather
than about work. A planner who never answers simply leaves the monitor waiting,
which costs the run nothing.

The filter never answers on the planner's behalf. A frame it cannot serve, and a
refusal from the channel itself, both exit non-zero with the reason on stderr
rather than printing a `{"completion": ...}` onejudge would act on — a fabricated
verdict there would continue or settle a run nobody ruled on.
`tests/e2e/test_orchestrate_launch_e2e.py` drives the whole round trip on a real
launch, and each refusal through the real script.

#### A monitor reports through the `finding` op

A monitor has exactly one way to tell the planner something, and it is the `finding` op
in an `onepipeline reply` envelope. The prose a turn ends in raises **no** planner
surface, whatever it says.

That is a deletion rather than a filter, and the measurement behind it is one run's own
queue. While prose was raised automatically, a monitor with a finding to file had three
moves and none of them was clean: the prose alone, which loses the operation's node
attribution and its structured kind; the operation *and* prose, which is two surfaces
for one finding; or the operation and a quiet-turn sentinel, which is one surface and a
false statement its own judge then scores against a bar about surfacing everything
observed. It chose the middle every time. Of `root-causes-94-plan`'s 54 surfaces, 19 are
findings and 8 are `monitor` prose, and all 8 duplicate the finding immediately before
them, raised three to thirty-five seconds later, six byte-identical and two
restatements — 30% of the monitor-authored surfaces carrying nothing the operator had
not been handed seconds earlier, against a
[`personas/orchestrator.yaml`](../personas/orchestrator.yaml) that promises a finding
arrives once. The one line a planner may never filter is the unread-surface count that
[`AGENTS.md`](../AGENTS.md) owns the rule about, and it is worth nothing when part of
that count is a repeat — a blocking surface produces no other signal until it is read,
so every duplicate degrades the one indicator that discipline exists to protect.

Suppressing prose that merely *resembles* a recent finding was the obvious alternative
and the same measurement rules it out: two of those eight were restatements at very low
token overlap, so content matching catches at most six of eight while risking the
suppression of a genuine follow-up. Removing the path removes the choice instead.

**What a turn's content is still read for is liveness, and nothing else.** A monitor
that found nothing and a monitor that said nothing are two different turns, and the
filter used to have one answer for both — which cost this host its whole supervisory
tier for hours at a time. A frame carrying no assistant content was refused as a
protocol failure, `oneagentgraph` recorded `member-died
{"rule":"provider-failure","cause":"protocol"}`, and the run carried on reporting
`ACTIVE` with nothing watching it. Observed on `spanish-language-tutor-upgrade`, which
lost its observer five minutes in and ran roughly two hours that way while every other
indicator stayed green.

| The turn's last message | What happens |
| --- | --- |
| any assistant content at all | no surface is raised and nothing is queued; onejudge is answered `{"completion": false}` with a message telling the monitor its report goes as a `finding`, so the member lives and keeps watching |
| a machine transcript a failure can be *proven* inside | the one surface left on this path: a bounded `monitor-failed` line, per the section above — this is the filter reporting on the member, not the monitor reporting on the run |
| no assistant content at all | refused: an empty turn is a real provider defect and is what a lost turn looks like from here |

There is deliberately no fixed string anywhere on this path and nothing to compare a
message against. A sentinel is a vocabulary, a vocabulary can be got wrong, and the
monitor was being scored on getting it wrong.

**What covers a monitor that observes something and does not file it** is its
supervisor, not this filter: prose is the safety net no longer, so the monitor's own
`user.persona` requires such a turn be sent back until the finding is on the channel.
The cost is stated plainly because it is real — a monitor that notices something, writes
it as prose, and is not sent back has reported it to nobody.

**The periodic `check-in` member is untouched by all of this.** It is a single-sided
`kind: oneharness` member with no judge side at all, so it never reaches
`scripts/channel-serve.py`: it raises its own surface with `onepipeline surface --kind
check-in` and its report reaches the queue under its own kind and source.
`tests/e2e/test_monitor_quiet_turn_e2e.py` proves that on a real launch rather than
asserting it, by reading the pacemaker's surface off the queue in the same run whose
monitor prose raises none.

Structured output would be the heavier way to draw the same line, and one constraint
rules it out: oneharness validates a structured answer against the complete response, so
`stream = true` and `schema_file` cannot both hold, and turning streaming off for the
run's long-lived watcher would trade away the per-turn visibility a manager supervises
with.

`tests/e2e/test_monitor_quiet_turn_e2e.py` drives each of those answers through the real
filter onto a real published channel, and reads what was and was not queued out of
`runs/<run-id>/channel/queue.json`.

#### The completion bar is scored by the planner too

onejudge asks a judge side **two** ops, not one. `supervisor` comes at each turn
boundary; `judge` comes once the conversation ends, to score `user.done_when` —
always, whether the supervisor ruled complete or the turn cap ran out, and
independently of `evals` and `assessment`. Measured on onejudge 0.7.0 with a
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

So the filter serves that op the same way it serves `supervisor`, and for the same
reason — **the planner is this member's judge side, so the planner scores the
criterion**. It raises the criterion as its own non-blocking surface, under kind
`monitor-completion`, and relays the ruling that comes back: `completion` becomes the
boolean score and the prose beside it becomes the rationale. Nothing is invented. The
surface says in as many words that the run is not blocked on it, because it arrives
once, at the end, and a manager meeting it for the first time must not read it as a
run held up on them. A planner who never answers costs nothing: `channel serve` times
out with its own non-completion, which reads through as `unsatisfied` — the
conservative direction, and only for the run nobody answered rather than permanently.

This is a **workaround for an upstream gap**, and it is written down as one so it can
be retired rather than maintained: a member whose judge side is not a harness still
gets a scored bar it cannot answer, and neither `done_when: null` nor
`done_when_replaces_base` can remove it. `tests/test_observer_judge_ops.py` holds the
filter to going on serving the op, and
`tests/e2e/test_monitor_survives_the_channel_e2e.py` asserts on a real launch that the
member still carries a bar at all — so the day a release lets one decline it, that
check fails and the score path can go.

`assess`, the op a top-level `assessment` produces, is still refused by name, as is a
`judge` asking for a score on a scale: a planner rules with a boolean, and a boolean is
not a number. Neither can arrive from this repository's graphs, because
`tests/test_observer_judge_ops.py` forbids any channel-served persona from declaring
the keys that would ask them.

#### An answer addressed to the engine, not to this reader

There is one answer the filter recognises and does **not** refuse, and it is the
second way a monitor dies. The channel is a durable queue with two readers — the
monitor's judge side, which wants a supervisor ruling, and the engine's reconciler,
which wants graph edits — and through onepipeline 0.8.x it arbitrated between them by
arrival order. So a manager's [live edit](#live-graph-edits) —
`{"version":1,"commands":[…]}` with no boolean `completion` — reached the monitor's
judge side whenever it got there first. Forty of this host's recorded dag-scope runs
died there, refused as `is not a supervisor ruling` and killed. The timing was the
worst part: it fired precisely while a manager was supervising, because the manager's
own correction was what killed the watcher.

**The adopted release fixed that at its source, and the filter stayed.** A reply is
now routed by the halves it carries: `Channel::answer_if_verdict` puts a commands-only
envelope on the command path alone and leaves the pending surface — and any reader
waiting there — untouched, `Channel::claim_reply` hands the verdict reader one ruling
per claim and passes over such an envelope an older build already queued, and an
envelope carrying both goes to both paths. So a manager issuing a live edit mid-turn
no longer reaches the monitor at all. What the filter below keeps is the answer for a
release that regresses, driven directly at both boundaries rather than through the
channel, because this failure announces itself nowhere else.

The filter discriminates on the verdict. An envelope carrying a boolean `completion`
is a ruling and is relayed exactly as before, however many edits ride with it. An
envelope carrying `commands` and no `completion` is not this reader's, and is
**recognised, named back to the monitor in a non-completion ruling, and otherwise left
alone** — so the member takes another turn instead of dying. A non-completion is not a
verdict on the planner's behalf: it settles nothing, completes nothing, and rules on
no work, and all it says is that this surface has not been answered yet, which is what
happened. Any prose the planner sent beside their edits is carried through to the
monitor, since this reader is the last thing holding it.

**Left alone, and not handed back.** The obvious repair — re-send the envelope with
`onepipeline reply` so the reconciler gets it — is wrong here, and the reason is
measured rather than argued. `onepipeline reply` applies an envelope's commands
*itself*, before the envelope is queued for any reader: replying `{"op":"add", …}` to
a real run on onepipeline 0.22.2 answers `{"reply":0,"state":"applied"}` and records
`edit-committed` there and then. The edit has therefore already reached the engine by
the time it arrives at this reader, which has nothing left to route — and re-sending
it applies it a **second** time. The same measurement, re-submitted, comes back
`add: node 'added-by-the-edit' already exists`; an op with no such guard (`retry`,
`cancel`, `requeue`) would simply be applied twice.

That premise is the load-bearing one: if `reply` ever stopped applying commands
itself, ignoring one here would lose it. So it is gated rather than remembered —
`tests/e2e/test_monitor_survives_the_channel_e2e.py` sends a real commands-only
envelope on a real run's channel, asserts the verb answered it the way this paragraph
quotes, asserts it reached the graph, and asserts the member lived through it. This is a local mirror in any case: the durable fix is upstream, a
reply routed by its intended reader rather than claimed by arrival, and the filter
holds the same line independently of whatever release is installed.

### A dispatched agent asks its manager

The same `channel serve` verb is the other end of the channel: how a worker that
has reached a decision fork stops and asks rather than guessing. **Every launch this
repository makes** exports the path of `scripts/ask-manager.sh` into the launch
environment as `ORCHESTRATOR_ASK_MANAGER`, and that wrapper is the one supported way
to ask. It takes the question as an argument, as `--file <path>`, or on stdin,
blocks, and prints the manager's answer on stdout.

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
*When* a fork is worth blocking on is the dispatched role's judgment rather than
this page's; both are in [`personas/planner.yaml`](../personas/planner.yaml).

The frame it writes is the shape the table above gives `channel serve`: kind
`planner-question`, and **`"blocking": true`**, which is what holds the run at
`awaiting-planner` until an answer arrives. It is one compact line, because a
pretty-printed frame is refused as a parse error at line 1 column 1, a message
naming the symptom and not the cause. `ONEPIPELINE_RUN_ID` names the run to
ask on, and an unset one is refused rather than guessed at. What sets it depends on
the launch, measured per shape by `tests/ask_seam/test_launch_ask_seam_e2e.py`: **every
node dispatch of a run carries it as of onepipeline 0.22.2**, composed where the
dispatch is made, so all three `just orchestrate` shapes reach a worker that can ask.
That names the release in force rather than the one it arrived in —
`executor::dispatch_env` has composed the pair since
https://github.com/nickderobertis/onepipeline/pull/76, and `AGENTS.md` carries that
release number, because this page is held to naming the adopted one alone.
Below *that* release only the **attached** shape did, and by accident of process rather
than by design — an attached driver starts its observer graph in its own process, and
the export leaked from there into every dispatch it made afterwards, which is why a
launch passing `--dag-graph off` carried nothing even attached. Every `just plan`
dispatch carries the id on either side of that bump, because that recipe exports it
itself; that is sound only because it refuses a name whose run root is already taken
— `onepipeline` mints `<name>-2` when one exists, so without that refusal the
exported id could name a live run belonging to somebody else's workstream. What a
`just orchestrate` dispatch is given is not derived here from the plan's `name`,
which would be restating how a run id is minted; the journey reads it back and
checks it names the one run that launch created. An observer member carries the
run's id too, so finding the variable says which run a process is *under* and never
that it is a dispatch.

**Naming the run is half of reaching it; the other half is knowing where that run's
records are.** `onepipeline` finds a run under `ONEPIPELINE_RUNS_DIR`, and under a
*relative* `runs` when nothing names one — and a lifecycle dispatch works in a session
worktree, which has no `runs` directory and is not the checkout the launch ran from. So
an ask made from one was refused `no such run '<run>' under runs`, with the question
never reaching the channel and no surface raised: silent from the manager's side, and a
worker told to ask left guessing after all. The wrapper now resolves that directory
before it serves, and states it to `channel serve` as an environment value rather than
by changing directory — which is what keeps a `--file` path and a piped question the
*caller's*, relative to wherever the agent ran the wrapper.

Two rungs, each corroborated against `launch.json`, the file `onepipeline` discovers a
run by. First the question the CLI is about to ask: is this run under the directory it
would look in? Yes leaves the environment untouched, which is the branch every ask from
the checkout root takes. No passes that directory over — the run id identifies the work
and a runs root is only how to find it — for `ONEPIPELINE_NODE_SCRATCH_DIR`, the one
thing a dispatch carries that names its own run's directory, walked up to the ancestor
named for this run that holds a launch record. **The checkout the wrapper itself lives
in is deliberately not a rung**: it is usually also where the launch ran, but nothing
ties the two, and asking confidently on the wrong store is worse than being refused.
Where neither rung answers, nothing is exported and the ask stays where it was, for
`serve` to refuse naming what it looked under. That the engine keeps a dispatch's
scratch under its run's own directory is `onepipeline`'s to change, so
`tests/ask_seam/test_launch_ask_seam_e2e.py` measures it against a real dispatch of
every launch shape rather than restating it.
`ORCHESTRATOR_ASK_MANAGER_NODE`
optionally attaches the surface to a node, and a node the run does not have is a
fatal refusal rather than a retry.

The manager answers it as an ordinary blocking surface — `just channel-next RUN`
hands it over, `just channel-reply RUN` answers — with one requirement the surface
states in its own text: **the reply's `message` must repeat the correlation token
verbatim.** A reply here is [claimed by whichever reader reaches it
next](#a-planner-writes-a-reply-once), so a ruling that does not echo the token is
somebody else's answer arriving at this call, and a listener re-arms rather than
handing it over.

**What it does about that depends on the token the stray ruling carries**, and the
difference is worth knowing because one of the two puts a second question in front of
you. A ruling echoing *another* ask's token is an answer that outlived the asker it was
meant for; this question's own blocking surface was never touched and is still the one
thing to answer, so the wrapper re-arms behind a **non-blocking** note that says so and
asks to be left alone — you answer the question, and the re-armed listener receives it.
A ruling echoing **no** token is this question's own surface answered without the echo:
that surface is spent, and a run with nothing blocking pending refuses every further
reply (`run '<id>' has settled, so nothing will ever read a reply to it`), so the
question is put back as a blocking surface or nobody could answer at all.

The split exists because a duplicate blocking question is self-perpetuating. On run
`issue-28` a re-ask queued one, the manager answered both copies, only one listener was
left to claim an answer, and the orphaned one was then drawn — milliseconds after
asking — by the next question put to that channel, which doubled in turn.

**A re-arm is a new listener, and the question only survives one because the ask has a
name.** `onepipeline channel serve` is a listener an asker rents rather than the asker
itself, so a succession of them over one still-pending question is what waiting here
looks like. A session that ends leaves what it raised marked as owed to nobody, and the
engine gives that back only to a later session carrying the **same asker** —
`ONEPIPELINE_CHANNEL_ASKER`, an opaque word compared for equality and nothing else,
which the engine composes for every dispatch it makes as that dispatch's own scratch
path. A session naming none adopts nothing and nothing adopts what it raised, so before
the wrapper named one, each re-arm withdrew the question: the queue held it in neither
slot, a manager's verdict naming it was refused for naming a question the run had not
handed out, and the ask blocked for its whole reply window and was killed with nothing
on either pipe. Nothing raised a surface, because a question that never arrives looks
exactly like an agent that never had one.

So `scripts/ask-manager.sh` **inherits a dispatch's asker untouched and names itself
when nothing gave it one**, from the same token it minted for that question — already
this invocation's alone and constant across its re-arms, which is what an asker has to
be. Inheriting rather than minting over is the engine's own model and not a detail: the
asker is the *dispatch*, so a question an earlier ask of that dispatch left outstanding
is still owed and is taken back. A blank inherited value is read as none given, because
`serve` refuses one — a name every session matches would take over questions belonging
to askers it has never heard of — and that refusal would reach a caller as a channel
that would not accept its question.

**An envelope that question cannot use is refused where you send it.** The wrapper acts
only on a JSON object carrying a boolean `completion` and discards everything else with
nothing on the channel to say so: three replies omitting it were each reported
`delivered`, each read by nobody, and the planner that asked stayed blocked for about
thirty-five minutes, re-asking twice. So `just channel-reply` reads the pending surface
first — `scripts/channel-reply.sh`, judging by the same rule the wrapper applies, out of
the one file both source — and refuses such an envelope, naming the field that is
missing or of the wrong type and leaving the question pending for your next try. It is
narrow deliberately: it fires only while a **blocking** surface carrying that wrapper's
token is pending, and never on an envelope carrying `commands`, which is a live edit the
engine routes to the command path and which you must still be able to send while a
question waits.

**The wrapper is mitigating this, not fixing it, and the fix is onepipeline's.** Two
things in that crate produce the duplication between them, and both are stated here
against the source at tag **v0.11.0** — the release
[`config/onepipeline.version`](../config/onepipeline.version) adopts and therefore the
one a dispatch runs. Line numbers are that tag's, which is immutable; the function
names are what to search by if a later release moves them.

*A reply is bound to the next reader, never to the surface it answers.*
`wait_for_reply` (`src/driver.rs:1918`) hands back whatever `ChannelState::claim_reply`
(`src/channel.rs:594`) gives it, and that is a cursor over `replies.jsonl` —
`self.replies().into_iter().find(|queued| queued.id >= claimed_through && …)` — so the
oldest unclaimed reply goes to whoever polls next regardless of which question it
answers. Nothing could correlate the two even in principle: `Reply`
(`src/channel.rs:173`) carries a version, an author, `completion`, `message`, `reason`
and `commands` — and no surface id. `ChannelState::answer` (`src/channel.rs:532`)
records none against the reply it queues either, clearing `pending` wholesale at
`:534`. That absence is the whole reason a correlation token had to be invented in a
shell wrapper. **The upstream change
is to carry the answered surface's id on `Reply` and `QueuedReply` and to have
`claim_reply` pass over a reply whose id is not the caller's own pending surface** —
which also retires the token, since a reply that cannot reach the wrong reader needs no
echo to be recognized. That id has to be an optional, defaulted field: `Reply` is
`#[serde(deny_unknown_fields)]` at `src/channel.rs:172`, so a reply written by anything
older carries none and must still be claimable, and a reply carrying none must still
serialize without it.

*A listener cannot wait without asking again.* `serve` (`src/driver.rs:1814`) loops over
its stdin frames at `:1819` and calls `channel.push(Surface { … })` at `:1850` for every
frame it accepts, and `ChannelState::push` (`src/channel.rs:482`) appends
unconditionally at `:491`. The single exception is the pacemaker's, `retain`ing away a
superseded `source::CHECK_IN` at `:486`-`:490` — a proposal frame, which is what an ask
is, has no such path. So there is no listen-only mode: waiting again necessarily
queues another surface. **The upstream change is a frame that waits on the reply queue
without pushing one** — the `ObserverFrame` at `src/driver.rs:1898` is where it would
be declared, and `serve`'s body is where the `push` would become conditional on it.

*What the mitigation does and does not prevent.* It prevents the observed defect: a
stray ruling no longer puts a second **blocking** question in front of the manager, so
the self-sustaining loop above is broken and one ask is one question to answer. It does
not prevent a second **surface** — the re-arm is still a `push`, of a non-blocking note,
because of the paragraph above — and it does not prevent a reply from reaching the wrong
reader in the first place, which is what the token detects rather than avoids.

Nothing else is handed back to the asking agent either: a reply that is not a
ruling — a ruling carries a boolean `completion`, and a live graph edit routed here
does not — and the ruling `channel serve` synthesizes at exit 0 for its own timeout
are both refused, with the reason on stderr and nothing on stdout. The window that
timeout measures is the wrapper's own, fifty minutes rather than `serve`'s ~30
seconds, and `ORCHESTRATOR_ASK_MANAGER_TIMEOUT_SECONDS` moves it.

`scripts/ask-manager.sh` states each of those checks against what measured it, and
`tests/ask_seam/test_ask_manager_e2e.py` drives every one against a real run's channel.

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

**Acceptance means delivery for a reply, exactly as it does for an edit.** A reply
`channel-reply` accepts is appended to `runs/<run-id>/channel/replies.jsonl` and
claimed from there by whichever reader reaches it next, under the queue lock, so
one reply reaches exactly one reader and no reader can lose it. Nothing has to be
listening at the moment the planner writes, and there is no boundary at which the
listener changes: the reconciler runs for as long as the run does.

The command reports what happened, on stdout:

```json
{"reply": 3, "state": "applied"}
```

`applied` means the reconciler answered the envelope before the command exited.
The reply survives whether or not it did — it is durable the moment it is
accepted — so a state that is not `applied` is not an instruction to resend.

**What that state is not is a receipt that anybody could act on the reply.** It is a
transport receipt: the engine took the envelope and handed it to whoever was waiting,
and whether that reader can *use* it is a different question the engine does not ask.
The one reader that provably cannot is [a dispatched agent's
wrapper](#a-dispatched-agent-asks-its-manager), which is why `just channel-reply`
refuses that one case before the engine ever sees it.

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
check-in agent. That read-only actor synthesizes a concise per-workstream update
from the run journal, status, monitor, telemetry, and labeled history, then sends
it exactly once with `onepipeline surface` — the verb
[`personas/check-in.yaml`](../personas/check-in.yaml) names, because that member's
working directory is the graph member's own scratch and there is no `justfile`
there to reach. `just channel-surface` is the operator's spelling of the same
verb. The command queues the non-blocking
surface without waiting for a planner reply; the reconciler neither authors nor
relays its content.

**The pacemaker's scope is structural, not advisory.** The member carries its own
`task` in `graphs/dag-scope.yaml`, and that task both scopes it to reporting and
forbids `onepipeline reply` by name. Live edits belong to the
[monitor](#the-agent-graphs-a-run-launches) and the planner. This member is
single-sided and finitely deadlined by `oneharness.check-in.toml`: it takes one
short turn and exits, so an edit issued from it would be answered by the reconciler
after the member that issued it had stopped watching — nobody left to read the
outcome, and nobody who saw the state that justified it. Its own task is what makes
that impossible whatever the run-level task happens to say.
`tests/e2e/test_orchestrate_launch_e2e.py` launches a run whose pacemaker comes due
mid-flight and reads the prompt each member was actually given, so a task that is
present in the file but not reaching the model fails there.

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
carrying `"heartbeat_interval"` is refused whole:

```
onepipeline: refused: the reply is malformed: unknown field `heartbeat_interval`,
expected one of `version`, `author`, `completion`, `message`, `reason`, `commands`
```

The verdict and graph edits in that reply are lost with it, so trying costs the
whole envelope rather than being a no-op. Pick the interval at launch.

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
decision, while `false` is an informational follow-up that does not stop the graph
frontier. Everything the monitor and the pacemaker raise is non-blocking by
construction: an observation is not a question the graph should stop for.
`monitor` renders `ACK REQUIRED` while any blocking surface, including closeout,
awaits a reply.

The raw reply schema remains available for edits and automation. A continuing
reply is `{"completion":false,"message":"what to do next","reason":"why"}`;
an approval is `{"completion":true,"reason":"what was verified"}`. Either may
also contain `"version":2` plus a `"commands"` array using the operations below.
That version is the engine's own `REPLY_ENVELOPE_VERSION`, and it reads **2** since
the manager-note collapse removed `context` from the envelope.
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

Send a version-1 edit envelope to `channel-reply`:

```sh
just channel-reply RUN <<'JSON'
{"version":2,"commands":[{"op":"reparent","id":"pending","deps":["slow_b"]},{"op":"drop","id":"slow_b","dependents":"detach"},{"op":"attest","ref":"approve"}]}
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
| `settle` | `id`; `outcome`: `done` or `failed`; `evidence` | Settle a node at what an operator can see it reached, from evidence this run never observed — the case being a change that merged while the node's own record read `failed`. It mutates no edge and moves no lineage: the node keeps its id and its dependents, and only its recorded state moves, which is the thing that was wrong. `evidence` is required and never blank, and is journalled as the reason the node is in the state it is. A settle that changes nothing is refused as a duplicate; a node that settled *something else* is exactly what it is for, and the earlier settlement stays in the journal beside it. |
| `finding` | `message`; optional `id`, `blocking` | Raise what the author saw as a planner surface of kind `finding`. Compiles to a `finding-raised` operation and mutates no graph. `message` may not be empty; `id`, when given, must name a node the run has and files the surface against that workstream; `blocking` defaults to false. |

#### Who issued an edit, and what that bounds

The envelope's optional `author` names who is asking, and takes `planner` (the
default) or `monitor`. It is not decoration: the engine records it on the
`edit-committed` event, and for a monitor edit it also queues a non-blocking
`monitor-edit` surface naming the command — so a fix the
[monitor](#the-agent-graphs-a-run-launches) applies is reported as the monitor's
without the monitor also having to report it.

`finding` is the exception, and deliberately so: it raises the finding's own
surface and **no** `monitor-edit` surface beside it, because there is no edit to
report — the surface *is* the report, and queueing a second one would double every
observation in the one line a planner may not filter.

It is also a bound. `author: monitor` may issue exactly `add`, `retry`, `cancel`,
`requeue`, and `finding`; the engine refuses the rest by name and says
why, and refuses a completion verdict from a monitor the same way:

```
onepipeline: refused: 'drop' is not an op the monitor may issue: removing work from
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
and enforced by the engine, and
`tests/e2e/test_orchestrate_launch_e2e.py` holds both halves — that the four
refusals are refusals, and that an in-allowlist op is judged on the graph's state
rather than on who asked.

A command-only envelope gets a synthesized continuing verdict. Commands can
instead accompany either legacy verdict, for example:

```json
{"completion":false,"message":"apply the replacement and continue","reason":"the failed node is retryable","version":2,"commands":[{"op":"retry","id":"failed","node":{"id":"retry","task":"No diff","expects_no_diff":true}}]}
```

`complete` is the versioned equivalent of a completion verdict and may share an
envelope with graph edits:

```json
{"version":2,"commands":[{"op":"complete","reason":"publication and follow-up triage verified"}]}
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

**Every edit is applied or rejected, and `channel-reply` reports which.** It
validates each edit against the graph projected from `events.jsonl` — through the
reconciler's own validator, so the answer is the one the reconciler would give —
and exits non-zero with the reason when it cannot be applied, before anything is
queued or sent. There is no round for an edit to be inside of: the desired graph is
what an edit changes, and it outlives any one dispatch. That is why an `add` is
still accepted against a run whose driver has exited, while a *verdict* to a
settled run is refused — nothing will read the verdict, but the graph is still
there to be edited and an adopted driver will act on it.

Accepted edits are appended to the durable channel queue and drained from there by
the reconciler, which answers each claimed command; `channel-reply` waits for that
verdict before it exits:

| Exit | Meaning | stdout |
| --- | --- | --- |
| 0 | every edit in the envelope was applied by the reconciler | `{"reply":N,"state":"applied"}` |
| 1 | the edits were accepted and durable but not reconciled in time; they remain queued — check `just monitor` rather than resubmitting | the reply's state |
| 2 | the reply was malformed, or an edit was refused at submission, or the reconciler rejected it | the reason, on stderr |

An edit that passes submission can still lose a race to the frontier it was
validated against — the log a submitter reads lags the live frontier — and that
case is a synchronous rejection to the caller that issued it, not a proposal to
be noticed later. Every rejection is also surfaced as a `reconciler: rejected ...`
proposal and recorded as an `edit-rejected` event carrying the command and the
reason. No accepted command is silently dropped.

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
{"version":2,"commands":[{"op":"requeue","id":"sweep","amend":{"max_turns":32}}]}
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

It is a **composition of two published verbs**, not one: `oneagentgraph sweep` for
the scratch a dispatch leaves behind, and `onevcs sweep` for the publication and
recovery workspaces a lifecycle leaves behind. `--dry-run` and `--min-age-hours`
reach both and mean one thing across every family. Neither verb's report is
rewritten — reformatting another repository's report here would make this listing
drift from the one that repository prints everywhere else — so what the recipe adds
is the trailer below.

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
verbs default to.** That is a choice this composition makes and states in its own
`--help`, and the reasoning is in `scripts/sweep.sh` beside the number so that moving
it is an argument with the measurement rather than with taste. In short: at
twenty-four the composed sweep reclaimed 0 B on this host while `--min-age-hours 4`
reclaimed 23.9 GB, because a host running several dispatches churns publication
workspaces and dispatch scratch hourly and almost nothing provably dead is ever a day
old. A floor that never fires is how a device reaches 100% with 2 MB free while every
sweep reports success. Lowering it weakens no proof — neither verb removes anything
on age alone, and `onevcs` still keeps its bounded recovery history — and the value
has to be a whole number of hours, because `oneagentgraph sweep` refuses a fractional
one where `onevcs sweep` takes it. It is *not* GNU `find`'s `-mtime` truncation,
which is the obvious suspicion and is ruled out by measurement: `-mtime +1` means "at
least two days" and skips the whole 24-48h band, but a directory 30 hours old is
reclaimed by each verb at `--min-age-hours 24`, and `oneagentgraph` quotes the floor
it applied in seconds. The floor was long, not truncated.

What that covers is narrower than it once was, and the difference is operational.
`oneagentgraph sweep` examines the two families it owns, `runs` and `temp`. The
families it does not — a private `nx` install per `bunx nx` invocation, a run directory
per pytest session, and onejudge's own scratch — are the *volume* ones, appearing
because dispatches are running, and nothing reclaims them now. One producer has since
left that list rather than been reclaimed from it: Nx copied its native binary once per
workspace root, which on a host where every dispatch works in a fresh worktree meant
another 22 MB directory per dispatch, and `scripts/nx.sh` now keys that copy on the
repository identity so there is one per origin instead of one per root. They share one root, `$TMPDIR` or `/tmp`, and
the trailer measures and names it for exactly that reason. Neither does anything reclaim the
**processes** a finished dispatch left running, which reparenting to init puts
outside every tree walk; the engines that start them own keeping them alive and
reaping them. `just sweep` says the same at the seam an operator touches.

Every sweep names the families it examined and, separately, the families it could
not. Each family appears in exactly one of the two lists, so a sweep that reclaimed
nothing always means "nothing was reclaimable", never "a family was never looked
at". A cleanup run that silently skips the family filling the disk reads as a clean
bill of health, which is worse than no cleanup at all.

**That is why the sections are rationed.** A sweep that examined every family and
left nothing to act on prints a short form naming the families it judged, how many
candidates it looked at, and how many it took, and prints no sections at all:

```text
just sweep: reclaimed 1 of 2 candidate(s) examined — every family examined: oneagentgraph runs, temp; onevcs publications, recoveries; preserved unpublished branches are examined by no sweep — just recoverable names them and the verb that lands each one; free space (df -h) is what says this host has room, not this line.
```

**Two of its readings look alike and are opposite pieces of news, so they are two
different sentences.** A sweep that reclaimed nothing because every candidate it judged
was live or within retention is the sweep working; a sweep that reclaimed nothing
because there was nothing to judge has said almost nothing about this host. Read as one
`Reclaimed: none` they are indistinguishable, which is how a full disk comes to read as
a clean bill of health:

```text
just sweep: nothing reclaimed — 3 candidate(s) examined across every family (oneagentgraph runs, temp; onevcs publications, recoveries), all live or within retention; preserved unpublished branches are examined by no sweep — just recoverable names them and the verb that lands each one; free space (df -h) is what says this host has room, not this line.
just sweep: nothing reclaimed — no candidate was examined; every family (oneagentgraph runs, temp; onevcs publications, recoveries) was empty; preserved unpublished branches are examined by no sweep — just recoverable names them and the verb that lands each one; free space (df -h) is what says this host has room, not this line.
```

**And no figure any of them prints is what says this host has room** — which is why
every verdict ends by saying so, as part of the one line rather than as a paragraph
under it. A sweep takes only what it can *prove* dead, in the families named beside the
number and in no others. The long form says the same at length, where a reader arrived
because something is wrong and the `Reclaimed:` line is in front of them.
`tests/e2e/test_sweep_e2e.py` drives both of the readings above against the real recipe
and asserts they differ.

A report this recipe cannot read those counts out of — a release that reworded one of
the lines they come from — prints the sections rather than guessing between the two
sentences, which is the conflation they exist to end. Both verbs' reports and the
trailer otherwise appear when — and only when — a verb failed or a family went
unexamined, which are the two states an operator has to do something about. Four sections of retentions on a host where everything was judged teach a
reader to skim, and what they learn to skim past is the trailer that names the family
nothing looked at. Rationing them is what keeps that trailer worth reading.
`--dry-run` is the exception and always prints them: it removes nothing and is asked
in order to be answered, so those reports are its return value rather than narration
of work it did. Neither verb's report is edited either way — being held back is not
being rewritten.

`just sweep`'s trailer is that invariant held across the two verbs rather than
inside one, and three cases are worth reading rather than inferring:

- **A verb that fails takes only its own families with it.** They move into the
  not-examined list with the exit status that produced them, the other verb still
  sweeps and still reclaims, and the recipe exits non-zero so the gap is in the
  status as well as in the report. Silently absent from both lists is the one
  outcome the trailer exists to rule out.
- **The pre-adoption `~/.ai-orchestrator/worktrees` root is reported, never
  reclaimed — and what each directory under it *is* is read rather than asserted.**
  The trailer used to retain the whole root on one sentence: that every directory
  under it was a registered git worktree, still listed by the checkout that lent it,
  still able to hold a branch nothing had published. On this host that was false —
  the lenders register no worktrees and the directories below it are not git
  repositories at all — and a retention reason nothing checks is worse than no
  reason, because it reads as evidence. So the reading is now asked of git, per
  directory, and each one is reported as whatever it turns out to be: **a registered
  worktree of its lender**, on the branch it holds and naming that lender — land or
  discard the branch first (`just recoverable` names the verb for it), then remove
  the tree with `git worktree remove` there; **a git repository of its own**, whose
  commits are its own to publish or copy out; **a submodule of another repository**,
  naming the superproject whose object store holds it and whose `git submodule deinit`
  owns it rather than an `rm` here; **a working tree carrying a `.git` git
  cannot read**, which reaches no history and so can be published from by nothing —
  deliberately not called stranded from a lender, because a deleted lender and a corrupt
  repository leave that alike and the read cannot tell them apart; **not a git working
  tree at all**, which is leftover content rather than work; or **unreadable**, which is
  unclassified rather than empty. The owner of each class present is printed under the listing, so
  no directory is named without one. The listing is ordered by what the reading found
  rather than by name — a directory that can still hold unpublished work first,
  leftover content last — because past eight directories the tail is folded into a
  tally by class, and the tail is where the fold should fall. A root that cannot be
  walked or measured is still named, without the number it could not get and with
  every directory under it left unclassified rather than guessed at, and a root
  holding nothing at all is not a family and says nothing.
- **The host scratch root is reported, never reclaimed, and it is the one that
  filled this disk.** `$TMPDIR` — `/tmp` unless something set it — is where
  `oneagentgraph` writes the family it owns and reaches nothing else, and where
  `onevcs` writes nothing at all, so every other directory under it is neither
  verb's. It reached 139 GB of a 169 GB device, taking `/` to 2 MB free and stopping
  every dispatch on this host, while every sweep that day reported success. The
  trailer names it with its size, its entry count, and its largest three name groups
  with each trailing id folded into the name in front of it — because the producer
  that filled it was 3,646 directories of one `nx` cache, which reads as a long tail
  of unrelated small ones in any per-directory listing. **Each group also says how
  recently anything in it was written**, which is the one thing a size cannot say and
  the thing that decides whether an operator should care: the residue of a leak that
  was fixed weeks ago and the cache filling this root right now are the same number of
  bytes, and only one of them is news. It is the newest top-level entry in the group,
  read from a second walk under the same exclusions the count applies; a group the
  size walk listed that the timestamp walk did not reach says its recency is
  unreadable rather than reading as never written, which would make it the oldest
  thing on the root and the first thing somebody cleared. The count is of entries
  rather than of directories because that is what fills a device and what the
  reclamation was accounted in — 11,127 of 60,208 entries, for 48 GB. Exactly two
  things are left out of it, and both are named: what `oneagentgraph` prefixed, since
  a verb above examined it and nothing may be counted in two families at once, and
  the lock `uv` takes in this root on the way to each verb, which is the recipe's own
  and would otherwise make this family non-empty on every host that has ever swept —
  taking the one-line form with it. A loose file is in both numbers, though `du`
  lists no file for it to be a name group. Nothing here removes any of it:
  promoting the root to a *reclaimed* family means implementing the proof both verbs
  already have, which is their work and not this wrapper's. A root that cannot be
  walked is still named, without the number it could not get, and a root only partly
  readable reports its size as a floor rather than as a total.
- **The preserved unpublished branches are named as a family of their own**, with
  `just recoverable` as the verb that answers them. They are not directories, so no
  sweep examines them and none can: judging one means deciding whether its work should
  land, which is not a proof either verb can make. They are named because they are
  what *holds* the workspaces above from being reclaimed — a sweep's retentions are
  this family's shadow, and until it was named an operator read the shadow with no way
  to see what cast it. It is named in the one-line verdicts as well as in the trailer,
  since the one line is what a sweep usually prints; it is deliberately **not**
  counted, because `onevcs recoverable` is the only thing that can take that count and
  it asks every registered identity, costing more than the whole sweep around it. And
  it deliberately does **not** decide whether the sections print: it is unexamined on
  every host and at every moment, so letting it decide would make the long form
  unconditional and take the short form away from every sweep there was nothing to act
  on.
- **Why those three are the composition's and not either sweeper's.** The obvious
  alternative is to teach `oneagentgraph sweep` or `onevcs sweep` to reclaim them.
  Both verbs judge a candidate on proven non-reference and each owns the directories
  it wrote; these three are accumulations of tools neither of them wrote — a build
  cache and a package store under the host scratch root, the leftovers of a layout
  this host used before either verb existed, and branches, which are not directories
  at all. Handing one to a sweeper means giving it a proof it cannot make: nothing
  tells it what a foreign tool's directory is for, and whether a preserved branch
  should land is a judgement about work rather than about liveness. That
  provable-or-nothing property is what makes those verbs safe to run unattended, so
  the answer is to name the owner rather than to widen the sweeper — and the owner is
  named in the composing report, which is `scripts/sweep.sh`.
- **The trailer restates each verb's family names** rather than pointing back at a
  report an operator has to scroll through. That restatement is held against what
  the installed verbs report examining, in `tests/e2e/test_sweep_e2e.py`, so a
  release that renames a family or adds one fails there rather than leaving the
  trailer quietly describing the previous release.

The ledger is **flat**, because a run has no phases to number:

```text
runs/<run-id>/plan.json          the plan as launched
runs/<run-id>/events.jsonl       the authoritative journal
runs/<run-id>/result.json        the settlement, rewritten as it moves
runs/<run-id>/launch.json        who launched it, with what, and how often adopted
runs/<run-id>/reports/           one report per dispatched agent-graph turn
runs/<run-id>/channel/           queue.json, surfaces.jsonl, replies.jsonl
```

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
`channel-reply` validates every live edit against this reader, so treating a
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
protection re-attributed a launched run away from its launcher's
`ORCHESTRATOR_AGENT_STATUS_DIR` stamp, because the sweep read that stamp as proof of
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

## Monitoring a live run

`just runs` says how far each run has got and `just history-show` says everything
about one thing in it. `just monitor` answers the question in between — "what is
happening right now, across the whole run?" It is the standard first view for
every recorded in-flight dispatch, from a wide DAG down to a one-node plan — the
one executor means there is no dispatch it cannot see. It folds four stores that
settle at different times into one ordered stream:

| Source | Read from | Reported when |
| --- | --- | --- |
| Run journal | `runs/<run-id>/events.jsonl` | every node transition, as it is appended |
| oneharness history | sessions whose `run_id` label names this run | a session's status or turn count moves |
| Git | commits on each known lifecycle branch | once per commit, ever |
| GitHub | each lifecycle-linked PR | its state or any check changes |

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
is the ownership registry the scratch sweep already trusts: the
`ORCHESTRATOR_AGENT_STATUS_DIR` stamp the kernel fixes into the environment of
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

Both flags are *positive* claims and are made only where they can be proven. The
node-level `UNDRIVEN` needs two things beyond "the registry saw nothing for this node":
the registry must have seen at least one live dispatch, which shows this reader is
looking at the scratch root the dispatchers write into, and the run's launch must be
observably working, which distinguishes one node losing its dispatch from the whole
run stopping — the second is already reported one level up by
the liveness verdict. Every other uncertainty resolves toward "still working",
and a view that can observe nothing reports exactly what it reported before any of
this existed.

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
for the DAG UI and any other live viewer. It is read-only in the same sense the
monitor is — no route mutates a run, executes a command, or accepts a path — so
it is safe to leave running beside an active orchestration. Its flags, response
shapes, and event vocabulary are fixed by
[`dag-ui/design.md`](dag-ui/design.md#running-it).

## Human completion attestations

After doing a reported action, attest it explicitly, over the live channel:

```sh
just channel-reply RUN <<'JSON'
{"version":2,"commands":[{"op":"attest","ref":"HUMAN_ID"},{"op":"attest","ref":"NODE_ID/STEP_ID"}]}
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

A **cross-DAG reference is not a dependency the graph can satisfy**:
`run:<id>#<node>` names no node of this graph, so nothing here ever removes it. It
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
declaration as it stands in onepipeline 0.22.2 rather than restating it independently.

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
note again. `just channel-reply` merges the recorded word into its own answer as
`reached`, per note it sent.

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
- **`oneagentgraph`** — one dispatched agent turn and the graph of members a run
  drives, its history records, and its scratch.
- **`onevcs`** — repository identity, the rules that resolve a policy from it,
  sessions over isolated worktrees, publication, recovery, and integration.

Their contracts are documented in their own repositories; this page holds the
judgment and the operating protocol a planner works this host through.
