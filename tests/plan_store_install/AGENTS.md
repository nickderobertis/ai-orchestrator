# `tests/plan_store_install`

The host-tool journeys over this checkout's own plan-store provisioning: the real
`scripts/onetaskgraph-install.sh`, the real `install_onetaskgraph` it sources, and the
lock they serialize on.

- **One target, `plan-store-install:test`, keyed on `planStoreInstallWorkspace`**, named
  file by file rather than as `scripts/**/*` or `config/**/*`: these journeys race real
  processes for a real lock and install a real release archive over and over, so every
  glob wider than the two scripts and the one pin they read makes an unrelated edit pay
  for that.
