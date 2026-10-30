"""Follow-up settle against the locked plan store and the loopback Linear wire.

Kept beside the wire's own checks rather than in the package's unit tier, because it runs
the real store CLI against a served loopback.
"""

from __future__ import annotations

from pathlib import Path

import onetaskgraph_user_layer
import pytest
from linear_board import linear_board

from orchestrator import follow_up_comments as comments


def test_a_body_linear_respelled_lands_by_the_stores_own_word(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Settle reads a body Linear stored in its own spelling as landed, from the store's answer.

    Driven through the locked store against the loopback Linear, which stores a list directly
    after a paragraph line with a blank line before it, as real Linear does: the update the
    settle sends answers the task as Linear stored it with `content` in `written`, which
    reads as landed though the answered body is not the one sent. The same update sent again
    answers `written: []`, the store finding nothing to write, which vouches nothing: a write
    whose body never reached the store answers alike. Nor does a `written` that is no list of
    field names naming `content`.
    """
    monkeypatch.setenv(
        "XDG_CONFIG_HOME",
        str(
            onetaskgraph_user_layer.installed(
                tmp_path / "config", onetaskgraph_user_layer.example()
            )
        ),
    )
    source = onetaskgraph_user_layer.ORGANIZATION_PLANS
    body = "**Contributing locations.**\n- one"
    with linear_board() as board:
        for key, value in board.environment(source).items():
            monkeypatch.setenv(key, value)
        board.create_issue("respelled", "A ticket", "Plain.")
        issue = f"{source}:respelled"
        sent = comments._update_words(issue, {"content": body}, tmp_path)
        answers = [comments.store_command(sent) for _ in range(2)]
    assert [answer.status for answer in answers] == [0, 0], answers
    assert [answer.document and answer.document.get("written") for answer in answers] == [
        ["content"],
        [],
    ], answers
    for answer in answers:
        task = (answer.document or {}).get("task")
        assert isinstance(task, dict) and task["content"].rstrip() != body, task
    assert comments.outcome(answers[0], issue, {"content": body}).state is None, answers[0]
    document = answers[0].document or {}
    for written in ([], None, [None], [{}], "content", ["title"]):
        unvouched = comments.outcome(
            comments.StoreAnswer(0, {**document, "written": written}, ""), issue, {"content": body}
        )
        assert unvouched.state in ("failed", "partial"), (written, unvouched)
        assert unvouched.error == "the task it answered does not hold the requested content"
