<!-- llmlint: ignore-file[contracts_have_one_source_or_a_drift_gate] Operator-facing wire examples are required here; the published `onepipeline` channel remains authoritative and its exact contract is exercised by that crate's own tests. -->
<!-- llmlint: ignore-file[no_redundant_instruction_pointers] Live cancellation can preserve incomplete commits; the recovery command must link its safety contract at the point of use. -->

# Tracked graph orchestration

`just orchestrate <plan.json>` turns one large task into one recorded hierarchical
DAG. It is the **only** way to dispatch direct agent work, full repository
lifecycles, and explicit actions that only a person can complete: a single dispatch
is a plan file holding one node, so every piece of running work has a journal, an
ownership row, surfaces, and a place in the DAG UI. The engine drives that DAG
**continuously to settlement** — it schedules, dispatches, reconciles, and settles
on its own, with no verb to advance it and nothing to advance between. See
`examples/single-node-direct.plan.json` and
`examples/single-node-lifecycle.plan.json`.

## The manager and the planner

The top-level session agent is the **manager**: it holds the user conversation,
launches runs, reviews what settles, and answers surfaces. The **planner** is not
that session. It is a dispatched onejudge worker like every other node — supervised
by its own simulated-user judge, costing turns, settling on the ledger — whose
deliverable is a plan file, and `just plan <brief.md>` is the one-node launch that
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
adopted `onepipeline` 0.15.1 also still reads 2 and 1, so an older plan file an
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
  what a live `context` edit attaches. Where it is delivered to the node's *next*
  dispatch it carries exactly that one; where the edit's `deliver` mode puts it into
  a running turn instead, the turn has read it and nothing is owed forward. See
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
  against onepipeline 0.15.1 by dumping both sides of a monitor member's whole
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

## The planner channel

`just orchestrate <plan.json>` starts the run, prints its run id, and then **stays
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
`scripts/channel-serve.py` raises `monitor`, `monitor-failed`, `monitor-transcript`
and `monitor-completion`, and how the engine raises `monitor-edit` and
`edit-rejected`.

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
just orchestrate plan.json
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
| onejudge 0.5.3 writes to a judge command | `{"op": "supervisor", "task", "persona", "done_when", "worktree", "history_name", "messages": [...], "session"}` |
| `onepipeline channel serve` reads | `{"kind", "message", "blocking"?, "node"?}` |

Naming `onepipeline channel serve` directly as the member's `judge.command` is
therefore refused on the first turn — `the observer emitted a bad frame: unknown
field 'op'` — and onejudge kills the member with `provider produced no output`,
leaving the run driven but unwatched. The filter recovers the two values a surface
needs, both out of the frame itself:

- **the run id**, from the composed task's opening ``onepipeline run `<id>```. The
  environment carries it too — `ONEPIPELINE_RUN_ID` is set to the run id there,
  measured against onepipeline 0.15.1 in the judge command's own environment on a real
  launch, and re-taken on every gate run by
  `tests/e2e/test_orchestrate_launch_e2e.py`. The filter reads the frame it already
  validates instead, because that is a contract rather than a per-release export;
- **the surface message**, from the last thing the monitor said — unless the turn
  failed, in which case that is not something the monitor said at all.

A turn the agent side lost writes the harness's own machine transcript into that
message: measured off this host's `runs/rc-fixes-brief` channel, fifteen JSON-RPC
frames and 21,531 characters, most of it the prompt echoed back, ending in a
`method: error` frame and a `turn/completed` whose `status` is `failed`. Twenty of
them queued unread on one run. The planner may not filter the unread-surface line —
a blocking surface produces no other signal until it is read — so raising one of
those verbatim is unreadable and undroppable at once. The filter recognises a lost
turn and raises it as a named failure instead, under its own kind:

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

**A transcript no failure can be proven inside is bounded all the same**, and it is by
far the commoner shape. Measured on 2026-08-24: 26 oversized surfaces on this host, every
one `status: completed` with `error: null`, so nothing was provable about any of them and
all 26 were raised as the monitor's own words — 176.1 MB of protocol carrying zero
model-authored characters, a 3.6 GB journal holding one 699 MB event line, and a
read-only `just runs` that needed 5.7 GB of RSS. So the bound is placed on what *is*
provable: what tells a transcript from prose is that every non-blank line of it parses as
a JSON object, which is the same test with no harness's vocabulary in it, and both
answers it identifies are bounded:

| | |
| --- | --- |
| kind | `monitor-transcript` |
| message | ``monitor answered with a machine transcript rather than an observation: 4787 characters from codex, and nothing in it says the turn failed. It is not repeated here — read it with `just monitor rc-fixes-brief --filter monitor`.`` |

It claims nothing about *why*, because nothing here proves a failure and a guessed cause
is worse than none. What it does name is the three things that keep a withheld
observation one command away rather than lost: whose output it was, how much of it
arrived, and where the full text is read. There is no size floor and no `jsonrpc` or
`method` check — a 300-character transcript published as the monitor's prose is the same
category error as a 1.87 MB one, just cheaper, and enumerating one known protocol is
exactly what the next harness's shape would outrun. The cost is accepted: a monitor
answer that genuinely consists only of JSON object lines is bounded too, and is readable
at the command the line names.

**An observation written as prose is untouched** and still raised verbatim under
`monitor`, including one that quotes protocol frames inside it — a prose line does not
parse as a JSON object. `tests/e2e/test_lost_turn_wire_contract_e2e.py` drives both
bounded surfaces onto a real published channel with the real `codex` transcript behind
them, and reads each back out of `runs/<run-id>/channel/queue.json` where a manager reads
one.

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

#### A monitor that found nothing, and a monitor that said nothing

These are two different turns and the filter used to have one answer for both, which
cost this host its whole supervisory tier for hours at a time.
[`personas/orchestrator.yaml`](../personas/orchestrator.yaml) tells the monitor to spend
no planner surface on a turn with no finding in it — a queue of throat-clearing buries
the blocking questions sharing it — and until the filter learned the difference, obeying
that instruction was fatal on the **first** quiet turn, which for a healthy run is
usually the first turn. A frame carrying no assistant content was refused as a protocol
failure, `oneagentgraph` recorded `member-died
{"rule":"provider-failure","cause":"protocol"}`, and the run carried on reporting
`ACTIVE` with nothing watching it. Observed on `spanish-language-tutor-upgrade`, which
lost its observer five minutes in and ran roughly two hours that way while every other
indicator stayed green.

So a monitor **says** its silence, and there are three answers rather than two:

| The turn's last message | What happens |
| --- | --- |
| exactly `NOTHING TO REPORT` | no surface is raised and nothing is queued; onejudge is answered `{"completion": false}` with a message telling the monitor to keep watching, so the member lives |
| anything else the monitor said | raised as the surface it always was — `monitor`, `monitor-failed`, or `monitor-transcript`, per the sections above |
| no assistant content at all | still refused, naming the sentinel: an empty turn is a real provider defect and is what the sentinel exists to stop being mistaken for |

Only decoration is forgiven around the sentinel — surrounding whitespace, a full stop,
bold markers, backticks — and case with it. It is matched against the **whole** message
and never searched for inside one: a turn that raised a finding and also wrote the
sentinel has raised a finding, and swallowing it would lose the observation the member
exists to produce.

Structured output would be the heavier way to draw the same line, and one constraint
rules it out: oneharness validates a structured answer against the complete response, so
`stream = true` and `schema_file` cannot both hold, and turning streaming off for the
run's long-lived watcher would trade away the per-turn visibility a manager supervises
with.

`tests/e2e/test_monitor_quiet_turn_e2e.py` drives all three through the real filter onto
a real published channel, and reads what was and was not queued out of
`runs/<run-id>/channel/queue.json`.

#### The completion bar is scored by the planner too

onejudge asks a judge side **two** ops, not one. `supervisor` comes at each turn
boundary; `judge` comes once the conversation ends, to score `user.done_when` —
always, whether the supervisor ruled complete or the turn cap ran out, and
independently of `evals` and `assessment`. Measured on onejudge 0.5.3 with a
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
a real run on onepipeline 0.15.1 answers `{"reply":0,"state":"applied"}` and records
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
the launch, measured per shape by `tests/e2e/test_launch_ask_seam_e2e.py`: **every
node dispatch of a run carries it as of onepipeline 0.15.1**, composed where the
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
`tests/e2e/test_ask_manager_e2e.py` drives every one against a real run's channel.

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
also contain `"version":1` plus a `"commands"` array using the operations below.
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
{"version":1,"commands":[{"op":"reparent","id":"pending","deps":["slow_b"]},{"op":"drop","id":"slow_b","dependents":"detach"},{"op":"attest","ref":"approve"}]}
JSON
```

The accepted commands are:

| `op` | Required fields | Effect |
| --- | --- | --- |
| `add` | `node`: full node mapping | Add a new node. Its `deps`, if any, must name graph nodes or valid cross-DAG references. |
| `drop` | `id`; `dependents`: `"drop"` or `"detach"` | Remove the node and recursively drop its dependents, or detach its direct dependents. |
| `reparent` | `id`; `deps`: list of dependency references | Replace an unstarted node's dependencies. |
| `retry` | `id`; `node`: full replacement node mapping with a new id | Supersede a running, failed, or cancelled node with a fresh lineage and redirect its direct dependents. |
| `cancel` | `id` | Park a pending or running node: [interrupt its live turn, kill the dispatch if it has not exited by the grace period](#what-a-cancellation-does-to-a-live-dispatch), and hold the node out of the frontier until a `requeue`. |
| `requeue` | `id`; optional `amend`: partial node overrides | Return a parked node to the desired frontier, optionally amending it (for example `max_turns`, or a `resume` pin onto the preserved branch). Refused while that node's dispatch is still in flight. |
| `attest` | `ref` | Complete a currently ready, waiting human action. |
| `complete` | `reason` | Journal the planner's completion request independently of graph mutation. |
| `context` | `id`; `note` | Attach one planner note to the node's next dispatch, without cancelling or restarting anything. |
| `amend` | `id`; `text` | Replace the binding amendment that becomes part of the node's effective task for its next and later dispatches. Blank text and a node already settled `done` are refused. |
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
`requeue`, `context`, and `finding`; the engine refuses the rest by name and says
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
{"completion":false,"message":"apply the replacement and continue","reason":"the failed node is retryable","version":1,"commands":[{"op":"retry","id":"failed","node":{"id":"retry","task":"No diff","expects_no_diff":true}}]}
```

`complete` is the versioned equivalent of a completion verdict and may share an
envelope with graph edits:

```json
{"version":1,"commands":[{"op":"complete","reason":"publication and follow-up triage verified"}]}
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
and `reparent`'s to change; `attest` requires a ready waiting human action; `context`
requires a node that can still be dispatched, so a note aimed at a node that
already settled `done` is refused rather than accepted into nothing. `drop` must
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
{"version":1,"commands":[{"op":"requeue","id":"sweep","amend":{"max_turns":32}}]}
```

An `amend` mapping is merged onto the node before it is redispatched, which is where
a raised turn budget or an explicit `resume` pin onto the preserved branch goes. It is
validated as the node it produces, so a malformed pin is refused at submission rather
than at the next dispatch. Omit it to requeue the node exactly as it was parked: the
compiled `node-requeued` operation then carries no `amend` at all, so "amended
nothing" and "amended with nothing" are one record rather than two.

## Node shapes

A plan file may be JSON or YAML, and each is read with **its own** escape
semantics: a `.json` file is parsed as JSON, so a `😀` surrogate pair —
what `json.dump` writes for one emoji, and therefore what a plan generated
programmatically contains — reaches the dispatched agent as the character it
encodes. Reading it as YAML instead yields the two unpaired halves, which no
UTF-8 encoder accepts, and the node fails on its own task prose. A `.json`
document that is not a mapping is refused by name (`must be a JSON mapping, got
list`); one JSON itself cannot parse falls back to the YAML reading, so nothing
that loaded before stops loading. The same rule governs the JSON documents the run
ledger writes and reads back — the plan and result records, the launch record, and
each node's report — so a recorded emoji survives to `just history-show` and the
read API.

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
what a live `context` edit attaches, and it lasts [one
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
[`tracked-graph.example.json`](../examples/tracked-graph.example.json) for direct,
lifecycle, top-level human, and nested human nodes in one graph.
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
default and existing callers untouched. Its `task` states the contract literally —
route with request and response fields and types, the exact signature, or the
field name, type, and default — because the producer node and every consumer node
restate it from there. Its acceptance criteria are satisfiable inside its own
dispatch: the surface exists, and existing behavior is unchanged. Name it in the
`deps` of the real implementation and of each consumer; those siblings then become
ready together instead of serializing, and each ships against the default or sample
behavior until the implementation lands.

A consumer that finds a departure it wants — a missing field, a wrong shape, a
better decomposition — does not change the interface. It surfaces the ordinary
`kind: "proposal"` its dispatch already has, and the planner decides with the user
whether to amend the contract or defer it as a follow-up. Amending it is a [live
edit](#live-graph-edits), and which edit depends on what the node is doing: a node
that has not started is parked with `cancel` and returned by a `requeue` whose
`amend` restates its `task`, `retry` replaces one already running with a task stating
the new contract, and a surface that has to change again after its node settled is an
`add` with the affected consumers `reparent`ed onto it. A `context` note is **not**
that lever: it is rendered as observed state that adds no acceptance criteria, so it
tells a worker about the new contract without changing what its judge reviews
against.

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
families it does not — a private `nx` install per `bunx nx` invocation, a copy of
Nx's native binary per workspace root, a run directory per pytest session, and
onejudge's own scratch — are the *volume* ones, appearing because dispatches are
running, and nothing reclaims them now. They share one root, `$TMPDIR` or `/tmp`, and
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
left nothing to act on prints one line naming the families it judged, and prints no
sections at all:

```text
just sweep: nothing to act on — every family examined: oneagentgraph runs, temp; onevcs publications, recoveries.
```

Both verbs' reports and the trailer appear when — and only when — a verb failed or a
family went unexamined, which are the two states an operator has to do something
about. Four sections of retentions on a host where everything was judged teach a
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
  reclaimed.** It is the family that has actually filled this host's disk, and it is
  neither verb's: every directory under it is a *registered* git worktree, still
  listed by the checkout that lent it, still able to hold a branch nothing has
  published. So the trailer names it with its size and its directory count and stops
  — land or discard that branch first (`just recoverable` names the verb for it),
  then remove the tree with `git worktree remove` in the lender. A root that cannot
  be walked or measured is still named, without the number it could not get.
- **The host scratch root is reported, never reclaimed, and it is the one that
  filled this disk.** `$TMPDIR` — `/tmp` unless something set it — is where
  `oneagentgraph` writes the family it owns and reaches nothing else, and where
  `onevcs` writes nothing at all, so every other directory under it is neither
  verb's. It reached 139 GB of a 169 GB device, taking `/` to 2 MB free and stopping
  every dispatch on this host, while every sweep that day reported success. The
  trailer names it with its size, its entry count, and its largest three name groups
  with each trailing id folded into the name in front of it — because the producer
  that filled it was 3,646 directories of one `nx` cache, which reads as a long tail
  of unrelated small ones in any per-directory listing. The count is of entries
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
a name-shaped guess. The view opens repositories to read and writes nothing, so it is
safe beside live dispatches.

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
{"version":1,"commands":[{"op":"attest","ref":"HUMAN_ID"},{"op":"attest","ref":"NODE_ID/STEP_ID"}]}
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

A `context` edit is where that knowledge goes. The note is set as the node's
`context` on the running graph and rendered as a `## Planner context` section of
the task the node dispatches. Because the reconciler installs the edited graph
immediately, it is already visible to a dispatch of that node which has not started
yet — including a `retry` replacement submitted in the same envelope, which the
compiled operation records as `delivery: immediate`.

**A note also reaches a dispatch that is already running.** That is the whole point
of the edit when a run is going wrong, and it is easy to miss because the mechanism
is a third field rather than a different op. `context` takes `deliver` beside `id`
and `note`, and omitting it means `auto`:

| `deliver` | where the note goes | recorded `delivery` |
| --- | --- | --- |
| `auto` (the default) | the node's running turn where it has a controllable one, its next dispatch where it does not | `live` or `deferred` |
| `live` | the running turn, or the edit is **refused** naming why it could not be | `live` |
| `next` | the next dispatch, and only there | `deferred` |

Live delivery is `oneagentgraph interrupt` against **the dispatch's own control
socket**, so it reaches a node only once something of that dispatch has reported a
member; before then there is no turn to address and `auto` falls through to the next
dispatch. The three modes and the two endings above are read from onepipeline 0.15.1,
where `Deliver` is still `auto` / `live` / `next` and `Delivery` still `live` /
`deferred`. That they *work* was measured on a live run under an earlier release and
has not been re-taken since: a note sent to a worker three hours into its dispatch
recorded `"delivery":"live"`, and the worker changed what it was doing in its next
turn. Read the vocabulary as current and the anecdote as the observation it is. A delivery that was *attempted
and broke* is neither ending and is refused under every mode, `auto` included — being
told `deferred` when the truth is that the lever failed is being told something untrue.

So `live` is the mode for a correction that cannot wait, and its refusal is the
feature: a planner who needs the worker to change course now is told plainly when that
did not happen, rather than discovering later that the note sat waiting for a dispatch
that never came.

**A deferred note lasts one dispatch.** It is consumed when it is delivered, not
carried until something removes it, which is the whole reason the field is one string
rather than a list. A note reports state observed while one attempt was running, so
it is stale the moment the next attempt moves; a note that still matters is one the
planner or the monitor attaches again against what the run now shows. That is what
stops a node accumulating instructions nobody re-read.

**A note delivered `live` is not re-owed to the next dispatch.** The running turn has
already read it, so carrying it forward would repeat a correction the worker has acted
on — which is why `delivery` is recorded on `edit-committed` rather than inferred, and
why replay can tell the two cases apart.

Two more things do not carry, for the same reason they never did. Context follows a
**node id**, so a `retry` replacement — a new id — starts with none, and a note
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
