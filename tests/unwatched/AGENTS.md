# `tests/unwatched`

The journeys over `onepipeline unwatched` and the `Stop` hook that reads it.

- **One target, `unwatched:test`, keyed on `unwatchedWorkspace`**, which names files
  rather than trees: these journeys arm real watches, kill real processes, hold this
  checkout's project-environment lock and spend three real launches, so every path in
  the key they never read makes an unrelated edit pay for all of that. The set was
  measured from what the tier actually opens, not guessed, so a launch path that starts
  reading a new file is a re-measurement, not a glob to widen.
