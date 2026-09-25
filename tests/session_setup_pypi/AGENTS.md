<!-- llmlint: ignore-file[instruction_layer_localized] Ownership of this subtree is routed: `.github/CODEOWNERS` is `* @nickderobertis`, which matches every path here as it does every sibling tier project under `tests/`, none of which carries an entry of its own; a per-directory line would restate that match rather than route anything. -->
# `tests/session_setup_pypi`

The journeys that run the real `scripts/session-setup.sh` in a fixture repository
`tests/e2e/provisioning.py` builds, where its real `uv sync` installs the adopted
published tools from PyPI: session setup's own behaviour, and what the installed engine
binary really links.

- **One target, `session-setup-pypi:test-pypi`, outside `just check`.** It reaches an
  outside service, so no selection the deterministic recipe makes runs it; `just test`
  and `just upgrade` do. It is a project rather than a second target of
  `tests/session_setup`, because Nx's edges and `nx affected` are per project.
- **Keyed on `sessionSetupPypiWorkspace` and the units it depends on.** Its own key names
  this directory less its prose, the test module it imports from `tests/`, and the engine
  pin; every
  file the fixture copies — the lock, the pins, the setup scripts — reaches it through the
  `tests/support/provisioning/` unit. The one module of `orchestrator/` in it is
  `orchestrator/root.py`, which every module here imports `REPO_ROOT` from.
