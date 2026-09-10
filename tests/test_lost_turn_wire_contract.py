"""The wire shape a lost monitor turn arrives in, reconciled against its real producer.

`scripts/channel-serve.py` reads a harness's own machine transcript in order to name a
turn its agent side lost, instead of raising twenty-one thousand characters of JSON-RPC
at a planner who may not filter it. To read one it has to *state* that transcript's
shape — `TranscriptFrame`, `FrameParams`, `TurnRecord`, `TurnError`, `HarnessResult` —
and every one of those declarations is a second copy of somebody else's contract.

That is the exact failure the surface exists for. The defect it fixes went unnoticed for
a day because a wire shape moved underneath this repository — `codex exec`'s
`turn.failed` became the app-server's `method: error` — and nothing anywhere noticed. A
hand-authored fixture cannot notice it either: it is written from the same reading of
the contract as the code it feeds, so the two drift together and stay green.

So this is the reconciling check that fails on drift, and both halves of it ask the
**producer**, through `tests/lost_turn_producer.py`:

* **What it emits.** A turn the real `codex` really lost, captured through a real
  `oneharness`. Every field the filter declares is looked for where the filter reads it,
  so a renamed key, a relocated payload, or a field that became something other than
  text fails here.
* **What it declares.** `codex app-server generate-json-schema` is the producer's own
  authoritative protocol schema, so the branches one lost turn cannot exercise are
  reconciled against it: that `failed` is still a `TurnStatus`, that the classification
  the recorded fixture names is still a `CodexErrorInfo`, that `error` still routes to
  the notification carrying a `TurnError`, and that `code` — which the filter tolerates
  — is still no part of this producer's error, so a `code` appearing later with some
  other meaning is noticed rather than silently preferred over `message`.

This is a drift gate over a declaration rather than a journey, which is why it sits
beside the gates over the pins and the prose instead of in `tests/e2e/`; the filter's
own behaviour on a real lost turn is driven end to end by
`tests/e2e/test_lost_turn_wire_contract_e2e.py`.

Both halves read the producer this host has installed, which lives outside the workspace
and so outside every `nx.json` key — a memoized verdict here would describe whatever
codex looked like when it was recorded, which for a drift gate is the one thing worse
than no gate. Hence the module marker: this runs in the uncached tier.
"""

# The finding these answer is about which Nx project owns this file, so it is the file
# that is suppressed and a project split that would resolve it.
# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] see above
# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] see above

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, NamedTuple

import lost_turn_producer
import pytest
from lost_turn_producer import CLASSIFICATION, PRODUCER, LostTurn

from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_checkouts

#: The filter whose declarations this gate reconciles.
CHANNEL_SERVE = REPO_ROOT / "scripts" / "channel-serve.py"

#: Where the recorded lost-turn fixture states the classification it was captured with,
#: and the name it states it under. Read as a declaration below rather than imported: it
#: is stated in a module under `tests/e2e/`, which is not on the import path of a check
#: that lives here — and reading it where it is declared is what this gate does anyway.
RECORDED_FIXTURE = REPO_ROOT / "tests" / "e2e" / "test_orchestrate_launch_e2e.py"
RECORDED_CAUSE = "LOST_TURN_CAUSE"


class Restated(NamedTuple):
    """One field `scripts/channel-serve.py` restates, and who declares it upstream.

    `definition` names the type in codex's own generated protocol schema that answers
    for the field, which is what makes each row checkable rather than a note. A row with
    `declared=False` records the opposite fact and is checked just as hard: the filter
    reads that key, this producer emits no such thing, and the day it does is the day
    somebody should look at it.
    """

    #: The `TypedDict` in the filter that declares the field.
    model: str
    #: The field it declares.
    field: str
    #: The definition in codex's protocol schema that answers for it.
    definition: str
    #: Whether that definition declares it. False records a tolerated non-contract key.
    declared: bool = True


#: Every field the filter's five harness-wire models declare, against the producer
#: definition that answers for it. `TranscriptFrame` is the JSON-RPC envelope, which is
#: why its three fields split across a notification and a response; the rest are the
#: app-server's own payloads. Reconciled for completeness against the filter's own
#: declarations below, so this cannot fall behind a field added there.
WIRE_CONTRACT = (
    Restated("TranscriptFrame", "method", "JSONRPCNotification"),
    Restated("TranscriptFrame", "params", "JSONRPCNotification"),
    Restated("TranscriptFrame", "result", "JSONRPCResponse"),
    Restated("HarnessResult", "codexHome", "InitializeResponse"),
    Restated("FrameParams", "turn", "TurnCompletedNotification"),
    Restated("FrameParams", "error", "ErrorNotification"),
    Restated("TurnRecord", "id", "Turn"),
    Restated("TurnRecord", "status", "Turn"),
    Restated("TurnRecord", "error", "Turn"),
    Restated("TurnError", "message", "TurnError"),
    Restated("TurnError", CLASSIFICATION, "TurnError"),
    Restated("TurnError", "code", "TurnError", declared=False),
)

#: The terminal turn status the filter reads as "nobody took this turn", and the
#: notification method it reads as a failure with no terminal turn behind it. Stated
#: here because they are literals inside the filter's `match`, and grounded twice: in
#: the producer's own enums below, and by the surface the journey reads off a real
#: channel, which the filter can only compose by matching on both.
LOST_STATUS = "failed"
FAILURE_NOTIFICATION = "error"


@pytest.fixture(scope="session")
def codex_bin() -> str:
    """The real producer binary, which this gate is nothing without.

    Deliberately a failure and not a skip: a reconciliation that quietly does not run is
    the state this whole module exists to prevent. `scripts/session-setup.sh` installs
    it, as the harness chains here name it on every side.
    """
    found = lost_turn_producer.installed_producer()
    if found is None:
        pytest.fail(
            f"the real {PRODUCER} producer is not installed, so the wire shape "
            "`scripts/channel-serve.py` reads cannot be reconciled against it — run "
            "`scripts/session-setup.sh`"
        )
    return found


@pytest.fixture(scope="session")
def refusing_codex_home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A codex home whose model endpoint refuses every turn and whose credentials are nobody's."""
    return lost_turn_producer.refusing_home(tmp_path_factory.mktemp("codex-home"))


@pytest.fixture(scope="session")
def lost_turn(
    tmp_path_factory: pytest.TempPathFactory,
    oneharness_bin: str,
    codex_bin: str,
    refusing_codex_home: Path,
) -> LostTurn:
    """A turn the real producer lost, captured through the real oneharness."""
    return lost_turn_producer.capture(
        oneharness_bin, codex_bin, refusing_codex_home, tmp_path_factory.mktemp("lost-turn")
    )


@pytest.fixture(scope="session")
def producer_schema(
    tmp_path_factory: pytest.TempPathFactory, codex_bin: str, refusing_codex_home: Path
) -> dict[str, Any]:
    """codex's own generated protocol schema: what the producer says it emits."""
    return lost_turn_producer.protocol_schema(
        codex_bin, refusing_codex_home, tmp_path_factory.mktemp("protocol-schema")
    )


def _declared() -> dict[str, dict[str, str]]:
    """Every field the filter's wire models declare, with the annotation each carries.

    Parsed out of the file rather than imported from it. What this gate reconciles is a
    *declaration*, so the file is read as the source of that fact — the way the cache-key
    gates here read `nx.json` and `orchestrator/project.json` — instead of executing the
    filter and reaching for the objects its declarations happen to build at runtime.
    """
    parsed = ast.parse(CHANNEL_SERVE.read_text(encoding="utf-8"), filename=str(CHANNEL_SERVE))
    named = {restated.model for restated in WIRE_CONTRACT}
    return {
        node.name: {
            field.target.id: ast.unparse(field.annotation)
            for field in node.body
            if isinstance(field, ast.AnnAssign) and isinstance(field.target, ast.Name)
        }
        for node in parsed.body
        if isinstance(node, ast.ClassDef) and node.name in named
    }


def _recorded_cause() -> str:
    """The classification the recorded lost-turn fixture was captured carrying.

    The fixture is hand-authored, so the one thing that keeps its cause a real one is
    this: the producer's own enum still has to declare it.
    """
    parsed = ast.parse(RECORDED_FIXTURE.read_text(encoding="utf-8"), filename=str(RECORDED_FIXTURE))
    stated = [
        node.value
        for node in parsed.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id == RECORDED_CAUSE
    ]
    assert len(stated) == 1, f"{RECORDED_FIXTURE} no longer states one {RECORDED_CAUSE}"
    cause = ast.literal_eval(stated[0])
    assert isinstance(cause, str), cause
    return cause


def _carries(value: object, annotation: str) -> bool:
    """Whether one emitted value is the kind of thing the filter declared it to be.

    Every field of every wire model is annotated either as text or as a nested frame
    model, which is the whole of what the filter branches on, so those are the two
    answers.
    """
    return isinstance(value, str) if annotation == "str" else isinstance(value, dict)


def _emitted(frames: tuple[dict[str, Any], ...]) -> dict[str, list[dict[str, Any]]]:
    """Every object the filter's wire models describe, found where the filter reads it.

    Each list is built by walking exactly the path the filter walks — a frame's `result`,
    a notification's `params`, that payload's `turn` or `error`, a turn's own `error` —
    so a key that moved is missing from these lists rather than found somewhere else.
    """
    results = [frame["result"] for frame in frames if isinstance(frame.get("result"), dict)]
    params = [frame["params"] for frame in frames if isinstance(frame.get("params"), dict)]
    turns = [payload["turn"] for payload in params if isinstance(payload.get("turn"), dict)]
    errors = [payload["error"] for payload in params if isinstance(payload.get("error"), dict)]
    errors += [turn["error"] for turn in turns if isinstance(turn.get("error"), dict)]
    return {
        "TranscriptFrame": list(frames),
        "HarnessResult": results,
        "FrameParams": params,
        "TurnRecord": turns,
        "TurnError": errors,
    }


def test_the_reconciliation_covers_every_field_the_filter_declares() -> None:
    """Nothing the filter states about the wire may sit outside this gate.

    A gate that covers four of five fields reports green about the fifth, which is worse
    than no gate: the field it does not cover is the one somebody added without asking
    who declares it. So the table is held to the filter's own declarations, and a model
    or a field added there fails here until it names a producer definition.
    """
    declared = {(model, field) for model, fields in _declared().items() for field in fields}
    reconciled = {(restated.model, restated.field) for restated in WIRE_CONTRACT}

    assert reconciled == declared, (
        "the wire shape `scripts/channel-serve.py` declares and the shape this gate "
        f"reconciles have diverged: {declared ^ reconciled}"
    )


def test_a_real_lost_turn_still_carries_every_field_the_filter_reads(
    lost_turn: LostTurn,
) -> None:
    """The producer still emits, where the filter reads it, everything the filter states.

    This is the half a hand-authored fixture cannot be: the frames come from the real
    binary losing a real turn, so a renamed key, a relocated payload, or a field that
    became something other than text fails here — which is precisely what nothing
    noticed when `turn.failed` became `method: error`.
    """
    emitted = _emitted(lost_turn.frames)
    declared = _declared()

    for restated in WIRE_CONTRACT:
        carrying = [
            instance
            for instance in emitted[restated.model]
            if _carries(instance.get(restated.field), declared[restated.model][restated.field])
        ]
        if restated.declared:
            assert carrying, (
                f"a real lost turn carries no `{restated.field}` where the filter reads "
                f"{restated.model}'s, so `{restated.definition}` has moved: "
                f"{lost_turn.transcript}"
            )
        else:
            assert not carrying, (
                f"the producer now emits a `{restated.field}` the filter reads as "
                f"{restated.model}'s; check what it means before it outranks `message`"
            )


def test_the_producers_own_schema_still_declares_the_wire_shape_the_filter_reads(
    producer_schema: dict[str, Any],
) -> None:
    """codex's generated protocol schema still says what the filter reads it to say.

    The live turn proves one failure's frames; this proves the contract behind every
    branch the filter has, including the two a reachable-provider host never produces —
    a turn recorded `failed`, and the quota classification the recorded fixture names.
    """
    for restated in WIRE_CONTRACT:
        definition = producer_schema.get(restated.definition)

        assert definition is not None, (
            f"codex no longer declares `{restated.definition}`, which is where the "
            f"filter's {restated.model}.{restated.field} came from"
        )
        assert (restated.field in definition["properties"]) is restated.declared, (
            f"`{restated.definition}` {'no longer' if restated.declared else 'now'} "
            f"declares `{restated.field}`, which the filter reads as "
            f"{restated.model}.{restated.field}"
        )

    assert LOST_STATUS in producer_schema["TurnStatus"]["enum"], producer_schema["TurnStatus"]
    classifications = next(
        variant["enum"]
        for variant in producer_schema["CodexErrorInfo"]["oneOf"]
        if variant.get("type") == "string"
    )
    assert _recorded_cause() in classifications, classifications
    routed = {
        variant["properties"]["method"]["enum"][0]: variant["properties"]["params"]["$ref"]
        for variant in producer_schema["ServerNotification"]["oneOf"]
    }
    assert routed[FAILURE_NOTIFICATION].endswith("/ErrorNotification"), routed[FAILURE_NOTIFICATION]


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]
