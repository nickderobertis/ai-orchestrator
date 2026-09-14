"""`just recoverable --repo` rests on a flag the installed `onevcs recoverable` offers.

`scripts/recoverable.sh` forwards its arguments to `onevcs recoverable` untouched, so the
recipe accepts `--repo` only because the verb does — and `AGENTS.md` teaches `--repo` as
how the recipe is scoped to one identity from any directory. The verb once took no
`--repo` and was scoped by working directory alone, so the only way to one identity's
answer was a `cd`. An installed release without the flag would refuse that documented
scoping on every use while the recipe's `--repo` journeys, which double the published
CLI, stayed green. This reads the installed binary itself.

llmlint: ignore-file[test_tiers_split_by_project_not_by_marker,shell_test_tiers_stay_split] The
`reads_checkouts` marker is this repository's tier mechanism rather than a way around one:
it moves this gate out of every memoized tier into the uncached
`orchestrator:test-checkouts`, because its subject — the `onevcs` installed under
`.venv`, which no `nx.json` input hashes — is outside this workspace. A project of its own
would give the gate a key over none of what it reads, and a memoized green would replay
across the very release change it exists to catch; every `reads_checkouts` gate over an
installed CLI here (`tests/test_adoption_guard.py`, `tests/test_watch_surface_drift.py`)
is tiered the same way. It runs no shell: it reads `--help` from one binary.
"""

from __future__ import annotations

import os

import onevcs_state_snapshot
import pytest
from published_surface import surface_of

RECOVERABLE = ("recoverable",)
REPO_FLAG = "--repo"


@pytest.mark.reads_checkouts
def test_the_installed_onevcs_recoverable_offers_repo() -> None:
    """`onevcs recoverable --help` declares `--repo`, read under this process's state root.

    `--help` should touch no registry, but the suite's rule is that no `onevcs` contact
    reaches the host's, so the per-process copy `tests/conftest.py` exports is confirmed
    to be the root the help run inherits rather than assumed.
    """
    state_root = os.environ.get(onevcs_state_snapshot.ONEVCS_HOME)
    assert state_root, "the suite exported no per-process ONEVCS_HOME for onevcs to read"
    assert state_root != str(onevcs_state_snapshot.host_root()), (
        f"ONEVCS_HOME is this host's own state root ({state_root}), not the suite's copy"
    )

    surface = surface_of("onevcs")

    assert RECOVERABLE in surface.paths, (
        "the installed onevcs has no `recoverable` verb, which `just recoverable` forwards to"
    )
    assert surface.accepts(RECOVERABLE, REPO_FLAG), (
        f"the installed `onevcs recoverable` no longer offers {REPO_FLAG}, so "
        f"`just recoverable {REPO_FLAG} <checkout>` — the scoping AGENTS.md documents — is "
        f"refused; it accepts {sorted(surface.flags[RECOVERABLE])}"
    )
