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
