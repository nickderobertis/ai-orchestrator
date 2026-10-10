"""Spike-only offline mode: which test targets' cache keys cover an edited path.

Resolves every project's test targets through ``tests/nx_inputs.py``
(``target_input_globs`` + ``covers``), the way the suite's read guards do, and runs no
target. Uncached targets (``"cache": false``) have no key and run on every gate; they are
listed as such. ``--affected`` additionally asks Nx's project graph which projects
``nx affected`` would select for the path (graph metadata only, no task runs), since a
target is only run by ``just check``'s first phase if its project is selected.

Run with the checkout's interpreter from the checkout root:
``.venv/bin/python spike/gate-profile/offline.py [--affected] PATH...``
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
sys.path[:0] = [str(ROOT / "tests"), str(ROOT)]

import nx_inputs  # noqa: E402


def test_targets() -> list[tuple[str, str, str, bool]]:
    defaults = nx_inputs.nx_config().get("targetDefaults", {})
    rows = []
    for root, decl in sorted(nx_inputs.project_declarations().items()):
        name = decl.get("name", root or "workspace")
        for target, spec in sorted(decl.get("targets", {}).items()):
            if target == "test-support" or not (target.startswith("test") or target == "coverage"):
                continue
            cache = spec.get("cache", defaults.get(target, {}).get("cache", False))
            rows.append((root, name, target, bool(cache)))
    return rows


def affected(path: str) -> list[str]:
    env = dict(os.environ, NX_DAEMON="false", NX_USE_LOCAL="true")
    out = subprocess.run(
        ["node_modules/.bin/nx", "show", "projects", "--affected", f"--files={path}", "--json"],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return sorted(json.loads(out.stdout))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--affected", action="store_true")
    ap.add_argument("paths", nargs="+")
    a = ap.parse_args()
    targets = test_targets()
    report = {"targets_examined": len(targets), "edits": {}}
    for path in a.paths:
        covering, uncached = [], []
        for root, name, target, cache in targets:
            if not cache:
                uncached.append(f"{name}:{target}")
                continue
            key = nx_inputs.target_input_globs(root, target)
            if nx_inputs.covers(key, path):
                covering.append(f"{name}:{target}")
        entry = {"cached_targets_whose_key_covers_it": covering, "uncached_targets_always_run": uncached}
        if a.affected:
            entry["nx_affected_projects"] = affected(path)
            sel = set(entry["nx_affected_projects"])
            entry["covering_targets_in_affected_projects"] = [t for t in covering if t.split(":")[0] in sel]
        report["edits"][path] = entry
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
