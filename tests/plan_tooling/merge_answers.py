"""Merge a JSON object over a file of template answers, in place: a stand-in writer's step.

A design-document writer copies its budget answers from what `python -m
orchestrator.plan_budgets <plan>` prints. A journey's stand-in writer stages the rest of its
answers before the launch and runs this, with the staged file and that command's printed
object, so the document it stores carries the budget answers exactly as printed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    staged, printed = Path(argv[0]), json.loads(argv[1])
    answers = json.loads(staged.read_text(encoding="utf-8"))
    if not isinstance(answers, dict) or not isinstance(printed, dict):
        print("merge_answers: both the staged answers and the printed ones must be objects")
        return 2
    staged.write_text(json.dumps({**answers, **printed}), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
