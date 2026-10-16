"""Spike pytest plugin (throwaway): attribute each TMPDIR leftover to the test that left it.

Loaded with ``-p attribute_plugin`` (this directory on PYTHONPATH) under
``GATE_ATTRIBUTE_ROOT``. Each test runs with ``TMPDIR``/``TMP``/``TEMP`` pointed at a fresh
directory of its own under that root, so everything it and its subprocesses leave in
the temporary directory is its own; after the test, the names still there are appended
to ``<root>/attribution-<pid>.jsonl`` with the test's node id and the Nx task running it,
and the directory is removed. A process that outlives its test and writes later is not
attributed. The pytest session's own base is made before any test and is left alone.
"""

from __future__ import annotations

import itertools
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT_ENV = "GATE_ATTRIBUTE_ROOT"
_count = itertools.count()


def _allocated(path: Path) -> int:
    total = 0
    for dirpath, dirnames, filenames in os.walk(path, onerror=lambda _e: None):
        for name in dirnames + filenames:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_blocks * 512
            except OSError:
                pass
    try:
        return total + path.lstat().st_blocks * 512
    except OSError:
        return total


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None) -> Iterator[None]:
    root = os.environ.get(ROOT_ENV)
    if not root:
        yield
        return
    own = Path(root) / f"{os.getpid()}-{next(_count)}"
    own.mkdir(parents=True)
    saved = {name: os.environ.get(name) for name in ("TMPDIR", "TMP", "TEMP")}
    for name in saved:
        os.environ[name] = str(own)
    tempfile.tempdir = None
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        tempfile.tempdir = None
        left = []
        for entry in sorted(own.iterdir()) if own.exists() else []:
            left.append({"name": entry.name, "dir": entry.is_dir(), "bytes": _allocated(entry)})
        if left:
            record = {
                "nodeid": item.nodeid,
                "nx_task": f"{os.environ.get('NX_TASK_TARGET_PROJECT', '?')}:{os.environ.get('NX_TASK_TARGET_TARGET', '?')}",
                "left": left,
            }
            with open(Path(root) / f"attribution-{os.getpid()}.jsonl", "a", encoding="utf-8") as out:
                out.write(json.dumps(record) + "\n")
        subprocess.run(["chmod", "-R", "u+rwx", str(own)], stderr=subprocess.DEVNULL, check=False)
        shutil.rmtree(own, ignore_errors=True)
