"""The board stand-in's own auditing, held on requests constructed to break it.

`_bounded_step` is what every board journey reads a step's requests through, so the rule
it enforces is tested here directly, on a duplicate the store itself never sends.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from github_board import (
    RequestCost,
    _bounded_step,
    _GitHubFixture,
    _GraphQLRequest,
    _request_points,
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
