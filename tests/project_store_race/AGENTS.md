<!-- llmlint: ignore-file[instruction_layer_localized] Ownership of this subtree is routed: `.github/CODEOWNERS` is `* @nickderobertis`, which matches every path here as it does every sibling tier project under `tests/`, none of which carries an entry of its own; a per-directory line would restate that match rather than route anything. -->
# `tests/project_store_race`

The two-process replacement race over `orchestrator/project_store.py`: writer processes
rewrite one project's records for a fixed span of the clock while a reader process reads
the root back through the module's own reader, and every read has to return the record
before the replacement or the one after it — never an empty or partial one, and never a
stage. What the unit tests in `tests/test_project_store.py` hold about a record at rest —
the staged write, the failed rename, the sweep that leaves a peer's stage alone — is not
restated here.

- **One target, `project-store-race:test`, keyed on `projectStoreRaceWorkspace`**: the
  module under race, this directory, and the suite modules `tests/conftest.py` imports.
  The race is bounded by the clock rather than by a count, so it costs the same two
  seconds on an idle host and a loaded one; keeping it in its own project is what lets
  `nx affected` charge that only to a change of the store or of these files, and not to
  every edit of `orchestrator/`.
- **Writers are processes, never threads.** A stage is named for the writing process, so
  two writers of one destination in one process would never exercise the name.
- **Before-or-after is a real choice.** The writers alternate between two renderings of
  the same project, so a read that returns either is held to one of two exact contents
  rather than to a front-matter prefix a truncated body would also carry.
