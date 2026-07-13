"""Local orchestration harness over onejudge.

The orchestrator decomposes a large task into a dependency graph of subtasks and
drives each to completion by dispatching a onejudge process with a fitting
persona. This package holds the deterministic mechanics — config merging, single
dispatch, and DAG scheduling — that the orchestrator (an agent following
AGENTS.md) calls; the judgment (decomposition, granularity, persona choice) stays
in the instructions.
"""

from pathlib import Path

__all__ = ["REPO_ROOT", "BASE_CONFIG", "PERSONA_DIR", "TEMPLATE"]

REPO_ROOT = Path(__file__).resolve().parent.parent
BASE_CONFIG = REPO_ROOT / "config" / "onejudge.base.yaml"
PERSONA_DIR = REPO_ROOT / "personas"
TEMPLATE = PERSONA_DIR / "_template.yaml"
