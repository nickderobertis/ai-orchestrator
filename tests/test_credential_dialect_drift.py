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

What `origin/main` holds is onetaskgraph's live state, so every disagreement found here is
settled by `tests/sibling_facts.py` rather than asserted: a change editing a registration
file fails for it, and any other change has it reported as `SiblingDrift`, because
onetaskgraph moving its parser in its own repository refuses no unrelated push here.

llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] The subject is a registered
checkout of another repository, outside this workspace and so outside every `nx.json`
input key — hence `reads_checkouts`, the uncached tier `just check` runs, where a memo
cannot replay a green across the parser change this exists to catch.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

import pytest
from credential_dialect import DIALECT, Rule
from registered_checkouts import (
    TRACKED_CHECKOUTS,
    UnreadableSibling,
    sibling_git,
)
from sibling_facts import (
    ENCLOSING_COMPARISON,
    REGISTRATION_FILES,
    SiblingDrift,
    commit_change,
    published_clone,
    registered_checkouts,
    scratch_checkout,
    settle_drift,
)

from orchestrator.root import REPO_ROOT

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


def _published_parser() -> str | None:
    """onetaskgraph's credentials parser, as its own repository publishes it.

    None, with the reason settled, where that ref shows no such file.
    """
    checkouts = registered_checkouts()
    checkout = checkouts.get(ONETASKGRAPH)
    if checkout is None:
        pytest.skip(
            f"no registered checkout of {ONETASKGRAPH} on this host, so nothing here can "
            f"reconcile the dialect against its own parser. {TRACKED_CHECKOUTS} lists "
            "every path `just repos-apply` registers; clone it into one of them"
        )
    return parser_at(checkout)


def parser_at(
    checkout: Path,
    *,
    root: Path = REPO_ROOT,
    comparison: Mapping[str, str] = ENCLOSING_COMPARISON,
) -> str | None:
    """The parser ``checkout`` publishes at its fetched base, or None with the reason settled.

    Decoded strictly, because the bytes are onetaskgraph's to move and a replaced byte can
    leave every fragment below intact: a source that stopped being UTF-8, like one that
    is gone or a checkout that cannot be read, is settled, never raised.
    """

    def settled(finding: str) -> None:
        settle_drift([finding], root=root, comparison=comparison)

    try:
        read = sibling_git(checkout, "show", f"{PUBLISHED}:{SECRETS_SOURCE}")
    except UnreadableSibling as error:
        settled(f"{error}, so {SECRETS_SOURCE} at {PUBLISHED} cannot be reconciled")
        return None
    if read.returncode != 0:
        settled(
            f"the {ONETASKGRAPH} checkout at {checkout} cannot show {SECRETS_SOURCE} at "
            f"{PUBLISHED}: {read.stderr.decode('utf-8', 'replace').strip()}. Fetch that ref, "
            f"or correct {SECRETS_SOURCE} here if the parser moved inside that repository"
        )
        return None
    try:
        return read.stdout.decode("utf-8")
    except UnicodeDecodeError as error:
        settled(
            f"{SECRETS_SOURCE} at {PUBLISHED} is not UTF-8 in the {ONETASKGRAPH} checkout "
            f"at {checkout} ({error}), so the dialect cannot be reconciled against it"
        )
        return None


def rule_findings(rule: Rule, parser: str) -> list[str]:
    """Each fragment of ``rule`` that ``parser`` no longer spells, as a finding."""
    return [
        f"onetaskgraph no longer implements '{rule.name}' as `{fragment}`. This "
        f"checkout's `.env` and {SECRETS_SOURCE} have parted company on syntax, so "
        "an operator writing both files now writes one of them wrongly: re-read that "
        "parser, then move `scripts/credentials-env.sh` and `tests/credential_dialect.py` "
        "together, or record here that the two shapes have deliberately diverged"
        for fragment in rule.upstream
        if fragment not in parser
    ]


def transformation_findings(parser: str) -> list[str]:
    """What ``parser``'s `fn parse(` applies that no row accounts for, as a finding.

    A parser that no longer defines `fn parse(` at all is the one finding, since there is
    then nothing to read the transformations off.
    """
    if "fn parse(" not in parser:
        return [f"{SECRETS_SOURCE} at {PUBLISHED} no longer defines `fn parse(`"]
    body = parser[parser.index("fn parse(") :]
    applied = set(re.findall(r"\b(line|name|value)\.(\w+)", body))
    unaccounted = {
        f"{subject}.{call}"
        for subject, call in applied
        if f"{subject}.{call}" not in ACCOUNTED_TRANSFORMATIONS
    }
    return (
        [
            f"onetaskgraph's credentials parser now applies {sorted(unaccounted)}, which no "
            "row of `tests/credential_dialect.py` accounts for. A rule added there and not "
            "here is a `.env` line that means one thing to onetaskgraph and another to this "
            "repository: read that parser, then either add the rule to "
            "`scripts/credentials-env.sh` and a row to `DIALECT`, or add the call here with "
            "a note saying why this repository deliberately does not follow it"
        ]
        if unaccounted
        else []
    )


# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] Settled rather than asserted
# because ai-orchestrator#1529 rules, and this check's task states as a criterion, that a check
# whose subject is a sibling's live state refuses only a push changing one of the five registration
# files and reports the drift otherwise; `tests/sibling_facts.py`'s `settle_drift` is the one place
# that is decided, and `tests/test_sibling_drift.py` drives both pushes through it.
@pytest.mark.parametrize("rule", DIALECT, ids=lambda rule: rule.name)
def test_each_dialect_rule_is_still_what_onetaskgraph_implements(rule) -> None:  # noqa: ANN001 - the row type is `credential_dialect.Rule`; parametrize hands it over untyped
    """Every rule this repository's loader implements is still upstream's rule.

    Compared against source expressions rather than the doc comment above them: prose
    can be reworded without the behaviour moving, and — the direction that matters — can
    sit unchanged while the behaviour moves underneath it.
    """
    parser = _published_parser()
    if parser is None:
        return
    settle_drift(rule_findings(rule, parser))


def test_onetaskgraph_applies_no_rule_the_dialect_does_not_account_for() -> None:
    """A rule *added* upstream fails here, rather than becoming a silently wrong value.

    The rules above catch a rule that changed or went away. Nothing in them could catch
    one that appeared — the local parser would go on passing its own tests while an
    operator's file quietly meant something else — so the parser's transformations are
    read as a closed set and compared whole.
    """
    parser = _published_parser()
    if parser is None:
        return
    settle_drift(transformation_findings(parser))


# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]


@pytest.mark.parametrize(
    ("changed", "settles"),
    [("config/onevcs.checkouts", "fails"), ("personas/crozier/crozier-corpus.yaml", "reports")],
)
def test_a_published_parser_that_is_not_utf_8_is_settled_though_every_rule_is_intact(
    tmp_path: Path, changed: str, settles: str
) -> None:
    """onetaskgraph's bytes moving off UTF-8 is its live state, settled like any drift.

    Every fragment every rule names is still spelled, so replacing the bad bytes would
    read as a parser nothing is wrong with; decoding is what decides.
    """
    intact = "\n".join(fragment for rule in DIALECT for fragment in rule.upstream)
    checkout = scratch_checkout(
        tmp_path / "onetaskgraph",
        ONETASKGRAPH,
        {SECRETS_SOURCE: intact.encode() + b"\n// \xff\xfe\n"},
    )
    clone = published_clone(tmp_path / "gated", ("personas/crozier/crozier-corpus.yaml",))
    commit_change(clone, changed)

    if settles == "fails":
        with pytest.raises(pytest.fail.Exception, match=f"this change edits {changed}") as failed:
            parser_at(checkout, root=clone, comparison={})
        assert f"{SECRETS_SOURCE} at {PUBLISHED} is not UTF-8" in str(failed.value)
    else:
        with pytest.warns(SiblingDrift, match=f"{SECRETS_SOURCE} at {PUBLISHED} is not UTF-8"):
            assert parser_at(checkout, root=clone, comparison={}) is None


#: The two changes each reading of onetaskgraph's checkout below is settled for: one
#: editing a registration file, which fails, and one editing only a persona, which is
#: reported.
PERSONA = "personas/crozier/crozier-corpus.yaml"
PUSHES = ("config/onevcs.checkouts", PERSONA)
#: Every fragment every rule names, so a published parser carrying it passes every rule.
INTACT = "\n".join(fragment for rule in DIALECT for fragment in rule.upstream)


def _settled_parser(tmp_path: Path, checkout: Path, changed: str, said: str) -> None:
    """``parser_at(checkout)`` for a change editing ``changed``: failed, or reported and None."""
    clone = published_clone(tmp_path / "gated", (PERSONA,))
    commit_change(clone, changed)
    if changed in REGISTRATION_FILES:
        with pytest.raises(pytest.fail.Exception, match=f"this change edits {changed}") as failed:
            parser_at(checkout, root=clone, comparison={})
        assert said in str(failed.value)
    else:
        with pytest.warns(SiblingDrift) as reported:
            assert parser_at(checkout, root=clone, comparison={}) is None
        assert said in str(reported[0].message)


@pytest.mark.parametrize("changed", PUSHES)
def test_a_published_parser_gone_from_its_ref_is_settled_by_what_the_change_touches(
    tmp_path: Path, changed: str
) -> None:
    """onetaskgraph moving or removing its parser is its live state, settled like any drift."""
    checkout = scratch_checkout(tmp_path / "onetaskgraph", ONETASKGRAPH, {"README.md": "x\n"})

    _settled_parser(
        tmp_path,
        checkout,
        changed,
        f"cannot show {SECRETS_SOURCE} at {PUBLISHED}",
    )


@pytest.mark.parametrize("changed", PUSHES)
def test_a_parser_checkout_that_cannot_be_entered_is_settled_by_what_the_change_touches(
    tmp_path: Path, changed: str
) -> None:
    """An OS error running git in the checkout is a finding naming it, never a raise.

    The listed path is a regular file, so git cannot even start there: the OS error a
    checkout replaced by something else raises, whoever this process runs as.
    """
    checkout = tmp_path / "onetaskgraph"
    checkout.write_text("not a checkout\n", encoding="utf-8")

    _settled_parser(
        tmp_path,
        checkout,
        changed,
        f"{checkout} cannot be read: `git show` could not run there",
    )


@pytest.mark.parametrize("changed", PUSHES)
def test_a_published_parser_defining_no_parse_is_settled_by_what_the_change_touches(
    tmp_path: Path, changed: str
) -> None:
    """A parser that kept every rule's fragment but no `fn parse(` is drift, not a pass."""
    checkout = scratch_checkout(
        tmp_path / "onetaskgraph", ONETASKGRAPH, {SECRETS_SOURCE: f"{INTACT}\n"}
    )
    parser = parser_at(checkout)
    assert parser == f"{INTACT}\n"
    assert all(not rule_findings(rule, parser) for rule in DIALECT)
    findings = transformation_findings(parser)
    assert findings == [f"{SECRETS_SOURCE} at {PUBLISHED} no longer defines `fn parse(`"]

    clone = published_clone(tmp_path / "gated", (PERSONA,))
    commit_change(clone, changed)
    if changed in REGISTRATION_FILES:
        with pytest.raises(pytest.fail.Exception, match=f"this change edits {changed}") as failed:
            settle_drift(findings, root=clone, comparison={})
        assert "no longer defines `fn parse(`" in str(failed.value)
    else:
        with pytest.warns(SiblingDrift, match=r"no longer defines `fn parse\(`"):
            settle_drift(findings, root=clone, comparison={})
