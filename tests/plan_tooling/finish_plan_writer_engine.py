"""The engine `just finish-plan`'s diagram journey launches through: the real one, `start` doubled.

`tests/plan_tooling/test_finish_plan_diagram_e2e.py` installs this at a copied checkout's
`.venv/bin/onepipeline`, the path `scripts/onepipeline.sh` runs, with the real binary beside
it. Every verb but `start` is handed to the real binary unchanged — `template resolve`,
`template check`, `plan check` and the rest are the engine's own. **`start` is the one
boundary doubled**, because a real one dispatches the writer and its judge through a live
driver whose launch reads remotes over the network; this does what the design-document
writer's turn does instead:

* it reads the launched project's one task — the writer's task `scripts/finish-plan.sh`
  composed — through the real plan store, and
* runs the commands the journey scripted for a task carrying a marker only that task
  carries, in the checkout the launch ran from, under the launch's own environment.

It settles `complete` only when the task carried the marker and every command exited 0, and
ends as an attached `start` does. Every call is appended to a log, so the journey reads that
the launch reached this double and nothing else.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

#: What the journey names, under the `FAKE_` prefix a launch keeps: the real engine, the
#: pinned store, the writer's scripted turn and where each `start` is logged.
REAL = "FAKE_ENGINE_REAL"
STORE = "FAKE_ENGINE_STORE"
TURN = "FAKE_ENGINE_WRITER_TURN"
LOG = "FAKE_ENGINE_LOG"


def _project(arguments: list[str]) -> str:
    """The qualified project a `start` names: its one argument not shaped as a flag."""
    for argument in arguments:
        if ":" in argument and not argument.startswith("-"):
            return argument
    raise SystemExit(f"onepipeline (doubled): start named no project in {arguments}")


def start(arguments: list[str]) -> int:
    project = _project(arguments)
    source, _, native = project.partition(":")
    listed = subprocess.run(
        [os.environ[STORE], "task", "list", "--source", source, "--project", native, "--json"],
        text=True,
        capture_output=True,
        check=True,
    )
    (task,) = json.loads(listed.stdout)["items"]
    content = str(task["item"]["content"])
    turn: dict[str, list[list[str]]] = json.loads(Path(os.environ[TURN]).read_text("utf-8"))
    commands = [command for marker, held in turn.items() if marker in content for command in held]
    done = bool(commands) and all(
        subprocess.run(one, check=False).returncode == 0 for one in commands
    )
    settlement = "complete" if done else "failed"
    with Path(os.environ[LOG]).open("a", encoding="utf-8") as log:
        log.write(json.dumps({"project": project, "ran": len(commands), "settlement": settlement}))
        log.write("\n")
    print(json.dumps({"project": project, "settlement": settlement}))
    return 0 if done else 1


def main() -> int:
    match sys.argv[1:]:
        case ["start", *arguments]:
            return start(arguments)
        case arguments:
            os.execv(os.environ[REAL], [os.environ[REAL], *arguments])


if __name__ == "__main__":
    raise SystemExit(main())
