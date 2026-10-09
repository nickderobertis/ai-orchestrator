<!-- llmlint: ignore-file[instruction_layer_localized] Ownership of this subtree is routed: `.github/CODEOWNERS` is `* @nickderobertis`, which matches every path here as it does every sibling tier project under `tests/`, none of which carries an entry of its own; a per-directory line would restate that match rather than route anything. -->
# `tests/render_diagram`

- **A project of its own** because its journeys launch a real Chromium through the locked
  mermaid-cli, a cost `nx affected` keeps off an unrelated edit only where it is a separate
  project; its key is exactly what a render reads, which `scripts/render-diagram.sh`'s
  header lists.
- **A variation runs out of a scratch copy** of the files the recipe reads, its
  `node_modules` linked back to this checkout's install except the one package varied, so
  nothing in this checkout changes.
