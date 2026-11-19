"""The normalized-origin shape `orchestrator/host_adoption_check.py` reads a repository by."""

from __future__ import annotations

import pytest

from orchestrator import record_shapes


@pytest.mark.parametrize(
    ("repo", "origin"),
    (
        ("github.com/acme/service", "github.com/acme/service"),
        ("https://github.com/acme/service.git", "github.com/acme/service"),
        ("http://git.example.org/acme/service", "git.example.org/acme/service"),
        ("acme__service", None),
        ("/srv/checkouts/service", None),
        ("git@github.com:acme/service.git", None),
        ("github.com/acme", None),
        ("github.com/acme/service?ref=main", None),
        ("github.com/ac me/service", None),
        ("github.com/acme/service\x1b[0m", None),
    ),
)
def test_hosted_origin_is_the_one_shape_the_repositories_field_holds(
    repo: str, origin: str | None
) -> None:
    """A normalized `host/owner/name`, with the clone URL's scheme and suffix dropped.

    Everything else — an alias, a path, an scp-like URL — is answered `None`, because none
    of them is a value the record's `repositories` list holds: those travel on the reserved
    `onepipeline.repo` key.
    """
    assert record_shapes.hosted_origin(repo) == origin
