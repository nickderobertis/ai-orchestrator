"""The suite reads a copy of this host's `onevcs` state root, and why it has to.

`tests/onevcs_state_snapshot.py` records the measurement: the adopted `onevcs` rewrites a
version-5 registry to version 6 on first contact — a pure read included — and the
release every dispatch of an older engine links cannot read what it writes. The first
journey here takes that measurement again against the installed CLI, on a registry of
the older shape planted under a scratch root, so the day a release stops migrating on
read this module's reason fails rather than going on describing a hazard nobody has.
The second holds the isolation itself: what this process exports as `ONEVCS_HOME` is a
copy, it carries the host's identities, and it carries none of the host's sessions.

Nothing here reads the host's root through `onevcs`. The host's registry is read as a
file, once, to compare identities — that is the one read of it this suite makes.
"""

# Every read this module makes of a `registry.json` is a read of the thing it is about:
# the schema a state root holds on disk, which is what one release writes and another
# refuses, and no view renders. The first journey asks the installed CLI the way a user
# does and then reads the document that CLI rewrote, because the interface that would
# observe the rewrite — a release below 0.21.0 refusing it — is not installed here. The
# second holds the suite's own isolation, whose subject has no CLI at all: the one entry
# point an operator has for the host's root is the `onevcs` read the first journey shows
# rewriting it, so asking the host that way would make the exact write this module
# proves the suite never makes. `host_root()` is `tests/onevcs_state_snapshot.py`'s one
# public statement of which root the copy was taken from.
# llmlint: ignore-file[tests_mirror_real_usage] see above

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import onevcs_state_snapshot
from waits import timeout as e2e_timeout

#: A registry as onevcs 0.19.x wrote one: version 5, each identity carrying the two
#: fields `register` inferred from whether the origin had a host. The checkout path
#: need not exist — a listing does not stat it — and the identity is one no real host
#: registers, so the document is legible on its own.
OLDER_SCHEMA = {
    "version": 5,
    "identities": {
        "github.com/scratchowner/older-schema": {
            "origin": "github.com/scratchowner/older-schema",
            "workflow": "remote",
            "repo_type": "team",
            "gate": "<no-op>",
        }
    },
    "checkouts": {
        "older-schema": {
            "path": "/nonexistent/older-schema",
            "identity": "github.com/scratchowner/older-schema",
        }
    },
}

#: The two inferred fields the migration drops; their absence is what an older
#: release refuses a migrated registry for (`missing field `workflow``).
INFERRED_FIELDS = ("workflow", "repo_type")


def _registry(root: Path) -> dict[str, object]:
    document = json.loads((root / "registry.json").read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _identities(root: Path) -> set[str]:
    """The identity keys a registry under ``root`` holds."""
    identities = _registry(root)["identities"]
    assert isinstance(identities, dict)
    return set(identities)


def test_the_installed_onevcs_rewrites_an_older_registry_on_a_read(tmp_path: Path) -> None:
    """A listing — the least a verb can do — leaves the registry at the newer schema.

    This is the whole reason the suite reads a copy: a check that merely *asked* the host
    which identities it holds would have moved the host's registry to a schema every
    process still on the older release cannot read.
    """
    root = tmp_path / "older"
    root.mkdir()
    (root / "registry.json").write_text(json.dumps(OLDER_SCHEMA, indent=2), encoding="utf-8")
    (root / "rules.yml").write_text(
        "version: 3\ntrailer_prefix: Orchestrator-\nrules: []\n"
        "default:\n  publication: change-auto\n  approvals: none\n",
        encoding="utf-8",
    )

    listed = subprocess.run(
        ["onevcs", "repos"],
        env={**os.environ, onevcs_state_snapshot.ONEVCS_HOME: str(root)},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )

    assert listed.returncode == 0, listed.stderr
    assert "github.com/scratchowner/older-schema" in listed.stdout
    rewritten = _registry(root)
    assert rewritten["version"] != OLDER_SCHEMA["version"], (
        "the installed onevcs read a version-5 registry and left it at version 5, so the "
        "migration `tests/onevcs_state_snapshot.py` isolates the host from no longer "
        "happens on a read; re-read whether the suite still needs to read a copy"
    )
    identities = rewritten["identities"]
    assert isinstance(identities, dict)
    record = identities["github.com/scratchowner/older-schema"]
    assert isinstance(record, dict)
    assert not any(field in record for field in INFERRED_FIELDS), (
        f"the migrated record still carries {INFERRED_FIELDS}: {record}; the older release "
        "refuses a registry for their *absence*, so the hazard has changed shape"
    )


def test_every_process_of_this_suite_reads_a_copy_of_the_hosts_root() -> None:
    """What `ONEVCS_HOME` names here is a copy: same identities, no sessions, not the host."""
    exported = os.environ.get(onevcs_state_snapshot.ONEVCS_HOME)
    assert exported, "the suite exports no ONEVCS_HOME, so every read below reaches the host"
    host = onevcs_state_snapshot.host_root()
    copy = Path(exported)

    assert copy.resolve() != host.resolve(), (
        f"ONEVCS_HOME names the host's own root {host}, which the installed onevcs would "
        "rewrite on the first read any test makes of it"
    )
    if not (host / "registry.json").is_file():
        # A host with no registry has nothing to be isolated from and nothing to compare;
        # the export above is still the copy, of nothing.
        assert not (copy / "registry.json").exists()
        return
    assert _identities(copy) == _identities(host), (
        "the copy holds different identities from the host, so a check asking it which "
        "repositories are registered here is asking about some other host"
    )
    assert not (copy / "sessions").exists(), (
        "the copy carries the host's session records, which are live claims about "
        "dispatches somebody else is driving"
    )
