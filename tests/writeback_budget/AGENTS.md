# `tests/writeback_budget`

- **A copy is only killed by a live driver.** A journey here keeps a node's turn in flight
  for as long as it measures a copy; a run whose remaining nodes are parked or waiting on a
  person settles, and a held copy then lands however the deadline is set.
- **Prove it against the release before the landing, not just the adopted one.** This
  journey is evidence only while it fails there, at the fixed sixty-second floor.
