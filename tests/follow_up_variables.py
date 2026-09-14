"""The names a launch exports its follow-up drafting seam under, read from their source.

`scripts/follow-up-env.sh` composes all three — the `drafts` source's root and plugin at
the plan store's environment layer, and the command a party drafts with — and is the only
place any of them is composed. So no test spells them: this reads each out of the helper by
sourcing it and printing the constant it holds, which is the read
`scripts/follow-up-draft.sh` makes for the root's name, one step short of the resolution.
"""

from __future__ import annotations

import functools
import subprocess

from orchestrator.root import REPO_ROOT

HELPER = REPO_ROOT / "scripts" / "follow-up-env.sh"

#: The helper's own constants, by the names it holds them under.
ROOT_HOLDER = "FOLLOW_UP_DRAFTS_ROOT_ENV"
PLUGIN_HOLDER = "FOLLOW_UP_DRAFTS_PLUGIN_ENV"
COMMAND_HOLDER = "FOLLOW_UP_DRAFT_ENV"


@functools.cache
def _held(holder: str) -> str:
    read = subprocess.run(  # noqa: S603 - this repository's own helper, read as its callers do
        ["bash", "-c", f'source "{HELPER}"; printf "%s" "${holder}"'],  # noqa: S607
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if read.returncode != 0 or not read.stdout.strip():
        raise AssertionError(
            f"{HELPER} did not answer with the {holder} it composes; it is the one source of "
            f"that name, so nothing here can stand in for it:\n{read.stdout}{read.stderr}"
        )
    return read.stdout.strip()


def root_name() -> str:
    """The variable the `drafts` source's absolute root is exported under."""
    return _held(ROOT_HOLDER)


def plugin_name() -> str:
    """The variable the `drafts` source's plugin is exported under."""
    return _held(PLUGIN_HOLDER)


def command_name() -> str:
    """The variable the drafting command's path is exported under."""
    return _held(COMMAND_HOLDER)


def all_names() -> tuple[str, str, str]:
    """All three, for a journey that clears what an enclosing launch exported."""
    return root_name(), plugin_name(), command_name()
