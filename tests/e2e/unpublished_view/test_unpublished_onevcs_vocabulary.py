"""The `onevcs` vocabularies `orchestrator/unpublished.py` restates, against onevcs's source.

The view reads two things `onevcs` declares and it cannot import: the lifecycle a
session record's `state` is spelled in (:data:`~orchestrator.unpublished.HolderState`),
and the grammar a session token is minted in (:data:`~orchestrator.unpublished.
SESSION_TOKEN`). Each is a copy of another repository's declaration, so each is read back
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
