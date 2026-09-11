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
<!-- llmlint: ignore-block[instruction_layer_localized] One rule true of all four
projects, stated once. A nested file per project would state it four times and give it
four places to drift apart; a project whose rules are its own gets its own file, and
`tests/dag_ui/AGENTS.md` is that. -->
- **A host-tool journey belongs to its own project, selected by directory rather than
  by marker.** A journey that spawns the installed `onepipeline`, the `just` recipes, a
  real `oneharness run` or a whole launch costs what a host tool costs and is answered by
  a different set of files from the Python suite beside it, and `nx affected` can only
  tell two costs apart where they are two projects. So `tests/plan_tooling/`,
  `tests/ask_seam/`, `tests/unwatched/` and `tests/plan_store_install/` are each a
  project of their own, each keyed in `nx.json` on the files its journeys read and held
  to that key by `conftest.py` exactly as the recipe tier is held to its own;
  `tests/nx_inputs.py` says beside each key what it names and why. None declares a
  Python distribution: this repository is one uv workspace with one `pyproject.toml` and
  one `uv.lock`, and each is an Nx target over a directory of it. A journey that builds a
  **copy** of this checkout reads everything git tracks, so it is keyed on the whole
  workspace — by a `reads_docs` target of *its own* project, never by handing it to
  another project's tier: the directory decides which project pays, and a marker only
  decides which of that project's keys the payment is memoized on. A project with no
  such second target fails a prose read in `conftest.py` instead of routing it.
<!-- llmlint: ignore-end[instruction_layer_localized] -->
<!-- llmlint: ignore-block[no_redundant_instruction_pointers] The pointer below is to
one subsection rather than to the document: `agents_md_durable_and_terse` holds this
file to the durable constraint and sends a dropped question's signature and evidence
to a linked diagnostic document, and a reader of this bullet cannot find that
subsection from the root advertising `docs/orchestration.md` as a whole. -->
- **A hang of the `ask-seam` tier is one of two diagnosed things, and the queue tells
  them apart.** Its journeys spend real launches and then wait on `uv run` through the
  exclusive lock those launches hold on `<root>/.venv`, so `SHARED_TOOLCHAIN_GROUP` in
  `tests/e2e/nx_workspace.py` keeps the writers of that lock and the readers waiting on
  it on one xdist worker. What that constraint does not account for is the channel
  queue's own defect — an unlocked read-modify-write on `runs/<run-id>/channel/queue.json`
  by which a read of the channel landing over a worker's concurrent write destroys the
  worker's blocking question — and that is the engine's to fix rather than a constraint
  this suite can declare. So read a fresh hang of that shape by opening the queue before
  re-diagnosing the group constraint; what a dropped question leaves behind, and what is
  and is not established about its cause, is in [A question that was raised and then
  dropped](../docs/orchestration.md#a-question-that-was-raised-and-then-dropped).
<!-- llmlint: ignore-end[no_redundant_instruction_pointers] -->
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
