"""The typed detail-id grammar: what it admits, and what it refuses to guess at.

These ids are the *keys* a snapshot is searched by and the argument
`just history-show` resolves, so the properties worth pinning are the two that make
them usable as either: one thing has exactly one spelling, and a string that claims
a namespace but breaks its grammar is an error rather than a substring search.
"""

from __future__ import annotations

import pytest

from orchestrator.ids import (
    DetailIdError,
    GitId,
    GraphId,
    OneharnessId,
    PrId,
    looks_like_detail_id,
    parse_detail_id,
)

FULL_SHA = "0123abcdef4567890123abcdef4567890123abcd"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "oh:engineer-20260714T100000Z-123",
            OneharnessId("engineer-20260714T100000Z-123"),
        ),
        ("git:local/app@0123abc", GitId("local/app", "0123abc")),
        (f"git:acme/app@{FULL_SHA}", GitId("acme/app", FULL_SHA)),
        ("pr:acme/app#12", PrId("acme/app", 12)),
        ("graph:watch-me/2/api", GraphId("watch-me", 2, "api")),
    ],
)
def test_each_namespace_round_trips_through_its_wire_form(value: str, expected: object) -> None:
    """The namespace travels with the value, so one string is enough to resolve it."""
    parsed = parse_detail_id(value)
    assert parsed == expected
    assert str(parsed) == value


@pytest.mark.parametrize(
    "value",
    [
        "build-history-command-20260714T100000Z-123",  # a legacy oneharness id
        "history-command",  # a legacy substring
        "",
        "PR:acme/app#1",  # the prefixes this module owns are lowercase
        "https://github.com/acme/app/pull/1",
    ],
)
def test_a_string_claiming_no_namespace_is_not_an_id_at_all(value: str) -> None:
    """None means "hand this to the legacy substring search", never "malformed"."""
    assert not looks_like_detail_id(value)
    assert parse_detail_id(value) is None


@pytest.mark.parametrize(
    "value",
    [
        "oh:",
        "git:acme/app@xyzxyzx",  # not hex
        "git:acme/app@abc12",  # shorter than git's own unambiguous minimum
        f"git:acme/app@{FULL_SHA}0",  # longer than a sha
        "git:acme/app@ABC1234",  # git shas are lowercase
        "git:acmeapp@0123abc",  # no owner/name split
        "pr:acme/app#0",  # the synthesized local PR is not addressable
        "pr:acme/app#007",  # one PR must not have two spellings
        "pr:acme/app#abc",
        "pr:acme/app",
        "graph:watch-me/0/api",  # rounds count from 1
        "graph:watch-me/2/",
        "graph:watch-me/2",
    ],
)
def test_a_malformed_claim_on_a_namespace_raises_rather_than_degrading(value: str) -> None:
    """A typo in an id the user meant must not quietly answer a question they did not ask."""
    assert looks_like_detail_id(value)
    with pytest.raises(DetailIdError):
        parse_detail_id(value)


def test_constructing_an_id_out_of_contract_raises_at_the_boundary() -> None:
    """The grammar is a construction-time contract, not merely a parse result."""
    with pytest.raises(DetailIdError, match="git sha"):
        GitId(identity="acme/app", sha="not-hex")
    with pytest.raises(DetailIdError, match="repo identity"):
        GitId(identity="acme app", sha="0123abc")
    with pytest.raises(DetailIdError, match="positive integer"):
        PrId(identity="acme/app", number=0)
    with pytest.raises(DetailIdError, match="positive integer"):
        GraphId(run_id="watch-me", round=0, node="api")
    with pytest.raises(DetailIdError, match="node id"):
        GraphId(run_id="watch-me", round=1, node="")


def test_a_bool_is_not_a_number_even_though_python_says_it_is() -> None:
    """`True == 1`, so an unguarded id would happily render `pr:acme/app#True`."""
    with pytest.raises(DetailIdError):
        PrId(
            identity="acme/app",
            number=True,  # type: ignore[arg-type]  # deliberate runtime guard probe
        )
    with pytest.raises(DetailIdError):
        GraphId(
            run_id="watch-me",
            round=True,  # type: ignore[arg-type]  # deliberate runtime guard probe
            node="api",
        )


def test_an_abbreviated_git_id_matches_only_the_full_sha_it_prefixes() -> None:
    """An id copied out of a log resolves by prefix; the match runs one way only."""
    short = GitId(identity="local/app", sha=FULL_SHA[:7])
    assert short.abbreviated
    assert short.matches(FULL_SHA)
    assert not short.matches("9999abcdef4567890123abcdef4567890123abcd")

    exact = GitId(identity="local/app", sha=FULL_SHA)
    assert not exact.abbreviated
    assert exact.matches(FULL_SHA)
    # The unsound direction: a stored abbreviation must never claim a longer id.
    assert not exact.matches(FULL_SHA[:7])


def test_a_graph_id_names_the_round_directory_the_ledger_actually_writes() -> None:
    assert GraphId(run_id="watch-me", round=2, node="api").round_name == "round-02"
    assert GraphId(run_id="watch-me", round=13, node="api").round_name == "round-13"
