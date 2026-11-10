"""The shapes several readers share about a plan-store record."""

from __future__ import annotations

import pytest

from orchestrator import record_shapes


@pytest.mark.parametrize(
    ("value", "parts"),
    (
        ("followups:I_created_0", ("followups", "I_created_0")),
        ("drafts:a-run/tickets/a-cause", ("drafts", "a-run/tickets/a-cause")),
        # The native half is the source's own answer about its own records, so a colon in
        # it is the source's business: only the first one separates.
        ("followups:a:b", ("followups", "a:b")),
        ("I_created_0", None),
        ("", None),
        (":native", None),
        ("followups:", None),
    ),
)
def test_a_qualified_id_is_a_source_and_a_native_half(
    value: str, parts: tuple[str, str] | None
) -> None:
    """The one parser for the shape, which the `delivers` writers and `qualified` ask."""
    assert record_shapes.qualified_id(value) == parts


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


def test_a_record_renders_its_fields_then_its_body() -> None:
    assert record_shapes.frontmatter({"title": "T", "metadata": {"k": [1]}}, "Body\n\n") == (
        '---\ntitle: "T"\nmetadata:\n  "k": [1]\n---\n\nBody\n'
    )
