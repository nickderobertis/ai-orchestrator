"""The documents that list Claude identities, held to the helper and configs they describe.

`AGENTS.md`, `docs/host-setup.md` and `docs/onejudge-integration.md` each restate which
identities a chain names and where each Claude identity's directory comes from. Those
lists have one source apiece — the variables and defaults in
`scripts/claude-alt-config-dir.sh`, the chains in the `oneharness*.toml` configs — so both
are read from there rather than restated here, and a variable or identity added to the
source without the prose fails this test.
"""

from __future__ import annotations

import re
import subprocess
import tomllib

import pytest

from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_docs

HELPER = REPO_ROOT / "scripts" / "claude-alt-config-dir.sh"
DOCUMENTS = ("AGENTS.md", "docs/host-setup.md", "docs/onejudge-integration.md")
IDENTITIES_FILE = "claude-identities.env"

NUMBER_WORDS = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")
#: A count of every identity a chain holds, however the sentence is wrapped. Only the two
#: phrasings the documents use for the whole chain, since "the other three identities" or
#: "the other provider's two identities" count a part of it and are true as written.
_WORDS = "|".join(NUMBER_WORDS)
IDENTITY_COUNT = re.compile(rf"\b(?:same ({_WORDS}) identities|({_WORDS}) harness identities)\b")


def _helper_defaults() -> dict[str, str]:
    """Each indirection the real helper walks, mapped to its `$HOME`-relative default."""
    script = (
        'source "$1"\n'
        'for name in "${CLAUDE_IDENTITY_CONFIG_VARIABLES[@]}"; do\n'
        '    leaf=$(_claude_identity_default "$name" | head -n 1)\n'
        '    printf "%s %s\\n" "$name" "$leaf"\n'
        "done\n"
    )
    listed = subprocess.run(
        ["bash", "-c", script, "claude-identity-docs", str(HELPER)],
        text=True,
        capture_output=True,
        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent-home"},
        check=True,
    )
    return dict(line.split(" ", 1) for line in listed.stdout.splitlines())


def _chain_identities() -> set[str]:
    """Every identity any shipped config's chain names."""
    identities: set[str] = set()
    for config in REPO_ROOT.glob("oneharness*.toml"):
        identities.update(tomllib.loads(config.read_text(encoding="utf-8"))["harnesses"])
    return identities


def _flat(relative_path: str) -> str:
    return " ".join((REPO_ROOT / relative_path).read_text(encoding="utf-8").split())


@pytest.mark.parametrize("document", DOCUMENTS)
def test_every_document_names_each_indirection_its_default_and_the_identities_file(
    document: str,
) -> None:
    defaults = _helper_defaults()
    assert len(defaults) == 4, defaults
    prose = _flat(document)

    missing = [
        claim
        for name, leaf in defaults.items()
        for claim in (name, f"$HOME/{leaf}")
        if claim not in prose
    ]
    if IDENTITIES_FILE not in prose:
        missing.append(IDENTITIES_FILE)
    assert not missing, f"{document} no longer names {missing}"


@pytest.mark.parametrize("document", DOCUMENTS)
def test_every_document_counts_and_names_the_identities_the_chains_hold(document: str) -> None:
    identities = _chain_identities()
    word = NUMBER_WORDS[len(identities) - 1]
    prose = _flat(document)

    counts = {same or harness for same, harness in IDENTITY_COUNT.findall(prose)}
    assert counts == {word}, (
        f"{document} counts {sorted(counts)} identities where the chains hold {word}"
    )
    assert "claude-code:primary-backup" in prose, f"{document} never names the primary-backup"
