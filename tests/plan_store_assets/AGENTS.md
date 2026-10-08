<!-- llmlint: ignore-file[instruction_layer_localized] Ownership of this subtree is routed: `.github/CODEOWNERS` is `* @nickderobertis`, which matches every path here as it does every sibling tier project under `tests/`, none of which carries an entry of its own; a per-directory line would restate that match rather than route anything. -->
# `tests/plan_store_assets`

- **Local sources only, by design.** The hosted uploads — GitHub user attachments and
  Linear's file upload — are proven in onetaskgraph's own suite against loopback
  endpoints, and no dispatch here holds a board credential; what this project owns is that
  the plan-store CLI this host locks carries an asset through a copy at all.
- **A project of its own** so a change to the planning scripts, personas or harness never
  re-runs a journey whose only subject is the locked store.
- **Images are generated, never committed**, from seeded pseudo-random pixels: compression
  cannot shrink one below a real screenshot's size, and no two are alike, so a copy that
  swapped two images is caught by its hash.
