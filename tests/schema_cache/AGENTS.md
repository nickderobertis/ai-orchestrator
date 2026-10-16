<!-- llmlint: ignore-file[instruction_layer_localized] Ownership of this subtree is routed: `.github/CODEOWNERS` is `* @nickderobertis`, which matches every path here as it does every sibling tier project under `tests/`, none of which carries an entry of its own; a per-directory line would restate that match rather than route anything. -->
# `tests/schema_cache`

The journeys over the one schema cache `tests/onejudge_bundle.py` warms for a session:
real multi-worker pytest sessions over this checkout's conftest, each test starting a
pytest of its own, read through a temporary directory of the journey's own during and
after the session; and the two exits a session never takes, a failed warm and a forked
child of the warming process.

- **A project of its own** because each journey starts whole pytest sessions that import
  the conftest in every worker and nested process, a cost `nx affected` keeps off an
  unrelated edit only where it is a separate project; its key is the bundle module, the
  conftest it is seeded from, and this directory.
- **The probe is a test of this module**, run as itself in an ordinary session and as a
  reporting probe only under the variable its journeys set, so it proves the cache was
  handed to it either way.
