"""The credentials-file dialect this repository reads, stated once.

`scripts/credentials-env.sh` parses the gitignored `.env` a launch exports onto its
dispatches, and it reads it in the shape `onetaskgraph` reads its own credentials file
in. That is deliberate: a host running both holds two such files (AGENTS.md says which
is which and which wins), and an operator who has written one of them should not have
learnt a second `.env` to write the other. `TOKEN="ghp_..."` meaning one thing here and
another there is a credential that fails authentication far away from the file that
defines it.

Every rule of that dialect is a row below, and both sides of the claim read these rows
rather than restating them:

* `tests/test_credentials_env.py` drives the real loader over each row's line and
  asserts the value it exports — this repository's parser really behaves this way;
* `tests/test_credential_dialect_drift.py` asserts each row's rule is still what
  onetaskgraph's own parser implements — the shape has not moved underneath us.

Two hand-maintained copies would drift together and stay green, which is the whole
failure mode a copied dialect has.
"""

from __future__ import annotations

from typing import NamedTuple


class Rule(NamedTuple):
    """One rule of the dialect, with what proves it on each side."""

    #: What the rule is called in a failure, so a drift report names the behaviour that
    #: moved rather than a line number in somebody else's crate.
    name: str
    #: A `.env` this repository's loader is driven over.
    written: str
    #: The value `GH_PROJECTS_TOKEN` must hold afterwards.
    expected: str
    #: Fragments of onetaskgraph's own parser that implement this rule. Source
    #: expressions rather than prose: a doc comment can be reworded without the
    #: behaviour moving, and can stay put while the behaviour moves under it.
    upstream: tuple[str, ...]


DIALECT: tuple[Rule, ...] = (
    Rule(
        "double-quoted value",
        'GH_PROJECTS_TOKEN="ghp_quoted"\n',
        "ghp_quoted",
        ("fn unquote", "for quote in ['\"', '\\'']", "value.len() >= 2"),
    ),
    Rule(
        "single-quoted value",
        "GH_PROJECTS_TOKEN='ghp_quoted'\n",
        "ghp_quoted",
        ("value.starts_with(quote) && value.ends_with(quote)",),
    ),
    Rule(
        "quotes stripped only as a matching pair",
        'GH_PROJECTS_TOKEN=ghp_trailing"\n',
        'ghp_trailing"',
        ("value.starts_with(quote) && value.ends_with(quote)",),
    ),
    Rule(
        "mismatched quotes are part of the value",
        "GH_PROJECTS_TOKEN=\"ghp_mismatched'\n",
        "\"ghp_mismatched'",
        ("value.starts_with(quote) && value.ends_with(quote)",),
    ),
    Rule(
        "optional export prefix",
        "export GH_PROJECTS_TOKEN=ghp_exported\n",
        "ghp_exported",
        ('line.strip_prefix("export ")',),
    ),
    Rule(
        "leading blanks on a line",
        "   GH_PROJECTS_TOKEN=ghp_indented\n",
        "ghp_indented",
        ("let line = line.trim();",),
    ),
    Rule(
        "blanks around a value",
        "GH_PROJECTS_TOKEN=ghp_padded   \n",
        "ghp_padded",
        ("unquote(value.trim())",),
    ),
    Rule(
        "blanks around a name",
        "GH_PROJECTS_TOKEN   =ghp_padded_name\n",
        "ghp_padded_name",
        ("let name = name.trim();",),
    ),
    Rule(
        "a comment line",
        "  # an indented comment\nGH_PROJECTS_TOKEN=ghp_after_comment\n",
        "ghp_after_comment",
        ("line.is_empty() || line.starts_with('#')",),
    ),
    Rule(
        # A credential may contain one, and guessing where a comment begins inside a
        # value silently truncates a token.
        "no inline comment inside a value",
        "GH_PROJECTS_TOKEN=ghp_with#hash\n",
        "ghp_with#hash",
        ("line.is_empty() || line.starts_with('#')",),
    ),
    Rule(
        "the first `=` separates name from value",
        "GH_PROJECTS_TOKEN=ghp_with=equals\n",
        "ghp_with=equals",
        ("line.split_once('=')",),
    ),
    Rule(
        "a final line with no newline",
        "GH_PROJECTS_TOKEN=ghp_no_final_newline",
        "ghp_no_final_newline",
        ("text.lines()",),
    ),
)
