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
  command line it must produce. Add the row with the recipe.
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
  tests sharing a name, and nothing holds two Nx targets apart. An unlocked
  read-modify-write on `runs/<run-id>/channel/queue.json` can destroy a worker's
  blocking question. Read the queue before re-diagnosing the group.
- **A shared stand-in is reached through `project_fixtures.helper`, never through a test
  module's own `__file__`**: a paid provider's stand-in that does not exist is not a
  stand-in — the journey spends real turns while passing.
- **A test must not leave a background daemon behind.** `NX_DAEMON=false` is set
  session-wide; a test that signals a process group starts that process in one of its
  own.
- **The environment a test runs in is the test's to state.** The autouse fixtures in
  `conftest.py` drop the dispatch's comparison base, ownership stamp, and per-side
  harness selection; a test that means to exercise one sets it itself.
- Every recipe needs a real journey here before it is done.
