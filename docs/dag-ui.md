# DAG Observatory

The read-only live and historical view of orchestrated DAG execution is
[`onepipeline-ui`](https://github.com/nickderobertis/onepipeline-ui). It is not
built in this repository: the design record, the component choices, the schema
validation, and the browser tier that photographs it all live there, beside the
code they describe. This page is the operational half — how the two published
pieces are started here, and what each of the recipes around them does.

## The two published pieces

The project publishes them separately, because they contain different things.

* **The read API** — the `onepipeline-api-cli` wheel, installed by session setup
  at the release `config/onepipeline-ui.version` declares, providing one command,
  `onepipeline-api serve --runs-root DIR`. It wraps the onepipeline SDK and serves
  the `/api/v2/...` contract plus `/healthz`.
* **The view** — the `onepipeline-ui` npm package, pinned to the same release in
  `package.json` and installed by `scripts/workspace-install.sh`. It installs no
  command: it is a built static bundle under `dist/`, to be served.

## Run it

```sh
just bootstrap
just telemetry-server   # the read API; reads ./runs, or --runs-dir elsewhere
just dag-ui             # the bundle, in a second shell
```

Open the address `just dag-ui` prints (`http://127.0.0.1:4173` by default).

The bundle asks for `/api/v2/...` relative to wherever it was served from — it
declares no API host — and the read API serves the data but not the bundle. So
`scripts/dag-ui-server.js` puts the two behind **one origin**: it serves the
bundle and proxies those two path prefixes to the API. A second port would make
every request cross-origin, and the read API sends no CORS headers, so a
same-origin proxy is the only arrangement a browser accepts. `DAG_UI_PORT`,
`DAG_UI_HOST`, `DAG_UI_API_URL`, and `DAG_UI_DIST` move either end of it.

`just telemetry-server` keeps the recipe's own flag spellings: `--runs-dir`,
`--host`, and `--port` are rendered as the published `--runs-root` and `--bind`,
and the runs root defaults to the one `just runs` and `just status` read
(`ONEPIPELINE_RUNS_DIR`, or `runs`), so the API serves the runs the planner is
already looking at.

## Photograph it

```sh
just dag-ui-screens                      # every surface, every viewport
just dag-ui-screens --run <run-id>       # a named run rather than the newest
just dag-ui-screens --runs-root DIR      # a different store
```

This starts an API and a bundle server on ports of its own, drives Playwright's
`screenshot` command over them, and prints the gitignored per-invocation gallery
it wrote under `.screenshots/`. Two of these at once neither collide nor leave the
tree dirty. Extra arguments reach `playwright screenshot`, so `--full-page` and
friends work.

The viewport matrix is declared once, in `scripts/dag-ui-screens.sh`: the desktop
sizes this view is read at, down to the smallest laptop still in use, plus one
phone width — the only entry where the shell's two columns stop fitting, and
therefore where every reflow defect shows up first.

What it photographs is bounded by what the published packages ship. `onepipeline-ui`
publishes the built bundle alone — no fixture server, and no screenshot surface
with the per-surface waits its own repository's tier uses — so the surfaces here
are the ones a URL names against a real runs root: the run list, and the
`overall`, `graph`, and `timeline` views of one run when the runs root has one. A
runs root with no runs in it is photographed as the empty run list, and the recipe
says so rather than reporting a fuller gallery than it captured. For the
surface-by-surface tier with its own fixtures, run `just dag-ui-screens` in
`onepipeline-ui` itself.
