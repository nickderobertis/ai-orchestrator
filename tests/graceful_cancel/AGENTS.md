<!-- llmlint: ignore-file[instruction_layer_localized] `.github/CODEOWNERS` has a wildcard owner for this subtree; another entry would not change ownership. -->
# `tests/graceful_cancel`

The journey over a `cancel` whose dispatch ends inside its grace period: a real launch,
the real channel reply and monitor, waited to the node's settlement. The arm a dispatch is
killed on stays in `tests/e2e/test_orchestrate_launch_e2e.py`. Before adding a file to
`gracefulCancelWorkspace`, confirm the tier reads it; a broad glob would charge every
unrelated edit for this real launch.
