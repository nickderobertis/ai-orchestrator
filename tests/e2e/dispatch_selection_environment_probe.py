"""Explicitly collected by the dispatch-selection isolation subprocess regression."""

from __future__ import annotations

import os

from orchestrator.harnesses import DISPATCH_SELECTION_ENV


def test_no_enclosing_dispatch_selection_is_visible() -> None:
    assert not [key for key in DISPATCH_SELECTION_ENV if key in os.environ]
