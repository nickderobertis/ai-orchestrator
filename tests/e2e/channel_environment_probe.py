"""Explicitly collected by the channel-isolation subprocess regression."""

from __future__ import annotations

import os

from orchestrator.environment import CHANNEL_ENV_PREFIX


def test_parent_channel_is_not_visible() -> None:
    assert not any(key.startswith(CHANNEL_ENV_PREFIX) for key in os.environ)
