"""The shared local-md root every test process of this repository publishes into.

`onetaskgraph.yaml` configures the `test-fixtures` source at one fixed directory, so
every pytest process here — and every `onetaskgraph` read a recipe makes from inside
one — walks the same tree. That tree is written and read concurrently: Nx runs `test`,
`test-docs`, `test-recipes` and `test-checkouts` as separate processes at the same
time, each with its own xdist workers, and a source root is a property of the tracked
configuration those journeys deliberately drive rather than something a test may point
elsewhere.

Removing a record from a tree somebody else is walking is what breaks that. The local
Markdown source reads a project document and then opens the task directory below it,
and a directory that vanished between the two is not a missing project to it — it is a
hard refusal, `the source returned data this interface cannot represent: … (os error
2)`, which reaches the caller as `just check-plan` exit 2 and fails whichever journey
was reading. That is not hypothetical: a publication's own pre-push gate failed exactly
that way, in `test-docs`, on a record the `test-checkouts` process was removing at its
exit.

So nothing removes a record here while a peer may be reading one. Every pytest process
takes a shared lock on this root for the whole of its session, and a sweep runs only
under the exclusive lock — so reclaiming is the next run's work, never a live one's,
and what a killed process leaves behind is bounded at one run's worth rather than
accumulating for as long as the host lives.
"""

from __future__ import annotations

import fcntl
import os
import re
from pathlib import Path

#: The directory `onetaskgraph.yaml`'s `test-fixtures` source is rooted at. Restated
#: here rather than read out of that file: this is the value the configuration has to
#: keep, and `tests/test_plan_source_roots.py` is what reconciles the two.
ROOT = Path("/tmp/ai-orchestrator-test-projects")

#: The lock file itself, which is neither a project, a task nor a document record and so
#: is invisible to the source: it sits beside `projects/`, `tasks/` and `documents/`
#: rather than inside any of them.
LOCK_NAME = ".readers.lock"

#: How `tests/e2e/project_fixtures.py` names what it writes — `test-<pid>-<n>-<slug>` —
#: so a record says which process wrote it. A record named any other way belongs to
#: something else and is never reclaimed here.
_OWNER = re.compile(r"^test-(\d+)-")

#: One held descriptor per root. The lock lives for the process, so this is deliberately
#: never released: the kernel drops it when the process ends, which is also the only
#: point at which this process has stopped reading.
_HELD: dict[Path, int] = {}


def _lock_descriptor(root: Path) -> int:
    root.mkdir(parents=True, exist_ok=True)
    return os.open(root / LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o600)


def hold_shared(root: Path = ROOT) -> None:
    """Take this process's reader lock on ``root`` and keep it until the process ends."""
    if root in _HELD:
        return
    descriptor = _lock_descriptor(root)
    fcntl.flock(descriptor, fcntl.LOCK_SH)
    _HELD[root] = descriptor


def holds_shared(root: Path = ROOT) -> bool:
    """Whether this process is holding the reader lock on ``root``."""
    return root in _HELD


def running(pid: int) -> bool:
    """Whether ``pid`` still names a live process, answered conservatively.

    Anything but a definite "no" keeps the record: reclaiming one that is still being
    written is the failure this module exists to prevent, and leaving one behind costs
    a directory until the next run.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (OSError, OverflowError):
        return True
    return True


def _natives(root: Path) -> set[str]:
    """Every native project id this root holds a record or a task directory for."""
    found: set[str] = set()
    projects, tasks = root / "projects", root / "tasks"
    if projects.is_dir():
        found |= {record.stem for record in projects.glob("*.md")}
    if tasks.is_dir():
        found |= {directory.name for directory in tasks.iterdir() if directory.is_dir()}
    return found


def _document_natives(root: Path) -> set[str]:
    """Every native document id this root holds.

    A third folder beside `projects/` and `tasks/`, and reclaimed on the same terms: a
    document is a record a journey wrote into a shared root, so one whose writing process
    is gone is one nothing will read again. It is kept apart from the project ids above
    because it is a different namespace — a document's own id, which the source addresses
    it by — and reclaiming a document named after a project would remove the wrong record.
    """
    documents = root / "documents"
    return {record.stem for record in documents.glob("*.md")} if documents.is_dir() else set()


def _reclaim(root: Path) -> list[str]:
    reclaimed: list[str] = []
    for native in sorted(_natives(root)):
        owner = _OWNER.match(native)
        if owner is None or running(int(owner.group(1))):
            continue
        # The project record goes first and the task directory second, so that no moment
        # of the removal leaves a project whose tasks the source would then fail to open.
        (root / "projects" / f"{native}.md").unlink(missing_ok=True)
        directory = root / "tasks" / native
        if directory.is_dir():
            for child in directory.iterdir():
                child.unlink()
            directory.rmdir()
        reclaimed.append(native)
    for native in sorted(_document_natives(root)):
        owner = _OWNER.match(native)
        if owner is None or running(int(owner.group(1))):
            continue
        (root / "documents" / f"{native}.md").unlink(missing_ok=True)
        reclaimed.append(native)
    return reclaimed


def sweep_dead_owners(root: Path = ROOT) -> list[str]:
    """Reclaim every record whose writing process is gone, if nothing may be reading.

    Answers the native ids it removed, and an empty list when the exclusive lock was
    not free — a peer holds the reader lock, so this process leaves the reclaim to
    whichever run finds the root quiet.
    """
    descriptor = _lock_descriptor(root)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return []
        return _reclaim(root)
    finally:
        os.close(descriptor)
