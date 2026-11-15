# `tests/plan_tooling`

The host-tool journeys over this repository's plan stores: the follow-up recipes and the
host layer's routing of human tasks. The planning road is journeyed in `tests/e2e/planning/`.

- **One project per subject, each keyed on what it reads.** `comment_version/`,
  `follow_ups/` and `host_layer_routing/` are the `plan-tooling-comment-version`,
  `-follow-ups` and `-host-layer-routing` projects, and `tests/nx_inputs.py`'s
  `PLAN_TOOLING_PROJECTS` names each one's key and the trace it was measured from, so an
  edit one subject never reads re-runs none of its journeys. A subject holding journeys
  that build a *copy* of this checkout has a second target, `test-docs`, keyed on the
  whole workspace because copying the tracked tree is reading all of it; `reads_docs`
  marks exactly those journeys, chooses between the project's two targets, and never
  routes a test out of the project. A journey here that reads named files carries no
  marker: its project's key names them, and `tests/conftest.py` holds it to that key.
