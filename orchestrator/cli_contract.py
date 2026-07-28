"""Shared command-line contract constants."""

from typing import Literal, get_args

ROUND_BUDGET_OPTION = "--round-budget"
# oneharness's approval/sandbox modes, offered identically by every entry point
# that dispatches (`just dispatch`, `just run-plan`, `just repo-task`, and
# `just orchestrate`) so one option cannot drift away from the others. The domain
# is closed, so it is a Literal; the tuple is derived from it rather than repeated,
# which is what argparse `choices` and the dispatch-time guards read.
OneharnessMode = Literal["read-only", "plan", "default", "edit", "auto", "bypass"]
ONEHARNESS_MODES: tuple[OneharnessMode, ...] = get_args(OneharnessMode)
# The no-approval mode. Correct here because the whole environment is a container:
# codex's own sandbox needs unprivileged user namespaces this host disables.
DEFAULT_ONEHARNESS_MODE: OneharnessMode = "bypass"
