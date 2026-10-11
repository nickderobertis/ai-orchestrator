# `tests/plan_tooling`

The host-tool journeys over this repository's plan surface: `just check-plan`, `just
review-plan`, `just plan`, the registered check script, the installed engine and a real
`oneharness run`, up to a whole launch driven to settlement.

- **One project per subject, each keyed on what it reads.** `check_and_review/`,
  `follow_ups/`, `plan_flow/` and `plan_copy/` are the `plan-tooling-check-and-review`,
  `-follow-ups`, `-plan-flow` and `-plan-copy` projects, and `tests/nx_inputs.py`'s
  `PLAN_TOOLING_PROJECTS` names each one's key and the trace it was measured from, so an
  edit one subject never reads re-runs none of its journeys. A subject holding journeys
  that build a *copy* of this checkout has a second target, `test-docs`, keyed on the
  whole workspace because copying the tracked tree is reading all of it; `reads_docs`
  marks exactly those journeys, chooses between the project's two targets, and never
  routes a test out of the project. A journey here that reads named files carries no
  marker: its project's key names them, and `tests/conftest.py` holds it to that key.
