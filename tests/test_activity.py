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
import re
import tempfile
import time
from pathlib import Path

from orchestrator import REPO_ROOT
from orchestrator.activity import (
    MAX_REPORTED_EVENTS,
    STALE_AFTER_SECONDS,
    SUMMARY_CHARS,
    live_activity,
)
from orchestrator.labels import graph_labels, semantic_agent_labels
from orchestrator.runs import NodeId, RunId, StepId

NOW = 1_800_000_000.0


def _publish(root: Path, name: str, payload: object) -> Path:
    status_dir = root / f"orchestrator-watchdog-{name}" / "agent"
    status_dir.mkdir(parents=True, exist_ok=True)
    path = status_dir / "agent.activity"
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


def test_a_published_summary_becomes_what_the_node_is_doing_now(tmp_path: Path) -> None:
    _publish(tmp_path, "live", _summary())

    found = live_activity("live-run", root=tmp_path, now=NOW)

    assert set(found) == {("1", "ship")}
    assert found[("1", "ship")].describe(now=NOW) == "now Bash just check (4 event(s), 3s ago)"


def test_only_this_run_is_reported(tmp_path: Path) -> None:
    """A shared host runs several planners' dispatches into one scratch root."""
    _publish(tmp_path, "mine", _summary())
    _publish(tmp_path, "theirs", _summary(run_id="another-run", node="theirs"))

    found = live_activity("live-run", root=tmp_path, now=NOW)

    assert set(found) == {("1", "ship")}


def test_the_newest_publication_for_a_node_wins(tmp_path: Path) -> None:
    """A retried node's finished dispatch can outlive the reaper beside its replacement."""
    _publish(tmp_path, "old", _summary(at=NOW - 120, detail="just check", events=2))
    _publish(tmp_path, "new", _summary(at=NOW - 1, detail="just gate", events=9))

    found = live_activity("live-run", root=tmp_path, now=NOW)

    assert found[("1", "ship")].detail == "just gate"
    assert found[("1", "ship")].events == 9


def test_a_zero_padded_round_names_the_same_round_the_journal_does(tmp_path: Path) -> None:
    _publish(tmp_path, "padded", _summary(round="01"))

    assert set(live_activity("live-run", root=tmp_path, now=NOW)) == {("1", "ship")}


def test_a_credential_never_reaches_the_planner(tmp_path: Path, monkeypatch) -> None:
    """A tool input can carry one, and this is where it would be rendered."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-secret-value")
    _publish(tmp_path, "secret", _summary(detail="curl -H 'x: sk-ant-not-a-real-secret-value'"))

    found = live_activity("live-run", root=tmp_path, now=NOW)

    assert "sk-ant-not-a-real-secret-value" not in found[("1", "ship")].detail


def test_a_long_field_is_bounded(tmp_path: Path) -> None:
    _publish(tmp_path, "long", _summary(detail="x" * (SUMMARY_CHARS * 4)))

    assert len(live_activity("live-run", root=tmp_path, now=NOW)[("1", "ship")].detail) == (
        SUMMARY_CHARS
    )


def test_a_stale_or_future_summary_is_dropped_rather_than_aged(tmp_path: Path) -> None:
    """Reporting either as "now" would be a fresher-looking lie than saying nothing."""
    _publish(tmp_path, "stale", _summary(at=NOW - STALE_AFTER_SECONDS - 1))
    _publish(tmp_path, "ahead", _summary(node="skewed", at=NOW + STALE_AFTER_SECONDS + 1))

    assert live_activity("live-run", root=tmp_path, now=NOW) == {}


def test_every_unusable_publication_degrades_to_silence(tmp_path: Path) -> None:
    """None of these can fail a read-only view; each simply is not reported."""
    _publish(tmp_path, "torn", "{not json")
    _publish(tmp_path, "list", [1, 2, 3])
    _publish(tmp_path, "no-round", _summary(node="a", round=None))
    _publish(tmp_path, "bad-round", _summary(node="b", round="round-one"))
    _publish(tmp_path, "no-node", _summary(node=""))
    _publish(tmp_path, "bad-clock", _summary(node="c", at="recently"))
    _publish(tmp_path, "bool-clock", _summary(node="d", at=True))
    # A directory where the file goes: the wrapper's atomic replace never makes one,
    # and a reader that raised on it would take the whole view down with it.
    (tmp_path / "orchestrator-watchdog-dir" / "agent" / "agent.activity").mkdir(parents=True)

    assert live_activity("live-run", root=tmp_path, now=NOW) == {}


def test_missing_optional_fields_still_describe_the_node(tmp_path: Path) -> None:
    """The publisher bounds what it writes, but the reader owes nothing to its shape."""
    _publish(tmp_path, "sparse", {"run_id": "live-run", "round": "2", "node": "ship", "at": NOW})

    found = live_activity("live-run", root=tmp_path, now=NOW)

    assert found[("2", "ship")].describe(now=NOW) == "now event (0 event(s), 0s ago)"


def test_an_absent_scratch_root_is_no_activity(tmp_path: Path) -> None:
    assert live_activity("live-run", root=tmp_path / "never-created", now=NOW) == {}


def test_the_reader_defaults_to_the_hosts_scratch_root(tmp_path: Path, monkeypatch) -> None:
    """`just status` passes no root; it reads where a dispatch's watchdog directory is.

    `tempfile` resolves the host's temp root once per process and remembers it, so a
    reader started later sees the same one the dispatcher created its directory
    under. Both are set here because a live `just status` inherits both.
    """
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    _publish(tmp_path, "default", _summary(at=time.time()))

    assert set(live_activity("live-run")) == {("1", "ship")}


def test_a_non_finite_timestamp_never_reaches_the_view(tmp_path: Path) -> None:
    """`NaN` and the infinities are JSON numbers Python parses and comparisons pass.

    Every age check answers false for them rather than true, so one would sail
    through and raise inside `describe`, taking down a read-only view whose whole
    contract is that it degrades instead.
    """
    for name, literal in (("nan", "NaN"), ("inf", "Infinity"), ("ninf", "-Infinity")):
        _publish(
            tmp_path, name, f'{{"run_id":"live-run","round":"1","node":"{name}","at":{literal}}}'
        )

    assert live_activity("live-run", root=tmp_path, now=NOW) == {}


def test_the_event_count_is_bounded_into_its_own_domain(tmp_path: Path) -> None:
    """It prints as an authoritative statement about how much work a node has done."""
    _publish(tmp_path, "negative", _summary(node="back", events=-5))
    _publish(tmp_path, "huge", _summary(node="many", events=MAX_REPORTED_EVENTS * 99))
    _publish(tmp_path, "text", _summary(node="worded", events="lots"))

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
