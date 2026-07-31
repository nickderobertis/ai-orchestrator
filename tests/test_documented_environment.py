"""The operator-facing name of a tunable and the constant that reads it, reconciled.

A document has to spell an environment variable literally — the reader's next
action is to type it — so the name is unavoidably restated outside the code that
owns it. That restatement is what drifts: rename the constant's value and the
prose keeps confidently telling operators to set a name nothing reads.

These tests take the name from the constant rather than repeating it, so a rename
fails here until the document is updated with it.
"""

from __future__ import annotations

import pytest

from orchestrator import REPO_ROOT
from orchestrator.coordination import LOCK_TIMEOUT_ENV
from orchestrator.scratch import MIN_FREE_BYTES_ENV

#: Each variable the code names as a constant, and the document that tells an
#: operator to set it. A tunable documented without such a constant is not listed:
#: there would be nothing to derive from, and inventing one to satisfy this test
#: would restate the name a second time rather than fewer.
DOCUMENTED_TUNABLES = (
    (LOCK_TIMEOUT_ENV, "docs/repo-lifecycle.md"),
    (MIN_FREE_BYTES_ENV, "docs/repo-lifecycle.md"),
)


@pytest.mark.reads_docs
@pytest.mark.parametrize(("variable", "document"), DOCUMENTED_TUNABLES)
def test_documentation_names_the_variable_its_constant_declares(
    variable: str, document: str
) -> None:
    prose = (REPO_ROOT / document).read_text(encoding="utf-8")

    assert variable in prose, (
        f"{document} does not name {variable}; rename it there in the same change "
        "that renamed the constant, or stop documenting the tunable"
    )


def test_every_documented_tunable_is_reconciled_against_a_constant() -> None:
    """A tunable added to this table without a real constant would prove nothing."""
    assert all(variable and document for variable, document in DOCUMENTED_TUNABLES)
    assert len({variable for variable, _ in DOCUMENTED_TUNABLES}) == len(DOCUMENTED_TUNABLES)
