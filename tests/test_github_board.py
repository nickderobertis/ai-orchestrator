"""The board stand-in's own auditing, held on requests constructed to break it.

`_bounded_step` is what every board journey reads a step's requests through, so the rule
it enforces is tested here directly, on a duplicate the store itself never sends.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from github_board import (
    BATCHED_CLEARS,
    BATCHED_WRITES,
    COPY_ORIGIN_SLOTS,
    COPY_WRITE_SLOTS,
    RequestCost,
    _bounded_step,
    _GitHubFixture,
    _GraphQLRequest,
    _Operation,
    _request_points,
    batched_slot_drift,
    copy_batch_drift,
    copy_read_drift,
)


def test_a_write_step_resolves_each_item_once_per_store_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Separate write invocations may each resolve the item; one invocation may not twice.

    The store's writes never resolve an item twice, so the duplicate is constructed: two
    reads of one item, stamped as the stand-in stamps them, beside an audited launch trace.
    """
    item = "I_kwDOAAAAAAAAAAAA"
    read = "query($id:ID!){node(id:$id){... on Issue{boards:projectItems(first:5){nodes{id}}}}}"

    def step(received: list[float], launched: list[float]) -> RequestCost:
        monkeypatch.setattr(
            _GitHubFixture,
            "requests",
            [_GraphQLRequest(read, {"id": item}, at) for at in received],
            raising=False,
        )
        trace = tmp_path / "store-calls.jsonl"
        trace.write_text(
            "".join(
                json.dumps({"kind": "store", "argv": ["onetaskgraph", "task", "copy"], "at": at})
                + "\n"
                for at in launched
            ),
            encoding="utf-8",
        )
        return _bounded_step("write step", bound=True, trace=trace, writes=True)

    assert _request_points(_GraphQLRequest(read, {"id": item}, 0.0)) > 0
    assert step(received=[11.0, 21.0], launched=[10.0, 20.0]).requests == 2
    with pytest.raises(AssertionError, match=item):
        step(received=[11.0, 12.0], launched=[10.0, 20.0])
    with pytest.raises(ValueError, match="needs the trace"):
        _bounded_step("write step", bound=True, writes=True)


def test_a_batched_field_document_whose_slots_moved_is_named_rather_than_modelled() -> None:
    """The board answers only the slots it models, so a document with others is refused by name.

    The store's own document reaches this check on every journey that copies a ticket; the
    one built here from the modelled slots is what that document has to agree with, and the
    two edits of it are the drift a release would bring: a slot renamed, and one added.
    """
    writes = " ".join(
        f"{'' if at == 0 else slot.alias + ':'}updateProjectV2ItemFieldValue"
        f"(input:${slot.variable}) @include(if:${slot.flag}){{projectV2Item{{id}}}}"
        for at, slot in enumerate(BATCHED_WRITES)
    )
    clears = " ".join(
        f"{slot.alias}:clearProjectV2ItemFieldValue(input:${slot.variable}) "
        f"@include(if:${slot.flag}){{projectV2Item{{id}}}}"
        for slot in BATCHED_CLEARS
    )
    document = f"mutation{{{writes} {clears}}}"

    assert batched_slot_drift(document) == []
    renamed = document.replace("sixth:", "seventh:")
    assert batched_slot_drift(renamed) == [
        "the batched field document lacks "
        "`sixth:updateProjectV2ItemFieldValue(input:$sixth) @include(if:$writeSixth)`"
    ]
    added = document.replace("}}", "} extra:x @include(if:$writeExtra){id}}", 1)
    assert batched_slot_drift(added) == [
        "the batched field document carries 10 slots and the board models 9"
    ]


def test_a_batched_copy_write_whose_slots_moved_is_named_rather_than_modelled() -> None:
    """A copy's aliased writes are held to the store's batch size and spelling, by name."""
    document = (
        "mutation{"
        + "".join(
            f" field{n}:updateProjectV2ItemFieldValue(input:$field{n}) @include(if:$send{n})"
            "{projectV2Item{id}}"
            for n in range(COPY_WRITE_SLOTS)
        )
        + "}"
    )

    assert copy_batch_drift(_Operation.WRITE_FIELD_VALUES, document) == []
    last = COPY_WRITE_SLOTS - 1
    shrunk = document.replace(
        f" field{last}:updateProjectV2ItemFieldValue(input:$field{last}) @include(if:$send{last})"
        "{projectV2Item{id}}",
        "",
    )
    assert copy_batch_drift(_Operation.WRITE_FIELD_VALUES, shrunk) == [
        "the batched copy document lacks "
        f"`field{last}:updateProjectV2ItemFieldValue(input:$field{last}) @include(if:$send{last})`",
        f"the batched copy document carries {last} slots, not {COPY_WRITE_SLOTS}",
    ]


def test_a_batched_copy_read_whose_slots_moved_is_named_rather_than_modelled() -> None:
    """A copy's origin lookups are held to the store's batch size, so a slot past it is named."""
    document = (
        "query{"
        + "".join(
            f" o{n}:repositoryOwner(login:$owner) @include(if:$ask{n}){{id}}"
            for n in range(COPY_ORIGIN_SLOTS)
        )
        + "}"
    )

    assert copy_read_drift(_Operation.ORIGIN_LOOKUPS, document) == []
    grown = document.replace(
        "}",
        f"}} o{COPY_ORIGIN_SLOTS}:repositoryOwner(login:$owner) "
        f"@include(if:$ask{COPY_ORIGIN_SLOTS}){{id}}",
        1,
    )
    assert copy_read_drift(_Operation.ORIGIN_LOOKUPS, grown) == [
        f"the batched copy read carries more than {COPY_ORIGIN_SLOTS} slots"
    ]
    shrunk = document.replace(" o0:repositoryOwner(login:$owner) @include(if:$ask0){id}", "")
    assert copy_read_drift(_Operation.ORIGIN_LOOKUPS, shrunk) == [
        "the batched copy read lacks `o0:repositoryOwner(login:$owner) @include(if:$ask0)`"
    ]
