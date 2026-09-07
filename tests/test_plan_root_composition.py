"""One tracked file composes the name a planning launch exports its plan-authoring root under.

`scripts/plan-root-env.sh` owns that name and the value beside it, and everything else
reads it from there. A second composition is invisible from outside — two launch paths
exporting two spellings both look like a configured host, and the plan a dispatch authors
lands where nobody looks — so the one place is a gate rather than a convention.

It sits here rather than beside the journeys that drive the helper, and the key is why:
this asks a question about the whole tracked tree, so it belongs to the tier keyed on the
whole workspace minus its prose. `tests/plan_tooling/test_plan_root_env.py` holds
everything the helper itself does, in the project that owns this repository's host-tool
plan journeys.
"""

from __future__ import annotations

import re
import subprocess

import plan_root_variable

from orchestrator.root import REPO_ROOT

#: The shape of a plan-store source-root variable at the store's environment layer,
#: matched rather than spelled: this is looking for a *second* composition of this
#: repository's own, and a second one would be spelled differently by definition.
ROOT_VARIABLE = re.compile(r"ONETASKGRAPH_SOURCES__[A-Z0-9_]+?__CONFIG__ROOT")

#: What this reads. Deliberately not this repository's prose: a document may name the
#: variable, and this tier is memoized on a key that drops markdown, so a gate that could
#: fail on a document would replay a green across the edit that broke it. What the
#: contract is about is which *code* composes the name.
CODE = (":!*.md", ":!docs/")


def test_the_launch_composes_that_variable_in_exactly_one_place() -> None:
    """One tracked file holds the name and the value; every other reader reads it from there.

    A second composition is invisible from outside: two launch paths exporting two
    spellings both look like a configured host, and the plan a dispatch authors lands
    somewhere nobody looks. Tests are read too — `tests/plan_root_variable.py` exists so
    that a journey measuring this seam is a reader rather than a stale copy of it.
    """
    listed = subprocess.run(  # noqa: S603 - git over this checkout's own tracked files
        ["git", "grep", "-l", "-E", ROOT_VARIABLE.pattern, "--", ".", *CODE],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert listed.returncode == 0, listed.stdout + listed.stderr

    name = plan_root_variable.name()
    composing = sorted(
        path
        for path in listed.stdout.split()
        if name in (REPO_ROOT / path).read_text(encoding="utf-8")
    )
    expected = [str(plan_root_variable.HELPER.relative_to(REPO_ROOT))]
    assert composing == expected, (
        f"{', '.join(composing)} spell {name}, where only {expected[0]} composes it; read "
        "it through tests/plan_root_variable.py instead"
    )
