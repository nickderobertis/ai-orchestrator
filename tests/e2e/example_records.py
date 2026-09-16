"""A journey's own copy of the example projects this repository ships, and proof it was one.

A launch writes its settlements back to the plan it was launched from, and `local-md`
does that by re-rendering the project's and its tasks' Markdown records. So a journey
that launched `examples:<project>` straight from the tracked `examples` source left this
checkout dirty after a passing run — the project `backlog`, its task `in progress` — in
a checkout several managers share. Every journey that launches an example launches it
from a copy made here instead, with the source's root pointed at that copy, and holds
the tracked records to byte-for-byte what they were before it ran.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from plan_sources import read_plan_sources

from orchestrator.root import REPO_ROOT

#: The source the examples ship in, as a launch id qualifies a project with it.
SOURCE = "examples"
#: The variable the plan store reads that source's root from, overriding the file.
ROOT_VARIABLE = f"ONETASKGRAPH_SOURCES__{SOURCE.upper()}__CONFIG__ROOT"


def tracked_root() -> Path:
    """The directory `onetaskgraph.yaml` roots the examples source at in this checkout."""
    sources = read_plan_sources((REPO_ROOT / "onetaskgraph.yaml").read_text(encoding="utf-8"))
    root = sources[SOURCE].root
    if root is None:
        raise AssertionError(f"onetaskgraph.yaml's {SOURCE!r} source names no root to copy")
    return REPO_ROOT / root


def _snapshot(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@dataclass(frozen=True)
class ExampleCopy:
    """One journey's copy of the example records, and the tracked ones as they stood."""

    #: The copy's root, which `environment` points the source at.
    root: Path
    #: Every tracked record, by path under the tracked root, as it was before the launch.
    tracked: Mapping[Path, bytes]

    @property
    def environment(self) -> dict[str, str]:
        """What a launch's environment carries so the source resolves to this copy."""
        return {ROOT_VARIABLE: str(self.root)}

    def assert_tracked_untouched(self) -> None:
        """The tracked records are exactly what they were: none rewritten, added or removed."""
        now = _snapshot(tracked_root())
        changed = sorted(
            str(path)
            for path in self.tracked.keys() | now.keys()
            if self.tracked.get(path) != now.get(path)
        )
        assert not changed, (
            f"a launch meant to read the copy at {self.root} rewrote the tracked example "
            f"records under {tracked_root()}: {', '.join(changed)}"
        )


@contextmanager
def isolated_examples(directory: Path) -> Iterator[ExampleCopy]:
    """Copy the shipped examples under `directory`, and on exit prove the originals intact.

    The check runs only when the block exits normally, so a journey that already failed
    reports its own failure rather than this one. Exit the block after the launch has
    settled or been stopped: a write-back is off the run's reconcile loop and can land
    after the launch returns.
    """
    tracked = tracked_root()
    copy = ExampleCopy(root=directory / SOURCE, tracked=_snapshot(tracked))
    shutil.copytree(tracked, copy.root)
    yield copy
    copy.assert_tracked_untouched()
