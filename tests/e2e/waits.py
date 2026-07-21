"""Load-tolerant wall-clock guards for end-to-end tests.

These timeouts only guard against a hung test. Deadlines whose expiry is the
behavior under test must remain explicit at their call site.
"""

from __future__ import annotations

import math
import os
import time

_ENVIRONMENT_VARIABLE = "ORCHESTRATOR_E2E_TIMEOUT_SCALE"
_DEFAULT_SCALE = 4.0


def timeout(seconds: float) -> float:
    """Scale a hang guard without changing timeout behavior under test."""
    raw_scale = os.environ.get(_ENVIRONMENT_VARIABLE, str(_DEFAULT_SCALE))
    try:
        scale = float(raw_scale)
    except ValueError as exc:
        raise ValueError(f"{_ENVIRONMENT_VARIABLE} must be a number, got {raw_scale!r}") from exc
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError(f"{_ENVIRONMENT_VARIABLE} must be finite and positive, got {raw_scale!r}")
    return seconds * scale


def deadline(seconds: float) -> float:
    """Return a monotonic deadline for a load-scaled hang guard."""
    return time.monotonic() + timeout(seconds)
