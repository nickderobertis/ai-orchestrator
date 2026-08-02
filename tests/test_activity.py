"""What the live-activity reader accepts, and what it refuses.

These files are written by a subprocess into a scratch root every user on the host
can write to, and what they carry ends up on a supervising planner's terminal as a
statement about what a node is doing now. So the reader is a trust boundary, and the
cases below are the ways a summary can be wrong rather than merely absent: not this
run's, not a locator the journal could ever record, old enough to describe a turn
that has moved on, or carrying a credential.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import fields
from pathlib import Path

import pytest

from orchestrator import REPO_ROOT
from orchestrator.activity import (
    FUTURE_TOLERANCE_SECONDS,
    MAX_REPORTED_EVENTS,
    MAX_SUMMARY_BYTES,
    STALE_AFTER_SECONDS,
    SUMMARY_CHARS,
    NodeActivity,
    live_activity,
)
from orchestrator.dispatch import AGENT_STDOUT_NAME
from orchestrator.labels import graph_labels, semantic_agent_labels
from orchestrator.runs import NodeId, RunId, StepId
from orchestrator.scratch import (
    AGENT_ACTIVITY_NAME,
    AGENT_STATUS_DIR_NAME,
    OWNER_LOCK_NAME,
    WATCHDOG_PATTERN,
    WATCHDOG_PREFIX,
    _OwnerIdentity,
    owned_scratch_directory,
)

NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def dispatch_scratch_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ExitStack]:
    """Point a dispatch's own scratch creation at this test's root, and reap it.

    `owned_scratch_directory` is what a dispatch uses: it creates the watchdog tree
    under the host's temp root and *holds* its owner lock for the dispatch's whole
    scope, which is the only evidence the reader accepts. Directing it here lets a
    test publish exactly as a live dispatch does, and the stack releases each claim
    when the test ends — or when a test releases one itself, to model a turn that is
    over.
    """
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    with ExitStack() as stack:
        yield stack


def _live_dispatch(stack: ExitStack) -> Path:
    """Create one live dispatch's status directory, exactly as `run_onejudge` does."""
    status_dir = stack.enter_context(owned_scratch_directory()) / AGENT_STATUS_DIR_NAME
    status_dir.mkdir()
    return status_dir


def _abandoned_dispatch(root: Path, name: str) -> Path:
    """Build a watchdog-shaped status directory by hand, with nothing behind it.

    Deliberately not through `owned_scratch_directory`: these are the shapes it never
    produces — a directory some other process left, and one whose dispatch died
    without the cleanup that would have removed it.
    """
    status_dir = root / f"{WATCHDOG_PREFIX}{name}" / AGENT_STATUS_DIR_NAME
    status_dir.mkdir(parents=True, exist_ok=True)
    return status_dir


def _write(status_dir: Path, payload: object) -> Path:
    path = status_dir / AGENT_ACTIVITY_NAME
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return path


def _summary(**overrides: object) -> dict[str, object]:
    return {
        "run_id": "live-run",
        "round": "1",
        "node": "ship",
        "persona": "engineer",
        "at": NOW - 3,
        "kind": "tool_call",
        "name": "Bash",
        "detail": "just check",
        "events": 4,
        **overrides,
    }


def test_a_published_summary_becomes_what_the_node_is_doing_now(
    tmp_path: Path, dispatch_scratch_root: ExitStack
) -> None:
    _write(_live_dispatch(dispatch_scratch_root), _summary())

    found = live_activity("live-run", root=tmp_path, now=NOW)

    assert set(found) == {("1", "ship")}
    assert found[("1", "ship")].describe(now=NOW) == "now Bash just check (4 event(s), 3s ago)"


def test_only_this_run_is_reported(tmp_path: Path, dispatch_scratch_root: ExitStack) -> None:
    """A shared host runs several planners' dispatches into one scratch root."""
    _write(_live_dispatch(dispatch_scratch_root), _summary())
    _write(_live_dispatch(dispatch_scratch_root), _summary(run_id="another-run", node="theirs"))

    found = live_activity("live-run", root=tmp_path, now=NOW)

    assert set(found) == {("1", "ship")}


def test_the_newest_publication_for_a_node_wins(
    tmp_path: Path, dispatch_scratch_root: ExitStack
) -> None:
    """A retried node's finished dispatch can outlive the reaper beside its replacement."""
    _write(
        _live_dispatch(dispatch_scratch_root), _summary(at=NOW - 120, detail="just check", events=2)
    )
    _write(
        _live_dispatch(dispatch_scratch_root), _summary(at=NOW - 1, detail="just gate", events=9)
    )

    found = live_activity("live-run", root=tmp_path, now=NOW)

    assert found[("1", "ship")].detail == "just gate"
    assert found[("1", "ship")].events == 9


def test_a_zero_padded_round_names_the_same_round_the_journal_does(
    tmp_path: Path, dispatch_scratch_root: ExitStack
) -> None:
    _write(_live_dispatch(dispatch_scratch_root), _summary(round="01"))

    assert set(live_activity("live-run", root=tmp_path, now=NOW)) == {("1", "ship")}


def test_a_credential_never_reaches_the_planner(
    tmp_path: Path, monkeypatch, dispatch_scratch_root: ExitStack
) -> None:
    """A tool input can carry one, and this is where it would be rendered."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-secret-value")
    _write(
        _live_dispatch(dispatch_scratch_root),
        _summary(detail="curl -H 'x: sk-ant-not-a-real-secret-value'"),
    )

    found = live_activity("live-run", root=tmp_path, now=NOW)

    assert "sk-ant-not-a-real-secret-value" not in found[("1", "ship")].detail


def test_a_long_field_is_bounded(tmp_path: Path, dispatch_scratch_root: ExitStack) -> None:
    _write(_live_dispatch(dispatch_scratch_root), _summary(detail="x" * (SUMMARY_CHARS * 4)))

    assert len(live_activity("live-run", root=tmp_path, now=NOW)[("1", "ship")].detail) == (
        SUMMARY_CHARS
    )


def test_a_stale_or_future_summary_is_dropped_rather_than_aged(
    tmp_path: Path, dispatch_scratch_root: ExitStack
) -> None:
    """Reporting either as "now" would be a fresher-looking lie than saying nothing."""
    _write(_live_dispatch(dispatch_scratch_root), _summary(at=NOW - STALE_AFTER_SECONDS - 1))
    _write(
        _live_dispatch(dispatch_scratch_root),
        _summary(node="skewed", at=NOW + FUTURE_TOLERANCE_SECONDS + 1),
    )

    assert live_activity("live-run", root=tmp_path, now=NOW) == {}


def test_every_unusable_publication_degrades_to_silence(
    tmp_path: Path, dispatch_scratch_root: ExitStack
) -> None:
    """None of these can fail a read-only view; each simply is not reported."""
    _write(_live_dispatch(dispatch_scratch_root), "{not json")
    _write(_live_dispatch(dispatch_scratch_root), [1, 2, 3])
    _write(_live_dispatch(dispatch_scratch_root), _summary(node="a", round=None))
    _write(_live_dispatch(dispatch_scratch_root), _summary(node="b", round="round-one"))
    _write(_live_dispatch(dispatch_scratch_root), _summary(node=""))
    _write(_live_dispatch(dispatch_scratch_root), _summary(node="c", at="recently"))
    _write(_live_dispatch(dispatch_scratch_root), _summary(node="d", at=True))
    # A directory where the file goes: the wrapper's atomic replace never makes one,
    # and a reader that raised on it would take the whole view down with it.
    (tmp_path / "orchestrator-watchdog-dir" / "agent" / "agent.activity").mkdir(parents=True)

    assert live_activity("live-run", root=tmp_path, now=NOW) == {}


def test_missing_optional_fields_still_describe_the_node(
    tmp_path: Path, dispatch_scratch_root: ExitStack
) -> None:
    """The publisher bounds what it writes, but the reader owes nothing to its shape."""
    _write(
        _live_dispatch(dispatch_scratch_root),
        {"run_id": "live-run", "round": "2", "node": "ship", "at": NOW},
    )

    found = live_activity("live-run", root=tmp_path, now=NOW)

    assert found[("2", "ship")].describe(now=NOW) == "now event (0 event(s), 0s ago)"


def test_an_absent_scratch_root_is_no_activity(tmp_path: Path) -> None:
    assert live_activity("live-run", root=tmp_path / "never-created", now=NOW) == {}


def test_the_reader_defaults_to_the_hosts_scratch_root(
    tmp_path: Path, monkeypatch, dispatch_scratch_root: ExitStack
) -> None:
    """`just status` passes no root; it reads where a dispatch's watchdog directory is.

    `tempfile` resolves the host's temp root once per process and remembers it, so a
    reader started later sees the same one the dispatcher created its directory
    under. Both are set here because a live `just status` inherits both.
    """
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    _write(_live_dispatch(dispatch_scratch_root), _summary(at=time.time()))

    assert set(live_activity("live-run")) == {("1", "ship")}


def test_a_non_finite_timestamp_never_reaches_the_view(
    tmp_path: Path, dispatch_scratch_root: ExitStack
) -> None:
    """`NaN` and the infinities are JSON numbers Python parses and comparisons pass.

    Every age check answers false for them rather than true, so one would sail
    through and raise inside `describe`, taking down a read-only view whose whole
    contract is that it degrades instead.
    """
    for name, literal in (("nan", "NaN"), ("inf", "Infinity"), ("ninf", "-Infinity")):
        _write(
            _live_dispatch(dispatch_scratch_root),
            f'{{"run_id":"live-run","round":"1","node":"{name}","at":{literal}}}',
        )

    assert live_activity("live-run", root=tmp_path, now=NOW) == {}


def test_the_event_count_is_bounded_into_its_own_domain(
    tmp_path: Path, dispatch_scratch_root: ExitStack
) -> None:
    """It prints as an authoritative statement about how much work a node has done."""
    _write(_live_dispatch(dispatch_scratch_root), _summary(node="back", events=-5))
    _write(
        _live_dispatch(dispatch_scratch_root),
        _summary(node="many", events=MAX_REPORTED_EVENTS * 99),
    )
    _write(_live_dispatch(dispatch_scratch_root), _summary(node="worded", events="lots"))

    found = live_activity("live-run", root=tmp_path, now=NOW)

    assert found[("1", "back")].events == 0
    assert found[("1", "many")].events == MAX_REPORTED_EVENTS
    assert found[("1", "worded")].events == 0


def test_the_publishers_locator_keys_are_ones_a_dispatch_can_actually_carry() -> None:
    """DRIFT-GATE the two sides of one contract, written in two languages.

    `orchestrator.labels` decides what a dispatch stamps its history labels with, and
    `scripts/oneharness-stream.py` parses that value back out of its environment to
    say which node a summary describes. Nothing links them at run time: a key renamed
    on either side would simply stop being joined, and the view would quietly go back
    to reporting elapsed time and nothing else.
    """
    declared = re.search(
        r"^LOCATOR_KEYS = \(([^)]*)\)",
        (REPO_ROOT / "scripts" / "oneharness-stream.py").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert declared is not None, "the stream filter no longer declares LOCATOR_KEYS"
    keys = set(re.findall(r'"([^"]+)"', declared.group(1)))

    stampable = set(
        graph_labels(
            run_id=RunId("a-run"), round_number=1, node=NodeId("a-node"), step=StepId("a-step")
        )
    ) | set(semantic_agent_labels("engineer"))

    assert keys <= stampable, f"the filter joins on labels no dispatch stamps: {keys - stampable}"
    # The join key itself, which the reader requires of every summary it accepts.
    assert {"run_id", "round", "node"} <= keys


def test_every_field_the_reader_reads_is_one_the_publisher_declares() -> None:
    """DRIFT-GATE the record itself, not only its locator.

    `Summary` in the stream filter and `NodeActivity` here are the two ends of one
    cross-process record, and nothing links them at run time either: renaming
    `detail` on one side would not fail to parse, it would just make the view report
    an empty phrase for every streaming node — a silent return to reporting elapsed
    time and nothing else.
    """
    filter_source = (REPO_ROOT / "scripts" / "oneharness-stream.py").read_text(encoding="utf-8")
    published = set(re.findall(r"^    (\w+): (?:str|float|int)$", filter_source, re.MULTILINE))
    read = {field.name for field in fields(NodeActivity)}

    assert read <= published, f"the reader reads fields nothing publishes: {read - published}"


def test_an_oversized_file_is_rejected_rather_than_read_as_its_prefix(
    tmp_path: Path, dispatch_scratch_root: ExitStack
) -> None:
    """A valid prefix of some much larger document is not a summary.

    The read is bounded because this file is foreign, and a bound that truncated
    instead of refusing would report whatever the first summary-shaped bytes of a
    log, a heap dump or a planted file happened to say.
    """
    padded = _summary(detail="x" * MAX_SUMMARY_BYTES)
    # Valid JSON well past the cap, whose first bytes parse as a complete summary
    # once truncated: `{...}` followed by filler the reader never reaches.
    _write(
        _live_dispatch(dispatch_scratch_root), json.dumps(_summary()) + "\n" + json.dumps(padded)
    )

    assert live_activity("live-run", root=tmp_path, now=NOW) == {}


def test_a_publication_no_live_dispatcher_holds_is_not_believed(
    tmp_path: Path, dispatch_scratch_root: ExitStack
) -> None:
    """A watchdog-shaped directory under a shared root is a shape, not a claim.

    Anything on this host can create one, and a dispatch that died without its
    cleanup leaves its own behind. Only a *held* owner lock separates a running
    dispatch's publication from both: a lock file that merely exists names a process
    that is not holding it, and a claim that has been released is a turn that is over.
    """
    _write(_abandoned_dispatch(tmp_path, "unlocked"), _summary(node="nobodys"))
    # A lock file recording this very process — live, and demonstrably not holding it.
    named_only = _abandoned_dispatch(tmp_path, "unheld")
    identity = _OwnerIdentity.current(os.getpid())
    assert identity is not None
    (named_only.parent / OWNER_LOCK_NAME).write_text(identity.render(), encoding="utf-8")
    _write(named_only, _summary(node="named-only"))
    # And a dispatch that has since finished: its claim was held and then released.
    finished = ExitStack()
    _write(_live_dispatch(finished), _summary(node="over"))
    finished.close()

    assert live_activity("live-run", root=tmp_path, now=NOW) == {}


def test_a_round_longer_than_a_label_can_be_is_refused_before_it_is_converted(
    tmp_path: Path, dispatch_scratch_root: ExitStack
) -> None:
    """`isdigit` says yes and `int` raises, so the length check has to come first.

    CPython refuses to convert a decimal string past its digit limit — by raising —
    and this value arrives from a subprocess. The domain is the history-label
    contract it was published from, which is far below that limit.
    """
    _write(_live_dispatch(dispatch_scratch_root), _summary(round="9" * 8000))
    _write(_live_dispatch(dispatch_scratch_root), _summary(node="n" * 8000))

    assert live_activity("live-run", root=tmp_path, now=NOW) == {}


def test_the_filters_status_directory_contract_matches_the_one_scratch_declares() -> None:
    """DRIFT-GATE the directory shape and file names the filter restates.

    `scripts/oneharness-stream.py` is stdlib-only — the wrapper runs it with whatever
    interpreter is available and it cannot import `orchestrator` — so it spells the
    scratch contract itself. Renaming any part of it here would not break the filter
    loudly; it would make the filter refuse every path a dispatch hands it, and every
    streamed turn would fail its capture instead.
    """
    source = (REPO_ROOT / "scripts" / "oneharness-stream.py").read_text(encoding="utf-8")
    restated = dict(re.findall(r'^([A-Z_]+) = "([^"]+)"$', source, re.MULTILINE))

    assert restated["WATCHDOG_PREFIX"] == WATCHDOG_PREFIX
    assert restated["STATUS_DIR_NAME"] == AGENT_STATUS_DIR_NAME
    assert restated["ACTIVITY_NAME"] == AGENT_ACTIVITY_NAME
    assert restated["RECORD_NAME"] == AGENT_STDOUT_NAME
    # And the pattern the reader globs with is that same prefix, so the two ends
    # cannot agree on a name the scan would never reach.
    assert f"{WATCHDOG_PREFIX}*" == WATCHDOG_PATTERN
