<!-- llmlint: ignore-file[instruction_layer_localized] Ownership of this subtree is routed: `.github/CODEOWNERS` is `* @nickderobertis`, which matches every path here as it does every sibling tier project under `tests/`, none of which carries an entry of its own; a per-directory line would restate that match rather than route anything. -->
# `tests/session_setup`

<!-- llmlint: ignore-block[no_redundant_instruction_pointers] The task that made session setup the only provisioning requires this file to point at the one statement of that rule rather than restate it; the pointer names which of the root's sections a maintainer of these journeys has to keep true. -->
The journeys that prove the rule `AGENTS.md` states under "The checks": session setup,
and `just bootstrap` which runs it, provision a worktree and nothing else installs.
<!-- llmlint: ignore-end[no_redundant_instruction_pointers] -->

- **Each module builds one toolchain, through the setup or bootstrap under test and no
  other way**, in a copy of the tracked files that arrives with neither `.venv` nor
  `node_modules`: a copy that arrived provisioned could not show what setup provisions.
- **It touches nothing of this host's.** The copy keeps this host's home, so installs
  come from the host's caches, under `provisioning.NO_NETWORK`, which fails an install
  that would download rather than reaching an index. Tool directories, sibling list
  (`ORCHESTRATOR_REPOS_BOOTSTRAP_CHECKOUTS`), state and cache roots are its own, so no
  journey reaches this host's registered siblings, whose bootstrap none may run.
- **Detached jobs are waited out.** Sibling bootstraps and the host sweep outlive each
  setup by design; a journey waits on the ending each records, and signals none.
- **The key is named file by file**, never `scripts/**/*` or `config/**/*`: provisioning
  is the cost, so a key wider than the chain setup and bootstrap run makes an unrelated
  edit pay for it.
- **The pre-push hook's provisioning is `tests/pre_push_provisioning/`**, a project of
  its own, so an edit to the hook's scripts does not rebuild these toolchains.
- **The PyPI journeys are `tests/session_setup_pypi/`**, a project of their own, so an
  edit only they read does not select this one.
