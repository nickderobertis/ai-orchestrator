"""Copy what a design-document writer's task quotes into its answers: a stand-in writer's step.

The finish-plan flow derives a design document's Planned tasks rows and Architecture unit
names from the plan's graph, renders that graph for a plan with an edge, and quotes all three
into the writer's task. A real writer reads its task and copies them; a journey's stand-in
writer runs this instead, with its staged answers file, so the document it stores carries
exactly what the task quoted. Its arguments are the staged answers file, the pinned store
binary and, optionally, the run.

It reads the task the way the writer would see it: the one task of the design-document
project the flow launched, `authoring:<run>`, through the pinned plan store — the run being
`ONEPIPELINE_RUN_ID`, which the engine hands every dispatch, unless one is named. It merges
into the staged answers, in place:

* `planned_tasks` — each quoted row, with a `delivers` of the writer's own;
* `units` — one entry per quoted unit, keeping a staged unit of that name and otherwise
  writing a one-sentence one;
* `dag_image` — the rendered image's file name, or `""` when the task quotes none.

It prints the path of the rendered image the task names, or nothing, so the caller gives it
to the store with `--asset`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

#: Where the task quotes the rows, and the units, and the image it rendered.
ROWS = re.compile(r"Answer `planned_tasks` with exactly these rows.*?```json\n(.*?)\n```", re.S)
UNITS = re.compile(
    r"The `name`s of the `units` answer are exactly these units, each once: (.*?)\.\n"
)
IMAGE = re.compile(r"give that file to the command that stores the document as `--asset ([^`]+)`")


def copied(answers: dict[str, object], content: str) -> tuple[dict[str, object], str]:
    """``answers`` with what the writer's task ``content`` quotes copied in, and its image."""
    rows = ROWS.search(content)
    units = UNITS.search(content)
    if rows is None or units is None:
        raise ValueError("the writer's task quotes no rows or no units")
    image = IMAGE.search(" ".join(content.split()))
    staged = answers.get("units")
    kept = {
        unit["name"]: unit
        for unit in (staged if isinstance(staged, list) else [])
        if isinstance(unit, dict)
    }
    named = re.findall(r"`([^`]+)`", units.group(1))
    return {
        **answers,
        "planned_tasks": [
            {**row, "delivers": f"what {row['task']} delivers"} for row in json.loads(rows.group(1))
        ],
        "units": [
            kept.get(name)
            or {
                "name": name,
                "repository": "ai-orchestrator",
                "part": "",
                "summary": f"{name} changes.",
                "reversible": [],
                "decisions": [],
            }
            for name in named
        ],
        "dag_image": Path(image.group(1)).name if image else "",
    }, image.group(1) if image else ""


def main(argv: list[str]) -> int:
    staged, store = Path(argv[0]), argv[1]
    run = argv[2] if len(argv) > 2 else os.environ["ONEPIPELINE_RUN_ID"]
    listed = subprocess.run(
        [store, "task", "list", "--source", "authoring", "--project", run, "--json"],
        text=True,
        capture_output=True,
        check=True,
    )
    (task,) = json.loads(listed.stdout)["items"]
    try:
        answers, image = copied(
            json.loads(staged.read_text(encoding="utf-8")), str(task["item"]["content"])
        )
    except ValueError as exc:
        print(f"copy_quoted_rows: {exc}", file=sys.stderr)
        return 2
    staged.write_text(json.dumps(answers), encoding="utf-8")
    print(image)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
