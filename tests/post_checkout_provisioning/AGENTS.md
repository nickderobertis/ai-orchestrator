<!-- llmlint: ignore-file[instruction_layer_localized] Ownership of this subtree is routed: `.github/CODEOWNERS` is `* @nickderobertis`, which matches every path here as it does every sibling tier project under `tests/`, none of which carries an entry of its own; a per-directory line would restate that match rather than route anything. -->
# `tests/post_checkout_provisioning`

The journey that runs the tracked `.githooks/post-checkout` through real git — a worktree
add, a branch checkout of an existing worktree, a file checkout — in a scratch clone of a
tree `tests/e2e/provisioning.py` builds. It follows `tests/session_setup/AGENTS.md`'s rules
for a provisioning journey: toolchains built by the hook under test, offline, touching
nothing of this host's.

- **Nothing is doubled but one setup on one branch.** The real setup exits only 0, 1 or 2,
  so the branch that proves the hook passes a status through unchanged carries a setup
  exiting 7; the real setup's own failure is driven on another branch.
- **Git reports a failed `post-checkout` as status 1**, so the hook's own status is read by
  running it as git does, in the checked-out tree with git's three arguments.
