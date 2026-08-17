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
adopted `onepipeline` 0.7.1 also still reads 2 and 1, so an older plan file an
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
  what a live `context` edit attaches, and it carries exactly one dispatch. See
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
  because no agent is required to run a plan. `just orchestrate` names this file
  so every run on this host gets a watcher, and passes a caller's own
  `--dag-graph` — including `off` — through untouched. There is no environment
  variable for it; the flag is the only way to move it.

  The document declares **schema 4**, for two fields. `check-in` carries its own
  `task`, which needs 3; that task opens with `{task}`, which `oneagentgraph`
  expands only from 4. `onepipeline` composes one task for this graph — it states
  what the run *is*, its id and its goal — and hands it to every member which does
  not claim one. A member's own `task` **replaces** it, so a member that claims one
  must interpolate it back in to learn which run it is on. That is not optional
  here: `onepipeline` exports **no** environment variable naming the run to an
  observer member, so a task written against `$ONEPIPELINE_RUN_ID` reads empty on an
  operator's launch — and, inside a dispatch that exports one for its *own* run,
  silently reports on the enclosing run instead. Give a scheduled member its own
  task whenever its job is not the run-level task, open it with `{task}`, and see
  [the pacemaker](#the-planner-update-pacemaker) for why this one is scoped away
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
onepipeline surface RUN --kind check-in --message "Node X failed its gate; retry with a corrected fixture?"
```

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
| onejudge 0.4.0 writes to a judge command | `{"op": "supervisor", "task", "persona", "done_when", "worktree", "history_name", "messages": [...], "session"}` |
| `onepipeline channel serve` reads | `{"kind", "message", "blocking"?, "node"?}` |

Naming `onepipeline channel serve` directly as the member's `judge.command` is
therefore refused on the first turn — `the observer emitted a bad frame: unknown
field 'op'` — and onejudge kills the member with `provider produced no output`,
leaving the run driven but unwatched. The filter recovers the two values the frame
carries and the environment does not:

- **the run id**, from the composed task's opening ``onepipeline run `<id>```,
  because `onepipeline` exports no variable naming the run to an observer member;
- **the surface message**, from the last thing the monitor said.

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

### A dispatched agent asks its manager

The same `channel serve` verb is the other end of the channel: how a worker that
has reached a decision fork stops and asks rather than guessing. `just plan` exports
the path of `scripts/ask-manager.sh` into the launch environment as
`ORCHESTRATOR_ASK_MANAGER`, and that wrapper is the one supported way to ask. It
takes the question as an argument, as `--file <path>`, or on stdin, blocks, and
prints the manager's answer on stdout. Only that recipe exports the variable, so a
worker dispatched by any other launch has no path to name and asks the channel
itself — which is why the fallback is stated in the persona rather than here.
*When* a fork is worth blocking on is the dispatched role's judgment rather than
this page's; both are in [`personas/planner.yaml`](../personas/planner.yaml).

The frame it writes is the shape the table above gives `channel serve`: kind
`planner-question`, and **`"blocking": true`**, which is what holds the run at
`awaiting-planner` until an answer arrives. It is one compact line, because a
pretty-printed frame is refused as a parse error at line 1 column 1, a message
naming the symptom and not the cause. `ONEPIPELINE_RUN_ID` names the run to
ask on: every dispatch is started with its own, an observer member has none, and an
unset one is refused rather than guessed at. `ORCHESTRATOR_ASK_MANAGER_NODE`
optionally attaches the surface to a node, and a node the run does not have is a
fatal refusal rather than a retry.

The manager answers it as an ordinary blocking surface — `just channel-next RUN`
hands it over, `just channel-reply RUN` answers — with one requirement the surface
states in its own text: **the reply's `message` must repeat the correlation token
verbatim.** A reply here is [claimed by whichever reader reaches it
next](#a-planner-writes-a-reply-once), so a ruling that does not echo the token is
somebody else's answer arriving at this call, and the question is asked again rather
than answered by it. Nothing else is handed back either: a reply that is not a
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
happened. Both views therefore add one line per affected run naming how many
surfaces are queued, how stale the oldest one is, and the command that reads them:

```
* harness-fixes-cont  [mine]  1/4 done  ACTIVE
    1 planner update waiting, unread for 3h; read it with: just channel-next harness-fixes-cont
```

The queue is `channel/queue.json` beside the durable `channel/surfaces.jsonl`; a
surface that has been consumed and is awaiting an answer is not part of it, and is
reported by the wait above instead. A run reported `DRIVER DEAD` or `PARKED` keeps
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

#### Who issued an edit, and what that bounds

The envelope's optional `author` names who is asking, and takes `planner` (the
default) or `monitor`. It is not decoration: the engine records it on the
`edit-committed` event, and for a monitor edit it also queues a non-blocking
`monitor-edit` surface naming the command — so a fix the
[monitor](#the-agent-graphs-a-run-launches) applies is reported as the monitor's
without the monitor also having to report it.

It is also a bound. `author: monitor` may issue exactly `add`, `retry`, `cancel`,
`requeue`, and `context`; the engine refuses the rest by name and says why, and
refuses a completion verdict from a monitor the same way:

```
onepipeline: refused: 'drop' is not an op the monitor may issue: removing work from
the graph is a decomposition decision the planner owns. Surface it to the planner
instead
```

`drop` and `reparent` are decomposition decisions, `attest` belongs to the person
who took the action, and whether the run is finished is the planner's verdict
rather than an observation. Each refusal names the available action, because the
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
naming the branch it lives on. When the pin and the resume agree but the
preserved work can no longer be resumed — it stopped being unattested-incomplete
because a recovery or an attestation landed on it, its branch is gone, or its
checkpoint is not in the repository — the node settles `resume-failed` on the
pinned branch with that reason, rather than moving to a freshly generated branch.
Which branch a retry produces is a function of the envelope alone; resubmit with
neither a `branch` pin nor a `resume` to start the work fresh. This is why an
accepted `retry` cannot quietly re-derive the work somewhere else: the reconciler
either honours the continuation the planner named or the dispatch says why not.

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
working correctly when it refuses completion, so the worker is parked and
re-asked until the turn cap — spending a full attempt, often several, to produce
a generic `not-completed` that names neither the criterion nor the cause. Before
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
edit](#live-graph-edits): a `context` note carries the amendment to a node that can
still be dispatched, `retry` replaces one already running with a task stating the
new contract, and a surface that has to change again after its node settled is an
`add` with the affected consumers `reparent`ed onto it.

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

The conservative scratch sweep exposed as `just sweep-scratch` reclaims what a
finished dispatch left behind. A directory its ownership proof does not clear is
reported as retained rather than removed, and scratch that is only stale is
eligible after the conservative age threshold alone. Use `just sweep-scratch
--dry-run` to inspect candidates without removing any of them.

Scratch a dispatch itself produces cannot wait for quiescence, so what replaces
quiescence is proven non-reference: a candidate a live process names — in its argv,
environment, working directory, root or executable links, open descriptors, or
file-backed memory mappings — is retained and reported. A procfs that cannot answer
the question at all is not the same as one answering "nothing is referenced": those
families are then left alone and the run reports that it could not prove them
unused. A short minimum age covers only the gap between creating a directory and the
first instant a process names it, and `--min-age-hours` governs the scratch that has
no such proof behind it.

What that covers is narrower than it once was, and the difference is operational.
`oneagentgraph sweep` examines the two families it owns, `runs` and `temp`. The
families it does not — a private `nx` install per `bunx nx` invocation, a copy of
Nx's native binary per workspace root, a run directory per pytest session, and
onejudge's own scratch — are the *volume* ones, appearing because dispatches are
running, and nothing reclaims them now. Neither does anything reclaim the
**processes** a finished dispatch left running, which reparenting to init puts
outside every tree walk; the engines that start them own keeping them alive and
reaping them. `just sweep-scratch` says the same at the seam an operator touches.

Every sweep names the families it examined and, separately, the families it could
not. Each family appears in exactly one of the two lists, so a sweep that reclaimed
nothing always means "nothing was reclaimable", never "a family was never looked
at". A cleanup run that silently skips the family filling the disk reads as a clean
bill of health, which is worse than no cleanup at all.

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

Each recorded schema-v4 node result carries its own `artifacts` paths. Lifecycle
step payloads carry their step-specific raw onejudge report and stable
oneharness-session pointer. Local gate failures surface from `git push`; the
lifecycle records `gate-failed` when hook output identifies the gate and otherwise
records a self-describing `error` outcome with Git's diagnostic.
Remote-first failures remain named required-check outcomes. Each lifecycle node
also records one `merge-gate-coverage` event before it dispatches, naming the
`pre-push` hook and required checks that will verify it, so a late rejection can
be read against what was expected to run. Each gated push then records a
`verification-finished` event with its verdict and a bounded `output_tail`, and
appends the whole run to the node's `artifacts.gate_log` — for a green
publication as much as a rejected one, so a settled node can show what its gate
did rather than only that nothing objected.

That log holds every record the merge path produced, in order, including the
publications that never reached a gate at all: a rebuild whose base was advanced
under it, or that could not fetch, build its worktree, or write. Those settle
with a `publication-failed` event carrying the same bounded `output_tail` — for a
lost base race, one line per attempt naming the sha it verified against and the
sha it then observed — and the result's detail names the log. Before that, such a
failure recorded neither, which is how a run could settle seconds after a green
gate with no evidence of what went wrong. Because artifact
paths are in the terminal `GraphResultItem`, crash projection retains them
byte-for-byte.

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
die, and only for a classified provider refusal: an attempt that ran and simply did
its job badly is a different failure. The check-in pacemaker's is deferred to its
next interval by its own lease instead.

Each attempt takes a conversation of its own (`…#relaunchN`), for the same reason a
lifecycle relaunch does — see
[repo-lifecycle.md](repo-lifecycle.md#a-death-that-left-no-work-is-not-charged-to-the-work-budget).
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
- **A preserved branch is continued from where it stopped.** Which failures leave
  one is not a judgement anything makes at a boundary: every settlement that
  preserves committed work records the continuation, keyed on the outcome domain —
  see [Preserved committed work implies a recorded
  continuation](repo-lifecycle.md#preserved-committed-work-implies-a-recorded-continuation).
  A gate rejection and a refused publication are both continued on their branch
  with no planner edit.

A **cross-DAG reference is not a dependency the graph can satisfy**:
`run:<id>#<node>` names no node of this graph, so nothing here ever removes it. It
stays on its node and, when a `drop` carries that consumer out, passes to whatever
still depends on it — the same pass-through the publication anchor gets, for the
same reason. A watch its own consumer's completion silently ended would stop
reporting `upstream-modified` and stop blocking on an upstream that became
unresolvable, which is exactly what the reference is for. A consumer with no
dependents leaves nothing to carry the watch, and it ends there.

An automatic continuation of a failed or cancelled lifecycle node is **bounded**:
the count is kept on the node's `resume.attempts` and, once spent, the failing
result stands for the planner and the branch stays recoverable with `just
repo-recover`. An explicit `retry` edit clears the count, so the bound only ever
stops the harness repeating itself — never a decision the planner made after
reading the result.

A **parked** node is the deliberate exception, and the distinction is worth holding
onto: `cancelled` is a stop the engine took, so continuing it is the harness
finishing what it started and it spends that budget. `parked` is a stop the
*planner* or the monitor took with `cancel`, so nothing redispatches it and it
spends nothing. It keeps its checkpoint regardless, because that preserved branch
is exactly what a later [`requeue`](#parking-a-node-and-picking-it-up-again) has to
pick up rather than cutting a fresh one beside it.

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
compiled operation records as `delivery: immediate`. A dispatch already running
does not re-read its prompt, so a note aimed at one is `deferred` and reaches the
node's next dispatch instead.

**A note lasts one dispatch.** It is consumed when it is delivered, not carried
until something removes it, which is the whole reason the field is one string
rather than a list. A note reports state observed while one attempt was running, so
it is stale the moment the next attempt moves; a note that still matters is one the
planner or the monitor attaches again against what the run now shows. That is what
stops a node accumulating instructions nobody re-read.

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
