<!-- llmlint: ignore-file[instruction_layer_localized] Ownership of this subtree is routed: `.github/CODEOWNERS` is `* @nickderobertis`, which matches every path here as it does every sibling tier project under `tests/`, none of which carries an entry of its own; a per-directory line would restate that match rather than route anything. -->
# `tests/project_store_race`

The two-process fixture-root isolation journey: two concurrent pytest sessions each create
one project through the pinned store (`orchestrator/plan_records.py`) in the
`test-fixtures` root its own session states, and one removes its copy while the other keeps
reading its own through the installed store CLI. Every read has to answer, which proves the
two processes never shared a root.

- **One target, `project-store-race:test`, keyed on `projectStoreRaceWorkspace`**: the
  writer, this directory, and the suite modules `tests/conftest.py` imports. Each journey
  pays for two whole pytest sessions; keeping it in its own project is what lets
  `nx affected` charge that only to a change of the writer or of these files, and not to
  every edit of `orchestrator/`.
