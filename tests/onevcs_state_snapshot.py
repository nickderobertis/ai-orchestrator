"""A copy of this host's `onevcs` state root, for every test process to read instead of it.

A check of this repository reads the host's registry constantly — `onevcs resolve` for
which checkout an identity publishes from, `onevcs repos` for which identities are
registered here, and every `onepipeline` view (`runs`, `status`, `results`) opens it to
decide whether a node's work landed. Through onevcs 0.19.3 those were reads. **At the
adopted onevcs 0.21.0 they are not**: the registry's schema moved from version 5 to 6,
dropping the two identity fields `register` used to infer from whether the origin had a
host, and the first *contact* of any 0.21.0 verb with a version-5 registry — a pure read,
`onevcs repos` or `onevcs resolve` or an engine view — rewrites it as version 6. A
release below 0.21.0 cannot read version 6 at all: `cannot read the registry at …:
missing field `workflow``. Measured here on 2026-09-12, under a throwaway state root for
the second half and, once, on the real one — a candidate `onevcs repos --audit-gates`
run to read the new audit migrated this host's shared registry, and the pinned CLI and
every live dispatch linking 0.19.3 refused it until it was restored by hand.

So a suite that read the real root at this pin would flip the host on its first read:
the migration is one-way, host-wide, and older than nothing that touches the root
afterwards. The publication gate of the very branch that adopts 0.21.0 runs this suite
from a clone whose `.venv` carries 0.21.0 while the engine publishing that branch still
links 0.19.3 — and every other live dispatch on the host does too. A check may not be
the thing that decides that for the host.

`snapshot()` is the answer: a per-process copy of the parts of the root a read needs —
the rules file, the release override, the workspaces file, and the release records —
under a scratch directory, and `ONEVCS_HOME` exported to it for the whole process so
every subprocess a test spawns reads the copy. A test that sets its own `ONEVCS_HOME` is
untouched, since it overrides this one in the environment it composes; a test that
redirects `HOME` to sandbox a program that derives the root from it names `ONEVCS_HOME`
beside it, because this export would otherwise win over the derivation. Sessions,
streams, locks, artifacts and the `workspaces` directory of live pool slots are
deliberately **not** copied: nothing a check reads needs them, they are large, and a copy
of a live session record — or of a slot another dispatch is working in — is a stale
claim about work somebody else is driving. The `workspaces.yml` beside that directory is
the opposite and **is** copied: it is the host's configuration rather than its live
state, and with it left out every pool read here answered `pool: 0` — pooling off — for
a host `just repos-apply` had configured.

**The exported root registers no identity.** A registry is not only something a check
reads: since onepipeline 0.51.0 every run's idle driver passes over *every identity the
registry it reads names* — `onevcs::retire_finished` fetching each registered checkout
and running `git ls-remote origin` per candidate branch, and deleting the origin copy of
a branch holding no work beyond its base — and `onevcs sweep` walks the same list. With
the host's registry in the export, every journey that launched a run or swept walked this
host's real repositories against their real remotes once its run settled: about 13–15
minutes per launch, spent serially under the shared toolchain group, and able to retire
real branches. So a process this suite starts sees only what it registered itself.

The host's identities are still readable, from a **second** copy that carries the
registry as well — `host_registry()` — which no environment carries unless a test puts it
there through `host_registry_environment()`, for the one read that needs the host's
identities: `onevcs resolve`, `release targets`, `pool status` or `rules check` of a
repository this host registers. That environment also refuses every git transport but
`file`, so such a read reaches the checkout on disk and never its origin; nothing that can
start a driver or a sweep is ever pointed at it.
`tests/e2e/test_onevcs_state_snapshot_e2e.py` drives the migration for real against a
copy, drives a capacity read against a copied workspaces file, holds the export to
carrying none of the host's identities, and drives the transport refusal through git;
`tests/e2e/test_launch_walks_no_host_identity_e2e.py` drives a real launch under the
export to prove the driver's idle pass reached no checkout or origin of this host's.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

#: The variable git counts its environment-supplied configuration entries by.
GIT_CONFIG_COUNT = "GIT_CONFIG_COUNT"

#: The variable `onevcs` reads its state root from, and `onepipeline` hands to every
#: `onevcs` it links; absent, both derive `$HOME/.onevcs`.
ONEVCS_HOME = "ONEVCS_HOME"

#: What a read of the root needs beside the registry, and all the exported root carries.
#: `releases` is a directory of per-identity release records; the other three are files,
#: and two of them are what `just repos-apply` installs beside the rules: `releases.yml`
#: from `config/onevcs.releases.yml` — left out, every `release targets` read here would
#: answer the global rung and no default target for a host that had configured both —
#: and `workspaces.yml` from `config/onevcs.workspaces.yml` overlaid by the host's own,
#: which is where an identity's pool capacity and maintenance are declared and the only
#: thing a `pool status` read answers them from. The `workspaces` directory those slots
#: live in is **not** here: it is live state, not configuration. A member absent on the
#: host is absent in the copy, which is the same answer.
CONFIGURATION = ("rules.yml", "releases.yml", "workspaces.yml", "releases")

#: The one member only `host_registry()`'s copy carries: which identities this host
#: registers and where their checkouts are, which is what a driver's idle pass and
#: `onevcs sweep` walk against each identity's origin.
REGISTRY = "registry.json"

#: Where this process's registry copy is, for a pytest process it starts — an xdist
#: worker, or a suite a test runs — to copy from. Such a process inherits the exported
#: `ONEVCS_HOME`, which registers nothing, so without this it would take that for the
#: host's root and find no identity to copy. No verb reads it.
HOST_REGISTRY_ENV = "ORCHESTRATOR_TEST_ONEVCS_HOST_REGISTRY"

#: The root this process was started under, recorded by the first `snapshot()` so a
#: check can compare the copy against it without asking `onevcs` — which would be the
#: read this module exists to keep off the host.
HOST_ROOT: Path | None = None

#: The copy of the host's root that carries its registry, taken beside the export by the
#: first `snapshot()` of this process and exported nowhere.
HOST_REGISTRY: Path | None = None


class Snapshot(NamedTuple):
    """The two copies one `snapshot()` takes of a state root."""

    #: The root exported as `ONEVCS_HOME`: the configuration, and no registered identity.
    exported: Path
    #: The same configuration with the source's registry beside it, for reads alone.
    with_registry: Path


def host_root() -> Path:
    """The state root this process was started under, before the snapshot is taken.

    For a process a snapshotting suite started, that is the suite's registry copy, which
    is the host's root as the suite found it.
    """
    if HOST_ROOT is not None:
        return HOST_ROOT
    inherited = os.environ.get(HOST_REGISTRY_ENV)
    if inherited:
        return Path(inherited)
    named = os.environ.get(ONEVCS_HOME)
    if named:
        return Path(named)
    return Path.home() / ".onevcs"


def host_registry() -> Path:
    """The copy of this host's root that registers its identities.

    Reached through `host_registry_environment()` by any process a test starts: this is
    only where that copy is, for a test reading it as a file.
    """
    assert HOST_REGISTRY is not None, "the suite took no snapshot of this host's onevcs root"
    return HOST_REGISTRY


#: git's transport policy in a read of the host's registry: every transport refused but
#: `file`, which reaches nothing but this host's disk. Stated as git's own default and one
#: exception rather than as a list of the network transports, so a transport git adds is
#: refused without anybody naming it.
TRANSPORT_POLICY = (("protocol.allow", "never"), ("protocol.file.allow", "always"))


def host_registry_environment(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """An environment for one read of a repository this host registers.

    ``base`` — this process's environment unless named — with `ONEVCS_HOME` naming
    `host_registry()`, and every git transport but `file` refused through git's own
    environment configuration. A read of a host repository reaches that repository's
    checkout on disk and nothing further: without the refusal, `onevcs release targets`
    asks the real origin for its default branch wherever a checkout records none, and a
    registry naming real identities is one verb away from the pass over their origins
    this copy is kept from. Never name it for a launch or a sweep.
    """
    environment = dict(os.environ if base is None else base)
    environment[ONEVCS_HOME] = str(host_registry())
    count = int(environment.get(GIT_CONFIG_COUNT, "0") or "0")
    for key, value in TRANSPORT_POLICY:
        environment[f"GIT_CONFIG_KEY_{count}"] = key
        environment[f"GIT_CONFIG_VALUE_{count}"] = value
        count += 1
    environment[GIT_CONFIG_COUNT] = str(count)
    return environment


def _copy(origin: Path, members: tuple[str, ...], prefix: str) -> Path:
    root = Path(tempfile.mkdtemp(prefix=prefix))
    for member in members:
        held = origin / member
        if held.is_dir():
            shutil.copytree(held, root / member)
        elif held.is_file():
            shutil.copy2(held, root / member)
    atexit.register(shutil.rmtree, root, ignore_errors=True)
    return root


def snapshot(source: Path | None = None) -> Snapshot:
    """Copy the readable parts of ``source`` into two scratch roots, and export the first.

    ``source`` defaults to the root the process started under. The copies live under
    `TMPDIR` rather than pytest's basetemp because they are taken at import, before any
    fixture exists, and are removed when the process ends.
    """
    global HOST_ROOT, HOST_REGISTRY  # noqa: PLW0603 - recorded once, for the reads above
    origin = host_root() if source is None else source
    taken = Snapshot(
        exported=_copy(origin, CONFIGURATION, "onevcs-state-snapshot-"),
        with_registry=_copy(origin, (*CONFIGURATION, REGISTRY), "onevcs-host-registry-"),
    )
    if source is None and HOST_ROOT is None:
        HOST_ROOT = origin
        HOST_REGISTRY = taken.with_registry
        os.environ[HOST_REGISTRY_ENV] = str(taken.with_registry)
    os.environ[ONEVCS_HOME] = str(taken.exported)
    return taken
