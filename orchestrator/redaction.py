"""Strip credential *values* out of anything this harness preserves.

Harness credentials are referenced by name in the environment, never committed,
and the tools they configure do not deliberately print them.  Preserved evidence
changes the calculus anyway: a gate log, a push transcript, or a bounded output
tail outlives the terminal it was printed to, so one accidental ``env`` dump or
verbose transport trace would durably record a live token.

This module keeps that impossible by construction, from the only place the value
is known: the environment the harness itself was handed.  Every secret-looking
variable's value is replaced with ``<redacted:NAME>`` wherever it appears.  It is
deliberately stdlib-only and import-light so ``scripts/redact-secrets.py`` can
stream shell output through it with the system interpreter, before any virtualenv
exists.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping

__all__ = ["SECRET_NAME", "SECRET_NAME_PATTERN", "redact", "secret_values"]

#: Names whose value is treated as a credential. Matched against the whole
#: variable name so ``KEYCHAIN`` or ``TOKENIZER`` cannot be mistaken for one.
#:
#: The one source of this grammar. ``scripts/preserved-log.sh`` carries a
#: byte-identical copy because it must run before any virtualenv exists;
#: ``tests/test_redaction.py`` fails if the two strings ever differ.
SECRET_NAME_PATTERN = (
    r"(^|_)(TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|API_?KEY|ACCESS_?KEY|"
    r"PRIVATE_?KEY|SESSION_?KEY|AUTH)S?$"
)
SECRET_NAME = re.compile(SECRET_NAME_PATTERN)

#: Shorter values collide with ordinary words and would corrupt the very evidence
#: this preserves. A credential that short is not one worth protecting.
_MIN_SECRET_LENGTH = 8


def secret_values(environ: Mapping[str, str] | None = None) -> list[tuple[str, str]]:
    """Return ``(name, value)`` for every credential-shaped variable worth hiding.

    Longest value first, so a token that contains another token's value as a
    prefix is replaced before the shorter one can split it.
    """
    source = os.environ if environ is None else environ
    found = [
        (name, value)
        for name, value in source.items()
        if SECRET_NAME.search(name.upper())
        and len(value) >= _MIN_SECRET_LENGTH
        and not any(character in value for character in "\r\n")
    ]
    return sorted(found, key=lambda item: (-len(item[1]), item[0]))


def redact(text: str, secrets: Iterable[tuple[str, str]] | None = None) -> str:
    """Replace every known credential value in ``text`` with its name."""
    for name, value in secret_values() if secrets is None else secrets:
        text = text.replace(value, f"<redacted:{name}>")
    return text
