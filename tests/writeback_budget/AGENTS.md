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
- **A pre-landing proof has to keep the engine it installed.** The launch wrapper runs
  the engine under `uv run`, which re-syncs the venv to the lock — the pinned release —
  before every launch, so an engine installed by hand is replaced silently and a journey
  meant to fail on the older release passes on the pin instead; and a dispatch inherits a
  `VIRTUAL_ENV` naming the canonical checkout's venv, so an install that does not name
  this checkout's lands in somebody else's. Read the engine the run really wrote with —
  the projection record's `schema_version` says which — before believing either verdict.
