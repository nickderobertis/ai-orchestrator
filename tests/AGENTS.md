# tests/AGENTS.md

Conventions for this repo's tests.

- **What this suite proves is the configuration layer.** The recipes, the wrapper
  scripts, the harness routing, the llmlint tier, the Nx cache keys, and the
  provisioning that installs the published CLIs. The engines those recipes
  delegate to are proven in their own repositories; nothing here re-proves them.
- **e2e (`tests/e2e/`) drives the real thing.** The real `just` recipes, the real
  wrapper scripts, the real `oneharness` CLI and its fallback chain, real Nx in
  real linked worktrees, and a real `uv sync` from PyPI. Two doubles are
  sanctioned and no others: the paid model, and the published CLI a recipe
  delegates to — the boundary *below* the seam under test, doubled so the
  delegation is observable and so a journey never launches agents. Never double a
  recipe, a wrapper script, or the shell they run in.
- **A delegated recipe's journey is its row in the table.**
  `tests/e2e/test_delegated_recipes_e2e.py` holds the whole mapping in one place:
  a `just` invocation and the single command line it must produce. A recipe that
  starts naming a different verb, or dropping an argument on the way, fails there
  rather than in an operator's terminal. Add the row with the recipe.
- **Wait on a fact, and assert one.** A rendezvous that sleeps and then assumes the
  work has begun measures the machine rather than the code, and so does an assertion
  that only holds when the box was quick. Wait for an observable state transition,
  and correlate fields from the same event rather than independent log-wide matches.
- **A test that reads this repository's prose declares it.** `orchestrator:test` is
  keyed on the workspace minus its documentation, so an undeclared read would let a
  documentation edit replay a stale verdict. The autouse guard in `conftest.py`
  fails such a test; mark it `@pytest.mark.reads_docs` and it runs in the
  whole-workspace target of the project that owns it.
<!-- llmlint: ignore-block[instruction_layer_localized] One rule true of all three
projects, stated once. A nested file per project would state it three times and give it
three places to drift apart; a project whose rules are its own gets its own file, and
`tests/dag_ui/AGENTS.md` is that. -->
- **A host-tool journey belongs to its own project.** There are three, each selected by
  directory rather than by marker: `tests/plan_tooling/` is `plan-tooling`, over the
  plan surface, and `tests/ask_seam/` is `ask-seam`, over the seam a dispatched agent
  asks its manager through. `ask-seam` has one target rather than two, because nothing
  there reads this repository's prose; `askSeamWorkspace` is its key, and a prose read
  there fails in `conftest.py` instead of being routed to a target that does not exist.
  `tests/plan_store_install/` is `plan-store-install`, over the provisioning that
  installs the pinned plan-store CLI into a checkout's own `.venv/bin`, with only the
  archive fetch doubled; its key `planStoreInstallWorkspace` is the narrowest of the
  three, naming `config/onetaskgraph.version` and the two scripts that read it, because
  what it proves is what a moved pin installs rather than anything a plan does.
  The rest of this note is written about `plan-tooling` and is true of all three:
  `tests/plan_tooling/` is the Nx project `plan-tooling`, selected by directory
  rather than by marker: a journey there spawns the installed `onepipeline`, the
  `just` recipes, the registered check script and a real `oneharness run` — up to a
  whole launch driven to settlement — which is
  a different cost from the Python suite and is answered by a different set of
  files. `planToolingWorkspace` in `nx.json` is that set and `conftest.py` holds
  these tests to it, exactly as it holds the recipe tier to its own. It declares no
  Python distribution: this repository is one uv workspace with one `pyproject.toml`
  and one `uv.lock`, and this is an Nx target over a directory of it. A journey that
  builds a **copy** of this checkout reads everything git tracks, so it is keyed on the
  whole workspace instead — but by a target of *this* project, never by handing it to
  another project's tier: the directory decides which project pays, and a marker only
  decides which of that project's keys the payment is memoized on.
<!-- llmlint: ignore-end[instruction_layer_localized] -->
- **A hang of the `ask-seam` tier is one of two diagnosed things, and the queue tells
  them apart.** Its journeys spend real launches and then wait on `uv run` through the
  exclusive lock those launches hold on `<root>/.venv`, so `SHARED_TOOLCHAIN_GROUP` in
  `tests/e2e/nx_workspace.py` keeps the writers of that lock and the readers waiting on
  it on one xdist worker — scattered, they failed four consecutive publication gates on
  branches touching none of it. What that constraint does not account for is the
  channel queue's own defect: a read of the channel is an unlocked read-modify-write on
  `runs/<run-id>/channel/queue.json`, and one landing over a worker's concurrent write
  permanently destroys the worker's blocking question, which is then indistinguishable
  from a worker still waiting for an answer.
  So read a fresh hang of that shape as that defect rather than as the group constraint
  having slipped, and read the queue before re-diagnosing it.
- **A shared stand-in is reached through `project_fixtures.helper`, never through a
  test module's own `__file__`.** A module that derives the path itself names a file
  relative to wherever it currently sits, so moving it substitutes a path this
  checkout does not have — and a *paid provider's* stand-in that does not exist is not
  a stand-in: oneharness falls through to the real identity and the journey spends
  real turns while passing. `helper` lives beside the stand-ins and refuses a name
  this checkout does not carry, so that failure is an import error rather than a bill.
- **A test that drives a recipe declares that too.** `orchestrator:test-recipes` is
  keyed on the `justfile`, `scripts/**`, the root manifests, and the modules that
  collect those tests — much narrower, so the same guard is stricter about it: a
  `@pytest.mark.reads_recipes` test that opens anything else in this checkout fails
  by name. A new recipe journey adds its own module to `recipeWorkspace` in
  `nx.json`, or the tier replays a verdict recorded before it existed.
- **A test must not leave a background daemon behind.** `NX_DAEMON=false` is set for
  the whole session and `scripts/nx.sh` defaults it off for every caller; anything
  else a test starts in the background owes the same, and a test that signals a
  process group starts that process in one of its own.
- **The environment a test runs in is the test's to state.** Every worker verifies
  itself by running this suite from inside a dispatch, so the suite inherits that
  dispatch's comparison base, ownership stamp, and per-side harness selection. The
  autouse fixtures in `conftest.py` drop all three; a test that means to exercise
  one sets it itself.
- Every recipe needs a real journey here before it is done.
