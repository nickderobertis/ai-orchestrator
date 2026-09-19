"""`docs/host-setup.md` names exactly the outcomes `just repos-bootstrap` reports.

The recipe's per-checkout line opens with one word — what happened to that checkout —
and the host-setup document tells an operator what each word means. The document is
prose and the script is what runs, so nothing but this test keeps the two vocabularies
the same: a word the script starts reporting that the document does not explain, or one
the document explains that the script no longer reports, fails here rather than in an
operator's reading of a session-start report.
"""

from __future__ import annotations

import re

import pytest

from orchestrator.root import REPO_ROOT

# llmlint: ignore[shell_test_tiers_stay_split] This module runs no shell and drives no
# script: it reads `scripts/repos-bootstrap.sh` as text and reconciles its outcome words
# against `docs/host-setup.md` — the offline drift gate over this repository's prose, which
# is what `reads_docs` selects, and whose `wholeWorkspace` key covers the script. There is
# no shell cost here for a project of its own to select or skip, and `reads_recipes` is for
# a tier that reads nothing of the prose, which `tests/conftest.py` enforces.
pytestmark = pytest.mark.reads_docs

SCRIPT = REPO_ROOT / "scripts" / "repos-bootstrap.sh"
DOCUMENT = REPO_ROOT / "docs" / "host-setup.md"
#: The heading under which the document explains the recipe's report.
SECTION = "### The sibling gates: `just repos-bootstrap`"
#: How the script reports one checkout: `report <outcome> <detail> <path>`.
REPORTED = re.compile(r"^\s*report (\w+) ", re.MULTILINE)
#: How the document explains one outcome: a bullet opening with the word in backticks.
EXPLAINED = re.compile(r"^- `(\w+)` — ", re.MULTILINE)


def _section() -> str:
    text = DOCUMENT.read_text(encoding="utf-8")
    start = text.index(SECTION)
    after = text.find("\n## ", start)
    return text[start : after if after != -1 else None]


def test_the_document_explains_every_outcome_the_script_reports() -> None:
    reported = set(REPORTED.findall(SCRIPT.read_text(encoding="utf-8")))
    assert reported, f"{SCRIPT.name} reports no outcome through report()"
    explained = set(EXPLAINED.findall(_section()))
    assert explained == reported, (
        f"{DOCUMENT.name} explains {sorted(explained)} but {SCRIPT.name} reports "
        f"{sorted(reported)}; say what each outcome means under {SECTION!r}"
    )
