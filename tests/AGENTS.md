# tests/AGENTS.md

Conventions for this repository's tests.

- **This suite proves the configuration layer** — the recipes, the wrapper scripts, the
  harness routing, the llmlint tier, the Nx cache keys, and the provisioning that
  installs the published CLIs. The engines those recipes delegate to are proven in their
  own repositories; nothing here re-proves them. Nx is one: the keys are held by static
  scans of the configuration, and which change selects, re-runs or replays which target
  is Nx's behaviour over it, which no test here drives real Nx to assert.
- **e2e (`tests/e2e/`) drives the real thing**: the real `just` recipes, the real wrapper
  scripts, the real `oneharness` CLI and its fallback chain, real Nx in copies of this
  checkout. Exactly two doubles are sanctioned — the paid model, and the published CLI
  a recipe delegates to, doubled below the seam under test. Never double a recipe, a
  wrapper script, or the shell they run in.
- **A delegated recipe's journey is its row in
  `tests/e2e/registry_and_recipes/test_delegated_recipes_e2e.py`** — the `just` invocation and the one
  command line it must produce. Add the row with the recipe. A recipe whose answer moves
  with an `orchestrator/` module lives in the tier keyed on that module instead, on the
  same `tests/delegation_checkout.py` checkout: `just follow-ups`' row is in
  `tests/plan_tooling/follow_ups/test_follow_ups_recipe_e2e.py`.
- **Wait on a fact, and assert one.** Never sleep and then assume; wait for an observable
  state transition, and correlate fields from one event rather than from log-wide
  matches.
- **A unit test directly under `tests/` that reads this repository's prose declares
  `@pytest.mark.reads_docs`, and one that drives a recipe declares
  `@pytest.mark.reads_recipes`.** `conftest.py` fails an undeclared read, because the
  code-keyed tier would otherwise replay a stale verdict over a documentation edit. In a
  directory-owned project `reads_docs` marks only a test that copies this checkout or
  scans every tracked document, collected by that project's whole-workspace `test-docs`
  target; a test there reading named prose carries no marker, and its project's key names
  what it reads. A drift gate — prose held to the code it describes — belongs in the
  subject project of its own under `tests/` (`tests/dispatch_bar/`,
  `tests/manager_guidance/`, `tests/plan_guidance/`, `tests/release_pins/`,
  `tests/family_scans/`, `tests/tree_scans/`), never in `orchestrator:test-docs`, which of
  the modules directly under `tests/` keeps only `tests/test_dispatch_appendix.py`, for the
  reason at its declaration.
- **A host-tool journey belongs to an Nx project of its own, selected by directory**, so
  an unrelated edit stops paying for it; `conftest.py` holds each project to its key, and
  `tests/nx_inputs.py` says what each key names and why.
- **A test whose subject is a process- or machine-wide resource declares that as a
  scheduling constraint** — never a loosened assertion, and never a solo re-proof by
  hand. This checkout's toolchain is not one: a test needing a mutable copy of this
  checkout runs it on this checkout's install through `share_this_checkouts_install` in
  `tests/e2e/nx_workspace.py`, by symlinks alone, and only the provisioning journeys
  build a toolchain of their own, through the setup under test. So an xdist group names
  one module's shared fixture, never a toolchain. Read a wait that *expires* as something
  holding a lock and a wait that merely lengthens as load.
- **A journey that hangs waiting on a `just` recipe or on a channel reply is read
  through its channel queue first, and not as a toolchain lock.** `uv run` holds the
  `<root>/.venv` lock only while it checks or syncs the environment, and no test syncs
  this checkout's: a copy shares its install through `tests/e2e/nx_workspace.py`'s
  symlinks and syncs nothing. A run's channel is a directory of
  `onemessagebus` queues the bus owns, so read it through the bus —
  `onemessagebus status --transport-dir runs/<run-id>/channel` — never by opening its
  files: a question waiting or pending there is a waiter nobody answered, and nothing
  there is a lock. A waiter here looks through
  `planner_channel.queue_may_hand_something_out`, which asks `status` and claims
  nothing, and reads through `channel-next` only once it holds something, because that
  verb claims what it hands out.
- **A shared stand-in is reached through `project_fixtures.helper`, never through a test
  module's own `__file__`**: a paid provider's stand-in that does not exist is not a
  stand-in — the journey spends real turns while passing.
- **A test must not leave a background daemon behind.** `NX_DAEMON=false` is set
  session-wide; a test that signals a process group starts that process in one of its
  own.
- **No process a test starts is handed this host's registered identities.** The
  `ONEVCS_HOME` every process inherits registers nothing, because a launched driver's idle
  pass and `onevcs sweep` walk every identity a registry names against its real origin
  and can retire branches there. A read of a repository this host registers names
  `onevcs_state_snapshot.host_registry()` for that one call, never for a launch or a
  sweep; `tests/e2e/launch_monitor_observer/test_launch_walks_no_host_identity_e2e.py` holds it.
- **The environment a test runs in is the test's to state.** The autouse fixtures in
  `conftest.py` drop the dispatch's comparison base, ownership stamp, per-side harness
  selection, and oneharness history settings — inherited, the last queue every turn on
  a host-wide history index — and a test that means to exercise one sets it itself.
- Every recipe needs a real journey here before it is done.

## The journeys' projects

Every journey module under `tests/e2e/` belongs to a directory-owned project — one
directory per family of journeys, keyed on what that family reads — the unit modules
directly under `tests/` are `orchestrator`'s, and each drift-gate subject is a project of
its own in a directory under `tests/`. The journey modules left directly under `tests/e2e/`
are `e2e-root`'s, which names each one; the other modules there are shared helpers,
carried by test-support units. `E2E_JOURNEYS` in `tests/nx_inputs.py` lists the journey
projects, and that module says how each key was derived.

- **A journey goes in the family whose key covers what it reads**, child processes
  included: a module's imports come from its project's test-support edges, and what the
  scripts it runs open comes from a trace of that project's command. A journey that
  copies this checkout reads every tracked file, so it goes in `workspace-copies`, marked
  `reads_docs`, whose `test-docs` target is the one keyed on the whole workspace.
- **A `reads_checkouts` journey runs in its project's uncached `test-checkouts` target**;
  a project that holds one declares that target, with `inputs` naming what its journeys
  read of this checkout. Uncached is not undeclared: `tests/conftest.py` holds those
  journeys to that declaration, and the drift gates hold it to their imports and to the
  `project.json` files their guard opens.
- **A journey imports a unit or drift-gate module only through a test-support unit**
  carrying it under `tests/support/`, because only an edge selects the journeys when that
  module changes.
- **A unit or drift-gate test that opens a journey by path names it in its project's
  `journeysTheUnitGatesRead`**, or `orchestrator:test` replays over an edit to it;
  `tests/conftest.py` fails the read until it does.
- **A journey project that measures `orchestrator/` writes `.coverage.<project>`** and
  is in `orchestrator:coverage`'s combine; `tests/tree_scans/test_nx_cache_scope.py` reads
  that set off the declarations.

## Project boundaries

Every Nx project carries a tag one row below names, and every edge it declares or
Nx infers is to a project tagged with what that row allows.
`tests/tree_scans/test_project_boundaries.py` reads this table and Nx's own project graph, and fails on
a project no row names or an edge no row allows, naming the project, the edge and the rule.

| A project tagged | may depend only on projects tagged |
| --- | --- |
| `type:test-support` | `type:test-support` |
| `type:tests` | `type:test-support` |
| `scope:orchestrator` | `type:test-support` |
| `scope:workspace` | nothing |

So no project depends on a project that collects tests, and a test-support unit depends
only on other units. A unit is `type:test-support` under `tests/support/`; a project
collecting tests is `type:tests`; the `orchestrator` project depends on units because its
own test targets import them.
