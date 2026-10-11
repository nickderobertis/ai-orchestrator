"""Restate a visual spike's report into a design document's answers: a stand-in writer's step.

The design-document writer of a visual plan answers `visual_changes` with what the plan's
`spike-visual-report` answers, and gives each image that report holds with `--asset`. A
journey's stand-in writer runs this with the plan-store CLI, its staged answers file and
the report's qualified id: it reads the report's answers and its assets out of the store,
merges `visual_changes` over the staged answers in place, and prints one asset path per
line for the command that stores the document to give as `--asset`.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _store(binary: str, *arguments: str) -> dict[str, object]:
    answered = subprocess.run(
        [binary, *arguments, "--json"], text=True, capture_output=True, check=True
    )
    read: dict[str, object] = json.loads(answered.stdout)
    return read


def main(argv: list[str]) -> int:
    binary, staged, report = argv[0], Path(argv[1]), argv[2]
    changes = _store(binary, "document", "answers", report)["visual_changes"]
    assets = _store(binary, "document", "show", report)["assets"]
    if not isinstance(changes, list) or not isinstance(assets, list):
        print(f"visual_answers: {report} holds no visual changes or no assets", file=sys.stderr)
        return 2
    held = {str(asset["name"]): str(asset["path"]) for asset in assets}
    answers = json.loads(staged.read_text(encoding="utf-8"))
    staged.write_text(json.dumps({**answers, "visual_changes": changes}), encoding="utf-8")
    for change in changes:
        for side in ("before", "after"):
            print(held[change[side]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
