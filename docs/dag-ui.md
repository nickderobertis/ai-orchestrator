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
`Card`; the navigation, detail panel and overall view scroll inside its
`ScrollArea`; the telemetry banner is its `Alert`; the loading view is its
`Skeleton`; and every secondary action is its `Button`, with `Separator`,
`Tooltip` and the `cn` helper where they fit. `ConversationView` is deliberately
not adopted: it requires a reply handler and continuation callbacks, and this app
is read-only.

Status is the one place the package's components are not used unchanged.
`StatusBadge` is the right component for a conversation, whose state really is one
of the four it knows, and `TranscriptPanel` uses it. A run or a node is not: the
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
against the token it claims, on the detail panel, the run list, and the graph
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
or Codex session. Select a run, then:

- use **Graph** to inspect status and progress; green nodes succeeded, red nodes
  failed or were cancelled, and an animated acid highlight marks active work;
- select a node in the graph or keyboard-accessible node list to see its task,
  completion criteria, dependencies, PR and checks, gate result, logs, and
  separately labeled worker, judge, check-in, and PR-author transcripts;
- use **Overall** to see whole-run telemetry and the planner/orchestrator
  conversation.

The selected run, node, and view are encoded in the URL query string, so a
drill-down can be bookmarked or shared. SSE events invalidate cached records;
the UI always refetches validated data through the telemetry client instead of
treating the event stream as a second state model. A dropped stream is reported
in the header banner and clears itself when the browser reconnects, because the
server opens every connection with a fresh snapshot.

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
