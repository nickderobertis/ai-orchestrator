# `tests/ask_seam`

The host-tool journeys over the seam a dispatched agent asks its manager through: the
real `scripts/ask-manager.sh`, the real `onepipeline channel serve` it asks through, and
the real launches that decide what a dispatch is given to ask with.

- **One target, `ask-seam:test`, keyed on `askSeamWorkspace`**: what these journeys
  drive and read, minus the prose they never open and the e2e helpers they never import —
  named one by one rather than as a directory, so a journey added beside them does not
  silently start invalidating this tier. Every journey here spends a real launch, which
  is why nothing wider may key it and why there is no second, whole-workspace target.
