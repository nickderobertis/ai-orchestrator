"""AGENTS.md's record of the composed create-repo references matches what `llmlint.yml` loads.

The create-repo baseline's buildout tier reads which reference fragments apply from the
"References composed" bullet of AGENTS.md's Stack and composition section, while llmlint
loads the same references' fragments from `llmlint.yml`'s plugin list. Two statements of one
composition, so this holds them together: each reference the bullet names is a fragment the
plugin list loads, each reference fragment loaded is named there, and the reference the
section records as excluded is loaded by neither.
"""

from __future__ import annotations

import re

import pytest
import yaml

from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_docs

#: Where a create-repo reference's fragment is published, and the path after it that names
#: the reference: `assets/llmlint/shapes/skills-repo.llmlint.yml@1` is `shapes/skills-repo.md`.
REFERENCE_FRAGMENT = re.compile(r"/skills/bootstrap/create-repo/assets/llmlint/(.+)\.llmlint\.yml@")
#: Fragments published beside the references that compose no reference of their own: a
#: tool's rules, loaded because this repository uses that tool.
TOOL_FRAGMENTS = "tools/"
COMPOSED = re.compile(r"^- \*\*References composed:\*\*(.+?)(?=^- |\Z)", re.MULTILINE | re.DOTALL)
EXCLUDED = re.compile(r"^- \*\*Excluded:\*\*(.+?)(?=^- |^<!--|\Z)", re.MULTILINE | re.DOTALL)
REFERENCE = re.compile(r"`([\w./-]+\.md)`")


def _section() -> str:
    text = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    _, heading, rest = text.partition("## Stack and composition")
    assert heading, "AGENTS.md has no Stack and composition section"
    return rest.split("\n## ", 1)[0]


def _loaded() -> set[str]:
    """The create-repo references whose fragments `llmlint.yml` loads, as reference paths."""
    plugins = yaml.safe_load((REPO_ROOT / "llmlint.yml").read_text(encoding="utf-8"))["plugins"]
    found = {match.group(1) for plugin in plugins if (match := REFERENCE_FRAGMENT.search(plugin))}
    return {f"{path}.md" for path in found if not path.startswith(TOOL_FRAGMENTS)}


def test_the_composed_references_are_the_fragments_llmlint_loads() -> None:
    composed = COMPOSED.search(_section())
    assert composed is not None, "the section states no References composed bullet"
    named = set(REFERENCE.findall(composed.group(1)))

    assert named == _loaded(), (
        f"AGENTS.md records {sorted(named)} as composed while llmlint.yml loads "
        f"{sorted(_loaded())}; change both, or neither"
    )


def test_an_excluded_reference_is_loaded_by_neither() -> None:
    excluded = EXCLUDED.search(_section())
    assert excluded is not None, "the section records no excluded reference"
    named = set(REFERENCE.findall(excluded.group(1)))

    assert named and not named & _loaded(), (named, sorted(_loaded()))
