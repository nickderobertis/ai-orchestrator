#!/usr/bin/env python3
"""Script paid words while retaining the selected real harness and its usage history."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import fake_backend as backend
    import fake_codex
    from fake_backend import BusyTurn, ControlledTurn


def answer(
    argv: list[str],
    text: str,
    *,
    lost: bool = False,
    activity: BusyTurn | None = None,
    controlled: ControlledTurn | None = None,
) -> int:
    if lost or activity is not None or controlled is not None:
        raise ValueError("this brief fixture scripts complete Codex turns only")
    environment = dict(os.environ)
    failed = bool(failure := environment.get("FAKE_BRIEF_FAILURE_MARKER", "")) and (
        failure in backend._prompt(argv)[1]
    )
    configs = [argv[index + 1] for index, flag in enumerate(argv[:-1]) if flag == "--config"]
    config = configs[-1] if configs else ""
    # A brief's own configs keep their chain, so its history names the identity it chose.
    selection = [] if "/spike-brief-" in config else ["--harness", backend.MOCK_HARNESS]
    if backend.CONTROL_FLAG not in argv:
        # An exec-road turn — the judge's evaluation — runs on the codex stand-in, which
        # bills `TURN_USAGE` in its own stream: oneharness 0.25.9 under `--mock-harness`
        # writes that road's history pointer but no record behind it.
        environment["FAKE_CODEX_ANSWERS"] = json.dumps([text])
        if failed:
            environment["FAKE_CODEX_FAIL_AFTER_TURN"] = "1"
        return subprocess.run(
            [environment[backend.REAL_BINARY_ENV], argv[0], *selection, *argv[1:]],
            env=environment,
            stdin=subprocess.DEVNULL,
            check=False,
        ).returncode
    environment[backend.MOCK_STDOUT_ENV] = "".join(
        json.dumps(event) + "\n"
        for event in [
            {"type": "thread.started", "thread_id": "brief-thread"},
            {
                "type": "item.completed",
                "item": {"id": "reply", "type": "agent_message", "text": text},
            },
            # The codex stand-in's own figure, which the mock provider's shared app-server
            # reports on `thread/tokenUsage/updated`, so the brief's model-token budget reads
            # one measured figure on both roads.
            {"type": "turn.completed", "usage": fake_codex.TURN_USAGE},
        ]
    )
    counted: Path | None = None
    if failed:
        # The controlled turn fails as the shared app-server answering it reports a
        # failed one, on every retry.
        counted = Path(tempfile.mkdtemp(prefix="brief-backend-failed-"))
        environment["MOCK_CODEX_OVERLOAD_ATTEMPTS"] = str(backend.LOST_TURN_ATTEMPTS)
        environment["MOCK_ATTEMPT_FILE"] = str(counted / "attempts")
    try:
        # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
        return subprocess.run(
            [
                environment[backend.REAL_BINARY_ENV],
                argv[0],
                *selection,
                "--mock-harness",
                backend.MOCK_HARNESS,
                *argv[1:],
            ],
            env=environment,
            stdin=subprocess.DEVNULL,
            check=False,
        ).returncode
    finally:
        if counted is not None:
            shutil.rmtree(counted, ignore_errors=True)


def _load(name: str, source: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, source)
    if spec is None or spec.loader is None:
        raise ValueError(f"shared paid-provider double {source} cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    shared = Path(os.environ["FAKE_BACKEND_SOURCE"])
    if not TYPE_CHECKING:
        backend = _load("fake_backend", shared)
        fake_codex = _load("fake_codex", shared.with_name("fake_codex.py"))
    # llmlint: ignore[e2e_not_mocked] Replace only the shared adapter's paid words.
    backend._answer = answer
    raise SystemExit(backend.main(sys.argv[1:]))
