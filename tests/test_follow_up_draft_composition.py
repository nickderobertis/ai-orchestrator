"""One tracked file composes the names a launch exports its follow-up drafting seam under.

`scripts/follow-up-env.sh` owns all three: the `drafts` source's root and plugin at the plan
store's environment layer, and `$ORCHESTRATOR_FOLLOW_UP_DRAFT`. A second composition is
invisible from outside — two launch paths exporting two roots both look like a configured
host, and a draft lands where the follow-up agent never looks — so the one place is a gate
rather than a convention, exactly as `tests/test_plan_root_composition.py` holds the
authoring root.

The two store settings are never spelled anywhere else in code, because nothing else has
reason to: `scripts/follow-up-draft.sh` reads the root's name out of the helper and hands it
to `orchestrator/follow_up_drafts.py`. The command's name is different, and so is what is
held of it: every briefing tells an agent to run `$ORCHESTRATOR_FOLLOW_UP_DRAFT`, so a
*mention* of it is the seam working. What is refused is composing it — assigning it, or
naming it as a quoted literal a program would set — anywhere but the helper.

Code only, not prose: a document may name any of these, and this tier is memoized on a key
that drops markdown, so a gate that could fail on a document would replay a green across
the edit that broke it.
"""

from __future__ import annotations

import re
import subprocess

import follow_up_variables

from orchestrator.root import REPO_ROOT

#: A plan-store source setting at the store's environment layer, matched rather than
#: spelled, so this file is not itself a second spelling of the names it looks for. POSIX
#: extended syntax, because `git grep -E` reads it.
STORE_SETTING = r"ONETASKGRAPH_SOURCES__[A-Z0-9_]+__(CONFIG__ROOT|PLUGIN)"

#: What is read: tracked code, and none of this repository's prose.
CODE = (":!*.md", ":!docs/")

#: The one file allowed to compose any of them.
HELPER = str(follow_up_variables.HELPER.relative_to(REPO_ROOT))


def _tracked_code_matching(pattern: str) -> list[str]:
    """Every tracked code file `git grep -E` finds ``pattern`` in."""
    listed = subprocess.run(  # noqa: S603 - git over this checkout's own tracked files
        ["git", "grep", "-l", "-E", pattern, "--", ".", *CODE],  # noqa: S607
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert listed.returncode in (0, 1), listed.stdout + listed.stderr
    return listed.stdout.split()


def composes_the_command(text: str, name: str) -> bool:
    """Whether ``text`` assigns ``name`` or names it as a quoted literal.

    A shell or prompt reference — `$NAME`, `${NAME}`, `"$NAME"` — is a mention and is how
    every briefing reaches the command; an assignment or a quoted bare name is how a
    program would set it.
    """
    return re.search(rf"[\"']{name}[\"']|(?<![$\w{{]){name}=", text) is not None


def test_the_store_settings_of_the_drafts_source_are_composed_in_exactly_one_place() -> None:
    """The root and the plugin are spelled by the helper and read from it everywhere else."""
    names = (follow_up_variables.root_name(), follow_up_variables.plugin_name())
    spelling = sorted(
        path
        for path in _tracked_code_matching(STORE_SETTING)
        if any(name in (REPO_ROOT / path).read_text(encoding="utf-8") for name in names)
    )
    assert spelling == [HELPER], (
        f"{', '.join(spelling)} spell {' or '.join(names)}, where only {HELPER} composes "
        "them; read them through tests/follow_up_variables.py or the helper instead"
    )


def test_the_drafting_command_is_composed_in_exactly_one_place() -> None:
    """Every file may tell an agent to run the command; only the helper may set it."""
    name = follow_up_variables.command_name()
    composing = sorted(
        path
        for path in _tracked_code_matching(name)
        if composes_the_command((REPO_ROOT / path).read_text(encoding="utf-8"), name)
    )
    assert composing == [HELPER], (
        f"{', '.join(composing)} compose {name}, where only {HELPER} does; export it by "
        "sourcing that helper instead"
    )


def test_the_command_gate_tells_a_composition_from_a_mention() -> None:
    """The detector above, proven both ways, so a green gate means no composition exists."""
    name = follow_up_variables.command_name()
    for mention in (f'"${name}" --title x', f"`${name}`", f"${{{name}}}"):
        assert not composes_the_command(mention, name), mention
    for composition in (f"export {name}=/x", f'environment["{name}"] = path', f"{name}=/x cmd"):
        assert composes_the_command(composition, name), composition
