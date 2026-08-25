"""The credentials-file dialect, reconciled against the parser it was taken from.

`scripts/credentials-env.sh` reads this checkout's `.env` in the shape onetaskgraph
reads its own credentials file in, for the reason `tests/credential_dialect.py` states:
a host runs both, an operator writes both, and a value that means one thing here and
another there is a credential that fails authentication far from the file defining it.

A copied shape that nothing reconciles is the failure this gate exists for. It is not
hypothetical here — the first version of this loader parsed `TOKEN="ghp_..."` literally
and exported a value with the quotes still attached, which no local test could notice
because the local tests were written from the same reading as the parser. So this asks
the **other** parser: every rule `DIALECT` claims is still an expression in
onetaskgraph's own `secrets.rs`, and that parser makes no transformation `DIALECT` does
not account for — which is what catches a rule *added* there rather than changed.

What it deliberately does not do is reconcile the two files' *contents*. Those hold the
same names and may hold different values on purpose; AGENTS.md says which file wins and
why nothing warns about that. This gate is about syntax, not secrets, and reads only
onetaskgraph's source — never its credentials file.

Read at `origin/main` rather than the working tree: a checkout here is whatever somebody
last left it at, and an operator writing a `.env` today is writing against what
onetaskgraph published, not against a colleague's half-finished branch. The read lives
outside this workspace and so outside every cache key, which is why the module runs in
the uncached tier.
"""

from __future__ import annotations

import re
import subprocess

import pytest
from credential_dialect import DIALECT
from registered_checkouts import TRACKED_CHECKOUTS, registered_checkouts

pytestmark = pytest.mark.reads_checkouts

#: The repository whose dialect this one reads, and the file its parser lives in.
ONETASKGRAPH = "github.com/nickderobertis/onetaskgraph"
SECRETS_SOURCE = "crates/onetaskgraph-core/src/secrets.rs"

#: The published ref. See the module docstring: never the working tree.
PUBLISHED = "origin/main"

#: Every transformation onetaskgraph's parser applies to a line, a name, or a value,
#: as the source spells it. Recorded as a closed set so a rule *added* upstream — an
#: escape sequence, a variable expansion, a case fold — fails here rather than becoming
#: a difference between the two files that only shows up as a credential that does not
#: work. Each entry has a row of `DIALECT` accounting for it.
ACCOUNTED_TRANSFORMATIONS = frozenset(
    {
        "line.trim",
        "line.is_empty",
        "line.starts_with",
        "line.strip_prefix",
        "line.split_once",
        "name.trim",
        "value.trim",
        "value.len",
        "value.starts_with",
        "value.ends_with",
        "value.to_owned",
        # `is_variable_name`'s walk over the name's characters. This is a rule of the
        # dialect rather than an incidental call, and it has no `DIALECT` row because
        # what it decides is a *refusal* rather than a value: `scripts/credentials-env.sh`
        # implements the same grammar as `^[A-Za-z_][A-Za-z0-9_]*$`, and
        # `tests/test_credentials_env.py` proves it refuses a name outside it, naming
        # the line and printing no value.
        "name.chars",
    }
)


def _published_parser() -> str:
    """onetaskgraph's credentials parser, as its own repository publishes it."""
    checkouts = registered_checkouts()
    checkout = checkouts.get(ONETASKGRAPH)
    if checkout is None:
        pytest.skip(
            f"no registered checkout of {ONETASKGRAPH} on this host, so nothing here can "
            f"reconcile the dialect against its own parser. {TRACKED_CHECKOUTS} lists "
            "every path `just repos-apply` registers; clone it into one of them"
        )
    read = subprocess.run(
        ["git", "-C", str(checkout), "show", f"{PUBLISHED}:{SECRETS_SOURCE}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert read.returncode == 0, (
        f"the {ONETASKGRAPH} checkout at {checkout} cannot show {SECRETS_SOURCE} at "
        f"{PUBLISHED}: {read.stderr.strip()}. Fetch that ref, or correct "
        f"{SECRETS_SOURCE} here if the parser moved inside that repository"
    )
    return read.stdout


@pytest.mark.parametrize("rule", DIALECT, ids=lambda rule: rule.name)
def test_each_dialect_rule_is_still_what_onetaskgraph_implements(rule) -> None:  # noqa: ANN001 - the row type is `credential_dialect.Rule`; parametrize hands it over untyped
    """Every rule this repository's loader implements is still upstream's rule.

    Compared against source expressions rather than the doc comment above them: prose
    can be reworded without the behaviour moving, and — the direction that matters — can
    sit unchanged while the behaviour moves underneath it.
    """
    parser = _published_parser()
    for fragment in rule.upstream:
        assert fragment in parser, (
            f"onetaskgraph no longer implements '{rule.name}' as `{fragment}`. This "
            f"checkout's `.env` and {SECRETS_SOURCE} have parted company on syntax, so "
            "an operator writing both files now writes one of them wrongly: re-read that "
            "parser, then move `scripts/credentials-env.sh` and `tests/credential_dialect.py` "
            "together, or record here that the two shapes have deliberately diverged"
        )


def test_onetaskgraph_applies_no_rule_the_dialect_does_not_account_for() -> None:
    """A rule *added* upstream fails here, rather than becoming a silently wrong value.

    The rules above catch a rule that changed or went away. Nothing in them could catch
    one that appeared — the local parser would go on passing its own tests while an
    operator's file quietly meant something else — so the parser's transformations are
    read as a closed set and compared whole.
    """
    parser = _published_parser()
    body = parser[parser.index("fn parse(") :]
    applied = set(re.findall(r"\b(line|name|value)\.(\w+)", body))
    unaccounted = {
        f"{subject}.{call}"
        for subject, call in applied
        if f"{subject}.{call}" not in ACCOUNTED_TRANSFORMATIONS
    }
    assert not unaccounted, (
        f"onetaskgraph's credentials parser now applies {sorted(unaccounted)}, which no "
        "row of `tests/credential_dialect.py` accounts for. A rule added there and not "
        "here is a `.env` line that means one thing to onetaskgraph and another to this "
        "repository: read that parser, then either add the rule to "
        "`scripts/credentials-env.sh` and a row to `DIALECT`, or add the call here with "
        "a note saying why this repository deliberately does not follow it"
    )
