"""The half of `tests/test_project_store.py` that reads this repository's prose.

It sits in the `manager-guidance` project.

These hold the documented reference shape to the one the renderer reads. That project's key
names each file they read, and `tests/conftest.py` holds them to it; the tests of that
module that read no prose stay there, in `orchestrator:test`.
"""

from __future__ import annotations

import re

from orchestrator import project_store
from orchestrator.root import REPO_ROOT


def test_the_documented_reference_shape_is_the_one_the_renderer_reads() -> None:
    """`docs/orchestration.md`'s node-shapes section and the renderer state one shape.

    The section is the shape's one source and the renderer is the one place this
    repository parses it, so the spelling is read back out of the prose rather than
    restated here, and the reader is shown accepting exactly the form the prose states —
    both placeholders filled — and refusing the same form with either half emptied.
    """
    text = (REPO_ROOT / "docs" / "orchestration.md").read_text(encoding="utf-8")
    start = text.index("\n## Node shapes\n")
    section = text[start : text.index("\n## ", start + 1)]
    documented = re.search(
        r"cross-DAG references of the form\s+`([^`]+)`", " ".join(section.split())
    )
    assert documented is not None, "the node-shapes section no longer states the reference shape"
    assert documented.group(1) == project_store.CROSS_DAG_REFERENCE_SHAPE

    placeholders = re.findall(r"<([^<>]+)>", project_store.CROSS_DAG_REFERENCE_SHAPE)
    assert placeholders == ["run_id", "node_id"], placeholders
    filled = project_store.CROSS_DAG_REFERENCE_SHAPE
    for placeholder, value in zip(placeholders, ("r-upstream-0001", "adopt"), strict=True):
        filled = filled.replace(f"<{placeholder}>", value)
    assert project_store.cross_dag_reference(filled) == filled
    for placeholder in placeholders:
        emptied = project_store.CROSS_DAG_REFERENCE_SHAPE.replace(f"<{placeholder}>", "")
        for other in placeholders:
            emptied = emptied.replace(f"<{other}>", "x")
        assert project_store.cross_dag_reference(emptied) is None, emptied
