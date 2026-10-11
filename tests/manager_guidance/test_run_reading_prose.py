"""The half of `tests/test_run_reading.py` that reads this repository's prose.

It sits in the `manager-guidance` project.

These hold the ending words the prose names to the engine's own. That project's key names
each file they read, and `tests/conftest.py` holds them to it; the tests of that module that
read no prose stay there, in `orchestrator:test`, and those reading a registered checkout in
`orchestrator:test-checkouts`.
"""

from __future__ import annotations

import re

import pytest
from test_run_reading import WORD_PASSAGES

from orchestrator import run_reading
from orchestrator.root import REPO_ROOT


@pytest.mark.parametrize(("document", "opens", "closes"), WORD_PASSAGES)
def test_the_prose_names_every_ending_word_and_only_the_engines_words(
    document: str, opens: str, closes: str
) -> None:
    """Each passage names every word a run that is not merely driven or not reads, and no other."""
    text = (REPO_ROOT / document).read_text(encoding="utf-8")
    start = text.index(opens)
    passage = " ".join(text[start : text.index(closes, start)].split())
    named = {
        word for word in re.findall(r"`([A-Z][A-Za-z ]*)`", passage) if word.split()[0].isupper()
    }

    assert named <= run_reading.WORDS, named - run_reading.WORDS
    assert named >= run_reading.WORDS - run_reading.LIVENESS, (
        f"{document} leaves out {sorted(run_reading.WORDS - run_reading.LIVENESS - named)}"
    )
