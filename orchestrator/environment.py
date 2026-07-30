"""Environment-variable namespaces shared across orchestration boundaries."""

CHANNEL_ENV_PREFIX = "AI_ORCHESTRATOR_CHANNEL_"

#: The namespace carrying one change's gate comparison identity to every process that
#: judges it. Named here rather than only in `verify.comparison_env` because it is
#: inherited: anything running inside a dispatch already has it set, so a nested
#: process that must resolve its own identity has to be able to clear the namespace.
COMPARISON_ENV_PREFIX = "ORCHESTRATOR_COMPARISON_"
