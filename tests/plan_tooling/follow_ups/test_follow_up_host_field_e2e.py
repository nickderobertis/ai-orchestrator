"""A copied follow-up ticket's host lands on the `followups` board's `Host` field.

The installed plan store and `python -m orchestrator.follow_up_tickets copy` run for real,
under this repository's committed `onetaskgraph.yaml`, with its public boundary switched on in
a configuration home of the suite's own and classifying the ticket's repository against a state
root that registers it public (`tests/follow_up_boundary.py`), against the loopback board in
`tests/github_board.py`: the projection is the store's, configured here, so what is proven is
that this host's configuration makes a real copy write the record's `host` onto the board, and
that a board without the field refuses the copy by name until the operator's `just plans
sources fields followups --apply` creates it.

The last journeys hold the host's public phrases (`$ONEVCS_HOME/public-phrases.yml`, onevcs's
`docs/contract.md`) to the same write path: a state root of their own also registers a
synthetic private identity whose committed manifest declares a package name, which the
boundary refuses in public text, and a phrase file lists one exact host name carrying that
name. The store's `write_policy` reaches the pinned `onevcs` through `PATH`, so a copy under
that root is the host's own write path, with every name invented.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import boundary_home
import follow_up_boundary
import github_board as host_board

from orchestrator import follow_up_tickets as tickets

#: The machine the ticket below names as where its verification ran.
VERIFIED_ON = "triage-box.example"


#: A synthetic private repository the phrase journeys' state root registers.
PRIVATE_REPOSITORY = "github.com/nickderobertis/quorvel-ledger"

#: The package name that repository's committed manifest declares: a private term, matched as
#: a whole word in any public text.
PRIVATE_TERM = "quorvel"

#: The store's words for a write refused because its text names a private repository's term.
TERM_REFUSAL = "carries a term of a private repository in its text"

#: A machine name carrying that term as a hyphen-separated word, which the host lists as public.
PUBLIC_PHRASE = f"bench-{PRIVATE_TERM}"


def _phrase_home(root: Path, *, phrases: tuple[str, ...]) -> Path:
    """A state root registering the ticket's public repository and `PRIVATE_REPOSITORY`.

    The private checkout commits a `pyproject.toml` naming `PRIVATE_TERM`, which is what the
    boundary derives the term from; ``phrases``, when any, are written as the host's
    `public-phrases.yml` under the root, and none leaves the file absent.
    """
    public = (boundary_home.THIS_REPOSITORY, host_board._hosted(host_board.SIBLING_REPOSITORY))
    home = boundary_home.registered_home(root, public=public)
    boundary_home.declared_home(root, public, (PRIVATE_REPOSITORY,))
    checkout = root / "private-checkout"
    checkout.mkdir()
    (checkout / "pyproject.toml").write_text(
        f'[project]\nname = "{PRIVATE_TERM}"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    process = boundary_home.environment(None, home)
    for arguments in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "add", "pyproject.toml"],
        ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid"]
        + ["commit", "-q", "-m", "init"],
        ["git", "remote", "add", "origin", f"https://{PRIVATE_REPOSITORY}.git"],
        [str(boundary_home.ONEVCS), "register", str(checkout)],
    ):
        done = subprocess.run(
            arguments, cwd=checkout, env=process, text=True, capture_output=True, check=False
        )
        assert done.returncode == 0, f"{arguments[:3]}: {done.stdout}{done.stderr}"
    if phrases:
        listed = "".join(f'  - "{phrase}"\n' for phrase in phrases)
        (home / "public-phrases.yml").write_text(
            f"version: 1\nphrases:\n{listed}", encoding="utf-8"
        )
    return home


def _phrase_copy(
    tmp_path: Path, *, phrases: tuple[str, ...], evidence: str = ""
) -> tuple[subprocess.CompletedProcess[str], list[host_board._Issue]]:
    """Copy a ticket verified on `PUBLIC_PHRASE` onto `followups` under a phrase state root.

    Answers the copy and the issues the board kept, read before the board stops serving.
    """
    environment, root = host_board._followups_environment(tmp_path)
    home = _phrase_home(tmp_path / "boundary", phrases=phrases)
    environment = boundary_home.environment(environment, home)
    ticket = host_board._follow_up_ticket(
        host_board.SIBLING_REPOSITORY, evidence=evidence, verified_on=PUBLIC_PHRASE
    )
    path = host_board._written_ticket(root, ticket)
    with host_board._serving_followups(environment, fields=True):
        assert host_board._decided_status(environment, path) == tickets.Status.PROPOSED
        copied = host_board._follow_up_step(
            environment, "copy", "--board", tickets.BOARD, str(path)
        )
        return copied, host_board.BOARD.issues_created_and_kept()


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
    environment = follow_up_boundary.environment(environment)
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
    environment = follow_up_boundary.environment(environment)
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


def test_a_host_named_by_a_listed_public_phrase_is_copied_onto_the_host_field(
    tmp_path: Path,
) -> None:
    copied, kept = _phrase_copy(tmp_path, phrases=(PUBLIC_PHRASE,))
    assert copied.returncode == 0, copied.stdout + copied.stderr
    destination = json.loads(copied.stdout)["destination"]
    (row,) = [issue for issue in kept if f"{tickets.BOARD}:{issue.content_id}" == destination]
    assert row.host == PUBLIC_PHRASE, (
        f"the copied ticket's `Host` field holds {row.host!r}, and the host lists "
        f"{PUBLIC_PHRASE!r} as a public phrase"
    )


def test_the_same_host_is_refused_with_nothing_written_when_no_phrase_is_listed(
    tmp_path: Path,
) -> None:
    refused, kept = _phrase_copy(tmp_path, phrases=())
    assert refused.returncode != 0, refused.stdout + refused.stderr
    assert TERM_REFUSAL in refused.stderr, refused.stderr
    assert kept == [], "the refused copy left an issue on the board, so it wrote before it refused"


def test_the_bare_term_beside_the_listed_phrase_is_still_refused_with_nothing_written(
    tmp_path: Path,
) -> None:
    refused, kept = _phrase_copy(
        tmp_path,
        phrases=(PUBLIC_PHRASE,),
        evidence=f"The {PRIVATE_TERM} service answered the same way.",
    )
    assert refused.returncode != 0, refused.stdout + refused.stderr
    assert TERM_REFUSAL in refused.stderr, refused.stderr
    assert kept == [], "the refused copy left an issue on the board, so it wrote before it refused"


# llmlint: ignore-end[e2e_not_mocked, expensive_tests_stay_behind_their_own_edge]  # noqa: E501 - directive rule lists occupy one line
