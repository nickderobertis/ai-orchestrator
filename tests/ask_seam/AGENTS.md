<!-- llmlint: ignore-file[instruction_layer_localized] Ownership of this subtree is routed: `.github/CODEOWNERS` is `* @nickderobertis`, which matches every path here as it does every sibling tier project under `tests/`, none of which carries an entry of its own; a per-directory line would restate that match rather than route anything. -->
# `tests/ask_seam`

The host-tool journeys over the seam a dispatched agent asks its manager through: the
real `scripts/ask-manager.sh` adapter, the installed engine's `onepipeline ask` it execs,
which raises every question under the bus configuration the run's launch recorded, and
the real launches that decide what a dispatch is given to ask with and which bus
configuration its run records.

- **These journeys prove this host's wiring, never the bus's behaviour.** What a question
  does once it is on the queue — its correlation, its wait, a timeout that carries no
  ruling, a listener re-armed rather than a question re-asked — is proven by the
  `onemessagebus` release's own suite. A journey here asks whether a real launch's
  dispatch, a real run's channel and the manager's own recipes reach that behaviour, and
  reads a channel only through the bus's verbs or `just channel-next`, never its files.

- **One journey, one Nx project, one key.** Every journey here runs the real host tools
  and most spend a real launch, so each is its own memoization unit: a directory of its
  own under this one holding its module and a `project.json` whose single `test` target is
  keyed on a named input that `project.json` declares, plus the `testSupport` of every
  test-support unit under `tests/support/` the project depends on. Together those name,
  file by file, exactly what the journey reads — its own module, the shared helpers it
  imports through the units' edges, and the host-tool paths it really opens or runs —
  and nothing wider: no `config/**/*`,
  `scripts/**/*` or `orchestrator/**/*`, no prose, and no helper or sibling journey it
  never opens, so an edit pays for a launch only where that launch would read the edit.
  `tests/nx_inputs.py` lists the journeys and `tests/test_nx_cache_scope.py` holds each
  key to that shape; `tests/conftest.py` fails a journey the moment it opens something its
  own key does not carry. A new journey is a new directory, a new row in that list and a
  new key measured from what it reads, with an edge to each unit its module imports, never a file added beside an existing module.
