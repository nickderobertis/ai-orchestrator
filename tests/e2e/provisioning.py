"""Provisioning a worktree's toolchain the way a session really does.

Shared by every journey that needs a venv built from this repository's own pins:
the session-setup suite that proves the script's own behaviour, and the
reconciliation journey that reads the engine binary that provisioning installs.
One copy because both drive the *same* real script — a second fixture builder
would be a second answer to "what does a provisioned worktree contain", and the
one that went stale would be the one nobody was reading.

llmlint: ignore-file[shell_test_tiers_stay_split] Which Nx project owns the journeys
this fixture feeds is a property of those modules, not of the three files this change
adds to the fixture: they have installed real published tools from the orchestrator
project since they were written, while a real session-setup run of *this* checkout is
owned by `tests/session_setup`. Moving them is a change to `nx.json`,
`orchestrator/project.json` and `tests/nx_inputs.py` together, drafted as a follow-up.
llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] Same site, same
follow-up: the cost of those journeys predates this change, and putting them behind a
narrower edge is the project split above.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
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
    # Session setup runs no orchestrator code — the sweep it invokes composes two
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
    # The first thing session setup runs, before any provisioning: a dispatch's run
    # root is reclaimable by a sibling from the moment it exists, so a fixture without
    # this script would provision a tree no real session start could reproduce.
    shutil.copy2(REPO_ROOT / "scripts" / "hold-run-lease.sh", scripts / "hold-run-lease.sh")
    # The sweep session setup runs is a composition of two published verbs rather
    # than one of them, so the wrapper that composes them is part of a repo this
    # script can be run in. `HOME` above already points every family it judges inside
    # `tmp_path`, so the sweep it performs here is real and reaches nothing.
    sweep = scripts / "sweep.sh"
    shutil.copy2(REPO_ROOT / "scripts" / "sweep.sh", sweep)
    sweep.chmod(0o755)
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
    # The sweep session setup runs measures the host scratch root as well as the
    # families the two verbs own, so isolating `HOME` no longer isolates all of it:
    # without this, every journey here would walk this host's real 83 GiB `/tmp` and
    # report on it. `TMPDIR` is where `oneagentgraph` writes its own scratch too, so
    # one redirect keeps both halves inside `tmp_path`.
    scratch_root = tmp_path / "tmp"
    scratch_root.mkdir(exist_ok=True)
    return subprocess.run(
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
            # relies on — the sweep session setup runs would read the copy, and the
            # journey that plants a broken root under `$HOME` would plant it where
            # nothing looks.
            "ONEVCS_HOME": str(tmp_path / ".onevcs"),
            "PATH": path or os.environ["PATH"],
            "TMPDIR": str(scratch_root),
            # A shared cache and a suite that corrupts what it installed cannot both
            # be hardlinks. uv installs by linking out of its cache, so the test
            # below that rewrites an installed `METADATA` to prove version drift is
            # detected rewrote the cache entry — and every other environment on this
            # host linked to the same inode — turning one deliberate corruption into
            # a real broken toolchain. Copying is the difference between sharing
            # downloads and sharing files; it costs a fraction of one download.
            "UV_LINK_MODE": "copy",
            **({"UV_CACHE_DIR": shared_cache} if shared_cache else {}),
        },
    )


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
