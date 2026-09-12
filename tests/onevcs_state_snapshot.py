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
the registry, the rules file, the release override, and the release records — under a
scratch directory, and
`ONEVCS_HOME` exported to it for the whole process so every subprocess a test spawns
reads the copy. A test that sets its own `ONEVCS_HOME` is untouched, since it overrides
this one in the environment it composes; a test that redirects `HOME` to sandbox a
program that derives the root from it names `ONEVCS_HOME` beside it, because this export
would otherwise win over the derivation. Sessions, streams, locks, artifacts and
workspaces are deliberately **not** copied: nothing a check reads needs them, they are
large, and a copy of a live session record is a stale claim about a dispatch somebody
else is driving. `tests/e2e/test_onevcs_state_snapshot_e2e.py` drives the migration for
real against a copy and holds the copy to being what the suite reads.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

#: The variable `onevcs` reads its state root from, and `onepipeline` hands to every
#: `onevcs` it links; absent, both derive `$HOME/.onevcs`.
ONEVCS_HOME = "ONEVCS_HOME"

#: What a read of the root needs, and all that is copied. `releases` is a directory
#: of per-identity release records; the other three are files, and `releases.yml` is
#: the override `just repos-apply` installs from `config/onevcs.releases.yml` — left
#: out, every `release targets` read here would answer the global rung and no default
#: target for a host that had configured both. A member absent on the host is absent
#: in the copy, which is the same answer.
COPIED = ("registry.json", "rules.yml", "releases.yml", "releases")

#: The root this process was started under, recorded by the first `snapshot()` so a
#: check can compare the copy against it without asking `onevcs` — which would be the
#: read this module exists to keep off the host.
HOST_ROOT: Path | None = None


def host_root() -> Path:
    """The state root this process was started under, before the snapshot is taken."""
    if HOST_ROOT is not None:
        return HOST_ROOT
    named = os.environ.get(ONEVCS_HOME)
    if named:
        return Path(named)
    return Path.home() / ".onevcs"


def snapshot(source: Path | None = None) -> Path:
    """Copy the readable parts of ``source`` into a scratch root, and export it.

    ``source`` defaults to the root the process started under. The copy lives under
    `TMPDIR` rather than pytest's basetemp because it is taken at import, before any
    fixture exists, and is removed when the process ends.
    """
    global HOST_ROOT  # noqa: PLW0603 - recorded once, for the comparison above
    origin = host_root() if source is None else source
    if source is None and HOST_ROOT is None:
        HOST_ROOT = origin
    root = Path(tempfile.mkdtemp(prefix="onevcs-state-snapshot-"))
    for member in COPIED:
        held = origin / member
        if held.is_dir():
            shutil.copytree(held, root / member)
        elif held.is_file():
            shutil.copy2(held, root / member)
    os.environ[ONEVCS_HOME] = str(root)
    atexit.register(shutil.rmtree, root, ignore_errors=True)
    return root
