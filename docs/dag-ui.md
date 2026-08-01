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
or Codex session, read from the `launch` join the run list itself carries. Select
a run, then:

- use **Graph** to inspect status and progress; green nodes succeeded, red nodes
  failed or were cancelled, and an animated acid highlight marks active work;
- select a node in the graph or keyboard-accessible node list to open its
  **timeline view** (below);
- use **Overall** to see whole-run telemetry and the run's **run-level sessions**.
  A session the graph placed at no node is run-level work, so that is where the
  planner's own conversation and the per-round check-ins are read; each is fetched
  only while it is open, and a timeline that has not arrived or could not be read
  is reported as such rather than as a run that recorded none.

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
Escape key. It is master and detail over `GET /api/v1/runs/{run_id}/timeline`:

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

Everything that tier does not share with another run of itself is allocated per
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

The fixture's `dag-ui-busy` run is the scale case: one node with two hundred
recorded sessions, one of them thirty turns long, so the browser tier proves the
grouped rail and both pagings against a real server rather than a payload written
by hand. What it cannot reach is a read that fails between the timeline and the
transcript it names — the run detail and the timeline are projected from the same
strict journal, so no served run fails one and not the other, and a browser only
sees the failure with the whole API unreachable. Those branches carry a
line-scoped `llmlint: ignore[changed_behavior_has_e2e]` naming this reason, and
are driven through the real telemetry client in `src/app/App.test.tsx`.
