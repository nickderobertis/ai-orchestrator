# DAG Observatory

The live and historical view of orchestrated DAG execution — which also
**supervises** it, stopping, adopting, replying to and shutting down runs, each
behind the engine's own authority and the acting session the server runs as — is
[`onepipeline-ui`](https://github.com/nickderobertis/onepipeline-ui). It is not
built in this repository, and since `onepipeline-api serve --ui` it is not even
composed here: the design record, the component choices, the schema validation, and
the browser tier that photographs it all live there, beside the code they describe,
and one published binary serves the view and its data together. This page is the
operational half — how that binary is started here, and what each of the two recipes
around it does.

It is not a read-only view, and that changes what starting it means rather than
only what it shows: the API wraps every post-launch verb, the browser performs each
one as **one acting session**, and an adoption made from it is driven by the engine
the reader links. [Supervising from the
browser](#supervising-from-the-browser) is that half; start there before acting from
a tab, because two of those three sentences are about *whose* run and *which* engine.

## The one published binary

**The view and its data come out of one process.** The `onepipeline-api-cli` wheel,
installed by session setup at the release `config/onepipeline-ui.version` declares,
provides one command, `onepipeline-api serve`. It wraps the onepipeline SDK and serves
the `/api/v2/...` contract plus `/healthz`; with **`--ui`** it also serves the DAG
Observatory **built into that same binary** — the view of its own release — at `/`,
answering every path the API does not own with the bundle's `index.html` so a deep link
opens. That is what makes the arrangement a browser requires its own rather than this
repository's: the bundle asks for `/api/v2/...` relative to wherever it was served from
and declares no API host, and the reader sends no CORS headers, so the two have to
share an origin. They do, inside the binary.

Which flags it takes is the command's own to say — `onepipeline-api serve --help`, and
nothing here restates it; the three this host supplies a default for are under [Run
it](#run-it) below, because those defaults are this repository's.

**The `onepipeline-ui` npm package is still installed, and deliberately so.** It is the
same built bundle published as a package, pinned to the same release in `package.json`
and installed by `scripts/workspace-install.sh`. Nothing serves it — that is the point,
and it is why a reader arriving later should not take it for cruft. It is a test-time
reference and nothing else: `tests/dag_ui/test_dag_ui_serving_e2e.py` holds what the
binary serves — the `index.html` and the script asset it loads — byte for byte to that
installed package, having first held the package's own version to
`config/onepipeline-ui.version`. It is an artifact of the release rather than a copy
of what was served, which is what makes it an independent reference at all.

That comparison is older than this arrangement but only now means anything. Through the
proxy the journey compared what was served against the very directory the proxy served
it out of, which no server could fail; the binary carries its own copy of the view, so
the same comparison is now a cross-check between two artifacts of one release and fails
if they are not. The asset half of it is new — it used to assert only that the script
was longer than a thousand bytes.

## Run it

```sh
just bootstrap
just dag-ui             # the view and its data, on one origin
```

Open the address it prints. The server names the runs root it is serving and the
address it bound on its first line.

`just telemetry-server` is the same command without `--ui`, for the read API alone.
Every flag either recipe takes is the published verb's own, forwarded untouched; what
`scripts/telemetry-server.sh` supplies is three defaults, each from the one source this
host keeps it in:

* **`--runs-root`** — the runs directory `just runs` and `just status` read
  (`ONEPIPELINE_RUNS_DIR`, or `runs`), so the view serves the runs the planner is
  already looking at.
* **`--bind`** — the address `config/read-api.address` holds, which is the one source
  both recipes bind from. Leaving the published CLI's own default to stand there would
  restate the address in a second place.
* **`--session`** — who the browser acts as, covered below.

A caller who spells any of the three owns it whole and keeps it:
`just dag-ui --runs-root /elsewhere --bind 0.0.0.0:9000` serves that root on that
address.

## Supervising from the browser

**Every post-launch verb is a route now, and the view offers all of them.** The
run page carries the channel and its reply composer, `attest`, `stop`, `adopt`,
`shutdown` ([below](#shutting-runs-down-from-the-browser)), a held `watch`, the
unwatched badge, and the rendered reads (`status`, `results`, `goals`,
`transcript`, `telemetry`, `host`); a landing project list and per-project page
replace the flat list of run ids as the view's front door; and an **Agents**
panel lists every oneharness session each of a run's dispatches opened. What the
browser never does is launch: a plan is authored and launched from a terminal, and
everything here is *post*-launch. Which is also why **a planning run's channel is
answerable from the browser like any other** — `just plan`'s two launches are
ordinary runs with ordinary channels, and a reply is the same post-launch route
whatever the run is planning.

**Who it acts as is one session, and this host names it.**
`scripts/telemetry-server.sh` sources `scripts/launcher-session.sh` — the same
ladder every `onepipeline` recipe uses — and passes the result as `--session`, so
the browser's stop is the stop of the manager who started the server. Three
consequences to hold:

* **A run another session owns is refused**, `409 not_owner`, naming the owner as
  the engine names it to a stranger (`[codex:160c290a]`, never the raw session).
  That is the ownership doctrine in `AGENTS.md` reaching the browser rather than a
  second rule, and a forced stop sits behind it as a deliberate second step.
* **An unattributed server owns nothing.** Started where this host can name no
  session it is refused *every* stop it does not force, and `GET /api/v2/unwatched`
  reports no run — which reads from a tab as a quiet host rather than as a server
  that cannot say who it is. The recipe passes a session whenever one resolves and
  omits it rather than inventing one when none does.
* **A tab is not a watch.** The Watch toggle holds `GET .../watch` for as long as
  it is on, and the server is the run's registered watcher only while it is held — so
  it dies with the tab, and a launch still owes an armed `just watch` that outlives the
  turn that started it.

`tests/dag_ui/test_dag_ui_serving_e2e.py` drives the grouping and both halves of
the identity rule on the served origin — the stranger's refusal naming the owner, and
the same stop reaching past ownership when the server acts as the run's own recorded
launcher — because the refusal on its own is equally satisfied by a server with no
session at all. Each of those is a `POST` to the same origin the view was served from,
which is the arrangement an operator acts through.

**An adopt from the browser is the one that changes which engine runs a dispatch.**
`POST .../adopt` retains **this API binary** at its own driver verb, so the adopted
run is driven by the engine `onepipeline-ui` links — not by the CLI
`config/onepipeline.version` installs. The driver runs in a process group of its
own, survives the server being stopped or restarted, and is read back off the run
record like any other. So on this host `config/onepipeline-ui.version` is a pin that
**can** govern a dispatch, which is why the suite holds the two pins to linking one
engine — see [Which release is answering](#which-release-is-answering) for the gate and
for the one question it leaves to `/healthz`.

### Shutting runs down from the browser

The view can shut down the work it shows, through **the same verb `just shutdown`
runs and with the same authority**: the API calls the engine's own shutdown seam as
the acting session above, and nothing about the interrupt, the wait, the kill or the
push exists in the view or in this repository.
<!-- llmlint: ignore[no_redundant_instruction_pointers] The task that added this section requires pointing a reader at the supervision guidance for what a soft shutdown does rather than restating it here, and this page is not auto-loaded: a reader who opened it for the browser control has not read that guidance. -->
What a soft shutdown does to a run —
and why it is not a stop — is `AGENTS.md`'s supervision guidance under `just
shutdown`; `just shutdown` is the terminal form of the same three scopes.

* **Shut down this run** is a control on a run's view, beside its stop and adopt,
  and is `POST /api/v2/runs/{run}/shutdown`.
* **Shut down all my runs** and **Shut down the entire host** are controls on the
  runs listing, and are `POST /api/v2/shutdown` with `scope` `mine` or `host`.

Each opens a **confirm dialog, and nothing is sent until it is confirmed**. Before
anything is sent the dialog names exactly which runs it will act on, by run id, and
who owns each — this session's runs told apart from every other by the acting
session's key, which `GET /api/v2/unwatched` serves as `session_key`. *All my runs*
acts on this session's runs and no other's. *The entire host* says plainly that it
acts on runs **other sessions own**, over their owners, and lists them apart from
this session's. A run another session owns is refused under the run scope as a stop
is, `409 not_owner`, before anything is signalled.

The dialog offers the **grace**, defaulting to the engine's ten minutes, and a
**force** option that is never the default and says what it gives up: it skips the
interrupt and the wait, so whatever a worker had not committed is lost.

**The report it shows is the product, so read it.** A shutdown waits out its grace
with the request open and the control showing its progress; its answer is the
engine's own report — per run, each dispatch and how it ended, the teardown, each
branch and where it went, and the host's other unpublished branches it did not push.
One the engine says was not complete answers `200` with that report and reads
**Shutdown incomplete**, never as a success; a request that lost its answer reads as
lost, since the engine may still be carrying it out.
`tests/dag_ui/test_dag_ui_serving_e2e.py` holds that the reader the two recipes serve
answers both shutdown routes and refuses what it should, without shutting anything
down. That the *view* carries these controls and draws this dialog is
`onepipeline-ui`'s own tier, which renders them in a browser this repository does not
provision; what every promise above is held to here is that repository's published
`docs/contract.md`, reconciled at the pinned tag by `tests/test_ui_api_contract.py`.

## Which release is answering

Moving `config/onepipeline-ui.version` installs a release; it does not put one in
front of an operator. The binary is loaded once, at start: a `just dag-ui` left
running from before a bump goes on serving the reader and the view it loaded, so
**an adoption reaches a browser only after it is restarted**. The two halves can no
longer be apart from each other — one binary carries both — so what is left to ask is
which binary is answering.

`/healthz` is what answers the question without guessing. The read API reports its
own liveness *and* the `onepipeline` release it links:

```sh
curl -s http://127.0.0.1:8765/healthz
{"status":"ok","onepipeline_version":"0.44.2"}
```

That release is the reader's own, and it is a **different adoption** from
`config/onepipeline.version` — this host pins the engine CLI and this reader
separately, and the reader links whatever its release was built against. What the
field is for is being able to say which engine is answering rather than assuming it.

**The two are kept level now, and the reason is an adopt.** They used to be free to
differ in either direction, because a reader that only read runs constrained nothing
about what a dispatch ran. An adoption made from the browser is not a read: `POST
/api/v2/runs/{run}/adopt` retains the API binary at its own driver verb, so the run it
revives is driven by the engine **this reader** links, and `config/onepipeline-ui.version`
is from then on a pin that can govern a dispatch on this host. So the two are held
level, and **not by an operator remembering to check**:
`tests/test_linked_libraries.py::test_the_ui_api_links_the_engine_this_host_pins`
reads the engine out of the adopted read-API wheel's own bill of materials and fails
when it is not the release `config/onepipeline.version` names, so a bump that moves one
alone fails on this host rather than at whatever a browser adoption then drives.

**They are not level today, by one declared exception.** `config/onepipeline.version`
reads 0.44.4, and the adopted `onepipeline-ui` 0.12.1 — the newest release there is —
links onepipeline 0.44.2. The engine pin moved anyway, because 0.44.4 is the release that
labels every session a node opens with its run, node and launching session; the gate
reads the pair through `DECLARED_UI_ENGINE_DIVERGENCE` in the same module, which is
satisfied only while the two are exactly 0.44.2 and 0.44.4 and fails, naming itself, the
moment either moves. **Until an `onepipeline-ui` release links onepipeline 0.44.4, do not
adopt a run from the browser**: the adopt would drive that run with the reader's own
0.44.2, an engine this host did not pin, while every other dispatch on this host runs
0.44.4. Adopt through the launcher, `just orchestrate --adopt <run-id>`, until the
declaration retires. What `/healthz` is for from here is the question that gate cannot
answer — which release is answering **on this port right now**, since both pieces load
once at start and a server left running from before a bump goes on serving what it
loaded.

The pair has been apart before, and that history is worth keeping because it says what
the old freedom cost. The reader has been moved with the engine because a reader linking
an older onejudge than the one writing a run's reports refuses a newer report schema and
renders no transcript. Through an earlier adoption the reader linked onepipeline 0.19.0
while the CLI a dispatch ran was nine minor releases ahead. It was the reverse for two
adoptions — the engine pin was held at 0.18.4 for a settlement write-back defect that had
nothing to do with reading runs, and the Observatory was adopted anyway because the
reader carries its own engine. None of those readers could drive a run from a browser;
this one can, which is what ended the freedom rather than any of them.

`tests/dag_ui/test_dag_ui_serving_e2e.py` holds a freshly started server to the
adopted release from that same served surface: the view handed back is byte-for-byte
the npm bundle installed at `config/onepipeline-ui.version`, and the reader answering
links the engine the adopted wheel links. So a bump that installs one release and
serves another fails there instead of being noticed by a person.

### What the adopted view renders, and what it has nothing to render

**`onepipeline-ui` 0.12.1**, the release `config/onepipeline-ui.version` pins, carries
what made this a supervising surface: the project list and per-project page are the
landing view, the run page carries the channel with its byte-for-byte reply composer,
`attest`, `stop` with the owner-naming refusal, `adopt`, a held `watch` with its
unwatched badge, and the rendered reads, and an **Agents** panel lists every oneharness
session a run's dispatches opened, per run, per node and per project. The whole of that
is [Supervising from the browser](#supervising-from-the-browser) above; what belongs
here is what the view has to *render* and may find nothing behind.

It still carries what 0.6.3 added: it shows which release carried each landed node,
alongside every release event.
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
`tests/dag_ui/test_dag_ui_serving_e2e.py` holds this on the served origin as an
operator's browser reads it — over HTTP — including that no span the read API serves for
a real recorded run carries a release. It renders nothing, and it needs no browser to
run: that a bundle draws what the reader serves is `onepipeline-ui`'s own tier to hold,
and a browser in a check tier here would be a browser on every publication's merge path,
which nothing on this host provisions. The
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
linked crate; on the adopted release that is **oneagentgraph 0.4.10**.

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
showing anything. **0.7.0 bounds that** — and the adopted 0.12.1 keeps it for the run
list, which is why the numbers below are read off that route — and it is the difference
between a view an operator opens and one they avoid: on the same root the same request
answers in **0.03-0.45 s** warm on the adopted release, against 76 s on the first cold
one, measured over this host's root at 634 run roots; the figures 0.7.0 was first read
at were **0.09-0.17 s** warm against 41 s cold, a run detail in **0.01-0.32 s**, and a
live run's run-scoped timeline in **0.14 s**. 0.7.0's `telemetry_schema_version` was 15
where 0.6.5 served 14. What has *not* changed is the page size: `limit` is capped at 50,
so a several-hundred-run answer is still cursor-paged.

**The grouped listing is not bounded, and it is the view's landing page.** This is the
one reading to take away before opening a tab against this host's own root.
`GET /api/v2/projects` answered in **21 to 45 seconds on every request** — 44.5 s, then
21.2 s, then 36.1 s on three consecutive reads of one warm server — over the same 634
run roots, which it grouped into 164 groups carrying 526 runs, while
`GET /api/v2/runs` on the same server answered in 0.45 s. So the route the app now opens
on costs what the whole view cost through 0.6.5, and for the same reason: it surveys the
store rather than a page of it, and `limit` does not reach it. Two consequences. Serving
**this host's own root** to a browser, expect the landing view to sit there and the flat
run list (`?list=runs`) to be the fast way in; serving a fixture or a small root, the
difference does not arise. And nothing here can fix it — the repair is
`onepipeline-ui`'s, the way the flat list's was — so read a slow landing page as this
paragraph rather than as a dead server, and read `/healthz`, which answers instantly,
to tell the two apart.

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

In `onepipeline-ui`, with the `dag-ui-screens` recipe of that repository — there, not
here: no recipe of this justfile photographs anything. The gallery is a development
tool of the repository that builds the view: it has the per-surface waits and the
fixture server this repository cannot reach, and this repository does not iterate on
the view. What is here instead is `just dag-ui` against a real runs root, which is the
surface an operator reads and the one these pages document.
