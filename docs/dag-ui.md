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

Open the address `just dag-ui` prints — it names the port it bound, which
`DAG_UI_PORT` moves.

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

## Which release is answering

Moving `config/onepipeline-ui.version` installs a release; it does not put one in
front of an operator. Both pieces are loaded once, at start: a `just
telemetry-server` and a `just dag-ui` left running from before a bump go on
serving what they loaded, so **an adoption reaches a browser only after both are
restarted**. Neither notices the other's release either — the reader and the
bundle are separate artifacts of one version.

`/healthz` is what answers the question without guessing. The read API reports its
own liveness *and* the `onepipeline` release it links:

```sh
curl -s http://127.0.0.1:8765/healthz
{"status":"ok","onepipeline_version":"0.7.3"}
```

That release is the reader's own, and it is **not**
`config/onepipeline.version` — this host pins the engine CLI and this reader
separately, and the reader links whatever its release was built against. So the
two are expected to differ; what the field is for is being able to say which
reader is answering rather than assuming it.

`tests/e2e/test_dag_ui_serving_e2e.py` holds a freshly started pair to the
adopted release from that same served surface: the bundle handed back is the npm
half installed at `config/onepipeline-ui.version`, and the reader answering links
the engine the adopted wheel links. So a bump that installs one release and
serves another fails there instead of being noticed by a person.

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
