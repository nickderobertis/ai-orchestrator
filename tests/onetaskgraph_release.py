"""Build a release archive the real installer accepts, and provision a checkout with it.

`scripts/session-setup.sh` installs the standalone `onetaskgraph` CLI from a
checksum-verified release archive into the *reading checkout's own* `.venv/bin`. Both
halves of that are worth driving for real, and both need the same two things: an
archive whose sidecar authenticates it, and a throwaway checkout to install into.

The archive fetch is the one thing doubled, at `curl` — the boundary the installer
crosses to reach GitHub. Everything above it is this repository's own code: the real
`install_onetaskgraph`, the real checksum verification, the real destination, and the
real `verify_onetaskgraph` that decides whether the binary that landed is the one this
checkout pinned. A throwaway checkout is what keeps that destination out of the
checkout the suite is running in, whose `.venv/bin` holds the binary every other test
here resolves.
"""

from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import subprocess
import tarfile
from pathlib import Path

from orchestrator.root import REPO_ROOT

#: The release target every fixture archive here is built for. The installer derives a
#: real one from `uname`, and each caller overrides `onetaskgraph_target` to name this
#: instead, so an archive built on one host installs on any of them.
FIXTURE_TARGET = "fixture-target"


def host_target() -> str:
    """The release target `onetaskgraph_target` derives on this host.

    Read from the same two answers that function reads — `uname -s` and `uname -m` —
    so an archive built for it is one the *real* target derivation asks for. That is
    what lets a journey drive the installer end to end with only `curl` doubled: the
    fetch is the boundary it crosses to reach GitHub, and this host's architecture is
    not something to substitute an answer for.
    """
    system, machine = os.uname().sysname, os.uname().machine
    targets = {
        ("Linux", "x86_64"): "x86_64-unknown-linux-gnu",
        ("Linux", "aarch64"): "aarch64-unknown-linux-gnu",
        ("Linux", "arm64"): "aarch64-unknown-linux-gnu",
        ("Darwin", "x86_64"): "x86_64-apple-darwin",
        ("Darwin", "arm64"): "aarch64-apple-darwin",
        ("Darwin", "aarch64"): "aarch64-apple-darwin",
    }
    target = targets.get((system, machine))
    if target is None:  # pragma: no cover - this host is one of the six above
        raise RuntimeError(f"onetaskgraph publishes no release archive for {system} {machine}")
    return target


def release_fixture(
    root: Path,
    version: str,
    *,
    valid_checksum: bool = True,
    reported_version: str | None = None,
    target: str = FIXTURE_TARGET,
) -> Path:
    """Write a release archive and its checksum sidecar under ``root``.

    The payload is a script that reports ``onetaskgraph <version>``, which is exactly
    what `verify_onetaskgraph` asks a candidate binary for. ``reported_version`` makes
    it lie, which is how the journey that refuses a mis-versioned archive is arranged.
    ``target`` names it for `host_target` where the real derivation is left to run.
    """
    payload = root / f"payload-{version}"
    payload.mkdir(parents=True, exist_ok=True)
    binary = payload / "onetaskgraph"
    binary.write_text(
        f"#!/usr/bin/env bash\nprintf '%s\\n' 'onetaskgraph {reported_version or version}'\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    archive = root / f"onetaskgraph-v{version}-{target}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(binary, arcname="onetaskgraph")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if not valid_checksum:
        digest = "0" * 64
    archive.with_suffix(archive.suffix + ".sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="utf-8"
    )
    return archive


def checkout(root: Path, *, version: str | None = None, recipes: bool = False) -> Path:
    """Copy this repository's provisioning into ``root`` as a throwaway checkout.

    Only `scripts/` and `config/` are copied: those are what the installer reads, and
    a checkout is exactly the pair of them for this purpose. ``version`` rewrites
    `config/onetaskgraph.version`, which is how two checkouts come to sit at two pins.
    ``recipes`` adds this repository's real `justfile`, for a journey whose subject is
    a recipe rather than the installer it calls.
    """
    root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(REPO_ROOT / "scripts", root / "scripts", dirs_exist_ok=True)
    (root / "config").mkdir(exist_ok=True)
    for declared in (REPO_ROOT / "config").glob("*.version"):
        shutil.copy2(declared, root / "config" / declared.name)
    if version is not None:
        (root / "config" / "onetaskgraph.version").write_text(f"{version}\n", encoding="utf-8")
    if recipes:
        shutil.copy2(REPO_ROOT / "justfile", root / "justfile")
    return root


#: The fetch double and the target override every installer journey shares. `curl` is
#: replaced with a copy from `$ARCHIVE`; the release target is pinned to the one
#: `release_fixture` builds for. Both are the boundary the installer crosses to reach
#: GitHub and this host's own architecture — nothing above them is substituted.
FETCH_DOUBLE = r"""
onetaskgraph_target() { printf '%s\n' fixture-target; }
curl() {
  local destination="${@: -1}" source
  if [[ "$destination" == *.sha256 ]]; then
    source="$ARCHIVE.sha256"
  else
    source="$ARCHIVE"
  fi
  cp "$source" "$destination"
}
"""


def run_installer(
    repo: Path,
    archive: Path,
    home: Path,
    *,
    prelude: str = "",
    script: str = "install_onetaskgraph",
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Drive the real installer inside ``repo`` against ``archive``.

    ``prelude`` is bash that runs after the fetch double and before ``script``, which
    is where a journey injects the one further failure it is about.
    """
    home.mkdir(parents=True, exist_ok=True)
    command = f"""
source scripts/session-setup.sh
{FETCH_DOUBLE}
{prelude}
{script}
"""
    return subprocess.run(
        ["bash", "-c", command],
        cwd=repo,
        env={
            **os.environ,
            "HOME": str(home),
            "ARCHIVE": str(archive),
            **(environment or {}),
        },
        text=True,
        capture_output=True,
        check=False,
    )


def installed_binary(repo: Path) -> Path:
    """Where a checkout's own provisioning puts the CLI: its own `.venv/bin`."""
    return repo / ".venv" / "bin" / "onetaskgraph"


def fetch_double(
    root: Path,
    archive: Path | None,
    *,
    name: str = "fetch-double",
    delay: float = 0.0,
    trace: Path | None = None,
) -> Path:
    """A directory holding a `curl` that serves ``archive``, for the front of `PATH`.

    The same double `FETCH_DOUBLE` is, at the boundary rather than as a shell function:
    an executable, so it survives into whatever subprocess the entry point under test
    spawns. That is what lets a journey drive `scripts/onetaskgraph-install.sh` — which
    is a script rather than a function — without substituting anything above the fetch.

    ``archive`` of ``None`` is a `curl` that always fails, which is how a journey proves
    a second run of the self-heal reaches no network at all: with that on `PATH`, an
    install that tried to fetch could not succeed.

    ``delay`` is how long each fetch takes, which is what a concurrency journey needs:
    a real network crossing is the slow part of an install, and holding one open is how
    a second caller is certainly still waiting while the first provisions. ``trace``
    records one line per fetch, so a journey can say how many callers crossed the
    boundary rather than inspecting the lock they crossed it under. ``name`` keeps two
    doubles in one journey from being written to the same directory.
    """
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    curl = directory / "curl"
    trace_command = f'printf "%s\\n" "$destination" >>{shlex.quote(str(trace))}\n' if trace else ""
    delay_command = f"sleep {delay}\n" if delay else ""
    if archive is None:
        curl.write_text(
            "#!/usr/bin/env bash\n"
            'destination="${@: -1}"\n'
            f"{trace_command}{delay_command}"
            "echo 'curl: this journey serves no archive; nothing here may fetch' >&2\n"
            "exit 1\n",
            encoding="utf-8",
        )
    else:
        curl.write_text(
            "#!/usr/bin/env bash\n"
            'destination="${@: -1}"\n'
            f"source={shlex.quote(str(archive))}\n"
            f"{trace_command}{delay_command}"
            'if [[ "$destination" == *.sha256 ]]; then source="$source.sha256"; fi\n'
            'cp "$source" "$destination"\n',
            encoding="utf-8",
        )
    curl.chmod(0o755)
    return directory


def path_without(root: Path, tool: str, *, name: str = "no-tool") -> Path:
    """A whole `PATH` holding every executable this one has, except ``tool``.

    A stand-in for a platform that does not ship a program, which is how the installer's
    `flock` fallback is reached: `command -v` searches `PATH`, so nothing put *in front*
    of one can hide a tool that is behind it. Symlinked mechanically rather than from a
    list, because the wrapper and the helper it sources reach a lot of ordinary tools and
    a list of them would be a second thing to keep current.
    """
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    for entry in os.environ["PATH"].split(os.pathsep):
        source = Path(entry)
        if not source.is_dir():
            continue
        for candidate in sorted(source.iterdir()):
            destination = directory / candidate.name
            # `is_symlink` as well as `exists`, because a `PATH` entry that is itself a
            # dangling link makes one here that `exists` reports False for — and then
            # the same name from a later entry fails rather than being skipped.
            if candidate.name == tool or destination.is_symlink() or destination.exists():
                continue
            destination.symlink_to(candidate)
    return directory
