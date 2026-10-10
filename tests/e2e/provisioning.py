"""Provisioning a worktree's toolchain the way a session really does.

Shared by every journey that needs a venv built from this repository's own pins:
the session-setup suite that proves the script's own behaviour, and the
reconciliation journey that reads the engine binary that provisioning installs.
One copy because both drive the *same* real script — a second fixture builder
would be a second answer to "what does a provisioned worktree contain", and the
one that went stale would be the one nobody was reading.

This module is a test-support unit, `tests/support/provisioning/`, carrying every file
it copies, so a tier reaches it by an edge and an edit here re-runs exactly the tiers
whose modules import it.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
import time
from collections.abc import Mapping
from pathlib import Path

from orchestrator.root import REPO_ROOT

ONEJUDGE_VERSION = (REPO_ROOT / "config" / "onejudge.version").read_text().strip()
ONEHARNESS_VERSION = (REPO_ROOT / "config" / "oneharness.version").read_text().strip()


@functools.cache
def _shared_uv_cache() -> str | None:
    """This host's real uv download cache, or `None` when uv cannot name one.

    Every test here points `HOME` at its own `tmp_path`, which is what keeps the
    isolation honest — session setup writes into `$HOME` and must not touch the
    developer's. But uv derives its download cache from `HOME` too, so each of the
    nine tests re-downloaded every wheel of a locked environment it had just
    downloaded, and the file cost about seventy-five seconds an invocation for
    answers already on disk. The cache is content-addressed and safe to share, and
    `uv sync` stays entirely real: sharing it changes where the wheels come from,
    not whether the sync resolves, builds, and installs them.

    Resolved once, from the ambient PATH, before any test narrows it.
    """
    uv = shutil.which("uv")
    if uv is None:  # pragma: no cover - the suite cannot run without uv on PATH
        return None
    located = subprocess.run([uv, "cache", "dir"], text=True, capture_output=True, check=False)
    return located.stdout.strip() or None if located.returncode == 0 else None


def setup_repo(
    tmp_path: Path,
    *,
    repo: Path | None = None,
    adopted_onejudge: str = ONEJUDGE_VERSION,
    adopted_oneharness: str = ONEHARNESS_VERSION,
    dependency_oneharness: str = ONEHARNESS_VERSION,
    adopted_published: Mapping[str, str] | None = None,
) -> Path:
    """A worktree session setup can be run in, at `tmp_path/repo` unless one is named.

    `repo` is for the journey whose subject is *where* a dispatch runs: a session
    worktree `onevcs` really cut, which no fixture can choose the path of. Everything
    else is identical, so that journey provisions the same tree as every other one.
    """
    repo = repo if repo is not None else tmp_path / "repo"
    scripts = repo / "scripts"
    config = repo / "config"
    package = repo / "orchestrator"
    scripts.mkdir(parents=True, exist_ok=True)
    config.mkdir(exist_ok=True)
    package.mkdir(exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copy2(REPO_ROOT / name, repo / name)
    shutil.copy2(REPO_ROOT / "justfile", repo / "justfile")
    # Session setup runs no orchestrator code — the sweep it starts detached composes two
    # published CLIs — so the package exists here only because `pyproject.toml`
    # declares it as the wheel's one package and `uv sync` builds it.
    if dependency_oneharness != ONEHARNESS_VERSION:
        pyproject = repo / "pyproject.toml"
        pyproject.write_text(
            pyproject.read_text(encoding="utf-8").replace(
                f"oneharness-cli=={ONEHARNESS_VERSION}",
                f"oneharness-cli=={dependency_oneharness}",
            ),
            encoding="utf-8",
        )
    shutil.copy2(REPO_ROOT / "scripts" / "session-setup.sh", scripts / "session-setup.sh")
    shutil.copy2(REPO_ROOT / "scripts" / "setup-llmlint.sh", scripts / "setup-llmlint.sh")
    # The sweep session setup starts detached is the justfile's own recipe over two
    # published verbs, started through `host-sweep.sh`. `HOME` above already points every
    # family it judges, and the lock it takes, inside `tmp_path`, so the sweep it performs
    # here is real and reaches nothing.
    shutil.copy2(REPO_ROOT / "scripts" / "host-sweep.sh", scripts / "host-sweep.sh")
    # The last thing session setup runs is `just repos-bootstrap`, which provisions the
    # registered sibling checkouts' gates — through the recipe, the script, and the
    # reader of the tracked checkout list, so all three are part of a repo this script
    # can be run in. `HOME` is `tmp_path` in every journey here, so every listed `~/`
    # path is absent and the recipe skips each one: no journey here runs a sibling's
    # bootstrap, and the report it leaves says so.
    for name in ("repos-bootstrap.sh", "registered-checkouts.sh"):
        shutil.copy2(REPO_ROOT / "scripts" / name, scripts / name)
    shutil.copy2(REPO_ROOT / "config" / "onevcs.checkouts", config / "onevcs.checkouts")
    # Every adopted release is copied, so a further pinned tool needs no fixture edit;
    # the parameters below then restate only what a journey deliberately moves.
    for declared in (REPO_ROOT / "config").glob("*.version"):
        shutil.copy2(declared, config / declared.name)
    (config / "onejudge.version").write_text(f"{adopted_onejudge}\n", encoding="utf-8")
    (config / "oneharness.version").write_text(f"{adopted_oneharness}\n", encoding="utf-8")
    for version_file, adopted in (adopted_published or {}).items():
        (config / version_file).write_text(f"{adopted}\n", encoding="utf-8")
    return repo


def run_setup(
    repo: Path, tmp_path: Path, *, path: str | None = None
) -> subprocess.CompletedProcess[str]:
    bun = shutil.which("bun")
    assert bun is not None
    bun_version = subprocess.run(
        [bun, "--version"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    shared_cache = _shared_uv_cache()
    _carry_asdf_versions(tmp_path)
    # The sweep session setup starts detached measures the host scratch root as well as the
    # families the two verbs own, so isolating `HOME` no longer isolates all of it:
    # without this, every journey here would walk this host's real 83 GiB `/tmp` and
    # report on it. `TMPDIR` is where `oneagentgraph` writes its own scratch too, so
    # one redirect keeps both halves inside `tmp_path`.
    scratch_root = tmp_path / "tmp"
    scratch_root.mkdir(exist_ok=True)
    setup = subprocess.run(
        ["bash", str(repo / "scripts" / "session-setup.sh")],
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "ASDF_BUN_VERSION": bun_version,
            "ASDF_DATA_DIR": os.environ.get("ASDF_DATA_DIR", str(Path.home() / ".asdf")),
            "HOME": str(tmp_path),
            # Named beside `HOME` rather than derived from it: the suite exports
            # `ONEVCS_HOME` to a copy of the host's root (`tests/onevcs_state_snapshot.py`),
            # and that export would otherwise win over the derivation this sandbox
            # relies on — the sweep session setup starts would read the copy, and the
            # journey that plants a broken root under `$HOME` would plant it where
            # nothing looks.
            "ONEVCS_HOME": str(tmp_path / ".onevcs"),
            "PATH": path or os.environ["PATH"],
            "TMPDIR": str(scratch_root),
            # Where uv puts its tools under that home anyway, named so that no tool
            # directory this process inherited sends setup's `llmlint` install elsewhere.
            "UV_TOOL_DIR": str(tmp_path / ".local" / "share" / "uv" / "tools"),
            "UV_TOOL_BIN_DIR": str(tmp_path / ".local" / "bin"),
            # A shared cache and a suite that corrupts what it installed cannot both
            # be hardlinks. uv installs by linking out of its cache, so the test
            # below that rewrites an installed `METADATA` to prove version drift is
            # detected rewrote the cache entry — and every other environment on this
            # host linked to the same inode — turning one deliberate corruption into
            # a real broken toolchain. Copying is the difference between sharing
            # downloads and sharing files; it costs a fraction of one download.
            "UV_LINK_MODE": "copy",
            **({"UV_CACHE_DIR": shared_cache} if shared_cache else {}),
            # Named rather than inherited, so the host sweep's lock and log are this
            # journey's own even on a host that exports its own cache home.
            "XDG_CACHE_HOME": str(host_sweep_cache(tmp_path).parents[1]),
        },
    )
    await_host_sweep(tmp_path)
    return setup


def host_sweep_cache(tmp_path: Path) -> Path:
    """Where the host sweep a `run_setup` starts keeps its lock, holder, stamp and log."""
    return tmp_path / ".cache" / "ai-orchestrator" / "sweep"


#: How long the detached sweep a session start leaves running may take over a
#: `tmp_path` state root: seconds of real work, bounded far above it.
HOST_SWEEP_SECONDS = 300


def await_host_sweep(tmp_path: Path) -> None:
    """Wait until the sweep job session setup started detached has exited.

    Session setup returns while its job still runs, by design. A journey reads what the
    job did from its log, and must not end with the job still alive, so every
    `run_setup` waits it out — by reading the process table for the pid the job's
    holder record names, never by signalling it.
    """
    holder = host_sweep_cache(tmp_path) / "holder"
    if not holder.exists():
        return
    fields = dict(
        row.partition(" ")[::2] for row in holder.read_text(encoding="utf-8").splitlines()
    )
    pid = fields.get("pid", "")
    if not pid.isdigit():
        return
    deadline = time.monotonic() + HOST_SWEEP_SECONDS
    while Path(f"/proc/{pid}").exists():
        assert time.monotonic() < deadline, (
            f"the host sweep job {pid} outlived {HOST_SWEEP_SECONDS}s"
        )
        time.sleep(0.2)


#: Where asdf reads a host's tool versions from when nothing nearer sets one. It is
#: keyed on `$HOME`, and `run_setup` redirects `$HOME` into `tmp_path` so the sweep it
#: performs reaches nothing real — which takes this file with it.
TOOL_VERSIONS = ".tool-versions"


def _carry_asdf_versions(home: Path) -> None:
    """Give the redirected `$HOME` the tool versions the real one resolves.

    `uv`, `just`, and `bun` are asdf **shims** on this host — `/home/…/.local/bin/uv`
    is a shim, not the binary — and a shim resolves its version from `.tool-versions`,
    walking up from the working directory and ending at `$HOME`. Redirecting `$HOME`
    to isolate the sweep therefore removes the only file that says which `uv` to run,
    and every shim on the path exits **126** listing the versions it could have picked.

    That surfaces as a failure nowhere near its cause: `session-setup.sh` completes
    and verifies every pinned tool, and then `just sweep` reports `onevcs sweep exited
    126, so nothing in these families was judged` — which reads like a defect in the
    sweep. Whether it happens at all depends on PATH ordering, so the journey passed
    wherever the real binary's directory preceded the shim directory and failed
    wherever it did not.

    Copying the file is the faithful repair rather than exporting one
    `ASDF_<TOOL>_VERSION` per tool: it reproduces the host's own resolution for every
    asdf-managed tool at once, including ones added later, instead of enumerating the
    three that happen to be shimmed today. `ASDF_BUN_VERSION` above predates this and
    stays: it is measured from the `bun` this process really resolved, which is a
    stronger statement than the file makes.
    """
    source = Path.home() / TOOL_VERSIONS
    if source.is_file():
        shutil.copy2(source, home / TOOL_VERSIONS)


def path_without_uv(tmp_path: Path) -> str:
    """A real PATH with `uv` genuinely absent — nothing here is a double.

    Every executable this returns is the host's own: `bun` is a symlink to the
    real binary `which` just resolved, and `/usr/bin:/bin` are the real system
    directories. What the narrowing removes is `uv`, because the journeys below
    prove what `session-setup.sh` does when `uv` is not installed, and the only
    faithful way to test that is for `uv` to actually not be on PATH.

    So this substitutes no behaviour and stubs no interface: the script still
    crosses every real process boundary it would cross in a session, and still
    fails for the real reason rather than a simulated one.
    """
    bun = shutil.which("bun")
    assert bun is not None
    tools = tmp_path / "real-tools"
    tools.mkdir(exist_ok=True)
    (tools / "bun").symlink_to(Path(bun).resolve())
    return f"{tools}:/usr/bin:/bin"
