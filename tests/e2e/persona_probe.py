"""Running one persona through a real graph, with only the paid provider substituted.

A persona is prose two files decide the meaning of — `config/onejudge.base.yaml` merges
it, and `oneagentgraph` resolves it — so the only way to read what a persona really
gives a member is to run a member. That is what this does: a real `oneagentgraph run`
of a probe graph, with the paid model stood in for at both seams it can be reached
through, and the turns it was handed recorded.

Shared because two journeys need the same run: `test_path_dispatched_personas_e2e.py`
asks whether each persona the observer graph names by path still loads, and
`test_persona_review_bar_e2e.py` asks what bar a repo-specific persona hands its
supervisor. Stating the environment twice would let one journey substitute a seam the
other left real, which is quota spent rather than a test failure.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import NotRequired, TypedDict, cast

from harness_indirections import established_indirections
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The stand-in for the paid model at the seam a two-party member reaches it, and the
#: stand-in one layer lower for the provider a single-sided member's in-library turn
#: spawns. Both are this suite's own; see each file's header.
FAKE_BACKEND = Path(__file__).resolve().parent / "fake_backend.py"
FAKE_CODEX = Path(__file__).resolve().parent / "fake_codex.py"

#: The directory put ahead of everything on `PATH`, holding a `claude` that refuses the
#: turn. It covers the identities `ONEHARNESS_BIN_*` cannot, which is every claude-code
#: variant these configs would fall through to.
PAID_PROVIDER_GUARD = Path(__file__).resolve().parent / "no-paid-provider"


class EnvelopeLabels(TypedDict, total=False):
    """The labels `oneagentgraph` stamps on an envelope, narrowed to what is read here.

    `total=False` because a graph-scope envelope carries only `run_id`: the member and
    the persona it resolved are what a member-scope one adds, and telling those apart is
    the whole of what these journeys read.
    """

    run_id: str
    member: str
    persona: str


class Settlement(TypedDict):
    """A `graph-settled` payload: how the run ended, and how each member did."""

    exit_code: int
    members: dict[str, str]


class Envelope(TypedDict):
    """One NDJSON envelope of a run, narrowed to the fields these journeys read.

    `oneagentgraph` owns the rest of the schema. `payload` is left untyped because the
    two kinds read here carry different ones, and only the settlement's is asserted on
    — through `Settlement`, at the site that knows which kind it is holding.
    """

    kind: str
    labels: EnvelopeLabels
    payload: object


class ProviderTurn(TypedDict):
    """One turn a stand-in recorded, as `fake_backend.py` and `fake_codex.py` write it.

    Both files are this suite's own on both ends, which is why this states their schema
    rather than validating somebody else's: `config` is absent from the codex record,
    because a single-sided member's in-library turn names none.
    """

    config: NotRequired[str | None]
    prompt: str
    system: NotRequired[str]


def probe_environment(tmp_path: Path, oneharness_bin: str, caller: str) -> dict[str, str]:
    """The environment a probe run gets, with the paid provider substituted twice.

    Both seams, because the two member shapes reach a provider by different paths and a
    journey covering one would let the other spend real quota: a two-party member spawns
    an `oneharness` CLI, and a single-sided one runs oneharness in-library and spawns
    only the provider binary. The indirections come from the helpers that own them,
    because a real oneharness refuses to start a variant whose `env_from` is unset and
    nothing on the `just gate` path exports one. `caller` is who those helpers attribute
    a refusal to.
    """
    environment = dict(os.environ)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment.update(established_indirections(caller))
    # Keeps this run's graph scratch and history out of the host's, so a probe never
    # reads or reclaims a live dispatch's.
    environment["XDG_STATE_HOME"] = str(tmp_path / "state")
    return environment


def run_graph(graph: Path, environment: dict[str, str], task: str) -> list[Envelope]:
    """Run one probe graph for real, and return the envelopes it streamed.

    `oneagentgraph run` is the verb `onepipeline` starts an observer graph with, and its
    NDJSON stream is where a member starting is observable at all.
    """
    ran = subprocess.run(
        ["oneagentgraph", "run", str(graph), "--task", task],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
        check=False,
    )
    assert ran.returncode == 0, (
        f"{graph.name} did not run to a settlement; a persona refused for its shape "
        f"fails here, in config validation, before any member starts:\n"
        f"{ran.stdout}\n{ran.stderr}"
    )
    # `cast` rather than a validating read: this is `oneagentgraph`'s own published
    # envelope stream, proven in its own repository, and `Envelope` states the three
    # fields these journeys read out of it.
    return [cast(Envelope, json.loads(line)) for line in ran.stdout.splitlines() if line.strip()]


def started_member(envelopes: list[Envelope], member: str) -> Envelope:
    """The `member-started` envelope for one member, which a refused persona has none of."""
    started = [
        envelope
        for envelope in envelopes
        if envelope["kind"] == "member-started" and envelope["labels"].get("member") == member
    ]
    assert started, (
        f"no member started for {member!r}, so its persona produced no member at all:\n"
        f"{json.dumps(envelopes, indent=2)}"
    )
    return started[0]


def settlement(envelopes: list[Envelope]) -> Settlement:
    """The graph's own settlement, as its last envelope states it."""
    settled = [envelope for envelope in envelopes if envelope["kind"] == "graph-settled"]
    assert settled, f"the probe graph never settled:\n{json.dumps(envelopes, indent=2)}"
    return cast(Settlement, settled[0]["payload"])


def recorded_turns(prompt_log: Path) -> list[ProviderTurn]:
    """Every turn a stand-in recorded, as it was actually given it."""
    if not prompt_log.is_file():
        return []
    # Test-owned on both ends, so the cast states that schema rather than skipping a
    # validation of somebody else's.
    return [
        cast(ProviderTurn, json.loads(line))
        for line in prompt_log.read_text(encoding="utf-8").splitlines()
    ]
