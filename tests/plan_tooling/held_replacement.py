"""Replace one record through `orchestrator/plan_store.py`, held between staging and rename.

A process of its own, as a writer racing a reader is. When the replacement asks for its
rename, the staged path is printed and the rename waits for a line on stdin. The hold is an
audit hook on the real call, so nothing the replacement does is stood in for.
"""

from __future__ import annotations

import sys
from pathlib import Path

from orchestrator import plan_store


def main(document: Path) -> None:
    def hold(event: str, arguments: tuple[object, ...]) -> None:
        if event == "os.rename" and Path(str(arguments[1])) == document:
            print(arguments[0], flush=True)
            sys.stdin.readline()

    sys.addaudithook(hold)
    plan_store.write_metadata(document, "orchestrator.plan-review", {"key": "abc"})


if __name__ == "__main__":
    main(Path(sys.argv[1]))
