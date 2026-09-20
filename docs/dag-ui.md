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
{"status":"ok","onepipeline_version":"0.29.0"}
```

That release is the reader's own, and it is **not**
`config/onepipeline.version` — this host pins the engine CLI and this reader
separately, and the reader links whatever its release was built against. So the
two are expected to differ; what the field is for is being able to say which
reader is answering rather than assuming it.

**Today the two differ, and the reading to carry is that neither number constrains
the other**: the adopted `onepipeline-ui` 0.9.0 statically links onepipeline 0.37.0 and
onejudge 0.13.1, read off its own wheel's SBOM, while `config/onepipeline.version` reads
0.40.0 — the engine moved for the worktree pool's capacity hold and maintenance schedule, which the reader has no part in, and the reader
stayed, because nothing requires the two to agree. The reader has been moved with the engine before
because a reader linking an older onejudge than the one writing a run's reports refuses
a newer report schema and renders no transcript. Through an earlier adoption the reader
linked onepipeline 0.19.0 while the CLI a dispatch ran was nine minor releases ahead.
It was the reverse for two adoptions — the engine pin was held at 0.18.4 for a
settlement write-back defect that had nothing to do with reading runs, and the
Observatory was adopted anyway because the reader carries its own engine. That hold is
over and this surface is still the one that says which engine is answering, measured
here rather than argued from the manifest, which is what `/healthz` is for.

`tests/dag_ui/test_dag_ui_serving_e2e.py` holds a freshly started pair to the
adopted release from that same served surface: the bundle handed back is the npm
half installed at `config/onepipeline-ui.version`, and the reader answering links
the engine the adopted wheel links. So a bump that installs one release and
serves another fails there instead of being noticed by a person.

### What the adopted view renders, and what it has nothing to render

**`onepipeline-ui` 0.9.0**, the release `config/onepipeline-ui.version` pins, carries
what 0.6.3 added: it shows which release carried each landed node, alongside every
release event.
Opening a node whose dependency was adopted `published` shows what it waited on and
the versions that arrived; opening one held shows what it is held on, whether that is
an automated probe or a person's release step.

**On this host it has so far rendered none of that, and that is not a defect in the
view.** No run recorded here yet holds a release event: `ai-orchestrator` declares no
release target, and no plan launched from here has yet awaited one. The tracked
override `config/onevcs.releases.yml`, which `just repos-apply` installs as
`$ONEVCS_HOME/releases.yml`, gives this repository the `published` rung and each
producer this host installs a default target, so a node here that depends on a
producer's node waits for that producer's wheel — and the first run that does is the
first this view has a release row for. The consequence worth internalising is that this
surface fails *silently* in the direction of looking absent: an operator sees no
release row whether the release is unadopted or the target is undeclared, and the two
are indistinguishable from the browser. `ai-orchestrator` still declares no target of
its own — that would be a `release-targets.toml` at its root, which nothing here
writes — so a plan of this repository earns a release row only as a consumer.

What 0.6.3's *reader* answered differently is one number — `timeline_schema_version`,
which it serves at 7 where 0.6.2 served 6, with every other byte of the timeline and
conversation responses identical on the runs compared. 0.6.5 — the release between
that one and 0.7.0 — leaves the timeline route exactly there and repairs
the **conversation** route: a two-party
dispatch's transcript is the agent's turns, where every release before it read the
supervisor's relayed side as rows of its own and served the conversation at twice its
length. Measured by serving one runs root from a 0.6.4 and a 0.6.5 reader at once,
`report-shape`'s worker conversation is 8 turns on 0.6.4 and 4 on 0.6.5. Each of the
four extra rows 0.6.4 served carried the *previous* turn's reply as its `user`, an empty
`assistant`, a null `model` and no usage at all, so an operator saw the agent's answer
again on the user side and the transcript ran at twice its length; 0.6.5 serves the
agent's side as the transcript and gives each of the four turns its own `model` —
`claude-opus-5` on that conversation, where 0.6.4 served null on every turn of it. The
accounting does not move, because the duplicate rows carried no usage: the per-turn
`costUsd` figures sum to the same total on both. 0.6.5 also
moved the reader's own linked `onepipeline`, which is what `/healthz` reports: 0.6.4
answered `0.7.3` and 0.6.5 answers `0.18.3`, still not
`config/onepipeline.version` and still not expected to be.
`tests/dag_ui/test_dag_ui_serving_e2e.py` holds this over the pair as an operator's
browser reads them — over HTTP — including that no span the read API serves for a real
recorded run carries a release. It renders nothing: that a bundle draws what the reader
serves is `onepipeline-ui`'s own tier to hold, and
`tests/dag_ui/test_no_browser_needed_e2e.py` holds this one to needing no browser. The
runs behind all of it are checked-in fixtures and cannot grow a release event, so what
fails the day a repository here declares a target is
`tests/e2e/test_release_adoption_in_force_e2e.py`, which asks every registered
identity — and that is when this page comes due.

### Which release *writes* what it reads

Those two pins say which reader answers. Neither says whether there is anything to
answer *with*, and that is a third pin: a run's turn transcripts are written by the
`oneagentgraph` **`onepipeline` links**, not by the one
`config/oneagentgraph.version` installs. onepipeline links it as a Rust library, so
the version in force is whatever that release's own build resolved — and the
installed wheel says which that is, without a network or a clone. `onepipeline-cli`
ships a CycloneDX SBOM under its `dist-info/sboms/`, declaring one version per
linked crate; on the adopted release that is **oneagentgraph 0.4.5**.

The session-conversation producer landed in oneagentgraph 0.3.3, so what put it in
force here was moving **`config/onepipeline.version`**, and installing a new
`oneagentgraph` alone had changed nothing:

```sh
grep -c oneharness-session runs/<run-id>/events.jsonl   # 0, for a whole release cycle
```

That is the failure this section exists to make cheap to recognise, because it
shows up as a route that renders and finds nothing behind it rather than as an
error. `tests/test_linked_libraries.py` now reads that SBOM on every gate run and
refuses a linked `oneagentgraph` below the producer release, so the silent version
of this cannot come back. It holds a **second** floor beside it, for the same class
of silence one layer in: 0.3.6 publishes the whole of a turn rather than an outline
— the tool result that answered a call, the live turn text, and the turn's own usage
— and under an older one this view renders a transcript with the tool results
missing from it and a cost of "Not reported", neither of which looks like a missing
producer. When a transcript surface is empty for a **new** run,
check the engine pin before the reader pins, and read the answer out of the run's
own journal: a member turn carries `labels.session` as `<stream>.<member>` —
`node-scope-….worker` — and an `oneharness-session` event carries the pointer and
its one artifact reference. `labels.session` is **not** `onevcs`'s session token,
which shares the key name and is spelled `s-<hex>`.

### What this host's own runs root costs to serve, and what it leaves out

Everything above is about which release answers. This is about serving **this host's
own** runs root — 454 recorded run roots — rather than a fixture tree, and it is
written down because the numbers are large enough to change how the view is used and
because the repair for them is in no published release yet.

**Which roots are served.** The reader serves every run root the adopted engine wrote
and none of the ones this repository's own pre-adoption implementation wrote. Measured
against that root: `onepipeline runs` surveys 385 run roots and names 70 more it
skipped, each with its reason; `GET /api/v2/runs?include_settled=true` answers 245, and
the 141 in the survey and not in that answer are exactly the 141 whose `launch.json` is
the pre-adoption shape — a `schema_version: 3` record with `channel_id`, `goal` and
`plan_name`, which the reader refuses with a `404 run_not_found` by id. **It reports
none of that**: those 141, and the 69 roots holding no `launch.json` at all, are absent
from the listing rather than named in it with a reason, so a run an operator cannot see
is indistinguishable from one the reader lost. `tests/e2e/test_legacy_runs_root_e2e.py`
is where that family's reader behaviour is held, and it holds the older shape — the one
with no `launch.json` — rather than this one.

**What that repaired, and it is the reason this host stopped serving a stand-in.** A
run launched from a plan-store project carries `"project": "<source>:<project>"` in its
`launch.json`, and `onepipeline-api` 0.6.4 refused that record outright: served from
one runs root at once, 0.6.4 answers `404 no recorded run` for `landed-claim`,
`aio-adopt-writeback` and `otg-tag-boundary` where 0.6.5 answers 200 with their
timelines and every conversation on them. That refusal is what a curated stand-in runs
root of rewritten launch records was standing in for on this host, and adopting 0.6.5
is what retired it.

**What a wait costs, and why the previous release made the view barely usable here.**
Through `onepipeline-api` 0.6.5 the reader surveyed the whole root on every request:
over this host's own root a first page of the run list answered in 17 to 40 seconds
*warm*, a run detail or a run-scoped timeline in about 20, and a browser — one page
load, one `/api/v2/events` subscription, one run list, then the selected run's detail
and timeline — sat on `Loading execution history…` for over a minute and a half before
showing anything. **0.7.0 bounds that** — and the adopted 0.9.0 keeps it, which is why the numbers below
are that release's rather than this pin's — and it is the difference between a view an
operator opens and one they avoid: on the same root the same request answers in
**0.09-0.17 s** warm, against 41 s on the first cold one; a run detail in
**0.01-0.32 s**; and a live run's run-scoped timeline in **0.14 s**. 0.7.0's
`telemetry_schema_version` was 15 where 0.6.5 served 14. What has *not* changed is the
page size: `limit` is capped at 50, so a several-hundred-run answer is still
cursor-paged.

**What an idle tab costs, and this is the one that was sharp.** Through 0.6.5, with
**one** idle `EventSource` connected — a browser tab left open, nothing clicked — the
reader held **98% of one core** over a 60-second measurement on this root, against 0%
with no subscriber and 0% on a one-run root: the survey ran on the stream's own interval
rather than on anything the tab asked for, so an operator who left the Observatory open
spent a core of this host on it. On 0.7.0 the same measurement over the same root reads
**0.00% of one core**, subscribed or not.

Both halves arrived together, in
[onepipeline-ui#46](https://github.com/nickderobertis/onepipeline-ui/pull/46), which is
also what moved the reader's own linked engine: `/healthz` answers `0.19.0` where 0.6.5
answered `0.18.3`. That was **past** the 0.18.4 `config/onepipeline.version` held the
engine CLI at then, and it is the reason the Observatory could be adopted while that pin
stayed put — the reader carries its own copy of the engine and reads runs with it,
whatever the CLI a dispatch runs is. That independence is what still holds now the pin
has moved past the reader's own engine instead. **What that release did
not touch is which roots are served** — the paragraph above still holds, and the 141
pre-adoption roots are still absent from the listing with no reason given.

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
