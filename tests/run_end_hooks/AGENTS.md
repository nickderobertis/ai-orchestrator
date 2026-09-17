<!-- llmlint: ignore-file[instruction_layer_localized] Ownership of this subtree is routed: `.github/CODEOWNERS` is `* @nickderobertis`, which matches every path here as it does every sibling tier project under `tests/`, none of which carries an entry of its own; a per-directory line would restate that match rather than route anything. -->
# `tests/run_end_hooks`

- **One target, `run-end-hooks:test`, keyed on `runEndHooksWorkspace`**, which names files
  rather than trees, measured from what the tier opens: every journey here spends real
  launches and waits out a follow-up run, so a path the key names that nothing reads makes
  an unrelated edit pay for all of it. Every pin in `config/` is read, the UI pin included:
  session setup verifies the plan-store CLI provisioned through the project lock and reads
  all four published-tool pins before the launch. A launch
  path that starts reading a new file is a re-measurement, not a glob to widen.
- **A hook fires only on a launch that names it.** Launch through `just orchestrate`, which
  adds both hooks, and name them blank (`--success-hook=`, `--failure-hook=`) for a twin
  whose settlement a hooked run is compared against.
- **State every store a hook reaches.** The success hook reads the run's drafts and writes a
  follow-up project, so a journey here names its own runs, drafts and authoring roots.
