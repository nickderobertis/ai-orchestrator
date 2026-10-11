"""The half of `tests/test_design_approval.py` that reads this repository's prose.

It sits in the `plan-guidance` project.

These hold every shipped example's approval to the chain it was recorded under. That
project's key names each file they read, and `tests/conftest.py` holds them to it; the tests
of that module that read no prose stay there, in `orchestrator:test`, and those reading a
registered checkout in `orchestrator:test-checkouts`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from test_design_approval import EXAMPLES, _stand_ins

from orchestrator import design_approval, design_chain, plan_store
from orchestrator.root import REPO_ROOT


def test_every_shipped_example_carries_a_persons_approval_current_or_left_stale_by_the_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every example carries an approval a person recorded, and only the chain may stale it.

    Approving a design document is a person's act, so no change to this repository records
    one for an example: a change to `templates/design-doc.md.j2` leaves every example's
    approval standing over the chain it was granted under, and the gate refuses each one as
    rendered from a template no longer in force until a person reads the regenerated
    document and runs `just approve-design` again. So an example passes here when its
    approval is current, or when it carries an approval record and the only thing refused is
    the moved chain. An example carrying no approval at all, or refused for anything else —
    no document, an edited body — fails, because nothing but a person could repair that.
    """
    projects = plan_store.local_projects(EXAMPLES)
    assert projects, f"the {EXAMPLES!r} source ships no project, so this proves nothing"
    _stand_ins(tmp_path, monkeypatch, projects)
    unapproved: list[str] = []
    for project in projects:
        assessed = design_approval.assess(project)
        if assessed.refusal is None:
            continue
        document = design_approval.design_document(project)
        stale = (
            design_approval.recorded(document) is not None
            and "that template now resolves to" in assessed.refusal
        )
        if not stale:
            unapproved.append(f"{project}: {assessed.refusal}")
    assert not unapproved, (
        "shipped example projects carry no approval a person recorded, or are refused for "
        "something other than the moved template chain:\n"
        + "\n".join(f"  - {reason}" for reason in unapproved)
    )


def test_regenerating_a_shipped_example_keeps_its_body_and_approves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each example is a rendering with stored answers, and regenerating it approves nothing.

    Driven through the pinned engine's real resolve piped into the pinned plan store's real
    `document render`, over a copy of the `examples` source so the tracked one is not
    written. The body the stored answers render is the body the document holds, so a
    regenerate moves only the chain it records. Whether the approval then holds is the
    person's record alone: one granted under the chain in force still holds, and one a moved
    chain left stale stays refused, now as a document nobody approved, until a person runs
    `just approve-design` — a regenerate never stands in for that.
    """
    copied = tmp_path / EXAMPLES
    shutil.copytree(REPO_ROOT / EXAMPLES, copied)
    monkeypatch.setenv("ONETASKGRAPH_SOURCES__EXAMPLES__CONFIG__ROOT", str(copied))
    project = f"{EXAMPLES}:health-endpoint"
    document = f"{project}-design"
    _stand_ins(tmp_path, monkeypatch, [project])
    repository = design_chain.plan_repository(project)
    assert repository is not None, "the example this regenerates names its one repository"
    before = design_approval.design_document(project)
    recorded = design_approval.recorded(before)
    assert recorded is not None, "the example carries no approval a person recorded"
    current = design_approval.assess(project).refusal is None

    resolve = subprocess.run(
        [str(REPO_ROOT / ".venv" / "bin" / "onepipeline"), "template", "resolve"]
        + [design_approval.TEMPLATE_NAME, *design_chain.resolve_arguments(repository), "--json"]
        + ["--template-root", str(design_approval.TEMPLATE_ROOT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    render = subprocess.run(
        [str(plan_store.locked_binary()), "document", "render", document]
        + ["--template-loader", "-", "--no-interactive", "--json"],
        cwd=REPO_ROOT,
        input=resolve.stdout,
        capture_output=True,
        text=True,
        check=False,
    )
    assert render.returncode == 0, render.stderr
    answered = json.loads(render.stdout)
    assert answered["changed"] is not current, answered
    assert answered["digest"] == json.loads(resolve.stdout)["digest"]
    after = design_approval.design_document(project)
    assert after.content == before.content, "the stored answers render another body"
    assert design_approval.recorded(after) == recorded, "a regenerate rewrote the approval"
    refusal = design_approval.assess(project).refusal
    if current:
        assert refusal is None, refusal
    else:
        assert refusal is not None and "carries no approval for what it currently says" in (
            refusal
        ), refusal
