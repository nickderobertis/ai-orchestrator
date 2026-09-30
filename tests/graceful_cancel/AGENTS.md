<!-- llmlint: ignore-file[instruction_layer_localized] `.github/CODEOWNERS` has a wildcard owner for this subtree; another entry would not change ownership. -->
# `tests/graceful_cancel`

The journey over a `cancel` whose dispatch stops when interrupted: a real launch, the real
channel reply and monitor, and the fake backend serving `oneharness interrupt`. The arm a
dispatch is killed on stays in `tests/e2e/test_orchestrate_launch_e2e.py`, on the backend
that refuses the interrupt. Before adding a file to `gracefulCancelWorkspace`, confirm the
tier reads it; a broad glob would charge every unrelated edit for this real launch.
