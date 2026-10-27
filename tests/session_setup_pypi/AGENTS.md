<!-- llmlint: ignore-file[instruction_layer_localized] Ownership of this subtree is routed: `.github/CODEOWNERS` is `* @nickderobertis`, which matches every path here as it does every sibling tier project under `tests/`, none of which carries an entry of its own; a per-directory line would restate that match rather than route anything. -->
# `tests/session_setup_pypi`

The journeys that run the real `scripts/session-setup.sh` in a fixture repository
`tests/e2e/provisioning.py` builds, where its real `uv sync` installs the adopted
published tools from PyPI and its frozen `bun install` the engine and the read API from
npmjs.com: session setup's own behaviour, what each installed binary reports, and what
the installed engine binary really links.

- **One target, `session-setup-pypi:test-pypi`, outside `just check`.** It reaches an
  outside service, so no selection the deterministic recipe makes runs it; `just test`
  and `just upgrade` do. It is a project rather than a second target of
  `tests/session_setup`, because Nx's edges and `nx affected` are per project.
- **Keyed on `sessionSetupPypiWorkspace` and the units it depends on.** A file the
  fixture copies is declared on the `tests/support/provisioning/` unit, never on this
  key, so every journey that copies it is re-run when it moves.
