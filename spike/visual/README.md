# spike-visual: the Planned tasks dependency graph, mocked

Throwaway harness for the `design-doc-why-and-images` plan; nothing here lands.

- `fixtures/plan-{10,48,100}.json` — three plans shaped like this host's (written once by `make-fixtures.mjs`).
- `graph.mjs` — stand-in for `onetaskgraph project graph <ID> --format mermaid --direction auto`.
- `capture.mjs` — renders each graph with `@mermaid-js/mermaid-cli` on the locked Playwright
  Chromium at an 880-px page, measures it, and screenshots the Planned tasks section before
  (table only) and after (graph above the table) at an 880-px GitHub-like column.

Re-capture everything from the fixtures (writes `out/`, including `out/measurements.json`):

    cd spike/visual && PUPPETEER_SKIP_DOWNLOAD=1 bun install && node capture.mjs
