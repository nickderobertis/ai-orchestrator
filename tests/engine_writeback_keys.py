"""The plan-store metadata keys a run writes and no plan authors, as the engine answers them.

The installed engine's `plan writeback-keys --json` is the one source, read through
`scripts/onepipeline.sh` exactly as `scripts/review-plan-item.sh`, the `refresh-plan` recipe
and `scripts/spike-records.sh` read it — the union of its `task` and `project` lists — so no
test restates a key and none can drift from the write-back that owns them.
"""

from __future__ import annotations

import functools
import json
import subprocess

from orchestrator.root import REPO_ROOT

#: The wrapper every reader runs the verb through, and the verb itself.
WRAPPER = REPO_ROOT / "scripts" / "onepipeline.sh"
VERB = ("plan", "writeback-keys", "--json")


@functools.cache
def run_written_keys() -> tuple[str, ...]:
    """Every key the engine's write-back owns on a task or a project, sorted and unique."""
    answered = subprocess.run(  # noqa: S603 - this checkout's own wrapper over the pinned engine
        [str(WRAPPER), *VERB], cwd=REPO_ROOT, text=True, capture_output=True, check=True
    )
    lists = json.loads(answered.stdout)
    keys = [*lists["task"], *lists["project"]]
    assert lists["task"] and all(isinstance(key, str) and key for key in keys), lists
    return tuple(sorted(set(keys)))


def excludes() -> tuple[str, ...]:
    """The `--exclude` flags a plan-review attestation passes for every run-written key."""
    return tuple(flag for key in run_written_keys() for flag in ("--exclude", key))
