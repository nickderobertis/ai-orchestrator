# DAG Observatory web UI

`apps/dag-ui` is the read-only live and historical view of orchestrated DAG
execution. It visualizes each round with React Flow, using the exact coordinates
from `@ai-orchestrator/dag-layout`, and renders agent transcripts with the
published `@oneharness/ui` components. Every payload it reads is validated by
`@ai-orchestrator/dag-model` through `@ai-orchestrator/telemetry-client`; the app
declares no schema, event name, or API path of its own.

## Run locally

Start the read-only telemetry API, then start Vite in a second shell:

```sh
just bootstrap
just telemetry-server --runs-dir runs
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
