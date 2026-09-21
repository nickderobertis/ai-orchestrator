"""What `run_snapshot.snapshot_run` does that `shutil.copytree` did not, under a live writer.

A real thread performs the engine's own two shapes against the directory for the whole
of each test — write `summary.tmp.<pid>` and rename it over `summary.json`; write
`owner.lock.tmp.<pid>.<n>`, link it exclusively to `owner.lock`, unlink it — and
`copytree` is shown to fail the way
https://github.com/nickderobertis/ai-orchestrator/issues/1138 records before the
snapshot is shown not to. The directory is large enough that copying it outlasts listing
it, and the race is asserted from the snapshot's own report of what it left out, so a
run in which the race happened not to land fails rather than passing on luck.

The vanish tests drive the window rather than await it: the record is unlinked at the
copy's own `open` of it, by the real `open` wrapped for the test's duration, and put back
by a real thread doing the engine's write-and-rename, so the re-read is asserted on every
run from the report and from the copied content.
"""

from __future__ import annotations

import builtins
import io
import os
import shutil
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

import pytest
from run_snapshot import is_staging_name, snapshot_run
from waits import timeout, until

#: How many stable records the source holds beside the ones the writer touches, so the
#: copy of the tree outlasts the listing of it by enough for the race to land.
STABLE_RECORDS = 200
#: The fewest copies the race test takes under its writer before it may conclude: every
#: one has to come out right, and the race has to have landed on at least one of them.
MINIMUM_ATTEMPTS = 10
#: How long a test keeps copying for the race to land at least once. Each attempt lands
#: it with better than even odds on an idle host and far worse on a loaded one, where a
#: thread that should be renaming is waiting to be scheduled, so the bound is a
#: load-scaled span rather than a count: a race that has not landed by then is a writer
#: that is not racing, and a snapshot that fails once before then is the defect back.
RACE_SECONDS = 30
#: How long the writer leaves each staging file in place before renaming it over its
#: record: long enough for a listing to catch it, short against the copy that follows.
STAGING_LINGER = 0.002
#: The grace the gone-for-good test gives its record to come back, which it never does.
#: Named at the call site because its expiry is that test's subject: the default is a
#: load-scaled hang guard sized for a stalled host, and a test whose whole point is to
#: spend it would spend most of a minute proving what a fraction of a second proves.
REFUSAL_GRACE = 0.2


@contextmanager
def concurrent(work: Callable[[threading.Event], None]) -> Iterator[None]:
    """Run `work` on a thread for the block's duration, and join it on the way out."""
    stop = threading.Event()
    thread = threading.Thread(target=work, args=(stop,), daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout(10))
        assert not thread.is_alive(), "the concurrent writer did not stop"


def rename_into_place(record: Path, body: str) -> None:
    """The engine's `write_atomic`: stage at `<stem>.tmp.<pid>`, then rename over the record."""
    staging = record.with_suffix(f".tmp.{os.getpid()}")
    staging.write_text(body, encoding="utf-8")
    time.sleep(STAGING_LINGER)
    os.replace(staging, record)


def link_into_place(record: Path, body: str, nonce: int) -> None:
    """The engine's `create_exclusively_filled`: stage at `<name>.tmp.<pid>.<nonce>`, link it in.

    The link is exclusive, refused where the record already exists, so a claim that is
    already held leaves the record untouched and only the staging file comes and goes.
    """
    staging = record.with_name(f"{record.name}.tmp.{os.getpid()}.{nonce}")
    staging.write_text(body, encoding="utf-8")
    time.sleep(STAGING_LINGER)
    with suppress(FileExistsError):
        os.link(staging, record)
    staging.unlink()


def atomic_writer(run: Path) -> Callable[[threading.Event], None]:
    """A writer replacing `summary.json` and claiming `owner.lock` by the engine's shapes."""

    def write(stop: threading.Event) -> None:
        nonce = 0
        while not stop.is_set():
            rename_into_place(run / "summary.json", f'{{"revision": {nonce}}}')
            link_into_place(run / "owner.lock", f"holder {nonce}", nonce)
            nonce += 1

    return write


class Opens:
    """How many times each source entry was read, and how many times a read found it gone."""

    def __init__(self) -> None:
        self.read: Counter[Path] = Counter()
        self.missed: Counter[Path] = Counter()


@contextmanager
def vanishing_at_its_open(record: Path) -> Iterator[tuple[threading.Event, Opens]]:
    """Unlink `record` the first time anything opens it for reading, and count every read.

    The wrapper is the real `open` with one thing in front of it: on the first read of
    `record` it removes the record, writes `late.json` beside it, and sets the event —
    then goes through, so the open fails with the real `ENOENT` the real file's absence
    gives. That is the vanish placed at the one instant it has to be at to be "between
    the listing and the copy": the listing is behind, the copy is this very call. Every
    later open goes straight through, and every read of a file under the record's run
    is counted by its outcome, so a test can say that each source entry was read once —
    a copy — and which of them a read found gone in between.

    `builtins.open` and `io.open` are both replaced because `pathlib` reaches the one
    and `shutil` the other, the same pair `tests/conftest.py`'s own guards wrap.
    """
    opener = builtins.open
    vanished = threading.Event()
    opens = Opens()
    lock = threading.Lock()

    # `Any` for the reason the conftest guards use it: this stands in for `open`, whose
    # return type is chosen by arguments it forwards untouched.
    def open_vanishing(file: Any, *args: Any, **kwargs: Any) -> Any:
        mode = args[0] if args else kwargs.get("mode", "r")
        if not isinstance(file, str | os.PathLike) or "r" not in mode:
            return opener(file, *args, **kwargs)
        path = Path(os.fspath(file))
        if record.parent not in path.parents:
            return opener(file, *args, **kwargs)
        unlinked = False
        if path == record and not vanished.is_set():
            with lock:
                if not vanished.is_set():
                    record.unlink()
                    (record.parent / "late.json").write_text("{}", encoding="utf-8")
                    unlinked = True
        try:
            opened = opener(file, *args, **kwargs)
        except FileNotFoundError:
            with lock:
                opens.missed[path] += 1
            if unlinked:
                vanished.set()
            raise
        with lock:
            opens.read[path] += 1
        return opened

    # llmlint: ignore-block[e2e_not_mocked, tests_mirror_real_usage] Nothing is doubled:
    # every call goes through to the real `open`, the unlink is a real unlink, and the
    # `ENOENT` is the kernel's answer for a file that is really gone. What the wrapper
    # adds is placement — the one real removal at the instant that defines the case,
    # between the listing and the copy's own open — which nothing outside the helper can
    # hit on purpose, because the helper's open is not observable from outside the
    # process: a thread keyed on the copy's progress lands before or after it by
    # scheduling luck, which is the retried coin toss this replaced.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(builtins, "open", open_vanishing)
        patch.setattr(io, "open", open_vanishing)
        yield vanished, opens
    # llmlint: ignore-end[e2e_not_mocked, tests_mirror_real_usage]


def restorer(record: Path, vanished: threading.Event) -> Callable[[threading.Event], None]:
    """Put `record` back, by write-and-rename, once `vanished` says the copy has lost it."""

    def restore(stop: threading.Event) -> None:
        while not stop.is_set() and not vanished.wait(0.01):
            pass
        if not vanished.is_set():
            return
        replacement = record.with_suffix(".restored")
        replacement.write_text("restored", encoding="utf-8")
        os.replace(replacement, record)

    return restore


@pytest.fixture
def run(tmp_path: Path) -> Path:
    """A run-shaped directory: many stable records, a nested one, and two the writer replaces."""
    source = tmp_path / "source-run"
    nested = source / "scheduler-research"
    nested.mkdir(parents=True)
    for index in range(STABLE_RECORDS):
        (source / f"record-{index:04}.json").write_text(f'{{"index": {index}}}', encoding="utf-8")
    (nested / "events.jsonl").write_text('{"kind": "launched"}\n', encoding="utf-8")
    (source / "launch.json").write_text('{"run": "source-run"}', encoding="utf-8")
    (source / "summary.json").write_text('{"revision": -1}', encoding="utf-8")
    (source / "owner.lock").write_text("holder -1", encoding="utf-8")
    return source


def stable_names(root: Path) -> set[str]:
    """Every path under `root` that is not a staging file, relative to it."""
    return {
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and not is_staging_name(path.name)
    }


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge, test_tiers_split_by_project_not_by_marker]  # noqa: E501 - llmlint reads a directive's rule list off one line
# The first of these races a real writer for a few seconds and the two beside it drive
# a real thread's write-and-rename, all on the filesystem this tier already writes, and
# they are the proof of the fixture helper `test_legacy_runs_root_e2e.py` copies a live
# run with — a journey that launches a real run in this same tier under this same pair
# of directives. No narrower project owns run fixtures, and one cut for a few seconds of
# tests would add graph structure the journey they protect does not have.
def test_the_snapshot_survives_the_writer_that_makes_copytree_fail(
    run: Path, tmp_path: Path
) -> None:
    """The ticket, reproduced and then closed over the same writer.

    `copytree` is driven first, until its failure is observed rather than assumed: a
    control that could not be made to fail would leave the snapshot's success proving
    nothing about the race. Then every snapshot under the same writer succeeds, holds
    every stable record the source held before the writer began, and holds no staging
    name — the copy of `summary.json` being a whole revision because each rename lands
    a complete one — and the snapshots' own reports show that their listings did catch
    staging names, so the exclusion was exercised rather than idle.
    """
    expected = stable_names(run)
    with concurrent(atomic_writer(run)):
        vanished: list[str] = []
        controls = 0

        def copytree_failed() -> bool:
            nonlocal controls
            controls += 1
            try:
                shutil.copytree(run, tmp_path / f"copytree-{controls}")
            except shutil.Error as error:
                # `copytree` collects each entry's `ENOENT` and raises them together.
                vanished.extend(Path(source).name for source, _, _ in error.args[0])
            return bool(vanished)

        until(
            "copytree racing the writer on a staging file",
            copytree_failed,
            seconds=RACE_SECONDS,
            state=lambda: f"{controls} whole-tree copies, none of them raised",
            interval=0,
        )
        assert all(is_staging_name(name) for name in vanished), (
            f"copytree did not race the writer the way the ticket records: {vanished}"
        )

        excluded: list[str] = []
        snapshots = 0

        def snapshot_caught_a_staging_name() -> bool:
            nonlocal snapshots
            snapshots += 1
            copy = tmp_path / f"snapshot-{snapshots}"
            snapshot = snapshot_run(run, copy)
            excluded.extend(snapshot.excluded)
            assert snapshot.re_read == (), snapshot
            copied = {str(path.relative_to(copy)) for path in copy.rglob("*") if path.is_file()}
            assert copied == expected, f"snapshot {snapshots} differs from the stable set"
            revision = (copy / "summary.json").read_text(encoding="utf-8")
            assert revision.startswith('{"revision": ') and revision.endswith("}"), revision
            return snapshots >= MINIMUM_ATTEMPTS and bool(excluded)

        until(
            "a snapshot's listing catching a staging file",
            snapshot_caught_a_staging_name,
            seconds=RACE_SECONDS,
            state=lambda: f"{snapshots} snapshots, none of them listed a staging name",
            interval=0,
        )
    assert all(is_staging_name(name) for name in excluded), excluded


def test_a_record_that_vanishes_after_its_listing_is_re_read_alone(
    run: Path, tmp_path: Path
) -> None:
    """A stable file removed at its copy's open and put back is copied from the re-read.

    The copy succeeds, holds every stable record, and holds nothing the source gained
    after the listing: `late.json`, written beside the removal, would be in the copy if
    the vanish had re-listed its directory or re-copied the tree. The report names the
    one re-read entry, the copy holds the restored content, and the count of opens shows
    every other record read exactly once — so the re-read is shown to have happened, to
    have been the source of the copy, and to have been the entry alone.
    """
    record = run / "owner.lock"
    copy = tmp_path / "restored"
    expected = stable_names(run)

    with vanishing_at_its_open(record) as (vanished, opens), concurrent(restorer(record, vanished)):
        snapshot = snapshot_run(run, copy)

    assert vanished.is_set(), "the copy never opened the record, so nothing vanished"
    assert snapshot.re_read == ("owner.lock",), snapshot
    assert snapshot.excluded == (), snapshot
    assert (copy / "owner.lock").read_text(encoding="utf-8") == "restored"
    assert not (copy / "late.json").exists(), (
        "the copy holds a file that appeared after the listing, so the vanish re-read "
        "more than the one entry"
    )
    copied = {str(path.relative_to(copy)) for path in copy.rglob("*") if path.is_file()}
    assert copied == expected
    # Every entry was read once, the record included — its copy is the re-read's — and
    # the record is the only one a read ever found gone; a neighbour read twice would be
    # one the vanish re-copied.
    assert {path.name for path, count in opens.read.items() if count != 1} == set(), opens.read
    assert {str(path.relative_to(run)) for path in opens.read} == expected, opens.read
    assert set(opens.missed) == {record}, opens.missed


def test_a_stable_file_that_stays_missing_fails_the_copy_by_name(run: Path, tmp_path: Path) -> None:
    """A listed record that is gone for good is reported, never silently left out.

    The record is removed at its copy's open and nothing puts it back, so the copy is
    refused once the grace named at this call site is spent — naming the file that is
    gone and saying that it vanished after its listing — rather than finishing without
    it and without a word, which is the hole this helper exists to refuse. The refusal
    is chained to the first failed open, so a reader sees both the wait ending and what
    started it.
    """
    record = run / "owner.lock"
    copy = tmp_path / "missing"

    with vanishing_at_its_open(record) as (vanished, opens):
        started = time.monotonic()
        with pytest.raises(FileNotFoundError) as refused:
            snapshot_run(run, copy, vanish_grace=REFUSAL_GRACE)
        waited = time.monotonic() - started

    assert vanished.is_set(), "the copy never opened the record, so nothing vanished"
    assert Path(str(refused.value.filename)) == record
    assert "vanished after its listing" in str(refused.value), refused.value
    assert isinstance(refused.value.__cause__, FileNotFoundError), refused.value.__cause__
    assert waited >= REFUSAL_GRACE, f"refused after {waited:.3f}s, before the grace was spent"
    assert not (copy / "owner.lock").exists()
    assert record not in opens.read, opens.read
    assert opens.missed[record] >= 2, "the record was refused without being looked for again"
    assert set(opens.missed) == {record}, opens.missed


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge, test_tiers_split_by_project_not_by_marker]  # noqa: E501 - llmlint reads a directive's rule list off one line


def test_a_destination_that_already_exists_is_refused_rather_than_merged_into(
    run: Path, tmp_path: Path
) -> None:
    """The snapshot creates its destination, the way `copytree` does, and refuses one it finds.

    A copy merged into a directory that already holds files would report a tree nobody
    listed, so an existing destination is a failure naming it, and the copy writes
    nothing into it.
    """
    destination = tmp_path / "already-there"
    destination.mkdir()
    (destination / "stale.json").write_text("{}", encoding="utf-8")

    with pytest.raises(FileExistsError) as refused:
        snapshot_run(run, destination)

    assert Path(str(refused.value.filename)) == destination
    assert [path.name for path in destination.iterdir()] == ["stale.json"]
