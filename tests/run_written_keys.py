"""The plan-store metadata keys a run writes and no plan authors, as the suite reads them.

`config/run-written-keys.txt` is the one list; this reads it the way the scripts do — one key
per line, `#` comments and blank lines skipped — so no test restates a key.
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.root import REPO_ROOT

#: The committed list.
LIST = REPO_ROOT / "config" / "run-written-keys.txt"


def read(path: Path = LIST) -> tuple[str, ...]:
    """Each key ``path`` lists, in its order."""
    lines = (line.split("#", 1)[0].strip() for line in path.read_text("utf-8").splitlines())
    return tuple(line for line in lines if line)


#: Every run-written key, in the list's order.
RUN_WRITTEN_KEYS = read()

#: The `--exclude` flags a plan-review attestation passes for the whole list.
EXCLUDES = tuple(flag for key in RUN_WRITTEN_KEYS for flag in ("--exclude", key))
