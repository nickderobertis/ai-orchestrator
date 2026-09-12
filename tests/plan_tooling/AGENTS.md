# `tests/plan_tooling`

The host-tool journeys over this repository's plan surface: `just check-plan`, `just
review-plan`, `just plan`, the registered check script, the installed engine and a real
`oneharness run`, up to a whole launch driven to settlement.

- **Two targets, keyed apart.** `plan-tooling:test` is memoized on
  `planToolingWorkspace` — the workspace minus the prose these journeys never open, so a
  `docs/` edit is free of them. `plan-tooling:test-docs` collects the journeys that build
  a *copy* of this checkout, keyed on the whole workspace because copying the tracked
  tree is reading all of it. `reads_docs` chooses between the two targets and never
  routes a test out of this project.
