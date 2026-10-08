# tests/AGENTS.md

Conventions for this repository's tests.

- **This suite proves the configuration layer** — the recipes, the wrapper scripts, the
  harness routing, the llmlint tier, the Nx cache keys, and the provisioning that
  installs the published CLIs. The engines those recipes delegate to are proven in their
  own repositories; nothing here re-proves them.
- **e2e (`tests/e2e/`) drives the real thing**: the real `just` recipes, the real wrapper
  scripts, the real `oneharness` CLI and its fallback chain, real Nx in real linked
  worktrees. Exactly two doubles are sanctioned — the paid model, and the published CLI
  a recipe delegates to, doubled below the seam under test. Never double a recipe, a
  wrapper script, or the shell they run in.
- **A delegated recipe's journey is its row in
  `tests/e2e/test_delegated_recipes_e2e.py`** — the `just` invocation and the one
  command line it must produce. Add the row with the recipe. A recipe whose answer moves
  with an `orchestrator/` module lives in the tier keyed on that module instead, on the
  same `tests/delegation_checkout.py` checkout: `just follow-ups`' row is in
  `tests/plan_tooling/test_follow_ups_recipe_e2e.py`.
- **Wait on a fact, and assert one.** Never sleep and then assume; wait for an observable
  state transition, and correlate fields from one event rather than from log-wide
  matches.
- **A test that reads this repository's prose declares `@pytest.mark.reads_docs`, and
  one that drives a recipe declares `@pytest.mark.reads_recipes`.** `conftest.py` fails
  an undeclared read, because the code-keyed tier would otherwise replay a stale verdict
  over a documentation edit.
- **A host-tool journey belongs to an Nx project of its own, selected by directory**, so
  an unrelated edit stops paying for it; `conftest.py` holds each project to its key, and
  `tests/nx_inputs.py` says what each key names and why.
- **A journey that hangs waiting on a `just` recipe or on a channel reply is one of two
  diagnosed things, and the channel queue tells them apart.** `uv run` waits on the
  exclusive `<root>/.venv` lock for as long as a writer holds it, so
  `SHARED_TOOLCHAIN_GROUP` in `tests/e2e/nx_workspace.py` keeps that lock's writers and
  readers on one xdist worker — one name, because `--dist loadgroup` co-locates only
  tests sharing a name, and nothing holds two Nx targets apart. A run's channel is a
  directory of `onemessagebus` queues the bus owns, so read it through the bus —
  `onemessagebus status --transport-dir runs/<run-id>/channel` — before re-diagnosing
  the group, never by opening its files: a question waiting or pending there is a
  waiter nobody answered, and nothing there is a lock. A waiter here looks through
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
  sweep; `tests/e2e/test_launch_walks_no_host_identity_e2e.py` holds it.
- **The environment a test runs in is the test's to state.** The autouse fixtures in
  `conftest.py` drop the dispatch's comparison base, ownership stamp, per-side harness
  selection, and oneharness history settings — inherited, the last queue every turn on
  a host-wide history index — and a test that means to exercise one sets it itself.
- Every recipe needs a real journey here before it is done.

## The journeys' project

The modules directly under `tests/e2e/` are `orchestrator-e2e`'s, and the unit and
drift-gate modules directly under `tests/` are `orchestrator`'s; `tests/nx_inputs.py` says
why each key is what it is.

- **Markers route a journey between `orchestrator-e2e:test`, `test-recipes` and
  `test-checkouts`**, as they route a unit test between the orchestrator project's
  targets. A `reads_docs` journey is collected by `orchestrator:test-docs` instead: a
  target keyed on the whole workspace would select the journeys for every diff.
- **A journey imports a unit or drift-gate module only through a test-support unit**
  carrying it under `tests/support/`, because only an edge selects the journeys when that
  module changes.
- **A unit or drift-gate test that opens a journey by path names it in
  `journeysTheUnitGatesRead`** in `tests/e2e/project.json`, or `orchestrator:test`
  replays over an edit to it; `tests/conftest.py` fails the read until it does.
- **A directory added at the repository root joins `e2eWorkspace`**, which names what
  it keeps; `tests/test_nx_cache_scope.py` fails until it does.

## Project boundaries

Every Nx project carries a tag one row below names, and every edge it declares or
Nx infers is to a project tagged with what that row allows.
`tests/test_project_boundaries.py` reads this table and Nx's own project graph, and fails on
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
