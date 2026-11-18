"""The spike scripts' attestation vocabulary, against the plan store's own schema.

`scripts/spike-records.sh` and `scripts/spike-evidence-check.sh` each validate a store
attestation check answer by closing its vocabulary: a `state` of `valid`, `void` or
`absent`, and a `void` record's `reason`. That closed list is a copy of the store's
`AttestationState` and `VoidReason`, so this reconciles each script's words with the
JSON Schema the locked store install prints from its own contract types
(`onetaskgraph schema`): every word a script compares a `state` or `reason` with is one the
store defines, and each script names every one the store defines, so a state or reason a
store release adds fails here rather than reading as a malformed answer at run time.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterator, Mapping
from functools import cache

import pytest

from orchestrator.root import REPO_ROOT

SCRIPTS = ("scripts/spike-records.sh", "scripts/spike-evidence-check.sh")
STORE = REPO_ROOT / ".venv" / "bin" / "onetaskgraph"

#: A jq comparison of a check answer's `state` or `reason`: `== "word"` or `| IN("a", "b")`.
COMPARISON = re.compile(r'\.(state|reason) (?:== "([a-z-]+)"|\| IN\(([^)]*)\))')


def _consts(definitions: Mapping[str, object], name: str) -> frozenset[str]:
    definition = definitions[name]
    assert isinstance(definition, dict), f"the store schema's {name} is not an object"
    return frozenset(str(variant["const"]) for variant in definition["oneOf"])


@cache
def store_vocabulary() -> dict[str, frozenset[str]]:
    """The `state` and `reason` words the installed store's schema defines."""
    printed = subprocess.run(
        [str(STORE), "schema"], check=True, capture_output=True, text=True
    ).stdout
    definitions = json.loads(printed)["roots"]["ProjectAttestationCheck"]["$defs"]
    return {
        "state": _consts(definitions, "AttestationState"),
        "reason": _consts(definitions, "VoidReason"),
    }


def script_vocabulary(path: str) -> dict[str, frozenset[str]]:
    """Every word one script compares a check answer's `state` or `reason` with."""
    words: dict[str, set[str]] = {"state": set(), "reason": set()}
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
    for field, single, listed in COMPARISON.findall(text):
        words[field].update([single] if single else re.findall(r'"([a-z-]+)"', listed))
    return {field: frozenset(found) for field, found in words.items()}


def _cases() -> Iterator[tuple[str, str]]:
    for path in SCRIPTS:
        for field in ("state", "reason"):
            yield path, field


@pytest.mark.parametrize(("path", "field"), list(_cases()))
def test_script_closes_exactly_the_store_vocabulary(path: str, field: str) -> None:
    store = store_vocabulary()[field]
    script = script_vocabulary(path)[field]
    assert not script - store, (
        f"{path} compares a check answer's {field} with {sorted(script - store)}, "
        f"which the store's schema does not define ({sorted(store)}); "
        "correct the word to the store's own"
    )
    assert not store - script, (
        f"{path} never names the store's {field} {sorted(store - script)}, so an answer "
        "carrying it reads as malformed; decide what the script does with it and name it"
    )
