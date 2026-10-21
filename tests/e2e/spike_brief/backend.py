#!/usr/bin/env python3
"""Script paid words while retaining the selected real harness and its usage history."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from fake_backend import BusyTurn, ControlledTurn


def answer(
    argv: list[str],
    text: str,
    *,
    lost: bool = False,
    activity: BusyTurn | None = None,
    controlled: ControlledTurn | None = None,
) -> int:
    environment = {**os.environ, "FAKE_CODEX_ANSWERS": json.dumps([text])}
    if lost or activity is not None or controlled is not None:
        raise ValueError("this brief fixture scripts complete Codex turns only")
    failure = environment.get("FAKE_BRIEF_FAILURE_MARKER")
    if failure and failure in backend._prompt(argv)[1]:
        environment["FAKE_CODEX_FAIL_AFTER_TURN"] = "1"
    configs = [argv[index + 1] for index, flag in enumerate(argv[:-1]) if flag == "--config"]
    config = configs[-1] if configs else ""
    selection = [] if "/spike-brief-" in config else ["--harness", "codex"]
    return subprocess.run(
        [environment["REAL_ONEHARNESS_BIN"], argv[0], *selection, *argv[1:]],
        env=environment,
        stdin=subprocess.DEVNULL,
        check=False,
    ).returncode


if __name__ == "__main__":
    spec = importlib.util.spec_from_file_location("fake_backend", os.environ["FAKE_BACKEND_SOURCE"])
    if spec is None or spec.loader is None:
        raise ValueError("shared paid-provider double cannot be loaded")
    backend = cast(Any, importlib.util.module_from_spec(spec))
    sys.modules[spec.name] = backend
    spec.loader.exec_module(backend)
    # answer runs the real harness, retaining the shared Codex double's usage events.
    # llmlint: ignore[e2e_not_mocked] Replace only the shared adapter's paid words.
    backend._answer = answer
    raise SystemExit(backend.main(sys.argv[1:]))
