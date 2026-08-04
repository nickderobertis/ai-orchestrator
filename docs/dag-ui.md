# DAG Observatory web UI

`apps/dag-ui` is the read-only live and historical view of orchestrated DAG
execution. It visualizes each round with React Flow, using the exact coordinates
from `@ai-orchestrator/dag-layout`, and builds its surface out of the published
`@oneharness/ui` components. Every payload it reads is validated by
`@ai-orchestrator/dag-model` through `@ai-orchestrator/telemetry-client`; the app
declares no schema, event name, or API path of its own.

## Design system

`@oneharness/ui` is the app's design system, not just its transcript renderer.
The view switcher is its `Tabs`; panels, metric tiles and transcript cards are its
`Card`; the navigation, timeline rail, item detail and overall view scroll inside
its `ScrollArea`; the node's task and context are its `Accordion`; a timeline row
discloses what it contains through its `Collapsible`; the telemetry banner is its
`Alert`; the loading view is its `Skeleton`; and every secondary action is its
`Button`, with `Badge`, `Separator`, `Tooltip` and the `cn` helper where they fit.
`ConversationView` is deliberately not adopted: it requires a reply handler and
continuation callbacks, and this app is read-only.

Status is the one place the package's components are not used unchanged.
`StatusBadge` is the right component for a conversation, whose state really is one
of the four it knows, and the opened session's header uses it. A run or a node is
not: the
ledger settles a node as `done` and a run as `complete`, holds a human action at
`waiting`, and abandons work as `cancelled`. `StatusBadge` gives an unrecognized
state no tone, so passing these through would leave a finished run and an
abandoned one looking alike, and relabelling them to fit its vocabulary would
replace the word the ledger recorded. `src/features/runs/StateBadge.tsx` keeps
both, mapping the orchestrator's own states onto the package's `Badge` and its
semantic utilities: settled work (`done` for a node, `complete` for a run) reads
`success`, lost work (`failed`, `cancelled`) reads `destructive`, and `running`
reads `info`. Every other state it can report — `pending`, `waiting`, `stopped`,
`parked`, `blocked`, `unknown` — stays neutral on purpose, because work with no
outcome yet has none to report. `e2e/dag-ui.spec.ts` asserts each of those tones
against the token it claims, on the node view, the run list, and the graph
canvas, whose node surfaces carry the same meanings.

One palette governs the whole surface. `src/styles.css` imports the package
stylesheet **through this app's Tailwind build** rather than injecting it as raw
text, which is what makes the package's tokens, its `dark` variant, its `@theme`
and its `@layer` rules real here — a raw `<style>` element would deliver the token
values and silently drop every `@apply` rule and every utility its components are
written in. The import carries `source(none)` because the package bakes
`source(…)` modifiers into its own `@import "tailwindcss"` that name directories
existing only in its source tree; the `@source` lines beside it name the trees
this app scans instead, including the package's `dist`, which Tailwind never scans
on its own. The dark palette is selected by `class="dark"` on the document element
in `index.html`, and the app's own chrome is written in the package's tokens
(`--card`, `--sidebar`, `--border`, `--success`, `--destructive`, `--info`,
`--warning`) rather than a palette of its own. React Flow scopes its variables to
its own root, so the canvas takes `colorMode="dark"` for the same reason.

The package's Radix, markdown and Tailwind peer dependencies are declared in
`apps/dag-ui/package.json`: the app imports the package's root entry, whose module
graph statically pulls all of them, and a locked install has to reproduce that
tree rather than rely on the installer filling peers in implicitly.

## Run locally

Start the read-only telemetry API, then start Vite in a second shell:

```sh
just bootstrap
just telemetry-server   # reads ./runs; pass --runs-dir to point elsewhere
just dag-ui
```

Open `http://127.0.0.1:4173`. Vite proxies `/api` and `/healthz` to
`http://127.0.0.1:8787` — the loopback address and port `orchestrator/server.py`
binds by default. Set `DAG_UI_API_URL` to proxy somewhere else. In a production
deployment, serve the built files from `apps/dag-ui/dist` on the same origin as
the API, or route those paths to it.

## Use

The left navigation groups current and settled DAGs by their launching Claude
or Codex session, read from the `launch` attribution the run list itself carries.
The grouping key is that record's opaque `session_key`, not its `launch_id`: one
planner session mints a fresh launch id per `just orchestrate`, so every run of one
session gathers under one heading, and its short form is the same fingerprint
`just runs` prints. A run whose session nothing can name reads as its launch, and a
run with no launch record at all — an e2e fixture, a bare `run-plan` — reads as
`Unattributed` rather than as an unknown session. Select a run, then:

- **Overall** is where an address that names no view lands, and it is the run read
  as a whole: its telemetry and its **run-level sessions**.
  A session the graph placed at no node is run-level work, so that is where the
  planner's own conversation and the per-round check-ins are read; each names its
  own role — orchestrator, check-in — from the role pair its timeline span carries,
  and the same launch phrase the navigation heads its group with. Each is fetched
  only while it is open, and a timeline that has not arrived or could not be read
  is reported as such rather than as a run that recorded none.
- use **Graph** to inspect status and progress; green nodes succeeded, red nodes
  failed or were cancelled, and an animated acid highlight marks active work;
- select a node in the graph or keyboard-accessible node list to open its
  **timeline view** (below).

Every stamp is read in the browser's own zone — as a clock time for work recorded
today and with the date it happened on for anything older — with the whole instant,
zone included, on hover. Durations are read in the units they ran in (`420ms`,
`42s`, `12m 4s`, `2h 5m 10s`), never as a raw second count.

The selected run, node, view, and opened timeline item are encoded in the URL
query string, so a specific moment of a node's execution can be bookmarked or
shared. SSE events invalidate cached records; the UI always refetches validated
data through the telemetry client instead of treating the event stream as a
second state model. A dropped stream is reported in the header banner and clears
itself when the browser reconnects, because the server opens every connection
with a fresh snapshot.

## The node timeline view

Opening a node replaces the graph with a view over the whole working area — the
graph stays one breadcrumb away, reachable by pointer, by Tab, and under the
Escape key. It is a **timeline over a transcript**, both projected from
`GET /api/v2/runs/{run_id}/timeline` and locked to one clock:

- the **timeline** is pinned across the full width and opens as one compact line
  showing what dominated each moment. Expanding gives every category a row:
  Worker, Judge, Lint, Orchestrator, Check-in, PR author, Verification,
  Publication, Lock waits, Human wait. Those are the served `agent_role`,
  `transport_role` and span-kind vocabulary rendered as words — an operator never
  reads a served identifier such as `rollup` or `pr-drafting`, and the span kinds
  that *hold* work rather than being work (a round, the node, a lifecycle step)
  occupy no lane at all. A journal record is a moment rather than an interval, so
  it is a **marker** — an icon on a full-height line over every lane. The axis
  reads local wall-clock time and elapsed-from-start, and the compact line and the
  expanded lanes always span the same window, so a moment does not move when the
  view is collapsed. An aggregate is plotted at the total it carries, not across
  the window its records happened to fall in.
- the **transcript** below it is the long-form reading: one item per span and
  event, in order, each with its summary inline. Scrolling it moves the
  timeline's cursor and clicking a segment or a marker scrolls and focuses its
  item; both directions are the package's `useTimelineScrollSync`. The selection
  is in the address, so an item stays bookmarkable.
- **detail on demand** slides in from the right over two thirds of the working
  area, leaving the navigation alone. Escape and its own control close it. A
  conversation renders the package's `ConversationTimeline` pinned above its
  turns, each `TurnCard` carrying the role that spoke — Worker, Judge or Lint.
- one **onejudge dispatch** — the agent session plus the judge and lint sessions
  that supervised it — is one labelled group, nested in the transcript and named
  on the conversation header. Schema 10 serves that identity as `dispatch_id`;
  until this repository's read model emits it,
  `src/features/timeline/timeline-model.ts` recovers the same grouping from the
  nesting and roles schema 9 does serve.
- the node's **task, completion criteria, dependencies, PR and gate result** are
  tabs beside the timeline, one selection away rather than a wall of blocks.

Nothing in either surface grows with the size of the run. A run of eight or more
consecutive same-kind siblings arrives as one grouped row, and a conversation
hands out `PAGE_SIZE` turns at a time, so the node whose recorded work is two
hundred conversations reads as a handful of items rather than two hundred.

The view reads only what it shows. The run detail is fetched for the selected run
alone and with `include_conversations=false`; the ordered record comes from the
timeline; and a transcript is fetched by id only for the item that is open. A node
the run has recorded nothing for says so, and a timeline read that fails is
reported where the timeline would have been rather than leaving an empty pane.

## Verification

```sh
just check
just gate
just dag-ui-screens
```

`just check` runs both tiers of the app's suite. Testing Library exercises the
views through the real telemetry client with only the browser's `fetch` and
`EventSource` replaced. Playwright then drives the built user journeys in a real
browser against a real `orchestrator/server.py` process:
`apps/dag-ui/e2e/fixtures/serve_fixture.py` writes a throwaway run directory with
the executor's own journal writers, serves it through the actual read API, and
`playwright.config.ts` starts both that server and Vite. Only the paid harness'
history store is recorded, through the same `tests/e2e/fake_oneharness.py`
subprocess the Python e2e suite uses.

Everything that tier does not share with another run of itself is chosen per
run: `playwright.config.ts` asks the kernel for its ports and makes its own fixture
directory, records both in the environment its workers are forked with, and
`e2e/global-teardown.ts` removes the directory afterwards. Concurrent worktrees are
the normal state on this host, and fixed ports plus one shared fixture path made two
overlapping runs collide by construction — a `--strictPort` Vite refusing a port the
other run holds, and a fixture server rebuilding the run directory the other run is
asserting against. The one port that must *refuse* connections, so the
unreachable-API journey has a real failure to observe, is held bound but unlistened
by the stall server (`serve_fixture.py --refuse-port`): leaving it merely free would
let a concurrent run's own API server take it.

`just dag-ui-screens` is the third config over the same servers
(`screens.config.ts`, `e2e-screens/`). It photographs the node view — collapsed,
expanded, and with a conversation open in its panel — at every width the layout
supports, into gitignored `apps/dag-ui/.screens/`. It is deliberately outside
`just check`: its product is images a reviewer reads for clipping, overlap and
reflow, which no selector describes. It is what found the tab list widening the
working area past the viewport, the pinned timeline leaving the transcript no
room at ten expanded lanes, and the document that scrolled out from under
`scrollIntoView`.

Every ordinary run exercises that choice; only two overlapping runs exercise what
it is for, so `isolation.config.ts` is one more Playwright run that starts no server
of its own and launches two real runs of the tier at once, asserting each built and
removed a fixture directory of its own. It is a separate config deliberately: a spec
under the tier's own `testDir` would inherit the environment recording that run's
choice, and the runs it launched would reuse it rather than choose their own.

The fixture stamps the live run's own sessions from the same wall clock its
journal is written with, and in the shape one claude-code dispatch really
records: a worker that talks for a couple of minutes, the lint run it makes of
its own work happening inside that dispatch, and the judge supervising it once it
stops. That is what the node view has to survive — sessions stamped on a fixed
calendar date sit hours from the spans they belong to, and every dispatch is then
plotted as a sliver too narrow to see, let alone click, while the journeys pass
anyway because a sliver still clears the design system's minimum bar width. The
journeys therefore read a supervising session's width as a *share of the plot*.

The fixture's `dag-ui-busy` run is the scale case: one node with two hundred
recorded sessions, one of them thirty turns long, so the browser tier proves the
grouped rail and both pagings against a real server rather than a payload written
by hand. What it cannot reach is a read that fails between the timeline and the
transcript it names — the run detail and the timeline are projected from the same
strict journal, so no served run fails one and not the other, and a browser only
sees the failure with the whole API unreachable. Those branches carry a
line-scoped `llmlint: ignore[changed_behavior_has_e2e]` naming this reason, and
are driven through the real telemetry client in `src/app/App.test.tsx`.
