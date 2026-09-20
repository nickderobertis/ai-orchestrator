"""A reader racing the record writers sees whole records and nothing else.

The root a fixture project is written into is walked concurrently by every other test
process's `onetaskgraph` reads, and that walk takes in every task file under the root —
not only the project being read — so a task file caught empty, between a truncating open
and the write that follows it, refuses an unscoped read in some other process, and on the
plan-store release this host ran then refused a read scoped to some *other* project too.
A publication's own pre-push gate failed exactly that way, in `merge-policy:test` and
`plan-tooling:test`, on records `tests/ask_seam/` was writing at that moment
(`tests/plan_fixture_root.py` records the incident). So `publish_record` stages a record
beside its destination and renames it into place, and this drives a reader process
against two writer processes replacing one project for long enough to catch a truncating
write — every read through the module's own `read_records`, so the listing rule that
keeps a stage out of a reader's hands is the one under test too.

The tier is clock-bounded, which is why it is a project of its own rather than a test in
`tests/test_project_store.py`: `nx affected` charges it to a change of the store or of
these files, and to nothing else of `orchestrator/`.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from orchestrator import project_store

#: What the reader process runs: the root read back through `read_records`, as fast as a
#: process can, until it is told to stop — each read held to exactly the records the two
#: renderings name, every one equal to its rendering before the replacement or after it.
#: The first read that differs is reported with what was seen, and a record missing from
#: a read, or a stage listed as one, fails the same way: the set of paths is the claim.
_READER = """
import json, sys
from pathlib import Path
from orchestrator.project_store import read_records
root, stop, renderings = Path(sys.argv[1]), Path(sys.argv[2]), json.loads(sys.argv[3])
paths = set(renderings[0])
while not stop.exists():
    records = read_records(root)
    if set(records) != paths:
        print(f"listed {sorted(set(records) ^ paths)} against {sorted(paths)}")
        sys.exit(1)
    for relative, text in records.items():
        if text not in (renderings[0][relative], renderings[1][relative]):
            print(f"{relative}: {len(text)} bytes, starting {text[:40]!r}")
            sys.exit(1)
sys.exit(0)
"""

#: What each writer process runs: one project, replaced with each of two plans in turn
#: until it is told to stop. A process of its own rather than a thread, because the stage
#: a record is written through is named for the writing process, and two writers of one
#: destination in one process would never exercise that.
_WRITER = """
import itertools, json, sys
from pathlib import Path
from orchestrator.project_store import write_plan_project
root, plans, stop = Path(sys.argv[1]), json.loads(sys.argv[2]), Path(sys.argv[3])
for plan in itertools.cycle(plans):
    if stop.exists():
        break
    write_plan_project(root, plan)
"""

#: How long the writers and the reader race. Bounded by the clock rather than by a count,
#: so that a loaded host lengthens nothing: a fixed number of rewrites once overran the
#: wait on them while the same run's other workers and another dispatch shared the CPU.
#: The unstaged write this guards against was caught within the first few rewrites, so
#: this is margin rather than a search.
_RACE_SECONDS = 2.0


def _plan(fill: str) -> dict[str, object]:
    """One of the two plans the writers alternate between: same records, different bodies."""
    return {"name": "raced", "tasks": [{"id": f"node-{n}", "task": fill * 4096} for n in range(8)]}


def _rendering(plan: dict[str, object]) -> dict[str, str]:
    return {
        str(relative): content
        for relative, content in project_store.render_plan_project(plan).items()
    }


def test_a_reader_racing_the_writers_sees_the_record_before_or_after_each_replacement(
    tmp_path: Path,
) -> None:
    """Every read during the race returns whole records, and only records."""
    root, stop = tmp_path / "root", tmp_path / "stop"
    plans = [_plan("x"), _plan("y")]
    renderings = [_rendering(plan) for plan in plans]
    assert renderings[0] != renderings[1], "the two plans have to render differently"
    project_store.write_plan_project(root, plans[0])

    reader = subprocess.Popen(
        [sys.executable, "-c", _READER, str(root), str(stop), json.dumps(renderings)],
        stdout=subprocess.PIPE,
        text=True,
    )
    writers = [
        subprocess.Popen([sys.executable, "-c", _WRITER, str(root), json.dumps(plans), str(stop)])
        for _ in range(2)
    ]
    try:
        time.sleep(_RACE_SECONDS)
    finally:
        stop.touch()
        for writer in writers:
            assert writer.wait(timeout=60) == 0, "a writer failed to replace the project"
        seen, _ = reader.communicate(timeout=60)
    assert reader.returncode == 0, f"a reader saw something other than a whole record: {seen}"

    # A finished write leaves the root holding records alone, each one whole: whichever
    # plan a writer wrote last, a record is one of the two renderings and never a mix.
    assert not [path for path in root.rglob("*") if path.name.startswith(".")], (
        "a finished write leaves no staging file behind"
    )
    settled = project_store.read_records(root)
    assert set(settled) == set(renderings[0])
    for relative, text in settled.items():
        assert text in (renderings[0][relative], renderings[1][relative]), relative
