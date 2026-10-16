<!-- llmlint: ignore-file[instruction_layer_localized] Ownership of this subtree is routed: `.github/CODEOWNERS` is `* @nickderobertis`, which matches every path here as it does every sibling tier project under `tests/`, none of which carries an entry of its own; a per-directory line would restate that match rather than route anything. -->
# `tests/pre_push_provisioning`

The journey that runs the tracked `.githooks/pre-push` in a copy nothing provisioned, as a
publication's worktree is. It follows `tests/session_setup/AGENTS.md`'s rules for a
provisioning journey: one toolchain per module, built by the hook under test, offline,
touching nothing of this host's.

- **The copy is committed and pushed to an origin of its own**, so the hook resolves a
  base and finds no launch path changed, as a publication of a branch level with its
  base would.
- **The gate ends at a stand-in `llmlint`**, the published CLI whose next step is a paid
  judged turn; everything before it is real.
- **The key names the hook's scripts file by file**, and this project is separate from
  `session-setup` so an edit to the hook does not rebuild those toolchains.
