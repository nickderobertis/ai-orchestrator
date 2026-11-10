"""Advisory terminality over the engine's validated reading vocabulary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator import run_reading, spike_brief


@pytest.mark.parametrize("kind", sorted(run_reading.ENDING_KINDS))
def test_every_authoritative_ending_retires_the_advisory(kind: str) -> None:
    reading = {
        "labels": {},
        "group": None,
        "schema_version": run_reading.SCHEMA_VERSION,
        "run_id": "spikes",
        "public_id": "run-0123456789ab",
        "word": "SETTLED" if kind == "complete" else "ENDED " + kind,
        "liveness": "UNDRIVEN",
        "driven": False,
        "ending": {"kind": kind, "nodes": []},
        "paused": None,
        "nodes": [],
    }
    assert spike_brief.terminal_reading(json.dumps(reading), run_reading.RunId("spikes"))


@pytest.mark.parametrize(
    ("liveness", "paused"),
    [(value, False) for value in sorted(run_reading.LIVENESS)]
    + [("UNDRIVEN", True), ("DRIVER DEAD", True)],
)
def test_nonterminal_readings_retain_the_advisory(liveness: str, paused: bool) -> None:
    reading = {
        "labels": {},
        "group": None,
        "schema_version": run_reading.SCHEMA_VERSION,
        "run_id": "spikes",
        "public_id": "run-0123456789ab",
        "word": "PAUSED" if paused else liveness,
        "liveness": liveness,
        "driven": liveness in ["ACTIVE", "PARKED"],
        "ending": None,
        "paused": {"human_actions": [], "blocking_surface": True} if paused else None,
        "nodes": [],
    }
    assert not spike_brief.terminal_reading(json.dumps(reading), run_reading.RunId("spikes"))


def test_unreadable_status_never_clears_the_advisory() -> None:
    with pytest.raises(run_reading.Unreadable):
        spike_brief.terminal_reading("{}", run_reading.RunId("spikes"))


def test_receipt_run_ids_and_roots_follow_the_existing_planning_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from orchestrator import spike_flow

    assert spike_brief.RUN_ID.pattern == spike_flow.RUN_ID.pattern
    assert spike_brief.RUNS_ROOT_ENV == spike_flow.RUNS_ROOT_ENV
    assert spike_brief.DEFAULT_RUNS_ROOT == spike_flow.DEFAULT_RUNS_ROOT
    monkeypatch.delenv(spike_flow.RUNS_ROOT_ENV, raising=False)
    assert spike_brief.runs_root() == Path(spike_flow.DEFAULT_RUNS_ROOT)
    monkeypatch.setenv(spike_flow.RUNS_ROOT_ENV, str(tmp_path))
    assert spike_brief.runs_root() == tmp_path


def test_delivery_telemetry_is_replaced_whole_and_leaves_no_stage(tmp_path: Path) -> None:
    """A rewrite reads back as the new telemetry, and nothing but the file is left beside it."""
    path = tmp_path / "runs" / spike_brief.DELIVERY_HISTORY
    first: spike_brief.Delivery = {"schema_version": 1, "eligible": "2026-01-01T00:00:00Z"}
    second: spike_brief.Delivery = {**first, "writer_finished": "2026-01-01T00:01:00Z"}

    spike_brief.write_delivery(path, first)
    spike_brief.write_delivery(path, second)

    assert spike_brief.read_delivery(path) == second
    assert [one.name for one in path.parent.iterdir()] == [path.name]


def test_delivery_telemetry_that_cannot_be_put_in_place_leaves_no_stage(tmp_path: Path) -> None:
    """A destination a rename cannot replace refuses the write, and its stage is taken back."""
    path = tmp_path / spike_brief.DELIVERY_HISTORY
    path.mkdir()

    with pytest.raises(OSError):
        spike_brief.write_delivery(path, {"schema_version": 1, "eligible": "2026-01-01T00:00:00Z"})

    assert [one.name for one in tmp_path.iterdir()] == [path.name]
    assert path.is_dir()
