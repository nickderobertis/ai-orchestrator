<!-- llmlint: ignore-file[instruction_layer_localized] Ownership of this subtree is routed: `.github/CODEOWNERS` is `* @nickderobertis`, which matches every path here as it does every sibling tier project under `tests/`, none of which carries an entry of its own; a per-directory line would restate that match rather than route anything. -->
# `tests/host_views`

The journeys over `just status` and `just host` — the two views a supervisor reads a run
and this host through, driven as real recipes over the installed engine.

- **One target, `host-views:test`, keyed on `hostViewsWorkspace`**, which names files
  rather than trees, measured from what the tier opens. One journey here spends a real
  `just orchestrate`, so a path the key names that nothing reads makes an unrelated edit
  pay for that launch. A launch path that starts reading a new file is a re-measurement,
  not a glob to widen.
- **A project rather than a marker tier, and no tier marker here.** These drive this
  repository's recipes and the engine's command surface for real, which is a cost `nx
  affected` can only keep off an unrelated edit where it is a separate project. What
  tiers a test here is where it lives: a `reads_recipes` marker fails, because that
  tier's key does not carry this directory, and a `reads_docs` one routes nothing,
  because this project has one target and its key is what every test here is held to.
- **`examples/**/*` is in the key for the real-launch control alone**, which copies the
  shipped records and launches `examples:scheduler-research` from the copy. It is the one
  prose the key carries, and it is data the journey reads rather than prose about this
  repository.
- **Reach a shared stand-in through `project_fixtures.helper`.** A path derived from this
  module's own `__file__` stops existing the moment the module moves, and a paid
  provider's stand-in that does not exist routes the turn to the real identity.
- **Derive an addition to `hostViewsWorkspace` from a read this suite performs, never
  from a sibling project's list.** The read guard in `tests/conftest.py` proves a key
  sufficient, never minimal.
