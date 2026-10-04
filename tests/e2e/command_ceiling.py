"""The one-hour shell-command ceiling a dispatched worker's turn carries, as each harness spells it.

`oneharness.toml` and `oneharness.follow-up.toml` raise it and every other role leaves
its provider's default; those files say why. Stated here once so the journey reading it
off a real turn (`tests/e2e/test_dispatch_environment_e2e.py`) and the one holding the
follow-up role to the judge's routing (`tests/e2e/test_oneharness_timeout_e2e.py`) read
the same values. Every name below is the provider's own, and
`tests/test_command_ceiling_providers.py` holds each to the installed provider.
"""

from __future__ import annotations

#: One hour, in the milliseconds both providers read.
CEILING_MS = "3600000"

#: Claude Code's ceiling on the `timeout` one Bash tool call may ask for, set through the
#: environment `[harness.claude-code] env` hands the provider.
CLAUDE_CODE_CEILING = "BASH_MAX_TIMEOUT_MS"

#: Claude Code's default per-call timeout, which the worker roles deliberately leave unset
#: so a hung command does not block for an hour by default.
CLAUDE_CODE_DEFAULT = "BASH_DEFAULT_TIMEOUT_MS"

#: Codex's bound on one `write_stdin` wait for a running `exec_command` process, set by the
#: arguments `[harness.codex] args` puts on the provider's command line.
CODEX_CEILING_KEY = "background_terminal_max_timeout"
CODEX_CEILING_ARGS = ("-c", f"{CODEX_CEILING_KEY}={CEILING_MS}")
