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
Escape key. It is master and detail over `GET /api/v2/runs/{run_id}/timeline`:

- the **rail** lists what the node recorded, in order. Each row states its kind,
  its time, its status and its duration; a span discloses the events and spans
  inside it and starts collapsed. The node's own span is the subject of the view
  rather than a row in it, so every row is one recorded activity.
- the **detail region** takes the rest of the width and shows the opened item
  expanded: a conversation turn through the package's `TurnCard`, a verification
  as the gate attestation it recorded and the log it points at, a publication as
  its PR and the checks observed on it, and anything else as the typed record the
  timeline served.
- the node's **task, completion criteria, dependencies, PR and gate result** stay
  above the rail as an `Accordion`, one disclosure away rather than a wall of
  stacked blocks.

Nothing in the rail grows with the size of the run. A run of eight or more
consecutive same-kind siblings arrives as one grouped row
(`src/features/timeline/timeline-model.ts`), and each expanded level hands out
`PAGE_SIZE` rows at a time, so the node whose recorded work is two hundred
conversations reads as a handful of rows rather than two hundred.

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
| `01-run-list-overall` | the run list beside the overall view |
| `02-graph` | the graph |
| `03-node-timeline` | the node view's timeline tab |
| `04-node-item-detail` | the node view with a timeline item open |
| `05-conversation` | an open conversation |

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

## Verification

```sh
just check
just gate
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

Every ordinary run exercises that choice; only two overlapping runs exercise what
it is for, so `isolation.config.ts` is one more Playwright run that starts no server
of its own and launches two real runs of the tier at once, asserting each built and
removed a fixture directory of its own. It is a separate config deliberately: a spec
under the tier's own `testDir` would inherit the environment recording that run's
choice, and the runs it launched would reuse it rather than choose their own.

The fixture's `dag-ui-busy` run is the scale case: one node with two hundred
recorded sessions, one of them thirty turns long, so the browser tier proves the
grouped rail and both pagings against a real server rather than a payload written
by hand. What it cannot reach is a read that fails between the timeline and the
transcript it names — the run detail and the timeline are projected from the same
strict journal, so no served run fails one and not the other, and a browser only
sees the failure with the whole API unreachable. Those branches carry a
line-scoped `llmlint: ignore[changed_behavior_has_e2e]` naming this reason, and
are driven through the real telemetry client in `src/app/App.test.tsx`.
