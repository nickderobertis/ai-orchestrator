"""A controlled codex turn runs under the model its side's config names.

Every codex-first supervisory side on this host — the monitor, the pacemaker, the judge,
the llmlint tier — runs its turn with `--control`, driven over `codex app-server`, and
names its model in `[harness.codex].model` rather than at the run level, because model
names differ per provider and a side's tier is chosen per harness. Until
https://github.com/nickderobertis/oneharness/pull/1284 that name reached the record and
never the wire: the control path was handed the run-level model alone, `thread/start`
carried none, and codex ran the default its own `config.toml` named — on this host
about ten times the weekly quota per token, with the record saying the configured model
throughout. The manager's stop-gap was a top-level `model` in `~/.codex/config.toml`,
which cannot say one thing for the judge and another for the worker.

Both journeys here drive that path with nothing standing in: the pinned `oneharness`
runs the real `codex app-server` under a config of exactly that shape, against the
refusing model endpoint `tests/lost_turn_producer.py` keeps — offline, no credential of
this host's in reach, no token spent — and read the model off the one request that
reached the endpoint before it was refused, and off the `observed_model` the server
stated on the thread's open response. The codex home names a *different* model as its
own default, so a turn that ran under the server's choice rather than the config's is
visible as the wrong name on the wire and not as a coincidence.

The refusal of a substituted model — `model_mismatch`, falling through a chain as
`model-mismatch` — is not re-taken here. Inducing it needs a server that would run a
thread under a model other than the one it was asked for, and the real one honours the
request; the producer's own suite holds that refusal, and what this host can prove is
the half it owns: that the request and the record now agree.

It reads the producer this host has installed, which lives outside the workspace and so
outside every `nx.json` key, so it runs in the uncached tier.
"""

# The finding these answer is about which Nx project owns this file, so it is the file
# that is suppressed and a project split that would resolve it. The journeys read the
# `codex` and `oneharness` this host installed, which live outside the workspace and so
# outside every `nx.json` key: the uncached `orchestrator:test-checkouts` tier exists
# for exactly that, selected by `reads_checkouts`, and a second Nx project would need
# its own key over the same nothing. `tests/test_nx_cache_scope.py` holds the marker to
# routing rather than to a shortcut.
# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] see above
# llmlint: ignore-block[shell_test_tiers_stay_split] see above
# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] see above

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import lost_turn_producer
import pytest
from lost_turn_producer import CONFIGURED_MODEL, PRODUCER, SERVER_DEFAULT_MODEL, ControlledResult

pytestmark = pytest.mark.reads_checkouts

#: A side's config as this host writes one: the model on the harness table and none at
#: the run level, which is the shape the control path used to drop.
SIDE_CONFIG = f"""harnesses = ["{PRODUCER}"]

[harness.{PRODUCER}]
model = "{CONFIGURED_MODEL}"
"""

#: The same side naming no model anywhere, so the server's own default is the honest
#: request and the only thing the record can report.
UNNAMED_CONFIG = f"""harnesses = ["{PRODUCER}"]
"""


@pytest.fixture(scope="session")
def codex_bin() -> str:
    """The real producer binary, without which there is no controlled turn to drive.

    Deliberately a failure and not a skip: a journey that quietly does not run reports
    the same green as one that ran. `scripts/session-setup.sh` installs it.
    """
    found = lost_turn_producer.installed_producer()
    if found is None:
        pytest.fail(
            f"the real {PRODUCER} producer is not installed, so no controlled turn can be "
            "driven the way a supervisory side drives one — run `scripts/session-setup.sh`"
        )
    return found


class ControlledTurn(NamedTuple):
    """One real controlled turn, read at the three places a model can be named."""

    #: The result the run published: its `model` is the requested one, its
    #: `observed_model` the one the server said the thread runs under.
    result: ControlledResult
    #: The `model` each request that reached the model endpoint named, in order.
    requested: list[str | None]


def _controlled_turn_under(
    tmp_path: Path, oneharness_bin: str, codex_bin: str, config: str
) -> ControlledTurn:
    """One real controlled turn under `config`, and the models its requests named.

    The codex home names `SERVER_DEFAULT_MODEL` as the server's own default, so the two
    answers a turn can give are different names rather than one name reached two ways.
    """
    endpoint = lost_turn_producer.recording_refusals()
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    home = lost_turn_producer.refusing_home(
        codex_home, base_url=endpoint.base_url, model=SERVER_DEFAULT_MODEL
    )
    root = tmp_path / "turn"
    root.mkdir()
    side = root / "oneharness.toml"
    side.write_text(config, encoding="utf-8")
    result = lost_turn_producer.controlled_turn(
        oneharness_bin, codex_bin, home, root, ("--config", str(side))
    )
    return ControlledTurn(result=result, requested=endpoint.models)


def test_a_controlled_turn_runs_under_the_model_its_sides_config_names(
    tmp_path: Path, oneharness_bin: str, codex_bin: str
) -> None:
    """The request on the wire and the server's own statement both name the config's model.

    Three readings, because the incident was one of them disagreeing with the other two:
    the record's `model` said the configured one the whole time, and only the wire and
    the server knew otherwise.
    """
    turn = _controlled_turn_under(tmp_path, oneharness_bin, codex_bin, SIDE_CONFIG)

    assert turn.result["model"] == CONFIGURED_MODEL, turn
    assert turn.requested == [CONFIGURED_MODEL], (
        f"the request that reached the model endpoint named {turn.requested}, not the "
        f"{CONFIGURED_MODEL!r} the side's [harness.{PRODUCER}] table names; this is the "
        "turn being billed under the server's own default while the record says otherwise"
    )
    observed = turn.result.get("observed_model")
    assert observed == CONFIGURED_MODEL, (
        f"the server stated the thread would run under {observed!r}, and the record has to "
        f"carry that beside the requested {CONFIGURED_MODEL!r}"
    )


def test_a_side_naming_no_model_records_the_one_the_server_chose(
    tmp_path: Path, oneharness_bin: str, codex_bin: str
) -> None:
    """With nothing requested there is nothing to contradict, and the record still says.

    The other half of what the observation buys: a side that names no model gets the
    server's default, as it always did, and the record now names which model that was
    rather than leaving the reader to look it up in the server's own config.
    """
    turn = _controlled_turn_under(tmp_path, oneharness_bin, codex_bin, UNNAMED_CONFIG)

    assert turn.result["model"] is None, turn
    assert turn.requested == [SERVER_DEFAULT_MODEL], turn
    assert turn.result.get("observed_model") == SERVER_DEFAULT_MODEL, turn


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
# llmlint: ignore-end[shell_test_tiers_stay_split]
# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]
