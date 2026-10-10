<!-- llmlint: ignore-file[instruction_layer_localized] `.github/CODEOWNERS` has a wildcard owner for this subtree; another entry would not change ownership. -->
# `tests/e2e/toolchain_isolation`

The journeys that build a copy of this checkout with a `.venv` and `node_modules` of its
own: the proof that a toolchain writer and a reader overlap without sharing a toolchain,
within an xdist tier and across Nx tiers, and the Nx wrapper's self-heal reading the
pinned Nx. `toolchainIsolationWorkspace` names what building and driving such a copy
reads; the contract they prove is `tests/e2e/nx_workspace.py`'s. Every install here runs
under that module's `OFFLINE_INSTALLS`, so the project reaches no package index: the
copies' lockfiles are this checkout's, whose own installs already filled both caches.
