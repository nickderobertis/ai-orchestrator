"""The suite reads a copy of this host's `onevcs` state root, and why it has to.

`tests/onevcs_state_snapshot.py` records the measurement: the adopted `onevcs` rewrites a
version-5 registry to version 6 on first contact — a pure read included — and the
release every dispatch of an older engine links cannot read what it writes. The first
journey here takes that measurement again against the installed CLI, on a registry of
the older shape planted under a scratch root, so the day a release stops migrating on
read this module's reason fails rather than going on describing a hazard nobody has.
The second holds the isolation itself: what this process exports as `ONEVCS_HOME` is a
copy carrying the host's configuration and none of its registered identities, which only
the registry copy a read names for itself carries, and neither copy carries the host's
sessions or its live pool slots; `tests/e2e/test_launch_walks_no_host_identity_e2e.py`
drives a launch under that export to prove what leaving the identities out is for. The
third asks the installed CLI for a capacity the way an operator does, against a copy
taken from a planted root: the workspaces file is what `onevcs pool status` resolves an
identity's pool out of, so a copy without it answers `pool: 0` — pooling off — for a
source that pools, and every check reading pool state under the snapshot would be
reading some other host.

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
import re
import subprocess
import sys
from pathlib import Path

import onevcs_state_snapshot
import pytest
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

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
    """`ONEVCS_HOME` names a copy of the host's configuration that registers nothing.

    The host's identities are in the registry copy a read names for itself, and nowhere a
    process inherits: a registry exported to every process is one every launched driver
    and every sweep walks against each identity's real origin.
    """
    exported = os.environ.get(onevcs_state_snapshot.ONEVCS_HOME)
    assert exported, "the suite exports no ONEVCS_HOME, so every read below reaches the host"
    host = onevcs_state_snapshot.host_root()
    copy = Path(exported)
    with_registry = onevcs_state_snapshot.host_registry()

    for taken in (copy, with_registry):
        assert taken.resolve() != host.resolve(), (
            f"a suite copy is the host's own root {host}, which the installed onevcs would "
            "rewrite on the first read any test makes of it"
        )
    assert not (copy / onevcs_state_snapshot.REGISTRY).exists(), (
        "the root every process of this suite inherits carries a registry, so a driver a "
        "journey launches, or a sweep it runs, walks those identities against their origins"
    )
    if (host / onevcs_state_snapshot.REGISTRY).is_file():
        assert _identities(with_registry) == _identities(host), (
            "the registry copy holds different identities from the host, so a check asking "
            "it which repositories are registered here is asking about some other host"
        )
    else:
        # A host with no registry has nothing to be isolated from and nothing to compare.
        assert not (with_registry / onevcs_state_snapshot.REGISTRY).exists()
    for taken in (copy, with_registry):
        # The release override is part of what a read answers from: with it missing, every
        # `release targets` read here would report the global rung and no default target
        # for a host `just repos-apply` had configured with both.
        if (host / "releases.yml").is_file():
            assert (taken / "releases.yml").read_bytes() == (host / "releases.yml").read_bytes(), (
                f"{taken} does not carry the host's release override byte for byte, so a "
                "check asking what a producer's default target is would answer for a host "
                "without one"
            )
        else:
            assert not (taken / "releases.yml").exists()
        # The pool configuration is the other half of what `just repos-apply` installs:
        # with it missing, every `pool status` read here would report pooling disabled for
        # a host that pools.
        if (host / "workspaces.yml").is_file():
            assert (taken / "workspaces.yml").read_bytes() == (
                host / "workspaces.yml"
            ).read_bytes(), (
                f"{taken} does not carry the host's workspaces file byte for byte, so a "
                "check asking what an identity's pool capacity is would answer for a host "
                "with pooling off"
            )
        else:
            assert not (taken / "workspaces.yml").exists()
        assert not (taken / "sessions").exists(), (
            f"{taken} carries the host's session records, which are live claims about "
            "dispatches somebody else is driving"
        )
        assert not (taken / "workspaces").exists(), (
            f"{taken} carries the host's pool slots, which are worktrees other dispatches "
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
    taken = onevcs_state_snapshot.snapshot(source)
    # The identity is only in the registry copy, which is the one a read names for itself.
    copy = taken.with_registry

    for held in taken:
        assert (held / "workspaces.yml").read_bytes() == (source / "workspaces.yml").read_bytes()
        assert not (held / "workspaces").exists(), (
            "the copy carries the source's pool slots, which are live worktrees; only the "
            "workspaces.yml that configures them is copied"
        )
    assert not (taken.exported / onevcs_state_snapshot.REGISTRY).exists()
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
    unconfigured = onevcs_state_snapshot.snapshot(
        _plant(tmp_path / "unconfigured", POOLED)
    ).with_registry
    assert not (unconfigured / "workspaces.yml").exists(), (
        "the copy invented a workspaces file its source does not hold, so a check would "
        "read a capacity this host never configured"
    )
    unpooled = _capacity(unconfigured, POOLED_IDENTITY)
    assert {field: unpooled[field] for field in POOLING_OFF} == POOLING_OFF, (
        f"a root carrying no workspaces file no longer answers {POOLING_OFF}: {unpooled}; "
        "re-read what the copy leaving that file out would cost a check reading pool state"
    )


#: The script `just repos-apply` runs, which is what puts configuration in a state root:
#: every file it installs there is one a read under the copy answers from.
APPLY_REPO_REGISTRY = REPO_ROOT / "scripts" / "apply-repo-registry.sh"
INSTALLED_MEMBER = re.compile(r'^installed_\w+="\$onevcs_home/([^"/]+)"$', re.MULTILINE)


def test_the_copy_carries_every_file_repos_apply_installs_in_the_root() -> None:
    """Each configuration file `just repos-apply` installs is one the exported copy carries.

    The copy is configuration only, so a file the recipe starts installing that the copy
    left out would have every read here answer for a host without it — as every pool read
    did before the copy carried `workspaces.yml`.
    """
    installed = set(INSTALLED_MEMBER.findall(APPLY_REPO_REGISTRY.read_text(encoding="utf-8")))

    assert installed, f"{APPLY_REPO_REGISTRY} names no file it installs; re-read its shape"
    left_out = installed - set(onevcs_state_snapshot.CONFIGURATION)
    assert not left_out, (
        f"`just repos-apply` installs {sorted(left_out)} into the state root and the "
        "suite's copy does not carry it"
    )


def _git(*arguments: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    done = subprocess.run(
        [
            "git",
            "-c",
            "user.name=Snapshot",
            "-c",
            "user.email=snapshot@example.invalid",
            *arguments,
        ],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert done.returncode == 0, (
        f"git {' '.join(arguments)} exited {done.returncode}: {done.stderr}"
    )
    return done.stdout


#: A configuration entry a caller's environment already carries through git's own
#: environment configuration, which the refusal has to add to rather than replace.
CALLER_ENTRY = ("core.abbrev", "12")


@pytest.mark.parametrize("carried", [False, True], ids=["bare", "caller-entries"])
@pytest.mark.parametrize(
    "origin", ["https://example.invalid/host.git", "ssh://example.invalid/host.git"]
)
def test_the_host_registry_environment_refuses_every_git_transport_but_file(
    tmp_path: Path, origin: str, carried: bool
) -> None:
    """Under `host_registry_environment()`, git reaches a `file` origin and refuses a network one.

    `onevcs release targets` resolves a checkout's default branch with `git ls-remote
    origin` wherever the checkout records none, which is what a read of a host
    repository did to that repository's real origin. The same environment still reaches
    a `file` origin, which is this host's disk, so the refusal is of the network and not
    of git — and it holds beside an entry the caller's environment already carried
    through git's own environment configuration, without dropping that entry.
    """
    bare = tmp_path / "local-origin.git"
    _git("init", "-q", "--bare", "-b", "main", str(bare))
    local = tmp_path / "local"
    _git("clone", "-q", str(bare), str(local))
    (local / "README.md").write_text("local\n", encoding="utf-8")
    _git("add", "-A", cwd=local)
    _git("commit", "-qm", "chore: seed", cwd=local)
    _git("push", "-q", "origin", "main", cwd=local)
    base = dict(os.environ)
    key, value = CALLER_ENTRY
    if carried:
        base.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0=key, GIT_CONFIG_VALUE_0=value)
    environment = onevcs_state_snapshot.host_registry_environment(base)

    assert "refs/heads/main" in _git("ls-remote", "origin", cwd=local, env=environment)
    if carried:
        assert _git("config", "--get", key, cwd=local, env=environment).strip() == value

    _git("remote", "set-url", "origin", origin, cwd=local)
    asked = subprocess.run(
        ["git", "ls-remote", "origin"],
        cwd=local,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    scheme = origin.split(":", 1)[0]
    assert asked.returncode != 0
    assert f"transport '{scheme}' not allowed" in asked.stderr, (
        f"git under the registry copy's environment tried {origin} rather than refusing "
        f"its transport: {asked.stderr}"
    )


#: What a process the suite starts runs to take its own snapshot, as an xdist worker does
#: on importing `tests/conftest.py`: the module's entry point, and then what it recorded —
#: read inside the child, whose copies are removed when it exits.
CHILD_SNAPSHOT = """
import json, os
from pathlib import Path
import onevcs_state_snapshot as snapshot
snapshot.snapshot()
registry = snapshot.host_registry() / snapshot.REGISTRY
exported = Path(os.environ[snapshot.ONEVCS_HOME])
print(json.dumps({
    "host_root": str(snapshot.host_root()),
    "exported_registers": (exported / snapshot.REGISTRY).exists(),
    "identities": (
        sorted(json.loads(registry.read_text())["identities"]) if registry.is_file() else None
    ),
}))
"""


def test_a_process_the_suite_starts_copies_the_hosts_registry_from_the_suites_copy() -> None:
    """A child's own snapshot registers the host's identities where this one does.

    The child inherits the exported `ONEVCS_HOME`, which registers nothing; taken for the
    host's root, it would leave the child's registry copy empty, and every read of a host
    repository in an xdist worker would answer "not registered".
    """
    child = subprocess.run(
        [sys.executable, "-c", CHILD_SNAPSHOT],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "tests")},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert child.returncode == 0, child.stderr
    taken = json.loads(child.stdout)

    assert Path(taken["host_root"]) == onevcs_state_snapshot.host_registry()
    assert taken["exported_registers"] is False
    host = onevcs_state_snapshot.host_root() / onevcs_state_snapshot.REGISTRY
    expected = sorted(_identities(host.parent)) if host.is_file() else None
    assert taken["identities"] == expected, (
        "the child's registry copy holds different identities from the host's, so a read "
        f"of a host repository in an xdist worker answers for another host: {taken}"
    )
