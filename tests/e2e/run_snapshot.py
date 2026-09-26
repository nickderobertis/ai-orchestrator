"""A copy of a run directory the engine may still be writing.

`shutil.copytree` over a live run races its atomic writers, which stage a record beside
itself and rename it into place, so the copy lists `summary.tmp.<pid>` and then finds it
gone (https://github.com/nickderobertis/ai-orchestrator/issues/1138). `snapshot_run`
copies from one listing per directory, leaves out every name shaped like a staging file,
and re-reads only the entry that vanished after its listing — never the tree.

`STAGING_NAME` restates the two shapes `src/ledger.rs` writes at the pinned engine
release, `<stem>.tmp.<pid>.ThreadId(<n>)` and `<record>.tmp.<pid>.<nonce>`, and
`tests/test_engine_contracts.py` holds it to that source.
"""

from __future__ import annotations

import errno
import os
import re
import shutil
import time
from pathlib import Path
from typing import NamedTuple

from waits import timeout

#: What the tail of an atomic writer's staging name looks like, for both of the shapes
#: the installed engine writes: `<stem>.tmp.<pid>.ThreadId(<n>)` from a rename-into-place,
#: the writing thread's id rendered as Rust's `Debug` of it, and
#: `<record>.tmp.<pid>.<nonce>` from a link-into-place.
STAGING_NAME = re.compile(r"\.tmp\.\d+\.(?:ThreadId\(\d+\)|\d+)$")

#: How long a vanished entry is given to come back under its name before its absence is
#: reported, in unscaled seconds; `snapshot_run` scales it like every wall-clock guard in
#: this tier. A stable name goes and returns when a lock is released and re-claimed, and
#: the re-claim is a journaled write that a loaded host can stall for seconds, so a short
#: grace blames the file for the host. Only a copy that is already failing spends it.
VANISH_GRACE = 10.0

RE_READ_INTERVAL = 0.001


class Snapshot(NamedTuple):
    """What one copy left out and what it had to re-read, each relative to the source."""

    excluded: tuple[str, ...]
    re_read: tuple[str, ...]


def is_staging_name(name: str) -> bool:
    """Whether `name` is one an atomic writer stages under and never leaves behind."""
    return STAGING_NAME.search(name) is not None


def snapshot_run(source: Path, destination: Path, *, vanish_grace: float | None = None) -> Snapshot:
    """Copy `source` to `destination` from one listing, leaving every staging file out.

    Each directory is listed exactly once, and the listing is what gets copied: an entry
    that appears afterwards is not part of the snapshot, and a staging name in the listing
    is dropped rather than copied, since by the time its turn comes it has been renamed
    into the record it was staging for. A listed entry that has vanished by its turn is
    re-read on its own — copied once it is back under its name, and reported by name if
    it is not within the grace — because a stable file the listing found and the copy
    lost is exactly the silent hole a snapshot must not paper over.

    `vanish_grace` is that wait in seconds. Left unset it is `VANISH_GRACE` scaled like
    every hang guard in this tier; a caller whose subject is the wait *expiring* names its
    own, unscaled, because a deadline whose expiry is the behaviour under test belongs at
    the call site rather than behind a scale factor.

    `destination` is created here and must not exist beforehand, the way `copytree`'s
    must not. The report says which staging names were left out and which entries were
    re-read, so a journey can say what its copy did.
    """
    grace = timeout(VANISH_GRACE) if vanish_grace is None else vanish_grace
    excluded: list[str] = []
    re_read: list[str] = []
    _snapshot_directory(source, destination, source, grace, excluded, re_read)
    return Snapshot(excluded=tuple(excluded), re_read=tuple(re_read))


def _snapshot_directory(
    source: Path,
    destination: Path,
    root: Path,
    grace: float,
    excluded: list[str],
    re_read: list[str],
) -> None:
    """One directory of the snapshot, recursing into the directories its listing holds."""
    with os.scandir(source) as listing:
        entries = list(listing)
    stable = []
    for entry in entries:
        if is_staging_name(entry.name):
            excluded.append(str(Path(entry.path).relative_to(root)))
        else:
            stable.append(entry)
    destination.mkdir(parents=True)
    for entry in stable:
        target = destination / entry.name
        if entry.is_dir(follow_symlinks=False):
            _snapshot_directory(Path(entry.path), target, root, grace, excluded, re_read)
        elif _copy_entry(Path(entry.path), target, grace):
            re_read.append(str(Path(entry.path).relative_to(root)))


def _copy_entry(entry: Path, target: Path, grace: float) -> bool:
    """Copy one listed file, re-reading it if it vanished after its listing.

    The re-read is the entry alone: a `copy2` that fails with `ENOENT` is tried again on
    the same name, every `RE_READ_INTERVAL`, until it goes through or the grace runs out
    — a name that comes back and goes again before its copy is simply tried once more,
    and a name that is back is copied whatever the clock says. Only a copy that is still
    failing when the grace is spent is refused, as a `FileNotFoundError` naming the file
    and saying that it vanished after its listing and stayed gone, chained to the first
    failure: the bare re-raise this replaced carried the first attempt's traceback, which
    read as the first `open` escaping the wait rather than the wait ending. Nothing here
    re-lists the directory or re-copies a neighbour. `True` when the copy came from the
    re-read.
    """
    try:
        shutil.copy2(entry, target, follow_symlinks=False)
    except FileNotFoundError as vanished:
        first = vanished
    else:
        return False
    deadline = time.monotonic() + grace
    while True:
        time.sleep(RE_READ_INTERVAL)
        try:
            shutil.copy2(entry, target, follow_symlinks=False)
        except FileNotFoundError:
            if time.monotonic() >= deadline:
                raise FileNotFoundError(
                    errno.ENOENT,
                    f"vanished after its listing and was not back within {grace:.0f}s",
                    str(entry),
                ) from first
        else:
            return True
