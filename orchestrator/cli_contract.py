"""Shared command-line contract constants."""

ROUND_BUDGET_OPTION = "--round-budget"
# oneharness's approval/sandbox modes, offered identically by every entry point
# that dispatches (`just dispatch`, `just run-plan`, `just repo-task`, and
# `just orchestrate`) so one option cannot drift away from the others.
ONEHARNESS_MODES = ("read-only", "plan", "default", "edit", "auto", "bypass")
# The no-approval mode. Correct here because the whole environment is a container:
# codex's own sandbox needs unprivileged user namespaces this host disables.
DEFAULT_ONEHARNESS_MODE = "bypass"
