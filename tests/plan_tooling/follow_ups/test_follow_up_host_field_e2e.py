"""A copied follow-up ticket's host lands on the `followups` board's `Host` field.

The installed plan store and `python -m orchestrator.follow_up_tickets copy` run for real,
under this repository's committed `onetaskgraph.yaml`, against the loopback board in
`tests/github_board.py`: the projection is the store's, configured here, so what is proven
is that this host's configuration makes a real copy write the record's `host` onto the
board, and that a board without the field refuses the copy by name until the operator's
`just plans sources fields followups --apply` creates it.
"""

from __future__ import annotations

import json
from pathlib import Path

import github_board as host_board

from orchestrator import follow_up_tickets as tickets

#: The machine the ticket below names as where its verification ran.
VERIFIED_ON = "triage-box.example"


def _copied_row(destination: str) -> host_board._Issue:
    """The board row the copy created, which it names as its qualified ``destination``."""
    (row,) = [
        issue
        for issue in host_board.BOARD.issues_created_and_kept()
        if f"{tickets.BOARD}:{issue.content_id}" == destination
    ]
    return row


# llmlint: ignore-block[e2e_not_mocked, expensive_tests_stay_behind_their_own_edge]  # noqa: E501 - directive rule lists occupy one line
# Only GitHub's wire is simulated, by the loopback board every follow-up journey here uses,
# because the real one is the live `followups` board every run shares; the plan store, the
# follow-up module and the recipe stay real. These journeys belong to plan-tooling-follow-ups,
# as tests/plan_tooling/AGENTS.md requires, behind that project's own edge, whose key is the
# subject's and is shared by every journey in it; narrowing it is not this test's change.
def test_a_copied_ticket_s_host_is_projected_onto_the_board_s_host_field(
    tmp_path: Path,
) -> None:
    environment, root = host_board._followups_environment(tmp_path)
    ticket = host_board._follow_up_ticket(host_board.SIBLING_REPOSITORY, verified_on=VERIFIED_ON)
    path = host_board._written_ticket(root, ticket)
    with host_board._serving_followups(environment, fields=True):
        assert host_board._decided_status(environment, path) == tickets.Status.PROPOSED
        copied = host_board._follow_up_step(
            environment, "copy", "--board", tickets.BOARD, str(path)
        )
        assert copied.returncode == 0, copied.stdout + copied.stderr
        row = _copied_row(json.loads(copied.stdout)["destination"])
    assert row.host == VERIFIED_ON, (
        f"the copied ticket's `Host` field holds {row.host!r}, and its record names "
        f"{VERIFIED_ON!r} as the machine its verification ran on"
    )


def test_a_board_without_the_host_field_refuses_the_copy_until_the_field_setup_creates_it(
    tmp_path: Path,
) -> None:
    environment, root = host_board._followups_environment(tmp_path)
    ticket = host_board._follow_up_ticket(host_board.SIBLING_REPOSITORY, verified_on=VERIFIED_ON)
    path = host_board._written_ticket(root, ticket)
    with host_board._serving_followups(environment, fields=True):
        host_board.BOARD.has_host_field = False
        assert host_board._decided_status(environment, path) == tickets.Status.PROPOSED
        refused = host_board._follow_up_step(
            environment, "copy", "--board", tickets.BOARD, str(path)
        )
        assert refused.returncode != 0, refused.stdout + refused.stderr
        assert "Host" in refused.stderr and "sources fields" in refused.stderr, refused.stderr
        assert host_board.BOARD.issues_created_and_kept() == [], (
            "the refused copy left an issue on the board, so it wrote before it refused"
        )

        set_up = host_board._followups_command(
            environment, ["just", "plans", "sources", "fields", tickets.BOARD, "--apply"]
        )
        assert set_up.returncode == 0, set_up.stdout + set_up.stderr
        assert host_board.BOARD.has_host_field, "the field setup did not create `Host`"
        copied = host_board._follow_up_step(
            environment, "copy", "--board", tickets.BOARD, str(path)
        )
        assert copied.returncode == 0, copied.stdout + copied.stderr
        row = _copied_row(json.loads(copied.stdout)["destination"])
    assert row.host == VERIFIED_ON, row.host


# llmlint: ignore-end[e2e_not_mocked, expensive_tests_stay_behind_their_own_edge]  # noqa: E501 - directive rule lists occupy one line
