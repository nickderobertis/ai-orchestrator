# Python orchestrator project

This project owns the Python orchestration engine and command-line entry points.
Its Nx targets are local wrappers around the existing uv, Ruff, mypy, and pytest
commands; keep those tools authoritative rather than duplicating their settings.

New public verbs require a realistic test through the real CLI boundary. Preserve
strict input validation and the serialized-contract rules in the root guidance.
