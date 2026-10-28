"""The board stand-in's own auditing, held on requests constructed to break it.

`_bounded_step` is what every board journey reads a step's requests through, so the rule
it enforces is tested here directly, on a duplicate the store itself never sends.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from github_board import (
    BATCHED_CLEARS,
    BATCHED_WRITES,
    CONFIGURED_REPOSITORY,
    COPY_BATCHES,
    COPY_ORIGIN_SLOTS,
    COPY_WIDE_SLOTS,
    COPY_WRITE_SLOTS,
    MEASURED_PLACEMENT_SECONDS,
    REPOSITORY_NODE_IDS,
    REQUEST_KINDS,
    REQUEST_LATENCY_MS,
    RequestCost,
    RequestKind,
    _Board,
    _bounded_step,
    _GitHubFixture,
    _GraphQLRequest,
    _Operation,
    _request_points,
    batched_slot_drift,
    copy_batch_drift,
    copy_read_drift,
    request_latency_ms,
    unanswered_selections,
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


def _wide(prefix: str, root: str, send: str, selection: str) -> str:
    """One kind's slots of a copy's wide write batch, spelled as the store spells them."""
    return "".join(
        f" {prefix}{n}:{root}(input:${prefix}{n}) @include(if:${send}{n}){{{selection}}}"
        for n in range(COPY_WIDE_SLOTS)
    )


def test_a_batched_copy_write_whose_slots_moved_is_named_rather_than_modelled() -> None:
    """A copy's aliased writes are held to the store's batch size and spelling, by name."""
    fields = _wide("field", "updateProjectV2ItemFieldValue", "send", "projectV2Item{id}")
    document = f"mutation{{{fields}}}"

    assert copy_batch_drift(_Operation.WRITE_FIELD_VALUES, document) == []
    last = COPY_WIDE_SLOTS - 1
    shrunk = document.replace(
        f" field{last}:updateProjectV2ItemFieldValue(input:$field{last}) @include(if:$send{last})"
        "{projectV2Item{id}}",
        "",
    )
    assert copy_batch_drift(_Operation.WRITE_FIELD_VALUES, shrunk) == [
        "the batched copy document lacks "
        f"`field{last}:updateProjectV2ItemFieldValue(input:$field{last}) @include(if:$send{last})`",
        f"the batched copy document carries {last} slots, not {COPY_WIDE_SLOTS}",
    ]


def test_a_request_of_field_values_and_edges_runs_every_value_before_the_first_edge() -> None:
    """The batch of both kinds is held to both kinds' slots, and is applied fields first.

    Its slots are told apart by `$sendField<n>` and `$sendBlock<n>`, so a release spelling them
    as the single-kind batches do is named, and a board answering it applies each sent slot of
    each kind in the order GitHub runs a document's mutation fields.
    """
    fields = _wide("field", "updateProjectV2ItemFieldValue", "sendField", "projectV2Item{id}")
    blocks = _wide("block", "addBlockedBy", "sendBlock", "issue{id} blockingIssue{id}")
    document = f"mutation({'$sendField0:Boolean!'}){{{fields}{blocks}}}"
    request = _GraphQLRequest(document, {}, 0.0)

    assert request.operation is _Operation.WRITE_FIELDS_AND_EDGES
    assert copy_batch_drift(_Operation.WRITE_FIELDS_AND_EDGES, document) == []
    single = document.replace("$sendBlock", "$send")
    assert copy_batch_drift(_Operation.WRITE_FIELDS_AND_EDGES, single)[0] == (
        "the batched copy document lacks "
        "`block0:addBlockedBy(input:$block0) @include(if:$sendBlock0)`"
    )

    applied: list[str] = []

    def write(kind: str, root: str) -> Callable[[dict[str, object]], dict[str, object]]:
        def written(variables: dict[str, object]) -> dict[str, object]:
            applied.append(f"{kind} {variables['input']}")
            return {"data": {root: {"id": kind}}}

        return written

    board = _Board()
    writes = {
        _Operation.UPDATE_FIELD: write("field", "updateProjectV2ItemFieldValue"),
        _Operation.ADD_BLOCKED_BY: write("edge", "addBlockedBy"),
    }
    answered = board.write_batch(COPY_BATCHES[_Operation.WRITE_FIELDS_AND_EDGES], writes)(
        {
            "sendBlock0": True,
            "block0": "b0",
            "sendField0": True,
            "field0": "f0",
            "sendField1": False,
            "field1": "f1",
            "sendField2": True,
            "field2": "f2",
        }
    )
    assert applied == ["field f0", "field f2", "edge b0"]
    assert answered == {
        "data": {
            "field0": {"id": "field"},
            "field2": {"id": "field"},
            "block0": {"id": "edge"},
        }
    }


def test_a_created_issue_is_on_the_board_only_once_its_measured_delay_has_passed() -> None:
    """A create filing an issue with `projectV2Ids` is placed late, and placed only once.

    Until its delay passes a read of the created issue's board items finds none, as GitHub
    answers one; once placed, filing it again is refused "Content already exists in this
    project" rather than answered — and an issue filed before its placement is answered.
    """
    board = _Board()
    board.placement_delays = (0.2, 30.0)
    issue = {
        "title": "T",
        "body": "B",
        "repositoryId": REPOSITORY_NODE_IDS[CONFIGURED_REPOSITORY],
        "projectV2Ids": [board.node_id],
    }

    def created() -> str:
        answer = board.create_issue({"input": issue})["data"]
        assert isinstance(answer, dict)
        return str(answer["createIssue"]["issue"]["id"])

    def items(content: str) -> list[object]:
        data = board.created_items_response({"id0": content, "read0": True})["data"]
        assert isinstance(data, dict)
        nodes = data["i0"]["projectItems"]["nodes"]
        assert isinstance(nodes, list)
        return nodes

    soon, late = created(), created()
    assert items(soon) == [] and items(late) == []
    deadline = time.monotonic() + 5
    while not items(soon):
        assert time.monotonic() < deadline, "the first create was never placed"
        time.sleep(0.05)
    assert items(late) == []
    refused = board.add_to_board({"input": {"contentId": soon}})
    assert refused["errors"] == [{"message": "Content already exists in this project"}]
    filed = board.add_to_board({"input": {"contentId": late}})
    assert filed["data"] == {"addProjectV2ItemById": {"item": {"id": board._issue(late).item_id}}}
    assert items(late) != []
    assert min(MEASURED_PLACEMENT_SECONDS) == 0.453
    assert max(MEASURED_PLACEMENT_SECONDS) == 1.837


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


def test_a_project_member_listing_is_charged_as_one_items_read_and_a_batched_read_is_not() -> None:
    """One issue's single page of sub-issues costs an item read; reads of several items do not.

    The adopted store lists a project's members as `subIssues` of the one project issue it
    names by `$id`, a page of up to 23 at a time, so the request reads one item — and the
    approved `plan-approval-seconds` evidence charges the approval's two listings that way.
    A request aliasing several issues' reads is a different request, still charged as the
    spike's 100-item page.
    """
    listing = _GraphQLRequest(
        "query($id:ID!,$memberFirst:Int!,$after:String){node(id:$id){__typename "
        "... on Issue{subIssues(first:$memberFirst,after:$after){pageInfo{hasNextPage "
        "endCursor} nodes{__typename}}}}}",
        {"id": "I_kwDOAAAAAAAAAAAA", "memberFirst": 23, "after": None},
        0.0,
    )
    batched = _GraphQLRequest(
        "query($id0:ID!,$id1:ID!){i0:node(id:$id0){__typename} i1:node(id:$id1){__typename}}",
        {"id0": "I_kwDOAAAAAAAAAAAA", "id1": "I_kwDOAAAAAAAAAAAB"},
        0.0,
    )

    assert listing.operation is _Operation.SUB_ISSUES
    assert request_latency_ms(listing) == REQUEST_LATENCY_MS[RequestKind.ITEM_READ]
    assert batched.operation is _Operation.ISSUE_DETAILS
    assert request_latency_ms(batched) == REQUEST_LATENCY_MS[RequestKind.BATCHED_READ]
    assert {
        operation for operation, kind in REQUEST_KINDS.items() if kind is RequestKind.BATCHED_READ
    } == {
        _Operation.ISSUE_DETAILS,
        _Operation.ISSUE_COPY_DETAILS,
        _Operation.ORIGIN_LOOKUPS,
        _Operation.ORIGIN_LOOKUPS_WITH_MEMBERS,
        _Operation.CREATED_BOARD_ITEMS,
    }


def test_a_selection_the_answer_omits_is_named_by_its_path() -> None:
    """Each field a document selects is owed, through fragments, aliases and lists.

    A spread, an inline fragment on the answered type and an aliased field are each walked; a
    field answered `null` is answered; a selection its `@include` or `@skip` leaves out, or an
    inline fragment on another type, is not owed.
    """
    document = (
        "query($id:ID!,$more:Boolean!){node(id:$id){__typename ...Held "
        '... on Issue{edges:blockedBy(first:5,after:"a{b"){nodes{id}} '
        "extra @include(if:$more) gone @skip(if:true)} ... on DraftIssue{draft}}} "
        "fragment Held on Issue{id title}"
    )
    whole = {
        "node": {
            "__typename": "Issue",
            "id": "I_1",
            "title": None,
            "edges": {"nodes": [{"id": "I_2"}, {"id": "I_3"}]},
        }
    }

    assert unanswered_selections(document, {"more": False}, whole) == []
    assert unanswered_selections(document, {"more": True}, whole) == ["node.extra"]
    no_edge = {"node": {**whole["node"], "edges": {"nodes": [{"id": "I_2"}, {}]}}}
    assert unanswered_selections(document, {"more": False}, no_edge) == ["node.edges.nodes[1].id"]
    no_spread = {"node": {key: value for key, value in whole["node"].items() if key != "title"}}
    assert unanswered_selections(document, {"more": False}, no_spread) == ["node.title"]
    # A union's member names no `__typename`: it owes the fragment whose fields it answers.
    union = "query{values{nodes{... on Text{text field{id}} ... on Select{name field{id}}}}}"
    answered = {"values": {"nodes": [{"text": "t", "field": {"id": "F"}}, {"name": "n"}]}}
    assert unanswered_selections(union, {}, answered) == ["values.nodes[1].field"]


def _at(answered: object, *path: str) -> object:
    """What ``answered`` holds at ``path``, each step narrowed to the object it reads."""
    for step in path:
        assert isinstance(answered, dict), (step, answered)
        answered = answered[step]
    return answered


def _created(board: _Board, title: str, parent: str | None = None) -> str:
    """The node id of an issue created on ``board``, filed under ``parent`` when one is named."""
    payload: dict[str, object] = {
        "repositoryId": REPOSITORY_NODE_IDS[CONFIGURED_REPOSITORY],
        "title": title,
        "body": "",
    }
    if parent is not None:
        payload["parentIssueId"] = parent
    return str(_at(board.create_issue({"input": payload}), "data", "createIssue", "issue", "id"))


def test_a_members_listing_answers_the_project_and_each_member_with_what_blocks_it() -> None:
    """The board answers a listing's project issue and every member whole, `blockedBy` too."""
    board = _Board()
    project_id = _created(board, "p")
    first, second = (_created(board, title, project_id) for title in ("a", "b"))
    board.add_blocked_by({"input": {"issueId": second, "blockingIssueId": first}})
    document = (
        "query($id:ID!){node(id:$id){__typename ...Held ... on Issue{"
        "blockedBy(first:5){nodes{id}pageInfo{hasNextPage endCursor}} "
        "subIssues(first:23){pageInfo{hasNextPage endCursor} nodes{__typename ...Held "
        "... on Issue{blockedBy(first:5){nodes{id}pageInfo{hasNextPage endCursor}}}}}}}} "
        "fragment Held on Issue{id title body parent{id} subIssuesSummary{total}}"
    )
    data = _at(board.sub_issues_response(project_id), "data")

    assert unanswered_selections(document, {"id": project_id}, data) == []
    assert _at(data, "node", "id") == project_id
    members = _at(data, "node", "subIssues", "nodes")
    assert isinstance(members, list)
    blockers = {_at(member, "id"): _at(member, "blockedBy", "nodes") for member in members}
    assert blockers == {first: [], second: [board._issue(first).content()]}
    # The answer the double gave before it answered the listing whole is named, field by field.
    listing = _at(data, "node", "subIssues")
    assert isinstance(listing, dict)
    bare = {"node": {"__typename": "Issue", "subIssues": listing | {"nodes": []}}}
    assert "node.id" in unanswered_selections(document, {"id": project_id}, bare)
    assert "node.blockedBy" in unanswered_selections(document, {"id": project_id}, bare)


def test_a_re_copys_aliased_issue_updates_apply_each_sent_slot_as_one_write() -> None:
    """The store's `update<n>` batch is its own operation, and each sent slot one issue update."""
    slots = "".join(
        f" update{n}:updateIssue(input:$update{n}) @include(if:$send{n}){{issue{{id}}}}"
        for n in range(COPY_WRITE_SLOTS)
    )
    document = f"mutation{{{slots}}}"
    request = _GraphQLRequest(document, {}, 0.0)
    assert request.operation is _Operation.UPDATE_ISSUES
    assert copy_batch_drift(_Operation.UPDATE_ISSUES, document) == []

    board = _Board()
    ids = [_created(board, title) for title in ("a", "b", "c")]
    variables: dict[str, object] = {
        f"update{n}": {"id": ids[min(n, 1)], "body": f"body {n}"} for n in range(COPY_WRITE_SLOTS)
    }
    variables |= {f"send{n}": n < 2 for n in range(COPY_WRITE_SLOTS)}
    answered = board.write_batch(
        COPY_BATCHES[_Operation.UPDATE_ISSUES], {_Operation.UPDATE_ISSUE: board.update_issue}
    )(variables)

    assert answered == {
        "data": {
            "update0": {"issue": {"id": ids[0]}},
            "update1": {"issue": {"id": ids[1]}},
        }
    }
    assert [board._issue(issue).body for issue in ids] == ["body 0", "body 1", ""]
    assert (
        request_latency_ms(_GraphQLRequest(document, variables, 0.0))
        == (REQUEST_LATENCY_MS[RequestKind.ALIASED_MUTATIONS])
    )
