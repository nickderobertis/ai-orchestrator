<!-- llmlint: ignore-file[instruction_layer_localized] Ownership of this subtree is routed: `.github/CODEOWNERS` is `* @nickderobertis`, which matches every path here as it does every sibling tier project under `tests/`, none of which carries an entry of its own; a per-directory line would restate that match rather than route anything. -->
# `tests/session_setup`

The journey over this checkout's own provisioning: the real `scripts/session-setup.sh`,
verifying the plan-store CLI the project lock installs at the version
`config/onetaskgraph.version` pins, creating the plan root a fresh checkout lacks, and
reaching `just repos-bootstrap` over stand-in siblings the journey registers through
`ORCHESTRATOR_REPOS_BOOTSTRAP_CHECKOUTS` — never over this host's real siblings, whose
bootstrap no journey may run.

- **One target, `session-setup:test`, keyed on `sessionSetupWorkspace`**, named file by
  file rather than as `scripts/**/*` or `config/**/*`: the journey re-provisions the
  project environment from the lock, so every glob wider than the script, the lock and
  the one pin it reads makes an unrelated edit pay for that.
- **It holds the shared toolchain.** A run of the real setup takes `uv`'s exclusive lock
  on this checkout's `.venv`, so the journey declares `shares_workspace_install` and is
  scheduled beside every other journey that does.
