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
`Card`; the navigation, item detail and overall view scroll inside
its `ScrollArea`; the node's task and context are its `Accordion`; the telemetry banner is its
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
  as a whole: its telemetry tiles over the **graph timeline** (below).
- use **Graph** to inspect status and progress; green nodes succeeded, red nodes
  failed or were cancelled, and an animated acid highlight marks active work. The
  canvas arrives **fitted whole** — every card of the graph inside it — at every
  width in the viewport matrix, and its own controls zoom in from there. That is a
  floor on `fitView`'s zoom rather than a fixed scale: at the previous floor a
  working area narrower than the graph divided by it could not hold the graph, and a
  phone's column showed about a third of one with the rest off both sides of a canvas
  that says nothing about having more beside it.
- select a node in the graph or keyboard-accessible node list to open its
  **timeline view** (below).

**One reading, at three scopes.** The same plot, the same lane words and the same
clock are used for the whole run, for one node, and for one conversation, and each
is one click into the last: the [graph timeline](#the-graph-timeline) on the overall
view opens a node's [timeline view](#the-node-timeline-view), whose transcript opens
a session in the right panel, where the package's `ConversationTimeline` plots that
session's own turns above them. Nothing about the vocabulary changes on the way down
— a segment labelled Judge at the graph scope is the Judge lane of its node and the
Judge turns of its conversation.

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

**An open conversation is readable while the run it belongs to is working.** A
transcript is re-read only when the served timeline says *that* session recorded
something — the span it opened carries the session's state, its end, and one event
per turn — so a run whose other nodes are busy costs the open transcript nothing,
and a session that has stopped recording is never read again. The re-read a live
session does earn happens underneath the reader: the turns already on the page stay
exactly where they are, new ones are appended to them, and the loading skeleton is
only ever the first read of a transcript, never a refresh of one. The panel follows
that growth only while the reader is at the end of it — scroll up and it holds the
position you chose while the session keeps being written below.

## The graph timeline

The overall view answers the question above a node: **what has this run spent its
life on?** It is one plot with three levels, each one click from the next and all
three in the vocabulary the node view already uses, projected from
`GET /api/v2/runs/{run_id}/timeline?scope=run`.

- **Collapsed** it is a single line covering the whole run — from its earliest
  record to its last, or to the moment the served payload was read while the run
  is still going. "Now" is the payload's own `observed_at` rather than the
  browser's clock, so the line says how long the run had been going when it was
  last read instead of drifting between polls.
- **Opened once** it is one row per plan node, plus a **run-level row** for the
  sessions recorded at no node — the orchestrator driving the graph and the
  per-round check-ins. Each row says how long it recorded work and how long it
  did not.
- **Opened again**, a row is that node's own category lanes: the same words the
  node view draws, read out of the `scope=run` summaries. A summary's lane comes
  from the *pair* of roles it carries, which is what tells a lint run from the
  worker whose semantic role it borrows.

Every plot is handed one controlled range, so a wheel or a brush anywhere reframes
all of them and a column means the same instant at every level. There is
deliberately **no time cursor** here: a cursor locks a plot to a position in a
stream being read beside it, and a graph is many streams at once.

**Silence is drawn.** A run's wall time is mostly not work — a node waits on a
dependency, a graph waits on a person, a driver waits on a provider — and blank
space cannot be told from a record that is missing. A stretch nothing was recorded
in is a hatched segment carrying how long it lasted, and a node the run never
reached is that reading for its whole life. Interior gaps too narrow to see are
dropped; the gaps at the two ends never are, because they are what makes every row
span the same interval, which is what one shared zoom rests on.

Clicking a node's row — its name, or any segment of it, working or idle — opens
that node's view. A run-level session has no node to drill into, so it opens in the
same two-thirds right panel a node's own sessions open in, with `ConversationTimeline`
and role-labeled turns, and Escape closes it.

A dozen rows stacked in one reading is what decides this surface's width behaviour.
Each row is a card of its own so the stack has an edge to be read against, its head
wraps its name away from the recorded-versus-idle reading rather than holding the row
wider than the screen, and at the phone the axis's two clock readings stack for the
same reason. The rows themselves state `min-width: 0`: a row's min-content is its
legend plus two monospace clock readings, which is wider than a phone, and without
that the rows sized the card and took their own controls off the side. A row is
narrow at that width and its segments are slivers — one shared zoom is the answer to
that, and it is why a wheel or a brush on any row reframes every one of them.

## Reading a segment

Hovering or focusing any segment, in any plot on any view, states what it is: its
label, its lane, its status, when it started and ended, how long it took, and for a
failure the excerpt of what went wrong. `@oneharness/ui` composes that text — this app
never restates it — but it paints it as a fixed 320px popover *inside* the plot, at a
fixed offset below the lane row, with no collision handling. A plot only has to be
shorter than the popover for the bottom of the reading to be cut off, which every
collapsed line and every graph row is, at every width in the matrix.

Widening a box does not fix that, because the clipping is not one box: the plot clips,
the node view's pinned region clips, and the overall view's scroll area clips. Nor does
un-clipping the plot, which trades the cut bottom for a popover that leaves the screen
sideways — measured at 241px past a 390px viewport.

So the presentation moves out of that stack entirely.
`src/features/timeline/TimelinePopover.tsx` is one layer mounted once at the app root,
which watches for a segment being pointed at or focused, **reads the text off the very
element the segment's `aria-describedby` names**, and renders it into a fixed-position
portal on the document — placed against the segment, flipped above it when there is no
room below, and clamped to the screen on both axes. The package's own copy stays where
it is and stays what `aria-describedby` resolves to, so assistive technology reads the
description exactly as before; it is only retired from the painted surface, and the
portal copy is `aria-hidden` because it is a second rendering of a description the
reader already has. It follows the segment as its region scrolls and as the window
resizes, and it goes when the segment does — including when the view it was in is
replaced under a reading nobody dismissed.

**Pointed at and focused are tracked apart**, which the package does not do: its plot
clears on mouse-leave whether or not the segment still holds focus, so a reader who had
tabbed to a segment lost its reading the moment a stationary pointer stopped being over
it — which a region scrolling underneath does by itself. Here the pointer wins while it
is over a segment, because that is the one being asked about, and the focused reading
comes back when the pointer leaves rather than nothing coming back.

One layer at the root covers all three scopes, because all three are the same
component: the graph timeline's rows, a node's plot, and the `ConversationTimeline` in
the opened panel — that last one nested deepest inside the clipping, in a panel inside
a scroll area.

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
  the window its records happened to fall in. The compact line is sized to fit
  whole at every width, because it is the view a node opens on. Ten expanded lanes
  and a reading fit no viewport shorter than the laptop the layout is designed
  against, so below that the region scrolls: the axis is painted inside the plot's
  own clipping box, so it cannot be pinned above that fold, and collapsing is the
  one-click way back to it.
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
  tabs beside the timeline, one selection away rather than a wall of blocks. Six
  names do not fit every width, so below the breakpoint they wrap onto a second
  row rather than hiding the ones past the edge behind a scroller — down to the
  phone, where the same names need four rows and 170px of an 844px screen, and
  the strip scrolls again so the timeline the view opens on has room to be drawn.

Nothing in either surface grows with the size of the run. A run of eight or more
consecutive same-kind siblings arrives as one grouped row, and a conversation
hands out `PAGE_SIZE` turns at a time, so the node whose recorded work is two
hundred conversations reads as a handful of items rather than two hundred.

The view reads only what it shows. The run detail is fetched for the selected run
alone and with `include_conversations=false`; the ordered record comes from the
timeline; and a transcript is fetched by id only for the item that is open. A node
the run has recorded nothing for says so, and a timeline read that fails is
reported where the timeline would have been rather than leaving an empty pane.

## Screens: seeing the app while it changes

The operator iterates on this surface visually, and a polish problem at one width is
invisible until somebody starts the app by hand at that width. `just dag-ui-screens`
removes that step:

```sh
just dag-ui-screens                       # every surface at every viewport
just dag-ui-screens --grep "at 390x844"   # one width; extra arguments reach Playwright
```

It boots the browser tier's own stack — `apps/dag-ui/screenshots.config.ts` reuses
`playwright.config.ts` wholesale, so the fixture server, Vite, the free ports and the
throwaway fixture directory are all chosen exactly as they are for the e2e run — drives
`e2e/gallery.screens.spec.ts`, and prints the gallery it wrote: one PNG per surface per
viewport, plus an `index.html` contact sheet that puts every viewport of one surface in
a row. Galleries land under the gitignored `apps/dag-ui/.screenshots/`, one
directory per invocation, because the gallery is the one thing the Playwright configs do
not already keep apart — so two operators, or two agents, capturing at the same time
neither collide nor dirty the tree. `scripts/dag-ui-screens.sh` is that path's one
source: the spec is handed it in `DAG_UI_SCREENSHOT_DIR` and refuses to run without one,
so there is no second place a gallery can land.

The **viewport matrix** is declared once, in `e2e/viewports.ts`, and used twice: the
gallery captures at every entry, and `e2e/dag-ui-navigation.spec.ts` drives the journeys
whose outcome depends on width at the widest and narrowest of them.

Each entry's name is what a captured file and a journey title are called, so the table
below reads in the same words the gallery does. `scripts/check-dag-state-contract.py`
reconciles it with that declaration, so a width can neither reach the gallery without
reaching this table nor be promised here without being photographed.

| Viewport | What it stands for |
| --- | --- |
| 1920x1080 | a full desktop screen |
| 1440x900 | a large laptop |
| 1280x800 | a common laptop |
| 1024x768 | the smallest desktop layout still in use |
| 390x844 | a phone — the only entry where the shell's two columns stop fitting |

The **surfaces** are declared once too, as `SURFACES` in `e2e/gallery.screens.spec.ts`,
and each one names the PNG it writes at every viewport — so the table below is also how
to find a capture in the gallery directory. `scripts/check-dag-state-contract.py`
reconciles it with that declaration for the same reason it reconciles the matrix: a
surface can neither be photographed without being listed here nor promised here without
being photographed.

| Captured file | What it shows |
| --- | --- |
| `01-run-list-overall` | the run list beside the overall view, its graph timeline collapsed to one line |
| `02-graph-rows` | that line opened into one row per node beside the run's own, with one row opened again into its lanes |
| `03-run-level-session` | a run-level session opened over that reading in the right panel |
| `04-graph` | the graph |
| `05-node-collapsed` | the node view as it opens: the compact line over its transcript |
| `06-node-expanded` | the same view with one row per category |
| `07-node-item-detail` | a verification opened over that reading |
| `08-conversation` | a conversation in the right panel |

The tier asserts nothing beyond having reached each surface with its real reads landed:
it is the operator's eyes, and `e2e/dag-ui-navigation.spec.ts` is what holds the
behaviour it photographs.

## Getting around: what scrolls and what stays put

The shell is exactly one viewport tall and every region inside it scrolls on its own,
which fails silently: a region that overflows its container reports nothing, it just
puts content where no scroll can reach it. `e2e/dag-ui-navigation.spec.ts` holds that
arrangement to what an operator can actually do — the run list scrolls and pages the
next runs in, the working area scrolls without moving the run list, a graph → node →
timeline item walk comes back the way it went, a deep link opens what it names at phone
width, and Escape leaves the node view. Each journey ends by asserting the *document*
does not scroll, because a document taller than the window is the signature of a region
that has put its content out of reach.

The same file holds what a region does with content **wider** than itself, which is
the other half of the same failure and the one a vertical scroll cannot rescue. Each
of these is a measurement of a real element against the box that is supposed to hold
it, rather than a screenshot somebody has to notice something in:

- every card of the graph is inside the canvas, at the widest entry and the narrowest;
- every item of a node's transcript is inside that region, and the region has nothing
  to scroll sideways to, driven at the node whose labels are unbreakable branch names;
- a turn's tool call is wholly in the viewport at phone width **and opens**, because
  the control that opens it is the row that was going off the edge;
- a lone recorded term takes the whole fact row, since the list paints its cells by
  showing its own border colour between them and an empty cell is a filled panel;
- a hovered segment's reading is whole — on screen on every side, with nothing hidden
  inside its own box, and carrying exactly the text of the description the segment
  names. That one is driven at **all five** widths rather than the two extremes,
  because the clipping it replaced was never a narrow-screen problem: it cut the same
  reading on a full desktop screen. Around it: the same reading reached by Tab from the
  top of the document rather than by pointer, in each of the three plots, following its
  segment through a scroll and a resize, flipped above a segment with no room below it,
  and gone when the pointer leaves, when focus leaves, and when the view it was in is
  replaced;
- the graph is whole on **both** axes, and stays inside the canvas at the new zoom
  floor the controls can now reach.

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

The gallery spec lives beside the journeys because it drives the same surfaces against
the same stack, but it asserts nothing and writes images, so `playwright.config.ts`
ignores `*.screens.spec.ts` and `screenshots.config.ts` runs nothing else. The journey
files themselves are ordered: Playwright collects test files in name order, and
`dag-ui.spec.ts`'s last journeys deliberately take the served runs away one at a time,
so `dag-ui-navigation.spec.ts` is named to sort ahead of it.

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

That gallery is deliberately outside `just check`: its product is images a reviewer
reads for clipping, overlap and reflow, which no selector describes. It is what found
the tab list widening the working area past the viewport, the pinned timeline leaving
the transcript no room at ten expanded lanes, and the document that scrolled out from
under `scrollIntoView`. It went on to find the axis sliced through the middle of its own
digits by the fold, the six tab names spilling past *both* edges of a scroller that
could only ever reach one of them, and — once they wrapped — a second row of them
drawn below the strip that was supposed to hold it. The pass after that found the
graph cropped rather than fitted wherever the working area was narrower than the graph
divided by `fitView`'s zoom floor, a transcript whose grid column had been sized to an
unbreakable branch name and so ran past the side of a region that scrolls vertically,
the tool row of a turn — which is the control that opens it — held at its own
min-content and pushed off the edge of a panel that clips sideways, and a fact list
painting half a panel of its own border colour beside a record that states one term.
Each is now a journey, because a gallery only catches what someone looks at.

The one it caught that no box in this app could hold is the reading a hovered segment
carries — see [Reading a segment](#reading-a-segment) below, which is where that moved
to and why.

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
