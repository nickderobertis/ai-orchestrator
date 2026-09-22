"""The suite reads a copy of this host's `onevcs` state root, and why it has to.

`tests/onevcs_state_snapshot.py` records the measurement: the adopted `onevcs` rewrites a
version-5 registry to version 6 on first contact — a pure read included — and the
release every dispatch of an older engine links cannot read what it writes. The first
journey here takes that measurement again against the installed CLI, on a registry of
the older shape planted under a scratch root, so the day a release stops migrating on
read this module's reason fails rather than going on describing a hazard nobody has.
The second holds the isolation itself: what this process exports as `ONEVCS_HOME` is a
copy, it carries the host's identities and its pool configuration, and it carries
neither the host's sessions nor its live pool slots. The third asks the installed CLI
for a capacity the way an operator does, against a copy taken from a planted root: the
workspaces file is what `onevcs pool status` resolves an identity's pool out of, so a
copy without it answers `pool: 0` — pooling off — for a source that pools, and every
check reading pool state under the snapshot would be reading some other host.

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
import pytest
from waits import timeout as e2e_timeout

#: The owner every identity planted here is filed under: one no real host registers, so
#: a document naming it is legible on its own and reaches nothing of this host's.
SCRATCH_OWNER = "scratchowner"


def older_schema(name: str) -> dict[str, object]:
    """A registry naming one identity, as onevcs 0.19.x wrote one.

    Version 5, the identity carrying the two fields `register` inferred from whether the
    origin had a host. The checkout path need not exist — neither a listing nor a
    capacity read stats it — so the document is the whole of what a journey has to plant
    to have an identity the installed CLI will answer about.
    """
    identity = f"github.com/{SCRATCH_OWNER}/{name}"
    return {
        "version": 5,
        "identities": {
            identity: {
                "origin": identity,
                "workflow": "remote",
                "repo_type": "team",
                "gate": "<no-op>",
            }
        },
        "checkouts": {name: {"path": f"/nonexistent/{name}", "identity": identity}},
    }


#: The registry the migration journey plants, and the shape every other one here uses.
OLDER_SCHEMA = older_schema("older-schema")

#: A policy file, because a verb loads one before it answers anything.
RULES = (
    "version: 3\ntrailer_prefix: Orchestrator-\nrules: []\n"
    "default:\n  publication: change-auto\n  approvals: none\n"
)

#: The two inferred fields the migration drops; their absence is what an older
#: release refuses a migrated registry for (`missing field `workflow``).
INFERRED_FIELDS = ("workflow", "repo_type")

#: The identity the capacity journey plants, and the capacity its workspaces file gives
#: it — by a *rule*, over a `default` that pools nothing, so the numbers a read answers
#: with can only have come from the copied file. Neither is what a root carrying no
#: workspaces file answers (`pool: 0`, `overflow: unlimited`), which is what the copy
#: reported before it carried one.
POOLED = "pooled"
POOLED_IDENTITY = f"github.com/{SCRATCH_OWNER}/{POOLED}"
POOL = 2
OVERFLOW = 3
POOLING = (
    "version: 1\n"
    "default: {pool: 0, overflow: unlimited}\n"
    "rules:\n"
    f"- match: {{host: github.com, owner: {SCRATCH_OWNER}, name: {POOLED}}}\n"
    f"  pool: {POOL}\n"
    f"  overflow: {OVERFLOW}\n"
)
#: What pooling off looks like, which is every capacity this suite read before the
#: snapshot carried a workspaces file.
POOLING_OFF = {"pool": 0, "overflow": "unlimited"}


def _registry(root: Path) -> dict[str, object]:
    document = json.loads((root / "registry.json").read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _identities(root: Path) -> set[str]:
    """The identity keys a registry under ``root`` holds."""
    identities = _registry(root)["identities"]
    assert isinstance(identities, dict)
    return set(identities)


def _plant(root: Path, name: str) -> Path:
    """A state root holding one identity and a policy, and nothing else."""
    root.mkdir(parents=True)
    (root / "registry.json").write_text(json.dumps(older_schema(name), indent=2), encoding="utf-8")
    (root / "rules.yml").write_text(RULES, encoding="utf-8")
    return root


def _capacity(root: Path, identity: str) -> dict[str, object]:
    """What the installed `onevcs` reports an identity's pool capacity to be under ``root``."""
    read = subprocess.run(
        ["onevcs", "pool", "status", identity, "--json"],
        env={**os.environ, onevcs_state_snapshot.ONEVCS_HOME: str(root)},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert read.returncode == 0, read.stderr
    reported = json.loads(read.stdout)["capacity"]
    assert isinstance(reported, dict)
    return reported


def test_the_installed_onevcs_rewrites_an_older_registry_on_a_read(tmp_path: Path) -> None:
    """A listing — the least a verb can do — leaves the registry at the newer schema.

    This is the whole reason the suite reads a copy: a check that merely *asked* the host
    which identities it holds would have moved the host's registry to a schema every
    process still on the older release cannot read.
    """
    root = _plant(tmp_path / "older", "older-schema")

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
    # The release override is part of what a read answers from: with it missing from the
    # copy, every `release targets` read here would report the global rung and no
    # default target for a host `just repos-apply` had configured with both.
    if (host / "releases.yml").is_file():
        assert (copy / "releases.yml").read_bytes() == (host / "releases.yml").read_bytes(), (
            "the host holds a release override the copy does not carry byte for byte, so "
            "a check asking what a producer's default target is would answer for a host "
            "without one"
        )
    else:
        assert not (copy / "releases.yml").exists()
    # The pool configuration is the other half of what `just repos-apply` installs, and
    # the same argument holds: with it missing from the copy, every `pool status` read
    # here would report pooling disabled for a host that pools, so a check asking what an
    # identity's capacity is would be asking about a host nobody has.
    if (host / "workspaces.yml").is_file():
        assert (copy / "workspaces.yml").read_bytes() == (host / "workspaces.yml").read_bytes(), (
            "the host holds a workspaces file the copy does not carry byte for byte, so "
            "a check asking what an identity's pool capacity is would answer for a host "
            "with pooling off"
        )
    else:
        assert not (copy / "workspaces.yml").exists()
    assert not (copy / "sessions").exists(), (
        "the copy carries the host's session records, which are live claims about "
        "dispatches somebody else is driving"
    )
    assert not (copy / "workspaces").exists(), (
        "the copy carries the host's pool slots, which are worktrees other dispatches "
        "are working in; only the workspaces.yml that configures them is copied"
    )


def test_a_capacity_read_under_the_copy_answers_from_the_copied_workspaces_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`onevcs pool status` under a copy reports the source's pool, not pooling off.

    This is what the copied `workspaces.yml` is for: a check that reads pool state under
    the snapshot — what an identity admits, what maintains its idle slots — has to be
    told what this host configured. The control below is the same copy with that one
    file removed, which is what the snapshot handed every such check before it carried
    one: `pool: 0`, an answer no host here holds.
    """
    source = _plant(tmp_path / "source", POOLED)
    (source / "workspaces.yml").write_text(POOLING, encoding="utf-8")
    # A slot of the source's own pool, which is a live worktree wherever the source is a
    # real host's root, and must not be in the copy however the configuration is.
    occupied = source / "workspaces" / "slot-1"
    occupied.mkdir(parents=True)
    (occupied / "in-use.txt").write_text("another dispatch's worktree\n", encoding="utf-8")

    # `snapshot()` exports `ONEVCS_HOME` process-wide, which is the export every other
    # test here reads; recording the suite's own copy first is what puts it back.
    monkeypatch.setenv(
        onevcs_state_snapshot.ONEVCS_HOME, os.environ[onevcs_state_snapshot.ONEVCS_HOME]
    )
    copy = onevcs_state_snapshot.snapshot(source)

    assert (copy / "workspaces.yml").read_bytes() == (source / "workspaces.yml").read_bytes()
    assert not (copy / "workspaces").exists(), (
        "the copy carries the source's pool slots, which are live worktrees; only the "
        "workspaces.yml that configures them is copied"
    )
    pooled = _capacity(copy, POOLED_IDENTITY)
    assert (pooled["pool"], pooled["overflow"]) == (POOL, OVERFLOW), (
        "the installed onevcs read the copy and reported a capacity the source's "
        f"workspaces.yml does not declare: {pooled}; a check reading pool state under "
        "the snapshot is reading some other host's configuration"
    )

    # The member's other half, taken through `snapshot()` rather than by deleting the
    # file afterwards: a source holding no workspaces file leaves the copy holding none,
    # which is the same answer, and that answer is the one every capacity read here got
    # before the copy carried the file at all.
    unconfigured = onevcs_state_snapshot.snapshot(_plant(tmp_path / "unconfigured", POOLED))
    assert not (unconfigured / "workspaces.yml").exists(), (
        "the copy invented a workspaces file its source does not hold, so a check would "
        "read a capacity this host never configured"
    )
    unpooled = _capacity(unconfigured, POOLED_IDENTITY)
    assert {field: unpooled[field] for field in POOLING_OFF} == POOLING_OFF, (
        f"a root carrying no workspaces file no longer answers {POOLING_OFF}: {unpooled}; "
        "re-read what the copy leaving that file out would cost a check reading pool state"
    )
