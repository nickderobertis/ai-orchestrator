"""The `onevcs` vocabularies `orchestrator/unpublished.py` restates, against onevcs's source.

The view reads things `onevcs` declares and it cannot import: the lifecycle a session
record's `state` is spelled in (:data:`~orchestrator.unpublished.HolderState`), the
`held_by.holding` words it prints as in flight (:data:`~orchestrator.unpublished.
IN_FLIGHT_HOLDINGS`), and the grammar a session token is minted in
(:data:`~orchestrator.unpublished.SESSION_TOKEN`). Each is a copy of another repository's declaration, so each is read back
against that declaration here rather than trusted: a lifecycle state onevcs adds, or a
token it mints in another shape, fails this before a row is joined to the wrong record or
a real session token is read as a manager session id — the own-sessions target.

The lifecycle is read at `config/onevcs.version`'s tag, because the view spawns the
`onevcs` CLI this checkout installs at that pin. The token grammar is read there *and* at
the release the engine links (`tests/test_engine_contracts.py`'s `ONEVCS`), because a
dispatch's session is minted by the engine's copy and the manager's by the CLI, and the
view is handed both. The checkout and the ref-reading are that module's, so how an
engine's source is reached is stated once.
"""

from __future__ import annotations

import re
import string
from typing import get_args

import pytest
from test_engine_contracts import ONEVCS, Engine, _pinned_tag, _source

from orchestrator import unpublished

pytestmark = pytest.mark.reads_checkouts

#: The `onevcs` the view spawns, at the release this checkout installs.
ONEVCS_CLI = Engine("onevcs", _pinned_tag("onevcs"), "crates/onevcs/src")

LIFECYCLE = re.compile(
    r'#\[serde\(rename_all = "kebab-case"\)\]\s*pub enum Lifecycle \{(?P<body>.*?)\n\}', re.S
)
HOLDING = re.compile(
    r'#\[serde\(rename_all = "kebab-case"\)\]\s*pub enum Holding \{(?P<body>.*?)\n\}', re.S
)
VARIANT = re.compile(r"^\s*([A-Z]\w*),", re.M)
SESSION_TOKEN_MINT = re.compile(
    r'pub fn session_token\(\) -> String \{\s*format!\("s-\{\}", short_digest\('
)
SHORT_DIGEST = re.compile(
    r"pub fn short_digest\(value: &str\) -> String \{\s*digest\(value\)\[\.\.(?P<length>\d+)\]"
)
HEX_DIGEST = re.compile(
    r'pub fn digest\(value: &str\) -> String \{.*?format!\("\{byte:02(?P<case>[xX])\}"\)', re.S
)
#: The digits each hex format spec renders a byte in.
HEX_ALPHABET = {"x": "0123456789abcdef", "X": "0123456789ABCDEF"}


def _kebab(variant: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "-", variant).lower()


# llmlint: ignore-block[tests_mirror_real_usage] This is a drift gate for copied wire
# vocabulary, so it must read the producer's pinned source declaration. The real CLI
# journey in this project's other test file proves the view's user behavior.
def test_the_holder_states_are_onevcs_s_session_lifecycle() -> None:
    declared = LIFECYCLE.search(_source(ONEVCS_CLI, "session.rs"))
    assert declared is not None, f"`Lifecycle` is no longer declared as read at {ONEVCS_CLI.ref}"
    wire = {_kebab(variant) for variant in VARIANT.findall(declared.group("body"))}
    assert wire, "`Lifecycle` declares no variant this read recognizes"
    assert wire == set(get_args(unpublished.HolderState)), (
        f"onevcs {ONEVCS_CLI.ref} spells a session's state {sorted(wire)}"
    )


# llmlint: ignore-end[tests_mirror_real_usage]


# llmlint: ignore-block[tests_mirror_real_usage] A drift gate for copied wire vocabulary
# reads the producer's pinned declaration; `test_unpublished_held_publication_e2e.py`
# beside this drives a `publication-running` holder through the real recipe.
def test_the_in_flight_holdings_are_onevcs_s_whole_holding_enum() -> None:
    """`IN_FLIGHT_HOLDINGS` names every `Holding` the CLI the view spawns can report.

    Equality rather than containment, both ways: a holding onevcs adds is a surface
    `--print-surface` would leave out, and one it drops is a word a consumer would wait
    for and never see.
    """
    declared = HOLDING.search(_source(ONEVCS_CLI, "session.rs"))
    assert declared is not None, f"`Holding` is no longer declared as read at {ONEVCS_CLI.ref}"
    wire = {_kebab(variant) for variant in VARIANT.findall(declared.group("body"))}
    assert wire, "`Holding` declares no variant this read recognizes"
    assert wire == set(unpublished.IN_FLIGHT_HOLDINGS), (
        f"onevcs {ONEVCS_CLI.ref} spells `held_by.holding` {sorted(wire)}, and the view's "
        f"IN_FLIGHT_HOLDINGS reads {sorted(unpublished.IN_FLIGHT_HOLDINGS)}"
    )


# llmlint: ignore-end[tests_mirror_real_usage]


# llmlint: ignore-block[tests_mirror_real_usage] The token regex is a copied producer
# declaration: only the pinned minting code proves the copy stays in step. The real
# session journey in this project separately exercises tokens through the CLI.
@pytest.mark.parametrize("onevcs", [ONEVCS_CLI, ONEVCS], ids=["cli-pin", "engine-linked"])
def test_the_session_token_grammar_is_exactly_what_onevcs_mints(onevcs: Engine) -> None:
    """Both directions: every token the release can mint matches, and nothing else does.

    The width and the alphabet are read off the minting code rather than assumed, so a
    release that widens the digest, or renders it in upper case, fails here in whichever
    direction the copy is now wrong.
    """
    ids = _source(onevcs, "ids.rs")
    assert SESSION_TOKEN_MINT.search(ids), "`session_token` no longer mints `s-<short digest>`"
    rendered = HEX_DIGEST.search(ids)
    assert rendered is not None, "`digest` is no longer a two-digit hex rendering of each byte"
    alphabet = HEX_ALPHABET[rendered.group("case")]
    length = SHORT_DIGEST.search(ids)
    assert length is not None, "`short_digest` is no longer a prefix of `digest`"
    width = int(length.group("length"))

    mintable = [f"s-{digit * width}" for digit in alphabet]
    mintable.append(f"s-{(alphabet * width)[:width]}")
    for token in mintable:
        assert unpublished.SESSION_TOKEN.fullmatch(token), (
            f"onevcs {onevcs.ref} can mint {token}, which the view refuses as a token"
        )

    foreign = sorted(set(string.printable) - set(alphabet))
    unmintable = [
        f"s-{alphabet[0] * (width - 1)}",
        f"s-{alphabet[0] * (width + 1)}",
        f"S-{alphabet[0] * width}",
        f"{alphabet[0] * width}",
        f"s-{alphabet[0] * width}\n",
        *(f"s-{alphabet[0] * (width - 1)}{char}" for char in foreign),
        *(f"s-{char}{alphabet[0] * (width - 1)}" for char in foreign),
    ]
    for candidate in unmintable:
        assert not unpublished.SESSION_TOKEN.fullmatch(candidate), (
            f"the view accepts {candidate!r} as a token, which onevcs {onevcs.ref} cannot mint"
        )


# llmlint: ignore-end[tests_mirror_real_usage]


#: onevcs's published contract at the release the view spawns: the one source of the
#: retirement classes and the `retirement` object's fields, which the view restates.
ONEVCS_CONTRACT = Engine("onevcs", _pinned_tag("onevcs"), "docs")
RETIREMENT_CLASS = re.compile(r"pub enum RetirementClass \{(?P<body>[^}]*)\}")
RETIREMENT_STRUCT = re.compile(r"pub struct Retirement \{(?P<body>.*?)\}", re.S)
SUPERSEDED_BY_STRUCT = re.compile(r"pub struct SupersededBy \{(?P<body>.*?)\}", re.S)
RECOVERABLE_GAINS = re.compile(r"Recoverable\s+pub retirement: Option<Retirement>")
FIELD = re.compile(r"pub (\w+):")
ENUM_VARIANT = re.compile(r"[A-Z]\w*")


# llmlint: ignore-block[tests_mirror_real_usage] A drift gate for copied wire vocabulary
# reads the producer's pinned declaration; `test_superseded_branches_e2e.py` beside this
# drives the same words through the real CLI and the recipe.
def test_the_retirement_words_the_view_reads_are_onevcs_s_contract() -> None:
    """The class the view acts on and every field it reads, against onevcs's contract.

    Read at `config/onevcs.version`'s tag, the CLI the view spawns. A class onevcs renames,
    or a field it moves, fails here rather than leaving every superseded row printed with
    today's `land it:` line and no evidence.
    """
    contract = _source(ONEVCS_CONTRACT, "contract.md")
    classes = RETIREMENT_CLASS.search(contract)
    retirement = RETIREMENT_STRUCT.search(contract)
    superseded_by = SUPERSEDED_BY_STRUCT.search(contract)
    assert classes and retirement and superseded_by, (
        f"onevcs {ONEVCS_CONTRACT.ref}'s contract no longer declares `RetirementClass`, "
        "`Retirement` and `SupersededBy`"
    )
    wire = {_kebab(variant) for variant in ENUM_VARIANT.findall(classes.group("body"))}
    assert unpublished.SUPERSEDED_WITH_CHANGES in wire, (
        f"onevcs {ONEVCS_CONTRACT.ref} spells its retirement classes {sorted(wire)}"
    )
    assert wire == set(unpublished.RETIREMENT_CLASSES), (
        f"onevcs {ONEVCS_CONTRACT.ref} spells its retirement classes {sorted(wire)}"
    )
    fields = set(FIELD.findall(retirement.group("body")))
    assert set(unpublished.RETIREMENT_FIELDS_READ) <= fields, (
        f"onevcs {ONEVCS_CONTRACT.ref}'s `Retirement` declares {sorted(fields)}"
    )
    by = set(FIELD.findall(superseded_by.group("body")))
    assert set(unpublished.SUPERSEDED_BY_FIELDS_READ) <= by, (
        f"onevcs {ONEVCS_CONTRACT.ref}'s `SupersededBy` declares {sorted(by)}"
    )
    assert RECOVERABLE_GAINS.search(contract) and "retirement" in unpublished.ROW_FIELDS, (
        "the `recoverable --json` row field the view carries whole is no longer `retirement`"
    )


# llmlint: ignore-end[tests_mirror_real_usage]
