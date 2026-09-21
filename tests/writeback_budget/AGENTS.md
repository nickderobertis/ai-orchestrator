<!-- llmlint: ignore-file[instruction_layer_localized] Ownership of this subtree is routed: `.github/CODEOWNERS` is `* @nickderobertis`, which matches every path here as it does every sibling tier project under `tests/`, none of which carries an entry of its own; a per-directory line would restate that match rather than route anything. -->
# `tests/writeback_budget`

- **A copy is only killed by a live driver.** A journey here keeps a node's turn in flight
  for as long as it measures a copy; a run whose remaining nodes are parked or waiting on a
  person settles, and a held copy then lands however the deadline is set.
- **Prove it against the release before the landing, not just the adopted one.** This
  journey is evidence only while it fails there, at the fixed sixty-second floor.
- **A cancel settles on the journal, not in the views.** `just status` and `just results`
  render a cancelled running node `parked`, because the park outranks the settlement; the
  `node-settled cancelled` a journey waits for before it retries that node is on
  `just monitor <run> --all`. A `retry` or `requeue` sent before it is refused for the
  dispatch still in flight, and the stand-in takes no redirection, so name the run's own
  `ONEPIPELINE_CANCEL_GRACE_SECONDS` (`launched(..., cancel_grace_seconds=)`) or wait the
  shipped five minutes out.
- **Keep a driver alive across a cancel with a root of its own.** A run whose remaining
  nodes are all parked settles and its driver lets go — which is how a run is handed to
  `just orchestrate --adopt`, and also how a journey that parks its only running node
  loses the driver it meant to watch. `launched(..., independent_nodes=)` holds a second
  root beside the one under test.
- **A `retry` or `add` stating a novel task spends a judged reviewer turn** before the bus
  appends it, on the stand-in codex the bench already puts at the `oneharness` seam, which
  answers no verdict unless `FAKE_CODEX_ANSWERS` scripts one. A sibling's envelope may
  pass on the `.cache/envelope-passes` a previous run left; a new envelope never does.
<!-- llmlint: ignore-block[determinism_vs_judgment] ai-orchestrator#1217 rejected a recipe that installs and restores an engine, because it mutates the environment every run on this host shares; the steps stay an operator's, and the launch prints the pair they are judged by. -->
<!-- llmlint: ignore-block[agents_md_durable_and_terse] ai-orchestrator#1217's accepted fix keeps these as operator steps documented here, written in terms of the line the launch prints; naming that line and the four steps is the whole of the instruction, and a pointer elsewhere would be a second place to keep them. -->
- **A pre-landing proof against another engine is read off the pair the launch prints.**
  Every `start` and `adopt` through `scripts/onepipeline.sh` writes one line to stderr:
  `onepipeline: engine requested <pin> (config/onepipeline.version), about to run
  <reported> (<binary>)`. Nothing installs, restores, or refuses a mismatched pair — which
  pair is expected is the manager's call. By hand, from the checkout root:
  1. `uv pip install --python .venv/bin/python onepipeline-cli==<version>`.
  2. Launch with `UV_NO_SYNC=1`: the design-approval gate ahead of `start` is a `uv run`,
     which re-syncs the pin.
  3. `about to run` differing from `requested` is the proof on the installed release; the
     two agreeing means the install did not survive and the verdict says nothing about it.
  4. `just bootstrap` afterwards puts the pin back.

  The projection record's `schema_version` is the independent reading of which engine a
  run wrote with.
<!-- llmlint: ignore-end[agents_md_durable_and_terse] -->
<!-- llmlint: ignore-end[determinism_vs_judgment] -->
