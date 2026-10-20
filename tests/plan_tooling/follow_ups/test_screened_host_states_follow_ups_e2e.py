"""The follow-up recipes in each host state the tracked public boundary meets.

The tracked `onetaskgraph.yaml` switches the store's public boundary on, so every host drafts,
verifies and answers follow-ups screened. A host meets that in one of three states
(`github_board.HostState`): no user layer; a user layer of
`config/onetaskgraph.user-layer.example.yaml`'s shape, over synthetic names, declaring its
organization's sources and its own stores private; and that layer in a process holding no
credential for the organization's sources. Each journey drives a recipe in all three, over
tickets naming no private repository: `just follow-up` drafting, `just follow-ups` over a run
with nothing to verify and the ticket copy its agent makes onto the board, and `just
follow-ups-answer-comments` gathering a person's comment and launching the run that replies.

The bench is `test_follow_ups_answer_comments_recipe_e2e.py`'s, imported rather than restated:
the loopback board served as `followups`, the paid model stood in by the command its turn runs,
and the recipes, the engine, the store and its layer real.

One gap of the pinned store is pinned rather than hidden. Its copy authenticates every private
source, so in the uncredentialed state a ticket's copy onto the board is refused naming the
organization's source and its missing credential (:data:`COPY_REFUSAL`); that journey accepts a
copy that lands or exactly that refusal, with nothing written. A comment gathered in that state
is on a ticket a process holding the credential filed, as a host's tickets are.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import boundary_home
import pytest
from github_board import LINEAR_KEY_ENV, HostState, host_state
from test_follow_ups_answer_comments_recipe_e2e import (
    OK,
    ON_A,
    REVIEWER,
    _answer,
    _commented,
    _filed,
    _follow_up_runs,
    _launching_checkout,
    _next_second,
    _replying,
    _scripted,
    _serving,
    _touched,
)
from test_follow_ups_recipe_e2e import Bench, _bench, _board_ids, _comments, _run
from waits import timeout as e2e_timeout

from orchestrator import follow_up_tickets as tickets
from orchestrator.root import REPO_ROOT

#: What the pinned store's ticket copy says in the uncredentialed state.
COPY_REFUSAL = f"authentication for this source failed: set environment variable {LINEAR_KEY_ENV}"


class Screened(NamedTuple):
    """One host state's bench, the checkout the recipes run from, and the store's record."""

    state: HostState
    bench: Bench
    checkout: Path
    record: Path


@pytest.fixture(params=list(HostState), ids=[state.value for state in HostState])
def screened(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Screened]:
    """A follow-ups bench on one host state, the loopback board served as `followups`."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    state = HostState(request.param)
    bench = _bench(tmp_path)
    for name, value in host_state(state, tmp_path / "host").items():
        if value is None:
            bench.environment.pop(name, None)
        else:
            bench.environment[name] = value
    bench.environment.update(
        {
            # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
            "FAKE_CODEX_RUN_ON_MARKER": str(tmp_path / "commands.json"),
            "FAKE_CODEX_RUN_ON_MARKER_LOG": str(tmp_path / "commands-ran.jsonl"),
            "FAKE_CODEX_PROMPT_LOG": str(tmp_path / "prompts.jsonl"),
            "UV_NO_SYNC": "1",
        }
    )
    checkout = _launching_checkout(tmp_path)
    with _serving(bench):
        yield Screened(state, bench, checkout, tmp_path / "store-record.jsonl")


def _filing(screened: Screened) -> Bench:
    """``screened``'s bench as the process that filed its tickets sees it.

    In the uncredentialed state that is a process of the same host holding the
    organization's credential, whose tracker this suite stands in by a folder, because no
    check may reach a Linear workspace: the organization-private state's layer.
    """
    if screened.state is not HostState.ORGANIZATION_UNCREDENTIALED:
        return screened.bench
    credentialed = host_state(HostState.ORGANIZATION_PRIVATE, screened.bench.tmp / "filing-host")
    environment = dict(screened.bench.environment)
    for name, value in credentialed.items():
        if value is None:
            environment.pop(name, None)
        else:
            environment[name] = value
    return screened.bench._replace(environment=environment)


def test_a_follow_up_is_drafted(screened: Screened) -> None:
    """`just follow-up` writes the draft into `drafts`, and the store reads it back."""
    run = f"drafted-{os.getpid()}"
    body = (
        "## What happened\nThe sweep reports a family it never examined.\n\n"
        "## Where\n`scripts/sweep.sh`, its trailer.\n\n"
        "## Why it is out of scope\nThis node owns the layer.\n\n"
        "## Evidence\n```\n0 B reclaimed\n```\n"
    )
    drafted = subprocess.run(
        ["just", "follow-up", run, "--title", "The sweep trailer names an unexamined family"]
        + ["--repository", boundary_home.THIS_REPOSITORY],
        cwd=REPO_ROOT,
        env=screened.bench.environment,
        input=body,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )

    assert drafted.returncode == 0, drafted.stdout + drafted.stderr
    (written,) = (screened.bench.drafts_root / "tasks" / run).rglob("*.md")
    assert "The sweep trailer names an unexamined family" in written.read_text(encoding="utf-8")


def test_the_follow_ups_recipe_runs_and_a_ticket_copies_onto_the_board(screened: Screened) -> None:
    """`just follow-ups` answers a run with nothing to verify, and a ticket copies onto the board.

    In the uncredentialed state the pinned store's copy may be refused naming the
    organization's source and its missing credential, with nothing written to the board.
    """
    bench = screened.bench
    empty = _run(["just", "follow-ups", f"empty-{os.getpid()}"], bench)
    assert empty.returncode == 0, empty.stdout + empty.stderr
    assert "nothing to verify and no follow-up run was launched" in empty.stdout, empty.stdout

    run = tickets.RunId(f"copied-{os.getpid()}")
    if screened.state is not HostState.ORGANIZATION_UNCREDENTIALED:
        issue = _filed(bench, run, "listing-cursor-skips-last-page")
        assert _comments(bench, issue) == []
        return
    try:
        _filed(bench, run, "listing-cursor-skips-last-page")
    except AssertionError as refused:
        assert COPY_REFUSAL in str(refused), str(refused)
        assert _board_ids(bench) == [], "a refused copy left an item on the board"


def test_a_person_s_comment_is_gathered_and_answered(screened: Screened) -> None:
    """`just follow-ups-answer-comments` launches the owning run, whose reply lands on the item."""
    bench = screened.bench
    run = tickets.RunId(f"answered-{os.getpid()}")
    issue = _filed(_filing(screened), run, "listing-cursor-skips-last-page")
    # A person comments after the run's last write, which is the boundary the gathering reads
    # past.
    _next_second()
    comment = _commented(bench, issue, ON_A, REVIEWER)
    _touched(issue)
    log = Path(bench.environment["FAKE_CODEX_PROMPT_LOG"])
    _scripted(bench, {run: [_replying(bench, run, log, bench.tmp / "agent-reads.jsonl")]})

    answered = _answer(bench, screened.checkout, screened.record)

    assert answered.result.returncode == OK, answered.report + answered.result.stderr
    assert _follow_up_runs(bench), answered.report
    replies = [
        one
        for one in _comments(bench, issue)
        if (owner := tickets.comment_owner(str(one["body"]))) is not None
        and owner.answers == comment
    ]
    assert len(replies) == 1, _comments(bench, issue)
