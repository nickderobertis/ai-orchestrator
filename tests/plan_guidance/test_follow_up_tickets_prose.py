"""The half of `tests/test_follow_up_tickets.py` that reads this repository's prose.

It sits in the `plan-guidance` project.

These hold the follow-up task's one status exception and its budget evidence to the prose
and the fixtures that state them. That project's key names each file they read, and
`tests/conftest.py` holds them to it; the tests of that module that read no prose stay
there, in `orchestrator:test`, and those reading a registered checkout in
`orchestrator:test-checkouts`.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import follow_up_variables
import pytest
from test_follow_up_tickets import (
    BOARD_STATUS,
    CAUSE,
    COMMIT,
    OTHER_RUN,
    RUN,
    _budget_account,
    _budget_cli,
    _budget_disposed,
    _cycle_entry,
    _feedback_task,
    _flat,
    _register_budget_repo,
    _standing_budget,
    _task,
    _template_withdrawal_description,
    _ticket,
    _write,
)

from orchestrator import follow_up_comments as comments
from orchestrator import follow_up_tickets as tickets
from orchestrator import plan_store
from orchestrator.root import REPO_ROOT


@pytest.fixture
def budget_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The `onevcs` registry the budget journeys write, as `tests/test_follow_up_tickets.py`
    gives its own: one per test, so no journey reads another's."""
    registry = tmp_path / "budget-registry"
    monkeypatch.setenv("ONEVCS_HOME", str(registry))
    return registry


@pytest.fixture
def drafts_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A drafts root the installed store reads, named the way a launch exports it, as
    `tests/test_follow_up_tickets.py` gives its own."""
    root = tmp_path / "follow-ups"
    root.mkdir()
    monkeypatch.setenv(follow_up_variables.root_name(), str(root))
    monkeypatch.setenv(follow_up_variables.plugin_name(), plan_store.WRITABLE_PLUGIN)
    return root


def test_the_one_status_exception_is_stated_once_in_the_task_the_gathering_and_agents_md() -> None:
    """Withdrawing this run's own proposal that is no longer relevant, and nothing else.

    The task and the gathering's preamble carry one wording, the module's; AGENTS.md states
    the same bound for a manager.
    """
    flat = _flat(_feedback_task())
    exception = _flat(tickets.WITHDRAWAL_EXCEPTION)

    assert flat.count(exception) == 1, flat
    for said in (
        "**Never change a board item's status except by one withdrawal.**",
        "Where the ticket of the issue a quoted comment sits on is no longer relevant — handled "
        "elsewhere, superseded, or its premise shown wrong, by the comment or by what "
        "investigating it found, whether or not the comment says so —",
        "that issue is this run's own item at `Proposal` (`Proposed` on Linear), withdraw it",
        "write `cancelled` as its ticket's `status` and validate the ticket",
        "the board is written once this run settles, and only while it still holds the item at "
        "`Proposal`",
        "say in that comment's reply and account entry that the item was withdrawn and why",
        "A ticket at `Deferred` (`Backlog` on Linear) may have its content edited but is never "
        "withdrawn: where it is no longer relevant, the reply says so with its evidence and "
        "recommends the person close it.",
        "No item at `Todo`, `Deferred`, `Queued` or `In Progress`, and no closed one, is ever "
        "withdrawn",
    ):
        assert said in exception, said
    assert "write `cancelled` as its `status` and validate it as step 1 says" in flat
    assert f"`{BOARD_STATUS} --board followups --withdraw <path of the ticket>`" not in flat
    assert "**Never change a board item's status**," not in flat, "the old rule still stands"
    assert "clearly" not in exception, "an explicit request is no longer the trigger"
    assert exception not in _flat(_task(redispatch=True)), (
        "the initial mode withdraws by its own rule"
    )
    preamble = comments.render(RUN, "followups", [], None, Path("/a/snapshot"))
    assert tickets.WITHDRAWAL_EXCEPTION in preamble
    agents = _flat((REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8"))
    assert (
        "It never changes a board item's status except by one withdrawal: this run's own item "
        "at `Proposal` (`Proposed` on Linear), when its ticket is no longer relevant."
    ) in agents
    assert (
        "changes only the ticket of an issue a quoted comment sits on — whenever the comment, or "
        "what investigating it found, makes a change right, not only on an explicit request —"
    ) in agents
    assert "an evidence comment carries only the new occurrence" in agents
    for described in (
        _flat(str(comments.__doc__)),
        _flat(_template_withdrawal_description()),
    ):
        assert "no longer relevant" in described, described
        assert "clearly" not in described, described


def test_budget_evidence_is_read_back_from_the_loopback_board(
    budget_registry: Path,
    drafts_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import github_board as remote

    environment, root = remote._followups_environment(tmp_path)
    with remote._serving_followups(environment, fields=True):
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        entry = _cycle_entry()
        entry["repository"] = remote._hosted(remote.CONFIGURED_REPOSITORY)
        budget = tickets._standing(entry)
        publication = tmp_path / "publication"
        _register_budget_repo(publication, str(budget.repository))
        _standing_budget(publication, entry)
        for word, cause in (("filed", CAUSE), ("budget-question", "closed-budget-cause")):
            earlier = _ticket(
                created_by_run=tickets.RunId(OTHER_RUN),
                owning_runs=(tickets.RunId(OTHER_RUN),),
                root_cause=tickets.RootCause(cause),
                repository=budget.repository,
                title=f"{tickets.repository_name(budget.repository)}: delivery overrun",
                basis=(tickets.Basis(budget.repository, tickets.Commit(COMMIT)),),
                drafts=(),
                budget=budget,
            )
            path = _write(root, earlier)
            assert tickets.main(["copy", "--board", "followups", str(path)]) == tickets.SOUND
            # The module's copy binds the stored record to the board's native item.
            read = tickets.read_ticket(path)
            assert read.board_item is not None
            item = f"followups:{read.board_item}"
            if word == "budget-question":
                plan_store.sdk(plan_store.client().task_status_set(item, tickets.Status.WITHDRAWN))
            else:
                plan_store.sdk(plan_store.client().task_status_set(item, tickets.Status.DEFERRED))
                local = dataclasses.replace(
                    earlier, created_by_run=tickets.RunId(RUN), owning_runs=(tickets.RunId(RUN),)
                )
                _write(root, local)
            entry["disposition"] = [_budget_disposed(item, word) | {"root_cause": cause}]
            _budget_account(root, entry)
            done = _budget_cli(root, RUN, "--board", "followups")
            assert done.returncode == tickets.UNSOUND
            assert "evidence" in done.stderr
            plan_store.sdk(
                plan_store.client().task_comment_add(
                    item,
                    body=tickets.render_comment(
                        RUN, cause, "Measured long gate. Should the budget change?"
                    ),
                )
            )
            assert tickets.main(["re-estimate", "--board", "followups", item]) == tickets.SOUND
            done = _budget_cli(root, RUN, "--board", "followups")
            assert done.returncode == tickets.SOUND, done.stderr


@pytest.mark.parametrize("feedback", [False, True])
def test_relatedness_contract_and_current_example_agree_in_both_modes(feedback: bool) -> None:
    task = _feedback_task() if feedback else _task()
    example = task.split("````markdown\n", 1)[1].split("````", 1)[0]
    assert example == tickets.ticket_example(RUN, "followups")
    assert tickets.RELATEDNESS_BAR in _flat(task)
    assert tickets.RELATEDNESS_BAR in _flat(example)
    assert tickets.DUPLICATE_SEARCH not in example
    assert "contributes to the same broader problem" not in task
    assert "related to or different from" not in task
    assert "different guarantee" not in task


def test_candidate_locations_follow_the_plain_schema_boundary_without_repurposing_it() -> None:
    task = _flat(_task())
    assert tickets.PLAIN_AT == 11 and tickets.SCHEMA == 12
    assert (
        "under `## Suggested fix`'s `**Contributing locations.**` on a schema-11 or later "
        "ticket, under `## Root cause`'s `**Contributing locations.**` on a schema-10 ticket"
    ) in task
