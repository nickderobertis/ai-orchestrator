"""The half of `tests/test_design_doc_template.py` that reads this repository's prose.

It sits in the `plan-guidance` project.

These hold the preserved design-document snapshots to their checksums. That project's key
names each file they read, and `tests/conftest.py` holds them to it; the tests of that
module that read no prose stay there, in `orchestrator:test`.
"""

from __future__ import annotations

import hashlib
import re

from test_design_doc_template import WHY_FIXTURE_PLANS, WHY_FIXTURES


def test_the_why_fixtures_are_the_preserved_snapshots_and_narrate_who_asked() -> None:
    """Each file is the preserved one, and each document's Why is the attributed kind."""
    listed = (WHY_FIXTURES / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    sums = dict(reversed(line.split("  ./", 1)) for line in listed if line.strip())
    on_disk = sorted(
        str(path.relative_to(WHY_FIXTURES))
        for path in WHY_FIXTURES.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    )
    assert on_disk == sorted(sums), on_disk
    for name, digest in sums.items():
        held = hashlib.sha256((WHY_FIXTURES / name).read_bytes()).hexdigest()
        assert held == digest, name
    for plan in WHY_FIXTURE_PLANS:
        document = (WHY_FIXTURES / plan / "documents" / f"{plan}-design.md").read_text("utf-8")
        why = document.split("\n## Why\n", 1)[1].split("\n## ", 1)[0]
        assert re.search(r"The user (reports|said)", why), why
        assert "“" in why, why
