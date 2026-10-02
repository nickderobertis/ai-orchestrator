<!-- llmlint: ignore-file[instruction_layer_localized] `.github/CODEOWNERS` has a wildcard owner for this subtree; another entry would not change ownership. -->
# `tests/e2e/board_copy_approval`

The journeys over approving a design document on the `plans` board: the real `just
copy-plan`, `just approve-design` and launch gate, the installed plan store and engine,
against the GitHub Projects double. Keep in-process tests of `orchestrator/design_approval.py`
in the orchestrator coverage tier. `boardCopyApprovalWorkspace` names what these recipes
read; narrow it only to files the tier is shown not to read.
